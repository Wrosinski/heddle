# Invalid State Files

Deliberately invalid `state.yaml` variants — the `workspace-invalid`
validation corpus for the kernel state reader and the `validate` command.
These live outside `tests/fixtures/workspaces/` because the normal
corpus conformance suite (`tests/runtime/test_fixtures.py`) walks every
`state.yaml` under that root and these files violate the vocabulary by
design. Flat files, not workspaces — `read_state_file` takes a path.

## Coverage map

| File | Violation |
| ---- | --------------------- |
| `unparsable.yaml` | YAML does not parse (unclosed flow sequence) |
| `missing-required-key.yaml` | required scalar `revision` absent |
| `missing-collection-key.yaml` | top-level collection `sessions` absent |
| `unknown-stage.yaml` | `stage: shipping` outside the §2.1 slugs |
| `unknown-top-level-key.yaml` | extra top-level key `notes` |
| `unknown-nested-key.yaml` | milestone carries unknown key `owner` |
| `duplicate-current-tasks.yaml` | two tasks `current` in one milestone |
| `schema-ahead.yaml` | `schema: heddle.state/v99` (hint names upgrading the runtime, not `heddle migrate`) |
| `boolean-tier.yaml` | `tier: true` where an integer is required; other integer cases are built at test time |

The unreadable-file case is built at test time
in `tmp_path` (`tests/kernel/test_state.py`) — committed permission bits do
not survive git reliably, and teardown must restore the mode.
