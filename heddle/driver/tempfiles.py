"""Temporary file helpers for the driver."""

from __future__ import annotations

import tempfile
from pathlib import Path


def empty_temp_path(*, prefix: str, suffix: str, dir: Path | None = None) -> Path:
    with tempfile.NamedTemporaryFile(
        prefix=prefix, suffix=suffix, dir=dir, delete=False
    ) as raw_file:
        return Path(raw_file.name)


def write_temp_text(
    text: str, *, prefix: str, suffix: str, dir: Path | None = None
) -> Path:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix=prefix, suffix=suffix, dir=dir, delete=False
    ) as handle:
        handle.write(text)
        return Path(handle.name)
