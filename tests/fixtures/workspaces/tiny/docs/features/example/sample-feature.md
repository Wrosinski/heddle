---
type: feature-spec
area: example
feature_name: Sample Feature
lifecycle: active
---

# Sample Feature

## Purpose

A minimal happy-path feature for the tiny fixture host: one feature, one
workspace, uniqueness inference resolves it (D24 case 3).

## Acceptance Criteria

### AC-1: input parses into the canonical record shape

Priority: MUST
Action: run the parser over the sample input file.
Expected: a record list with one entry per input line; malformed lines are
reported, not dropped silently.

### AC-2: the CLI prints the record count

Priority: MUST
Action: run `example-app count sample.txt`.
Expected: exit code 0 and the record count on stdout.
