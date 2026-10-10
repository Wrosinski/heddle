"""Pure working-source definitions, observations and canonical identity framing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Literal, get_args

import yaml

from heddle.kernel.project_config import KernelError
from heddle.kernel.review_equivalence import equivalence_identity, review_equivalent

SOURCE_MANIFEST_SCHEMA = "heddle.verification-source/v2"
SOURCE_EVIDENCE_V1_SCHEMA = "heddle.source-evidence/v1"
# Writers emit v2; v1 artifacts recorded before it still decode byte-exactly.
SOURCE_EVIDENCE_SCHEMA = "heddle.source-evidence/v2"
SOURCE_EVIDENCE_SCHEMAS = (SOURCE_EVIDENCE_V1_SCHEMA, SOURCE_EVIDENCE_SCHEMA)
SOURCE_EVIDENCE_REFERENCE_SCHEMA = "heddle.source-evidence-ref/v1"
SourceSetKind = Literal[
    "milestone-owns", "feature-owned-union", "feature-inputs", "outside-feature"
]
SOURCE_SET_KINDS: frozenset[str] = frozenset(get_args(SourceSetKind))
_EVIDENCE_FIELDS = frozenset({"schema", "definition", "observations", "source_sha256"})
_V2_EVIDENCE_FIELDS = frozenset({"components"})
_OBSERVATION_FIELDS = frozenset({"path", "kind", "executable", "content_sha256"})
_V2_OBSERVATION_FIELDS = frozenset({"equivalence"})
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
class EquivalenceIdentity:
    """A review-equivalence identity: a rule id and its canonical digest."""

    rule: str
    digest: str


@dataclass(frozen=True)
class EvidenceObservation:
    path: str
    kind: Literal["file", "symlink", "directory", "missing"]
    executable: bool
    content_sha256: str
    # Recorded beside raw identity (v2 only); never part of source_sha256.
    equivalence: EquivalenceIdentity | None = None


@dataclass(frozen=True)
class EvidenceComponent:
    """One named non-file digest of a basis manifest."""

    name: str
    digest: str


@dataclass(frozen=True)
class SourceEvidence:
    schema: str
    definition: SourceDefinition
    observations: tuple[EvidenceObservation, ...]
    source_sha256: str
    # Ordered named digests (v2 only); never part of source_sha256.
    components: tuple[EvidenceComponent, ...] = ()


@dataclass(frozen=True)
class EvidenceReference:
    schema: str
    artifact: str
    artifact_sha256: str
    source_sha256: str


COMPONENT_CHANGE_PREFIX = "component:"


@dataclass(frozen=True)
class SourceChange:
    """One typed difference from a recorded manifest to the current one.

    A path change names its path; a named component change has the kind
    ``component:<name>`` and no path.
    """

    kind: str
    path: str | None = None


@dataclass(frozen=True)
class SourceComparison:
    changes: tuple[SourceChange, ...]
    equivalent: bool


def byte_sort_key(value: str) -> bytes:
    """Order paths by UTF-8 bytes so source identities stay portable."""
    return value.encode("utf-8")


def raw_observation(row: EvidenceObservation) -> tuple[str, str, bool, str]:
    """Return the raw fields a source-evidence comparer binds.

    Comparers use these, never whole observations, so a v1 record without an
    equivalence identity compares exactly as before against v2 observations.
    """
    return row.path, row.kind, row.executable, row.content_sha256


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
    """Build the immutable, compact evidence form for one source observation.

    Observations carry raw identity only, so reads pay no rule cost; a write
    records equivalence through ``stamp_equivalence``.
    """
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


def stamp_equivalence(
    evidence: SourceEvidence, contents: Mapping[str, Iterable[bytes]]
) -> SourceEvidence:
    """Record the review-equivalence identity of the named observations.

    A named file observation takes the identity of the first candidate whose
    SHA-256 is its raw content digest, so the identity is always that of the
    observed bytes. With no such candidate, or no rule for the path, it records
    none. Other observations, raw identity and ``source_sha256`` are unchanged.
    """
    return replace(
        evidence,
        observations=tuple(
            replace(row, equivalence=_observed_identity(row, contents[row.path]))
            if row.path in contents
            else row
            for row in evidence.observations
        ),
    )


def _observed_identity(
    row: EvidenceObservation, candidates: Iterable[bytes]
) -> EquivalenceIdentity | None:
    if row.kind != "file":
        return None
    for content in candidates:
        if hashlib.sha256(content).hexdigest() == row.content_sha256:
            identity = equivalence_identity(row.path, content)
            return None if identity is None else EquivalenceIdentity(*identity)
    return None


def compare_source_evidence(
    recorded: SourceEvidence, current: SourceEvidence
) -> SourceComparison:
    """Name every change from a recorded manifest; the one equivalence verdict.

    A path in only one manifest's path set is a ``membership`` change. A path in
    both is ``added`` or ``removed`` when it flips between missing and present,
    ``kind`` when file, symlink or directory differ, ``mode`` when the
    executable bit differs, and otherwise ``formatting-only`` when the two
    observations are review-equivalent, else ``content``. Each named component
    whose digest differs is ``component:<name>``. The manifests are equivalent
    only when every change is formatting-only. Equivalence needs identity on
    both sides, so a current read is stamped against the record first.
    """
    before = {row.path: row for row in recorded.observations}
    after = {row.path: row for row in current.observations}
    changes = [
        SourceChange(kind, path)
        for path in sorted(before.keys() | after.keys(), key=byte_sort_key)
        if (kind := _path_change(before.get(path), after.get(path))) is not None
    ]
    left = {component.name: component.digest for component in recorded.components}
    right = {component.name: component.digest for component in current.components}
    changes.extend(
        SourceChange(COMPONENT_CHANGE_PREFIX + name)
        for name in (*left, *(name for name in right if name not in left))
        if left.get(name) != right.get(name)
    )
    return SourceComparison(
        tuple(changes), all(change.kind == "formatting-only" for change in changes)
    )


def changed_paths(changes: Iterable[SourceChange]) -> tuple[str, ...]:
    """Return the distinct changed paths in canonical order."""
    return tuple(
        sorted(
            {change.path for change in changes if change.path is not None},
            key=byte_sort_key,
        )
    )


def _path_change(
    recorded: EvidenceObservation | None, current: EvidenceObservation | None
) -> str | None:
    if recorded is None or current is None:
        return "membership"
    if raw_observation(recorded) == raw_observation(current):
        return None
    if (recorded.kind == "missing") != (current.kind == "missing"):
        return "added" if recorded.kind == "missing" else "removed"
    if recorded.kind != current.kind:
        return "kind"
    if recorded.executable != current.executable:
        return "mode"
    return "formatting-only" if review_equivalent(recorded, current) else "content"


def encode_source_evidence(evidence: SourceEvidence) -> bytes:
    """Return one deterministic UTF-8 representation for evidence identity."""
    _validate_source_evidence(evidence)
    payload: dict[str, Any] = {
        "schema": evidence.schema,
        "definition": {
            "kind": evidence.definition.kind,
            "declaration_paths": list(evidence.definition.declaration_paths),
            "paths": list(evidence.definition.paths),
        },
        "observations": [_encode_observation(row) for row in evidence.observations],
        "source_sha256": evidence.source_sha256,
    }
    if evidence.components:
        payload["components"] = [
            {"name": component.name, "digest": component.digest}
            for component in evidence.components
        ]
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
    if not isinstance(payload, dict) or not _EVIDENCE_FIELDS <= set(payload):
        raise _invalid_evidence("source evidence has unsupported fields")
    schema = payload["schema"]
    if schema not in SOURCE_EVIDENCE_SCHEMAS:
        raise _invalid_evidence(f"unsupported source evidence schema: {schema!r}")
    current = schema == SOURCE_EVIDENCE_SCHEMA
    if not set(payload) <= _EVIDENCE_FIELDS | (
        _V2_EVIDENCE_FIELDS if current else frozenset()
    ):
        raise _invalid_evidence("source evidence has unsupported fields")
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
    allowed_row_fields = _OBSERVATION_FIELDS | (
        _V2_OBSERVATION_FIELDS if current else frozenset()
    )
    for value in rows_value:
        if (
            not isinstance(value, dict)
            or not _OBSERVATION_FIELDS <= set(value)
            or not set(value) <= allowed_row_fields
        ):
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
        equivalence = (
            _decode_named_digest(
                value["equivalence"], "rule", "observation equivalence"
            )
            if "equivalence" in value
            else None
        )
        rows.append(
            EvidenceObservation(
                path,
                kind,
                executable,
                digest,
                None if equivalence is None else EquivalenceIdentity(*equivalence),
            )
        )
    if tuple(rows) != tuple(sorted(rows, key=lambda row: byte_sort_key(row.path))):
        raise _invalid_evidence("source evidence observations are not canonical")
    source_sha256 = payload["source_sha256"]
    _require_digest(source_sha256, "source evidence identity")
    components_value = payload.get("components", [])
    if not isinstance(components_value, list):
        raise _invalid_evidence("source evidence components must be a list")
    components = tuple(
        EvidenceComponent(*_decode_named_digest(value, "name", "evidence component"))
        for value in components_value
    )
    evidence = SourceEvidence(
        schema, definition, tuple(rows), source_sha256, components
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


def _encode_observation(row: EvidenceObservation) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": row.path,
        "kind": row.kind,
        "executable": row.executable,
        "content_sha256": row.content_sha256,
    }
    if row.equivalence is not None:
        value["equivalence"] = {
            "rule": row.equivalence.rule,
            "digest": row.equivalence.digest,
        }
    return value


def _decode_named_digest(value: object, key: str, label: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {key, "digest"}:
        raise _invalid_evidence(f"{label} is malformed")
    return value[key], value["digest"]


def _validate_source_evidence(evidence: SourceEvidence) -> None:
    if evidence.schema not in SOURCE_EVIDENCE_SCHEMAS:
        raise _invalid_evidence(
            f"unsupported source evidence schema: {evidence.schema!r}"
        )
    _validate_v2_columns(evidence)
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


def _validate_v2_columns(evidence: SourceEvidence) -> None:
    """Equivalence and components exist only in v2, and only in their shapes."""
    current = evidence.schema == SOURCE_EVIDENCE_SCHEMA
    for row in evidence.observations:
        if row.equivalence is None:
            continue
        if not current:
            raise _invalid_evidence(
                f"{evidence.schema} observations carry no equivalence identity"
            )
        if row.kind != "file":
            raise _invalid_evidence(
                f"only a file observation carries an equivalence identity: {row.path}"
            )
        if not isinstance(row.equivalence.rule, str) or not row.equivalence.rule:
            raise _invalid_evidence("observation equivalence rule must be a string")
        _require_digest(row.equivalence.digest, "observation equivalence")
    if not isinstance(evidence.components, tuple):
        raise _invalid_evidence("source evidence components must be a tuple")
    if evidence.components and not current:
        raise _invalid_evidence(f"{evidence.schema} evidence carries no components")
    names: set[str] = set()
    for component in evidence.components:
        if (
            not isinstance(component, EvidenceComponent)
            or not isinstance(component.name, str)
            or not component.name
        ):
            raise _invalid_evidence("evidence component must have a name")
        if component.name in names:
            raise _invalid_evidence(f"duplicate evidence component: {component.name}")
        names.add(component.name)
        _require_digest(component.digest, "evidence component")


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
