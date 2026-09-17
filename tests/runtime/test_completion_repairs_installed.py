"""Installed F3 journey for completion repair reporting and one acceptance."""

from __future__ import annotations

import tarfile

import pytest

from tests.content_identity_helpers import git
from tests.runtime.test_completion_installed import FEATURE, SPEC, native_pending_host
from tests.runtime.wheel_harness import build_installed_wheel, snapshot_tree

pytestmark = [pytest.mark.acceptance, pytest.mark.toolchain, pytest.mark.e2e]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    return build_installed_wheel(tmp_path_factory.mktemp("f3-installed"))


def _repair_pairs(envelope) -> list[tuple[str, str]]:
    return [
        (repair["check"], repair["cause"])
        for repair in envelope["error"]["details"]["repairs"]
    ]


def test_installed_completion_reports_repairs_then_accepts_once(
    installed, tmp_path
) -> None:
    journey = native_pending_host(installed, tmp_path, reviewed=False)
    retro = journey.root / "journey-plans" / f"{FEATURE}.friction-retrospective.md"
    journal = journey.root / "journey-plans" / f"{FEATURE}.decision-journal.md"
    spec = journey.root / SPEC
    original = {
        "retro": retro.read_bytes(),
        "journal": journal.read_bytes(),
        "spec": spec.read_bytes(),
    }
    state_before = journey.state.read_bytes()
    calls_before = journey.count(journey.calls)
    providers_before = journey.count(journey.providers)
    assert providers_before == 0

    retro.write_text("# Retrospective without required sections\n", encoding="utf-8")
    journal.write_bytes(b"\xff\xfe")
    assert original["spec"].startswith(b"---\n")
    spec.write_bytes(original["spec"].removeprefix(b"---\n"))
    defective_tree = snapshot_tree(journey.root)

    preview = journey.run("feature", "complete", "--dry-run", expected=3)
    applied = journey.run("feature", "complete", expected=3)
    expected = [
        ("retrospective", "missing-headings"),
        ("decision-journal", "unreadable"),
        ("spec-stamp", "invalid-frontmatter"),
    ]
    assert _repair_pairs(preview) == expected
    assert _repair_pairs(applied) == expected
    assert preview["error"]["details"] == applied["error"]["details"]
    assert snapshot_tree(journey.root) == defective_tree
    assert journey.state.read_bytes() == state_before
    assert journey.count(journey.calls) == calls_before
    assert journey.count(journey.providers) == providers_before
    assert not journey.archive.exists()

    retro.write_bytes(original["retro"])
    journal.write_bytes(original["journal"])
    spec.write_bytes(original["spec"])
    # Settle Git's stat cache after restoring tracked bytes so the no-effect
    # oracle observes workflow artifacts, not an index refresh during preflight.
    git(journey.root, "status", "--short")
    repaired_tree = snapshot_tree(journey.root)
    clean_preview = journey.run("feature", "complete", "--dry-run")
    assert clean_preview["data"]["accepted"] is False
    assert snapshot_tree(journey.root) == repaired_tree
    assert journey.count(journey.calls) == calls_before

    accepted = journey.run("feature", "complete")
    assert accepted["data"]["accepted"] is True
    assert all(
        effect["status"] == "complete"
        for effect in accepted["data"]["effects"].values()
    )
    assert journey.count(journey.calls) == calls_before + 1
    assert journey.count(journey.providers) == providers_before
    accepted_state = journey.state.read_bytes()
    accepted_fact = journey.read()["completion"]
    with tarfile.open(journey.archive, "r:gz") as archive:
        assert archive.extractfile("state.yaml").read() == accepted_state

    journey.retain()
    retry = journey.run("feature", "complete")
    assert retry["data"]["effects"]["cleanup"]["status"] == "complete"
    assert journey.state.read_bytes() == accepted_state
    assert journey.read()["completion"] == accepted_fact
    assert journey.count(journey.calls) == calls_before + 1
    assert journey.count(journey.providers) == providers_before
    assert not installed.forbidden_log.exists()
    assert not installed.network_log.exists()
