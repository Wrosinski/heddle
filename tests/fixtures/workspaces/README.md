# Fixture Workspaces

These directories are synthetic Git hosts used to exercise project discovery,
state validation, migration, and workflow derivation without a live adopter.
Every domain-bearing value uses the `example` namespace.

## Hosts

- **`tiny/`** contains one active feature and the minimum project
  configuration. It exercises single-workspace selection and the compact
  configuration form.
- **`golden/`** resembles a larger adopter with configured commands, an
  `src/example` source root, multiple specs, two feature workspaces, and a
  legacy input pair.
- **`edge/`** contains vocabulary-conformant workspaces that each arm one
  blocking-condition, gate-convergence, or co-occurrence case. Tests select
  them by explicit slug, so the host's multiple workspaces are intentional.
- **`completed-boundary/`** contains an accepted feature used to exercise
  retained completion evidence and review artifacts.

Deliberately invalid states live in `tests/fixtures/invalid-states/`, and
synthetic project-configuration variants live in `tests/fixtures/configs/`.

## Golden-host coverage

| Behavior | Location |
| -------- | -------- |
| Multi-milestone state with expanded tasks | `golden/plans/nl-screening/state.yaml` |
| Multiple review runs and retained review artifacts | `golden/plans/nl-screening/reviews/` |
| An acceptance criterion deliberately absent from every milestone | `golden/docs/features/analysis/nl-screening.md` and `golden/plans/nl-screening/state.yaml` |
| Legacy one-document plan and dotted review artifact | `golden/plans/legacy-feature.plan.md` and `golden/plans/legacy-feature.self-review-m1.md` |
| Multiple workspaces with no recorded pointer | `golden/plans/` |
| Empty Tier 1 milestone skeleton | `golden/plans/tier1-quickfix/state.yaml` |

The corpus also includes an on-disk verification log, resolved decision and
session facts, a spec-review fact with an artifact, and both minimal and full
project configurations.

## Edge-host derivation coverage

Each workspace below arms one current derivation.

| Workspace | Expected behavior |
| --------- | ----------------- |
| `blocked-not-authorized` | reports `not-authorized` |
| `blocked-two-current` | reports `milestone-out-of-sequence` and projects the first milestone |
| `blocked-deps-unmet` | reports `milestone-out-of-sequence` for unmet dependencies |
| `blocked-task-incomplete` | reports `task-incomplete` |
| `blocked-verification-missing` | reports `verification-missing` |
| `blocked-pending-decision` | reports `pending-decisions` |
| `gate-stage-latest-fail` | reports `gate-not-converged` for a latest failing run |
| `gate-stage-append-order` | uses append order rather than timestamps |
| `gate-stage-converged` | routes to `phase-exit` |
| `gate-run-missing` | reports `gate-not-converged` for a required absent run |
| `gate-all-converged` | routes to `phase-exit` |
| `stacked-family-a` | reports authorization before pending decisions |
| `stacked-cross-family` | reports pending decisions before gate convergence |

The shared spec target is `docs/features/example/edge-cases.md`. The synthetic
`legacy-corpus/` directory exercises isolation of records from closed features.
