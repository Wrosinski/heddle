"""Ensure the enforcement baseline is filled, not left as a template."""

from __future__ import annotations

import sys
from pathlib import Path

BASELINE = Path("docs/workflow/enforcement-baseline.md")
REQUIRED_HOOKS = {
    "check-prompt-conventions",
    "check-no-src-print",
    "check-test-deletions",
    "check-subprocess-handlers",
    "check-bypass-justification",
    "check-principles-amendment",
    "check-no-committed-gate-locks",
    "check-skip-only-test-scaffolding",
    "check-enforcement-baseline-doc-language-aware",
    "check-ac-test-coverage",
}
TEMPLATE_MARKERS = {
    "YYYY-MM-DD",
    "Example format",
    "Add project-specific hooks here",
    "Document your linter configuration here",
    "If none exist",
    "Document known enforcement gaps",
}


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else BASELINE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"ERROR: unable to read {path}: {error}")
        return 1

    violations: list[str] = []
    for marker in sorted(TEMPLATE_MARKERS):
        if marker in text:
            violations.append(f"template marker still present: {marker}")
    missing = sorted(hook for hook in REQUIRED_HOOKS if f"`{hook}`" not in text)
    if missing:
        violations.append("missing local hook documentation: " + ", ".join(missing))
    required_sections = (
        "## Pre-Commit Hooks",
        "## Linter Rules",
        "## Type Checking",
        "## CI Checks",
        "## Structural Tests",
        "## Known Gaps",
    )
    for section in required_sections:
        if section not in text:
            violations.append(f"missing section {section}")

    if not violations:
        return 0

    print("ERROR: Enforcement baseline is incomplete.")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
