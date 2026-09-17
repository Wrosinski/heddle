"""UTC timestamp helpers for control-plane facts.

Extracted from ``write_path.py`` so the auto-close and verify modules can
share them without importing the write seam. Minute precision is the
state-fact convention (workflow-model §10.1 ``at`` fields); second
precision names verification logs.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_minutes() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")


def utc_now_seconds() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
