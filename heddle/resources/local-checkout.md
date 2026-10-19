# Local checkout

Git ignores this project's `.heddle.yaml`: the owner keeps Heddle local to this
clone, because the repository's other contributors do not use it. Every Heddle
file stays untracked and hidden. This guidance takes precedence over any
briefing that conflicts with it.

- Never stage a Heddle file, and never force-add one with `git add -f`.
- Keep Heddle out of everything the other contributors see. Code, tests, tracked
  docs, commit messages and pull requests carry no Heddle path (`.heddle*` and
  the configured feature, plan and principles paths) and no AC, finding or
  decision ID. In particular:
  - Bind tests to ACs only through the Feature Spec's `Verified-by:` routes, and
    name each test for the behavior it checks.
  - Describe a review remediation by its change in the commit message, and keep
    its finding references in Heddle's records.
  - When completion asks you to update repository docs, change only the tracked
    docs the other contributors need, without Heddle references.
- Run `heddle init` only with `--no-session-entry`.
- Keep research notes and command payload files under `.heddle/scratch/`: source
  coverage counts every other untracked, unignored file as a change.
- Feature records exist only in this checkout. `git worktree remove` and
  `git clean -dfx` delete them without warning, so finish or copy out a feature
  first.
- After merging the shared branch, attribute the other contributors' changes as
  the implementation phase describes, citing a new
  `.heddle/scratch/upstream-sync-<n>.md` note for each merge. Never edit or
  delete a cited note.
