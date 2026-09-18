from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from heddle.contracts import operations as ops
from heddle.contracts.result import HeddleResult
from heddle.io.source import observe_source
from heddle.kernel.project_config import KernelError
from heddle.kernel.source_attribution import (
    SourceAttribution,
    attribution_error,
    attribution_payload,
    overlaps_ownership,
    parse_attribution_input,
    validate_attribution_evidence,
)
from heddle.kernel.source_manifest import SourceDefinition, SourceEvidence
from heddle.kernel.state import StateFile, parse_state_document
from heddle.kernel.verification import (
    reconcile_source_coverage,
    resolve_source_declaration,
)
from heddle.runtime.clock import utc_now_seconds
from heddle.runtime.feature_context import ResolvedSnapshotContext
from heddle.runtime.feature_inputs import _read_payload
from heddle.runtime.verification import inventory_source_paths, workflow_control_paths
from heddle.runtime.write_args import parse_write_args, usage_failure
from heddle.runtime.write_path import emit_result, run_state_mutation

_USAGE = "heddle feature sources attribute --from-file <json>|- [--feature <slug>]"


def observe_attribution(root: Path, paths: tuple[str, ...]) -> SourceEvidence:
    return observe_source(root, SourceDefinition("outside-feature", paths, paths))


def qualified_attribution_paths(root: Path, state: StateFile) -> tuple[str, ...]:
    owned = resolve_source_declaration(state, "acceptance").paths
    latest = {
        path: index
        for index, row in enumerate(state.source_attributions)
        for path in row.source.definition.paths
    }
    qualified: list[str] = []
    for index, row in enumerate(state.source_attributions):
        paths = tuple(
            path
            for path in row.source.definition.paths
            if latest[path] == index and not overlaps_ownership(path, owned)
        )
        if not paths:
            continue
        current = observe_attribution(root, paths)
        expected = tuple(item for item in row.source.observations if item.path in paths)
        if current.observations != expected:
            raise attribution_error(f"outside-feature attribution is stale for {paths}")
        references = observe_attribution(root, row.references.definition.paths)
        if references != row.references:
            raise attribution_error(
                f"outside-feature attribution evidence changed for {paths}"
            )
        qualified.extend(paths)
    return tuple(sorted(qualified))


def attribute_sources(operation: ops.AttributeSources) -> HeddleResult:
    try:
        entries = parse_attribution_input(operation.payload)
    except KernelError as error:
        return usage_failure(error.message, error.hint)
    context: ResolvedSnapshotContext | None = None

    def prepare(value: ResolvedSnapshotContext) -> None:
        nonlocal context
        context = value

    def render(document: dict[str, Any]) -> dict[str, Any]:
        assert context is not None
        state = parse_state_document(document, source=context.state_path)
        root = context.config.root
        declaration = resolve_source_declaration(state, "acceptance")
        controls = workflow_control_paths(context.config, state)
        inventory = inventory_source_paths(
            root,
            declaration,
            baseline_probe=context.state_path.relative_to(root).as_posix(),
            source_baseline=state.source_baseline,
        )
        coverage = reconcile_source_coverage(
            declaration, inventory, controls.roots, controls.exact
        )
        if coverage.status == "baseline-unavailable":
            raise attribution_error(coverage.action or "source baseline unavailable")
        unresolved = set(coverage.unresolved_paths)
        result = deepcopy(document)
        retained = result.setdefault("source_attributions", [])
        proposed = []
        for entry in entries:
            paths = tuple(entry["paths"])
            if not set(paths) <= unresolved:
                raise attribution_error(
                    "only exact unresolved outside-owned paths may be attributed: "
                    f"{sorted(set(paths) - unresolved)}"
                )
            refs = tuple(entry["references"])
            if context.state_path.relative_to(root).as_posix() in refs:
                raise attribution_error(
                    "attribution cannot cite its own mutable ledger"
                )
            source = observe_attribution(root, paths)
            references = observe_attribution(root, refs)
            validate_attribution_evidence(source, references=False)
            validate_attribution_evidence(references, references=True)
            row = SourceAttribution(
                entry["reason"], utc_now_seconds(), source, references
            )
            payload = attribution_payload(row)
            proposed.append(payload)
        previous = retained[-len(proposed) :]
        if [{k: v for k, v in row.items() if k != "at"} for row in previous] == [
            {k: v for k, v in row.items() if k != "at"} for row in proposed
        ]:
            return document
        retained.extend(proposed)
        return result

    return run_state_mutation(
        operation,
        command_name="feature sources attribute",
        render=render,
        precheck=prepare,
        extra={"attributed_paths": [path for row in entries for path in row["paths"]]},
    )


def run_attribute_sources(args: list[str], json_mode: bool) -> int:
    parsed, values, _, failure = parse_write_args(
        args,
        value_flags={"--from-file": ("--from-file requires a path or -", _USAGE)},
        unknown_hint=_USAGE,
        allow_positionals=False,
    )
    if failure is not None:
        return emit_result(failure, json_mode)
    assert parsed is not None
    source = values["--from-file"]
    if source is None:
        return emit_result(usage_failure("--from-file is required", _USAGE), json_mode)
    try:
        content = _read_payload(source)
        if len(content) > 1024 * 1024:
            raise ValueError("source attribution payload exceeds 1 MiB")
        payload = json.loads(content)
        parse_attribution_input(payload)
    except (OSError, ValueError, RecursionError, KernelError) as error:
        return emit_result(usage_failure(str(error), _USAGE), json_mode)
    from heddle.runtime.application import execute

    return emit_result(
        execute(
            ops.AttributeSources(
                payload,
                feature=parsed.feature,
                expect_revision=parsed.expect_revision,
                dry_run=parsed.dry_run,
            )
        ),
        json_mode,
    )
