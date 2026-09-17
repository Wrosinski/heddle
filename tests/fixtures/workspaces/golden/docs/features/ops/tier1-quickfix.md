---
type: feature-spec
area: ops
feature_name: Tier 1 Quickfix
lifecycle: active
---

# Tier 1 Quickfix

## Purpose

Abbreviated Tier 1 spec: silence a noisy retry warning in the ingestion
logger. Tier 1 features carry an empty milestone skeleton and are granted
`authorized_through: complete` at `feature start` (WM-2).

## Acceptance Criteria

### AC-1: retry warnings are logged once per batch, not per row

Priority: MUST
Action: run the ingestion smoke test over the sample batch.
Expected: exactly one retry warning per batch in the captured log.
