<!-- heddle:begin session-entry -->
This repository's feature workflow runs through the `heddle` CLI.

To enter a feature-work session:

1. Run `heddle orient`.
2. Follow the `next_actions` it returns.
3. Run `heddle kickoff` only when `orient` routes there.
<!-- heddle:end session-entry -->

Engineering preferences are governed by `docs/workflow/engineering-principles.md`.

Feature execution records stay local under the paths declared in
`release/public-repository.toml`. Never stage or force-add those records.
Keep proposals in the ignored proposal directory; publish maintained product
documentation separately from implementation history. Follow the repository
policy in `docs/workflow/local-records.md` even when packaged guidance permits
optional record commits. Tests use synthetic fixtures, never local run evidence.

Test selection, execution authority, design and maintenance are governed by
[the testing strategy](docs/workflow/testing-strategy.md). Consult its current
capability notes and [selection map](docs/testing/test-selection-map.md) before
choosing commands. Repository pytest policy is active: plain pytest selects the
fast band, while formal proof uses
`.venv/bin/python -m tests.proof_runner <exact-targets>`.

Run narrow relevant hermetic tests, configured lint and applicable pre-commit
checks as part of the requested work. Fix failures caused by the change and
rerun affected checks without asking at each step. Broad suites and both local
full-workflow e2e and real-provider tests require explicit execution authority
for that scope; existing applicable grants persist. Prerequisites and inherited
plan commands are not permission. Preserve required unrun proof as pending and
inspect nested launches before execution. Use `--test-band e2e --allow-e2e`
or `--test-band live --allow-live --allow-e2e` only for an exactly authorized
file or node selection.

Committing: use `scripts/git-commit-retry.sh <git-commit args>` — when pre-commit hooks reformat
staged files it re-stages exactly those files and retries once; any other failure propagates
unchanged.
Keep behavior changes and mechanical changes (formatting, renames, moves) in
separate commits.
