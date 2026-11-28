---
name: launch-token-orphan-reaping
area: architecture
status: active
created: 2026-11-28
superseded_by: null
---

# Launch-token orphan reaping

**Intent:** stop every process an external CLI left behind, including ones
that escaped its process group or outlived their parent, without ever
signalling an unrelated process that reused a process ID.

**When to use:** a runner launches a third-party CLI whose own children (tool
commands, shells, helpers) can survive `SIGTERM`, a normal exit or a crash of
the launcher, so killing the process group is not enough. It does not apply
when the child is fully under your control and never detaches, where a
process group or `PR_SET_PDEATHSIG` suffices.

**Recipe:**

1. Mint one unguessable token per physical launch (16 random bytes as hex)
   and pass it in the child's environment under one fixed variable. Every
   descendant inherits it, whatever its process group or session.
2. Before the launch starts, write a launch record into the run's temporary
   directory: the token, the launcher's process ID and start time, and the
   raw stream the launch will write. The record is how a later process
   finds a launch whose launcher crashed.
3. To reap, scan the process table for processes whose environment carries
   exactly `variable=token` as one whole entry. On Linux, for each process
   open a process descriptor (`pidfd_open`) first, then read its start time,
   its environment, and its start time again. Only when both start times
   agree and the descriptor's process has not exited do the reads describe
   the process the descriptor names; otherwise observe it again, a bounded
   number of times.
4. Signal only through the descriptor (`pidfd_send_signal`): `SIGTERM`, a
   grace period, then `SIGKILL`. A recycled process ID can never receive the
   signal, because the descriptor names the original process.
5. Scan again after every pass, so a process born during cleanup is stopped
   too, and stop starting passes after a fixed bound of grace periods.
6. Judge processes whose environment cannot be read instead of guessing. A
   process cannot carry the token if it started before the launcher, if this
   user cannot signal it, or if its readable parent (other than the launcher)
   lacks the token. Anything else is reported as unverifiable and left
   running.
7. Keep cleanup warning-only: an unreadable process table, an unverifiable
   process or a `SIGKILL` survivor becomes a warning, and cleanup counts as
   verified only when a final scan finds no tokened process.
8. Recover departed launches. On each later invocation, and before any reuse
   decision, read every launch record. Finish a launch only when its
   launcher is proven gone by process ID plus start time. A live or
   unverifiable launcher's launch is left untouched. Delete the record only
   after both reaping and any stream redaction succeed, so a failed pass is
   retried.

**Anti-patterns / caveats:** killing by process ID read from a scan, which
races process-ID reuse; matching the token as a substring, which lets a
longer value or a different variable match; trusting a process group, which
a CLI's detached children leave; recovering only on the failure path, so a
run that merely reuses earlier output never cleans up a crashed launch;
reaping a launch whose launcher is still running, which kills a live
sibling's tools. Without `/proc` and process descriptors (macOS, for one),
`ps eww` can find tokened processes but cannot bind their identity, so
report them rather than signal them.

**Concrete future-feature scenario:** Codex reviews leave orphaned tool
commands after a timeout. The Codex runner adopts the same token variable
and launch record. Its existing cleanup and the shared recovery pass then
reap those orphans with no Codex-specific process logic.
