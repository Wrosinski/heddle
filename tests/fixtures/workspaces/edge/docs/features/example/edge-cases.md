# Edge Cases (shared synthetic spec)

Shared spec-path target for every workspace in the `edge` host — the
kernel never reads this file (control-plane hot path, D20/D25a); it exists
so each workspace's `spec` path resolves on disk (corpus conformance).
See `tests/fixtures/workspaces/README.md` for the per-workspace coverage map.
