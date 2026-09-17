"""
Shared fixtures for gate tooling tests.

Provides sample plans, specs, diffs, JSONL streams, and gate outputs
used across milestone test modules. Fixtures are designed so each
pipeline stage can be tested in isolation.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Sample plan / spec content
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_plan_content() -> str:
    return dedent("""\
        ---
        type: implementation-plan
        feature_spec: docs/features/data/sample-feature.md
        feature_name: Sample Feature
        status: in-progress
        tier: 2
        current_milestone: 2
        total_milestones: 3
        smoke_test: "poetry run pytest -q -x tests/smoke.py"
        test_command: "poetry run pytest -q -n auto tests/sample/"
        lint_command: "poetry run flake8 src/example/sample/"
        acceptance_test: "poetry run pytest -q tests/acceptance/test_sample.py"
        live_e2e_test: "poetry run pytest -m live tests/live/test_sample_e2e.py"
        live_e2e_prerequisites_confirmed: false
        created: 2026-03-01
        last_updated: 2026-03-20T10:00Z
        ---

        ## Quick Orientation

        Sample feature for gate tooling tests.

        ## Design Context

        <!-- Derived from spec. Last refreshed: 2026-03-15 (for milestones 2 and 3) -->

        ### Relevant Architecture

        Module at `src/example/sample/core.py`.

        ## Milestones

        ### Milestone 1: Foundation

        Status: complete
        Satisfies: AC-1, AC-2

        Tasks:
        - [x] Create `src/example/sample/core.py`
        - [x] Create `src/example/sample/models.py`

        ### Milestone 2: Integration

        Status: in-progress
        Satisfies: AC-3, AC-4

        Owned paths:
        - src/example/sample/core.py
        - config/sample/default.yaml

        Tasks:
        - [ ] Integrate with `src/example/sample/core.py`
        - [ ] Add `config/sample/default.yaml`

        ### Milestone 3: Polish

        Status: not-started
        Satisfies: AC-5

        Tasks:
        - [ ] Add error handling
    """)


@pytest.fixture()
def sample_spec_content() -> str:
    return dedent("""\
        ---
        type: feature-spec
        area: data
        feature_name: Sample Feature
        status: in-progress
        created: 2026-02-15
        last_updated: 2026-03-10
        ---

        ## Purpose

        A sample feature for testing gate tooling.

        ## Acceptance Criteria

        ### AC-1: Basic operation

        Precondition: Module is installed.
        Action: Call `process()` with valid input.
        Expected: Returns processed result.

        ### AC-2: Error handling

        Precondition: Module is installed.
        Action: Call `process()` with invalid input.
        Expected: Raises ValueError with descriptive message.

        ### AC-3: Integration

        Precondition: Pipeline is configured.
        Action: Run pipeline with sample config.
        Expected: Output written to expected path.

        ### AC-4: Config validation

        Precondition: None.
        Action: Load config with missing required field.
        Expected: Raises ConfigError at startup.

        ### AC-5: Performance

        Precondition: Dataset with 1000 rows.
        Action: Run process().
        Expected: Completes in under 5 seconds.
    """)


@pytest.fixture()
def sample_plan_path(tmp_path: Path, sample_plan_content: str) -> Path:
    p = tmp_path / "plans" / "sample-feature.plan.md"
    p.parent.mkdir(parents=True)
    p.write_text(sample_plan_content)
    return p


@pytest.fixture()
def sample_spec_path(tmp_path: Path, sample_spec_content: str) -> Path:
    p = tmp_path / "docs" / "features" / "data" / "sample-feature.md"
    p.parent.mkdir(parents=True)
    p.write_text(sample_spec_content)
    return p


# ---------------------------------------------------------------------------
# Sample diff content
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_diff_content() -> str:
    return dedent("""\
        diff --git a/src/example/sample/core.py b/src/example/sample/core.py
        index abc1234..def5678 100644
        --- a/src/example/sample/core.py
        +++ b/src/example/sample/core.py
        @@ -1,5 +1,10 @@
         def process(data):
        -    return data
        +    if not data:
        +        raise ValueError("empty input")
        +    return transform(data)
        +
        +def transform(data):
        +    return {k: v.strip() for k, v in data.items()}
        diff --git a/config/sample/default.yaml b/config/sample/default.yaml
        new file mode 100644
        --- /dev/null
        +++ b/config/sample/default.yaml
        @@ -0,0 +1,3 @@
        +sample:
        +  enabled: true
        +  threshold: 0.5
    """)


@pytest.fixture()
def sample_diff_path(tmp_path: Path, sample_diff_content: str) -> Path:
    p = tmp_path / "sample.diff"
    p.write_text(sample_diff_content)
    return p


# ---------------------------------------------------------------------------
# Sample Codex JSONL streams
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_codex_jsonl() -> str:
    """A clean Codex JSONL stream with tool events and a final message."""
    import json

    events = [
        {"type": "item.started", "item": {"type": "command_execution", "id": "cmd1"}},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "cmd1"}},
        {"type": "item.started", "item": {"type": "command_execution", "id": "cmd2"}},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "cmd2"}},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": (
                    "# Self-Review: sample-feature\n\n"
                    "**Verdict:** CLEAN\n\n"
                    "## AC Verification\n\n"
                    "| AC | Status |\n|---|---|\n"
                    "| AC-3 | Pass |\n| AC-4 | Pass |\n\n"
                    "## Issues Found\n\nNone.\n\n"
                    "## Enforcement Rule Compliance\n\n"
                    "All rules satisfied.\n\n"
                    "## Enforcement Extraction\n\nN/A\n\n"
                    "## Resolution Diagnostics\n\nN/A\n\n"
                    "## Summary\n\nAll acceptance criteria verified."
                ),
            },
        },
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


@pytest.fixture()
def corrupted_codex_jsonl(clean_codex_jsonl: str) -> str:
    """Codex JSONL with parse errors and transport messages mixed in."""
    lines = clean_codex_jsonl.strip().split("\n")
    corrupted = [
        lines[0],
        "Reconnecting...",
        '{"type": "item.completed", "item": {"truncated',  # parse error
        lines[1],
        "Transport error: network error",
        lines[2],
        lines[3],
        lines[4],
        lines[5],
    ]
    return "\n".join(corrupted) + "\n"


@pytest.fixture()
def empty_codex_jsonl() -> str:
    return ""


# ---------------------------------------------------------------------------
# Sample Claude stream-json
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_claude_stream_json() -> str:
    import json

    events = [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "thinking..."}]},
        },
        {
            "type": "result",
            "subtype": "success",
            "result": (
                "# Self-Review: sample-feature\n\n"
                "**Verdict:** CLEAN\n\n## Summary\n\nAll good."
            ),
        },
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


# ---------------------------------------------------------------------------
# Sample gate output files (valid / invalid)
# ---------------------------------------------------------------------------


@pytest.fixture()
def valid_self_review_output() -> str:
    import json

    from tests.structured_review_helpers import content

    value = content()
    row = value["details"]["ac_status"][0]
    value["details"]["ac_status"] = [
        {**row, "ac_id": identifier} for identifier in ("AC-3", "AC-4")
    ]
    return json.dumps(value)


@pytest.fixture()
def invalid_self_review_output(valid_self_review_output) -> str:
    import json

    value = json.loads(valid_self_review_output)
    del value["details"]["ac_status"]
    del value["details"]["assessment"]
    return json.dumps(value)
