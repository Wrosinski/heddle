"""Create-only admission from confirmed intake, with state as the commit point.

The persisted intake manifest owns interrupted scaffold recovery. A committed
intake binding owns exact retry, and the shared workspace checker validates
both initial publication and readback.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.result import (
    Diagnostic,
    ExitCode,
    HeddleError,
    HeddleResult,
    NextAction,
    Severity,
)
from heddle.contracts.schemas import FLOW_MODES
from heddle.io.git import capture_head_commit
from heddle.kernel.model import resolve_snapshot
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    load_project_config_from_cwd,
)
from heddle.kernel.resources import resolve_resource
from heddle.kernel.state import SourceBaseline, parse_source_baseline
from heddle.kernel.write_path import seed_state
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.diagnostics import kernel_error_result
from heddle.runtime.output import emit_envelope
from heddle.runtime.state_store import check_expect_revision, create_state
from heddle.runtime.validate import check_workspace
from heddle.runtime.write_path import Conflict

_PLACEHOLDERS = ("slug", "area", "date")

# The ratified slug grammar uses lowercase kebab case with a leading
# letter/digit. The slug becomes a directory name, a spec filename, and
# the feature: scalar, so anything looser creates workspaces other
# surfaces cannot address uniformly.
_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")

_SCAFFOLD_ASSETS = (
    "feature-spec.scaffold.md",
    "plan.scaffold.md",
    "brief.scaffold.md",
)

_USAGE_HINT = (
    "usage: heddle feature start <slug> [--area <confirmed-area>] "
    "[--flow hitl|auto] [--expect-revision <n>] [--dry-run]"
)

_EXITS = {
    "usage": ExitCode.USAGE,
    "workspace-exists": ExitCode.FATAL,
    "workspace-invalid": ExitCode.FATAL,
}


def render_scaffold(template_text: str, values: Mapping[str, str]) -> str:
    rendered = template_text
    for key in _PLACEHOLDERS:
        rendered = rendered.replace("{{" + key + "}}", values[key])
    return rendered


@dataclass(frozen=True)
class _StartArgs:
    slug: str
    area: str | None
    flow: str | None
    expect_revision: int | None
    dry_run: bool


@dataclass(frozen=True)
class _CreatePaths:
    """One source for the create-path topology: physical write
    destinations and the payload's repo-relative strings must never drift
    apart. ``created_paths`` lists state.yaml last and the writes land
    exactly there."""

    workspace: Path
    state_path: Path
    spec_path: Path
    workspace_rel: str
    spec_rel: str
    created_paths: tuple[str, ...]


def _create_paths(config: ProjectConfig, *, area: str, slug: str) -> _CreatePaths:
    plans_rel = config.layout.plans
    workspace_rel = f"{plans_rel}/{slug}/"
    spec_rel = f"{config.layout.specs}/{area}/{slug}.md"
    workspace = config.root / plans_rel / slug
    return _CreatePaths(
        workspace=workspace,
        state_path=workspace / "state.yaml",
        spec_path=config.root / config.layout.specs / area / f"{slug}.md",
        workspace_rel=workspace_rel,
        spec_rel=spec_rel,
        created_paths=(
            spec_rel,
            f"{workspace_rel}brief.md",
            f"{workspace_rel}plan.md",
            f"{workspace_rel}reviews/",
            f"{workspace_rel}verification/",
            f"{workspace_rel}state.yaml",
        ),
    )


def run_feature_start(args: list[str], json_mode: bool) -> int:
    parsed = _parse_args(args)
    if isinstance(parsed, HeddleResult):
        return _emit(parsed, json_mode)
    from heddle.runtime.application import execute

    operation = ops.FeatureStart(
        parsed.slug,
        area=parsed.area,
        flow=parsed.flow,
        expect_revision=parsed.expect_revision,
        dry_run=parsed.dry_run,
    )
    return _emit(execute(operation), json_mode)


# ---------------------------------------------------------------------------
# argv parsing — every usage fault exits 2 before any filesystem access, so
# validation happens strictly before config resolution.
# ---------------------------------------------------------------------------


def _parse_args(args: list[str]) -> _StartArgs | HeddleResult:
    slug: str | None = None
    area: str | None = None
    flow: str | None = None
    expect_revision: int | None = None
    dry_run = False
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--area" and index + 1 < len(args):
            area = args[index + 1]
            index += 2
            continue
        if token == "--flow" and index + 1 < len(args):
            flow = args[index + 1]
            index += 2
            continue
        if token == "--expect-revision" and index + 1 < len(args):
            try:
                expect_revision = int(args[index + 1])
            except ValueError:
                return _usage("--expect-revision must be an integer")
            if expect_revision < 0:
                return _usage("--expect-revision must be nonnegative")
            index += 2
            continue
        if token == "--dry-run":
            dry_run = True
            index += 1
            continue
        if token.startswith("-"):
            return _usage(f"unrecognized argument {token!r}")
        if slug is not None:
            return _usage(
                f"unexpected extra positional argument {token!r} — "
                "feature start takes exactly one <slug>"
            )
        slug = token
        index += 1

    if slug is None:
        return _usage("a <slug> positional argument is required")
    if not _SLUG_PATTERN.fullmatch(slug):
        return _usage(
            f"slug {slug!r} does not match the slug grammar "
            "[a-z0-9][a-z0-9-]* (lowercase kebab, leading letter/digit)"
        )
    if area is not None and (
        "/" in area
        or "\\" in area
        or area in ("", ".", "..")
        # `.` and `..` are path traversal, not plain segments — they
        # would escape docs/features/ before self-validation could
        # object. The boundary rule: whitespace/control characters
        # would render broken frontmatter (`area: run\ntime`) and mint
        # control-character directories before self-validation fires;
        # a leading `-` reads as a flag everywhere else the value is
        # spelled. Areas stay free-form over printable segment
        # characters.
        or area.startswith("-")
        or any(ch.isspace() or not ch.isprintable() for ch in area)
    ):
        return _usage(
            f"area {area!r} must be a plain path segment "
            "(no separators, whitespace, or control characters; "
            "not '.' or '..'; no leading '-')"
        )
    if flow is not None and flow not in FLOW_MODES:
        return _usage(f"flow is {flow!r}, not one of {list(FLOW_MODES)}")

    return _StartArgs(
        slug=slug,
        area=area,
        flow=flow,
        expect_revision=expect_revision,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# create path
# ---------------------------------------------------------------------------


def feature_start(parsed: ops.FeatureStart) -> HeddleResult:
    import hashlib
    from contextlib import nullcontext

    from heddle.kernel.feature_policy import parse_policy
    from heddle.runtime import intake
    from heddle.runtime.locking import state_recording_lock
    from heddle.runtime.state_store import read_state_document, replace_file_text

    try:
        intake.validate_identity(parsed.slug, parsed.area)
        config = load_project_config_from_cwd()
        intake_file = intake.intake_path(config, parsed.slug)
        intake.safe_target(config.root, intake_file)
        with nullcontext() if parsed.dry_run else state_recording_lock(intake_file):
            document = intake.read_intake(config, parsed.slug)
            if conflict := check_expect_revision(
                parsed.expect_revision, document["revision"]
            ):
                from heddle.runtime.diagnostics import conflict_failure

                return conflict_failure(conflict)
            paths = _create_paths(config, area=document["area"], slug=parsed.slug)
            intake.safe_target(config.root, paths.state_path)
            if paths.state_path.exists():
                state = read_state_document(paths.state_path)
                binding = {
                    "path": intake_file.relative_to(config.root).as_posix(),
                    "sha256": intake.intake_digest(document),
                }
                if state.get("intake") != binding or state["feature"] != parsed.slug:
                    raise intake.invalid(
                        "existing state is not owned by this intake admission"
                    )
                return _self_validate(
                    config,
                    parsed.slug,
                    {
                        "feature": parsed.slug,
                        "revision": state["revision"],
                        "reused": True,
                        "effective_policy": intake.policy_projection(
                            parse_policy(state["feature_policy"])
                        ),
                        **(
                            {"source_baseline": state["source_baseline"]}
                            if "source_baseline" in state
                            else {}
                        ),
                    },
                )
            if document["route"] != "heddle":
                raise intake.invalid("direct route needs no formal workspace")
            if document["feature_policy"] is None:
                raise intake.invalid(
                    "confirm the complete feature policy before creating documents"
                )
            if parsed.area is not None and parsed.area != document["area"]:
                raise intake.invalid("area differs from the confirmed intake")
            if parsed.flow is not None and parsed.flow not in FLOW_MODES:
                raise intake.invalid("flow must be hitl or auto")
            research = document["research"]
            if (
                intake.research_identity(config.root, research["reference"])
                != research["sha256"]
            ):
                raise intake.invalid("research changed after confirmation")
            file_paths = {
                paths.spec_rel: "feature-spec.scaffold.md",
                f"{paths.workspace_rel}plan.md": "plan.scaffold.md",
                f"{paths.workspace_rel}brief.md": "brief.scaffold.md",
            }
            directories = [
                paths.workspace_rel.rstrip("/"),
                f"{paths.workspace_rel}reviews",
                f"{paths.workspace_rel}verification",
            ]
            expected = set(file_paths) | set(directories)
            admission = document["create_manifest"]
            if admission is not None and document["schema"] == intake.INTAKE_SCHEMA_V1:
                raise intake.invalid(
                    "heddle.intake/v1 interrupted admission has no frozen source "
                    "baseline; preserve it under a compatible runtime or prepare "
                    "a new feature"
                )
            flow = parsed.flow or config.autopilot.flow
            if admission is not None:
                if parsed.flow is not None and parsed.flow != admission["flow"]:
                    raise intake.invalid("flow differs from the interrupted admission")
                flow = admission["flow"]
            manifest = admission["files"] if admission is not None else None
            source_baseline: SourceBaseline | None = (
                parse_source_baseline(admission["source_baseline"])
                if admission is not None
                else None
            )
            if manifest is None:
                for relative in expected:
                    path = config.root / relative
                    intake.safe_target(config.root, path)
                    if path.exists():
                        raise intake.invalid(
                            f"unknown existing admission path: {relative}"
                        )
                values = {
                    "slug": parsed.slug,
                    "area": document["area"],
                    "date": utc_now_minutes()[:10],
                }
                manifest = [
                    {
                        "path": relative,
                        "kind": "directory",
                        "content": None,
                        "sha256": None,
                    }
                    for relative in directories
                ]
                for relative, asset in file_paths.items():
                    template, _source = resolve_resource(asset, config)
                    rendered = render_scaffold(
                        template.read_text(encoding="utf-8"), values
                    )
                    manifest.append(
                        {
                            "path": relative,
                            "kind": "file",
                            "content": rendered,
                            "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                        }
                    )
                source_baseline = SourceBaseline(
                    kind="git-commit", oid=capture_head_commit(config.root)
                )
            assert source_baseline is not None
            if {row["path"] for row in manifest} != expected:
                raise intake.invalid(
                    "create manifest paths differ from the declared admission"
                )
            if paths.workspace.exists():
                unknown = [
                    p.relative_to(config.root).as_posix()
                    for p in paths.workspace.rglob("*")
                    if p.relative_to(config.root).as_posix() not in expected
                ]
                if unknown:
                    raise KernelError(
                        code="workspace-invalid",
                        message=f"unknown content in interrupted workspace: {unknown}",
                        hint=(
                            "inspect and preserve the listed paths; move any "
                            "unrelated or abandoned files outside the workspace, "
                            f"then retry `heddle feature start {parsed.slug}`; "
                            "do not delete unfamiliar content"
                        ),
                    )
            for row in manifest:
                target = config.root / row["path"]
                intake.safe_target(config.root, target)
                if row["kind"] != (
                    "file" if row["path"] in file_paths else "directory"
                ):
                    raise intake.invalid(
                        "create manifest path type differs from admission"
                    )
                if target.exists() and (
                    not target.is_dir()
                    if row["kind"] == "directory"
                    else not target.is_file()
                    or bool(target.stat().st_mode & 0o111)
                    or hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]
                ):
                    raise intake.invalid(
                        "changed content is not recoverable admission evidence: "
                        f"{row['path']}"
                    )
            data = {
                "feature": parsed.slug,
                "workspace": paths.workspace_rel,
                "spec": paths.spec_rel,
                "effective_policy": intake.policy_projection(
                    parse_policy(document["feature_policy"])
                ),
                "dry_run": parsed.dry_run,
                "reused": False,
                "source_baseline": asdict(source_baseline),
            }
            if parsed.dry_run:
                return HeddleResult.success(data)
            if document["create_manifest"] is None:
                document["schema"] = intake.INTAKE_SCHEMA_V2
                document["create_manifest"] = {
                    "files": manifest,
                    "flow": flow,
                    "source_baseline": asdict(source_baseline),
                }
                document["revision"] += 1
                document["updated"] = utc_now_minutes()
                intake.publish_intake(intake_file, document)
            paths.spec_path.parent.mkdir(parents=True, exist_ok=True)
            for relative in directories:
                (config.root / relative).mkdir(parents=True, exist_ok=True)
            for row in manifest:
                target = config.root / row["path"]
                if row["kind"] == "file" and not target.exists():
                    replace_file_text(target, row["content"], create=True)
            seed = seed_state(
                slug=parsed.slug,
                spec=paths.spec_rel,
                feature_policy=document["feature_policy"],
                flow=flow,
                authorized_through="specify",
                at=document["updated"],
                source_baseline=source_baseline,
            )
            seed["intake"] = {
                "path": intake_file.relative_to(config.root).as_posix(),
                "sha256": intake.intake_digest(document),
            }
            create_state(paths.state_path, seed)
            read_state_document(paths.state_path)
            return _self_validate(
                config, parsed.slug, {**data, "revision": seed["revision"]}
            )
    except KernelError as error:
        return kernel_error_result(error, exit_codes=_EXITS)
    except (OSError, ValueError) as error:
        return kernel_error_result(intake.invalid(str(error)), exit_codes=_EXITS)


def _self_validate(
    config: ProjectConfig,
    slug: str,
    data: dict[str, Any],
) -> HeddleResult:
    """Re-resolve the just-written workspace and run the
    shared cross-plane checks against it — success is never reported on a
    workspace validate would immediately flag."""
    try:
        snapshot = resolve_snapshot(config, slug)
        findings = check_workspace(config.root, snapshot)
    except KernelError as error:
        return _self_validation_failure(
            slug,
            config.layout.plans,
            failing=f"{error.code}: {error.message}",
        )
    fatal = [f for f in findings if f.severity is Severity.FATAL]
    if fatal:
        failing = "; ".join(f"{f.code}: {f.message}" for f in fatal)
        return _self_validation_failure(
            slug,
            config.layout.plans,
            failing=failing,
            findings=tuple(findings),
        )
    data["validated"] = True
    return HeddleResult.success(
        data,
        diagnostics=tuple(findings),
        next_actions=(
            NextAction(
                action=ops.CommandAction(ops.FeatureSwitch(slug)),
                reason="make the new feature active",
            ),
        ),
    )


def _self_validation_failure(
    slug: str,
    plans_rel: str,
    *,
    failing: str,
    findings: tuple[Diagnostic, ...] = (),
) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            code="workspace-invalid",
            message=(f"self-validation failed on the created workspace — {failing}"),
            hint=(
                f"the committed {plans_rel}/{slug}/ and spec are preserved; "
                "repair the reported document mismatch, run heddle validate, "
                "then retry feature start for readback"
            ),
        ),
        exit_code=ExitCode.FATAL,
        diagnostics=findings,
    )


def _usage(message: str) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(code="usage", message=message, hint=_USAGE_HINT),
        exit_code=ExitCode.USAGE,
    )


def _conflict_failure(error: Conflict) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            code="conflict",
            message=str(error),
            hint="re-read the feature state and retry the command",
        ),
        exit_code=ExitCode.CONFLICT,
        next_actions=(
            NextAction(
                action=ops.CommandAction(ops.Status()),
                reason="refresh the active feature state before retrying",
            ),
        ),
    )


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        error = result.error
        print(f"heddle: error[{error.code}]: {error.message}")
        print(f"  hint: {error.hint}")
        return
    data = result.data or {}
    print(
        f"feature start [{data.get('feature')}]: "
        f"{'preview' if data.get('dry_run') else 'admitted'} "
        "with confirmed feature policy"
    )
    for diagnostic in result.diagnostics:
        print(f"  {diagnostic.severity.value}: {diagnostic.message}")
    for action in result.next_actions:
        print(f"  next: {action.command} — {action.reason}")
