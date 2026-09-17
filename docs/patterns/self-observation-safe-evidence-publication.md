---
name: self-observation-safe-evidence-publication
area: workflow
status: active
created: 2026-07-14
superseded_by: null
---

# Self-observation-safe evidence publication

**Intent:** let a command run tests or other observers over a worktree without
the command's own in-flight evidence changing what those observers see.

**When to use:** a command launches a subprocess whose checks inspect the live
repository (ownership rails, clean-tree checks, file inventories) and must
archive stdout/stderr or another proof artifact inside that repository. It is
not needed when the observer cannot see the destination or when evidence is
kept permanently outside the inspected tree.

**Recipe:**

1. **Capture outside the observed tree.** Open a unique temporary capture in an
   external temporary directory before launching the subprocess. Do not create
   the final log, a placeholder, or an in-repository staging file while the
   observer is running.
2. **Complete and close the capture first.** Wait for the subprocess, flush and
   close its output, and retain the real exit code. Capture failure is a typed
   command failure, not partial evidence.
3. **Stage beside the destination.** Copy the completed bytes to a unique
   destination-sibling stage. This puts the final publication on the target
   filesystem and preserves raw bytes without exposing a partially written
   final path.
4. **Publish atomically.** Install the sibling stage with the repository's
   atomic write primitive, then clean the stage. Only a fully completed
   artifact becomes visible at the final path.
5. **Record the fact after publication.** Append the control-plane fact only
   after the final artifact exists. Capture or publication failure records no
   fact and returns a typed, actionable error.
6. **Keep evidence raw through hooks.** Exclude evidence paths only from hooks
   that mutate text (for example trailing-whitespace and EOF normalization).
   Continue to run size, secret, schema, and other non-mutating semantic hooks.
7. **Test the observation boundary.** During the spawned command, assert that
   the repository contains neither final nor staging evidence. Inject capture,
   copy, install, and cleanup failures; prove no dangling fact and no partial
   final artifact. Also lock the narrow hook exclusions.
8. **Reserve a unique final destination per attempt.** Generate the final
   identity once before execution and carry that exact value through capture,
   publication, and fact construction. Concurrent attempts must never share a
   timestamp-only final name. If publication wins but the later CAS append
   loses, the complete orphaned artifact remains non-authoritative and may be
   cleaned independently; never reinterpret it as the winner's evidence.

**Anti-patterns / caveats:** committing before execution does not solve this if
the command opens its log in the worktree before the observer runs. Writing
directly to the final log path makes the observer self-referential. Capturing
externally but recording the fact before publication creates a dangling
reference. A repository-global hook exclusion is also too broad because it can
skip security or semantic checks. Atomic replacement here is process-level;
power-loss durability still requires file and directory synchronization when
the owning contract demands it. Atomic publication also does not make a
published artifact authoritative: only its successfully committed fact does.

**Concrete future-feature scenario:** a future `heddle benchmark` command runs
a suite that rejects unexpected worktree paths and then stores a raw benchmark
transcript under the active feature workspace. It captures the transcript in
the system temporary directory, closes it after the suite exits, atomically
publishes it beside the final transcript path, and only then records the
benchmark fact. The suite never observes its own transcript.
