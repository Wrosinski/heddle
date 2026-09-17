---
type: implementation-plan
feature_spec: docs/features/data/legacy-feature.md
feature_name: Legacy Feature
status: in-progress
tier: 2
current_milestone: 1
total_milestones: 2
smoke_test: poetry run pytest -q -m smoke
dev_setup: poetry install --with dev
test_command: poetry run pytest -q -n auto
lint_command: poetry run flake8
acceptance_test: poetry run pytest -q -m "acceptance and legacy_feature"
live_e2e_test: poetry run pytest -q -m "live and legacy_feature"
created: 2026-03-02
last_updated: 2026-03-18
---

# Implementation Plan: Legacy Feature

Legacy one-document format: facts (YAML header, milestone Status lines, task
checkboxes, `← current` marker) and judgment prose interleaved in one file.
This is the `migrate --from-legacy` extraction input — the workflow-model
§13 migration map names every field's v0 home.

## Milestones

### Milestone 1: Column normalizer

Status: in-progress
Complexity: Medium
Estimated effort: 2-4 hours
Satisfies: AC-1
Depends on: None

Scope: `src/example/ingest/loader.py` maps legacy column names onto the
canonical schema.

Tasks:

- [x] Inventory the legacy column names from the sample extract
- [ ] Implement the mapping table ← current
- [ ] Unit tests: full extract round-trip

Verification:
Command: `poetry run pytest src/tests/ingest -q`
Expected: all tests pass.

### Milestone 2: Loader integration

Status: not-started
Complexity: Low
Estimated effort: 1-2 hours
Satisfies: AC-1
Depends on: Milestone 1

Scope: wire the normalizer into the loader's read path.

Tasks:

[Expanded by implementing agent before coding begins]

Verification:
Command: `poetry run pytest src/tests/ingest -q`
Expected: all tests pass.

## Session Log

### 2026-03-18

Completed: column inventory from the sample extract.
Started: mapping table implementation.
Next steps: finish the mapping table, then round-trip tests.
Blockers: None.
