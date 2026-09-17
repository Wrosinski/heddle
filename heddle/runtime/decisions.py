"""Typed decision batches and user resolutions over the current fact graph."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, cast

from heddle.contracts import decisions as kinds
from heddle.contracts import operations as ops
from heddle.contracts.result import Conflict, ExitCode, HeddleError, HeddleResult
from heddle.contracts.schemas import (
    DECISION_BATCH_CLASSES,
    DECISION_BATCH_KINDS,
    DECISION_BATCH_SOURCES,
    DECISION_RESOLUTION_KINDS,
    POLICY_JOURNAL_FIELDS,
    POLICY_RESOLUTION_REQUIRED_FIELDS,
    normalize_decision_route,
)
from heddle.kernel import blockers
from heddle.kernel.model import FeatureSnapshot, milestones_landed_since
from heddle.kernel.project_config import KernelError, ProjectConfig, feature_state_path
from heddle.kernel.smoke_disposition import validate_smoke_proposal
from heddle.kernel.state import DecisionFact, StateFile, parse_state_document
from heddle.runtime import application, audit, state_store
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.diagnostics import kernel_error_result
from heddle.runtime.feature_context import (
    ResolveFeatureFailure,
    resolve_snapshot_from_cwd,
)
from heddle.runtime.locking import state_recording_lock
from heddle.runtime.output import emit_envelope
from heddle.runtime.recording import allocate_decision_ids
from heddle.runtime.state_store import check_expect_revision, commit_state
from heddle.runtime.verification import (
    prepare_smoke_disposition,
)
from heddle.runtime.write_args import parse_common_with_positionals, usage_failure

_RESOLUTION_EXITS = {"usage": ExitCode.USAGE, "feature-ambiguous": ExitCode.USAGE}


@dataclass(frozen=True)
class _Target:
    feature: str
    state_path: Path
    config: ProjectConfig
    snapshot: FeatureSnapshot

    @property
    def root(self) -> Path:
        return self.config.root

    @property
    def state(self) -> StateFile:
        return self.snapshot.state

    @property
    def revision(self) -> int:
        return self.state.revision


def _resolve_target(
    feature: str | None, expect_revision: int | None = None, *, writable: bool = True
) -> _Target | HeddleResult:
    resolved = resolve_snapshot_from_cwd(feature, writable=writable)
    if isinstance(resolved, ResolveFeatureFailure):
        return kernel_error_result(
            resolved.error,
            exit_codes=_RESOLUTION_EXITS,
            diagnostics=resolved.diagnostics,
        )
    target = _Target(
        resolved.feature,
        feature_state_path(resolved.config, resolved.feature),
        resolved.config,
        resolved.snapshot,
    )
    if conflict := check_expect_revision(expect_revision, target.revision):
        return _conflict_failure(conflict)
    return target


def decisions_list(operation: ops.DecisionsList) -> HeddleResult:
    target = _resolve_target(operation.feature, writable=False)
    if isinstance(target, HeddleResult):
        return target
    return HeddleResult.success(
        {
            "feature": target.feature,
            "decisions": [
                _decision_payload(
                    decision,
                    landed_since=milestones_landed_since(target.state, decision),
                )
                for decision in target.state.decisions
            ],
        }
    )


def resolve_decision(operation: ops.ResolveDecision) -> HeddleResult:
    if not isinstance(operation.rationale, str) or not operation.rationale.strip():
        return usage_failure(
            "--resolution requires nonempty rationale",
            "describe the ruling and its permanent record",
        )
    route = normalize_decision_route(operation.routes_to)
    if route is None:
        return HeddleResult.failure(
            HeddleError(
                "invalid-route",
                f"invalid resolution route {operation.routes_to!r}",
                "provide one truthful repo-relative path",
            ),
            exit_code=ExitCode.USAGE,
        )
    if operation.kind == "policy" or operation.kind not in {
        kind for values in DECISION_RESOLUTION_KINDS.values() for kind in values
    }:
        return usage_failure(
            f"unsupported user resolution kind {operation.kind!r}",
            "use an eligible user resolution; policy is recorded through record-policy",
        )
    target = _resolve_target(operation.feature, operation.expect_revision)
    if isinstance(target, HeddleResult):
        return target
    affected: dict[str, str] = {}
    smoke_binding = None
    if operation.kind == "accept-degraded-smoke":
        proposed = next(
            (d for d in target.state.decisions if d.id == operation.decision_id), None
        )
        if (
            proposed is not None
            and proposed.kind == "smoke-disposition"
            and proposed.status != "resolved"
        ):
            assert proposed.smoke_disposition is not None
            try:
                smoke_binding = prepare_smoke_disposition(
                    target.root,
                    target.state,
                    {
                        key: value
                        for key, value in proposed.smoke_disposition.items()
                        if key not in {"bindings", "ownership"}
                    },
                )
            except KernelError as error:
                return kernel_error_result(error, exit_codes=_RESOLUTION_EXITS)

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        state = parse_state_document(document, source=target.state_path)
        decision = next(
            (d for d in state.decisions if d.id == operation.decision_id), None
        )
        if decision is None:
            raise _usage_error(f"unknown decision id {operation.decision_id!r}")
        if operation.kind not in DECISION_RESOLUTION_KINDS[decision.kind]:
            raise _usage_error(
                f"{operation.kind} is ineligible for decision kind {decision.kind}"
            )
        affected["decision_id"] = decision.id
        if decision.status == "resolved":
            if _same_resolution(decision, operation, route):
                return document
            raise _already_resolved(decision.id)
        resolution = _resolution_for(target, state, decision, operation, smoke_binding)
        updated = next(d for d in document["decisions"] if d["id"] == decision.id)
        if decision.status == "resolved":
            updated = {
                **deepcopy(updated),
                "id": allocate_decision_ids(document, 1)[0],
                "supersedes": decision.id,
                "created_at": utc_now_minutes(),
            }
            document["decisions"].append(updated)
            affected["decision_id"] = updated["id"]
        updated.update(
            status="resolved",
            resolution=kinds.resolution_payload(resolution),
            rationale=operation.rationale,
            routes_to=[route],
            resolution_source="user",
            resolved_at=utc_now_minutes(),
        )
        try:
            parse_state_document(document, source=target.state_path)
        except KernelError as error:
            raise _usage_error(error.message) from error
        return document

    return _mutate(target, operation.dry_run, "decisions resolve", transform, affected)


def _same_resolution(
    decision: DecisionFact, operation: ops.ResolveDecision, route: str
) -> bool:
    resolution = decision.resolution
    return (
        decision.status == "resolved"
        and resolution is not None
        and resolution.kind == operation.kind
        and decision.rationale == operation.rationale
        and decision.routes_to == (route,)
        and decision.resolution_source == "user"
    )


def _resolution_for(
    target: _Target,
    state: StateFile,
    decision: DecisionFact,
    operation: ops.ResolveDecision,
    smoke_binding: dict | None,
) -> kinds.Resolution:
    if operation.kind == "disposition":
        return kinds.Disposition()
    if operation.kind == "continue-review":
        from heddle.runtime.review_assignments import projection

        assignment = next(
            (
                a
                for a in state.review_assignments.assignments
                if a.stop_decision_id == decision.id
            ),
            None,
        )
        if (
            assignment is None
            or decision.source != "session"
            or decision.escalation_class != 2
        ):
            raise _usage_error(
                "continue-review requires the current assignment stop decision"
            )
        # Use the same qualified source and evidence projection as round opening.
        snap = replace(target.snapshot, state=state)
        row = next(
            row
            for row in projection(target.config, snap)["review_closure"]["assignments"]
            if row["assignment_id"] == assignment.id
        )
        if (
            row["closed"]
            or row["missing_slots"]
            or row["stop_reason"] not in {"round-limit", "no-progress", "no-decrease"}
        ):
            raise _usage_error("continue-review requires an unresolved review stop")
        return kinds.ContinueReview(assignment.id, len(assignment.rounds))
    if decision.target_stage is not None and decision.target_stage != state.stage:
        raise _usage_error("privileged resolution targets a different stage")
    if operation.kind == "continue-stage":
        return kinds.ContinueStage(
            0,
            blockers.errored_gate_run_count(state, target.snapshot.required_gates),
        )
    if operation.kind == "continue-session":
        return kinds.ContinueSession(
            sum(session.stage == decision.target_stage for session in state.sessions)
        )
    if operation.kind == "accept-degraded-smoke":
        assert decision.smoke_disposition is not None
        # The CAS protects ordinary decision/verification/declaration changes;
        # gate appends at the same revision remain in this locked document.
        if smoke_binding != dict(decision.smoke_disposition):
            raise _usage_error(
                "smoke disposition evidence changed since it was proposed"
            )
        return kinds.AcceptDegradedSmoke()
    raise _usage_error(f"unsupported resolution kind {operation.kind}")


def decisions_add(operation: ops.DecisionsAdd) -> HeddleResult:
    target = _resolve_target(operation.feature, operation.expect_revision)
    if isinstance(target, HeddleResult):
        return target
    if not operation.decisions:
        return usage_failure(
            "decision batch must be nonempty", "provide at least one eligible decision"
        )
    smoke_bindings = {}
    for index, item in enumerate(operation.decisions):
        if item.kind != "smoke-disposition":
            continue
        try:
            proposal = validate_smoke_proposal(target.state, item.smoke_disposition)
        except KernelError as error:
            return kernel_error_result(
                _usage_error(error.message), exit_codes=_RESOLUTION_EXITS
            )
        try:
            smoke_bindings[index] = prepare_smoke_disposition(
                target.root, target.state, proposal
            )
        except KernelError as error:
            return kernel_error_result(error, exit_codes=_RESOLUTION_EXITS)

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        state = parse_state_document(document, source=target.state_path)
        identifiers = allocate_decision_ids(document, len(operation.decisions))
        additions = []
        for index, (identifier, item) in enumerate(
            zip(identifiers, operation.decisions, strict=True)
        ):
            if (
                item.kind not in DECISION_BATCH_KINDS
                or item.escalation_class not in DECISION_BATCH_CLASSES
                or item.source not in DECISION_BATCH_SOURCES
            ):
                raise _usage_error(
                    "decision kind/class/source is not eligible for an explicit batch"
                )
            if (
                not all(value.strip() for value in (item.title, item.question))
                or not item.options
                or any(not value.strip() for value in item.options)
            ):
                raise _usage_error(
                    "decision title, question and options must be nonempty"
                )
            routes = [normalize_decision_route(route) for route in item.routes_to]
            if not routes or any(route is None for route in routes):
                raise _usage_error(
                    "decision routes_to must be a nonempty array of truthful paths"
                )
            fields = {
                key: value for key, value in asdict(item).items() if value is not None
            }
            fields.pop("escalation_class")
            fields["options"] = list(item.options)
            fields["routes_to"] = routes
            fields.update(
                id=identifier,
                status="pending",
                resolution=None,
                rationale=None,
                resolution_source=None,
                created_at=utc_now_minutes(),
                resolved_at=None,
            )
            fields["class"] = item.escalation_class
            if item.kind in {"stage-cap", "session-cap"}:
                _bind_cap(state, target.snapshot, item, fields)
            if item.kind == "smoke-disposition":
                fields["smoke_disposition"] = smoke_bindings[index]
            additions.append(fields)
        document["decisions"].extend(additions)
        try:
            parse_state_document(document, source=target.state_path)
        except KernelError as error:
            raise _usage_error(error.message) from error
        return document

    return _mutate(target, operation.dry_run, "decisions add", transform, {})


def record_policy(operation: ops.RecordPolicy) -> HeddleResult:
    """Record only ordinary standing-policy choices, journal before facts."""
    target = _resolve_target(operation.feature, operation.expect_revision)
    if isinstance(target, HeddleResult):
        return target
    journal = audit.decision_journal_path(target.config, target.feature)
    publication: str | None = None
    journal_published = False

    def transform(document: dict[str, Any]) -> dict[str, Any]:
        nonlocal publication
        state = parse_state_document(document, source=target.state_path)
        _validate_policy_inputs(operation.resolutions, state)
        additions = []
        existing = {decision.id: decision for decision in state.decisions}
        for item in operation.resolutions:
            candidate = _policy_fact(item)
            prior = existing.get(item.id)
            if prior is not None:
                fields = _decision_payload(prior)
                changed = [
                    key
                    for key, value in candidate.items()
                    if key not in {"created_at", "resolved_at"}
                    and fields.get(key) != value
                ]
                if changed:
                    if prior.kind != "policy":
                        detail = f"ID belongs to an existing {prior.kind} decision"
                    else:
                        if "resolution" in changed:
                            changed.remove("resolution")
                            changed.extend(
                                f"resolution.{key}"
                                for key, value in candidate["resolution"].items()
                                if fields["resolution"].get(key) != value
                            )
                        detail = "policy fields differ: " + ", ".join(changed)
                    raise _policy_collision(item.id, target.state_path, detail)
            else:
                if item.basis.casefold() == "conflict":
                    raise KernelError(
                        code="usage",
                        message=f"policy {item.id!r} has an unresolved conflict",
                        hint=(
                            "record an owner question with heddle decisions add; "
                            "journal-only legacy conflict recovery also requires "
                            "an owner ruling because the journal cannot prove "
                            "structured identity. Only exact complete-fact retries "
                            "may use record-policy"
                        ),
                    )
                additions.append(candidate)
        try:
            text = journal.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = f"# Decision Journal: {target.feature}\n"
        except (OSError, UnicodeError) as error:
            raise _policy_journal_error(journal, error) from error
        sections = audit.journal_sections(text)
        new_sections = []
        for item in operation.resolutions:
            expected = _policy_section(item)
            prior_sections = sections.get(item.id, ())
            if prior_sections:
                if len(prior_sections) != 1 or _policy_section_identity(
                    prior_sections[0]
                ) != _policy_section_identity(expected):
                    raise _policy_collision(
                        item.id, journal, "journal section differs or is duplicated"
                    )
            else:
                new_sections.append("## " + expected)
        publication = (
            text.rstrip() + "\n\n" + "\n\n".join(new_sections) + "\n"
            if new_sections
            else None
        )
        document["decisions"].extend(additions)
        return document

    def publish_journal() -> None:
        nonlocal journal_published
        if publication is not None:
            _publish_policy_journal(journal, publication)
            journal_published = True

    try:
        if operation.dry_run:
            with state_recording_lock(target.state_path):
                before = state_store.read_state_document(target.state_path)
                if conflict := check_expect_revision(
                    target.revision, before["revision"]
                ):
                    raise conflict
                after = transform(deepcopy(before))
                parse_state_document(after, source=target.state_path)
                wrote = after != before
                revision = before["revision"] + int(wrote)
        else:
            commit = state_store._commit_state(
                target.state_path,
                expect_revision=target.revision,
                transform=transform,
                before_publish=publish_journal,
            )
            revision, wrote = commit.revision, commit.wrote
    except Conflict as error:
        return _conflict_failure(error)
    except KernelError as error:
        if journal_published:
            error = KernelError(
                code=error.code,
                message=f"{error.message}; policy journal was published at {journal}",
                hint=(
                    "repair state publication, then retry the identical policy "
                    "batch to attach the journal entry to its state fact"
                ),
                details=error.details,
            )
        return kernel_error_result(error, exit_codes=_RESOLUTION_EXITS)
    return HeddleResult.success(
        {
            "feature": target.feature,
            "revision": revision,
            "mutation_summary": {
                "command": "decisions record-policy",
                "would_write": wrote or publication is not None,
            },
            "decision_ids": [item.id for item in operation.resolutions],
            **({"dry_run": True} if operation.dry_run else {}),
        }
    )


def _validate_policy_inputs(
    items: tuple[kinds.PolicyResolutionInput, ...], state: StateFile
) -> None:
    if not isinstance(items, tuple) or not items:
        raise _usage_error("policy resolutions must be a nonempty tuple")
    identifiers: set[str] = set()
    scopes = {"feature", *(m.id for m in state.milestones if m.status == "current")}
    for item in items:
        if not isinstance(item, kinds.PolicyResolutionInput):
            raise _usage_error(
                "policy resolutions require PolicyResolutionInput records"
            )
        fields = asdict(item)
        for key, value in fields.items():
            if key in {"options", "routes_to"}:
                valid = (
                    isinstance(value, tuple)
                    and bool(value)
                    and all(_policy_line(entry) for entry in value)
                )
            elif key == "review_flag":
                valid = value in ("", "[REVIEW]")
            else:
                valid = _policy_line(value)
            if not valid:
                raise _usage_error(
                    f"policy {key} must contain the declared single-line values"
                )
        if any(char.isspace() for char in item.id) or item.id in identifiers:
            raise _usage_error("policy IDs must be unique and contain no whitespace")
        identifiers.add(item.id)
        if (
            item.choice not in item.options
            or item.reversibility != "reversible"
            or item.confidence not in {"high", "medium", "low"}
            or item.scope not in scopes
            or (
                item.basis.casefold() in {"none", "conflict"}
                and item.review_flag != "[REVIEW]"
            )
        ):
            raise _usage_error(
                f"policy {item.id!r} is not an eligible reversible choice "
                "in the current scope"
            )
        if any(normalize_decision_route(route) is None for route in item.routes_to):
            raise _usage_error("policy routes_to must name truthful repository paths")


def _policy_line(value: Any) -> bool:
    return (
        isinstance(value, str) and bool(value.strip()) and value.splitlines() == [value]
    )


def _policy_fact(item: kinds.PolicyResolutionInput) -> dict[str, Any]:
    at = utc_now_minutes()
    return {
        "id": item.id,
        "kind": "policy",
        "source": "policy",
        "title": item.title,
        "question": item.question,
        "options": list(item.options),
        "status": "resolved",
        "resolution": kinds.resolution_payload(
            kinds.PolicyResolution(
                item.choice,
                item.basis,
                item.reversibility,
                item.confidence,
                item.review_flag,
                item.scope,
            )
        ),
        "rationale": item.rationale,
        "resolution_source": "policy",
        "routes_to": [normalize_decision_route(route) for route in item.routes_to],
        "created_at": at,
        "resolved_at": at,
    }


def _policy_section(item: kinds.PolicyResolutionInput) -> str:
    fields = asdict(item)
    fields["options"] = ", ".join(item.options)
    fields["routes_to"] = ", ".join(
        str(normalize_decision_route(route)) for route in item.routes_to
    )
    return f"{item.id} — {item.title}\n\n- record_kind: policy\n" + "\n".join(
        f"- {key}: {fields[key]}".rstrip() for key in POLICY_JOURNAL_FIELDS
    )


def _policy_section_identity(section: str) -> str:
    """Ignore incidental line-end formatting, retaining every semantic byte."""
    return "\n".join(line.rstrip() for line in section.splitlines()).rstrip()


def _publish_policy_journal(path: Path, text: str) -> None:
    try:
        state_store.replace_file_text(path, text)
    except OSError as error:
        raise _policy_journal_error(path, error) from error


def _policy_journal_error(path: Path, error: Exception) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"policy journal {path} is unavailable: {error}",
        hint="repair journal access, then retry the identical policy batch",
    )


def _bind_cap(
    state: StateFile,
    snapshot: FeatureSnapshot,
    item: kinds.DecisionInput,
    fields: dict[str, Any],
) -> None:
    if item.target_stage != state.stage:
        raise _usage_error("cap must identify the currently reached stage")
    if item.kind == "session-cap":
        reached = (
            item.target_blocker == "no-progress" and blockers.no_progress_reached(state)
        ) or (
            item.target_blocker == "attempt-cap" and blockers.attempt_cap_reached(state)
        )
    elif item.kind == "stage-cap":
        reached = (
            item.target_blocker == "gate-cap-reached"
            and blockers.gate_cap_reached(state, snapshot.required_gates)
        )
    else:
        reached = False
    if not reached:
        raise _usage_error(
            "cap target does not identify one currently reached native limit"
        )


def _mutate(
    target: _Target,
    dry_run: bool,
    command: str,
    transform: state_store.StateTransform,
    affected: dict[str, Any],
) -> HeddleResult:
    try:
        if dry_run:
            with state_recording_lock(target.state_path):
                before = state_store.read_state_document(target.state_path)
                if conflict := check_expect_revision(
                    target.revision, before["revision"]
                ):
                    raise conflict
                after = transform(deepcopy(before))
                parse_state_document(after, source=target.state_path)
                wrote = after != before
                revision = before["revision"] + int(wrote)
        else:
            commit = commit_state(
                target.state_path, expect_revision=target.revision, transform=transform
            )
            revision, wrote = commit.revision, commit.wrote
    except Conflict as error:
        return _conflict_failure(error)
    except KernelError as error:
        return kernel_error_result(error, exit_codes=_RESOLUTION_EXITS)
    return HeddleResult.success(
        {
            "feature": target.feature,
            "revision": revision,
            "mutation_summary": {"command": command, "would_write": wrote},
            **affected,
            **({"dry_run": True} if dry_run else {}),
        }
    )


def _decision_payload(
    decision: DecisionFact, *, landed_since: tuple[str, ...] = ()
) -> dict[str, Any]:
    payload = ops.decoded_payload(decision)
    payload["class"] = payload.pop("escalation_class")
    payload["milestones_landed_since"] = list(landed_since)
    return payload


def _usage_error(message: str) -> KernelError:
    return KernelError(
        code="usage",
        message=message,
        hint="inspect the decision kind and exact target before resolving",
    )


def _already_resolved(identifier: str) -> KernelError:
    return KernelError(
        code="decision-already-resolved",
        message=f"decision {identifier!r} is already resolved with different data",
        hint="retain the original ruling; only an eligible verdict extension "
        "may supersede it",
    )


def _policy_collision(identifier: str, path: Path, detail: str) -> KernelError:
    return KernelError(
        code="decision-already-resolved",
        message=f"policy {identifier!r}: {detail} in {path}",
        hint=(
            "preserve the recorded policy and journal; retry the identical batch "
            "with its matching journal section, or choose a fresh policy ID"
        ),
    )


def _conflict_failure(error: Conflict) -> HeddleResult:
    return HeddleResult.failure(
        HeddleError(
            "conflict", str(error), "read current status and retry the operation"
        ),
        exit_code=ExitCode.CONFLICT,
    )


def _read_json_payload(source: str) -> Any:
    try:
        text = (
            sys.stdin.read()
            if source == "-"
            else Path(source).read_text(encoding="utf-8")
        )
        return json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return usage_failure(
            f"invalid decision JSON: {error}", "provide one readable UTF-8 JSON batch"
        )


def run_decisions(args: list[str], json_mode: bool) -> int:
    verb = args[0] if args and not args[0].startswith("--") else "list"
    rest = args[1:] if args and args[0] == verb else args
    parsed, positionals, failure = _parse_decision_args(rest, verb)
    if failure is not None:
        return _emit(failure, json_mode)
    assert parsed is not None
    feature, expect_revision, dry_run, values = parsed
    operation: (
        ops.DecisionsList | ops.ResolveDecision | ops.DecisionsAdd | ops.RecordPolicy
    )
    if verb == "list":
        if positionals or expect_revision is not None or dry_run or values:
            return _emit(
                usage_failure(
                    "invalid decisions list arguments",
                    "use heddle decisions list [--feature <slug>]",
                ),
                json_mode,
            )
        operation = ops.DecisionsList(feature)
    elif verb == "resolve":
        if len(positionals) != 1 or any(
            flag not in values for flag in ("--kind", "--resolution", "--routes-to")
        ):
            return _emit(
                usage_failure(
                    "decisions resolve requires id, --kind, --resolution "
                    "and --routes-to",
                    "provide the typed resolution and authored rationale",
                ),
                json_mode,
            )
        operation = ops.ResolveDecision(
            positionals[0],
            cast(kinds.ResolutionKind, values["--kind"]),
            values["--resolution"],
            values["--routes-to"],
            feature=feature,
            expect_revision=expect_revision,
            dry_run=dry_run,
        )
    elif verb == "add":
        if positionals or "--input-json" not in values:
            return _emit(
                usage_failure(
                    "decisions add requires --input-json",
                    "provide one heddle.decision-batch/v1 document",
                ),
                json_mode,
            )
        batch = _decode_decision_batch(_read_json_payload(values["--input-json"]))
        if isinstance(batch, HeddleResult):
            return _emit(batch, json_mode)
        operation = ops.DecisionsAdd(
            batch,
            feature=feature,
            expect_revision=expect_revision,
            dry_run=dry_run,
        )
    elif verb == "record-policy":
        if positionals or "--from-file" not in values:
            return _emit(
                usage_failure(
                    "decisions record-policy requires --from-file",
                    "provide one heddle.policy-batch/v1 document",
                ),
                json_mode,
            )
        policies = _decode_policy_batch(_read_json_payload(values["--from-file"]))
        if isinstance(policies, HeddleResult):
            return _emit(policies, json_mode)
        operation = ops.RecordPolicy(
            policies,
            feature=feature,
            expect_revision=expect_revision,
            dry_run=dry_run,
        )
    else:
        return _emit(
            usage_failure(
                f"unknown decisions command {verb!r}",
                "use list, add, resolve or record-policy",
            ),
            json_mode,
        )
    return _emit(application.execute(operation), json_mode)


def _parse_decision_args(
    args: list[str], verb: str
) -> tuple[
    tuple[str | None, int | None, bool, dict[str, str]] | None,
    list[str],
    HeddleResult | None,
]:
    flags = {
        "resolve": {"--kind", "--resolution", "--routes-to"},
        "add": {"--input-json"},
        "record-policy": {"--from-file"},
        "list": set(),
    }.get(verb, set())
    values: dict[str, str] = {}
    remaining: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in flags:
            if index + 1 == len(args) or token in values:
                return (
                    None,
                    [],
                    usage_failure(
                        f"{token} requires one value", "check decisions command help"
                    ),
                )
            values[token] = args[index + 1]
            index += 2
        else:
            remaining.append(token)
            index += 1
    parsed, positionals, failure = parse_common_with_positionals(remaining)
    if failure is not None:
        return None, [], failure
    assert parsed is not None
    return (
        (
            parsed.feature,
            parsed.expect_revision,
            parsed.dry_run,
            values,
        ),
        positionals,
        None,
    )


def _decode_policy_batch(
    value: Any,
) -> tuple[kinds.PolicyResolutionInput, ...] | HeddleResult:
    if isinstance(value, HeddleResult):
        return value
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "resolutions"}
        or value["schema"] != "heddle.policy-batch/v1"
        or not isinstance(value["resolutions"], list)
        or not value["resolutions"]
    ):
        return usage_failure(
            "expected a nonempty heddle.policy-batch/v1 document",
            "provide exactly schema and resolutions",
        )
    required = set(POLICY_RESOLUTION_REQUIRED_FIELDS)
    policies = []
    for item in value["resolutions"]:
        if (
            not isinstance(item, dict)
            or not required <= set(item)
            or not set(item) <= required | {"review_flag"}
        ):
            return usage_failure(
                "policy resolution has missing or unknown fields",
                "use the closed policy input; "
                "authority and provenance are runtime-owned",
            )
        if any(
            not isinstance(item[key], list)
            or any(not isinstance(entry, str) for entry in item[key])
            for key in ("options", "routes_to")
        ):
            return usage_failure(
                "policy options and routes_to must be string arrays",
                "provide nonempty option and repository path arrays",
            )
        policies.append(
            kinds.PolicyResolutionInput(
                **{
                    **item,
                    "options": tuple(item["options"]),
                    "routes_to": tuple(item["routes_to"]),
                }
            )
        )
    return tuple(policies)


def _decode_decision_batch(
    value: Any,
) -> tuple[kinds.DecisionInput, ...] | HeddleResult:
    if isinstance(value, HeddleResult):
        return value
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "decisions"}
        or value["schema"] != "heddle.decision-batch/v1"
        or not isinstance(value["decisions"], list)
        or not value["decisions"]
    ):
        return usage_failure(
            "expected a nonempty heddle.decision-batch/v1 document",
            "provide exactly schema and decisions",
        )
    required = {"kind", "class", "source", "title", "question", "options", "routes_to"}
    optional = {"recommendation", "target_stage", "target_blocker", "smoke_disposition"}
    entries = []
    for item in value["decisions"]:
        if (
            not isinstance(item, dict)
            or not required <= set(item)
            or not set(item) <= required | optional
        ):
            return usage_failure(
                "decision has missing or unknown fields",
                "use the closed decision input contract",
            )
        if item["kind"] not in DECISION_BATCH_KINDS:
            return usage_failure(
                "finding and policy decisions are not public batch kinds",
                "use an eligible explicit question or cap",
            )
        if type(item["class"]) is not int or any(
            not isinstance(item[key], str)
            for key in ("kind", "source", "title", "question")
        ):
            return usage_failure(
                "invalid decision field type",
                "class must be an integer and authored fields must be strings",
            )
        if any(
            not isinstance(item[key], list)
            or any(not isinstance(entry, str) for entry in item[key])
            for key in ("options", "routes_to")
        ):
            return usage_failure(
                "options and routes_to must be string arrays",
                "record normalized route arrays",
            )
        fields = {
            **item,
            "escalation_class": item["class"],
            "options": tuple(item["options"]),
            "routes_to": tuple(item["routes_to"]),
        }
        del fields["class"]
        entries.append(kinds.DecisionInput(**fields))
    return tuple(entries)


def _emit(result: HeddleResult, json_mode: bool) -> int:
    return emit_envelope(result, json_mode, _render_human)


def _render_human(result: HeddleResult) -> None:
    if result.error is not None:
        print(
            f"heddle: error[{result.error.code}]: {result.error.message}",
            file=sys.stderr,
        )
        print(f"  hint: {result.error.hint}", file=sys.stderr)
        return
    data = result.data or {}
    for decision in data.get("decisions", []):
        print(f"{decision['id']} — {decision['status']} — {decision['title']}")
        if decision.get("rationale") is not None:
            print(f"  rationale: {decision['rationale']}")
        if decision.get("question"):
            print(f"  question: {decision['question']}")
        if decision.get("options"):
            print(f"  options: {', '.join(decision['options'])}")
        if decision.get("recommendation"):
            print(f"  recommendation: {decision['recommendation']}")
        if decision.get("milestones_landed_since"):
            print(
                "  landed since raised: "
                + ", ".join(decision["milestones_landed_since"])
            )
    if "decisions" not in data:
        print(
            f"{data.get('feature', '')}: decision operation recorded "
            f"at revision {data.get('revision')}"
        )
