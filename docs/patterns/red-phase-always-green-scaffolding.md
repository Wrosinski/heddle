---
name: red-phase-always-green-scaffolding
area: testing
status: active
created: 2026-06-11
superseded_by: null
---

# Red-phase × always-green test scaffolding

**Intent:** scaffold *all* milestones' tests at Phase 5 (full red-phase
coverage) while keeping the plan-level `test_command` green at every
intermediate milestone boundary.

**When to use:** multi-milestone features that scaffold tests up front
under an always-green `test_command` discipline. Pointless for
single-milestone features (everything goes live at once).

**Recipe:**

1. The **current** (first) milestone's test files import the feature
   module directly — collection-error red before implementation; the
   scaffolding gate blesses this as the expected red signal.
2. **Later** milestones' test files put a module-level
   `pytest.importorskip("<their milestone's module>")` at the top — they
   skip cleanly until that module lands, then go live automatically in
   the same run that creates it.
3. Keep the smoke test scoped to exclude the feature's test directory,
   so pre-existing-system health stays measurable during the red phase.
4. **Validate the red/survivor phase** before implementation starts and
   record it: every changed-behavior AC has an expected failing/erroring
   red discriminator; pre-existing behavior at risk is covered by an
   explicitly labelled passing survivor pin; expected skips are counted.
   An unlabelled pass remains a scaffold defect.
5. **Retire every `importorskip` to a hard import at feature
   completion** — once the gating module exists, a leftover guard
   converts any future import-time break into a silent suite skip
   instead of a loud failure.

**Variant — seam-keyed skips for reds in EXISTING shared files:** `importorskip` is
unavailable when a later milestone's reds must live in files whose modules
already exist (shared driver/kernel test files selected by earlier
milestones' stored verification commands). Gate those reds instead with a
`skipif` on a *production-seam probe* — a behavior only the awaited
milestone introduces (there: `allowed_tools_for_stage("complete", …)`
raising `ValueError`). The probe cannot rot like a deselect list: it
self-deactivates the moment the seam lands, and the awaited milestone's own
verification expects the tests to run, so a wrong probe fails loud. The
retirement discipline is stronger than step 5's importorskip→hard-import
swap: DELETE the probe and markers entirely once the seam lands — leaving
them inert means a future regression of the seam re-masks the guards as
skips instead of failures. Validated end-to-end on
kickoff-guidance-integration (14 gated reds; gating removed post-m3;
113 passed / 0 skipped after retirement).

**Anti-patterns / caveats:** the retirement step (5) is the part teams
forget — it often surfaces only during review, not in design.
Guards must reference *their own* milestone's module, not the feature
root, or they go live too early/late. A gated module may live in
*another suite's* directory when the feature touches it (for example,
`tests/gate/test_prompt_config_seam.py` gated on the pm1 module kept
the gate suite's smoke scope green) — the guard travels with the test
file, wherever it lives; step 3's smoke exclusion covers only the
feature's own test directory. Two further sharp edges are: (a)
**scope per-milestone verification by marker** — when the whole suite is
scaffolded red up front, an always-green `test_command` must select only
the landed scope (`-m pm1`), and the markers must be registered *during*
Phase 5 (not at the first milestone's last task) so the red-phase run can
select them; (b) **the `tmp_path` substring trap** — a "red" assertion that
substring-searches text derived from pytest's `tmp_path` can pass
*spuriously*, because `tmp_path` embeds the test's own name; strip the host
path before searching so the discriminator is genuinely red.

**Concrete future-feature scenario:** every multi-milestone Heddle feature's
Phase 5 — for example, scaffold kernel tests red while `status`/`orient`
dispatch tests gate on the increment-6 module.
