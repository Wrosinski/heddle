"""Pre-admission research and explicit route choices, never lifecycle authority."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import yaml

from heddle.contracts import operations as ops
from heddle.contracts.feature_policy import (
    INTAKE_INPUT_FIELDS,
    INTAKE_INPUT_SCHEMA_ID,
    INTAKE_RESEARCH_FIELDS,
    INTAKE_ROUTES,
    POLICY_SCHEMA,
    ConfirmedPolicy,
)
from heddle.contracts.result import Conflict, HeddleResult, NextAction
from heddle.contracts.schemas import FLOW_MODES
from heddle.io.source import capture_path
from heddle.kernel.feature_policy import (
    effective_policy,
    parse_axes,
    parse_policy,
    recommend_policy,
)
from heddle.kernel.project_config import (
    KernelError,
    ProjectConfig,
    feature_state_path,
    load_project_config_from_cwd,
)
from heddle.kernel.source_manifest import normalize_paths
from heddle.kernel.state import parse_source_baseline
from heddle.runtime.clock import utc_now_minutes
from heddle.runtime.diagnostics import conflict_failure, write_failure
from heddle.runtime.locking import state_recording_lock
from heddle.runtime.output import emit_envelope
from heddle.runtime.state_store import replace_file_text
from heddle.runtime.write_args import parse_write_args, usage_failure

INTAKE_SCHEMA_V1 = "heddle.intake/v1"
INTAKE_SCHEMA_V2 = "heddle.intake/v2"
INTAKE_SCHEMA = INTAKE_SCHEMA_V2
INTAKE_SCHEMA_VERSIONS = (INTAKE_SCHEMA_V1, INTAKE_SCHEMA_V2)
_KEYS = {
    "schema",
    "feature",
    "area",
    "revision",
    "created",
    "updated",
    "route",
    "route_reason",
    "research",
    "axes",
    "feature_policy",
    "policy_history",
    "assessments",
    "create_manifest",
}


def invalid(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"feature intake: {message}",
        hint="inspect research with `heddle feature prepare`, then explicitly confirm "
        "the complete matrix with `heddle feature policy`; preserve conflicting files",
    )


def object_fields(value: Any, keys: tuple[str, ...] | set[str]) -> dict[str, Any]:
    expected = set(keys)
    if not isinstance(value, dict) or set(value) != expected:
        raise invalid(f"expected exactly fields {sorted(expected)}")
    return value


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise invalid(f"{label} must be nonempty trimmed text")
    return value


def validate_identity(slug: str, area: str | None = None) -> None:
    if not isinstance(slug, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug) is None:
        raise invalid("feature slug must be lowercase kebab text")
    if area is not None and (
        not isinstance(area, str)
        or area in {"", ".", ".."}
        or area.startswith("-")
        or "/" in area
        or "\\" in area
        or any(ch.isspace() or not ch.isprintable() for ch in area)
    ):
        raise invalid("area must be a plain path segment without whitespace")


def intake_path(config: ProjectConfig, slug: str) -> Path:
    validate_identity(slug)
    return config.root / ".heddle" / "intake" / f"{slug}.yaml"


def safe_target(root: Path, path: Path) -> None:
    """Refuse static symlinks in every component before reading or publishing."""
    relative = path.relative_to(root).as_posix()
    normalize_paths((relative,))
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise invalid(f"symlink cannot own admission content: {relative}")


def research_identity(root: Path, reference: str) -> str:
    normalized = normalize_paths((nonempty(reference, "research reference"),))[0]
    observed = capture_path(root, normalized)
    if observed.kind != "file":
        raise invalid(f"research is not an available regular file: {normalized}")
    return hashlib.sha256(bytes([observed.executable]) + observed.content).hexdigest()


def intake_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def policy_document(policy: ConfirmedPolicy) -> dict[str, Any]:
    return {"schema": POLICY_SCHEMA, **ops.decoded_payload(policy)}


def policy_projection(policy: ConfirmedPolicy) -> dict[str, Any]:
    return ops.decoded_payload(effective_policy(policy))


def validate_intake(document: Any) -> dict[str, Any]:
    row = object_fields(document, _KEYS)
    if row["schema"] not in INTAKE_SCHEMA_VERSIONS:
        raise invalid(f"schema must be one of {INTAKE_SCHEMA_VERSIONS}")
    validate_identity(row["feature"], nonempty(row["area"], "area"))
    if type(row["revision"]) is not int or row["revision"] < 1:
        raise invalid("revision must be a positive integer")
    if row["route"] not in ("direct", "heddle"):
        raise invalid("route must be direct or heddle")
    for field in ("created", "updated", "route_reason"):
        nonempty(row[field], field)
    research = object_fields(row["research"], {"reference", "summary", "sha256"})
    for field, value in research.items():
        nonempty(value, "research " + field)
    normalize_paths((research["reference"],))
    if re.fullmatch(r"[a-f0-9]{64}", research["sha256"]) is None:
        raise invalid("research needs a SHA-256 identity")
    parse_axes(row["axes"])
    if row["feature_policy"] is not None:
        parse_policy(row["feature_policy"])
    if not isinstance(row["policy_history"], list):
        raise invalid("policy history must be a list")
    history = [parse_policy(policy) for policy in row["policy_history"]]
    if any(
        earlier.revision >= later.revision
        for earlier, later in zip(history, history[1:], strict=False)
    ):
        raise invalid("policy history revisions must strictly increase")
    if (
        bool(history) != (row["feature_policy"] is not None)
        or history
        and row["policy_history"][-1] != row["feature_policy"]
    ):
        raise invalid("policy history must end at the confirmed policy")
    if row["route"] == "direct" and row["feature_policy"] is not None:
        raise invalid("direct intake cannot hold confirmed review authority")
    if not isinstance(row["assessments"], list):
        raise invalid("assessments must be a list")
    for assessment in row["assessments"]:
        object_fields(assessment, {"axes", "at"})
        parse_axes(assessment["axes"])
        nonempty(assessment["at"], "assessment time")
    manifest = row["create_manifest"]
    if manifest is not None:
        manifest_keys = (
            {"files", "flow", "source_baseline"}
            if row["schema"] == INTAKE_SCHEMA_V2
            else {"files", "flow"}
        )
        if not isinstance(manifest, dict) or set(manifest) != manifest_keys:
            raise invalid(
                f"{row['schema']} create manifest expected exactly fields "
                f"{sorted(manifest_keys)}"
            )
        if row["feature_policy"] is None:
            raise invalid("admission manifest requires a confirmed policy")
        if manifest["flow"] not in FLOW_MODES:
            raise invalid("admission flow must be hitl or auto")
        if not isinstance(manifest["files"], list) or not manifest["files"]:
            raise invalid("create manifest must be a nonempty list")
        paths = []
        for item in manifest["files"]:
            object_fields(item, {"path", "kind", "content", "sha256"})
            path = nonempty(item["path"], "manifest path")
            normalize_paths((path,))
            paths.append(path)
            if item["kind"] == "file":
                if (
                    not isinstance(item["content"], str)
                    or hashlib.sha256(item["content"].encode()).hexdigest()
                    != item["sha256"]
                ):
                    raise invalid("create manifest content differs from its hash")
            elif (
                item["kind"] != "directory"
                or item["content"] is not None
                or item["sha256"] is not None
            ):
                raise invalid("create manifest has an invalid path type")
        if len(set(paths)) != len(paths):
            raise invalid("create manifest has duplicate paths")
        if row["schema"] == INTAKE_SCHEMA_V2:
            try:
                parse_source_baseline(manifest["source_baseline"])
            except ValueError as error:
                raise invalid(str(error)) from error
    return row


def read_intake(config: ProjectConfig, slug: str) -> dict[str, Any]:
    path = intake_path(config, slug)
    safe_target(config.root, path)
    try:
        document = validate_intake(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise invalid(
            f"intake is unreadable at {path.relative_to(config.root)}: {error}"
        ) from error
    if document["feature"] != slug:
        raise invalid("intake feature does not match its path")
    return document


def publish_intake(
    path: Path, document: dict[str, Any], *, create: bool = False
) -> None:
    validate_intake(document)
    replace_file_text(path, yaml.safe_dump(document, sort_keys=False), create=create)


def pending_intakes(config: ProjectConfig) -> tuple[dict[str, Any], ...]:
    """Return validated formal-route intakes that do not yet have state owners."""
    root = config.root / ".heddle" / "intake"
    if not root.is_dir():
        return ()
    safe_target(config.root, root)
    rows: list[dict[str, Any]] = []
    try:
        paths = sorted(root.glob("*.yaml"), key=lambda path: path.name)
    except OSError as error:
        raise invalid(f"cannot scan pending intakes: {error}") from error
    for path in paths:
        slug = path.stem
        validate_identity(slug)
        if feature_state_path(config, slug).is_file():
            continue
        document = read_intake(config, slug)
        if document["route"] == "heddle":
            rows.append(document)
    return tuple(rows)


def pending_intake_action(
    config: ProjectConfig, document: dict[str, Any]
) -> NextAction:
    """Project the next admission action from one validated intake revision."""
    slug = document["feature"]
    if document["feature_policy"] is None:
        return NextAction(
            ops.AuthoringAction(
                feature=slug,
                work="feature-policy",
                references=(
                    intake_path(config, slug).relative_to(config.root).as_posix(),
                    document["research"]["reference"],
                ),
                input_schema=POLICY_SCHEMA,
                expected_revision=document["revision"],
            ),
            "Confirm the complete review matrix for the prepared intake at its "
            "current revision",
        )
    flow = (
        document["create_manifest"]["flow"]
        if document["create_manifest"] is not None
        else config.autopilot.flow
    )
    return NextAction(
        ops.CommandAction(
            ops.FeatureStart(
                slug=slug,
                area=document["area"],
                flow=flow,
                expect_revision=document["revision"],
            )
        ),
        "Admit the confirmed intake at its current revision",
    )


def prepare_feature(operation: ops.FeaturePrepare) -> HeddleResult:
    try:
        validate_identity(operation.slug, operation.area)
        payload = object_fields(operation.payload, INTAKE_INPUT_FIELDS)
        if payload["schema"] != INTAKE_INPUT_SCHEMA_ID:
            raise invalid(f"prepare input requires {INTAKE_INPUT_SCHEMA_ID}")
        if payload["route"] not in INTAKE_ROUTES:
            raise invalid(f"route must be {' or '.join(INTAKE_ROUTES)}")
        nonempty(payload["route_reason"], "route reason")
        research = object_fields(payload["research"], INTAKE_RESEARCH_FIELDS)
        nonempty(research["summary"], "research summary")
        axes = parse_axes(payload["axes"])
        config = load_project_config_from_cwd()
        path = intake_path(config, operation.slug)
        safe_target(config.root, path)
        formal = feature_state_path(config, operation.slug)
        if formal.exists() or formal.is_symlink():
            raise invalid(
                "admitted intake is immutable; use feature policy or reassess "
                "at its state owner"
            )
        observed_research = {
            **research,
            "sha256": research_identity(config.root, research["reference"]),
        }
        now = utc_now_minutes()
        if not operation.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
        with nullcontext() if operation.dry_run else state_recording_lock(path):
            # Admission takes the same intake lock before its state lock.
            if formal.exists() or formal.is_symlink():
                raise invalid("formal state now owns this feature; intake is immutable")
            previous = read_intake(config, operation.slug) if path.exists() else None
            if previous and previous["create_manifest"] is not None:
                raise invalid(
                    "admission has started; retry the exact feature start "
                    "before changing intake"
                )
            row = {
                "schema": INTAKE_SCHEMA,
                "feature": operation.slug,
                "area": operation.area,
                "revision": previous["revision"] + 1 if previous else 1,
                "created": previous["created"] if previous else now,
                "updated": now,
                "route": payload["route"],
                "route_reason": payload["route_reason"],
                "research": observed_research,
                "axes": ops.decoded_payload(axes),
                "feature_policy": None,
                "policy_history": [],
                "assessments": [],
                "create_manifest": None,
            }
            same = previous is not None and all(
                previous[key] == row[key]
                for key in (
                    "feature",
                    "area",
                    "route",
                    "route_reason",
                    "research",
                    "axes",
                )
            )
            if same:
                assert previous is not None
                row = previous
            elif not operation.dry_run:
                publish_intake(path, row, create=previous is None)
            return HeddleResult.success(
                {
                    "feature": operation.slug,
                    "revision": row["revision"],
                    "route": row["route"],
                    "dry_run": operation.dry_run,
                    "reused": same,
                    "recommendation": ops.decoded_payload(recommend_policy(axes)),
                    "host_gate_constraints": list(config.gates_enabled)
                    if config.gates_enabled is not None
                    else None,
                },
                next_actions=(pending_intake_action(config, row),)
                if row["route"] == "heddle"
                else (),
            )
    except Conflict as error:
        return conflict_failure(error)
    except KernelError as error:
        return write_failure(error)
    except (OSError, ValueError, yaml.YAMLError) as error:
        return write_failure(invalid(str(error)))


def run_feature_input(args: list[str], json_mode: bool, kind: str) -> int:
    """One argv/YAML adapter for explicit feature-input operations."""
    parsed, values, positionals, failure = parse_write_args(
        args,
        value_flags={
            flag: (f"{flag} requires a value", f"use feature {kind} --help")
            for flag in ("--from-file", "--area")
        },
        unknown_hint=f"use feature {kind} --help",
        allow_positionals=True,
    )
    if failure is None:
        assert parsed is not None
        if (
            len(positionals) != 1
            or parsed.feature is not None
            or values["--from-file"] is None
        ):
            failure = usage_failure(
                f"feature {kind} needs one slug and --from-file",
                f"use feature {kind} --help",
            )
        else:
            try:
                source = values["--from-file"]
                payload = yaml.safe_load(
                    sys.stdin.read()
                    if source == "-"
                    else Path(source).read_text(encoding="utf-8")
                )
                if not isinstance(payload, dict):
                    raise ValueError("input must be a mapping")
                operation: ops.Operation
                if kind == "prepare":
                    if values["--area"] is None or parsed.expect_revision is not None:
                        raise ValueError(
                            "prepare requires --area and does not accept "
                            "--expect-revision"
                        )
                    operation = ops.FeaturePrepare(
                        positionals[0], values["--area"], payload, parsed.dry_run
                    )
                elif values["--area"] is not None:
                    raise ValueError(f"{kind} does not accept --area")
                elif kind == "policy":
                    operation = ops.FeaturePolicy(
                        positionals[0],
                        payload,
                        parsed.expect_revision,
                        parsed.dry_run,
                    )
                else:
                    if parsed.dry_run:
                        raise ValueError("reassess does not accept --dry-run")
                    operation = ops.FeatureReassess(
                        positionals[0], payload, parsed.expect_revision
                    )
                from heddle.runtime.application import execute

                return emit_envelope(execute(operation), json_mode, _render)
            except (OSError, ValueError, yaml.YAMLError) as error:
                failure = usage_failure(str(error), f"use feature {kind} --help")
    assert failure is not None
    return emit_envelope(failure, json_mode, _render)


def run_feature_prepare(args: list[str], json_mode: bool) -> int:
    return run_feature_input(args, json_mode, "prepare")


def _render(result: HeddleResult) -> None:
    stream = sys.stdout if result.ok else sys.stderr
    print(
        json.dumps(result.data if result.ok else result.to_envelope(), indent=2),
        file=stream,
    )
