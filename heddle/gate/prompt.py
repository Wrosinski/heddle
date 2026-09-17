from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from heddle.gate.extraction import (
    extract_acceptance_criteria_blocks,
    extract_markdown_h2_section,
    extract_markdown_section,
)
from heddle.gate.types import (
    DiffResult,
    GateContext,
    GateType,
    OverlapResult,
    PreflightResult,
    PreparedDiff,
    PromptTransport,
    ResolvedGatePrompt,
    ResolvedResource,
)
from heddle.kernel.decision_guidance import (
    DecisionGuidance,
    compose_decision_guidance,
    load_decision_guidance,
)
from heddle.kernel.project_config import (
    DEFAULT_AUTOPILOT,
    DEFAULT_LAYOUT,
    HEDDLE_CONFIG_FILENAME,
    KernelError,
    ProjectConfig,
    load_project_config,
)
from heddle.kernel.resources import resolve_host_override

# The packaged prompt corpus ships *inside* the distribution, beside the other
# packaged resources (briefings, scaffolds). `prompt_template` values stay
# host-shaped (``prompts/<gate>.md``) so the override and generated tiers —
# and any host that projects a prompt tree — are unaffected by where the floor
# physically lives.
_PACKAGED_RESOURCES_ROOT = Path(__file__).resolve().parents[1] / "resources"
PACKAGED_PROMPTS_DIR = _PACKAGED_RESOURCES_ROOT / "prompts"


def packaged_prompt_path(prompt_template: str) -> Path:
    """The installed-package floor for ``prompt_template``.

    Kept as one named seam so the floor's location is stated once: every
    caller that needs the packaged corpus (resolution, the corpus sweeps, the
    convention checker) derives it from here rather than re-deriving a path
    relative to some file's position in the source tree."""
    return (_PACKAGED_RESOURCES_ROOT / prompt_template).resolve()


def resolve_tests_root(repo_root: Path) -> str:
    """The repo-relative tests root is `.heddle.yaml` `layout.tests`;
    hosts without a config — fixture repos in the gate test suite —
    get the loader default. A config that exists but fails to load is
    surfaced as the engine's operator-error idiom, never papered over
    with the default root."""
    if (repo_root / HEDDLE_CONFIG_FILENAME).is_file():
        try:
            return load_project_config(repo_root).layout.tests
        except KernelError as error:
            raise ValueError(
                f"cannot resolve the tests root: {HEDDLE_CONFIG_FILENAME} "
                f"at {repo_root} failed to load — {error.message} "
                f"({error.hint})"
            ) from error
    return DEFAULT_LAYOUT.tests


def _resolve_prompt_template(gate_type: GateType, repo_root: Path) -> Path:
    """The gate's prompt template, resolved by the host-tier precedence
    (override > generated) over the engine's own packaged template: a
    host projection of ``<gate>.prompt.md`` wins; otherwise the engine's
    bundled ``prompt_template`` is the packaged floor."""
    return _resolve_prompt_template_with_source(gate_type, repo_root)[0]


def _resolve_prompt_template_with_source(
    gate_type: GateType, repo_root: Path
) -> tuple[Path, Literal["override", "generated", "packaged"]]:
    """Resolve one prompt without consulting process CWD.

    The installed-host tiers use ``<gate>.prompt.md``. A host's own
    ``prompts/<gate>.md`` remains the generated-development projection used by
    source checkouts and fixture hosts; the packaged corpus inside the
    installed distribution is the floor beneath it.

    The floor lives at ``heddle/resources/prompts/``. It was
    previously derived from the *descriptor's* source tree
    (``Path(__file__).parents[2]``), which resolves to the checkout one level
    above the package directory — a path that exists only in a source checkout
    and never inside a wheel, so every gate raised ``FileNotFoundError`` from
    an installed artifact. Resolving under ``PACKAGED_PROMPTS_DIR`` keeps the
    corpus inside the distribution, where ``packages = ["heddle"]`` ships it.

    The identity check below is now a symlink guard only. Before A1 it carried
    real weight: the Heddle repo's own corpus *was* the floor, so the two
    candidate paths were the same file and it decided the reported provenance.
    A1 separated them — the packaged corpus no longer sits at any repo root —
    so it now only stops a host that symlinks its projection at the packaged
    tree from being reported as a distinct generated projection.
    """
    host = _host_override_template(gate_type, repo_root)
    if host is not None:
        path, source = host
        return path, cast(Literal["override", "generated"], source)

    packaged_path = packaged_prompt_path(gate_type.prompt_template)
    generated_path = repo_root / gate_type.prompt_template
    if generated_path.is_file() and generated_path.resolve() != packaged_path:
        return generated_path, "generated"
    if packaged_path.is_file():
        return packaged_path, "packaged"
    raise FileNotFoundError(f"Prompt template not found: {gate_type.prompt_template}")


PARTIALS_DIRNAME = "_partials"
_PARTIAL_TOKEN = re.compile(r"\[partial-([a-z0-9-]+)\]")

# The shared contract every gate prompt is written against. A change here
# changes what every gate is asked to do, so it belongs in the prompt version.
# Packaged beside the corpus it governs: every install carries it,
# so version buckets are comparable across repos and a missing file means a
# broken installation rather than a host that never had the document.
PACKAGED_STANDARDS_DOC = _PACKAGED_RESOURCES_ROOT / "prompt-authoring-standards.md"


def partials_dir_for(template_path: Path) -> Path:
    """The partials tree that serves ``template_path``: the ``_partials/``
    directory beside it. A template and the partials it splices must come
    from the same tree — heddle's template already resolves from different
    roots by context (host-tier ``.heddle/`` projection vs the repo-tree
    ``prompts/``), and pairing a template from one tree with partials from
    another loads the template fine while the rules it references go missing.
    Deriving the partials root from the *resolved* template makes the two a
    unit under every resolution path, including any future packaged floor."""
    return template_path.parent / PARTIALS_DIRNAME


@dataclass(frozen=True)
class _CapturedPartial:
    path: Path
    effective_text: str
    authoring_text: str


@dataclass(frozen=True)
class _ExpandedTemplate:
    visible_template: str
    expanded: str
    partials: Mapping[str, _CapturedPartial]

    def authoring_by_name(self) -> dict[str, str]:
        return {name: partial.authoring_text for name, partial in self.partials.items()}


def expand_partials(
    template: str,
    partials_dir: Path,
    *,
    template_resource: Path | None = None,
) -> str:
    """Splice shared prompt blocks into the template at build time.

    A rule that binds many gates lives once, in ``_partials/<name>.md``
    beside the template, and is rendered *inline* wherever
    ``[partial-<name>]`` appears. Referencing the shared text by anchor
    instead would be cheaper to write and worse to run: a CLI-invoked gate
    agent does not reliably read a reference file mid-run
    (prompt-authoring-standards.md, packaged), so a cited rule is a rule
    the model may never see. Inlining keeps one source of truth on disk *and*
    the full text in the model's context.

    A missing partial raises a typed workspace error. A gate that silently
    shipped without its severity taxonomy or its finding structure would still produce
    confident-looking output, and the loss would surface only as degraded
    findings weeks later. This fail-loud error is also the split-brain rail:
    a host that overrides a template without projecting its ``_partials/``
    fails at render time instead of reviewing under silently absent rules.

    Expansion is a single pass (flat-only, matching the pinned source): a
    partial whose body itself contains ``[partial-x]`` is NOT recursively
    expanded — the inner token survives literally, where the corpus render
    sweep flags it as a stray. Partials are leaf rule blocks by contract;
    nesting would reintroduce the indirection inlining exists to remove."""

    result = _capture_and_expand_partials(
        template,
        partials_dir,
        template_resource=template_resource or Path("<template>"),
    )
    return result.expanded


def _partial_logical_name(name: str) -> str:
    """Return the reviewer-facing logical path for one partial."""
    return f"prompts/_partials/{name}.md"


def _capture_and_expand_partials(
    template: str,
    partials_dir: Path,
    *,
    template_resource: Path,
) -> _ExpandedTemplate:
    """Strip comments, capture each live partial once, and expand every use."""
    visible_template = _strip_author_comments(template, template_resource)
    captured: dict[str, _CapturedPartial] = {}
    for match in _PARTIAL_TOKEN.finditer(visible_template):
        name = match.group(1)
        if name in captured:
            continue
        partial_path = partials_dir / f"{name}.md"
        if not partial_path.is_file():
            raise KernelError(
                code="workspace-invalid",
                message=(
                    f"prompt partial {name}.md was not found "
                    f"(referenced as [partial-{name}])"
                ),
                hint="restore the referenced partial in the resolved prompt tree",
            )
        authoring_text = partial_path.read_text(encoding="utf-8").strip()
        effective_text = _strip_author_comments(
            authoring_text, Path(_partial_logical_name(name))
        ).strip()
        captured[name] = _CapturedPartial(
            path=partial_path,
            effective_text=effective_text,
            authoring_text=authoring_text,
        )
    expanded = expand_captured_partials(
        visible_template,
        {name: partial.effective_text for name, partial in captured.items()},
    )
    return _ExpandedTemplate(
        visible_template=visible_template,
        expanded=expanded,
        partials=captured,
    )


def expand_captured_partials(template: str, bodies: Mapping[str, str]) -> str:
    """Expand captured partial bodies once; nested tokens remain literal."""
    return _PARTIAL_TOKEN.sub(lambda match: bodies[match.group(1)], template)


def resolve_gate_prompt(
    context: GateContext,
    gate_type: GateType,
    *,
    cli: str | None = None,
    basis_only: bool = False,
) -> ResolvedGatePrompt:
    """Return the effective prompt and the provenance of every source byte.

    Template and partials resolve as one tree, partial expansion is flat and
    occurrence-ordered, and validation-only authoring metadata is removed
    before the reviewer-facing value is exposed. ``cli`` is optional for the
    public two-argument contract; callers preparing a concrete lane pass the
    already selected lane explicitly.
    """
    del basis_only
    selected_cli = cli or gate_type.default_cli
    template_path, source = _resolve_prompt_template_with_source(
        gate_type, context.repo_root
    )
    template_text = template_path.read_text(encoding="utf-8")
    partials_dir = partials_dir_for(template_path)
    captured = _capture_and_expand_partials(
        template_text,
        partials_dir,
        template_resource=Path(gate_type.prompt_template),
    )
    partials = tuple(
        ResolvedResource(
            logical_name=_partial_logical_name(name),
            path=partial.path,
            source=source,
            effective_text=partial.effective_text,
            authoring_text=partial.authoring_text,
        )
        for name, partial in captured.partials.items()
    )
    authoring_by_name = captured.authoring_by_name()
    variables = {
        "feature": context.feature,
        "spec-path": _reviewer_path(context, context.spec_path),
        "plan-path": _reviewer_path(context, context.plan_path),
        "milestone": str(context.milestone or ""),
        "cli-tool": selected_cli,
        "review-file-suffix": ("" if selected_cli == "claude" else f".{selected_cli}"),
    }
    rendered = render_template(captured.expanded, variables, strip_validation=True)
    policy = _decision_policy(context.repo_root)
    logical_instructions = compose_decision_guidance(policy, rendered.strip())
    constraint = output_constraint(selected_cli)
    effective = "\n\n".join([constraint, logical_instructions])
    transport = PromptTransport(
        system=constraint if selected_cli == "claude" else None,
        stdin=logical_instructions if selected_cli == "claude" else effective,
    )
    return ResolvedGatePrompt(
        gate=gate_type.name,
        template=ResolvedResource(
            logical_name=gate_type.prompt_template,
            path=template_path,
            source=source,
            effective_text=render_template(
                captured.visible_template,
                {},
                strip_validation=True,
            ),
            authoring_text=template_text,
        ),
        partials=partials,
        decision_policy=policy,
        logical_instructions=logical_instructions,
        effective_instructions=effective,
        prompt_version=_prompt_version_digest(
            template_text, authoring_by_name, policy.text
        ),
        effective_prompt_sha256=hashlib.sha256(effective.encode("utf-8")).hexdigest(),
        output_constraint=constraint,
        transport=transport,
    )


def _decision_policy(repo_root: Path) -> DecisionGuidance:
    config = (
        load_project_config(repo_root)
        if (repo_root / HEDDLE_CONFIG_FILENAME).exists()
        else ProjectConfig(
            repo_root, DEFAULT_LAYOUT, {}, {}, None, DEFAULT_AUTOPILOT, ()
        )
    )
    return load_decision_guidance(config)


def _prompt_version_digest(
    template_text: str, partials: Mapping[str, str], policy: str
) -> str:
    """Hash one prompt's authoring sources with stable bare-name ordering."""
    digest = hashlib.sha256(template_text.encode("utf-8"))
    for name in sorted(partials):
        digest.update(b"\0")
        digest.update(partials[name].encode("utf-8"))
    digest.update(b"\0")
    digest.update(PACKAGED_STANDARDS_DOC.read_bytes())
    digest.update(b"\0decision-routing.md\0")
    digest.update(policy.encode("utf-8"))
    return digest.hexdigest()[:16]


def _strip_author_comments(text: str, path: Path) -> str:
    """Remove well-formed non-nested author comments before token discovery."""
    output: list[str] = []
    cursor = 0
    while cursor < len(text):
        opening = text.find("<!--", cursor)
        closing = text.find("-->", cursor)
        if closing >= 0 and (opening < 0 or closing < opening):
            raise _comment_error(path)
        if opening < 0:
            output.append(text[cursor:])
            break
        output.append(text[cursor:opening])
        closing = text.find("-->", opening + 4)
        if closing < 0 or text.find("<!--", opening + 4, closing) >= 0:
            raise _comment_error(path)
        cursor = closing + 3
    return "".join(output)


def _comment_error(path: Path) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"prompt resource {path.name} contains a malformed author comment",
        hint="use non-nested <!-- ... --> author comments",
    )


def _reviewer_path(context: GateContext, path: Path | str | None) -> str:
    """Return stable reviewer-facing path text without an absolute root."""
    if path is None:
        return ""
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(context.repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return candidate.as_posix()


def compute_prompt_version(gate_type: GateType, repo_root: Path) -> str | None:
    """Hash the prompt this gate would be given: its template, every partial
    the template splices in, and the shared standards doc they are all
    written against.

    This is what makes a prompt edit measurable. The trajectory aggregate can
    already say a gate averages N iterations to converge — but not which
    wording produced that number, so no change to the wording can be shown to
    have helped or hurt. Bucketing the same metric by prompt version turns an
    argument into a measurement required by the measured-gap rule.

    Returns None when the template cannot be resolved; a missing prompt is
    already a loud failure at build time and must not become a summary-write
    failure here."""
    try:
        template_path = _resolve_prompt_template(gate_type, repo_root)
    except FileNotFoundError:
        return None

    # Every read below is best-effort for the same reason the missing-partial
    # skip is: this runs inside the failure-summary write, where a raise would
    # mask the real error. A non-ENOENT OSError (permissions, a TOCTOU race)
    # therefore declines to stamp rather than crash the summary.
    try:
        template = template_path.read_text(encoding="utf-8")
        partials_dir = partials_dir_for(template_path)
        captured = _capture_and_expand_partials(
            template,
            partials_dir,
            template_resource=Path(gate_type.prompt_template),
        )
        # The standards doc ships inside the package, so absence means a broken
        # installation. Decline to stamp rather than hash a silently different
        # input set — a wrong-but-plausible version would split trajectory
        # buckets invisibly, while an absent one is visible in the summary.
        if not PACKAGED_STANDARDS_DOC.is_file():
            return None
        return _prompt_version_digest(
            template, captured.authoring_by_name(), _decision_policy(repo_root).text
        )
    except (OSError, KernelError):
        return None


def _host_override_template(
    gate_type: GateType, repo_root: Path
) -> tuple[Path, str] | None:
    if not (repo_root / HEDDLE_CONFIG_FILENAME).is_file():
        return None
    try:
        config = load_project_config(repo_root)
    except KernelError:
        return None
    return resolve_host_override(f"{gate_type.name}.prompt.md", config)


def render_template(
    template: str,
    variables: dict[str, str],
    *,
    strip_validation: bool = False,
) -> str:
    content = template
    if strip_validation:
        content = re.sub(r"\n## Validation\n.*\Z", "", content, flags=re.DOTALL)
    return re.sub(
        r"\[([a-z0-9_-]+)\]",
        lambda match: variables.get(match.group(1), match.group(0)),
        content,
    )


def output_constraint(cli: str) -> str:
    if cli == "codex":
        return (
            "Return only the review JSON object required by the supplied output "
            "schema in the final assistant message."
        )
    return "Produce the structured review JSON object using the supplied JSON Schema."


def build_runtime_manifest(
    ctx: GateContext,
    diff: DiffResult | PreparedDiff,
    overlap: OverlapResult,
    preflight: PreflightResult,
) -> str:
    payload: dict[str, Any] = {
        "gate": ctx.gate_type.name,
        "feature": ctx.feature,
        "plan": str(ctx.plan_path or ""),
        "spec": str(ctx.spec_path or ""),
        "milestone": {
            "number": ctx.milestone,
            "title": ctx.milestone_title,
            "satisfies_ac_ids": ctx.satisfies_ac_ids,
            "owned_paths": ctx.owned_paths,
        },
        "diff": {
            "source": diff.source,
            "source_detail": diff.source_detail,
            "changed_files": diff.changed_files,
            "untracked_files": diff.untracked_files,
        },
        "overlap": {"status": overlap.status, "files": overlap.files},
        "preflight": {"status": preflight.status, "anomalies": preflight.anomalies},
        "output_contract": None,
    }
    from heddle.gate.results import ReviewOutputContract

    if isinstance(ctx.gate_type.output_contract, ReviewOutputContract):
        contract = ctx.gate_type.output_contract
        payload["output_contract"] = {
            "role": contract.role,
            "version": contract.version,
            "sha256": contract.schema_sha256,
        }
    return (
        "Gate Runtime Manifest (operator-generated; authoritative)\n"
        "===== BEGIN GATE RUNTIME MANIFEST =====\n"
        f"{json.dumps(payload, indent=2)}\n"
        "===== END GATE RUNTIME MANIFEST ====="
    )


def build_rerun_ledger(ctx: GateContext) -> str:
    """The disposition-rerun context: prior-round artifacts and the
    already-adjudicated decision ground, plus the protocol that scopes a
    rerun to disposition verification instead of fresh open-ended discovery
    (`prompt-authoring-standards.md#disposition-rerun`, packaged).

    Verdict gates receive the same blocks — their rerun charter is
    verifying the prior round's dispositions against the revised document,
    with the Status/Rerun verdict semantics unchanged. A gate's first lane
    run still receives the adjudicated decision ground when the pipeline
    already carries decisions. Canonical results and decisions come from
    the captured input; rendering performs no artifact or ledger reads."""
    if ctx.document_review_round == 1:
        return "\n".join(
            (
                "Native document-review assignment context:\n"
                + ctx.document_review_context,
                "This is independent discovery round 1 under the new policy. Retained "
                "legacy reports are historical obligations, not proof this assignment "
                "already ran. Inspect the full scope. Native source context preserves "
                "legacy obligations outside the new assignment's prior_dispositions. "
                "On this initial pass, prior_dispositions and regressions are empty. "
                "Do not inspect the other initial slot.",
                *(
                    f'<prior-review-artifact run_id="{a.run_id}">'
                    f"{a.content}</prior-review-artifact>"
                    for a in ctx.prior_review_artifacts
                ),
                _decision_ground(ctx),
            )
        )
    if ctx.prior_completed_runs < 1:
        return _decision_ground(ctx)
    lines = [
        "Disposition Rerun Ledger (operator-generated; authoritative)",
        "===== BEGIN DISPOSITION RERUN LEDGER =====",
        f"This is completed review {ctx.prior_completed_runs + 1} of this "
        "gate for this scope — a DISPOSITION RERUN, not fresh discovery.",
        "",
        "Protocol (per prompt-authoring-standards.md#disposition-rerun):",
        "- Verify each prior IMPLEMENT finding is addressed in the current "
        "reviewed content; report any that is not, citing the prior "
        "finding ID.",
        "- Report regressions the intervening fixes introduced.",
        "- Do NOT re-raise, reword, or derive variants of the adjudicated "
        "decisions below; a resolved decision is settled ground, and the "
        "absence of enforcement for a documented accepted-risk "
        "assumption is not a finding.",
        "- Do NOT emit findings for pending decisions below; they are "
        "already awaiting a ruling.",
        "- New findings are in scope ONLY inside content the intervening "
        "fixes changed; everything else already had its full review in "
        "the prior rounds.",
    ]
    if ctx.document_review_context:
        lines.insert(
            0,
            "Native document-review assignment context:\n"
            + ctx.document_review_context,
        )
    lines.append("")
    if ctx.prior_review_artifacts:
        lines.append("Prior review artifacts (captured before execution):")
        for artifact in ctx.prior_review_artifacts:
            lines.extend(
                (
                    f'<prior-review-artifact path="review-event/{artifact.run_id}" '
                    f'run_id="{artifact.run_id}">',
                    artifact.content,
                    "</prior-review-artifact>",
                )
            )
    else:
        lines.append("Prior review artifacts: none in the captured input.")
    lines.extend(_decision_sections(ctx))
    lines.append("===== END DISPOSITION RERUN LEDGER =====")
    return "\n".join(lines)


def _decision_ground(ctx: GateContext) -> str:
    """The decisions half of the ledger, standing alone: injected on a
    capped gate's FIRST lane run when the review pipeline already carries
    recorded decisions. Without it, a first run over previously
    adjudicated reviewer findings would re-record settled items
    as fresh REPORT decisions — the re-litigation the ledger exists to
    block."""
    sections = _decision_sections(ctx)
    if not sections:
        return ""
    lines = [
        "Adjudicated Decision Ground (operator-generated; authoritative)",
        "===== BEGIN ADJUDICATED DECISION GROUND =====",
        "This review pipeline already carries adjudicated ground — "
        "recorded decisions and/or the Feature Spec's Decision Log (per "
        "prompt-authoring-standards.md#disposition-rerun):",
        "- Do NOT re-raise, reword, or derive variants of the adjudicated "
        "decisions below; a resolved decision is settled ground, and the "
        "absence of enforcement for a documented accepted-risk "
        "assumption is not a finding.",
        "- Do NOT emit findings for pending decisions below; they are "
        "already awaiting a ruling.",
    ]
    lines.extend(sections)
    lines.append("===== END ADJUDICATED DECISION GROUND =====")
    return "\n".join(lines)


def _spec_decision_log_section(ctx: GateContext) -> list[str]:
    """Surface the Feature Spec's own `## Decision Log` as adjudicated ground.

    The section is a verbatim heading-to-next-H2
    slice of `ctx.spec_content`, no entry parsing. The reviewer keeps the
    single structural-forbid instruction and never self-classifies against
    the ledger. An absent or empty section injects nothing."""
    section = extract_markdown_section(ctx.spec_content, "## Decision Log")
    body = section.removeprefix("## Decision Log").strip()
    if not body:
        return []
    return [
        "",
        "Ratified decisions from the Feature Spec (settled ground — "
        "do not re-raise; verbatim from the spec):",
        "",
        section,
    ]


def _decision_sections(ctx: GateContext) -> list[str]:
    lines: list[str] = []
    adjudicated = [
        decision
        for decision in ctx.prior_decisions
        if decision.get("status") == "resolved"
    ]
    pending = [
        decision
        for decision in ctx.prior_decisions
        if decision.get("status") == "pending"
    ]
    if adjudicated:
        lines.append("")
        lines.append("Adjudicated decisions (settled — do not re-litigate):")
        lines.extend(
            f"- {decision['id']}: {decision['title']} — RESOLVED: "
            f"{decision.get('resolution', '')}"
            + (
                f" [resolution source: {decision['resolution_source']}]"
                if "resolution_source" in decision
                else ""
            )
            for decision in adjudicated
        )
    if pending:
        lines.append("")
        lines.append("Pending decisions (do not duplicate):")
        lines.extend(f"- {decision['id']}: {decision['title']}" for decision in pending)
    lines.extend(_spec_decision_log_section(ctx))
    return lines


def build_codex_local_review_instructions(ctx: GateContext, gate_name: str) -> str:
    lines = [
        "Codex local-workspace instruction:",
        (
            "The authoritative review inputs are present on disk in the local "
            "repository and must be read from the workspace before producing the "
            "review."
        ),
        (
            "Do not claim the document is unavailable unless a direct local file read "
            "actually fails."
        ),
        "Use local file-reading tools as needed; do not browse the web for this gate.",
        (
            "Do not spawn agents or delegate. Read the files directly in the current "
            "Codex process."
        ),
        (
            "Prefer direct file reads plus `sed`/`rg`; do not assume a `python` shell "
            "alias exists in this environment."
        ),
    ]
    if gate_name == "spec-review":
        if ctx.spec_path:
            lines.append(f"Read this Feature Spec from disk: {ctx.spec_path}")
        return "\n".join(lines)
    if ctx.plan_path:
        lines.append(f"Read this Implementation Plan from disk: {ctx.plan_path}")
    if ctx.spec_path:
        if gate_name == "milestone-review":
            lines.append(
                "Read the current milestone's satisfied Acceptance Criteria and "
                "their explicitly referenced defining commitments from this "
                f"Feature Spec: {ctx.spec_path}"
            )
        elif gate_name == "review-test-scaffolding":
            lines.append(
                "Read the Acceptance Criteria and only their explicitly referenced "
                "defining commitments from this Feature Spec: "
                f"{ctx.spec_path}"
            )
        else:
            lines.append(f"Read this Feature Spec from disk: {ctx.spec_path}")
    return "\n".join(lines)


def build_codex_milestone_context(
    ctx: GateContext,
    diff: DiffResult,
    gate_name: str,
    *,
    diff_text: str,
) -> str:
    sections: list[str] = [
        "AUTHORITATIVE INPUT RESOLUTION OVERRIDE FOR CODEX:",
        (
            "- Treat the embedded blocks below as the resolved source of truth for "
            "this gate's required inputs."
        ),
        (
            "- When the prompt says to locate, read, or obtain the plan/spec sections "
            "or diff, use the embedded blocks below."
        ),
        (
            "- Do not rely on shell, filesystem, or git commands inside the sandbox "
            "when the needed content is embedded here."
        ),
        (
            "- Use the Gate Runtime Manifest to diagnose missing inputs, zero-overlap "
            "diffs, or ownership mismatches before doing any extra workspace "
            "exploration."
        ),
        (
            "- Only report missing inputs if the corresponding embedded block is "
            "absent from this prompt."
        ),
    ]

    yaml_header = extract_yaml_header(ctx.plan_content)
    if yaml_header and ctx.plan_path:
        sections.extend(
            [
                "",
                f"===== BEGIN IMPLEMENTATION PLAN YAML HEADER: {ctx.plan_path} =====",
                yaml_header,
                "===== END IMPLEMENTATION PLAN YAML HEADER =====",
            ]
        )

    if ctx.milestone_section and ctx.plan_path:
        sections.extend(
            [
                "",
                "Resolved input: Current Milestone section",
                f"===== BEGIN CURRENT MILESTONE SECTION: {ctx.plan_path} =====",
                ctx.milestone_section,
                "===== END CURRENT MILESTONE SECTION =====",
            ]
        )

    if gate_name == "milestone-review" and ctx.spec_path:
        if ctx.satisfies_ac_ids:
            acceptance = extract_acceptance_criteria_blocks(
                ctx.spec_content,
                ctx.satisfies_ac_ids,
            )
        else:
            acceptance = extract_markdown_h2_section(
                ctx.spec_content, "Acceptance Criteria"
            )
        if acceptance:
            sections.extend(
                [
                    "",
                    "Resolved input: Feature Spec Acceptance Criteria section",
                    (
                        "===== BEGIN FEATURE SPEC ACCEPTANCE CRITERIA: "
                        f"{ctx.spec_path} ====="
                    ),
                    acceptance,
                    "===== END FEATURE SPEC ACCEPTANCE CRITERIA =====",
                ]
            )

    if diff_text:
        sections.extend(
            [
                "",
                "Resolved input: Authoritative diff",
                "===== BEGIN AUTHORITATIVE DIFF =====",
                diff_text,
                "===== END AUTHORITATIVE DIFF =====",
            ]
        )

    return "\n".join(sections)


def build_test_scaffolding_context(
    ctx: GateContext,
    diff: DiffResult,
    *,
    relevant_paths: tuple[str, ...] | None = None,
) -> str:
    tests_root = resolve_tests_root(ctx.repo_root)
    sections = [
        "Resolved authoritative inputs for review-test-scaffolding:",
        "- Use the embedded sections below before doing any additional discovery.",
        (
            "- The plan sections and canonical AC slice are resolved here. Follow "
            "explicit AC references to their defining commitments in the resolved "
            "Feature Spec; do not assess unrelated spec content."
        ),
        (
            "- Focus tool reads on the relevant test files after consuming this "
            "embedded context."
        ),
        f"- This repository keeps its test suite under {tests_root}/.",
        (
            "- Treat the RELEVANT TEST FILES block as the authoritative starting set. "
            "Only broaden discovery if those paths are missing or AC coverage tracing "
            "proves a dependency is absent."
        ),
        (
            "- Do not open unrelated smoke-test target files unless a specific finding "
            "requires evidence beyond the smoke_test command itself."
        ),
        (
            "- Do not read generic repo helpers or unrelated tests unless they are "
            "imported by the listed scaffold files."
        ),
    ]
    if ctx.verification_commands:
        sections.extend(
            [
                "",
                "===== BEGIN VERIFICATION COMMAND FACTS =====",
                json.dumps(
                    ctx.verification_commands,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "===== END VERIFICATION COMMAND FACTS =====",
            ]
        )

    yaml_header = extract_yaml_header(ctx.plan_content)
    if yaml_header and ctx.plan_path:
        sections.extend(
            [
                "",
                f"===== BEGIN IMPLEMENTATION PLAN YAML HEADER: {ctx.plan_path} =====",
                yaml_header,
                "===== END IMPLEMENTATION PLAN YAML HEADER =====",
            ]
        )

    for heading_variants, title in (
        (("## Technical Architecture",), "Technical Architecture"),
        (("### Verification Commands",), "Verification Commands"),
        (("### Live E2E Test Prerequisites",), "Live E2E Test Prerequisites"),
        (("## AC Coverage Matrix", "### AC Coverage Matrix"), "AC Coverage Matrix"),
    ):
        content = ""
        for heading in heading_variants:
            content = extract_markdown_section(ctx.plan_content, heading)
            if content:
                break
        if content:
            sections.extend(
                [
                    "",
                    f"===== BEGIN PLAN SECTION: {title} =====",
                    content,
                    f"===== END PLAN SECTION: {title} =====",
                ]
            )

    acceptance = extract_markdown_section(ctx.spec_content, "## Acceptance Criteria")
    if acceptance and ctx.spec_path:
        sections.extend(
            [
                "",
                "===== BEGIN FEATURE SPEC SECTION: Acceptance Criteria =====",
                acceptance,
                "===== END FEATURE SPEC SECTION: Acceptance Criteria =====",
            ]
        )

    sections.extend(["", "===== BEGIN RELEVANT TEST FILES ====="])
    if relevant_paths is None:
        from heddle.gate.preparation import declared_scaffold_paths

        relevant_paths = declared_scaffold_paths(ctx, diff)
    if relevant_paths:
        sections.extend(relevant_paths)
    else:
        sections.append(
            "(none resolved from the plan or diff; discover test files via the"
            " Verification Commands and AC Coverage Matrix above)"
        )
    sections.append("===== END RELEVANT TEST FILES =====")
    return "\n".join(sections)


def extract_yaml_header(content: str) -> str:
    if not content.startswith("---\n"):
        return ""
    try:
        _, front_matter, _ = content.split("---", 2)
    except ValueError:
        return ""
    return "---\n" + front_matter.strip() + "\n---"
