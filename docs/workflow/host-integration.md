# Integrate Heddle into a host repository

This guide defines the portable boundary between Heddle and a repository that
uses it. The host owns its code, instructions, principles, test policy,
credentials, deployment process, and release authority. Heddle owns its CLI
state and the managed regions and files declared by its adoption contract.
Scaffold-once targets such as `.heddle.yaml` and the engineering-principles seed
become host-owned after creation. `.heddle.lock` records adoption-time facts; it
does not give Heddle continuing ownership of those files.

Use the public `heddle` commands shown here. Do not edit a feature's
`state.yaml` or other command-owned operational state by hand. For the feature
lifecycle after adoption, use the [workflow reference](workflow.md).

## Check prerequisites and assess the host

Use Python 3.13 and install [uv](https://docs.astral.sh/uv/). Work from an
existing Git repository. Confirm that the selected agent runners are available,
and install the host application's own dependencies through its normal package
manager.

Before adoption, identify the host's source, test, schema, prompt, specification,
and planning paths. Read its root instructions and governing principles. Decide
which verification commands are safe, focused, and hermetic, and identify which
external, broad, production, or publication actions require separate authority.

## Choose one installation mode

Choose exactly one of these supported modes. Replace every angle-bracket
placeholder with a value for your environment.

### Editable checkout

Use an editable checkout when Heddle itself is under active development and the
host should observe source changes without rebuilding a package.

```bash
uv tool install --force --editable --python 3.13 <checkout>
```

### Pinned Git

Use a full commit SHA when the repository needs a reproducible Git checkpoint.

```bash
uv tool install --force --refresh --python 3.13 "heddle @ git+https://github.com/Wrosinski/heddle.git@<full-commit-sha>"
```

### Reviewed wheel

Use an absolute path to the exact reviewed artifact for release-style or
installed-package qualification.

```bash
uv tool install --force --python 3.13 /absolute/path/to/dist/heddle-<version>-py3-none-any.whl
```

The command is normally available from uv's tool binary directory. A repository
may wrap that path for repeatable setup, but the wrapper should still bind all
qualification commands to the selected executable.

## Adopt a repository for the first time

Create a checkpoint of the host before Heddle writes anything, then inspect the
working tree:

```bash
git status --short
```

Read the existing `AGENTS.md` and any agent-specific instruction files. Read the
host's `docs/workflow/engineering-principles.md` if it exists, along with the
repository's test and release guidance. Host-authored instructions and
principles remain authoritative outside Heddle's bounded managed regions.

Preview the complete adoption footprint:

```bash
heddle init --dry-run
```

Inspect every target's proposed create, accept, integrate, refuse, or skip result.
Review each refused target and resolve it explicitly by relocating or renaming
the proposed target, choosing another path, or otherwise repairing the conflict;
do not overwrite or remove occupied content just to satisfy initialization.
When the configuration and principles are already host-authored and the adoption
lock is missing, explicitly preserve and adopt them:

```bash
heddle init --adopt-existing --dry-run
heddle init --adopt-existing
```

Review the preview before applying. This option records the existing regular
files' bytes without replacing them or changing their ratification status.
Configuration still must parse; symlinks, invalid managed markers, conflicting
instruction mirrors and malformed locks still refuse. Missing targets receive
normal scaffolds. Existing valid locks retain their original hashes. The option
does not reset adoption history or suppress subsequent doctor diagnostics.

Resolve structural conflicts explicitly and preview again; do not remove
host-authored content just to obtain a successful initialization.

Apply only after the preview is understood and every refusal is resolved:

```bash
heddle init
```

Review the resulting diff. Heddle writes its lock last and preserves host bytes
outside its managed markers. The host must review and ratify the generated
engineering-principles seed; `init` does not ratify it.

## Configure the host repository

`.heddle.yaml` is the repository-specific integration contract. Keep its values
portable within the host rather than copying paths or commands from another
repository unchanged.

- Set `layout.specs`, `layout.plans`, `layout.prompts`, `layout.schemas`,
  `layout.tests`, and `layout.source` to the host's actual directories.
- Set `commands.test`, `commands.lint`, and `commands.typecheck` to the host's
  real verification entry points. These values describe commands; they do not
  grant permission to run broad, external, or paid scopes.
- Enable the available runners with `agents.claude` and `agents.codex`.
- Set `sync.mirror` to an agent-instruction mirror such as `CLAUDE.md`, or set it
  to `null` when the host does not want a mirror.

Host tooling owns `.heddle.yaml`'s `autopilot.test_command`, which configures
the additional close suite run by `heddle feature complete`. It does not replace
feature proof, and `feature complete` does not create a clean environment; it
runs the command in the current host checkout. Choose a command that creates or
activates its own isolation when the host requires one.

Keep repository instructions in `AGENTS.md` outside the Heddle-managed session
entry. Those instructions retain test authority and all permission boundaries
for provider access, production mutation, push, deployment, and publication.
Heddle does not infer any of those permissions from populated commands,
credentials, a phase transition, or a review schedule.

Select a workflow only through the CLI. For an existing feature, use
`heddle flow set <flow> --feature <slug>` and follow the command's current help
and revision requirements. Do not edit the flow or review matrix in operational
state.

## Verify the integration

Start with diagnostics, then validate repository state, then ask for the next
legal workflow action:

```bash
heddle doctor --json
heddle validate
heddle orient
```

On a healthy repository with no active feature, `heddle orient` may return exit
3 and route to `heddle feature prepare`. That is a legal and expected fresh-host
outcome rather than an integration failure.

Fresh checkouts do not need historical verification logs or raw archives from
accepted features. Missing logs are informational in `doctor` and `validate` by
default, including explicit historical feature checks. Retained structured
ledgers, canonical reviews, and verification manifests still undergo integrity
validation. Active work and acceptance continue to require their evidence.

Treat a fatal diagnostic as a real blocker. Resolve or repair its stated cause
before feature work, then rerun the affected checks. Informational runner and
installation diagnostics describe the environment; they do not prove package
provenance or grant execution authority.

When `orient` routes to feature preparation, research the proposed work, prepare
its intake through `heddle feature prepare`, confirm the returned policy through
the native feature-policy command, and start the feature only after the owner has
approved that complete policy.

Heddle installs its runtime and packaged resources. It does not copy the source
repository's feature workspaces or development-run evidence into the host.
Regression tests create their own inputs and need no prior feature runs.

## Add another host

Install the same selected Heddle version or checkout on the new machine. Clone
the host repository, install its application dependencies, and verify that its
agent runners and credentials meet the host's own policy. Do not run `init` again
merely because the machine is new: the integration footprint belongs to the Git
repository and should already be present.

Run the verification sequence above from the cloned host. Compare the reported
package location and interpreter with the installation mode you selected. If the
host repository is not yet adopted, follow the first-adoption procedure once and
commit that integration through the host's normal review process.

## Operate features normally

Begin each work session with `heddle orient` and follow its ordered
`next_actions`. Run `heddle kickoff` only when `orient` routes there. Use native
commands for tasks, decisions, reviews, verification, sessions, milestones, and
phase transitions. Commit useful checkpoints with the host's own commit process.

A configured verification command is a workflow fact, not permission to execute
it. The host's instructions continue to decide whether broad suites, live
providers, external effects, production changes, pushes, or publication are
allowed.

## Update Heddle safely

Checkpoint the host and any editable Heddle checkout before changing the active
runtime. Record the current runtime provenance, including the selected
installation mode, executable path, package location, interpreter, and commit or
artifact identity.

For an editable runtime, committed Python source and packaged resources are
visible to a fresh process without reinstalling. Stop long-lived Heddle
processes before relying on those edits so an older imported module cannot remain
resident.

For a selected package such as a pinned Git revision or reviewed wheel, reinstall
when the version changes. Reinstall an editable checkout as well after package
metadata, dependency metadata, build configuration, or an entry point changes.
Those installation-time inputs are not refreshed by ordinary source import.

Start a fresh process and confirm the active runtime:

```bash
heddle doctor --json
```

Run affected verification before you rely on new evidence produced by the
updated runtime. A new Heddle version never silently rewrites lock-tracked host
documents; installation and projection remain separate operations.

### Active-feature projection

When an active feature exists, preview package-resource projections before you
apply them. `heddle sync` requires that active workspace:

```bash
heddle sync --dry-run
heddle sync
```

Inspect the sync preview and apply only approved managed-region changes.
Current sync targets are the plan-status region and the root AGENTS session-entry
region; mirror detection does not provide a mirror writer.

### No active feature

With no active feature, do not create artificial feature work merely to run
sync. Confirm the repository remains healthy instead:

```bash
heddle validate
heddle orient
```
