"""
Contract tests for the prompt-refinement workflow companion.

The changed-behavior tests were strict xfails during scaffold and became
ordinary tests when milestone core supplied the author-facing contract. Tests
explicitly labelled as survivor pins protect pre-existing behavior.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

import heddle
from heddle.gate.extraction import extract_markdown_h2_section
from heddle.gate.prompt import PACKAGED_STANDARDS_DOC

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = REPO_ROOT / "docs/workflow/prompt-refinement.md"
WORKFLOW_PATH = REPO_ROOT / "docs/workflow/workflow.md"

ROLE_VALUES = ("procedure", "doctrine", "reusable-library")
SCHEMA = "heddle.prompt-refinement/v1"
STATUS_VALUES = ("planning", "evaluated", "ready-for-promotion")
INTENT_VALUES = (
    "addition",
    "removal",
    "consolidation",
    "relocation",
    "wording-correction",
)
ATTRIBUTION_VALUES = (
    "prompt",
    "model-selection",
    "delivery-transport",
    "runtime",
    "invalid-comparison",
    "not-run",
)
PROMOTION_VALUES = ("PROMOTE", "NO CHANGE", "WITHHELD", "REVISE", "REMOVE")
LIVE_EDIT_OUTCOMES = (("addition", "PROMOTE"), ("removal", "REMOVE"))
COMPARISON_AXES = (
    "cases",
    "rubric",
    "grader",
    "gate",
    "cli",
    "model",
    "reasoning_effort",
    "sandbox",
    "runtime_revision",
    "iteration_cap",
    "configuration",
)
SHA256_PATHS = (
    "baseline.effective_prompt_sha256",
    "candidate.sha256",
    "arms.baseline.effective_prompt_sha256",
    "arms.baseline.review_basis_hash",
    "arms.baseline.input_hash",
    "arms.candidate.effective_prompt_sha256",
    "arms.candidate.review_basis_hash",
    "arms.candidate.input_hash",
)
PROMOTION_EVIDENCE_PATHS = (
    "baseline.repo_revision",
    "baseline.runtime_revision",
    "baseline.prompt_version",
    *SHA256_PATHS,
    "candidate.path",
    "evaluation.cases",
    "evaluation.rubric.id",
    "evaluation.rubric.version",
    "evaluation.grader.id",
    "evaluation.grader.version",
    *(f"evaluation.execution.{axis}" for axis in COMPARISON_AXES[3:]),
    *(
        f"arms.{arm}.execution.{axis}"
        for arm in ("baseline", "candidate")
        for axis in COMPARISON_AXES
    ),
    *(
        f"arms.{arm}.{result}"
        for arm in ("baseline", "candidate")
        for result in (
            "expected_signal_hits",
            "false_positives",
            "output_contract",
            "iterations",
            "cost",
            "latency",
        )
    ),
)

RECORD_PATHS = (
    "schema",
    "record_id",
    "status",
    "date",
    "source.accepted_intent_ids",
    "source.observed_failure_ids",
    "classification.role",
    "classification.intent",
    "placement.default",
    "placement.mode",
    "placement.target",
    "placement.anchors",
    "placement.consumers",
    "placement.affected_families",
    "baseline.repo_revision",
    "baseline.runtime_revision",
    "baseline.prompt_version",
    "baseline.effective_prompt_sha256",
    "candidate.path",
    "candidate.sha256",
    "evaluation.evidence_sufficient",
    "evaluation.cases",
    "evaluation.expected_signals",
    "evaluation.rubric.id",
    "evaluation.rubric.version",
    "evaluation.grader.id",
    "evaluation.grader.version",
    "evaluation.execution.gate",
    "evaluation.execution.cli",
    "evaluation.execution.model",
    "evaluation.execution.reasoning_effort",
    "evaluation.execution.sandbox",
    "evaluation.execution.runtime_revision",
    "evaluation.execution.iteration_cap",
    "evaluation.execution.configuration",
    "arms.baseline.execution.cases",
    "arms.baseline.execution.rubric.id",
    "arms.baseline.execution.rubric.version",
    "arms.baseline.execution.grader.id",
    "arms.baseline.execution.grader.version",
    "arms.baseline.execution.gate",
    "arms.baseline.execution.cli",
    "arms.baseline.execution.model",
    "arms.baseline.execution.reasoning_effort",
    "arms.baseline.execution.sandbox",
    "arms.baseline.execution.runtime_revision",
    "arms.baseline.execution.iteration_cap",
    "arms.baseline.execution.configuration",
    "arms.baseline.effective_prompt_sha256",
    "arms.baseline.review_basis_hash",
    "arms.baseline.input_hash",
    "arms.baseline.expected_signal_hits",
    "arms.baseline.false_positives",
    "arms.baseline.output_contract",
    "arms.baseline.iterations",
    "arms.baseline.cost",
    "arms.baseline.latency",
    "arms.candidate.execution.cases",
    "arms.candidate.execution.rubric.id",
    "arms.candidate.execution.rubric.version",
    "arms.candidate.execution.grader.id",
    "arms.candidate.execution.grader.version",
    "arms.candidate.execution.gate",
    "arms.candidate.execution.cli",
    "arms.candidate.execution.model",
    "arms.candidate.execution.reasoning_effort",
    "arms.candidate.execution.sandbox",
    "arms.candidate.execution.runtime_revision",
    "arms.candidate.execution.iteration_cap",
    "arms.candidate.execution.configuration",
    "arms.candidate.effective_prompt_sha256",
    "arms.candidate.review_basis_hash",
    "arms.candidate.input_hash",
    "arms.candidate.expected_signal_hits",
    "arms.candidate.false_positives",
    "arms.candidate.output_contract",
    "arms.candidate.iterations",
    "arms.candidate.cost",
    "arms.candidate.latency",
    "disposition.attribution",
    "disposition.promotion",
    "disposition.authority",
    "disposition.target",
    "disposition.reason",
)


def _required_text(path: Path, *, ac: str) -> str:
    assert path.is_file(), f"FAIL {ac}: required document is missing: {path}"
    return path.read_text(encoding="utf-8")


def _required_h2(text: str, heading: str, *, ac: str) -> str:
    section = extract_markdown_h2_section(text, heading)
    assert section, f"FAIL {ac}: missing exact H2 section {heading!r}"
    return section


def _yaml_record(text: str) -> dict[str, Any]:
    section = _required_h2(text, "Record template", ac="AC-1")
    blocks = re.findall(r"```yaml\n(.*?)\n```", section, flags=re.DOTALL)
    assert len(blocks) == 1, (
        "FAIL AC-1: Record template must contain exactly one fenced YAML record"
    )
    loaded = yaml.safe_load(blocks[0])
    assert isinstance(loaded, dict), "FAIL AC-1: record template is not a YAML mapping"
    return loaded


def _lookup(record: dict[str, Any], dotted_path: str) -> Any:
    value: Any = record
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(dotted_path)
        value = value[part]
    return value


def _drop(record: dict[str, Any], dotted_path: str) -> None:
    parent: Any = record
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        parent = parent[part]
    del parent[parts[-1]]


def _record_errors(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for dotted_path in RECORD_PATHS:
        try:
            _lookup(record, dotted_path)
        except KeyError:
            errors.append(dotted_path)

    source = record.get("source", {})
    if isinstance(source, dict) and not (
        source.get("accepted_intent_ids") or source.get("observed_failure_ids")
    ):
        errors.append("source.accepted_intent_ids|observed_failure_ids")
    return errors


def _backticked_values(section: str, label: str, *, ac: str) -> tuple[str, ...]:
    line = next((line for line in section.splitlines() if label in line), "")
    assert line, f"FAIL {ac}: missing {label!r} enumeration"
    return tuple(re.findall(r"`([^`]+)`", line))


def _assert_required_terms(
    section: str,
    required: tuple[str, ...],
    *,
    failure_prefix: str,
) -> None:
    normalized = " ".join(section.casefold().split())
    missing = [term for term in required if term not in normalized]
    assert not missing, f"{failure_prefix} {missing}"


def _classification_placement_errors(
    record: dict[str, Any],
    *,
    expected_consumers: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    classification = record.get("classification", {})
    if not isinstance(classification, dict):
        return ["classification.role", "classification.intent"]
    if classification.get("role") not in ROLE_VALUES:
        errors.append("classification.role")
    if classification.get("intent") not in INTENT_VALUES:
        errors.append("classification.intent")

    placement = record.get("placement", {})
    if not isinstance(placement, dict):
        return [
            *errors,
            "placement.default",
            "placement.mode",
            "placement.anchors",
            "placement.consumers",
            "placement.family-census",
            "placement.affected-families.case-id",
        ]
    if placement.get("default") != "local":
        errors.append("placement.default")
    if placement.get("mode") not in ("local", "shared"):
        errors.append("placement.mode")
    anchors = placement.get("anchors")
    consumers = placement.get("consumers")
    families = placement.get("affected_families")
    anchors_valid = (
        isinstance(anchors, list)
        and bool(anchors)
        and all(
            isinstance(anchor, dict)
            and bool(anchor.get("path"))
            and bool(anchor.get("contains"))
            for anchor in anchors
        )
    )
    if not anchors_valid:
        errors.append("placement.anchors")
    consumers_valid = (
        isinstance(consumers, list)
        and bool(consumers)
        and all(isinstance(consumer, str) and bool(consumer) for consumer in consumers)
    )
    if not consumers_valid:
        errors.append("placement.consumers")
    if (
        expected_consumers is not None
        and consumers_valid
        and set(consumers) != expected_consumers
    ):
        errors.append("placement.consumer-census")
    families_valid = (
        isinstance(families, list)
        and bool(families)
        and all(isinstance(family, dict) for family in families)
    )
    covered = (
        [
            consumer
            for family in families
            for consumer in (
                family.get("consumers", [])
                if isinstance(family.get("consumers"), list)
                else []
            )
        ]
        if families_valid
        else []
    )
    covered_strings = all(isinstance(consumer, str) for consumer in covered)
    if (
        not consumers_valid
        or not families_valid
        or not covered_strings
        or set(covered) != set(consumers)
        or len(covered) != len(set(covered))
    ):
        errors.append("placement.family-census")
    if not families_valid or any(not family.get("case_id") for family in families):
        errors.append("placement.affected-families.case-id")
    return errors


def _planned_execution(record: dict[str, Any]) -> dict[str, Any]:
    evaluation = record["evaluation"]
    return {
        "cases": evaluation["cases"],
        "rubric": evaluation["rubric"],
        "grader": evaluation["grader"],
        **evaluation["execution"],
    }


def _comparison_mismatches(record: dict[str, Any]) -> list[str]:
    planned = _planned_execution(record)
    return [
        f"{arm}.{axis}"
        for arm in ("baseline", "candidate")
        for axis in COMPARISON_AXES
        if record["arms"][arm]["execution"][axis] != planned[axis]
    ]


def _contains_not_run(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_not_run(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_not_run(child) for child in value)
    return value == "NOT RUN"


def _unresolved_value_paths(value: Any, prefix: str = "") -> list[str]:
    unresolved: list[str] = []
    if isinstance(value, dict):
        if not value:
            unresolved.append(prefix)
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else key
            unresolved.extend(_unresolved_value_paths(child, child_path))
    elif isinstance(value, list):
        if (
            not value
            and prefix
            not in {
                "source.accepted_intent_ids",
                "source.observed_failure_ids",
                "arms.baseline.expected_signal_hits",
            }
            and not prefix.endswith(".false_positives")
            and not prefix.startswith(
                (
                    "evaluation.execution.configuration.",
                    "arms.baseline.execution.configuration.",
                    "arms.candidate.execution.configuration.",
                )
            )
        ):
            unresolved.append(prefix)
        for index, child in enumerate(value):
            unresolved.extend(_unresolved_value_paths(child, f"{prefix}.{index}"))
    elif value is None or (
        isinstance(value, str)
        and (
            not value.strip()
            or value == "YYYY-MM-DD"
            or re.search(r"<[^>]+>", value) is not None
        )
    ):
        unresolved.append(prefix)
    return unresolved


def _field_shape_errors(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if record["schema"] != SCHEMA:
        errors.append("schema")
    if not isinstance(record["record_id"], str) or not record["record_id"]:
        errors.append("record_id")
    if record["status"] not in STATUS_VALUES:
        errors.append("status")
    try:
        date.fromisoformat(record["date"])
    except (TypeError, ValueError):
        errors.append("date")

    source = record["source"]
    for name in ("accepted_intent_ids", "observed_failure_ids"):
        value = source[name]
        if not isinstance(value, list) or not all(
            isinstance(item, str) and bool(item) for item in value
        ):
            errors.append(f"source.{name}")

    placement = record["placement"]
    if not _is_normalized_repo_path(placement["target"]):
        errors.append("placement.target")
    anchors = placement["anchors"]
    if not (
        isinstance(anchors, list)
        and bool(anchors)
        and all(
            isinstance(anchor, dict)
            and _is_normalized_repo_path(anchor.get("path"))
            and isinstance(anchor.get("contains"), str)
            and bool(anchor["contains"])
            for anchor in anchors
        )
    ):
        errors.append("placement.anchors")
    consumers = placement["consumers"]
    if not (
        isinstance(consumers, list)
        and bool(consumers)
        and all(_is_normalized_repo_path(consumer) for consumer in consumers)
    ):
        errors.append("placement.consumers")
    families = placement["affected_families"]
    if not (
        isinstance(families, list)
        and bool(families)
        and all(
            isinstance(family, dict)
            and isinstance(family.get("id"), str)
            and bool(family["id"])
            and isinstance(family.get("case_id"), str)
            and bool(family["case_id"])
            and isinstance(family.get("consumers"), list)
            and bool(family["consumers"])
            and all(
                _is_normalized_repo_path(consumer) for consumer in family["consumers"]
            )
            for family in families
        )
    ):
        errors.append("placement.affected_families")

    for name in ("repo_revision", "runtime_revision"):
        value = record["baseline"][name]
        if not isinstance(value, str) or not value:
            errors.append(f"baseline.{name}")
    for dotted_path in SHA256_PATHS:
        value = _lookup(record, dotted_path)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            errors.append(dotted_path)
    prompt_version = record["baseline"]["prompt_version"]
    if prompt_version != "not-applicable" and (
        not isinstance(prompt_version, str)
        or re.fullmatch(r"[0-9a-f]{16}", prompt_version) is None
    ):
        errors.append("baseline.prompt_version")
    if not _is_normalized_repo_path(record["candidate"]["path"]):
        errors.append("candidate.path")

    evaluation = record["evaluation"]
    if not isinstance(evaluation["evidence_sufficient"], bool):
        errors.append("evaluation.evidence_sufficient")
    for name in ("cases", "expected_signals"):
        value = evaluation[name]
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ):
            errors.append(f"evaluation.{name}")

    for execution_path, execution in (
        ("evaluation", _planned_execution(record)),
        ("arms.baseline.execution", record["arms"]["baseline"]["execution"]),
        ("arms.candidate.execution", record["arms"]["candidate"]["execution"]),
    ):
        cases = execution["cases"]
        if (
            not isinstance(cases, list)
            or not cases
            or not all(isinstance(item, str) and item for item in cases)
        ):
            errors.append(f"{execution_path}.cases")
        for identity in ("rubric", "grader"):
            value = execution[identity]
            if not (
                isinstance(value, dict)
                and isinstance(value.get("id"), str)
                and value["id"]
                and isinstance(value.get("version"), str)
                and value["version"]
            ):
                errors.append(f"{execution_path}.{identity}")
        for axis in (
            "gate",
            "cli",
            "model",
            "reasoning_effort",
            "sandbox",
            "runtime_revision",
        ):
            value = execution[axis]
            if not isinstance(value, str) or not value:
                errors.append(f"{execution_path}.{axis}")
        cap = execution["iteration_cap"]
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            errors.append(f"{execution_path}.iteration_cap")
        configuration = execution["configuration"]
        if not isinstance(configuration, dict) or not configuration:
            errors.append(f"{execution_path}.configuration")

    for arm in ("baseline", "candidate"):
        results = record["arms"][arm]
        if results["output_contract"] not in ("PASS", "FAIL"):
            errors.append(f"arms.{arm}.output_contract")
        iterations = results["iterations"]
        if (
            not isinstance(iterations, int)
            or isinstance(iterations, bool)
            or iterations < 1
        ):
            errors.append(f"arms.{arm}.iterations")
        for metric in ("cost", "latency"):
            value = results[metric]
            metric_value = value.get("value") if isinstance(value, dict) else None
            if not (
                isinstance(metric_value, (int, float))
                and not isinstance(metric_value, bool)
                and metric_value >= 0
                and isinstance(value.get("unit"), str)
                and value["unit"]
            ):
                errors.append(f"arms.{arm}.{metric}")
        for result_list in ("expected_signal_hits", "false_positives"):
            value = results[result_list]
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                errors.append(f"arms.{arm}.{result_list}")

    disposition = record["disposition"]
    if disposition["attribution"] not in ATTRIBUTION_VALUES:
        errors.append("disposition.attribution")
    if disposition["promotion"] not in PROMOTION_VALUES:
        errors.append("disposition.promotion")
    for name in ("authority", "target", "reason"):
        value = disposition[name]
        if not isinstance(value, str) or not value:
            errors.append(f"disposition.{name}")
    return errors


def _is_normalized_repo_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == value
        and value not in {".", ""}
    )


def _artifact_binding_errors(
    record: dict[str, Any],
    *,
    workspace_root: Path,
) -> list[str]:
    errors: list[str] = []
    candidate_path = record["candidate"]["path"]
    if _is_normalized_repo_path(candidate_path):
        candidate = _contained_file(workspace_root, candidate_path)
        if candidate is None:
            errors.append("candidate.path")
        else:
            try:
                candidate_bytes = candidate.read_bytes()
            except OSError:
                errors.append("candidate.path")
            else:
                try:
                    candidate_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    errors.append("candidate.utf8")
                if (
                    hashlib.sha256(candidate_bytes).hexdigest()
                    != record["candidate"]["sha256"]
                ):
                    errors.append("candidate.sha256")

    anchors = record["placement"]["anchors"]
    if isinstance(anchors, list):
        for anchor in anchors:
            if not isinstance(anchor, dict):
                errors.append("placement.anchors")
                continue
            path = anchor.get("path")
            contains = anchor.get("contains")
            if not _is_normalized_repo_path(path) or not isinstance(contains, str):
                errors.append("placement.anchors")
                continue
            live_path = _contained_file(workspace_root, path)
            try:
                anchor_matches = (
                    live_path is not None
                    and contains in live_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError):
                anchor_matches = False
            if not anchor_matches:
                errors.append("placement.anchors")
    return list(dict.fromkeys(errors))


def _contained_file(workspace_root: Path, relative: str) -> Path | None:
    root = workspace_root.resolve()
    try:
        candidate = (root / relative).resolve(strict=True)
    except OSError:
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _promotion_errors(
    record: dict[str, Any],
    *,
    workspace_root: Path = REPO_ROOT,
) -> list[str]:
    errors = _record_errors(record)
    if errors:
        structural = [f"record.{error}" for error in errors]
        if _contains_not_run(record):
            structural.append("record.not-run")
        return structural
    errors.extend(f"unresolved.{path}" for path in _unresolved_value_paths(record))
    errors.extend(_field_shape_errors(record))
    errors.extend(_classification_placement_errors(record))
    errors.extend(_artifact_binding_errors(record, workspace_root=workspace_root))
    errors.extend(f"comparison.{item}" for item in _comparison_mismatches(record))
    if record["status"] not in ("evaluated", "ready-for-promotion"):
        errors.append("status.not-evaluated")
    if _contains_not_run(record):
        errors.append("record.not-run")
    if record["evaluation"]["evidence_sufficient"] is not True:
        errors.append("evaluation.evidence_sufficient")
    if record["disposition"]["attribution"] != "prompt":
        errors.append("disposition.attribution")
    promotion = record["disposition"]["promotion"]
    intent = record["classification"]["intent"]
    if promotion not in ("PROMOTE", "REMOVE"):
        errors.append("disposition.promotion")
    if (promotion == "REMOVE") != (intent == "removal"):
        errors.append("disposition.intent-promotion")
    return errors


def _promotion_eligible(
    record: dict[str, Any],
    *,
    workspace_root: Path = REPO_ROOT,
) -> bool:
    return not _promotion_errors(record, workspace_root=workspace_root)


def _different(value: Any) -> Any:
    if isinstance(value, list):
        return [*value, "<different>"]
    if isinstance(value, dict):
        return {**value, "version": "<different>"}
    return f"{value}-different"


def _materialize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _materialize(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_materialize(child) for child in value]
    if isinstance(value, str):
        if value == "YYYY-MM-DD":
            return "2026-09-02"
        return re.sub(r"<[^>]+>", "resolved", value)
    return value


def _resolved_record() -> dict[str, Any]:
    record = _materialize(_yaml_record(_required_text(PROTOCOL_PATH, ac="AC-3")))
    record.update(
        record_id="prompt-refinement-evaluated-case",
        status="evaluated",
        date="2026-09-02",
    )
    record["source"] = {
        "accepted_intent_ids": ["OD-3"],
        "observed_failure_ids": [],
    }
    record["placement"] = {
        "default": "local",
        "mode": "local",
        "target": "heddle/resources/prompts/peer-review-sequential.md",
        "anchors": [
            {
                "path": "heddle/resources/prompts/peer-review-sequential.md",
                "contains": "Do not narrate your review process",
            }
        ],
        "consumers": ["heddle/resources/prompts/peer-review-sequential.md"],
        "affected_families": [
            {
                "id": "example-family",
                "case_id": "case-1",
                "consumers": ["heddle/resources/prompts/peer-review-sequential.md"],
            }
        ],
    }
    record["baseline"] = {
        "repo_revision": "a" * 40,
        "runtime_revision": "b" * 40,
        "prompt_version": "0123456789abcdef",
        "effective_prompt_sha256": "1" * 64,
    }
    record["candidate"] = {
        "path": "tests/fixtures/prompt-refinement-candidate.md",
        "sha256": hashlib.sha256(b"Candidate instruction bytes.\n").hexdigest(),
    }
    record["evaluation"] = {
        "evidence_sufficient": True,
        "cases": ["case-1"],
        "expected_signals": ["The required issue is identified without noise."],
        "rubric": {"id": "rubric-1", "version": "v1"},
        "grader": {"id": "grader-1", "version": "v1"},
        "execution": {
            "gate": "spec-review",
            "cli": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "xhigh",
            "sandbox": "read-only",
            "runtime_revision": "b" * 40,
            "iteration_cap": 4,
            "configuration": {"tool_permissions": ["Read", "Grep"]},
        },
    }
    planned_execution = _planned_execution(record)
    record["arms"] = {
        "baseline": {
            "execution": deepcopy(planned_execution),
            "effective_prompt_sha256": "1" * 64,
            "review_basis_hash": "3" * 64,
            "input_hash": "4" * 64,
            "expected_signal_hits": ["signal-1"],
            "false_positives": [],
            "output_contract": "PASS",
            "iterations": 2,
            "cost": {"value": 0.01, "unit": "USD"},
            "latency": {"value": 1.2, "unit": "seconds"},
        },
        "candidate": {
            "execution": deepcopy(planned_execution),
            "effective_prompt_sha256": "5" * 64,
            "review_basis_hash": "6" * 64,
            "input_hash": "7" * 64,
            "expected_signal_hits": ["signal-1"],
            "false_positives": [],
            "output_contract": "PASS",
            "iterations": 1,
            "cost": {"value": 0.01, "unit": "USD"},
            "latency": {"value": 1.1, "unit": "seconds"},
        },
    }
    record["disposition"].update(
        attribution="prompt",
        promotion="PROMOTE",
        authority="accepted-program-decision",
        target="heddle/resources/prompts/peer-review-sequential.md",
        reason="Controlled comparison supports the candidate.",
    )
    return record


def _materialized_record(workspace_root: Path) -> dict[str, Any]:
    record = _resolved_record()
    target_relative = Path("live/prompts/example.md")
    candidate_relative = Path("evidence/candidate.md")
    target = workspace_root / target_relative
    candidate = workspace_root / candidate_relative
    target.parent.mkdir(parents=True)
    candidate.parent.mkdir(parents=True)
    target.write_text("Header\n\nCurrent instruction bytes.\n", encoding="utf-8")
    candidate.write_text("Candidate instruction bytes.\n", encoding="utf-8")
    record["placement"] = {
        "default": "local",
        "mode": "local",
        "target": target_relative.as_posix(),
        "anchors": [
            {
                "path": target_relative.as_posix(),
                "contains": "Current instruction bytes.",
            }
        ],
        "consumers": [target_relative.as_posix()],
        "affected_families": [
            {
                "id": "example-family",
                "case_id": "case-1",
                "consumers": [target_relative.as_posix()],
            }
        ],
    }
    record["candidate"] = {
        "path": candidate_relative.as_posix(),
        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
    }
    record["disposition"]["target"] = target_relative.as_posix()
    return record


def _set_leaf(record: Any, dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    parent = record
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    last = parts[-1]
    if isinstance(parent, list):
        parent[int(last)] = value
    else:
        parent[last] = value


def _string_leaf_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{prefix}.{key}" if prefix else key
            paths.extend(_string_leaf_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_string_leaf_paths(child, f"{prefix}.{index}"))
    elif isinstance(value, str):
        paths.append(prefix)
    return paths


def test_survivor_packaged_standards_remains_package_relative() -> None:
    """AC-6 survivor pin: the installed contract remains package-owned."""
    package_root = Path(heddle.__file__).resolve().parent
    assert PACKAGED_STANDARDS_DOC.is_file()
    assert PACKAGED_STANDARDS_DOC.is_relative_to(package_root)


def test_ac1_protocol_has_the_complete_ordered_workflow() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-1")
    headings = [
        "When to use",
        "Record contract",
        "Classify and place the candidate",
        "Run a controlled comparison",
        "Attribute the result",
        "Promotion decision",
        "Model-generation audit",
        "Record template",
    ]
    positions = []
    for heading in headings:
        section = _required_h2(text, heading, ac="AC-1")
        positions.append(text.index(section))
    assert positions == sorted(positions), (
        "FAIL AC-1: workflow sections are out of order"
    )


def test_ac1_record_template_rejects_every_missing_mandatory_field() -> None:
    record = _yaml_record(_required_text(PROTOCOL_PATH, ac="AC-1"))
    assert not _record_errors(record), (
        f"FAIL AC-1: intact record is incomplete: {_record_errors(record)}"
    )

    undetected: list[str] = []
    for dotted_path in RECORD_PATHS:
        mutated = deepcopy(record)
        _drop(mutated, dotted_path)
        if dotted_path not in _record_errors(mutated):
            undetected.append(dotted_path)
    assert not undetected, (
        f"FAIL AC-1: one-field destructive mutations escaped: {undetected}"
    )


def test_ac1_record_requires_an_accepted_intent_or_observed_failure() -> None:
    record = _yaml_record(_required_text(PROTOCOL_PATH, ac="AC-1"))
    record["source"]["accepted_intent_ids"] = []
    record["source"]["observed_failure_ids"] = []
    assert "source.accepted_intent_ids|observed_failure_ids" in _record_errors(record)


def test_ac1_pre_edit_check_fails_closed_before_live_bytes_change() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-1")
    section = _required_h2(text, "Record contract", ac="AC-1")
    _assert_required_terms(
        section,
        (
            "before changing live bytes",
            "recompute the candidate sha-256",
            "re-read every exact anchor",
            "missing mandatory key",
            "set promotion to `withheld`",
            "outside live resolver roots",
        ),
        failure_prefix="FAIL AC-1: pre-edit validity check missing",
    )


def test_ac1_ac3_boolean_cannot_promote_the_unresolved_record_template() -> None:
    record = _yaml_record(_required_text(PROTOCOL_PATH, ac="AC-1"))
    record["status"] = "evaluated"
    record["evaluation"]["evidence_sufficient"] = True
    record["disposition"].update(
        attribution="prompt",
        promotion="PROMOTE",
        authority="accepted-program-decision",
        target="heddle/resources/prompts/example.md",
        reason="Declared sufficient without resolved evidence.",
    )
    errors = _promotion_errors(record)
    assert any(error.startswith("unresolved.") for error in errors)
    assert not _promotion_eligible(record)


def test_ac1_ac3_every_resolved_string_leaf_rejects_blank_promotion_data() -> None:
    record = _resolved_record()
    assert not _unresolved_value_paths(record)
    assert _promotion_eligible(record)

    undetected: list[str] = []
    for dotted_path in _string_leaf_paths(record):
        mutated = deepcopy(record)
        _set_leaf(mutated, dotted_path, "")
        if f"unresolved.{dotted_path}" not in _promotion_errors(mutated):
            undetected.append(dotted_path)
    assert not undetected, (
        f"FAIL AC-1/AC-3: blank promotion values escaped: {undetected}"
    )


def test_ac1_ac3_planning_status_cannot_authorize_promotion() -> None:
    record = _resolved_record()
    record["status"] = "planning"
    assert "status.not-evaluated" in _promotion_errors(record)
    assert not _promotion_eligible(record)


def test_ac1_ac3_completed_baseline_can_have_zero_expected_signal_hits() -> None:
    record = _resolved_record()
    record["arms"]["baseline"]["expected_signal_hits"] = []
    record["arms"]["baseline"]["false_positives"] = ["unacceptable-input-expansion"]
    assert _promotion_eligible(record)
    record["arms"]["candidate"]["expected_signal_hits"] = []
    assert "unresolved.arms.candidate.expected_signal_hits" in _promotion_errors(record)
    record["arms"]["baseline"]["expected_signal_hits"] = "NOT RUN"
    assert "record.not-run" in _promotion_errors(record)


def test_ac1_ac3_resolved_empty_configuration_lists_preserve_axis_guards() -> None:
    record = _resolved_record()
    executions = (
        record["evaluation"]["execution"],
        record["arms"]["baseline"]["execution"],
        record["arms"]["candidate"]["execution"],
    )
    for execution in executions:
        execution["configuration"]["context_builders"] = []
        execution["configuration"]["runtime"] = {"excluded_paths": []}
    assert _promotion_eligible(record)

    record["arms"]["candidate"]["execution"]["configuration"]["context_builders"] = [
        "extra-builder"
    ]
    assert "comparison.candidate.configuration" in _promotion_errors(record)
    for execution in executions:
        execution["configuration"]["context_builders"] = []
    for unresolved in (None, "", "NOT RUN"):
        for execution in executions:
            execution["configuration"]["runtime"]["excluded_paths"] = unresolved
        assert not _promotion_eligible(record)


def test_ac1_ac3_positive_promotion_fixture_uses_field_valid_evidence(
    tmp_path: Path,
) -> None:
    record = _materialized_record(tmp_path)
    assert not _unresolved_value_paths(record)
    assert not _field_shape_errors(record)
    assert not _artifact_binding_errors(record, workspace_root=tmp_path)
    assert not _contains_not_run(record)
    assert _promotion_eligible(record, workspace_root=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("stale-candidate-hash", "candidate.sha256"),
        ("missing-candidate", "candidate.path"),
        ("non-utf8-candidate", "candidate.utf8"),
        ("escaping-candidate", "candidate.path"),
        ("missing-anchor", "placement.anchors"),
        ("drifted-anchor", "placement.anchors"),
        ("escaping-anchor", "placement.anchors"),
    ),
)
def test_ac1_ac3_artifact_binding_fails_closed(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    record = _materialized_record(workspace)
    if mutation == "stale-candidate-hash":
        record["candidate"]["sha256"] = "0" * 64
    elif mutation == "missing-candidate":
        record["candidate"]["path"] = "evidence/missing.md"
    elif mutation == "non-utf8-candidate":
        candidate = workspace / record["candidate"]["path"]
        candidate.write_bytes(b"\xff\xfe")
        record["candidate"]["sha256"] = hashlib.sha256(
            candidate.read_bytes()
        ).hexdigest()
    elif mutation == "escaping-candidate":
        candidate = workspace / record["candidate"]["path"]
        outside = tmp_path / "outside-candidate.md"
        outside.write_text("Candidate instruction bytes.\n", encoding="utf-8")
        candidate.unlink()
        candidate.symlink_to(outside)
    elif mutation == "missing-anchor":
        record["placement"]["anchors"][0]["path"] = "live/prompts/missing.md"
    elif mutation == "drifted-anchor":
        record["placement"]["anchors"][0]["contains"] = "Drifted bytes."
    else:
        anchor = workspace / record["placement"]["anchors"][0]["path"]
        outside = tmp_path / "outside-anchor.md"
        outside.write_text("Current instruction bytes.\n", encoding="utf-8")
        anchor.unlink()
        anchor.symlink_to(outside)

    errors = _artifact_binding_errors(record, workspace_root=workspace)
    assert expected_error in errors
    assert not _promotion_eligible(record, workspace_root=workspace)


def test_ac1_ac3_every_sha256_identity_rejects_malformed_bytes() -> None:
    record = _resolved_record()
    undetected: list[str] = []
    for dotted_path in SHA256_PATHS:
        mutated = deepcopy(record)
        _set_leaf(mutated, dotted_path, "not-a-sha256")
        if dotted_path not in _field_shape_errors(mutated):
            undetected.append(dotted_path)
    assert not undetected, (
        f"FAIL AC-1/AC-3: malformed SHA-256 identities escaped: {undetected}"
    )


@pytest.mark.parametrize(
    ("dotted_path", "invalid", "expected_error"),
    (
        ("date", "September 2", "date"),
        ("baseline.prompt_version", "xyz", "baseline.prompt_version"),
        ("evaluation.cases", "case-1", "evaluation.cases"),
        ("evaluation.rubric", "rubric-1", "evaluation.rubric"),
        ("evaluation.grader", {}, "evaluation.grader"),
        (
            "evaluation.execution.iteration_cap",
            0,
            "evaluation.iteration_cap",
        ),
        (
            "evaluation.execution.configuration",
            [],
            "evaluation.configuration",
        ),
        ("arms.baseline.output_contract", "MAYBE", "arms.baseline.output_contract"),
        ("arms.candidate.iterations", 0, "arms.candidate.iterations"),
        ("arms.baseline.cost", "unknown", "arms.baseline.cost"),
        (
            "arms.candidate.latency",
            {"value": -1, "unit": "seconds"},
            "arms.candidate.latency",
        ),
        (
            "arms.baseline.expected_signal_hits",
            "signal-1",
            "arms.baseline.expected_signal_hits",
        ),
        (
            "arms.candidate.false_positives",
            "none",
            "arms.candidate.false_positives",
        ),
    ),
)
def test_ac1_ac3_malformed_typed_evidence_cannot_promote(
    dotted_path: str,
    invalid: Any,
    expected_error: str,
) -> None:
    record = _resolved_record()
    _set_leaf(record, dotted_path, invalid)
    assert expected_error in _field_shape_errors(record)
    assert not _promotion_eligible(record)


@pytest.mark.parametrize(
    ("dotted_path", "invalid", "expected_error"),
    (
        ("schema", "wrong-schema", "schema"),
        ("record_id", 7, "record_id"),
        ("status", "unknown", "status"),
        ("status", 7, "status"),
        ("source.accepted_intent_ids", [7], "source.accepted_intent_ids"),
        ("source.observed_failure_ids", "failure-1", "source.observed_failure_ids"),
        ("placement.target", "./live/prompt.md", "placement.target"),
        ("placement.target", 7, "placement.target"),
        ("placement.anchors", [7], "placement.anchors"),
        ("placement.anchors.0.path", 7, "placement.anchors"),
        ("placement.anchors.0.contains", 7, "placement.anchors"),
        ("placement.consumers", [7], "placement.consumers"),
        (
            "placement.affected_families.0.id",
            7,
            "placement.affected_families",
        ),
        (
            "placement.affected_families.0.case_id",
            7,
            "placement.affected_families",
        ),
        (
            "placement.affected_families.0.consumers",
            [7],
            "placement.affected_families",
        ),
        ("baseline.repo_revision", 7, "baseline.repo_revision"),
        ("baseline.runtime_revision", 7, "baseline.runtime_revision"),
        ("candidate.path", "../candidate.md", "candidate.path"),
        ("disposition.attribution", "unknown", "disposition.attribution"),
        ("disposition.attribution", 11, "disposition.attribution"),
        ("disposition.promotion", "UNKNOWN", "disposition.promotion"),
        ("disposition.promotion", 11, "disposition.promotion"),
        ("disposition.authority", 11, "disposition.authority"),
        ("disposition.target", 11, "disposition.target"),
        ("disposition.reason", 11, "disposition.reason"),
    ),
)
def test_ac1_ac3_every_mandatory_value_has_a_typed_domain_guard(
    dotted_path: str,
    invalid: Any,
    expected_error: str,
) -> None:
    record = _resolved_record()
    _set_leaf(record, dotted_path, invalid)
    assert expected_error in _field_shape_errors(record)
    assert not _promotion_eligible(record)


def test_ac1_ac3_missing_family_id_cannot_promote() -> None:
    record = _resolved_record()
    del record["placement"]["affected_families"][0]["id"]
    assert "placement.affected_families" in _field_shape_errors(record)
    assert not _promotion_eligible(record)


def test_ac2_role_and_intent_vocabulary_is_exact_and_removal_is_equal() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-2")
    section = _required_h2(text, "Classify and place the candidate", ac="AC-2")
    assert _backticked_values(section, "Role values:", ac="AC-2") == ROLE_VALUES
    assert _backticked_values(section, "Intent values:", ac="AC-2") == INTENT_VALUES
    assert "removal has equal standing with addition" in section.casefold()


def test_ac2_local_is_default_and_shared_requires_complete_evidence() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-2")
    section = _required_h2(text, "Classify and place the candidate", ac="AC-2")
    _assert_required_terms(
        section,
        (
            "local placement is the default",
            "exact target",
            "exact anchors",
            "every consumer",
            "materially affected family",
            "fixed compatibility case",
        ),
        failure_prefix="FAIL AC-2: placement contract missing",
    )


@pytest.mark.parametrize(
    ("field", "allowed"),
    (("role", ROLE_VALUES), ("intent", INTENT_VALUES)),
)
def test_ac2_classification_fixture_rejects_missing_or_unknown_values(
    field: str,
    allowed: tuple[str, ...],
) -> None:
    record = _yaml_record(_required_text(PROTOCOL_PATH, ac="AC-2"))
    for value in allowed:
        record["classification"][field] = value
        assert f"classification.{field}" not in _classification_placement_errors(record)

    del record["classification"][field]
    assert f"classification.{field}" in _classification_placement_errors(record)
    record["classification"][field] = "<unknown>"
    assert f"classification.{field}" in _classification_placement_errors(record)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("anchor", "placement.anchors"),
        ("consumer-census", "placement.consumer-census"),
        ("family-census", "placement.family-census"),
        ("family-case", "placement.affected-families.case-id"),
    ),
)
def test_ac2_shared_placement_rejects_each_incomplete_evidence_class(
    mutation: str,
    expected_error: str,
) -> None:
    record = _yaml_record(_required_text(PROTOCOL_PATH, ac="AC-2"))
    record["placement"]["mode"] = "shared"
    expected_consumers = {
        "heddle/resources/prompts/gate-a.md",
        "heddle/resources/prompts/gate-b.md",
    }
    record["placement"]["anchors"] = [
        {
            "path": "heddle/resources/prompts/_partials/shared-rule.md",
            "contains": "<verbatim-shared-anchor>",
        }
    ]
    record["placement"]["consumers"] = sorted(expected_consumers)
    record["placement"]["affected_families"] = [
        {
            "id": "family-a",
            "case_id": "case-a",
            "consumers": ["heddle/resources/prompts/gate-a.md"],
        },
        {
            "id": "family-b",
            "case_id": "case-b",
            "consumers": ["heddle/resources/prompts/gate-b.md"],
        },
    ]
    assert not _classification_placement_errors(
        record, expected_consumers=expected_consumers
    )

    if mutation == "anchor":
        record["placement"]["anchors"] = []
    elif mutation == "consumer-census":
        record["placement"]["consumers"].pop()
        record["placement"]["affected_families"].pop()
    elif mutation == "family-census":
        record["placement"]["affected_families"].pop()
    else:
        del record["placement"]["affected_families"][0]["case_id"]

    errors = _classification_placement_errors(
        record, expected_consumers=expected_consumers
    )
    assert expected_error in errors, (
        f"FAIL AC-2: {mutation} omission did not make shared placement ineligible"
    )


def test_ac3_comparison_freezes_every_axis_and_changes_only_candidate_bytes() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-3")
    section = _required_h2(text, "Run a controlled comparison", ac="AC-3")
    _assert_required_terms(
        section,
        (
            "fresh, isolated arms",
            "same cases",
            "same rubric",
            "same grader",
            "same cli",
            "same model",
            "same reasoning effort",
            "same sandbox",
            "same runtime revision",
            "same iteration cap",
            "same configuration",
            "only the candidate bytes differ",
            "raw finding counts are insufficient",
        ),
        failure_prefix="FAIL AC-3: controlled-comparison contract missing",
    )


@pytest.mark.parametrize("axis", COMPARISON_AXES)
def test_ac3_each_axis_mismatch_invalidates_comparison_and_withholds(
    axis: str,
) -> None:
    record = _resolved_record()
    record["evaluation"]["evidence_sufficient"] = True
    record["disposition"].update(
        attribution="prompt",
        promotion="PROMOTE",
        authority="accepted-program-decision",
        target="heddle/resources/prompts/example.md",
    )
    assert not _comparison_mismatches(record)
    assert _promotion_eligible(record)

    baseline_value = record["arms"]["baseline"]["execution"][axis]
    record["arms"]["candidate"]["execution"][axis] = _different(baseline_value)
    record["disposition"].update(
        attribution="invalid-comparison",
        promotion="WITHHELD",
    )

    assert _comparison_mismatches(record) == [f"candidate.{axis}"]
    assert record["disposition"]["attribution"] == "invalid-comparison"
    assert record["disposition"]["promotion"] == "WITHHELD"
    assert not _promotion_eligible(record)


@pytest.mark.parametrize("axis", COMPARISON_AXES)
def test_ac3_synchronized_axis_drift_still_invalidates_both_arms(axis: str) -> None:
    record = _resolved_record()
    record["evaluation"]["evidence_sufficient"] = True
    record["disposition"].update(
        attribution="prompt",
        promotion="PROMOTE",
        authority="accepted-program-decision",
        target="heddle/resources/prompts/example.md",
    )
    planned_value = _planned_execution(record)[axis]
    drifted = _different(planned_value)
    for arm in ("baseline", "candidate"):
        record["arms"][arm]["execution"][axis] = deepcopy(drifted)

    assert _comparison_mismatches(record) == [f"baseline.{axis}", f"candidate.{axis}"]
    assert not _promotion_eligible(record)


@pytest.mark.parametrize("missing", ("evidence", "authority", "target"))
@pytest.mark.parametrize(("intent", "promotion"), LIVE_EDIT_OUTCOMES)
def test_ac3_live_edit_requires_evidence_authority_and_target(
    missing: str,
    intent: str,
    promotion: str,
) -> None:
    record = _resolved_record()
    record["classification"]["intent"] = intent
    record["evaluation"]["evidence_sufficient"] = True
    record["disposition"].update(
        attribution="prompt",
        promotion=promotion,
        authority="accepted-program-decision",
        target="heddle/resources/prompts/example.md",
    )
    assert _promotion_eligible(record)

    if missing == "evidence":
        record["evaluation"]["evidence_sufficient"] = False
        expected_error = "evaluation.evidence_sufficient"
    else:
        record["disposition"][missing] = ""
        expected_error = f"disposition.{missing}"

    assert expected_error in _promotion_errors(record)
    assert not _promotion_eligible(record)
    record["disposition"]["promotion"] = "WITHHELD"
    assert record["disposition"]["promotion"] == "WITHHELD"


@pytest.mark.parametrize(("intent", "promotion"), LIVE_EDIT_OUTCOMES)
def test_ac3_not_run_attribution_or_arm_results_cannot_authorize_live_edit(
    intent: str,
    promotion: str,
) -> None:
    record = _resolved_record()
    record["classification"]["intent"] = intent
    record["evaluation"]["evidence_sufficient"] = True
    record["disposition"].update(
        attribution="prompt",
        promotion=promotion,
        authority="accepted-program-decision",
        target="heddle/resources/prompts/example.md",
    )
    assert _promotion_eligible(record)

    record["disposition"]["attribution"] = "not-run"
    assert "disposition.attribution" in _promotion_errors(record)
    assert not _promotion_eligible(record)

    record["disposition"]["attribution"] = "prompt"
    record["arms"]["baseline"]["output_contract"] = "NOT RUN"
    assert "record.not-run" in _promotion_errors(record)
    assert not _promotion_eligible(record)

    record["arms"]["baseline"]["output_contract"] = "<pass-or-fail>"
    for execution in (
        record["evaluation"]["execution"],
        record["arms"]["baseline"]["execution"],
        record["arms"]["candidate"]["execution"],
    ):
        execution["gate"] = "NOT RUN"
    assert "record.not-run" in _promotion_errors(record)
    assert not _promotion_eligible(record)


@pytest.mark.parametrize(("intent", "promotion"), LIVE_EDIT_OUTCOMES)
def test_ac1_ac3_not_run_is_rejected_at_every_live_edit_evidence_field(
    intent: str,
    promotion: str,
) -> None:
    record = _resolved_record()
    record["classification"]["intent"] = intent
    record["disposition"]["promotion"] = promotion
    assert _promotion_eligible(record)

    undetected: list[str] = []
    for dotted_path in PROMOTION_EVIDENCE_PATHS:
        mutated = deepcopy(record)
        _set_leaf(mutated, dotted_path, "NOT RUN")
        if "record.not-run" not in _promotion_errors(mutated):
            undetected.append(dotted_path)
    assert not undetected, (
        f"FAIL AC-1/AC-3: NOT RUN evidence escaped promotion guard: {undetected}"
    )


def test_ac3_evidence_backed_remove_is_eligible(tmp_path: Path) -> None:
    record = _materialized_record(tmp_path)
    record["classification"]["intent"] = "removal"
    record["disposition"]["promotion"] = "REMOVE"
    assert _promotion_eligible(record, workspace_root=tmp_path)


@pytest.mark.parametrize(
    ("intent", "promotion"),
    (("removal", "PROMOTE"), ("addition", "REMOVE")),
)
def test_ac3_live_disposition_must_match_change_intent(
    intent: str,
    promotion: str,
) -> None:
    record = _resolved_record()
    record["classification"]["intent"] = intent
    record["disposition"]["promotion"] = promotion
    assert "disposition.intent-promotion" in _promotion_errors(record)
    assert not _promotion_eligible(record)


def test_ac3_attribution_and_promotion_fail_closed() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-3")
    attribution = _required_h2(text, "Attribute the result", ac="AC-3")
    assert (
        _backticked_values(attribution, "Attribution values:", ac="AC-3")
        == ATTRIBUTION_VALUES
    )

    promotion = _required_h2(text, "Promotion decision", ac="AC-3")
    _assert_required_terms(
        promotion,
        (
            "named authority",
            "named target",
            "sufficient evidence",
            "no change",
            "withheld",
            "revise",
            "remove",
            "retain inert",
        ),
        failure_prefix="FAIL AC-3: fail-closed disposition contract missing",
    )


def test_ac7_protocol_stays_generic_and_non_authorizing() -> None:
    text = _required_text(PROTOCOL_PATH, ac="AC-7")
    forbidden = (
        "config/prompts/fev_agentic.yaml",
        "Langfuse",
        "[LIB]",
        "NVDA",
        "node×corpus",
    )
    offenders = [term for term in forbidden if term.casefold() in text.casefold()]
    assert not offenders, f"FAIL AC-7: product-specific policy leaked in: {offenders}"
    assert "does not authorize a gate, skill, default, topology, cap, stop" in text
