"""``heddle doctor`` — runtime and repository integration diagnostics.

A pure control-plane read that reports six diagnostic categories — install mode,
config, parse, resource,
artifact-path, and a gate-summary roll-up — in the envelope ``diagnostics[]``
with the advisory/fatal severity split (exit 0 healthy / 4 advisory-only / 3
fatal). A usage error (a bad flag) or active-feature ambiguity
short-circuits to exit 2, mirroring ``kickoff``/``run-gate`` (Feature Spec
Boundary conditions). It reads the kernel snapshot and the
Band-2 resolver only and stores nothing.

The install-mode category answers the adopter's question: "am I running the
installed package or my working copy?" It adds the interpreter beside that mode
and resolved package location so mixed launchers are visible. It still reports no
ref, dirty state, or bundle version, and no ``heddle --version`` while the
version is the constant ``0.0.1`` for every build.
"""

from __future__ import annotations

import shlex
import shutil
import stat
import sys
from pathlib import Path

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.schemas import AGENTS_KEYS, STAGES
from heddle.gate import entry as gate_entry
from heddle.gate import prompt as gate_prompt
from heddle.gate.registry import GATES
from heddle.kernel.decision_guidance import load_decision_guidance
from heddle.kernel.model import (
    FeatureSnapshot,
    is_terminal,
    list_active_feature_workspaces,
    resolve_active_feature,
    resolve_snapshot,
)
from heddle.kernel.project_config import (
    HEDDLE_CONFIG_FILENAME,
    MISSING_PROJECT_ROOT,
    KernelError,
    ProjectConfig,
    load_project_config_from_cwd,
)
from heddle.kernel.resources import resolve_resource
from heddle.kernel.state import INCOMPATIBLE_STATE_SCHEMA, state_loader_capability
from heddle.runtime.cli_args import parse_feature_flag
from heddle.runtime.diagnostics import (
    kernel_error_result,
    missing_project_root_next_actions,
)
from heddle.runtime.feature_start import _SCAFFOLD_ASSETS
from heddle.runtime.identity import runtime_identity_diagnostic
from heddle.runtime.init_host import INIT_LOCK_MISSING, read_init_lock
from heddle.runtime.output import emit_envelope
from heddle.runtime.readiness import validate_retained_reviews

_STATIC_PROBE_ASSETS = (
    "decision-routing.md",
    "autonomy-addendum.md",
    "gate-failure-guide.md",
    "prompt-authoring-standards.md",
    "heddle-yaml.scaffold.yaml",
    "engineering-principles.seed.md",
)


def run_doctor(args: list[str], json_mode: bool) -> int:
    explicit, parse_failure = parse_feature_flag(args, command="doctor")
    if parse_failure is not None:
        return _emit(parse_failure, json_mode)

    from heddle.runtime.application import execute

    return _emit(execute(ops.Doctor(feature=explicit)), json_mode)


def doctor(operation: ops.Doctor) -> HeddleResult:
    explicit = operation.feature
    diagnostics: list[Diagnostic] = []

    # Install mode first: it is the one diagnostic that holds without a project,
    # and "which copy of the code is running" is the question every other
    # diagnostic gets read against.
    diagnostics.append(runtime_identity_diagnostic())

    # Config: unknown-key advisories from the loader, fatal on unparsable YAML.
    try:
        config = load_project_config_from_cwd()
    except KernelError as error:
        if _is_missing_project_root(error):
            return HeddleResult.failure(
                HeddleError(
                    code=error.code,
                    message=error.message,
                    hint=error.hint,
                    details=error.details,
                ),
                exit_code=ExitCode.FATAL,
                # Carried deliberately: "no project here" is the error an
                # adopter hits before `init`, and it is exactly when knowing
                # whether the wheel or a checkout is running matters.
                diagnostics=tuple(diagnostics),
                next_actions=missing_project_root_next_actions(),
            )
        diagnostics.append(
            Diagnostic(Severity.FATAL, "config-unparsable", error.message)
        )
        return _report(
            diagnostics,
            feature=None,
            project_root=None,
        )
    diagnostics.extend(_config_diagnostics(config))
    diagnostics.extend(_runner_diagnostics(config))
    diagnostics.extend(_lock_diagnostics(config))
    loader = state_loader_capability()
    if not loader["accelerated"]:
        diagnostics.append(
            Diagnostic(
                Severity.ADVISORY,
                "state-loader-fallback",
                "state YAML is using the safe Python loader because CSafeLoader "
                "is unavailable",
                source=str(loader["loader"]),
            )
        )

    # Parse: resolve the active feature + its snapshot (state + plan). An
    # initialized host with zero workspaces is a healthy adopter state, not a
    # broken feature selection.
    feature: str | None = None
    snapshot = None
    try:
        if explicit is None and not list_active_feature_workspaces(config):
            diagnostics.append(
                Diagnostic(
                    Severity.INFO,
                    "parse-zero-features",
                    f"no active feature workspaces under {config.layout.plans}/",
                )
            )
        else:
            resolution = resolve_active_feature(config, explicit)
            feature = resolution.feature
            snapshot = resolve_snapshot(config, feature)
            diagnostics.append(
                Diagnostic(Severity.INFO, "parse-ok", f"state parsed for {feature}")
            )
    except KernelError as error:
        if error.code == "feature-ambiguous":
            # Active-feature
            # ambiguity is a usage failure → exit 2, mirroring `kickoff`/
            # `run-gate`. Doctor returns early rather than report-and-continue —
            # with no resolved feature its feature-scoped probes (parse,
            # artifact, gate roll-up) have nothing to run against. The
            # control-plane diagnostics gathered before resolution are carried
            # into the failure envelope.
            return _feature_ambiguous_failure(error, diagnostics)
        severity = (
            Severity.FATAL if error.code == "workspace-invalid" else Severity.ADVISORY
        )
        diagnostics.append(Diagnostic(severity, f"parse-{error.code}", error.message))
        if error.reason == INCOMPATIBLE_STATE_SCHEMA:
            return kernel_error_result(
                error,
                exit_codes={"workspace-invalid": ExitCode.FATAL},
                diagnostics=tuple(diagnostics),
            )

    # Resource: resolve each probe asset through the chain, naming source.
    # The FATAL `resource-missing` branch is unreachable in a healthy install:
    # every `_PROBE_ASSETS` entry has a packaged floor, so resolution cannot
    # fail through the chain. It exists to surface a genuinely broken
    # package (a missing packaged default) and is exercised at the unit level;
    # deeper resource-integrity probing belongs to the artifact qualifier.
    for asset in _probe_assets():
        try:
            if asset == "decision-routing.md":
                policy = load_decision_guidance(config)
                source = policy.source
            else:
                _path, source = resolve_resource(asset, config)
            diagnostics.append(
                Diagnostic(
                    Severity.INFO,
                    f"resource-{asset}",
                    f"{asset} resolved from the {source} tier",
                )
            )
        except KernelError as error:
            diagnostics.append(
                Diagnostic(
                    Severity.FATAL, "resource-missing", f"{error.message}; {error.hint}"
                )
            )
    # Gate prompts use the same resolver as show-prompt and run-gate. Keep one
    # representative prompt diagnostic explicit so operators can distinguish
    # override/generated/packaged provenance without interpreting asset paths.
    spec_review = GATES.get("spec-review")
    if (
        snapshot is not None
        and not is_terminal(snapshot.state)
        and spec_review is not None
    ):
        try:
            prompt_context = gate_entry.build_gate_context(
                snapshot,
                config,
                spec_review,
                cli=spec_review.default_cli,
            )
            resolved_prompt = gate_prompt.resolve_gate_prompt(
                prompt_context,
                spec_review,
                cli=spec_review.default_cli,
            )
            diagnostics.append(
                Diagnostic(
                    Severity.INFO,
                    "resource-spec-review-prompt",
                    "spec-review prompt resolved from the "
                    f"{resolved_prompt.template.source} tier",
                    source=resolved_prompt.template.source,
                )
            )
        except (KernelError, OSError, UnicodeDecodeError) as error:
            diagnostics.append(
                Diagnostic(
                    Severity.FATAL,
                    "resource-spec-review-prompt",
                    f"spec-review prompt could not be resolved: {error}",
                )
            )

    # Artifact-path + gate-summary roll-up (need a resolved snapshot).
    if snapshot is not None:
        try:
            from heddle.runtime.verification import verification_statuses

            verification_statuses(config.root, snapshot.state)
        except KernelError as error:
            diagnostics.append(
                Diagnostic(
                    Severity.FATAL,
                    "verification-evidence-invalid",
                    error.message,
                )
            )
        if is_terminal(snapshot.state):
            from heddle.runtime.validate import check_workspace

            diagnostics.extend(check_workspace(config.root, snapshot))
        diagnostics.append(_artifact_diagnostic(config.root, snapshot))
        diagnostics.append(_gate_summary_diagnostic(snapshot))
        needs_classification = _needs_classification_diagnostic(snapshot)
        if needs_classification is not None:
            diagnostics.append(needs_classification)

    return _report(
        diagnostics,
        feature=feature,
        project_root=config.root,
    )


def _probe_assets() -> tuple[str, ...]:
    """Return the complete derived corpus in stable first-seen order."""
    return tuple(
        dict.fromkeys(
            (
                *(gate.prompt_template for gate in GATES.values()),
                *(f"{stage}.briefing.md" for stage in STAGES),
                *_SCAFFOLD_ASSETS,
                *_STATIC_PROBE_ASSETS,
            )
        )
    )


def _runner_diagnostics(config: ProjectConfig) -> list[Diagnostic]:
    """Report executable presence for enabled runners without spawning them."""
    diagnostics: list[Diagnostic] = []
    for agent in AGENTS_KEYS:
        if not config.agents.get(agent, False):
            continue
        executable = shutil.which(agent)
        if executable is None:
            message = f"{agent} executable not found on PATH"
            source = "absent"
        else:
            message = f"{agent} executable found at {Path(executable).resolve()}"
            source = "present"
        diagnostics.append(
            Diagnostic(
                Severity.INFO,
                f"runner-{agent}",
                message,
                source=source,
            )
        )
    return diagnostics


def _lock_diagnostics(config: ProjectConfig) -> list[Diagnostic]:
    """Validate lock syntax and require every recorded target to be present."""
    try:
        entries = read_init_lock(config.root)
    except KernelError as error:
        code = (
            "lock-missing" if error.reason == INIT_LOCK_MISSING else "lock-unparseable"
        )
        return [
            Diagnostic(
                Severity.FATAL,
                code,
                error.message,
                source=".heddle.lock",
            )
        ]

    path_faults: list[Diagnostic] = []
    for entry in entries:
        target = config.root / entry.path
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            mode = None
        except OSError as error:
            relative = entry.path.as_posix()
            path_faults.append(
                Diagnostic(
                    Severity.FATAL,
                    "lock-recorded-path-unreadable",
                    (
                        f"{relative} is recorded by .heddle.lock but could not "
                        f"be inspected: {type(error).__name__}: {error}"
                    ),
                    source=relative,
                )
            )
            continue
        if mode is None or not stat.S_ISREG(mode):
            relative = entry.path.as_posix()
            path_faults.append(
                Diagnostic(
                    Severity.FATAL,
                    "lock-recorded-path-missing",
                    f"{relative} is recorded by .heddle.lock but missing",
                    source=relative,
                )
            )
    if path_faults:
        return path_faults
    return [
        Diagnostic(
            Severity.INFO,
            "lock-ok",
            f".heddle.lock is valid and all {len(entries)} recorded paths are present",
            source=".heddle.lock",
        )
    ]


def _config_diagnostics(config: ProjectConfig) -> list[Diagnostic]:
    diagnostics = [
        Diagnostic(Severity(diagnostic.severity), diagnostic.code, diagnostic.message)
        for diagnostic in config.diagnostics
    ]
    if not diagnostics:
        diagnostics.append(
            Diagnostic(Severity.INFO, "config-ok", f"{HEDDLE_CONFIG_FILENAME} is valid")
        )
    return diagnostics


def _artifact_diagnostic(root: Path, snapshot: FeatureSnapshot) -> Diagnostic:
    artifacts = snapshot.artifacts
    retained = snapshot.state.review_assignments.retained
    if not artifacts and not retained:
        return Diagnostic(
            Severity.INFO, "artifact-none", "no gate artifacts recorded yet"
        )
    try:
        validate_retained_reviews(root, snapshot)
    except KernelError as error:
        return Diagnostic(
            Severity.ADVISORY,
            "artifact-required-invalid",
            f"required gate evidence is unavailable or changed: {error.message}",
        )
    return Diagnostic(
        Severity.INFO,
        "artifact-ok",
        f"all {len(artifacts) + len(retained)} required gate artifact(s) are valid",
    )


def _gate_summary_diagnostic(snapshot: FeatureSnapshot) -> Diagnostic:
    gates = snapshot.state.gates
    runs = sum(len(gate.runs) for gate in gates)
    return Diagnostic(
        Severity.INFO,
        "gate-summary",
        f"{len(gates)} gate(s) with {runs} recorded run(s)",
    )


def _needs_classification_diagnostic(snapshot: FeatureSnapshot) -> Diagnostic | None:
    """An ADVISORY when any recorded gate run carries unclassified findings
    (``by_classification.unknown > 0``). An unclassified finding creates no
    decision fact and no in-phase action, so ``doctor`` is its only surface
    (Feature Spec Boundary conditions, the unknown-classified case). Read
    defensively — the shape is kernel-validated, but a roll-up must not
    raise on a partial mapping."""
    unknown = 0
    for gate in snapshot.state.gates:
        for run in gate.runs:
            by_classification = run.findings.get("by_classification", {})
            if isinstance(by_classification, dict):
                unknown += int(by_classification.get("unknown", 0) or 0)
    if unknown == 0:
        return None
    return Diagnostic(
        Severity.ADVISORY,
        "gate-needs-classification",
        f"{unknown} recorded gate finding(s) are unclassified — classify them "
        "(no decision fact or in-phase action is created for an unclassified "
        "finding)",
    )


def _report(
    diagnostics: list[Diagnostic],
    *,
    feature: str | None,
    project_root: Path | None,
) -> HeddleResult:
    next_actions = _report_next_actions(diagnostics, project_root=project_root)
    severities = {diagnostic.severity for diagnostic in diagnostics}
    if Severity.FATAL in severities:
        fatal_rows = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.severity is Severity.FATAL
        ]
        fatal = next(
            (
                diagnostic
                for diagnostic in fatal_rows
                if diagnostic.code == "verification-evidence-invalid"
            ),
            fatal_rows[0],
        )
        result = HeddleResult.failure(
            HeddleError(
                code="workspace-invalid",
                message=f"doctor found fatal integration faults: {fatal.message}",
                hint="resolve the fatal diagnostics and re-run `heddle doctor`",
            ),
            exit_code=ExitCode.FATAL,
            diagnostics=tuple(diagnostics),
            next_actions=next_actions,
        )
    else:
        advisory = Severity.ADVISORY in severities
        # Categories are derived from the diagnostic codes actually emitted —
        # the category word is the code prefix (``config-ok`` → ``config``) — so
        # a consumer can distinguish "checked" from "skipped" (e.g. artifact/gate
        # are absent when no snapshot resolved). Not a static five-item menu.
        categories = sorted(
            {diagnostic.code.split("-", 1)[0] for diagnostic in diagnostics}
        )
        result = HeddleResult.success(
            {
                "feature": feature,
                "healthy": not advisory,
                "categories": categories,
            },
            diagnostics=tuple(diagnostics),
            next_actions=next_actions,
            exit_code=ExitCode.ADVISORY if advisory else ExitCode.OK,
        )
    return result


def _report_next_actions(
    diagnostics: list[Diagnostic],
    *,
    project_root: Path | None,
) -> tuple[NextAction, ...]:
    codes = {diagnostic.code for diagnostic in diagnostics}
    unreadable = next(
        (
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code == "lock-recorded-path-unreadable"
        ),
        None,
    )
    if unreadable is not None:
        source = unreadable.source or "."
        target = Path(source)
        if project_root is not None:
            target = project_root / target
        return (
            NextAction(
                action=ops.ManualAction(f"ls -ld -- {shlex.quote(str(target))}"),
                reason=(
                    "inspect and restore access to every unreadable "
                    "lock-recorded target before rerunning doctor"
                ),
            ),
        )
    if "lock-recorded-path-missing" in codes:
        return (
            NextAction(
                action=ops.CommandAction(ops.Init(dry_run=True)),
                reason="restore every lock-recorded target before rerunning doctor",
            ),
        )
    if "lock-missing" in codes:
        return (
            NextAction(
                action=ops.CommandAction(ops.Init()),
                reason="complete or recover host adoption before rerunning doctor",
            ),
        )
    if "lock-unparseable" in codes:
        return (
            NextAction(
                action=ops.ManualAction("${EDITOR:-vi} .heddle.lock"),
                reason="restore a valid .heddle.lock, then rerun heddle doctor",
            ),
        )
    if "parse-zero-features" in codes:
        return (
            NextAction(
                action=ops.ManualAction(
                    "heddle feature prepare <slug> --area <area> "
                    "--from-file <research-input.yaml>"
                ),
                reason=(
                    "record research and explicitly confirm policy before feature start"
                ),
            ),
        )
    return ()


def _is_missing_project_root(error: KernelError) -> bool:
    return error.reason == MISSING_PROJECT_ROOT


def _feature_ambiguous_failure(
    error: KernelError, diagnostics: list[Diagnostic]
) -> HeddleResult:
    """An active-feature ambiguity → exit 2, mirroring `kickoff`/`run-gate`
    and returns a usage failure. It names candidate workspaces in a `feature switch`
    next-action and carries the pre-resolution diagnostics forward."""
    candidates = getattr(error, "candidates", ()) or ()
    reason = "select the active feature"
    if candidates:
        reason += f" (candidates: {', '.join(candidates)})"
    return HeddleResult.failure(
        HeddleError(
            code=error.code,
            message=error.message,
            hint=error.hint,
            details=error.details,
        ),
        exit_code=ExitCode.USAGE,
        diagnostics=tuple(diagnostics),
        next_actions=(
            NextAction(ops.ManualAction("heddle feature switch <slug>"), reason=reason),
        ),
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        error = result.error
        print(f"heddle: error[{error.code}]: {error.message}", file=sys.stderr)
        print(f"  hint: {error.hint}", file=sys.stderr)
    for diagnostic in result.diagnostics:
        print(f"{diagnostic.severity.value}: {diagnostic.code}: {diagnostic.message}")
