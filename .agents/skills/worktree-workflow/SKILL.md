---
name: worktree-workflow
description: |
  Set up an isolated git worktree for new, clearly scoped work, or audit an existing worktree
  before cleanup or removal. Use for separate features, implementations, documentation efforts,
  and worktree-lifecycle safety checks. Do not use for adjustments or fixes to current branch work,
  or for general exploration and review unrelated to a worktree's lifecycle.
---

# Git Worktree Workflow

Give separate work its own worktree, and check a worktree with fresh evidence
before it is removed.

## Approach

- **A worktree is for separate work.** Create one for a new feature, a
  standalone implementation, or an independent documentation effort that
  deserves its own branch. Stay in the current worktree for adjustments, fixes,
  review, investigation, or small work that belongs to what is under way there.
- **Creating and removing are separate permissions.** Permission to create a
  worktree does not cover merging it, discarding it, or removing it later.
- **Decide removal from evidence gathered now.** A handoff, a
  remembered divergence count, a branch name, or an earlier clean status does
  not count. Gather the evidence for each worktree separately.
- **Ask before anything that can lose work.** These need the user's go-ahead:
  removing a worktree; deleting by hand or with force (`--force`, `-D`);
  treating a squash- or rebase-merged branch as merged; and discarding ignored
  files that no repository rule declares disposable. Creating a worktree and
  every read-only command below need none. Fetch, merge, push, prune, and
  deleting a remote branch are not cleanup steps; leave them out.
- **How worktrees behave.** Commits are shared. Each worktree has its own
  working directory, index, and `HEAD`, and its own installed dependencies and
  build artifacts. The stash list is shared. A branch can be checked out in
  only one worktree at a time. Never create a worktree inside another
  worktree's directory tree; removing the outer worktree can delete the inner
  one's files.

## Steps: create a worktree

1. List the registered worktrees and choose the local start point:

   ```bash
   git worktree list --porcelain
   git worktree add -b <branch> ../<branch> <start-point>
   ```

   For a local branch that already exists:

   ```bash
   git worktree add ../<branch> <branch>
   ```

2. Enter the new directory and follow the repository's own setup instructions.
   Check that its documented interpreter or toolchain works before you edit.
   This skill adds no permission and no restriction for network access,
   installing dependencies, or credentials; the task and the repository's
   instructions decide those.
3. Work only inside that worktree.

## Steps: check a worktree before removal

The check is read-only and uses local Git data. Passing it makes a worktree
eligible; the removal still needs the user's go-ahead.

1. **Name the inputs.** Pick one registered worktree and one explicit local
   integration ref. Use a local ref, not a remote-tracking one, and do not
   contact a remote.
2. **Confirm the target.** Start from another worktree. Resolve the target to
   an absolute path, match it to one entry in the listing, and confirm Git
   reports the same top level:

   ```bash
   git worktree list --porcelain
   git -C <target> rev-parse --show-toplevel
   ```

   Stop if the path does not match, the worktree is not registered, another
   process is writing to it, its submodules or index are in a state you did
   not expect, or you cannot tell which directory the user meant. Report the
   exact target and what you saw.
3. **Check that its commits are in the integration ref.** Resolve both sides
   to current object IDs:

   ```bash
   git -C <target> rev-parse --verify HEAD^{commit}
   git -C <target> rev-parse --verify <integration-ref>^{commit}
   git -C <target> rev-list --left-right --count <integration-oid>...<worktree-oid>
   git -C <target> merge-base --is-ancestor <worktree-oid> <integration-oid>
   ```

   For the last command, exit `0` means the worktree's commit is contained in
   the integration commit, exit `1` means it is not, and a higher exit code
   means the check failed. The divergence count describes the branch; it does
   not decide removal.

   A branch that is not contained stays blocked. If it was squash-merged or
   rebased, only current human confirmation can establish that the commits are
   squash/rebase-equivalent; similar messages or diffs do not. Even then the
   four file lists must be empty, and the user decides that cleanup itself.
4. **List the local files.** Run all four lists in the target:

   ```bash
   git -C <target> diff --name-status --
   git -C <target> diff --cached --name-status --
   git -C <target> ls-files --others --exclude-standard
   git -C <target> ls-files --others --ignored --exclude-standard
   ```

   They cover unstaged tracked, staged, untracked, and ignored files. Use `-z`
   when a program parses the output. An entry in any list
   blocks ordinary removal until its owner saves it or says it can go.

   `.gitignore` does not make a file safe to delete. Leave an ignored file out
   of the count only when an exact rule in the repository declares that path
   disposable, or the current owner confirms it can go for this cleanup. A
   convention, a guess from the filename, or what another worktree contains is
   not enough.
5. **Check again, then remove.** Immediately before removal, repeat the target
   check, both object IDs, the divergence count, the containment check, and
   all four file lists. If anything changed, stop and report the new evidence.

   A worktree is eligible only when all three hold: it is still registered,
   its current commit is contained in the current local integration commit,
   and the four file lists are empty. With the user's go-ahead, run from
   another worktree:

   ```bash
   git worktree remove <target>
   ```

6. **Delete the merged local branch.** Go-ahead to remove the worktree of
   finished, merged work also covers its local branch. Once the removal
   succeeds, delete the branch from the checkout whose `HEAD` is the
   integration ref, because without an upstream `-d` checks containment
   against the current `HEAD`:

   ```bash
   git branch -d <branch>
   ```

   `-d` refuses a branch that is not merged. Such a branch, for example a
   squash- or rebase-merged one, needs the user's decision; do not force it
   with `-D` on your own. Remote branches are out of scope.

If the worktree is not eligible or the go-ahead is missing, leave the worktree
and its branch as they are and report the exact blocker.
