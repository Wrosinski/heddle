from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from heddle.kernel.project_config import KernelError
from heddle.kernel.source_manifest import (
    SourceEvidence,
    decode_source_evidence,
    encode_source_evidence,
    normalize_paths,
)


@dataclass(frozen=True)
class SourceAttribution:
    reason: str
    at: str
    source: SourceEvidence
    references: SourceEvidence


def attribution_error(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message=message,
        hint=(
            "inspect exact outside-feature changes and their evidence, then use "
            "heddle feature sources attribute --from-file <json>; owned or "
            "unclassified changes still require verification ownership"
        ),
    )


def parse_attribution_input(value: Any) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "attributions"}
        or value["schema"] != "heddle.source-attribution-input/v1"
        or not isinstance(value["attributions"], list)
        or not value["attributions"]
    ):
        raise attribution_error("invalid source attribution input envelope")
    result = []
    seen: set[str] = set()
    for entry in value["attributions"]:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"paths", "references", "reason"}
            or not isinstance(entry["reason"], str)
            or not entry["reason"].strip()
        ):
            raise attribution_error("attribution needs paths, references and a reason")
        row = dict(entry)
        for key in ("paths", "references"):
            if not isinstance(entry[key], list) or not entry[key]:
                raise attribution_error(f"attribution {key} must be a non-empty list")
            row[key] = list(normalize_paths(entry[key]))
        if seen.intersection(row["paths"]):
            raise attribution_error("attribution batch repeats a path")
        seen.update(row["paths"])
        result.append(row)
    return tuple(result)


def parse_source_attributions(value: Any) -> tuple[SourceAttribution, ...]:
    if not isinstance(value, list):
        raise attribution_error("source_attributions must be a list")
    result = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {
            "reason",
            "at",
            "source",
            "references",
        }:
            raise attribution_error("invalid retained source attribution")
        if any(
            not isinstance(row[k], str) or not row[k].strip() for k in ("reason", "at")
        ):
            raise attribution_error(
                "source attribution reason and timestamp are required"
            )
        source = decode_source_evidence(_canonical_bytes(row["source"]))
        references = decode_source_evidence(_canonical_bytes(row["references"]))
        validate_attribution_evidence(source, references=False)
        validate_attribution_evidence(references, references=True)
        result.append(SourceAttribution(row["reason"], row["at"], source, references))
    return tuple(result)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def validate_attribution_evidence(
    evidence: SourceEvidence, *, references: bool
) -> None:
    definition = evidence.definition
    if (
        definition.kind != "outside-feature"
        or definition.paths != definition.declaration_paths
        or any(row.kind == "directory" for row in evidence.observations)
        or (references and any(row.kind != "file" for row in evidence.observations))
    ):
        raise attribution_error(
            "attribution requires exact leaf paths and existing regular evidence files"
        )


def attribution_payload(value: SourceAttribution) -> dict[str, Any]:
    return {
        "reason": value.reason,
        "at": value.at,
        "source": json.loads(encode_source_evidence(value.source)),
        "references": json.loads(encode_source_evidence(value.references)),
    }


def overlaps_ownership(path: str, owned: tuple[str, ...]) -> bool:
    return any(
        path == owner or path.startswith(owner + "/") or owner.startswith(path + "/")
        for owner in owned
    )
