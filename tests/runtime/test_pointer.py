"""
feature switch surface tests — milestone completion.

Covers: AC-11 (the pointer contract against REAL git layouts — normal
checkout and linked worktree — plus idempotence, unknown slug, outside-git,
and malformed-config usage mapping).
Behavior contract: reader-kernel-read-model

preimplementation state (retired): importorskip-gated on heddle.runtime.pointer until
completion landed it; guard retired to a hard import at feature completion
(pattern red-phase-always-green-scaffolding, step 5).

These tests create throwaway git repositories in tmp_path and never touch
this repository's .git (plan §Idempotence and Recovery). feature resolution pins the
pointer location as `git rev-parse --git-path heddle/active-feature`; the
kernel derives it in pure Python (Assumption A3) — both layouts are
asserted against real git's answer, the semantic specification.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from heddle.kernel import CONFIG_UNKNOWN_KEY

if shutil.which("git") is None:  # pragma: no cover - environment guard
    pytest.skip("git binary required for AC-11 layout tests", allow_module_level=True)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "workspaces" / "golden"

MALFORMED_CONFIG = (
    REPO_ROOT / "tests" / "fixtures" / "configs" / "malformed" / ".heddle.yaml"
)


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_git_host(tmp_path: Path, name: str = "main") -> Path:
    """A real git checkout around a copy of the golden host."""
    host = tmp_path / name
    shutil.copytree(GOLDEN, host)
    _git(["init", "-q"], host)
    _git(["config", "user.email", "edge@example.com"], host)
    _git(["config", "user.name", "Example"], host)
    _git(["add", "-A"], host)
    _git(["commit", "-q", "-m", "fixture host"], host)
    return host


def git_pointer_path(cwd: Path) -> Path:
    """
    Real git's answer for the pointer location — the feature resolution semantic spec.
    """
    raw = _git(["rev-parse", "--git-path", "heddle/active-feature"], cwd)
    path = Path(raw)
    return path if path.is_absolute() else (cwd / path)


class TestAC11FeatureSwitch:
    """AC-11: the pointer is recorded at the derived git-path location with
    the pinned heddle.feature-switch/v0 payload; exit codes ⊆ {0, 1, 2}."""

    def test_switch_records_pointer_with_pinned_payload(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        host = make_git_host(tmp_path)
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        # The pinned payload: project-root-relative pointer in the normal
        # .git-directory case; previous null on first record.
        assert envelope["data"] == {
            "feature": "nl-screening",
            "pointer": ".git/heddle/active-feature",
            "previous": None,
        }
        pointer = host / ".git" / "heddle" / "active-feature"
        assert pointer.read_text(encoding="utf-8") == "nl-screening\n"
        # The written location matches real git's --git-path answer (feature
        # resolution).
        assert pointer.resolve() == git_pointer_path(host).resolve()

    def test_rerun_is_idempotent(self, run_cli, envelope_tools, tmp_path, monkeypatch):
        host = make_git_host(tmp_path)
        monkeypatch.chdir(host)
        run_cli(["feature", "switch", "nl-screening", "--json"])
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        assert envelope["data"] == {
            "feature": "nl-screening",
            "pointer": ".git/heddle/active-feature",
            "previous": "nl-screening",
        }
        pointer = host / ".git" / "heddle" / "active-feature"
        assert pointer.read_text(encoding="utf-8") == "nl-screening\n"

    def test_switch_reports_previous(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        host = make_git_host(tmp_path)
        monkeypatch.chdir(host)
        run_cli(["feature", "switch", "nl-screening", "--json"])
        code, out, _err = run_cli(["feature", "switch", "tier1-quickfix", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        assert envelope["data"]["feature"] == "tier1-quickfix"
        assert envelope["data"]["previous"] == "nl-screening"
        pointer = host / ".git" / "heddle" / "active-feature"
        assert pointer.read_text(encoding="utf-8") == "tier1-quickfix\n"

    def test_unknown_slug_is_usage_listing_workspaces(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        host = make_git_host(tmp_path)
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["feature", "switch", "unknown-slug", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        blob = envelope["error"]["message"] + envelope["error"]["hint"]
        assert "nl-screening" in blob and "tier1-quickfix" in blob, (
            "FAIL: the unknown-slug error must list known workspaces"
        )

    def test_linked_worktree_pointer_lands_in_private_git_dir(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        host = make_git_host(tmp_path)
        worktree = tmp_path / "wt"
        _git(["worktree", "add", "-q", str(worktree), "-b", "edge-wt"], host)
        monkeypatch.chdir(worktree)
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        expected_pointer = git_pointer_path(worktree).resolve()
        # Linked worktree: the pointer lives in the worktree's PRIVATE git
        # dir (under the main repo's .git/worktrees/<name>/) — the payload
        # carries the pinned absolute form.
        assert envelope["data"]["feature"] == "nl-screening"
        assert Path(envelope["data"]["pointer"]).is_absolute()
        assert Path(envelope["data"]["pointer"]).resolve() == expected_pointer
        assert expected_pointer.read_text(encoding="utf-8") == "nl-screening\n"
        assert str(expected_pointer).startswith(str(host.resolve())), (
            "FAIL: the linked-worktree pointer must live under the main "
            "repo's .git/worktrees/<name>/"
        )
        # The main checkout's resolution is unaffected: no pointer record
        # exists at the main checkout's derived location.
        assert not (host / ".git" / "heddle" / "active-feature").exists()

    def test_outside_git_is_usage_error_writing_nothing(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # .heddle.yaml present but no .git anywhere: the pointer has no home
        # (feature resolution) — usage error naming the --feature alternative; no
        # fallback
        # location is invented.
        host = tmp_path / "no-git"
        shutil.copytree(GOLDEN, host)
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 2
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        assert "--feature" in (
            envelope["error"]["message"] + envelope["error"]["hint"]
        ), "FAIL: the hint must name the per-invocation --feature alternative"
        assert not (host / ".git").exists(), "FAIL: nothing may be written"

    def test_malformed_config_maps_to_usage_never_fatal(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # feature switch's exit-code contract has no 3: any config-load
        # KernelError maps to usage (exit 2), the cause staying visible.
        host = make_git_host(tmp_path)
        (host / ".heddle.yaml").write_text(
            MALFORMED_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
        )
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 2, (
            f"FAIL: malformed config must map to usage/exit 2, never 3 (got {code})"
        )
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"

    def test_pointer_write_failure_maps_to_usage(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # AC-11 / Checkpoint 2 R2 (review): a pointer-write I/O
        # failure maps to usage/exit 2 naming the unwritable path and the
        # --feature alternative — never the generic internal failure (A6).
        # Fault-injected on exactly the pointer write (permission bits do
        # not bind for root).
        host = make_git_host(tmp_path)
        monkeypatch.chdir(host)
        real_write_text = Path.write_text

        def deny_pointer_write(self, *args, **kwargs):
            # The atomic write targets a sibling temp file first (8→9 gate
            # configuration), so match the pointer name as a prefix.
            if self.name.startswith("active-feature"):
                raise PermissionError(13, "Permission denied", str(self))
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", deny_pointer_write)
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 2, (
            f"FAIL: a pointer-write failure must map to usage/exit 2, "
            f"never internal/1 (got {code})"
        )
        envelope = envelope_tools.parse(out)
        assert envelope["error"]["code"] == "usage"
        blob = envelope["error"]["message"] + envelope["error"]["hint"]
        assert "active-feature" in blob, "FAIL: must name the unwritable path"
        assert "--feature" in blob, (
            "FAIL: the hint must name the per-invocation --feature alternative"
        )

    def test_failed_write_preserves_prior_pointer(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # 8→9 gate configuration (robustness review): a failed switch must not destroy
        # the previously recorded pointer — the atomic temp+rename write
        # fails before touching it, and no temp debris remains.
        host = make_git_host(tmp_path)
        monkeypatch.chdir(host)
        code, _out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 0
        pointer = host / ".git" / "heddle" / "active-feature"
        assert pointer.read_text(encoding="utf-8") == "nl-screening\n"
        real_write_text = Path.write_text

        def deny_pointer_write(self, *args, **kwargs):
            if self.name.startswith("active-feature"):
                raise OSError(28, "No space left on device", str(self))
            return real_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", deny_pointer_write)
        code, out, _err = run_cli(["feature", "switch", "tier1-quickfix", "--json"])
        assert code == 2
        assert envelope_tools.parse(out)["error"]["code"] == "usage"
        assert pointer.read_text(encoding="utf-8") == "nl-screening\n", (
            "FAIL: a failed switch must leave the prior pointer intact "
            "(atomic recovery — the old write_text truncated before failing)"
        )
        leftovers = sorted(
            entry.name
            for entry in pointer.parent.iterdir()
            if entry.name != "active-feature"
        )
        assert leftovers == [], (
            f"FAIL: the failed atomic write left temp debris: {leftovers}"
        )

    def test_switch_envelope_carries_config_diagnostics(
        self, run_cli, envelope_tools, tmp_path, monkeypatch
    ):
        # REQ-23 on the feature-switch surface (review foundation): an
        # advisory config-unknown-key diagnostic from a successfully loaded
        # config rides the switch envelope and renders as a human `note:`
        # line; the data payload stays the pinned three keys.
        host = make_git_host(tmp_path)
        config_path = host / ".heddle.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\nci:\n  pipeline: example\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(host)
        code, out, _err = run_cli(["feature", "switch", "nl-screening", "--json"])
        assert code == 0
        envelope = envelope_tools.parse(out)
        envelope_tools.assert_shape(envelope)
        assert envelope["data"] == {
            "feature": "nl-screening",
            "pointer": ".git/heddle/active-feature",
            "previous": None,
        }, "FAIL: diagnostics must not change the pinned payload"
        codes = [d["code"] for d in envelope["diagnostics"]]
        assert CONFIG_UNKNOWN_KEY in codes, (
            "FAIL: the config advisory must ride the switch envelope (REQ-23)"
        )
        code, human_out, _err = run_cli(["feature", "switch", "nl-screening"])
        assert code == 0
        assert f"note: {CONFIG_UNKNOWN_KEY}:" in human_out, (
            "FAIL: REQ-23's human form — one `note:` line per diagnostic"
        )
