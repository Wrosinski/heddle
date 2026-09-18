"""Pure working-source definitions, observations and canonical identity framing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, get_args

import yaml

from heddle.kernel.project_config import KernelError

SOURCE_MANIFEST_SCHEMA = "heddle.verification-source/v2"
SOURCE_EVIDENCE_SCHEMA = "heddle.source-evidence/v1"
SOURCE_EVIDENCE_REFERENCE_SCHEMA = "heddle.source-evidence-ref/v1"
SourceSetKind = Literal[
    "milestone-owns", "feature-owned-union", "feature-inputs", "outside-feature"
]
SOURCE_SET_KINDS: frozenset[str] = frozenset(get_args(SourceSetKind))
HEDDLE_OWNED_ROOTS = ("plans",)
_HEDDLE_OWNED_ROOTS = tuple(tuple(root.split("/")) for root in HEDDLE_OWNED_ROOTS)


@dataclass(frozen=True)
class SourceDefinition:
    kind: SourceSetKind
    declaration_paths: tuple[str, ...]
    paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_SET_KINDS:
            raise _invalid_source_declaration(f"unknown source-set kind: {self.kind!r}")
        declarations = normalize_paths(self.declaration_paths)
        if not declarations:
            raise _invalid_source_declaration("source declaration is empty")
        object.__setattr__(self, "declaration_paths", declarations)
        object.__setattr__(self, "paths", normalize_paths(self.paths))


@dataclass(frozen=True)
class ObservedPath:
    path: str
    kind: Literal["file", "symlink", "directory", "missing"]
    executable: bool
    content: bytes


@dataclass(frozen=True)
class EvidenceObservation:
    path: str
    kind: Literal["file", "symlink", "directory", "missing"]
    executable: bool
    content_sha256: str


@dataclass(frozen=True)
class SourceEvidence:
    schema: str
    definition: SourceDefinition
    observations: tuple[EvidenceObservation, ...]
    source_sha256: str


@dataclass(frozen=True)
class EvidenceReference:
    schema: str
    artifact: str
    artifact_sha256: str
    source_sha256: str


def byte_sort_key(value: str) -> bytes:
    """Order paths by UTF-8 bytes so source identities stay portable."""
    return value.encode("utf-8")


def normalize_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Normalize repository-relative declarations, including gate control inputs."""
    normalized: list[str] = []
    seen: set[str] = set()
    try:
        values = tuple(paths)
    except TypeError as error:
        raise _invalid_source_declaration("source paths must be iterable") from error
    for raw in values:
        if not isinstance(raw, str):
            raise _invalid_source_declaration("source paths must be strings")
        value = raw[:-1] if raw.endswith("/") else raw
        parts = safe_relative_parts(value)
        rendered = "/".join(parts)
        if rendered in seen:
            raise _invalid_source_declaration(
                f"source paths contain a normalized duplicate: {rendered!r}"
            )
        seen.add(rendered)
        normalized.append(rendered)
    return tuple(sorted(normalized, key=byte_sort_key))


def normalize_source_paths(paths: Iterable[str]) -> tuple[str, ...]:
    """Apply verification's control-root exclusion to normalized declarations."""
    normalized = normalize_paths(paths)
    for path in normalized:
        parts = tuple(path.split("/"))
        if any(
            parts[: len(root)] == root or root[: len(parts)] == parts
            for root in _HEDDLE_OWNED_ROOTS
        ):
            raise _invalid_source_declaration(
                f"source path overlaps a Heddle-owned root: {path!r}"
            )
    return normalized


def normalize_milestone_source_paths(
    paths: Iterable[str], *, feature: str
) -> tuple[str, ...]:
    """Normalize milestone ownership without making its own ledger self-observing.

    A migration may explicitly own another feature's retained Heddle records.
    The active feature's workspace and close records remain runtime-owned, as does
    the shared gate-effectiveness log.  Broad ``plans`` ownership overlaps those
    controls and is therefore refused.
    """
    normalized = normalize_paths(paths)
    protected_roots = tuple(
        tuple(path.split("/"))
        for path in (
            f"plans/{feature}",
            f"plans/{feature}.decision-journal.md",
            f"plans/{feature}.friction-retrospective.md",
            "plans/gate-effectiveness.md",
        )
    )
    for path in normalized:
        parts = tuple(path.split("/"))
        overlaps_protected_root = any(
            parts[: len(root)] == root or root[: len(parts)] == parts
            for root in protected_roots
        )
        if overlaps_protected_root:
            raise _invalid_source_declaration(
                "source path overlaps a Heddle-owned root among this feature's "
                f"Heddle-owned controls: {path!r}"
            )
    return normalized


def safe_relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\0" in value:
        raise _invalid_source_declaration(f"source path is empty or invalid: {value!r}")
    raw_parts = value.split("/")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or tuple(raw_parts) != pure.parts
    ):
        raise _invalid_source_declaration(
            f"source path escapes or is not normalized: {value!r}"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _invalid_source_declaration(
            f"source path is not valid UTF-8: {value!r}"
        ) from error
    return tuple(raw_parts)


def build_source_evidence(
    definition: SourceDefinition,
    observations: Iterable[ObservedPath],
) -> SourceEvidence:
    """Build the immutable, compact evidence form for one source observation."""
    rows: list[EvidenceObservation] = []
    seen: set[str] = set()
    for observation in observations:
        path = normalize_paths((observation.path,))[0]
        if path in seen:
            raise _invalid_source_declaration(
                f"source observations contain a duplicate: {path!r}"
            )
        if observation.kind not in {"file", "symlink", "directory", "missing"}:
            raise _invalid_source_declaration(
                f"unknown source observation kind: {observation.kind!r}"
            )
        seen.add(path)
        rows.append(
            EvidenceObservation(
                path,
                observation.kind,
                observation.executable,
                hashlib.sha256(observation.content).hexdigest(),
            )
        )
    rows.sort(key=lambda row: byte_sort_key(row.path))
    expected = set(definition.declaration_paths) | set(definition.paths)
    if seen != expected:
        raise _invalid_source_declaration(
            "source observations must exactly cover declarations and selected paths"
        )
    source_sha256 = _source_identity_from_evidence(definition, tuple(rows))
    return SourceEvidence(
        SOURCE_EVIDENCE_SCHEMA, definition, tuple(rows), source_sha256
    )


def encode_source_evidence(evidence: SourceEvidence) -> bytes:
    """Return one deterministic UTF-8 representation for evidence identity."""
    _validate_source_evidence(evidence)
    payload = {
        "schema": evidence.schema,
        "definition": {
            "kind": evidence.definition.kind,
            "declaration_paths": list(evidence.definition.declaration_paths),
            "paths": list(evidence.definition.paths),
        },
        "observations": [
            {
                "path": row.path,
                "kind": row.kind,
                "executable": row.executable,
                "content_sha256": row.content_sha256,
            }
            for row in evidence.observations
        ],
        "source_sha256": evidence.source_sha256,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def decode_source_evidence(content: bytes) -> SourceEvidence:
    """Decode and semantically validate canonical source-evidence bytes."""
    try:
        payload = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise _invalid_evidence(f"malformed source evidence: {error}") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "definition",
        "observations",
        "source_sha256",
    }:
        raise _invalid_evidence("source evidence has unsupported fields")
    if payload["schema"] != SOURCE_EVIDENCE_SCHEMA:
        raise _invalid_evidence(
            f"unsupported source evidence schema: {payload['schema']!r}"
        )
    definition_value = payload["definition"]
    if not isinstance(definition_value, dict) or set(definition_value) != {
        "kind",
        "declaration_paths",
        "paths",
    }:
        raise _invalid_evidence("source evidence definition is malformed")
    try:
        definition = SourceDefinition(
            definition_value["kind"],
            tuple(definition_value["declaration_paths"]),
            tuple(definition_value["paths"]),
        )
    except (KernelError, TypeError) as error:
        raise _invalid_evidence(
            f"source evidence definition is invalid: {error}"
        ) from error
    rows_value = payload["observations"]
    if not isinstance(rows_value, list):
        raise _invalid_evidence("source evidence observations must be a list")
    rows: list[EvidenceObservation] = []
    seen: set[str] = set()
    for value in rows_value:
        if not isinstance(value, dict) or set(value) != {
            "path",
            "kind",
            "executable",
            "content_sha256",
        }:
            raise _invalid_evidence("source evidence observation is malformed")
        try:
            path = normalize_paths((value["path"],))[0]
        except KernelError as error:
            raise _invalid_evidence(
                f"source evidence observation is invalid: {error}"
            ) from error
        if path in seen:
            raise _invalid_evidence(f"duplicate source evidence observation: {path}")
        seen.add(path)
        kind = value["kind"]
        executable = value["executable"]
        digest = value["content_sha256"]
        if kind not in {"file", "symlink", "directory", "missing"}:
            raise _invalid_evidence(f"unsupported observation kind: {kind!r}")
        if not isinstance(executable, bool):
            raise _invalid_evidence("observation executable must be a boolean")
        _require_digest(digest, "observation content")
        rows.append(EvidenceObservation(path, kind, executable, digest))
    if tuple(rows) != tuple(sorted(rows, key=lambda row: byte_sort_key(row.path))):
        raise _invalid_evidence("source evidence observations are not canonical")
    source_sha256 = payload["source_sha256"]
    _require_digest(source_sha256, "source evidence identity")
    evidence = SourceEvidence(
        SOURCE_EVIDENCE_SCHEMA, definition, tuple(rows), source_sha256
    )
    _validate_source_evidence(evidence)
    if encode_source_evidence(evidence) != content:
        raise _invalid_evidence("source evidence bytes are not canonical")
    return evidence


def evidence_reference(evidence: SourceEvidence) -> EvidenceReference:
    content = encode_source_evidence(evidence)
    artifact_sha256 = hashlib.sha256(content).hexdigest()
    return EvidenceReference(
        SOURCE_EVIDENCE_REFERENCE_SCHEMA,
        f"verification/source-evidence.{artifact_sha256}.yaml",
        artifact_sha256,
        evidence.source_sha256,
    )


def decode_evidence_reference(value: object) -> EvidenceReference:
    """Validate the compact state reference without reading its artifact."""
    if isinstance(value, EvidenceReference):
        reference = value
    elif isinstance(value, dict) and set(value) == {
        "schema",
        "artifact",
        "artifact_sha256",
        "source_sha256",
    }:
        reference = EvidenceReference(
            value["schema"],
            value["artifact"],
            value["artifact_sha256"],
            value["source_sha256"],
        )
    else:
        raise _invalid_evidence("source evidence reference is malformed")
    if reference.schema != SOURCE_EVIDENCE_REFERENCE_SCHEMA:
        raise _invalid_evidence(
            f"unsupported source evidence reference schema: {reference.schema!r}"
        )
    _require_digest(reference.artifact_sha256, "artifact identity")
    _require_digest(reference.source_sha256, "source identity")
    expected = f"verification/source-evidence.{reference.artifact_sha256}.yaml"
    if reference.artifact != expected:
        raise _invalid_evidence(
            "source evidence reference artifact contradicts its identity"
        )
    return reference


def _validate_source_evidence(evidence: SourceEvidence) -> None:
    if evidence.schema != SOURCE_EVIDENCE_SCHEMA:
        raise _invalid_evidence(
            f"unsupported source evidence schema: {evidence.schema!r}"
        )
    paths = tuple(row.path for row in evidence.observations)
    if len(set(paths)) != len(paths):
        raise _invalid_evidence("duplicate source evidence observation")
    if paths != tuple(sorted(paths, key=byte_sort_key)):
        raise _invalid_evidence("source evidence observations are not canonical")
    if set(paths) != set(evidence.definition.declaration_paths) | set(
        evidence.definition.paths
    ):
        raise _invalid_evidence(
            "source evidence observations do not cover the source definition"
        )
    for row in evidence.observations:
        _require_digest(row.content_sha256, "observation content")
    _require_digest(evidence.source_sha256, "source evidence identity")
    expected = _source_identity_from_evidence(
        evidence.definition, evidence.observations
    )
    if evidence.source_sha256 != expected:
        raise _invalid_evidence("source evidence identity contradicts its observations")


def _source_identity_from_evidence(
    definition: SourceDefinition, observations: tuple[EvidenceObservation, ...]
) -> str:
    hasher = hashlib.sha256()
    _manifest_record(hasher, b"domain", SOURCE_MANIFEST_SCHEMA.encode("ascii"))
    _manifest_record(hasher, b"source-set-kind", definition.kind.encode("ascii"))
    for path in definition.declaration_paths:
        _manifest_record(hasher, b"declaration", path.encode("utf-8"))
    for path in definition.paths:
        _manifest_record(hasher, b"selected", path.encode("utf-8"))
    for entry in observations:
        _manifest_record(
            hasher,
            b"entry",
            entry.path.encode("utf-8"),
            entry.kind.encode("ascii"),
            b"1" if entry.executable else b"0",
            bytes.fromhex(entry.content_sha256),
        )
    return hasher.hexdigest()


def _require_digest(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _invalid_evidence(f"{label} must be 64 lowercase hexadecimal characters")
    return value


def _invalid_evidence(problem: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"invalid source evidence: {problem}",
        hint="restore the immutable evidence artifact and retry",
    )


def _manifest_record(hasher: Any, *fields: bytes) -> None:
    for field in fields:
        hasher.update(len(field).to_bytes(8, "big"))
        hasher.update(field)


def _invalid_source_declaration(problem: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=f"invalid source declaration: {problem}",
        hint=(
            "declare normalized UTF-8 repository-relative paths "
            "outside verification outputs"
        ),
    )
