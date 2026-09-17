"""W5 AC-10 repository-only generation and unsafe-path discrimination."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def fixture(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "check-skill-sync.py"
    shutil.copyfile(REPO / "scripts/check-skill-sync.py", script)
    source = tmp_path / ".claude/skills"
    mirror = tmp_path / ".codex/skills"
    shutil.copytree(REPO / ".claude/skills", source)
    shutil.copytree(source, mirror)
    return script, source, mirror


def run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=script.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )


def snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("SKILL.md")
    }


@pytest.mark.parametrize("damage", ["missing", "stale"])
def test_ac10_generate_repairs_mirrors_then_is_exact_noop(tmp_path, damage):
    script, source, mirror = fixture(tmp_path)
    target = mirror / "root-cause-analysis/SKILL.md"
    if damage == "missing":
        target.unlink()
    else:
        target.write_text("stale mirror\n")
    before_check = snapshot(source), snapshot(mirror)
    assert run(script).returncode != 0
    assert (snapshot(source), snapshot(mirror)) == before_check
    original_source = snapshot(source)
    generated = run(script, "--generate")
    assert generated.returncode == 0, (
        f"FAIL W5 AC-10: {generated.stdout} {generated.stderr}"
    )
    assert {key: value[0] for key, value in snapshot(mirror).items()} == {
        key: value[0] for key, value in original_source.items()
    }
    before = snapshot(mirror)
    assert run(script, "--generate").returncode == 0
    assert snapshot(mirror) == before and snapshot(source) == original_source
    assert len(before) == 5
    assert run(script).returncode == 0


@pytest.mark.parametrize(
    "position",
    [
        "source-file",
        "mirror-file",
        "source-parent",
        "mirror-parent",
        "source-root",
        "mirror-root",
        "source-container",
        "mirror-container",
    ],
)
@pytest.mark.parametrize("mode", [(), ("--generate",)])
def test_ac10_generation_refuses_symlinks_without_touching_outside(
    tmp_path, position, mode
):
    script, source, mirror = fixture(tmp_path)
    relative = Path("root-cause-analysis/SKILL.md")
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "SKILL.md"
    external.write_bytes((source / relative).read_bytes())
    if position.endswith(("root", "container")):
        selected = source if position.startswith("source") else mirror
        target = selected if position.endswith("root") else selected.parent
        external_tree = outside / "tree"
        shutil.copytree(target, external_tree)
        shutil.rmtree(target)
        target.symlink_to(external_tree, target_is_directory=True)
    elif position.endswith("parent"):
        parent = source if position == "source-parent" else mirror
        shutil.rmtree(parent / relative.parent)
        (parent / relative.parent).symlink_to(outside, target_is_directory=True)
    else:
        target = (source if position == "source-file" else mirror) / relative
        target.unlink()
        target.symlink_to(external)
    before = snapshot(outside)
    result = run(script, *mode)
    assert result.returncode != 0, "FAIL W5 AC-10: unsafe skill path was followed"
    assert snapshot(outside) == before


@pytest.mark.parametrize("mode", [(), ("--generate",)])
def test_ac10_survivor_unknown_mirror_is_preserved(tmp_path, mode):
    """Survivor: an orphan is an error, never silent pruning."""
    script, _source, mirror = fixture(tmp_path)
    (mirror / "root-cause-analysis/SKILL.md").write_text("stale known mirror\n")
    extra = mirror / "operator-owned/SKILL.md"
    extra.parent.mkdir()
    extra.write_text("operator owned\n")
    before = snapshot(mirror)
    result = run(script, *mode)
    assert result.returncode != 0 and snapshot(mirror) == before
