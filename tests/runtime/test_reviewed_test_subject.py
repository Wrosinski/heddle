"""AC-7: the reviewed-test subject depends on content, not history.

Each host records a feature baseline, then changes ``tests/test_changed.py`` in
a commit and adds an unrelated tip commit, so the subject is the
``Verified-by:`` binding plus the changed test, and the tip commit touches no
test. Both bases are read through their real consumers: the reviewer basis
through gate preparation, the assignment basis through ``current_basis``.

Activation seams: none. Every case runs the current gate preparation and
assignment basis; the baseline-relative subject resolver is reached through
them.
"""

from __future__ import annotations

import pytest
import yaml

from tests.change_impact_subject_helpers import (
    BINDING_TEST,
    CHANGED_TEST,
    DOOMED_TEST,
    STABLE_TEST,
    UNRELATED,
    BaselineHost,
    assignment_basis,
    baseline_host,
    commit_all,
    expected_assignment_basis,
    git,
    reviewer_basis,
    reviewer_subject,
)

SUBJECT = frozenset({BINDING_TEST, CHANGED_TEST})


def _subject_host(tmp_path, monkeypatch, *, record: bool = True) -> BaselineHost:
    host = baseline_host(tmp_path, monkeypatch, record=record)
    (host.root / CHANGED_TEST).write_text("def test_changed():\n    assert 1 == 2\n")
    commit_all(host.root, "change a test after the baseline")
    (host.root / UNRELATED).write_text("notes, revised\n")
    commit_all(host.root, "unrelated tip commit")
    return host


def _bases(host: BaselineHost) -> tuple[str, str, frozenset[str]]:
    return (
        reviewer_basis(host.root),
        assignment_basis(host.root),
        reviewer_subject(host.root),
    )


def _assert_selects(host: BaselineHost, subject: frozenset[str], case: str) -> None:
    observed = reviewer_subject(host.root)
    assert observed == subject, (
        f"FAIL AC-7: the reviewer basis does not select the baseline-relative "
        f"subject after {case}: expected {sorted(subject)}, got {sorted(observed)}"
    )
    assert assignment_basis(host.root) == expected_assignment_basis(
        host.root, subject
    ), (
        f"FAIL AC-7: the assignment basis does not select the same subject as "
        f"the reviewer basis after {case}: expected {sorted(subject)}"
    )


def test_ac7_reviewer_and_assignment_bases_select_one_baseline_relative_subject(
    tmp_path, monkeypatch
):
    host = _subject_host(tmp_path, monkeypatch)

    _assert_selects(host, SUBJECT, "a test commit followed by an unrelated commit")


def test_ac7_commit_then_revert_leaves_subject_and_both_digests_unchanged(
    tmp_path, monkeypatch
):
    host = _subject_host(tmp_path, monkeypatch)
    before = _bases(host)

    (host.root / STABLE_TEST).write_text("def test_stable():\n    assert 'a' == 'b'\n")
    commit_all(host.root, "change an unchanged test")
    git(host.root, "revert", "--no-edit", "HEAD")

    assert _bases(host) == before, (
        "FAIL AC-7: a commit and its revert changed the reviewed-test subject "
        "or a basis digest"
    )
    _assert_selects(host, SUBJECT, "a commit and its revert")


@pytest.mark.parametrize("rewrite", ["amend", "rebase"])
def test_ac7_amend_or_rebase_with_a_reachable_baseline_leaves_subject_unchanged(
    tmp_path, monkeypatch, rewrite
):
    host = _subject_host(tmp_path, monkeypatch)
    before = _bases(host)
    tip = git(host.root, "rev-parse", "HEAD")
    test_commit = git(host.root, "rev-parse", "HEAD~1")

    if rewrite == "amend":
        # Fold the unrelated tip commit into the test commit.
        git(host.root, "reset", "--soft", "HEAD~1")
        git(host.root, "commit", "--amend", "--no-edit", "-q")
    else:
        # Replay the two feature commits in the opposite order.
        git(host.root, "reset", "-q", "--hard", host.baseline)
        git(host.root, "cherry-pick", tip)
        git(host.root, "cherry-pick", test_commit)
    git(host.root, "merge-base", "--is-ancestor", host.baseline, "HEAD")
    assert git(host.root, "rev-parse", "HEAD") not in {tip, test_commit}

    assert _bases(host) == before, (
        f"FAIL AC-7: {rewrite} changed the reviewed-test subject or a basis digest "
        "while the baseline stayed reachable"
    )
    _assert_selects(host, SUBJECT, rewrite)


def test_ac7_a_deleted_test_is_a_missing_observation_whether_or_not_it_changed(
    tmp_path, monkeypatch
):
    host = _subject_host(tmp_path, monkeypatch)
    present = _bases(host)
    expected = SUBJECT | {DOOMED_TEST}

    (host.root / DOOMED_TEST).unlink()
    assert DOOMED_TEST in reviewer_subject(host.root), (
        "FAIL AC-7: the reviewer basis drops a deleted test that is unchanged "
        "since the baseline instead of recording it as missing"
    )
    _assert_selects(host, expected, "deleting a test unchanged since the baseline")
    deleted_unchanged = _bases(host)

    git(host.root, "checkout", "--", DOOMED_TEST)
    (host.root / DOOMED_TEST).write_text("def test_doomed():\n    assert 2 == 3\n")
    commit_all(host.root, "change the test that will be deleted")
    (host.root / DOOMED_TEST).unlink()
    assert DOOMED_TEST in reviewer_subject(host.root), (
        "FAIL AC-7: the reviewer basis drops a deleted test that changed since "
        "the baseline instead of recording it as missing"
    )
    _assert_selects(host, expected, "deleting a test changed since the baseline")

    assert _bases(host) == deleted_unchanged, (
        "FAIL AC-7: the same deletion gives different bases depending on history"
    )
    assert deleted_unchanged[0] != present[0], (
        "FAIL AC-7: the reviewer basis digest ignores a deleted test"
    )
    assert deleted_unchanged[1] != present[1], (
        "FAIL AC-7: the assignment basis digest ignores a deleted test"
    )


def test_ac7_an_untracked_test_joins_and_untracked_files_elsewhere_do_not(
    tmp_path, monkeypatch
):
    host = _subject_host(tmp_path, monkeypatch)
    (host.root / "tests/test_new.py").write_text("def test_new():\n    assert True\n")
    (host.root / "src/test_outside.py").write_text("def test_outside(): pass\n")
    (host.root / "docs/test_notes.py").write_text("def test_notes(): pass\n")

    _assert_selects(host, SUBJECT | {"tests/test_new.py"}, "adding untracked files")


def test_ac7_only_paths_under_the_configured_tests_root_join(tmp_path, monkeypatch):
    host = _subject_host(tmp_path, monkeypatch)
    config_path = host.root / ".heddle.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["layout"]["tests"] = "checks"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    (host.root / "checks").mkdir()
    (host.root / "checks/test_committed.py").write_text(
        "def test_committed():\n    assert True\n"
    )
    commit_all(host.root, "configure the checks tests root")
    (host.root / UNRELATED).write_text("notes, revised again\n")
    commit_all(host.root, "unrelated tip commit")
    (host.root / "checks/test_untracked.py").write_text(
        "def test_untracked():\n    assert True\n"
    )
    (host.root / "tests/test_outside_root.py").write_text(
        "def test_outside_root():\n    assert True\n"
    )

    # The binding stays declared; tests/test_changed.py differs from the
    # baseline but lies outside the configured root.
    _assert_selects(
        host,
        frozenset(
            {BINDING_TEST, "checks/test_committed.py", "checks/test_untracked.py"}
        ),
        "configuring the checks tests root",
    )


def test_ac7_formatting_only_change_to_an_undeclared_unchanged_test_keeps_membership(
    tmp_path, monkeypatch
):
    host = _subject_host(tmp_path, monkeypatch)
    before = _bases(host)

    (host.root / STABLE_TEST).write_text('def test_stable():\n    assert "a" == "a"\n')
    assert _bases(host) == before, (
        "FAIL AC-7: a formatting-only change to an undeclared test unchanged "
        "since the baseline changed the subject or a basis digest"
    )
    _assert_selects(host, SUBJECT, "a formatting-only change")

    (host.root / STABLE_TEST).write_text('def test_stable():\n    assert "a" == "b"\n')
    _assert_selects(host, SUBJECT | {STABLE_TEST}, "a content change")


def test_ac7_feature_without_a_recorded_baseline_keeps_todays_selection(
    tmp_path, monkeypatch
):
    """Survivor pin: without a baseline the reviewer keeps the HEAD cascade.

    The cascade takes the uncommitted test change and the binding, and omits
    the test changed in an earlier commit.
    """
    host = _subject_host(tmp_path, monkeypatch, record=False)
    assert "source_baseline" not in yaml.safe_load(host.state.read_text())
    (host.root / STABLE_TEST).write_text("def test_stable():\n    assert 'a' == 'b'\n")

    assert reviewer_subject(host.root) == {BINDING_TEST, STABLE_TEST}
