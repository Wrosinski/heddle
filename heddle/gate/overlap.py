from __future__ import annotations

from collections.abc import Sequence

from heddle.gate.types import OverlapResult


def compute_overlap(
    changed_files: Sequence[str], owned_paths: Sequence[str]
) -> OverlapResult:
    if not owned_paths:
        return OverlapResult(status="unassessed", files=())

    matched: list[str] = []
    for changed in changed_files:
        for owned in owned_paths:
            if paths_overlap(changed, owned):
                matched.append(changed)
                break

    if matched:
        return OverlapResult(status="matched", files=tuple(matched))
    return OverlapResult(status="zero-overlap", files=())


def paths_overlap(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(f"{right.rstrip('/')}/")
        or right.startswith(f"{left.rstrip('/')}/")
    )
