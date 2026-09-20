"""AC-8: the public owns_append seam reuses existing edit and proof rules."""

import pytest

from heddle.kernel.state import read_state_file
from heddle.runtime.verification import assess_current_verification
from tests.proof_continuity_helpers import edit, read, source_host, verify, write
from tests.tiering_helpers import snapshot


@pytest.mark.parametrize("status", ["todo", "current", "done"])
def test_append_preserves_owns_and_proof_with_atomic_preview_and_replay(
    tmp_path, monkeypatch, capsys, status
):
    root, path = source_host(tmp_path, monkeypatch)
    verify(path, "m1")
    state = read(path)
    state["milestones"][0]["status"] = status
    write(path, state)
    (root / "src/extra.py").write_text("EXTRA = 7\n")
    original = state["milestones"][0]["owns"]
    patch = {"owns_append": ["src/extra.py", original[0]]}
    before = snapshot(root)
    code, preview = edit(capsys, tmp_path, patch, flags=("--dry-run",))
    assert code == 0 and preview["ok"], preview
    assert snapshot(root) == before
    code, result = edit(
        capsys, tmp_path, patch, flags=("--expect-revision", str(state["revision"]))
    )
    assert code == 0 and result["ok"], result
    after = read(path)
    assert set(after["milestones"][0]["owns"]) == {*original, "src/extra.py"}
    assert len(after["milestones"][0]["owns"]) == len(original) + 1
    assert after["verifications"] == state["verifications"]
    assert (
        assess_current_verification(root, read_state_file(path), "m1").status
        == "source-set-stale"
    )
    before_replay = snapshot(root)
    code, replay = edit(capsys, tmp_path, patch)
    assert code == 0 and replay["ok"], replay
    assert snapshot(root) == before_replay


@pytest.mark.parametrize(
    "bad",
    [
        {"owns_append": ["../escape"]},
        {"owns_append": ["plans/operational-example/state.yaml"]},
        {"owns_append": ["src/extra.py", "src/extra.py"]},
        {"owns_append": "src/extra.py"},
        {"owns_append": ["src/extra.py"], "owns": ["src/example.py"]},
        {"owns_append": ["src/extra.py"], "title": "Illegal done edit"},
    ],
)
def test_append_refusals_are_atomic_after_operation_activation(
    tmp_path, monkeypatch, capsys, bad
):
    root, path = source_host(tmp_path, monkeypatch)
    state = read(path)
    state["milestones"][0]["status"] = "done"
    write(path, state)
    (root / "src/extra.py").write_text("EXTRA = 7\n")
    code, enabled = edit(capsys, tmp_path, {"owns_append": ["src/extra.py"]})
    assert code == 0 and enabled["ok"], (
        enabled
    )  # Missing command is not a negative pass.
    before = snapshot(root)
    code, refused = edit(capsys, tmp_path, bad)
    assert code != 0 and not refused["ok"], refused
    assert snapshot(root) == before


def test_existing_replace_and_append_cas_remain_distinct(tmp_path, monkeypatch, capsys):
    root, path = source_host(tmp_path, monkeypatch)
    code, result = edit(capsys, tmp_path, {"owns": ["tests/check.py"]})
    assert code == 0 and result["ok"], result
    assert read(path)["milestones"][0]["owns"] == ["tests/check.py"]
    code, result = edit(capsys, tmp_path, {"owns_append": ["src/example.py"]})
    assert code == 0 and result["ok"], result
    before = snapshot(root)
    code, stale = edit(
        capsys,
        tmp_path,
        {"owns_append": ["src/example.py"]},
        flags=("--expect-revision", "0"),
    )
    assert code != 0 and stale["error"]["code"] == "conflict", stale
    assert snapshot(root) == before
