"""Repository skill classification, generation, and unsafe-path checks."""

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
    claude = tmp_path / ".claude/skills"
    mirror = tmp_path / ".codex/skills"
    native = tmp_path / ".agents/skills"
    shutil.copytree(REPO / ".claude/skills", claude)
    shutil.copytree(REPO / ".codex/skills", mirror)
    shutil.copytree(REPO / ".agents/skills", native)
    return script, claude, mirror, native


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
    script, claude, mirror, native = fixture(tmp_path)
    target = mirror / "root-cause-analysis/SKILL.md"
    if damage == "missing":
        target.unlink()
    else:
        target.write_text("stale mirror\n")
    before_check = snapshot(claude), snapshot(mirror), snapshot(native)
    assert run(script).returncode != 0
    assert (snapshot(claude), snapshot(mirror), snapshot(native)) == before_check

    original_claude = snapshot(claude)
    original_native = snapshot(native)
    generated = run(script, "--generate")
    assert generated.returncode == 0, f"{generated.stdout} {generated.stderr}"
    mirrored = snapshot(mirror)
    assert {key: value[0] for key, value in mirrored.items()} == {
        key: original_claude[key][0] for key in mirrored
    }
    assert len(mirrored) == 5

    before = snapshot(mirror)
    assert run(script, "--generate").returncode == 0
    assert snapshot(mirror) == before
    assert snapshot(claude) == original_claude
    assert snapshot(native) == original_native
    assert run(script).returncode == 0


@pytest.mark.parametrize("inventory", ["claude", "native"])
@pytest.mark.parametrize("mode", [(), ("--generate",)])
def test_unclassified_provider_skill_is_preserved_and_refused(
    tmp_path, inventory, mode
):
    script, claude, mirror, native = fixture(tmp_path)
    selected = claude if inventory == "claude" else native
    extra = selected / "operator-owned/SKILL.md"
    extra.parent.mkdir()
    extra.write_text("operator owned\n")
    before = snapshot(selected), snapshot(mirror)
    result = run(script, *mode)
    assert result.returncode != 0
    assert (snapshot(selected), snapshot(mirror)) == before
    assert "unclassified" in result.stdout


@pytest.mark.parametrize(
    ("inventory", "relative"),
    [
        ("claude", Path("implement-with-opus/SKILL.md")),
        ("native", Path("implement-with-sol/SKILL.md")),
    ],
)
@pytest.mark.parametrize("mode", [(), ("--generate",)])
def test_missing_provider_specific_skill_is_not_generated(
    tmp_path, inventory, relative, mode
):
    script, claude, mirror, native = fixture(tmp_path)
    selected = claude if inventory == "claude" else native
    (selected / relative).unlink()
    before = snapshot(claude), snapshot(mirror), snapshot(native)
    result = run(script, *mode)
    assert result.returncode != 0
    assert (snapshot(claude), snapshot(mirror), snapshot(native)) == before
    assert "missing" in result.stdout


@pytest.mark.parametrize(
    "position",
    [
        f"{inventory}-{location}"
        for inventory in ("source", "mirror", "native")
        for location in ("file", "parent", "root", "container")
    ],
)
@pytest.mark.parametrize("mode", [(), ("--generate",)])
def test_ac10_generation_refuses_symlinks_without_touching_outside(
    tmp_path, position, mode
):
    script, claude, mirror, native = fixture(tmp_path)
    inventory, location = position.split("-")
    selected = {"source": claude, "mirror": mirror, "native": native}[inventory]
    relative = (
        Path("implement-with-sol/SKILL.md")
        if inventory == "native"
        else Path("root-cause-analysis/SKILL.md")
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "SKILL.md"
    external.write_bytes((selected / relative).read_bytes())
    if location in {"root", "container"}:
        target = selected if location == "root" else selected.parent
        external_tree = outside / "tree"
        shutil.copytree(target, external_tree)
        shutil.rmtree(target)
        target.symlink_to(external_tree, target_is_directory=True)
    elif location == "parent":
        shutil.rmtree(selected / relative.parent)
        (selected / relative.parent).symlink_to(outside, target_is_directory=True)
    else:
        target = selected / relative
        target.unlink()
        target.symlink_to(external)
    before = snapshot(outside)
    result = run(script, *mode)
    assert result.returncode != 0, "unsafe skill path was followed"
    assert snapshot(outside) == before


@pytest.mark.parametrize("mode", [(), ("--generate",)])
def test_ac10_survivor_unknown_mirror_is_preserved(tmp_path, mode):
    script, _claude, mirror, _native = fixture(tmp_path)
    (mirror / "root-cause-analysis/SKILL.md").write_text("stale known mirror\n")
    extra = mirror / "operator-owned/SKILL.md"
    extra.parent.mkdir()
    extra.write_text("operator owned\n")
    before = snapshot(mirror)
    result = run(script, *mode)
    assert result.returncode != 0 and snapshot(mirror) == before
