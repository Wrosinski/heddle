"""Thin pre-commit wrapper over the engine prompt-conventions guardrail.

The single implementation lives in
``heddle.runtime.guardrails.prompt_convention_violations``; `heddle validate`
sweeps the same check. This wrapper keeps
the repo's commit-time entry point and output format.
"""

from __future__ import annotations

import sys
from pathlib import Path

from heddle.kernel.project_config import (
    DEFAULT_LAYOUT,
    HEDDLE_CONFIG_FILENAME,
    KernelError,
    load_project_config,
)
from heddle.runtime.guardrails import prompt_convention_violations


def _prompts_dir(argv: list[str]) -> Path:
    """The prompt corpus root to check.

    An explicit path argument wins; otherwise the root is read from
    `.heddle.yaml` `layout.prompts` — the same config seam the `validate`
    guardrail consumes, so the commit-time wrapper and the runtime check can
    never disagree about which tree they check. Heddle's own corpus is the
    packaged one under `heddle/resources/prompts/`, declared
    through that key.

    A tree with no config falls back to the loader default, matching
    `prompt.py::_resolve_tests_root`: only a config that exists *and* fails to
    load is an error worth surfacing."""
    if argv:
        return Path(argv[0])
    root = Path.cwd()
    if not (root / HEDDLE_CONFIG_FILENAME).is_file():
        return root / DEFAULT_LAYOUT.prompts
    return root / load_project_config(root).layout.prompts


def main(argv: list[str] | None = None) -> int:
    try:
        prompts_dir = _prompts_dir(list(argv if argv is not None else sys.argv[1:]))
    except KernelError as error:
        print(f"ERROR: {error.message}")
        return 1
    config_path = prompts_dir / "conventions.yaml"
    if not config_path.is_file():
        print(f"ERROR: unable to read {config_path}: file is missing")
        return 1
    try:
        violations = prompt_convention_violations(prompts_dir)
    except KernelError as error:
        print(f"ERROR: {error.message}")
        return 1

    if not violations:
        return 0

    print("ERROR: Prompt convention violations detected.")
    for violation in violations:
        print(f"  - {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
