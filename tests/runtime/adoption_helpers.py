"""
Shared adapters for initialized-adoption fixtures.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from heddle.kernel.managed_regions import begin_marker, end_marker
from heddle.runtime.sync import SESSION_ENTRY_ID, render_session_entry


def adopt_fixture_host(host: Path) -> None:
    """Add a valid adoption footprint without changing existing host bytes."""
    config = host / ".heddle.yaml"
    if not config.is_file():
        raise AssertionError(f"fixture host has no {config.name}: {host}")

    agents = host / "AGENTS.md"
    if not agents.exists():
        agents.write_text(
            f"{begin_marker(SESSION_ENTRY_ID)}\n"
            f"{render_session_entry()}"
            f"{end_marker(SESSION_ENTRY_ID)}\n",
            encoding="utf-8",
        )

    principles = host / "docs/workflow/engineering-principles.md"
    if not principles.exists():
        principles.parent.mkdir(parents=True, exist_ok=True)
        principles.write_text(
            "---\nstatus: ratified\n---\n\n# Engineering Principles\n",
            encoding="utf-8",
        )

    rows = (
        (".heddle.yaml", "scaffold-once"),
        ("AGENTS.md", "managed-region"),
        ("docs/workflow/engineering-principles.md", "scaffold-once"),
    )
    lines = ["schema: heddle.lock/v1", "targets:"]
    for relative, class_ in rows:
        digest = hashlib.sha256((host / relative).read_bytes()).hexdigest()
        lines.extend(
            (
                f"- path: {relative}",
                f"  class: {class_}",
                f"  sha256: {digest}",
            )
        )
    (host / ".heddle.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")
