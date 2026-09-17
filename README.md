# Heddle

Heddle is an open-source local workflow runtime for LLM-assisted software
engineering. It combines flexibility with formal verification: agents can adapt
their work to the repository while Heddle records explicit workflow state,
review obligations, and content-bound verification evidence.

Heddle requires Python 3.13 or newer. It uses Codex and Claude Code by default;
the selected command-line runners must already be installed and authenticated.
The project is provided under the MIT License with no support, no guarantees,
and no warranty. Use it at your own responsibility and review every command
before allowing it to change a repository or call an external service.

The maintained [architecture](docs/design/architecture.md),
[workflow model](docs/design/workflow-model.md), and
[testing strategy](docs/workflow/testing-strategy.md) describe the product
contracts.

## Install Heddle for adoption

Choose exactly one installation mode. The canonical
[host-integration guide](docs/workflow/host-integration.md) describes the full
setup, configuration, verification, and update boundaries.

### Editable checkout

Use this while developing Heddle from a local checkout:

```bash
uv tool install --force --editable --python 3.13 <checkout>
```

### Pinned Git commit

Use a reviewed full commit SHA for a reproducible Git installation:

```bash
uv tool install --force --refresh --python 3.13 "heddle @ git+https://github.com/Wrosinski/heddle.git@<full-commit-sha>"
```

### Reviewed wheel

Use an absolute path to the exact wheel that was reviewed:

```bash
uv tool install --force --python 3.13 /absolute/path/to/dist/heddle-<version>-py3-none-any.whl
```

## Adopt a host

Follow the [host-integration guide](docs/workflow/host-integration.md) for the
complete contract. From the intended Git host, preview and then apply the
repository footprint:

```bash
heddle init --dry-run
heddle init
```

If `AGENTS.md` already exists, Heddle integrates one bounded managed region and
preserves all host-authored bytes outside it. It refuses malformed markers or
occupied scaffold targets instead of overwriting them.

For an existing host configuration and principles file without an adoption lock,
preview `heddle init --adopt-existing --dry-run`, then run
`heddle init --adopt-existing` after reviewing the result. This preserves those
files and records their current hashes; it does not ratify principles or bypass
invalid configuration, marker faults, or mirror conflicts.

The adopter must author and ratify
`docs/workflow/engineering-principles.md`. Set `status: ratified` only after the
adopter has reviewed and authored the principles; then use doctor to validate
the installation:

```bash
heddle doctor
```

Create the first feature only after configuration and principles are ready.
Start with orientation, prepare the researched intake it requests, review and
confirm the complete returned policy, and only then admit the feature:

```bash
heddle orient
heddle feature prepare <slug> --area <area> --from-file <intake.yaml>
heddle feature policy <slug> --from-file <policy.yaml> --expect-revision <revision>
heddle feature start <slug> --area <area> --expect-revision <revision>
heddle orient --feature <slug>
```

Owner approval of the complete policy is required before the `feature policy`
confirmation. Use the current revision returned by each mutating command for the
next command's revision guard.

## Configuration and overrides

`.heddle.yaml` defines the host layout, verification commands, enabled Codex and
Claude Code runners, and instruction mirror. Repository instructions continue to
control execution authority. Review policy overrides are recorded per feature
through Heddle's policy commands; populated commands or credentials never grant
permission by themselves. Never edit command-owned `state.yaml` files directly.

Start each work session with `heddle orient` and follow its `next_actions`. Run
`heddle kickoff` only when orientation routes there. Use `heddle help` for the
current command surface and `heddle status --feature <slug>` for operational
facts.

Heddle never silently rewrites lock-tracked host documents when installations
switch. Preview any package-resource projection with `heddle sync --dry-run`
before applying it with `heddle sync`.

## Update Heddle safely

1. stop long-lived Heddle processes, especially `heddle drive`.
2. checkpoint the host in Git and record current Heddle provenance.
3. edit and verify Heddle in its own checkout or select a reviewed immutable artifact.
4. start a fresh CLI process in the selected mode after reinstalling.
5. run `heddle doctor` and resolve fatal diagnostics.
6. follow the [guide's active-feature or no-active-feature update route](docs/workflow/host-integration.md); projection application requires an active feature.
7. resume after runtime/state compatibility is confirmed with `heddle status` and `heddle validate`.

live Python processes do not reload package changes; a fresh process is the
boundary that activates an updated installation.

## Develop and verify

Create a Python 3.13 environment and install the development dependencies:

```bash
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
.venv/bin/python scripts/install-repository-hooks.py
```

Use the [test-selection map](docs/testing/test-selection-map.md) to select
relevant checks. Formal proof runs through
`.venv/bin/python -m tests.proof_runner <exact-targets>`. Plain pytest selects
the fast band, while E2E and live tests require their explicit allow flags and
repository authorization. Run all configured repository checks with:

```bash
.venv/bin/pre-commit run --all-files
```

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. Report a
security vulnerability through the process in [SECURITY.md](SECURITY.md). These
channels do not create a support commitment.

Heddle is copyright Wojtek Rosinski and distributed under the
[MIT License](LICENSE).
