"""Schema compatibility, precise remedies, and historical wire readers."""

from pathlib import Path

import pytest

from heddle.contracts import schemas
from heddle.kernel.project_config import KernelError
from heddle.kernel.state import INCOMPATIBLE_STATE_SCHEMA, read_state_file
from tests.runtime.validation_helpers import copy_host, read_yaml, write_yaml

ROOT = Path(__file__).resolve().parents[2]
TINY = ROOT / "tests/fixtures/workspaces/tiny"
SOURCE = TINY / "plans/sample-feature/state.yaml"


@pytest.mark.parametrize(
    "schema",
    [
        "heddle.state/v6",
        "heddle.state/v7",
        "heddle.state/v8",
        "heddle.state/v0",
        "heddle.state/v1",
        "heddle.state/v2",
        "heddle.state/v3",
        "heddle.state/v99",
        "foreign/v3",
        "heddle.state/vx",
        None,
    ],
)
def test_incompatible_schema_refuses_before_shape_validation(tmp_path, schema):
    assert schemas.STATE_SCHEMA_VERSIONS == ("heddle.state/v9", "heddle.state/v10")
    assert schemas.CURRENT_STATE_SCHEMA == schemas.STATE_SCHEMA_ID == "heddle.state/v10"
    assert read_state_file(SOURCE).schema == "heddle.state/v9"
    value = read_yaml(SOURCE)
    if schema is None:
        value.pop("schema")
    else:
        value["schema"] = schema
    value["future_additive_key"] = True
    path = tmp_path / "state.yaml"
    write_yaml(path, value)
    before = path.read_bytes()
    with pytest.raises(KernelError) as caught:
        read_state_file(path)
    assert caught.value.code == "workspace-invalid"
    assert caught.value.reason == INCOMPATIBLE_STATE_SCHEMA
    assert (schema or "missing") in caught.value.message
    assert "fresh workspace" in caught.value.hint
    assert "unknown" not in caught.value.message
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["status", "doctor", "validate"])
def test_public_incompatible_schema_preserves_legal_remedy(
    tmp_path, monkeypatch, run_cli, envelope_tools, command
):
    host = copy_host(tmp_path, TINY)
    path = host / "plans/sample-feature/state.yaml"
    value = read_yaml(path)
    value["schema"] = "heddle.state/v99"
    write_yaml(path, value)
    before = path.read_bytes()
    monkeypatch.chdir(host)
    code, out, _ = run_cli([command, "--feature", "sample-feature", "--json"])
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert "fresh workspace" in envelope["error"]["hint"]
    assert len(envelope["next_actions"]) == 1
    action = envelope["next_actions"][0]
    assert action["action"]["kind"] == "manual"
    assert "fresh workspace" in action["command"]
    if command in {"status", "doctor"}:
        identity = [
            row for row in envelope["diagnostics"] if row["code"] == "install-mode"
        ]
        assert len(identity) == 1
        assert "package=" in identity[0]["message"]
        assert "interpreter=" in identity[0]["message"]
    assert path.read_bytes() == before


def test_current_corpus_loads_without_restamping_and_malformed_current_still_refuses():
    paths = sorted((ROOT / "tests/fixtures/workspaces").glob("**/state.yaml"))
    assert paths
    for path in paths:
        before = path.read_bytes()
        assert read_state_file(path).schema == "heddle.state/v9"
        assert path.read_bytes() == before
    malformed = read_yaml(SOURCE)
    malformed["unknown_top_level_key"] = True
    path = ROOT / "tests/fixtures/workspaces/tiny/plans/sample-feature/malformed.yaml"
    write_yaml(path, malformed)
    try:
        with pytest.raises(KernelError) as caught:
            read_state_file(path)
    finally:
        path.unlink()
    assert caught.value.code == "workspace-invalid"
    assert caught.value.reason != INCOMPATIBLE_STATE_SCHEMA
    assert "unknown" in caught.value.message
