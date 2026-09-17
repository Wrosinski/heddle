"""Focused contracts for the reusable host-guidance bundle (AC-1..AC-9)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE_SKILL = "architecture-proposal-review"
WORKTREE_SKILL = "worktree-workflow"
CLEANUP_INVENTORY_COMMANDS = (
    "diff --name-status --",
    "diff --cached --name-status --",
    "ls-files --others --exclude-standard",
    "ls-files --others --ignored --exclude-standard",
)


def _skill(agent: str, name: str) -> Path:
    return ROOT / f".{agent}" / "skills" / name / "SKILL.md"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _frontmatter(text: str) -> Mapping[str, object]:
    lines = text.splitlines()
    assert lines and lines[0] == "---", "FAIL AC-3: missing frontmatter"
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise AssertionError("FAIL AC-3: unclosed frontmatter") from exc
    value = yaml.safe_load("\n".join(lines[1:closing]))
    assert isinstance(value, Mapping), "FAIL AC-3: frontmatter is not a mapping"
    return value


def _missing_groups(text: str, groups: Mapping[str, tuple[str, ...]]) -> list[str]:
    normalized = _normalized(text)
    return [
        label
        for label, fragments in groups.items()
        if not all(fragment.casefold() in normalized for fragment in fragments)
    ]


def _architecture_gaps(text: str) -> list[str]:
    return _missing_groups(
        text,
        {
            "claim evidence": ("verified", "contradicted", "unresolved", "evidence"),
            "dependency census": ("upstream", "downstream", "blast radius"),
            "adjacent mechanisms": ("adjacent", "absorb", "exclude", "missed"),
            "single principle source": (
                "docs/workflow/engineering-principles.md",
                "by name",
            ),
            "separate verdicts": ("architecture verdict", "delivery verdict"),
            "real alternatives": (
                "materially different",
                "benefit",
                "effort",
                "dominated",
            ),
            "owner decisions": ("owner decisions", "implications", "recommendation"),
            "read-only default": ("read-only by default", "explicitly requests"),
        },
    )


def _worktree_gaps(text: str) -> list[str]:
    return _missing_groups(
        text,
        {
            "explicit inputs": (
                "registered worktree",
                "explicit local integration ref",
            ),
            "live integration": (
                "git worktree list --porcelain",
                "rev-parse --verify head^{commit}",
                "rev-list --left-right --count",
                "merge-base --is-ancestor",
            ),
            "four inventories": CLEANUP_INVENTORY_COMMANDS,
            "fresh recheck": ("immediately before", "repeat", "object ids"),
            "no remembered authority": (
                "remembered divergence count",
                "earlier clean status",
            ),
            "blocking inventory": ("blocks ordinary removal",),
            "conjunctive eligibility": (
                "eligible case is conjunctive",
                "effective inventory is empty",
            ),
            "human equivalence exception": (
                "squash/rebase-equivalent",
                "current human confirmation",
                "empty effective inventory",
            ),
            "ignored content": (".gitignore", "not disposal authority"),
            "narrow authority": (
                "separately authorized",
                "does not authorize",
                "fetch",
                "merge",
                "push",
                "branch deletion",
                "force removal",
                "manual deletion",
                "prune",
            ),
        },
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _git_ok(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    completed = _git(cwd, *args)
    assert completed.returncode == 0, (
        f"FAIL AC-7/AC-8: git {' '.join(args)} exited "
        f"{completed.returncode}: {completed.stderr.strip()}"
    )
    return completed


def test_ac1_ac2_ac3_architecture_review_skill_contract() -> None:
    """Red discriminator: the new mirrored review skill carries P3 semantics."""
    codex = _skill("codex", ARCHITECTURE_SKILL)
    claude = _skill("claude", ARCHITECTURE_SKILL)
    assert codex.is_file() and claude.is_file(), (
        f"FAIL AC-3: missing architecture-review mirror: {[codex, claude]}"
    )
    text = codex.read_text(encoding="utf-8")
    assert text == claude.read_text(encoding="utf-8"), (
        "FAIL AC-3: architecture-review mirrors drifted"
    )

    metadata = _frontmatter(text)
    assert set(metadata) == {"name", "description"}, (
        "FAIL AC-3: frontmatter must contain only name and description"
    )
    assert metadata["name"] == ARCHITECTURE_SKILL
    description = _normalized(str(metadata["description"]))
    assert all(word in description for word in ("architecture", "proposal", "review"))

    gaps = _architecture_gaps(text)
    assert not gaps, f"FAIL AC-1/AC-2/AC-3: review contract gaps: {gaps}"

    forbidden = (
        "docs/analysis/",
        "product-principles",
        "fev",
        "heddle run-gate",
        "prompts/architecture-proposal-review",
        "/srv/private-source",
    )
    found = [fragment for fragment in forbidden if fragment in text.casefold()]
    assert not found, f"FAIL AC-3: source-only assumptions remain: {found}"

    for fragment, expected_gap in (
        ("contradicted", "claim evidence"),
        ("absorb", "adjacent mechanisms"),
        ("delivery verdict", "separate verdicts"),
        ("dominated", "real alternatives"),
    ):
        mutated = re.sub(fragment, "", text, flags=re.IGNORECASE)
        assert expected_gap in _architecture_gaps(mutated), (
            f"FAIL AC-1/AC-2: removing {fragment!r} did not break the contract"
        )


def test_ac7_ac8_ac9_worktree_cleanup_contract() -> None:
    """Red discriminator: cleanup requires fresh, conjunctive evidence."""
    codex = _skill("codex", WORKTREE_SKILL)
    claude = _skill("claude", WORKTREE_SKILL)
    assert codex.is_file() and claude.is_file(), (
        f"FAIL AC-7/AC-9: missing worktree mirror: {[codex, claude]}"
    )
    text = codex.read_text(encoding="utf-8")
    assert text == claude.read_text(encoding="utf-8"), (
        "FAIL AC-9: worktree skill mirrors drifted"
    )
    metadata = _frontmatter(text)
    description = _normalized(str(metadata["description"]))
    assert all(word in description for word in ("worktree", "audit", "cleanup")), (
        "FAIL AC-7/AC-9: worktree skill description does not route cleanup audits"
    )
    gaps = _worktree_gaps(text)
    assert not gaps, f"FAIL AC-7/AC-8/AC-9: worktree audit gaps: {gaps}"

    forbidden_commands = (
        "\ngit fetch origin\n",
        "\ngit worktree prune\n",
        "rm -rf",
    )
    found = [command.strip() for command in forbidden_commands if command in text]
    assert not found, f"FAIL AC-9: implicit destructive/remote commands: {found}"
    assert "docs/references/tooling/git-worktrees.md" not in text, (
        "FAIL AC-9: worktree skill points to a missing repository reference"
    )

    for fragment, expected_gap in (
        ("merge-base --is-ancestor", "live integration"),
        ("ls-files --others --ignored --exclude-standard", "four inventories"),
        ("current human confirmation", "human equivalence exception"),
        ("remembered divergence count", "no remembered authority"),
        ("blocks ordinary removal", "blocking inventory"),
        ("eligible case is conjunctive", "conjunctive eligibility"),
        ("does not authorize", "narrow authority"),
    ):
        mutated = text.replace(fragment, "")
        assert expected_gap in _worktree_gaps(mutated), (
            f"FAIL AC-7/AC-8: removing {fragment!r} did not break the contract"
        )


def test_ac7_ac8_git_primitives_expose_each_cleanup_input(tmp_path: Path) -> None:
    """Red discriminator: documented primitives distinguish all audit inputs."""
    _git_ok(tmp_path, "init", "-q", "-b", "main")
    _git_ok(tmp_path, "config", "user.email", "audit@example.invalid")
    _git_ok(tmp_path, "config", "user.name", "Audit Fixture")
    (tmp_path / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git_ok(tmp_path, "add", ".gitignore", "tracked.txt")
    _git_ok(tmp_path, "commit", "-q", "-m", "base")

    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git_ok(tmp_path, "add", "staged.txt")
    (tmp_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (tmp_path / "cache.ignored").write_text("ignored\n", encoding="utf-8")

    assert "M\ttracked.txt" in _git(tmp_path, "diff", "--name-status", "--").stdout
    assert (
        "A\tstaged.txt"
        in _git(tmp_path, "diff", "--cached", "--name-status", "--").stdout
    )
    assert _git(tmp_path, "ls-files", "--others", "--exclude-standard").stdout == (
        "untracked.txt\n"
    )
    assert (
        _git(
            tmp_path,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
        ).stdout
        == "cache.ignored\n"
    )

    head = _git(tmp_path, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40,64}", head)
    divergence = _git(
        tmp_path, "rev-list", "--left-right", "--count", f"{head}...{head}"
    )
    assert divergence.stdout == "0\t0\n"
    assert _git(tmp_path, "merge-base", "--is-ancestor", head, head).returncode == 0

    skill = _skill("codex", WORKTREE_SKILL).read_text(encoding="utf-8")
    missing = [
        command for command in CLEANUP_INVENTORY_COMMANDS if command not in skill
    ]
    assert not missing, f"FAIL AC-7/AC-8: skill omits proven Git inputs: {missing}"


def test_ac7_ac8_worktree_states_discriminate_eligibility(tmp_path: Path) -> None:
    """A real worktree distinguishes contained-clean, stale, and uncontained."""
    repository = tmp_path / "repository"
    target = tmp_path / "target"
    repository.mkdir()
    _git_ok(repository, "init", "-q", "-b", "main")
    _git_ok(repository, "config", "user.email", "audit@example.invalid")
    _git_ok(repository, "config", "user.name", "Audit Fixture")
    (repository / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git_ok(repository, "add", ".gitignore", "tracked.txt")
    _git_ok(repository, "commit", "-q", "-m", "base")
    _git_ok(repository, "worktree", "add", "-q", "-b", "feature", str(target))

    worktrees = _git_ok(repository, "worktree", "list", "--porcelain").stdout
    assert f"worktree {target.resolve()}\n" in worktrees
    assert _git_ok(target, "rev-parse", "--show-toplevel").stdout.strip() == str(
        target.resolve()
    )
    integration = _git_ok(
        target, "rev-parse", "--verify", "refs/heads/main^{commit}"
    ).stdout.strip()
    target_head = _git_ok(
        target, "rev-parse", "--verify", "HEAD^{commit}"
    ).stdout.strip()
    contained = (
        _git(target, "merge-base", "--is-ancestor", target_head, integration).returncode
        == 0
    )
    assert contained
    assert (
        _git(
            target,
            "rev-list",
            "--left-right",
            "--count",
            f"{target_head}...{integration}",
        ).stdout
        == "0\t0\n"
    )
    inventory_empty = all(
        _git(target, *command.split()).stdout == ""
        for command in CLEANUP_INVENTORY_COMMANDS
    )
    assert contained and inventory_empty

    inventory_probe = target / "inventory-probe.txt"
    inventory_probe.write_text("blocks ordinary removal\n", encoding="utf-8")
    inventory_empty = all(
        _git(target, *command.split()).stdout == ""
        for command in CLEANUP_INVENTORY_COMMANDS
    )
    assert contained and not inventory_empty
    assert not (contained and inventory_empty)
    inventory_probe.unlink()

    stale = _git(target, "rev-parse", "--verify", "refs/heads/missing^{commit}")
    assert stale.returncode != 0 and stale.stdout == ""

    (target / "tracked.txt").write_text("diverged\n", encoding="utf-8")
    _git_ok(target, "add", "tracked.txt")
    _git_ok(target, "commit", "-q", "-m", "diverge")
    target_head = _git_ok(
        target, "rev-parse", "--verify", "HEAD^{commit}"
    ).stdout.strip()
    containment = _git(target, "merge-base", "--is-ancestor", target_head, integration)
    assert containment.returncode == 1
    assert (
        _git(
            target,
            "rev-list",
            "--left-right",
            "--count",
            f"{target_head}...{integration}",
        ).stdout
        == "1\t0\n"
    )
    inventory_empty = all(
        _git(target, *command.split()).stdout == ""
        for command in CLEANUP_INVENTORY_COMMANDS
    )
    assert inventory_empty and not (containment.returncode == 0 and inventory_empty)
