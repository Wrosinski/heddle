---
name: event-anchored-fake-clock
area: testing
status: active
created: 2026-07-21
superseded_by: null
---

# Event-Anchored Fake Clock

**Intent:** key fake-clock jumps in tests to observed harness events
instead of calibrated clock-read counts or uniform step sizes, so timing
tests survive production-code changes to how often the clock is read.

**When to use:** a test must make time appear to pass at a specific point
in a production flow (a budget trips, a deadline lapses, a stall detector
fires) and the production code reads a fake clock an unspecified number
of times before that point. Applicability conditions:

- The code under test reads time through an injectable seam (a `clock`
  callable, a patched `monotonic`), not the wall clock.
- The number of clock reads before the moment of interest is an
  implementation detail: adding a logging line, a pre-call check, or a
  retry changes it.
- The test harness records observable progress signals (an event list, a
  call counter on a fake port, a recorded checkpoint) that identify the
  moment of interest independently of read counts.

**Recipe:**

1. Identify a harness-observable anchor that marks the moment time should
   jump: an emitted event name, a fake-port call counter, a recorded
   checkpoint or stage marker.
2. Implement the fake clock as a closure over the harness state: return
   the baseline time while the anchor has not occurred, then the jumped
   time after it. If the contract needs a small fixed number of
   post-anchor baseline reads, state that number next to the anchor —
   never as a global read budget.
3. Patch the closure onto the production seam
   (`monkeypatch.setattr(module, "monotonic", fake)`).
4. Assert the contract (the budget refused, the stall was detected) —
   never assert the total number of clock reads.

**Anti-patterns / caveats:**

- Absolute read-count calibration ("return baseline for the first N
  reads, then jump"): any new clock-reading check shifts N and the test
  fails while the contract is intact.
- Uniform step sizes tuned so a specific cumulative read count crosses a
  threshold: a new deadline elsewhere in the flow trips first and the
  test fails against the wrong stage.
- Review heuristic: treat `reads <= N` literals or tuned uniform steps in
  timing tests as fragility findings.
- The anchor must itself be a stable contract surface (a named event, a
  port call), not another implementation detail; anchoring to an internal
  helper's call count just moves the fragility.

**Concrete future-feature scenario:** any extension of the gate monitor
or drive loop that adds a per-stage timeout or budget check (e.g. a
stall detector reading `monotonic` between checkpoint writes) changes
clock-read frequency in existing flows; timing tests anchored to
recorded checkpoints keep passing, while read-count-calibrated ones
break for reasons unrelated to the contract under test.

This pattern is maintained as standalone deterministic workflow-testing guidance.
