"""
PL-lifecycle boundary(b) (commands plan-review, user-sanctioned 2026-07-06): the two
rails
that made deletion milestones unimplementable stop contradicting each other.

Before: gate preflight fatally rejected any changed file outside the
milestone's ``owns`` (deletions are changed files → the deletion set MUST
be owned), while ``validate`` fatally rejected any owns entry missing from
disk (→ the deletion set MUST NOT be owned once deleted). Both arms fatal;
write-class deletion milestones could not converge their own gates and close
validate-clean (plan-review PL-C1). Related, PL-I1: workspace ``state.yaml``
/``reviews/`` churn rides every milestone diff (gate recording, session
facts) and read as off-scope, forcing a brittle commit choreography.

Contract now:
- The native workspace directory joins the plan/spec as a workflow control
  surface in ``_workflow_control_paths`` — its churn is never off-scope.
- ``owned-path-missing`` stays FATAL for never-tracked completed paths;
  a missing owns entry that git ever tracked is a sanctioned deletion →
  ADVISORY ``owned-path-deleted``. Unfinished planned paths have their own
  lifecycle coverage in test_planned_owned_paths.py; Git errors remain fatal.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from heddle.contracts.result import Severity
from heddle.runtime.validate import _tracked_deletion


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": "/usr/bin:/bin",
        },
    )


class TestOwnedPathDeletionIsAdvisory:
    def test_git_tracked_deletion_reads_as_deletion(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")
        target = tmp_path / "heddle" / "legacy.py"
        target.parent.mkdir(parents=True)
        target.write_text("old\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "add legacy")
        _git(tmp_path, "rm", "-q", "heddle/legacy.py")
        _git(tmp_path, "commit", "-q", "-m", "delete legacy")

        assert _tracked_deletion(tmp_path, "heddle/legacy.py") is True, (
            "FAIL PL-lifecycle boundary(b): a git-tracked-then-deleted owns entry must "
            "read "
            "as a sanctioned deletion, not a typo"
        )

    def test_never_tracked_path_stays_the_fatal_arm(self, tmp_path: Path) -> None:
        _git(tmp_path, "init", "-q")
        (tmp_path / "real.py").write_text("x\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "seed")

        assert _tracked_deletion(tmp_path, "heddle/typo.py") is False, (
            "FAIL: a never-tracked owns entry is a typo — the FATAL "
            "owned-path-missing arm must be unchanged"
        )

    def test_no_git_repo_fails_closed(self, tmp_path: Path) -> None:
        assert _tracked_deletion(tmp_path, "anything.py") is False, (
            "FAIL: without git the check must fail closed to the fatal arm "
            "(the M5 git-less fixture tests depend on this)"
        )

    def test_validate_emits_advisory_for_tracked_deletion(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """End-to-end through _check_owned_paths: deleted-owned → ADVISORY
        owned-path-deleted; never-tracked → FATAL owned-path-missing."""
        from types import SimpleNamespace

        from heddle.runtime.validate import _check_owned_paths

        _git(tmp_path, "init", "-q")
        kept = tmp_path / "kept.py"
        kept.write_text("k\n", encoding="utf-8")
        gone = tmp_path / "gone.py"
        gone.write_text("g\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-q", "-m", "seed")
        _git(tmp_path, "rm", "-q", "gone.py")
        _git(tmp_path, "commit", "-q", "-m", "delete gone")

        context = SimpleNamespace(
            root=tmp_path,
            snapshot=SimpleNamespace(
                state=SimpleNamespace(
                    feature="demo",
                    stage="implement",
                    milestones=[
                        SimpleNamespace(
                            id="m4",
                            status="done",
                            owns=("kept.py", "gone.py", "typo.py"),
                        )
                    ],
                )
            ),
        )
        diagnostics = _check_owned_paths(context)
        by_code = {d.code: d for d in diagnostics}
        assert set(by_code) == {"owned-path-deleted", "owned-path-missing"}, (
            f"FAIL: expected one advisory + one fatal, got {sorted(by_code)}"
        )
        assert by_code["owned-path-deleted"].severity == Severity.ADVISORY
        assert "gone.py" in by_code["owned-path-deleted"].message
        assert by_code["owned-path-missing"].severity == Severity.FATAL
        assert "typo.py" in by_code["owned-path-missing"].message
