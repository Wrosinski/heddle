# Contributing to Heddle

Heddle is open-source software without a support commitment. Contributions are
welcome when they preserve the documented contracts and include focused evidence.

Use Python 3.13 or newer. Create an isolated environment, install the development
extra, and install the commit, message, and push hooks:

```bash
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
.venv/bin/python scripts/install-repository-hooks.py
```

Before editing, read `AGENTS.md`, the engineering principles, testing strategy,
and the affected implementation and consumers. Keep behavior changes separate
from mechanical moves or formatting. Add tests for observable contracts and
meaningful failure cases. Formal proof uses
`.venv/bin/python -m tests.proof_runner <exact-targets>`; run pre-commit on the
complete tree before proposing a change.

Do not include credentials, private workflow records, generated environments, or
provider transcripts. Do not run live providers, publish artifacts, or mutate a
remote unless the repository owner explicitly authorizes that action.
Follow the [local workflow records policy](docs/workflow/local-records.md).
Releases follow the [release process](docs/workflow/release-process.md).

Bug reports and proposals should state the current behavior, desired behavior,
minimal reproduction, environment, and relevant evidence. Security reports use
`SECURITY.md`, not a public issue.
