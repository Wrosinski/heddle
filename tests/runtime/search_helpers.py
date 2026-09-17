"""Shared feature-search test oracles."""

from pathlib import Path


def snapshot_tree(root: Path) -> dict[str, bytes | None]:
    """Return relative directory topology and file bytes below ``root``."""
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes() if path.is_file() else None
        )
        for path in sorted(root.rglob("*"))
    }
