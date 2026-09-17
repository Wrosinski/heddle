---
name: worktree-workflow
description: |
  Set up an isolated git worktree for new, clearly scoped work, or audit an existing worktree
  before cleanup or removal. Use for separate features, implementations, documentation efforts,
  and worktree-lifecycle safety checks. Do not use for adjustments or fixes to current branch work,
  or for general exploration and review unrelated to a worktree's lifecycle.
---

# Git Worktree Workflow

Use a dedicated worktree for genuinely separate work. Treat creation and
cleanup as different authority boundaries: creating an isolated workspace does
not imply permission to integrate, discard, or remove it later.

## When to use

Create a worktree for a new feature, standalone implementation, or independent
documentation effort that warrants its own branch. Continue in the current
worktree for adjustments, fixes, review, investigation, or small work belonging
to the active context.

## Create and set up

Inspect the registered worktrees and choose the intended local start point:

```bash
git worktree list --porcelain
git worktree add -b <branch> ../<branch> <start-point>
```

For an already-existing local branch:

```bash
git worktree add ../<branch> <branch>
```

Enter the new directory and follow the repository's own setup instructions.
Confirm its documented interpreter or toolchain works before editing. Do not
infer remote access, dependency installation, or credential use from this
skill; those actions need their ordinary task authority.

Work only inside the intended worktree. Commits are shared through Git's object
store, but each worktree has its own working directory, index, and `HEAD`.

## Cleanup is a fresh audit

Never decide cleanup from a handoff, remembered divergence count, branch name,
or earlier clean status. Select one explicit registered worktree and one
explicit local integration ref, then recompute the evidence below. The audit is
read-only and does not itself authorize removal.

### 1. Validate the exact target

Start from another worktree. Resolve the proposed target to an absolute path,
match it to one entry from the porcelain listing, and confirm Git agrees on its
top level:

```bash
git worktree list --porcelain
git -C <target> rev-parse --show-toplevel
```

Stop on a path mismatch, missing registration, active writer, unexpected
submodule state, unsupported index condition, or any ambiguity. Report the
exact target and observed state; do not guess which directory the user meant.

### 2. Resolve current integration evidence

Resolve both sides to current object IDs. The integration ref must be local and
explicit; do not substitute a remote-tracking ref or contact a remote.

```bash
git -C <target> rev-parse --verify HEAD^{commit}
git -C <target> rev-parse --verify <integration-ref>^{commit}
git -C <target> rev-list --left-right --count <integration-oid>...<worktree-oid>
git -C <target> merge-base --is-ancestor <worktree-oid> <integration-oid>
```

Containment exit `0` means the worktree commit is reachable from the resolved
integration commit. Exit `1` means it is not contained. Any greater exit code
is an audit failure. Divergence is descriptive evidence, never disposal
authority by itself.

An uncontained branch stays blocked. If the commits were squash-merged or
rebased, only current human confirmation may establish that they are
squash/rebase-equivalent. That exception still requires an empty effective
inventory and a separate, case-specific cleanup decision; it is never inferred
from similar messages or diffs.

### 3. Inventory every local-file class

Run four independent inventories in the target:

```bash
git -C <target> diff --name-status --
git -C <target> diff --cached --name-status --
git -C <target> ls-files --others --exclude-standard
git -C <target> ls-files --others --ignored --exclude-standard
```

These cover unstaged tracked, staged, untracked, and ignored content. Use `-z`
for machine parsing. An entry in any class blocks ordinary removal until its
owner preserves or explicitly dispositions it.

`.gitignore` is not disposal authority. Exclude ignored content from the
effective inventory only when an exact repository-owned declaration identifies
that path as disposable, or the current owner explicitly confirms disposal for
this cleanup. A convention, filename guess, or another worktree's contents is
not enough.

### 4. Recheck, then stop or request removal authority

Immediately before any removal, repeat target validation, both object IDs,
divergence, containment, and all four inventories. If any result changed, stop
and report the new evidence.

The ordinary eligible case is conjunctive: the exact target is still
registered, its current commit is contained by the current local integration
commit, and its effective inventory is empty. Even then, removal is a
separately authorized action. When authorized, run from another worktree:

```bash
git worktree remove <target>
```

This audit does not authorize fetch, merge, push, remote contact, branch
deletion, force removal, manual deletion, or prune. Do not infer any of those
actions as cleanup steps. If the ordinary eligibility proof or the requested
authority is absent, leave the worktree intact and report the exact blocker.

## Stable rules

- A branch can be checked out in only one worktree at a time.
- Never create a worktree inside another worktree's directory tree.
- The stash list is shared; installed dependencies and build artifacts are
  worktree-local.
- Containment proves commit reachability, not that local files are disposable.
- Cleanup evidence is live and per target; repeat it for every worktree.
