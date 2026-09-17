# Local workflow records

Heddle's development records remain local to the checkout. The repository
contains source, tests, packaged prompts and scaffolds, configuration, and
maintained product documentation. The publication policy owns the exact private
path categories in `release/public-repository.toml`.

Intake, proposals, feature specifications and their index, plans, state,
decisions, reviews, receipts, feedback logs, trajectories, and completion
archives are private workflow records. Store proposals in the ignored proposal
directory. Promote useful conclusions into maintained documentation through an
explicit scoped change; do not publish the execution history alongside it.
Packaged templates and synthetic test fixtures remain versioned.

Run `.venv/bin/python scripts/install-repository-hooks.py` after development
environment setup. It installs the standard commit/message hooks, a dedicated
push hook that reads every ref update, and missing local record scaffolds.
Existing record contents and unfamiliar push hooks are preserved. Re-run setup
in a new clone; Git does not distribute installed hooks or local evidence.

The commit check inspects the index, including force-added files. The push check
examines each new commit, not just the final tree, so committing a record and
then deleting it does not make it publishable. CI repeats the commit-history
check with full history. The policy records an immutable already-published
baseline; historical commits are not rewritten, and new private-path changes
after that baseline are rejected. Deletion-only cleanup remains allowed.

Ignore rules and hooks protect the normal workflow, not a hostile contributor
who deliberately bypasses hooks or changes the policy. CI rejection happens
after upload and cannot replace the local push check. Review changes to this
boundary like other publication-policy changes.

Completion, verification and local resume do not require committing their
records. Keep source commits as usual; record evidence remains content-bound.
Local evidence does not travel with branch pushes or fresh clones. Back up or
transfer needed feature specifications, intake, workspace state, referenced
evidence, shared feedback and archives together to a private destination under
the owner's authority. Do not assume a workspace archive contains every
supporting file. No automatic backup, external storage, or cleanup is configured.
