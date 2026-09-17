# Enforcement Baseline

Catalog of automated enforcement currently active for this repository.
Implementation Plans link to this owner rather than copy an Active Enforcement
Rules register.

Last updated: 2026-09-09

## Pre-Commit Hooks

### Standard hooks

| Hook | What it enforces |
| ---- | ---------------- |
| `trailing-whitespace` | No trailing whitespace on committed lines |
| `end-of-file-fixer` | Text files end with exactly one newline |
| `check-yaml` | YAML syntax is valid |
| `check-toml` | TOML syntax is valid |
| `check-json` | JSON syntax is valid |
| `check-merge-conflict` | Merge-conflict markers are absent |
| `check-case-conflict` | File names remain portable across case-insensitive filesystems |
| `debug-statements` | Python debugger imports and calls are absent |
| `detect-private-key` | Private-key material is not committed |
| `check-symlinks` | Committed symlinks resolve to an existing target |
| `check-executables-have-shebangs` | Executable text files declare an interpreter |
| `check-shebang-scripts-are-executable` | Files with shebangs carry executable mode |
| `check-added-large-files` | Large accidental files are not committed |
| `ruff-check` | Ruff lint rules pass over maintained Python, with safe fixes applied |
| `ruff-format` | Python formatting matches Ruff format |
| `mypy` | Whole-package Heddle type checking passes through `pyproject.toml` |

### Local hooks

The workflow-owned guards marked "packaged" run from the packaged hook
library `heddle/resources/hooks/` — the repo
dogfoods the unrendered files with their placeholder defaults; hosts get
them projected by `heddle init`.

| Hook | Runs on | What it enforces |
| ---- | ------- | ---------------- |
| `check-prompt-conventions` | the `layout.prompts` corpus (`heddle/resources/prompts/` here) | Prompt files named in that corpus's `conventions.yaml` carry their declared convention markers |
| `check-no-src-print` | `heddle/**/*.py` | Non-CLI source modules do not add accidental `print()` calls |
| `check-test-deletions` (packaged) | staged diff | Test functions, classes, and files are not deleted without `ALLOW_TEST_DELETION=1` |
| `check-subprocess-handlers` | `heddle/gate/**/*.py` | Direct subprocess calls stay inside sanctioned gate helper modules |
| `check-bypass-justification` (packaged) | environment / commit message | Active `ALLOW_*` bypasses require a `Bypass-Justification:` trailer or `BYPASS_JUSTIFICATION` |
| `check-principles-amendment` (packaged) | commit message when `docs/workflow/engineering-principles.md` is staged | Principles amendments carry an owner-ratified `Principles-Amendment:` trailer |
| `check-no-committed-gate-locks` (packaged) | `plans/` | Gate lock files under `plans/.gate-locks` or `*.lock` are not committed |
| `check-skip-only-test-scaffolding` (packaged) | staged test diff | New skips need a narrow reviewed reason; strict `xfail` needs an executable non-constant assertion |
| `check-enforcement-baseline-doc-language-aware` | this document | This baseline stays filled and names the active local hooks |
| `check-ac-test-coverage` | staged tests and bound specs / native workspace metadata | Active scaffold and later AC bindings resolve through the AST checker; explicitly changed historical bindings also resolve |
| `check-skill-sync` | repository skill files | `.codex/skills` matches authored `.claude/skills` bytes; missing, stale, extra or symlinked paths fail |

The repository authors the five skills under `.claude/skills`. Run
`python scripts/check-skill-sync.py --generate` to create or repair their Codex
mirrors. The default invocation checks without writing. Generation preflights
both trees, refuses symlinks and unknown mirrors, and preserves unchanged files.
This is repository maintenance; it does not distribute skills to host projects.

Native verification facts supply command, source identity, outcome and log. The
retired flat-plan prose proof hook no longer duplicates that evidence.

## Linter Rules

Ruff is configured in `pyproject.toml`.

| Setting | Value |
| ------- | ----- |
| Target version | `py313` |
| Line length | `88` |
| Selected rule groups | `E`, `F`, `I`, `B`, `UP` |

The `ruff-check` and `ruff-format` hooks cover every `.py` file under
`heddle/`, `tests/`, and `scripts/`. Their shared scope makes the maintained
Python surface identical for lint and format enforcement.

## Type Checking

Mypy is configured in `pyproject.toml`.

| Setting | Value |
| ------- | ----- |
| Python version | `3.13` |
| Checked package | `heddle` |
| Missing imports | not ignored |
| Untyped bodies | checked |
| Redundant casts | warned |
| Unreachable code | warned |
| Equality overlap | checked |
| Unused configuration | warned |
| Unused ignores | warned |

The development environment pins mypy, `types-pyyaml`, and `types-jsonschema`
to reproducible versions. The pre-commit mypy hook runs `.venv/bin/mypy`
without file arguments, so `files = ["heddle"]` in `pyproject.toml` remains the
single owner of package scope.

## CI Checks

`.github/workflows/quality.yml` runs on pushes and pull requests with
`contents: read` permission. Its Python 3.13 job creates `.venv`, installs the
development extras, runs `.venv/bin/pre-commit run --all-files`, then runs
the explicit fast and toolchain bands. E2E and live execution retain their
separate permission boundaries.

The complete local acceptance sequence is:

```bash
.venv/bin/pre-commit run --all-files
.venv/bin/ruff check heddle tests scripts
.venv/bin/ruff format --check heddle tests scripts
.venv/bin/mypy
.venv/bin/python -m pytest --test-band=fast
.venv/bin/python -m pytest --test-band=toolchain
.venv/bin/heddle validate
```

## Structural Tests

The import AST rails below enforce "Dependencies point inward, not back out".
Write-sink rails protect "One owner per authority" at the existing publication
boundary. The ratified principles define the rules; this table names their
current enforcement rather than introducing another standard.

| Test area | What it enforces |
| --------- | ---------------- |
| `tests/kernel/test_boundaries.py` | Kernel boundary and write/process sink discipline |
| `tests/runtime/test_boundaries.py` | Runtime write/process sink allowlists and M4 seam accounting |
| `tests/runtime/test_schemas.py` | Runtime schema constants and M4 write-seam expectations |
| `tests/runtime/test_contracts.py` | Command contract metadata and output schema discipline |
| `tests/runtime/test_dispatch.py` | Dispatcher routing and stub/live command behavior |
| `tests/runtime/test_write_path_manifest_guardrails.py` | Write-path manifest, repository guardrails, and exit bars |
| `tests/guardrails/test_repository_hooks.py` | Focused behavior checks for the wired local hooks (scripts + packaged) |
| `tests/guardrails/test_packaged_hooks.py` | Packaged hook library: manifest conformance, stdlib-only contract, placeholder rendering, per-hook pass/reject behavior |
| `tests/gate/test_extraction.py` | Plan owned-path extraction and host-contract path recognition |

## Known Gaps

- The local guardrails police this bootstrap repository's current layout. Future
  host repositories may need layout-specific `.heddle.yaml` values before
  reusing the hooks unchanged.
- The write commands are validated against fixture workspaces in the test
  suite; this repository's own features now run on the native surface
  (`heddle drive` / `heddle run-gate` / `heddle kickoff`, m6b switchover).
