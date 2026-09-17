---
type: feature-spec
area: data
feature_name: Legacy Feature
status: in-progress
created: 2026-03-02
last_updated: 2026-03-18
related:
  code_paths:
    - src/example/ingest/loader.py
---

# Legacy Feature

## Purpose

A spec in the legacy one-document-era format: `status` frontmatter instead
of the runtime-written `lifecycle` stamp (WM-5), prose milestones tracked in
a dotted-suffix plan file rather than a feature workspace. This pair
(`docs/features/data/legacy-feature.md` + `plans/legacy-feature.plan.md`)
is the `migrate --from-legacy` extraction input (workflow-model §13).

## Acceptance Criteria

### AC-1: loader normalizes the legacy column names

Priority: MUST
Action: load the sample extract through the loader.
Expected: canonical column names in the output frame.
