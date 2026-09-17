"""
Literal value-locks for the shared test-policy constants.

authoring code-quality reader review, option 3: the scanning tests consume the
shared constants in tests/runtime/policy_helpers.py; THIS file re-pins
their exact contents as independent literals. A mistaken helper edit
cannot silently align every consumer — it fails here until the policy
growth is re-pinned deliberately alongside the spec that ratified it.
"""

from __future__ import annotations

from tests.runtime.policy_helpers import WRITE_SEAMS


def test_write_seam_allowlist_matches_the_ratified_set() -> None:
    assert WRITE_SEAMS == {
        "pointer.py": {"write_active_feature_pointer"},
        "state_store.py": {"_write_text"},
        "write_path.py": {"stamp_lifecycle"},
        # W5 AC-4/5: accepted-identity stamping, verified raw archive and
        # deletion only after exact committed retention.
        "completion.py": {
            "_stamp",
            "_publish_archive",
            "_cleanup",
            "cleanup_artifacts",
        },
        # operational core AC-7/AC-10: durable create-only evidence publication and
        # exact typed cleanup are the only new filesystem write seams.
        "verification.py": {"replace_file_bytes"},
        "sync.py": {"install_projection"},
        # The trajectory record and aggregate archival pair run best-effort at
        # close.
        "trajectory.py": {"_atomic_write_json", "write_aggregate"},
    }, (
        "FAIL: the shared write-seam allowlist drifted from the ratified "
        "seam set (feature resolution pointer / M3 ledger / M4 write path / M6a "
        "workspace collision-A4 "
        "create + scaffold / trajectory archival / verification evidence). "
        "Allowlists encode "
        "the spec, not the code: a new seam needs its ratifying spec or "
        "ruling record first, then this literal re-pin alongside the "
        "policy_helpers.py edit."
    )


def test_every_subprocess_boundary_is_bounded() -> None:
    """
    completion robustness-analysis enforce-later (review / Assumption A2): every
        `subprocess.run` call in heddle/ carries `timeout=`, and every
        `MonitorConfig` construction carries both `hard_timeout_s` and
        `inactivity_timeout_s` — no unbounded external call.
    """
    import ast
    from pathlib import Path

    offenders: list[str] = []
    for path in sorted(Path("heddle").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            keywords = {keyword.arg for keyword in node.keywords}
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and "timeout" not in keywords
            ):
                offenders.append(
                    f"{path}:{node.lineno} subprocess.run without timeout="
                )
            if (
                isinstance(func, ast.Name)
                and func.id == "MonitorConfig"
                and not {"hard_timeout_s", "inactivity_timeout_s"} <= keywords
            ):
                offenders.append(
                    f"{path}:{node.lineno} MonitorConfig without both "
                    "hard_timeout_s and inactivity_timeout_s"
                )
    assert not offenders, (
        "FAIL timeout/A2: subprocess boundaries must be bounded — external "
        f"calls without explicit timeouts: {offenders}"
    )


def test_state_bearing_writers_use_the_atomic_replace_idiom() -> None:
    """
    completion impl-review enforce-later (review): the ratified state-bearing
        writers route through the temp-file + os.replace / os.link idiom, never
        a bare write_text, so a crash or ENOSPC cannot truncate durable
        evidence.
    """
    import inspect

    from heddle.runtime import state_store, write_path

    publisher = inspect.getsource(state_store._write_text)
    assert "os.replace(temporary, path)" in publisher
    assert "os.link(temporary, path)" in publisher
    assert "dir=path.parent" in publisher
    assert "os.replace" in inspect.getsource(write_path.stamp_lifecycle)
    for writer in (
        state_store._commit_state,
        state_store.append_state,
        state_store.create_state,
    ):
        source = inspect.getsource(writer)
        assert "_publish(" in source
        assert "os.replace" not in source and "os.link" not in source
    assert "_commit_state(" in inspect.getsource(state_store.commit_state)
    for wrapper in (state_store._publish, state_store.replace_file_text):
        assert "_write_text(" in inspect.getsource(wrapper)
        assert "os.replace" not in inspect.getsource(wrapper)


def test_every_completion_ac_id_is_named_in_tests() -> None:
    """completion review-tests enforce-later: each of AC-1..AC-15 is named in at
    least one COMPLETION-marked test file, so no acceptance criterion can silently
    lose its runtime discriminator."""
    from pathlib import Path

    completion_texts = [
        path.read_text(encoding="utf-8")
        for path in sorted(Path("tests").rglob("*.py"))
        if "test_completion_" in path.read_text(encoding="utf-8").lower()
    ]
    assert completion_texts, "FIXTURE ROT: no completion tests found"
    unnamed = [
        f"AC-{number}"
        for number in range(1, 16)
        if not any(f"AC-{number}" in text for text in completion_texts)
    ]
    assert not unnamed, (
        "FAIL: these completion acceptance criteria are named in no completion test "
        "file "
        f"(docstring or assertion): {unnamed}"
    )
