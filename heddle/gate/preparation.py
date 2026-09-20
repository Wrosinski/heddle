"""Canonical gate-input preparation and semantic identity.

This module owns the pure, domain-separated manifest encoding. File discovery
is deliberately declaration-driven: feature documents, registry context
builders, explicitly supplied reviewed inputs, and milestone ownership are the
only source inputs. Repository-wide walks are never used as implicit review
scope.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath

from heddle.gate.extraction import (
    extract_acceptance_criteria_blocks,
    extract_command_test_paths,
    extract_explicit_test_paths,
    unsupported_command_test_tokens,
    unsupported_explicit_test_tokens,
)
from heddle.gate.input_navigation import CODEX_INLINE_LIMIT, NAVIGATION_POLICY
from heddle.gate.overlap import compute_overlap
from heddle.gate.preflight import preflight
from heddle.gate.prompt import (
    build_codex_local_review_instructions,
    build_codex_milestone_context,
    build_rerun_ledger,
    build_runtime_manifest,
    build_test_scaffolding_context,
    render_template,
    resolve_tests_root,
)
from heddle.gate.results import ReviewOutputContract, canonical_json
from heddle.gate.types import (
    ContextProjection,
    DiffResult,
    GateContext,
    GateExecutionConfig,
    GateInputContract,
    GateType,
    OverlapResult,
    PreflightResult,
    PreparedDiff,
    PreparedGateRun,
    PromptTransport,
    ResolvedGateInvocation,
    ResolvedGatePrompt,
    ReviewBasis,
    ReviewDecision,
    ReviewedInput,
)
from heddle.io.git import observe_dirty_paths
from heddle.io.source import (
    capture_source_path,
    observe_source,
    resolve_source_definition,
)
from heddle.kernel.managed_regions import PLAN_STATUS_ID, without_managed_region
from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import (
    ObservedPath,
    SourceDefinition,
    build_source_evidence,
    safe_relative_parts,
)
from heddle.kernel.test_bindings import (
    PrimaryTestBinding,
    PythonSymbolInspection,
    TestBindingIssue,
    inspect_python_test_source,
    parse_primary_test_bindings,
    resolve_primary_test_binding,
)

_BASIS_DOMAIN = b"heddle.gate-review-basis/v4"
_INPUT_DOMAIN = b"heddle.gate-input/v4"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DIFF_CONSUMING_CONTEXT_BUILDERS = frozenset({"milestone", "test-scaffolding"})


def encode_gate_manifest(records: tuple[tuple[str, bytes], ...]) -> bytes:
    """Encode ordered named byte records using the ratified v1 framing."""
    encoded = bytearray()
    for name, value in records:
        if not isinstance(name, str) or not isinstance(value, bytes):
            raise TypeError("gate manifest records must be (str, bytes) pairs")
        name_bytes = name.encode("utf-8")
        encoded += len(name_bytes).to_bytes(8, "big") + name_bytes
        encoded += len(value).to_bytes(8, "big") + value
    return bytes(encoded)


def input_contract_for(gate_type: GateType) -> GateInputContract:
    """Derive the one immutable input-contract projection from GateType."""
    return GateInputContract(
        requires_spec=gate_type.requires_spec,
        requires_plan=gate_type.requires_plan,
        requires_milestone=gate_type.requires_milestone,
        requires_diff=gate_type.requires_diff,
        context_builders=gate_type.context_builders,
        source_selector=gate_type.source_selector,
    )


def gate_requires_diff_resolution(gate_type: GateType) -> bool:
    """Whether preparation consumes diff data, directly or through context."""
    return gate_type.requires_diff or bool(
        _DIFF_CONSUMING_CONTEXT_BUILDERS.intersection(gate_type.context_builders)
    )


def gate_scope(gate_type: GateType, context: GateContext) -> str:
    """Return the canonical scope for a gate and its resolved context."""
    return (
        str(context.milestone)
        if gate_type.requires_milestone and context.milestone is not None
        else "feature"
    )


def prepared_validation_error(
    gate_type: GateType,
    context: GateContext,
    prepared: PreparedGateRun,
) -> KernelError | None:
    """Validate one prepared handoff before any runner uses its contents."""
    expected_scope = gate_scope(gate_type, context)
    if (
        prepared.feature != context.feature
        or prepared.gate != gate_type.name
        or prepared.scope != expected_scope
    ):
        return KernelError(
            code="workspace-invalid",
            message=(
                "prepared gate identity does not match requested "
                f"{gate_type.name}/{expected_scope}"
            ),
            hint="prepare the selected gate again",
        )
    prepared_cli = prepared.invocation.exec_config.cli
    if prepared_cli not in gate_type.supported_clis:
        return KernelError(
            code="workspace-invalid",
            message=(
                f"prepared CLI {prepared_cli} is not supported by gate {gate_type.name}"
            ),
            hint="prepare the selected gate again",
        )
    return None


@dataclass(frozen=True)
class _CapturedReviewInputs:
    basis: ReviewBasis
    scope: str
    output_version: str | None
    output_digest: str | None
    contract: GateInputContract
    prompt: ResolvedGatePrompt
    diff: PreparedDiff
    diff_text: str
    overlap: OverlapResult
    preflight: PreflightResult
    reviewed: tuple[ReviewedInput, ...]
    projections: tuple[ContextProjection, ...]
    rule_context: str
    diagnostics: tuple[str, ...]
    binding_issues: tuple[TestBindingIssue, ...]


def _capture_review_inputs(
    context: GateContext,
    *,
    gate_type: GateType,
    invocation: ResolvedGateInvocation,
    prompt: ResolvedGatePrompt,
    diff: DiffResult,
) -> _CapturedReviewInputs:
    """Capture stable sources once for both full and producer-basis preparation."""
    contract = input_contract_for(gate_type)
    exec_config = invocation.exec_config
    output_version, output_digest = _output_contract_identity(gate_type)
    diff_bytes = diff.diff_path.read_bytes()
    diff_text = diff_bytes.decode("utf-8")
    workflow_paths = _workflow_control_paths(context)
    diff = replace(
        diff,
        untracked_files=tuple(
            path
            for path in diff.untracked_files
            if not any(_paths_overlap(path, control) for control in workflow_paths)
        ),
    )
    scope = gate_scope(gate_type, context)
    overlap = (
        compute_overlap(diff.changed_files, context.owned_paths)
        if gate_requires_diff_resolution(gate_type)
        else OverlapResult(status="unassessed", files=())
    )
    preflight_result = preflight(
        gate_type=gate_type,
        plan_path=str(context.plan_path) if context.plan_path else None,
        milestone=context.milestone,
        milestone_section=context.milestone_section,
        satisfies_ac_ids=context.satisfies_ac_ids,
        diff_non_empty=bool(diff_bytes),
        changed_files=diff.changed_files,
        owned_paths=context.owned_paths,
        overlap_files=overlap.files,
        untracked_files=diff.untracked_files,
        allowed_non_owned_paths=workflow_paths,
    )
    allow_missing = preflight_result.fatal_reason is not None
    owned, source_paths = _owned_content(context, contract, allow_empty=allow_missing)
    projections, scaffold_paths, binding_issues = _context_projections(
        context,
        contract,
        diff,
        exec_config,
        diff_text=diff_text,
    )
    reviewed = _declared_reviewed_inputs(
        context,
        contract,
        projections,
        allow_missing_required=allow_missing,
    )
    document_paths = tuple(
        _logical_document_path(context, path, label)
        for required, path, label in (
            (contract.requires_spec, context.spec_path, "spec"),
            (contract.requires_plan, context.plan_path, "plan"),
        )
        if required and path is not None
    )
    source_advice = _review_source_advice(
        context.repo_root,
        tuple({*document_paths, *source_paths, *scaffold_paths}),
    )
    basis_records = _review_basis_records(
        context,
        contract,
        gate_type,
        scope,
        exec_config,
        prompt,
        reviewed,
        owned,
        allow_missing=allow_missing,
    )
    basis = ReviewBasis(
        gate=gate_type.name,
        review_basis_hash=hashlib.sha256(
            encode_gate_manifest(tuple(basis_records))
        ).hexdigest(),
        ac_ids=tuple(
            context.satisfies_ac_ids
            if gate_type.requires_milestone
            else context.spec_ac_ids
        ),
        active_rules=None,
        output_contract_version=output_version,
        output_contract_sha256=output_digest,
    )
    return _CapturedReviewInputs(
        basis=basis,
        scope=scope,
        output_version=output_version,
        output_digest=output_digest,
        contract=contract,
        prompt=prompt,
        diff=PreparedDiff(
            source=diff.source,
            source_detail=diff.source_detail,
            changed_files=diff.changed_files,
            untracked_files=diff.untracked_files,
            warnings=diff.warnings,
        ),
        diff_text=diff_text,
        overlap=overlap,
        preflight=preflight_result,
        reviewed=reviewed,
        projections=projections,
        rule_context="",
        diagnostics=(*context.preparation_diagnostics, *source_advice),
        binding_issues=binding_issues,
    )


def prepare_review_basis(
    context: GateContext,
    *,
    gate_type: GateType,
    invocation: ResolvedGateInvocation,
    prompt: ResolvedGatePrompt,
    diff: DiffResult,
) -> ReviewBasis:
    """Compute stable role validation inputs without rendering rerun history."""
    return _capture_review_inputs(
        context, gate_type=gate_type, invocation=invocation, prompt=prompt, diff=diff
    ).basis


def prepare_gate_run(
    context: GateContext,
    *,
    gate_type: GateType,
    invocation: ResolvedGateInvocation,
    prompt: ResolvedGatePrompt,
    diff: DiffResult,
) -> PreparedGateRun:
    """Render exact current/replay transports from one captured source set."""
    captured = _capture_review_inputs(
        context, gate_type=gate_type, invocation=invocation, prompt=prompt, diff=diff
    )
    if gate_type.name == "review-test-scaffolding" and captured.binding_issues:
        details = "; ".join(
            f"{issue.ac_id}: {issue.target}: {issue.reason}"
            for issue in captured.binding_issues
        )
        raise _input_error(
            f"review-test-scaffolding primary bindings are invalid: {details}",
            (
                "add or repair each Verified-by target using a module test function "
                "or a Test class-qualified method"
            ),
        )
    current = _prepare_variant(context, captured, gate_type, invocation)
    if context.review_assignment is not None:
        return current
    if (
        context.prior_review_artifacts
        and context.latest_lane_run_id == context.prior_review_artifacts[-1].run_id
    ):
        latest = context.prior_review_artifacts[-1]
        replay_context = replace(
            context,
            prior_review_artifacts=context.prior_review_artifacts[:-1],
            prior_completed_runs=context.prior_completed_runs - 1,
            prior_decisions=[
                decision
                for decision in context.prior_decisions
                if not (
                    decision.get("status") == "pending"
                    and decision.get("origin_run_id") == latest.run_id
                )
            ],
        )
        replay = _prepare_variant(replay_context, captured, gate_type, invocation)
        if replay.input_hash == latest.input_hash:
            return replay
    return current


def _prepare_variant(
    context: GateContext,
    captured: _CapturedReviewInputs,
    gate_type: GateType,
    invocation: ResolvedGateInvocation,
) -> PreparedGateRun:
    """Pure rendering and identity over captured sources and disposition data."""
    runtime_sections = _runtime_sections(
        context, captured, gate_type, invocation.exec_config
    )
    stdin = "\n\n".join(
        section
        for section in (captured.prompt.transport.stdin, *runtime_sections)
        if section
    ).strip()
    if invocation.exec_config.cli == "codex" and len(stdin) > CODEX_INLINE_LIMIT:
        # This changes review behavior, unlike the transport-only wrapper.
        # Test the original assembled length once: the inline boundary stays put.
        stdin = NAVIGATION_POLICY + "\n\n" + stdin
    transport = PromptTransport(system=captured.prompt.transport.system, stdin=stdin)
    effective_digest = hashlib.sha256(
        canonical_json({"system": transport.system, "stdin": transport.stdin})
    ).hexdigest()
    input_hash = hashlib.sha256(
        encode_gate_manifest(
            tuple(
                _input_records(
                    context, captured.basis.review_basis_hash, effective_digest
                )
            )
        )
    ).hexdigest()
    return PreparedGateRun(
        assignment_id=context.review_assignment.id
        if context.review_assignment
        else None,
        round_number=context.assignment_round.number
        if context.assignment_round
        else None,
        reviewer_slot=context.reviewer_slot,
        required_prior_references=context.required_prior_references,
        feature=context.feature,
        gate=gate_type.name,
        scope=captured.scope,
        invocation=invocation,
        prompt=captured.prompt,
        prompt_version=captured.prompt.prompt_version,
        effective_prompt_sha256=effective_digest,
        transport=transport,
        runtime_sections=runtime_sections,
        reviewed_inputs=captured.reviewed,
        review_basis_hash=captured.basis.review_basis_hash,
        input_hash=input_hash,
        diff=captured.diff,
        diff_text=captured.diff_text,
        overlap=captured.overlap,
        preflight=captured.preflight,
        diagnostics=captured.diagnostics,
        output_contract=gate_type.output_contract,
        output_contract_version=captured.output_version,
        output_contract_sha256=captured.output_digest,
        ac_ids=captured.basis.ac_ids,
        prior_reviews=context.prior_review_artifacts,
        active_rules=captured.basis.active_rules,
        review_decisions=tuple(
            ReviewDecision(
                item["id"],
                item["status"],
                item["origin_run_id"],
                item["origin_finding_id"],
            )
            for item in context.prior_decisions
        ),
    )


def _runtime_sections(
    context: GateContext,
    captured: _CapturedReviewInputs,
    gate_type: GateType,
    exec_config: GateExecutionConfig,
) -> tuple[str, ...]:
    runtime_sections: list[str] = []
    if captured.rule_context:
        runtime_sections.append(captured.rule_context)
    ledger = build_rerun_ledger(context)
    if ledger:
        runtime_sections.append(_stable_text(context, ledger))
    for builder, projection in zip(
        captured.contract.context_builders, captured.projections, strict=True
    ):
        if builder == "test-scaffolding" and projection.text:
            runtime_sections.append(projection.text)
    if exec_config.cli == "codex" and gate_type.name in {
        "spec-review",
        "plan-review",
        "milestone-review",
        "review-test-scaffolding",
    }:
        runtime_sections.append(
            _stable_text(
                context,
                build_codex_local_review_instructions(context, gate_type.name),
            )
        )
    runtime_sections.append(
        _stable_text(
            context,
            build_runtime_manifest(
                context,
                captured.diff,
                captured.overlap,
                captured.preflight,
            ),
        )
    )
    if exec_config.cli == "codex" and "milestone" in captured.contract.context_builders:
        milestone_projection = captured.projections[
            captured.contract.context_builders.index("milestone")
        ]
        if milestone_projection.text:
            runtime_sections.append(milestone_projection.text)
    return tuple(runtime_sections)


def _review_basis_records(
    context: GateContext,
    contract: GateInputContract,
    gate_type: GateType,
    scope: str,
    exec_config: GateExecutionConfig,
    prompt: ResolvedGatePrompt,
    reviewed: tuple[ReviewedInput, ...],
    owned: tuple[tuple[str, str, str], ...],
    *,
    allow_missing: bool = False,
) -> list[tuple[str, bytes]]:
    records: list[tuple[str, bytes]] = [
        ("domain", _BASIS_DOMAIN),
        ("feature", context.feature.encode("utf-8")),
        ("gate", gate_type.name.encode("utf-8")),
        ("scope", scope.encode("utf-8")),
        ("exec.cli", exec_config.cli.encode("utf-8")),
        ("exec.model", exec_config.model.encode("utf-8")),
        ("exec.reasoning_effort", exec_config.reasoning_effort.encode("utf-8")),
        ("exec.sandbox", exec_config.sandbox.encode("utf-8")),
        ("input-contract", canonical_json(asdict(contract))),
    ]
    version, digest = _output_contract_identity(gate_type)
    records.extend(
        (
            ("review.output_contract_version", (version or "").encode("utf-8")),
            ("review.output_contract_sha256", (digest or "").encode("ascii")),
            (
                "prompt.effective_instructions",
                _canonical_logical_instructions(
                    context, prompt, exec_config.cli
                ).encode("utf-8"),
            ),
        )
    )
    records.extend(
        _required_fact_records(context, contract, allow_missing=allow_missing)
    )
    for index, item in enumerate(reviewed):
        prefix = f"reviewed/{index:08d}"
        records.extend(
            (
                (f"{prefix}/name", item.name.encode("utf-8")),
                (f"{prefix}/kind", item.kind.encode("utf-8")),
                (f"{prefix}/identity", item.identity.encode("ascii")),
            )
        )
    for index, (path, kind, identity) in enumerate(owned):
        prefix = f"owned/{index:08d}"
        records.extend(
            (
                (f"{prefix}/path", path.encode("utf-8")),
                (f"{prefix}/kind", kind.encode("ascii")),
                (f"{prefix}/identity", identity.encode("ascii")),
            )
        )
    return records


def _input_records(
    context: GateContext,
    review_basis_hash: str,
    effective_prompt_sha256: str,
) -> list[tuple[str, bytes]]:
    records: list[tuple[str, bytes]] = [
        ("domain", _INPUT_DOMAIN),
        ("review_basis_hash", review_basis_hash.encode("ascii")),
        ("effective_prompt_sha256", effective_prompt_sha256.encode("ascii")),
        ("prior_completed_runs", str(context.prior_completed_runs).encode("ascii")),
        ("decisions", canonical_json(context.prior_decisions)),
    ]
    if context.review_assignment is not None:
        records.append(("reviewer_slot", (context.reviewer_slot or "").encode()))
    if context.document_review_context:
        records.append(
            ("document_review_context", context.document_review_context.encode("utf-8"))
        )
    for index, artifact in enumerate(context.prior_review_artifacts):
        prefix = f"prior_artifact/{index:08d}"
        records.extend(
            (
                (f"{prefix}/run_id", artifact.run_id.encode("utf-8")),
                (
                    f"{prefix}/identity",
                    hashlib.sha256(artifact.content.encode("utf-8"))
                    .hexdigest()
                    .encode("ascii"),
                ),
            )
        )
    return records


def _declared_reviewed_inputs(
    context: GateContext,
    contract: GateInputContract,
    projections: tuple[ContextProjection, ...],
    *,
    allow_missing_required: bool = False,
) -> tuple[ReviewedInput, ...]:
    items: list[ReviewedInput] = []
    if contract.requires_spec:
        if allow_missing_required and (
            context.spec_path is None or not context.spec_content
        ):
            pass
        else:
            _require_document(
                context.spec_path,
                context.spec_content,
                label="spec",
            )
            items.append(_reviewed("reviewed/spec", "content", context.spec_content))
            items.append(_document_identity(context, context.spec_path, "spec"))
    if contract.requires_plan:
        if allow_missing_required and (
            context.plan_path is None or not context.plan_content
        ):
            pass
        else:
            _require_document(
                context.plan_path,
                context.plan_content,
                label="plan",
            )
            items.append(
                _reviewed(
                    "reviewed/plan", "content", _authored_plan(context.plan_content)
                )
            )
            items.append(_document_identity(context, context.plan_path, "plan"))

    for item in context.reviewed_inputs:
        content = _content_bytes(item.content)
        actual = hashlib.sha256(content).hexdigest()
        if item.identity != actual or not _HEX64.fullmatch(item.identity):
            raise _input_error(
                f"reviewed input {item.name!r} has an invalid content identity",
                "recompute the reviewed input identity from its exact raw bytes",
            )
        items.append(item)

    for projection in projections:
        items.extend(projection.reviewed_inputs)

    by_name: dict[str, ReviewedInput] = {}
    for item in items:
        logical_name = _normalize_path(item.name)
        if logical_name in by_name:
            raise _input_error(
                f"duplicate reviewed input component {logical_name}",
                "declare each reviewed input logical name exactly once",
            )
        by_name[logical_name] = replace(item, name=logical_name)
    return tuple(sorted(by_name.values(), key=lambda item: item.name.encode("utf-8")))


def _context_projections(
    context: GateContext,
    contract: GateInputContract,
    prepared_diff: DiffResult,
    exec_config: GateExecutionConfig,
    *,
    diff_text: str,
) -> tuple[
    tuple[ContextProjection, ...], tuple[str, ...], tuple[TestBindingIssue, ...]
]:
    projections: list[ContextProjection] = []
    selected_paths: set[str] = set()
    binding_issues: list[TestBindingIssue] = []
    bindings: tuple[PrimaryTestBinding, ...] = ()
    for builder in contract.context_builders:
        if builder == "test-scaffolding":
            declared_paths = declared_scaffold_paths(context, prepared_diff)
            mandatory_paths = set(declared_paths)
            bindings, parse_issues = _required_scaffold_bindings(context)
            binding_issues.extend(parse_issues)
            valid_bindings: list[PrimaryTestBinding] = []
            binding_paths: set[str] = set()
            for binding in bindings:
                try:
                    safe_relative_parts(binding.path)
                except KernelError as error:
                    binding_issues.append(
                        TestBindingIssue(binding.ac_id, binding.target, error.message)
                    )
                    continue
                valid_bindings.append(binding)
                binding_paths.add(binding.path)
            selected = tuple(
                sorted(
                    mandatory_paths | binding_paths,
                    key=lambda value: value.encode("utf-8"),
                )
            )
            selected_paths.update(selected)
            text = build_test_scaffolding_context(
                context, prepared_diff, relevant_paths=selected
            )
            reviewed_inputs = []
            captures: dict[str, ObservedPath] = {}
            inspections: dict[str, PythonSymbolInspection] = {}
            for relative in selected:
                captured = capture_source_path(
                    context.repo_root, relative, context.source_observations
                )
                if captured.kind != "file":
                    if relative in mandatory_paths:
                        raise _input_error(
                            f"required reviewed test artifact {relative} "
                            "is missing or nonregular",
                            f"restore or create {relative} before running this gate",
                        )
                    captures[relative] = captured
                    continue
                captures[relative] = captured
                if relative in binding_paths:
                    inspections[relative] = inspect_python_test_source(captured.content)
                content = captured.content
                reviewed_inputs.append(_captured_identity(captured, "test/" + relative))
                reviewed_inputs.append(
                    _reviewed(
                        f"reviewed/test/{relative}",
                        "content",
                        content,
                    )
                )
            for binding in valid_bindings:
                captured = captures[binding.path]
                if captured.kind != "file":
                    binding_issues.append(
                        TestBindingIssue(
                            binding.ac_id,
                            binding.target,
                            f"referenced path is {captured.kind}",
                        )
                    )
                    continue
                resolution = resolve_primary_test_binding(
                    binding,
                    inspections[binding.path],
                    policy="native",
                )
                if resolution.issue is not None:
                    binding_issues.append(resolution.issue)
        elif builder == "milestone":
            text = (
                build_codex_milestone_context(
                    context,
                    prepared_diff,
                    context.gate_type.name,
                    diff_text=diff_text,
                )
                if exec_config.cli == "codex"
                else ""
            )
            reviewed_inputs = []
        else:
            raise _input_error(
                f"unknown gate context builder {builder!r}",
                "register a typed context projection before using this builder",
            )
        projections.append(
            ContextProjection(
                text=_stable_text(context, text),
                reviewed_inputs=tuple(reviewed_inputs),
            )
        )
    return (
        tuple(projections),
        tuple(sorted(selected_paths, key=lambda value: value.encode())),
        _ordered_binding_issues(context, bindings, binding_issues),
    )


def _required_scaffold_bindings(
    context: GateContext,
) -> tuple[tuple[PrimaryTestBinding, ...], tuple[TestBindingIssue, ...]]:
    bindings: list[PrimaryTestBinding] = []
    issues: list[TestBindingIssue] = []
    for ac_id in context.spec_ac_ids:
        block = extract_acceptance_criteria_blocks(context.spec_content, [ac_id])
        parsed = parse_primary_test_bindings(ac_id, block)
        bindings.extend(parsed.bindings)
        issues.extend(parsed.issues)
    return tuple(bindings), tuple(issues)


def _ordered_binding_issues(
    context: GateContext,
    bindings: tuple[PrimaryTestBinding, ...],
    issues: list[TestBindingIssue],
) -> tuple[TestBindingIssue, ...]:
    ac_order = {ac_id: index for index, ac_id in enumerate(context.spec_ac_ids)}
    target_order: dict[tuple[str, str], int] = {}
    for ordinal, binding in enumerate(bindings):
        target_order.setdefault((binding.ac_id, binding.target), ordinal)
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                ac_order.get(issue.ac_id, len(ac_order)),
                target_order.get((issue.ac_id, issue.target), -1),
            ),
        )
    )


def _required_fact_records(
    context: GateContext,
    contract: GateInputContract,
    *,
    allow_missing: bool = False,
) -> list[tuple[str, bytes]]:
    plan_facts = context.operational_plan_facts
    if contract.requires_milestone:
        plan_facts = tuple(
            item for item in plan_facts if item["id"] == context.milestone
        )
    elif context.gate_type.name not in {"plan-review", "review-test-scaffolding"} and (
        contract.source_selector == "none"
    ):
        plan_facts = ()
    include_tasks = context.gate_type.name in {"plan-review", "review-test-scaffolding"}
    facts = tuple(
        {key: value for key, value in item.items() if key != "tasks" or include_tasks}
        for item in plan_facts
    )
    records = [("fact/operational-plan", canonical_json(facts))]
    if contract.requires_spec:
        spec_path = (
            ""
            if allow_missing and context.spec_path is None
            else _logical_document_path(context, context.spec_path, "spec")
        )
        records.append(
            (
                "fact/requires_spec",
                spec_path.encode("utf-8"),
            )
        )
    if contract.requires_plan:
        plan_path = (
            ""
            if allow_missing and context.plan_path is None
            else _logical_document_path(context, context.plan_path, "plan")
        )
        records.append(
            (
                "fact/requires_plan",
                plan_path.encode("utf-8"),
            )
        )
    if contract.requires_milestone:
        if not context.milestone and not allow_missing:
            raise _input_error(
                f"{context.gate_type.name} requires a current milestone",
                "create or select the current milestone before running this gate",
            )
        records.append(
            (
                "fact/requires_milestone",
                str(context.milestone or "").encode("utf-8"),
            )
        )
    return records


def _owned_content(
    context: GateContext,
    contract: GateInputContract,
    *,
    allow_empty: bool = False,
) -> tuple[tuple[tuple[str, str, str], ...], tuple[str, ...]]:
    if contract.source_selector == "none":
        return (), ()
    declarations = (
        tuple(context.owned_paths)
        if contract.source_selector == "milestone-owns"
        else context.feature_owned_paths
    )
    if not declarations:
        if allow_empty:
            return (), ()
        raise _input_error(
            f"{context.gate_type.name} has no declared implementation source",
            "declare the applicable milestone ownership before review",
        )
    definition = resolve_source_definition(
        context.repo_root, contract.source_selector, declarations
    )
    observed = observe_source(
        context.repo_root, definition, observations=context.source_observations
    )
    return (
        (("declared-source", definition.kind, observed.source_sha256),),
        tuple(
            sorted(
                {*definition.declaration_paths, *definition.paths},
                key=lambda value: value.encode(),
            )
        ),
    )


def _review_source_advice(repo_root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    observation = observe_dirty_paths(repo_root, paths)
    if observation.warning is not None:
        return (
            "Git status for selected review inputs is unknown; review will continue "
            "without claiming those inputs are clean: " + observation.warning,
        )
    if not observation.dirty_paths:
        return ()
    rendered = ", ".join(f"`{path}`" for path in observation.dirty_paths)
    return (
        "Selected review inputs have uncommitted Git changes: "
        f"{rendered}. Format these exact paths and commit them before paid review "
        "when appropriate; captured input identity remains authoritative.",
    )


def _authored_plan(content: str) -> str:
    """Exclude only the exact existing native managed status region."""
    return without_managed_region(content, PLAN_STATUS_ID)


def _document_identity(
    context: GateContext, path: Path | None, label: str
) -> ReviewedInput:
    relative = _logical_document_path(context, path, label)
    captured = capture_source_path(
        context.repo_root, relative, context.source_observations
    )
    if label == "plan":
        captured = replace(
            captured, content=_authored_plan(captured.content.decode()).encode()
        )
    return _captured_identity(captured, label)


def _captured_identity(captured: ObservedPath, label: str) -> ReviewedInput:
    definition = SourceDefinition("feature-inputs", (captured.path,), (captured.path,))
    evidence = build_source_evidence(definition, (captured,))
    return _reviewed("reviewed/identity/" + label, "fact", evidence.source_sha256)


def declared_scaffold_paths(
    context: GateContext,
    diff: DiffResult | None = None,
    *,
    require_existing: bool = True,
) -> tuple[str, ...]:
    """Resolve explicit plan declarations and current changed test files once."""
    tests_root = _normalize_path(resolve_tests_root(context.repo_root))
    declared: set[str] = set()
    unsupported_plan = unsupported_explicit_test_tokens(
        context.plan_content, tests_root=tests_root
    )
    if unsupported_plan:
        token = unsupported_plan[0]
        raise _input_error(
            f"unsupported plan test declaration {token!r}",
            f"replace {token!r} with exact test file paths or supported selectors",
        )
    unsupported_commands = unsupported_command_test_tokens(
        tuple(context.verification_commands.values()), tests_root=tests_root
    )
    if unsupported_commands:
        token = unsupported_commands[0]
        raise _input_error(
            f"unsupported command test declaration {token!r}",
            f"replace {token!r} with exact test file paths or supported selectors",
        )
    explicit_paths = list(
        extract_explicit_test_paths(context.plan_content, tests_root=tests_root)
    )
    explicit_paths.extend(
        extract_command_test_paths(
            tuple(context.verification_commands.values()), tests_root=tests_root
        )
    )
    initial = list(explicit_paths)
    if diff is not None:
        initial.extend(diff.changed_files)
        initial.extend(diff.untracked_files)

    explicit = set(explicit_paths)

    def add(value: str) -> None:
        logical = _normalize_path(value)
        if not logical.startswith(f"{tests_root}/"):
            return
        candidate = context.repo_root.joinpath(*PurePosixPath(logical).parts)
        if not candidate.exists():
            if value in explicit:
                if not require_existing:
                    declared.add(logical)
                    return
                raise _input_error(
                    f"required reviewed test artifact {logical} is missing",
                    f"restore or create {logical} before running this gate",
                )
            return
        if candidate.is_dir():
            declared.update(
                path.relative_to(context.repo_root).as_posix()
                for path in candidate.rglob("*.py")
                if path.is_file()
            )
        else:
            declared.add(logical)

    for value in initial:
        add(value)

    for relative in tuple(declared):
        candidate = context.repo_root.joinpath(*PurePosixPath(relative).parts)
        if not candidate.is_file():
            continue
        for helper_name in ("conftest.py", "helpers.py"):
            helper_path = candidate.parent / helper_name
            if helper_path.is_file():
                declared.add(helper_path.relative_to(context.repo_root).as_posix())
        parent_helper = candidate.parent.parent / "helpers.py"
        if parent_helper.is_file():
            declared.add(parent_helper.relative_to(context.repo_root).as_posix())
        declared.update(
            _local_test_dependencies(context.repo_root, candidate, tests_root)
        )
    return tuple(sorted(declared, key=lambda value: value.encode("utf-8")))


def _local_test_dependencies(
    repo_root: Path, path: Path, tests_root: str
) -> tuple[str, ...]:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return ()

    dependencies: set[str] = set()
    for node in ast.walk(module):
        names: tuple[str, ...] = ()
        if isinstance(node, ast.ImportFrom) and node.module:
            names = (node.module,)
        elif isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
        for module_name in names:
            dependency = _tests_module_to_relative_path(
                repo_root, module_name, tests_root
            )
            if dependency is not None:
                dependencies.add(dependency)
    return tuple(sorted(dependencies, key=lambda value: value.encode("utf-8")))


def _tests_module_to_relative_path(
    repo_root: Path, module_name: str, tests_root: str
) -> str | None:
    if not module_name.startswith("tests."):
        return None
    module_parts = module_name.removeprefix("tests.").split(".")
    base_path = repo_root / tests_root / Path(*module_parts)
    for candidate in (base_path.with_suffix(".py"), base_path / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(repo_root).as_posix()
    return None


def _reviewed(
    name: str,
    kind: str,
    content: str | bytes,
) -> ReviewedInput:
    raw = _content_bytes(content)
    if kind not in {"fact", "content", "producer"}:
        raise ValueError(kind)
    return ReviewedInput(
        name=name,
        kind=kind,  # type: ignore[arg-type]
        identity=hashlib.sha256(raw).hexdigest(),
        content=content,
    )


def _content_bytes(content: str | bytes) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


def _require_document(path: Path | None, content: str, *, label: str) -> None:
    if path is None or not content:
        raise _input_error(
            f"required {label} input is missing or empty",
            f"restore or provide the readable {label} before running this gate",
        )


def _logical_document_path(context: GateContext, path: Path | None, label: str) -> str:
    if path is None:
        raise _input_error(
            f"required {label} input is missing",
            f"restore or provide the {label} before running this gate",
        )
    try:
        return _normalize_path(
            path.absolute().relative_to(context.repo_root.absolute()).as_posix()
        )
    except (OSError, ValueError) as error:
        raise _input_error(
            f"required {label} path is outside the repository",
            f"provide the {label} as a repo-contained input",
        ) from error


def _normalize_path(value: str) -> str:
    # Existing milestone ownership uses a single trailing slash to denote a
    # directory. It is presentation, not a semantic path segment; canonical
    # manifest names omit it. Internal/repeated empty segments still fail.
    if value.endswith("/") and not value.endswith("//"):
        value = value[:-1]
    if not value or "\\" in value:
        raise _input_error(
            f"input path is empty or not POSIX-normalized: {value!r}",
            "use a non-empty repo-relative POSIX path",
        )
    pure = PurePosixPath(value)
    parts = value.split("/")
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or tuple(parts) != pure.parts
    ):
        raise _input_error(
            f"input path is not a normalized repo-relative path: {value!r}",
            "use a normalized repo-relative POSIX path without dot segments",
        )
    return pure.as_posix()


def _output_contract_identity(gate_type: GateType) -> tuple[str | None, str | None]:
    """Capture the current native schema identity."""
    if isinstance(gate_type.output_contract, ReviewOutputContract):
        contract = gate_type.output_contract
        return contract.version, contract.schema_sha256
    return None, None


def _canonical_logical_instructions(
    context: GateContext,
    prompt: ResolvedGatePrompt,
    cli: str,
) -> str:
    """Rebuild captured logical instructions with stable producer markers.

    Producer presence, location, and digest belong to the full input manifest;
    the review basis describes the reusable reviewer instructions. Rebuilding
    from captured resources avoids a second filesystem read while keeping an
    absent optional lane and its later concrete artifact on one source basis.
    """
    from heddle.gate.prompt import expand_captured_partials
    from heddle.kernel.decision_guidance import compose_decision_guidance

    expanded = expand_captured_partials(
        prompt.template.effective_text,
        {
            PurePosixPath(partial.logical_name).stem: partial.effective_text
            for partial in prompt.partials
        },
    )
    variables = {
        "feature": context.feature,
        "spec-path": _stable_document_path(context, context.spec_path),
        "plan-path": _stable_document_path(context, context.plan_path),
        "milestone": str(context.milestone or ""),
        "cli-tool": cli,
        "review-file-suffix": "" if cli == "claude" else f".{cli}",
    }
    logical = render_template(expanded, variables, strip_validation=True).strip()
    logical = compose_decision_guidance(prompt.decision_policy, logical)
    return "\n\n".join((prompt.output_constraint, logical))


def _stable_document_path(context: GateContext, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return _normalize_path(
            path.absolute().relative_to(context.repo_root.absolute()).as_posix()
        )
    except (OSError, ValueError):
        return path.as_posix()


def _workflow_control_paths(context: GateContext) -> list[str]:
    paths: list[str] = []
    for path in (context.plan_path, context.spec_path, context.workspace_dir):
        if path is None:
            continue
        try:
            paths.append(
                path.resolve().relative_to(context.repo_root.resolve()).as_posix()
            )
        except ValueError:
            continue
    return paths


def _paths_overlap(left: str, right: str) -> bool:
    left_path = PurePosixPath(left)
    right_path = PurePosixPath(right)
    return (
        left_path == right_path
        or left_path in right_path.parents
        or right_path in left_path.parents
    )


def _stable_text(context: GateContext, text: str) -> str:
    root = context.repo_root.as_posix().rstrip("/")
    return text.replace(f"{root}/", "")


def _input_error(message: str, hint: str) -> KernelError:
    return KernelError(code="workspace-invalid", message=message, hint=hint)
