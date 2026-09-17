# Synthetic `.heddle.yaml` Variants

Each directory holds one project configuration for a distinct loader case.
They live outside `tests/fixtures/workspaces/` because malformed fragments are
not complete host repositories.

| Directory | Case |
| --------- | ---- |
| `empty/` | empty file uses the complete defaults table |
| `unknown-keys/` | unknown section and layout key produce advisory diagnostics |
| `malformed/` | unparsable YAML produces `workspace-invalid` |
| `wrong-type-layout/` | non-string `layout.tests` names the invalid key |
| `wrong-type-command/` | non-string `commands.test` names the invalid key |
| `wrong-type-agent/` | non-boolean `agents.codex` is rejected |
| `wrong-type-gates/` | non-list `gates.enabled` is rejected |
| `narrowed-golden/` | removing one gate narrows the required gate tuple |

Project-discovery cases use the synthetic hosts and temporary directories
rather than additional committed assets.
