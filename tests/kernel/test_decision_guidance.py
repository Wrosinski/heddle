"""AC-1/7: one captured policy, resolved independently of consumer templates."""

from pathlib import Path

import pytest

from heddle.kernel.project_config import KernelError, load_project_config


def test_policy_tiers_preserve_exact_bytes_and_capture(tmp_path):
    from heddle.kernel.decision_guidance import load_decision_guidance

    (tmp_path / ".heddle.yaml").write_text("{}")
    config = load_project_config(tmp_path)
    packaged = load_decision_guidance(config)
    assert packaged.source == "packaged"
    assert "# Decision routing" in packaged.text
    for tier in ("generated", "overrides"):
        path = tmp_path / ".heddle" / tier / "decision-routing.md"
        path.parent.mkdir(parents=True)
        raw = f"{tier}\r\npolicy  \n".encode()
        path.write_bytes(raw)
        captured = load_decision_guidance(config)
        assert captured.text.encode() == raw
        assert captured.path == path
        assert captured.source == ("override" if tier == "overrides" else tier)
        path.write_text("changed")
        assert captured.text.encode() == raw


@pytest.mark.parametrize("body", [b" \n", b"\xff"])
def test_invalid_selected_policy_never_falls_back(tmp_path, body):
    from heddle.kernel.decision_guidance import load_decision_guidance

    (tmp_path / ".heddle.yaml").write_text("{}")
    path = tmp_path / ".heddle/overrides/decision-routing.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    with pytest.raises(KernelError) as error:
        load_decision_guidance(load_project_config(tmp_path))
    assert error.value.code == "workspace-invalid"
    assert "decision-routing.md" in error.value.message
    assert error.value.hint


def test_missing_policy_is_typed(tmp_path, monkeypatch):
    from heddle.kernel import resources
    from heddle.kernel.decision_guidance import load_decision_guidance

    (tmp_path / ".heddle.yaml").write_text("{}")
    monkeypatch.setattr(resources, "_PACKAGED_ROOT", tmp_path / "absent")
    with pytest.raises(KernelError, match="decision-routing.md"):
        load_decision_guidance(load_project_config(tmp_path))


def test_unreadable_selected_policy_is_typed(tmp_path, monkeypatch):
    from heddle.kernel.decision_guidance import load_decision_guidance

    (tmp_path / ".heddle.yaml").write_text("{}")
    path = tmp_path / ".heddle/overrides/decision-routing.md"
    path.parent.mkdir(parents=True)
    path.write_text("policy")
    original = Path.read_bytes

    def read_bytes(self):
        if self == path:
            raise PermissionError("synthetic denied read")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    with pytest.raises(KernelError, match="decision-routing.md"):
        load_decision_guidance(load_project_config(tmp_path))
