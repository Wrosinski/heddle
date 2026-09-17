---
type: feature-spec
area: example
feature_name: Complete Boundary
lifecycle: active
---

# Complete Boundary

## Purpose

Fixture-only feature for the M4 `robustness -> complete` lifecycle stamp.

## Acceptance Criteria

### AC-1: complete boundary can be stamped

Priority: MUST
Action: run `heddle phase-exit --through complete`.
Expected: frontmatter lifecycle becomes `complete` and this body is unchanged.
