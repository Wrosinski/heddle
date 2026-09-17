# Implementation Plan: Sample Feature

Judgment prose for the sample-feature workspace. Operational facts live in
`state.yaml` (the control plane); this document carries only the prose the
runtime never touches (WM-7) — the `### Milestone <id>:` headings are the
id-join (§4.1).

## Quick Orientation

A two-milestone Tier 2 feature inside the tiny fixture host: parse an input
file into records, then wire a counting CLI over the parser.

### Milestone m1: Core parser

Scope: `src/example_app/parser.py` reads the sample input format into a list
of records, one per line.

Work: implement the line parser; treat malformed lines as reported errors
rather than silent drops.

Notes: keep the record shape minimal — the CLI milestone consumes it as-is.

Decisions (this milestone):
[Recorded during implementation]

Discoveries (this milestone):
[Recorded during implementation]

### Milestone m2: CLI wiring

Scope: `src/example_app/cli.py` exposes `example-app count <file>`.

Work: wire the parser behind the `count` subcommand; print the record count;
exit 0 on success.

Notes: usage errors exit 2, consistent with the host's CLI conventions.

Decisions (this milestone):
[Recorded during implementation]

Discoveries (this milestone):
[Recorded during implementation]
