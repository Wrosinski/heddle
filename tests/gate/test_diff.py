"""
Tests for authoritative diff resolution.

Covers the diff cascade: provided-file, git-diff-head, git-diff-cached,
git-diff-head-parent, git-diff-feature-baseline (milestone gates on native
workspaces), none. Also covers changed-file extraction from diff content.
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

REQUIRES_IMPL = pytest.mark.skipif(False, reason="gate package implemented")


class TestDiffCascade:
    def test_oversized_provided_diff_fails_before_it_is_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from heddle.gate import diff as diff_module
        from heddle.kernel.project_config import KernelError

        diff_path = tmp_path / "large.diff"
        diff_path.write_bytes(b"x" * 17)
        monkeypatch.setattr(diff_module, "MAX_GATE_DIFF_BYTES", 16)

        with pytest.raises(KernelError, match="16-byte limit") as caught:
            diff_module.resolve_diff(diff_path=diff_path, repo_root=tmp_path)

        assert caught.value.code == "workspace-invalid"

    def test_non_utf8_git_output_fails_with_named_preparation_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from heddle.gate import diff as diff_module
        from heddle.kernel.project_config import KernelError

        def undecodable(*_args, **_kwargs):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        monkeypatch.setattr(diff_module.git.subprocess, "run", undecodable)

        with pytest.raises(KernelError, match="non-UTF-8.*byte 0"):
            diff_module._run_git_result(["git", "diff", "HEAD"], tmp_path)

    @REQUIRES_IMPL
    def test_provided_file_takes_priority(
        self, tmp_path: Path, sample_diff_path: Path
    ) -> None:
        from heddle.gate.diff import resolve_diff

        result = resolve_diff(diff_path=sample_diff_path, repo_root=tmp_path)
        assert result.source == "provided-file"
        assert result.source_detail == str(sample_diff_path)
        assert result.diff_path.stat().st_size > 0

    @REQUIRES_IMPL
    def test_git_diff_head_when_no_provided_file(self, tmp_path: Path) -> None:
        from heddle.gate.diff import resolve_diff

        # Setup: git repo with unstaged changes (mocked or real)
        result = resolve_diff(diff_path=None, repo_root=tmp_path)
        assert result.source in (
            "git-diff-head",
            "git-diff-cached",
            "git-diff-head-parent",
            "none",
        )

    @REQUIRES_IMPL
    def test_none_when_no_diff_available(self, tmp_path: Path) -> None:
        from heddle.gate.diff import resolve_diff

        # Empty git repo with no changes
        result = resolve_diff(diff_path=None, repo_root=tmp_path)
        assert result.source == "none"
        assert result.changed_files == ()

    @REQUIRES_IMPL
    @pytest.mark.parametrize(
        ("git_outputs", "expected_source", "expected_detail"),
        (
            (["diff --git a/src/a.py b/src/a.py\n", "", ""], "git-diff-head", "HEAD"),
            (
                ["", "diff --git a/src/b.py b/src/b.py\n", ""],
                "git-diff-cached",
                "--cached",
            ),
            (
                ["", "", "diff --git a/src/c.py b/src/c.py\n"],
                "git-diff-head-parent",
                "HEAD^..HEAD",
            ),
            (["", "", ""], "none", ""),
        ),
    )
    def test_source_values_match_bash_variants(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        git_outputs: list[str],
        expected_source: str,
        expected_detail: str,
    ) -> None:
        from heddle.gate import diff as diff_module

        responses = iter(git_outputs)

        def fake_run_git_result(
            command: list[str],
            repo_root: Path,
            **_kwargs,
        ) -> tuple[str, str | None]:
            assert repo_root == tmp_path.resolve()
            if command[:2] == ["git", "ls-files"]:
                return "", None
            return next(responses), None

        monkeypatch.setattr(diff_module.git, "run_git", fake_run_git_result)

        result = diff_module.resolve_diff(diff_path=None, repo_root=tmp_path)

        assert result.source == expected_source
        assert result.source_detail == expected_detail
        assert result.diff_path.exists()
        assert result.diff_path.parent != tmp_path.resolve()

    @REQUIRES_IMPL
    def test_source_values_match_bash(self) -> None:
        valid_sources = {
            "provided-file",
            "git-diff-head",
            "git-diff-cached",
            "git-diff-head-parent",
            "git-diff-feature-baseline",
            "none",
        }

        assert valid_sources == {
            "provided-file",
            "git-diff-head",
            "git-diff-cached",
            "git-diff-head-parent",
            "git-diff-feature-baseline",
            "none",
        }

    @REQUIRES_IMPL
    def test_untracked_files_are_reported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from heddle.gate import diff as diff_module

        def fake_run_git_result(
            command: list[str],
            repo_root: Path,
            **_kwargs,
        ) -> tuple[str, str | None]:
            assert repo_root == tmp_path.resolve()
            if command[:2] == ["git", "ls-files"]:
                return "src/new_file.py\0plans/example.md\0", None
            return "", None

        monkeypatch.setattr(diff_module.git, "run_git", fake_run_git_result)

        result = diff_module.resolve_diff(diff_path=None, repo_root=tmp_path)

        assert result.source == "none"
        assert result.untracked_files == ("src/new_file.py", "plans/example.md")

    def test_non_utf8_path_listing_degrades_to_gate_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from heddle.gate import diff as diff_module

        def fake_run_git_result(command, _repo_root, **_kwargs):
            if command[:2] == ["git", "ls-files"]:
                raise diff_module.git.GitError(
                    "git ls-files -z produced non-UTF-8 path bytes"
                )
            return "", None

        monkeypatch.setattr(diff_module.git, "run_git", fake_run_git_result)
        result = diff_module.resolve_diff(diff_path=None, repo_root=tmp_path)

        assert result.source == "none"
        assert result.untracked_files == ()
        assert result.warnings == ("git ls-files -z produced non-UTF-8 path bytes",)

    def test_oversized_unscoped_probe_does_not_reject_small_authoritative_diff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from heddle.gate import diff as diff_module

        small = "diff --git a/src/owned.py b/src/owned.py\n"
        large = "diff --git a/outside.bin b/outside.bin\n" + ("x" * 128)

        def fake_run(command, **_kwargs):
            if command[:3] == ["git", "ls-files", "--others"]:
                stdout = ""
            elif command == ["git", "diff", "HEAD", "--", "src/owned.py"]:
                stdout = ""
            elif command == ["git", "diff", "HEAD"]:
                stdout = large
            elif command == ["git", "diff", "baseline", "--", "src/owned.py"]:
                stdout = small
            elif command == [
                "git",
                "diff",
                "--name-status",
                "-z",
                "-M",
                "baseline",
                "--",
                "src/owned.py",
            ]:
                stdout = "M\0src/owned.py\0"
            else:
                raise AssertionError(command)
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(diff_module.git.subprocess, "run", fake_run)
        monkeypatch.setattr(diff_module, "MAX_GATE_DIFF_BYTES", 64)
        monkeypatch.setattr(
            diff_module, "_feature_baseline_commit", lambda *_args: ("baseline", None)
        )

        result = diff_module.resolve_diff(
            diff_path=None,
            repo_root=tmp_path,
            owned_paths=["src/owned.py"],
            feature_baseline_probe="plans/feature/state.yaml",
        )

        assert result.changed_files == ("src/owned.py",)


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
            "HOME": str(root),
        },
    )


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class TestFeatureBaselineRung:
    """
    Milestone-baseline mode (commands dogfood, user-sanctioned 2026-07-06).

        The auto flow commits milestone work before gates run (fault tolerance)
        and the driver records verify facts into state.yaml between the work
        commit and the gate — so the working-tree rung always found a non-empty
        bookkeeping-only diff and preflight fataled zero-diff-owned-path-overlap
        on every driver-executed milestone gate. Sanctioned maintenance commits
        also displace the milestone work from the tip, breaking HEAD^..HEAD.
        Contract: rungs without milestone-owned overlap are not authoritative
        (fall through, recorded in warnings); the final rung is the owned
        surface's cumulative diff since the workspace birth commit, pathspec'd
        to the owned paths so interleaved commits are invisible by construction.

    """

    OWNED = ["prompts/x.md", "docs/guide.md"]
    PROBE = "plans/demo/state.yaml"

    def _repo(self, tmp_path: Path) -> Path:
        root = tmp_path / "host"
        (root / "plans" / "demo").mkdir(parents=True)
        (root / "prompts").mkdir()
        (root / "docs").mkdir()
        _git(root, "init", "-q")
        (root / "plans" / "demo" / "state.yaml").write_text("revision: 1\n")
        (root / "prompts" / "x.md").write_text("original prompt\n")
        (root / "other.py").write_text("untouched\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "birth: workspace + pre-existing surface")
        (root / "prompts" / "x.md").write_text("refreshed prompt\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "m1 work")
        (root / "other.py").write_text("foreign maintenance change\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "interleaved maintenance")
        return root

    def _resolve(self, root: Path):
        from heddle.gate.diff import resolve_diff

        return resolve_diff(
            diff_path=None,
            repo_root=root,
            temp_dir=root / "tmp",
            owned_paths=list(self.OWNED),
            feature_baseline_probe=self.PROBE,
        )

    def test_explicit_feature_baseline_drives_cumulative_worktree_diff(
        self, tmp_path: Path
    ) -> None:
        """Local-history AC-3 red: v10 never consults state-file Git history."""
        from heddle.gate.diff import resolve_diff
        from heddle.kernel import state as state_module

        assert "feature_baseline" in inspect.signature(resolve_diff).parameters, (
            "FAIL local-history AC-3: gate diff has no explicit baseline input"
        )
        assert hasattr(state_module, "SourceBaseline")
        root = self._repo(tmp_path)
        baseline = _git_output(root, "rev-list", "--max-parents=0", "HEAD")
        (root / "plans/demo/state.yaml").write_text("revision: local-only\n")
        (root / "docs/guide.md").write_text("dirty guide\n")

        result = resolve_diff(
            None,
            root,
            root / "tmp-explicit",
            owned_paths=list(self.OWNED),
            feature_baseline=state_module.SourceBaseline("git-commit", baseline),
            feature_baseline_probe=self.PROBE,
        )

        assert result.source == "git-diff-feature-baseline"
        assert baseline in result.source_detail
        assert sorted(result.changed_files) == ["docs/guide.md", "prompts/x.md"]
        assert "new file mode" in result.diff_path.read_text()
        assert "+dirty guide" in result.diff_path.read_text()

    @pytest.mark.parametrize("fault", ["missing", "noncommit", "diverged"])
    def test_explicit_feature_baseline_refuses_unusable_history(
        self, tmp_path: Path, fault: str
    ) -> None:
        """Local-history AC-3 red: an authoritative v10 baseline never falls back."""
        from heddle.gate.diff import resolve_diff
        from heddle.kernel import state as state_module
        from heddle.kernel.project_config import KernelError

        assert "feature_baseline" in inspect.signature(resolve_diff).parameters
        root = self._repo(tmp_path)
        baseline = _git_output(root, "rev-list", "--max-parents=0", "HEAD")
        if fault == "missing":
            oid = "f" * len(baseline)
        elif fault == "noncommit":
            blob = subprocess.run(
                ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
                input=b"not a commit\n",
                check=True,
                capture_output=True,
            )
            oid = blob.stdout.decode().strip()
        else:
            _git(root, "checkout", "--orphan", "diverged")
            (root / "foreign.txt").write_text("foreign\n")
            _git(root, "add", "foreign.txt")
            _git(root, "commit", "-qm", "diverged root")
            oid = baseline

        with pytest.raises(KernelError, match="baseline"):
            resolve_diff(
                None,
                root,
                root / f"tmp-{fault}",
                owned_paths=list(self.OWNED),
                feature_baseline=state_module.SourceBaseline("git-commit", oid),
                feature_baseline_probe=self.PROBE,
            )

    def test_untracked_owned_path_preserves_exact_identity_through_preflight(
        self, tmp_path: Path
    ) -> None:
        from heddle.gate.diff import resolve_diff
        from heddle.gate.preflight import preflight
        from heddle.gate.registry import GATES
        from heddle.kernel import state as state_module

        root = self._repo(tmp_path)
        baseline = _git_output(root, "rev-list", "--max-parents=0", "HEAD")
        relative = "docs/naïve source file.md"
        (root / relative).write_text("local source\n")

        result = resolve_diff(
            None,
            root,
            root / "tmp-encoded-untracked",
            owned_paths=["docs"],
            feature_baseline=state_module.SourceBaseline("git-commit", baseline),
        )
        prepared = preflight(
            gate_type=GATES["milestone-review"],
            plan_path="plans/demo/plan.md",
            milestone="m2",
            milestone_section="## Milestone m2",
            satisfies_ac_ids=("AC-4",),
            diff_non_empty=bool(result.diff_path.read_bytes()),
            changed_files=result.changed_files,
            owned_paths=("docs",),
            overlap_files=result.changed_files,
            untracked_files=result.untracked_files,
        )

        assert result.changed_files == (relative,)
        assert prepared.status == "passed"
        assert prepared.fatal_reason is None

    def test_bookkeeping_only_tree_falls_through_to_baseline(
        self, tmp_path: Path
    ) -> None:
        root = self._repo(tmp_path)
        # The driver just recorded a verify fact: state.yaml is dirty, the
        # milestone work is committed two commits back.
        (root / "plans" / "demo" / "state.yaml").write_text("revision: 2\n")

        result = self._resolve(root)

        assert result.source == "git-diff-feature-baseline", (
            "FAIL: a bookkeeping-only working-tree diff must not be "
            f"authoritative for a milestone gate, got {result.source!r} "
            f"({result.changed_files!r})"
        )
        assert result.changed_files == ("prompts/x.md",), (
            "FAIL: the baseline rung must show exactly the owned-path work — "
            "interleaved maintenance commits are invisible by construction, "
            f"got {result.changed_files!r}"
        )
        assert result.source_detail.endswith("..HEAD")
        assert any("falling through" in warning for warning in result.warnings), (
            "FAIL (fail-loud): the skipped rung must be recorded in warnings, "
            f"got {result.warnings!r}"
        )

    def test_foreign_tip_commit_never_reaches_head_parent_rung(
        self, tmp_path: Path
    ) -> None:
        root = self._repo(tmp_path)  # clean tree, foreign commit at tip

        result = self._resolve(root)

        assert result.source == "git-diff-feature-baseline", (
            "FAIL: HEAD^..HEAD would diff the foreign tip commit (zero owned "
            f"overlap) — the baseline rung must win, got {result.source!r}"
        )
        assert result.changed_files == ("prompts/x.md",)

    def test_dirty_owned_work_stays_authoritative(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        (root / "prompts" / "x.md").write_text("in-flight edit\n")
        (root / "plans" / "demo" / "state.yaml").write_text("revision: 2\n")

        result = self._resolve(root)

        assert result.source == "git-diff-feature-baseline", (
            "FAIL: uncommitted owned work (a session gating its dirty tree) "
            f"must retain cumulative baseline-to-worktree authority, got "
            f"{result.source!r}"
        )
        assert "prompts/x.md" in result.changed_files

    def test_dirty_owned_work_includes_committed_owned_changes(
        self, tmp_path: Path
    ) -> None:
        root = self._repo(tmp_path)
        (root / "docs" / "guide.md").write_text("committed guide\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "committed owned guide")
        (root / "prompts" / "x.md").write_text("in-flight prompt edit\n")

        result = self._resolve(root)

        assert result.source == "git-diff-feature-baseline"
        assert sorted(result.changed_files) == [
            "docs/guide.md",
            "prompts/x.md",
        ], (
            "FAIL: dirty owned work must not hide committed owned changes "
            f"since the feature baseline, got {result.changed_files!r}"
        )

    def test_multi_commit_milestone_work_is_cumulative(self, tmp_path: Path) -> None:
        root = self._repo(tmp_path)
        # A later fold-fix commit touching a second owned path — the range
        # must include BOTH owned commits (a nearest-commit walk would not).
        (root / "docs" / "guide.md").write_text("fold fix\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "m1 fold fix")

        result = self._resolve(root)

        assert result.source == "git-diff-feature-baseline"
        assert sorted(result.changed_files) == ["docs/guide.md", "prompts/x.md"], (
            "FAIL: the baseline rung must accumulate every owned-path commit "
            f"since workspace birth, got {result.changed_files!r}"
        )

    def test_w5_ac15_dirty_cumulative_metadata_reproduces_actual_diff(self, tmp_path):
        from tests.content_identity_helpers import git

        root = self._repo(tmp_path)
        (root / "docs/guide.md").write_text("committed guide\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "another owned implementation commit")
        (root / "prompts/x.md").write_text("dirty prompt after owned commits\n")
        result = self._resolve(root)
        assert result.source != "git-diff-head", (
            "FAIL W5 AC-15: baseline-to-worktree bytes are labelled as HEAD diff"
        )
        baseline = (
            git(root, "log", "--diff-filter=A", "--format=%H", "--", self.PROBE)
            .decode()
            .strip()
            .splitlines()[-1]
        )
        expected = git(root, "diff", baseline, "--", *self.OWNED)
        assert result.diff_path.read_bytes() == expected
        assert baseline in result.source_detail and "WORKTREE" in result.source_detail

    def test_missing_baseline_is_loud(self, tmp_path: Path) -> None:
        from heddle.kernel.project_config import KernelError

        root = tmp_path / "host"
        root.mkdir()
        _git(root, "init", "-q")
        (root / "tracked.md").write_text("x\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "no workspace state.yaml ever committed")

        with pytest.raises(KernelError, match="baseline unresolvable") as raised:
            self._resolve(root)

        assert "restore the recorded baseline commit" in raised.value.hint

    def test_missing_baseline_refuses_head_fallback(self, tmp_path: Path) -> None:
        from heddle.kernel.project_config import KernelError

        root = tmp_path / "host"
        root.mkdir()
        _git(root, "init", "-q")
        (root / "tracked.md").write_text("x\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "no workspace state.yaml ever committed")

        (root / "prompts").mkdir()
        (root / "prompts/x.md").write_text("dirty owned source\n")

        with pytest.raises(KernelError, match="baseline unresolvable") as raised:
            self._resolve(root)

        assert "restore the recorded baseline commit" in raised.value.hint

    def test_legacy_mode_is_unchanged(self, tmp_path: Path) -> None:
        from heddle.gate.diff import resolve_diff

        root = self._repo(tmp_path)
        (root / "plans" / "demo" / "state.yaml").write_text("revision: 2\n")

        result = resolve_diff(diff_path=None, repo_root=root)

        assert result.source == "git-diff-head", (
            "FAIL: without the milestone-baseline kwargs the cascade must "
            f"behave exactly as before, got {result.source!r}"
        )
        assert result.changed_files == ("plans/demo/state.yaml",)


class TestChangedFileExtraction:
    @REQUIRES_IMPL
    def test_extracts_paths_from_diff(
        self, sample_diff_content: str, tmp_path: Path
    ) -> None:
        from heddle.gate.diff import extract_changed_files

        diff_path = tmp_path / "test.diff"
        diff_path.write_text(sample_diff_content)
        files = extract_changed_files(diff_path)
        assert "src/example/sample/core.py" in files
        assert "config/sample/default.yaml" in files

    @REQUIRES_IMPL
    def test_empty_diff_returns_empty_list(self, tmp_path: Path) -> None:
        from heddle.gate.diff import extract_changed_files

        diff_path = tmp_path / "empty.diff"
        diff_path.write_text("")
        assert extract_changed_files(diff_path) == []

    @REQUIRES_IMPL
    def test_strips_a_prefix_from_paths(self, tmp_path: Path) -> None:
        from heddle.gate.diff import extract_changed_files

        diff_path = tmp_path / "test.diff"
        diff_path.write_text("diff --git a/src/foo.py b/src/foo.py\n")
        files = extract_changed_files(diff_path)
        assert files == ["src/foo.py"]
