from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from heddle.contracts import operations as ops
from heddle.kernel.project_config import KernelError
from heddle.runtime import init_host
from heddle.runtime.application import execute
from tests.runtime.wheel_harness import build_installed_wheel, snapshot_tree


def existing_host(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    (root / ".git").mkdir(parents=True)
    (root / ".heddle.yaml").write_text(
        "layout: {plans: custom-plans}\n"
        "agents: {claude: false, codex: false}\n"
        "sync: {mirror: null}\n"
    )
    principles = root / "docs/workflow/engineering-principles.md"
    principles.parent.mkdir(parents=True)
    principles.write_text(
        "---\nstatus: ratified\n---\n\n# Host principles\n\n"
        "## Inviolables\n\n- Preserve owner-authored content.\n"
    )
    (root / "AGENTS.md").write_text("Host instructions stay intact.\n")
    return root


def test_explicit_adoption_preserves_existing_files_and_records_exact_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = existing_host(tmp_path)
    monkeypatch.chdir(root)
    before = snapshot_tree(root)
    refusal = execute(ops.Init())
    assert not refusal.ok and refusal.error is not None
    assert "--adopt-existing" in refusal.error.hint
    assert snapshot_tree(root) == before
    preview = execute(ops.Init(dry_run=True, adopt_existing=True))
    assert preview.ok, preview.to_envelope()
    assert snapshot_tree(root) == before
    assert preview.data is not None
    rows = {row["path"]: row["action"] for row in preview.data["targets"]}
    assert rows[".heddle.yaml"] == "accept"
    assert rows["docs/workflow/engineering-principles.md"] == "accept"
    original = {
        name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns)
        for name in (".heddle.yaml", "docs/workflow/engineering-principles.md")
    }
    result = execute(ops.Init(adopt_existing=True))
    assert result.ok, result.to_envelope()
    for name, (content, mtime) in original.items():
        assert (root / name).read_bytes() == content
        assert (root / name).stat().st_mtime_ns == mtime
    assert (root / "AGENTS.md").read_text().startswith("Host instructions stay intact.")
    for entry in init_host.read_init_lock(root):
        assert (
            entry.sha256 == hashlib.sha256((root / entry.path).read_bytes()).hexdigest()
        )
    adopted = snapshot_tree(root)
    assert execute(ops.Init()).ok
    assert execute(ops.Init(adopt_existing=True)).ok
    assert snapshot_tree(root) == adopted
    assert execute(ops.Doctor()).ok


def test_adoption_does_not_ratify_or_refresh_existing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = existing_host(tmp_path)
    monkeypatch.chdir(root)
    principles = root / "docs/workflow/engineering-principles.md"
    principles.write_text("---\nstatus: template\n---\n\n# Owner draft\n")
    original = principles.read_bytes()
    assert execute(ops.Init(adopt_existing=True)).ok
    assert principles.read_bytes() == original
    lock = (root / ".heddle.lock").read_bytes()
    principles.write_text("Host edits after adoption\n")
    assert execute(ops.Init(adopt_existing=True)).ok
    assert (root / ".heddle.lock").read_bytes() == lock
    assert principles.read_text() == "Host edits after adoption\n"


@pytest.mark.parametrize(
    "fault",
    [
        "invalid-config",
        "config-symlink",
        "principles-directory",
        "markers",
        "mirror",
        "lock",
    ],
)
def test_adoption_does_not_bypass_structural_faults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = existing_host(tmp_path)
    monkeypatch.chdir(root)
    if fault == "invalid-config":
        (root / ".heddle.yaml").write_text("layout: [\n")
    elif fault == "config-symlink":
        target = root / ".heddle.yaml"
        target.rename(root / "external-config")
        target.symlink_to(root / "external-config")
    elif fault == "principles-directory":
        target = root / "docs/workflow/engineering-principles.md"
        target.unlink()
        target.mkdir()
    elif fault == "markers":
        (root / "AGENTS.md").write_text("<!-- heddle:begin session-entry -->\n")
    elif fault == "mirror":
        config = root / ".heddle.yaml"
        config.write_text(
            config.read_text().replace("mirror: null", "mirror: CLAUDE.md")
        )
        (root / "CLAUDE.md").write_text("Independent host instructions\n")
    else:
        (root / ".heddle.lock").write_text("schema: invalid\n")
    before = snapshot_tree(root)
    result = execute(ops.Init(adopt_existing=True))
    assert not result.ok
    assert snapshot_tree(root) == before


def test_adoption_rejects_changed_accepted_file_before_writing(
    tmp_path: Path,
) -> None:
    root = existing_host(tmp_path)
    plan = init_host.plan_init(root, adopt_existing=True)
    (root / ".heddle.yaml").write_text("layout: {plans: changed}\n")
    before = snapshot_tree(root)
    with pytest.raises(KernelError, match="changed during init"):
        init_host.apply_init(plan)
    assert snapshot_tree(root) == before


def test_adoption_rechecks_accepted_files_before_writing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = existing_host(tmp_path)
    plan = init_host.plan_init(root, adopt_existing=True)
    install = init_host.install_projection

    def changing_install(destination: Path, text: str, *, create: bool) -> None:
        install(destination, text, create=create)
        if destination.name == "AGENTS.md":
            (root / ".heddle.yaml").write_text("layout: {plans: changed}\n")

    monkeypatch.setattr(init_host, "install_projection", changing_install)
    with pytest.raises(KernelError, match="changed during init"):
        init_host.apply_init(plan)
    assert not (root / ".heddle.lock").exists()
    assert (root / ".heddle.yaml").read_text() == "layout: {plans: changed}\n"


@pytest.mark.parametrize(
    "arguments",
    [["--adopt-existing", "--adopt-existing"], ["--adopt-existing", "--unknown"]],
)
def test_adoption_rejects_invalid_flags_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    root = existing_host(tmp_path)
    monkeypatch.chdir(root)
    before = snapshot_tree(root)
    assert init_host.run_init(arguments, json_mode=True) == 2
    assert snapshot_tree(root) == before


def test_adoption_typed_command_renders_explicit_flag() -> None:
    assert ops.operation_command(ops.Init(dry_run=True, adopt_existing=True)) == (
        "heddle init --adopt-existing --dry-run"
    )


@pytest.mark.toolchain
def test_installed_adoption_preserves_host_and_clears_doctor(tmp_path: Path) -> None:
    installed = build_installed_wheel(tmp_path / "wheel")
    root = existing_host(tmp_path)
    before = snapshot_tree(root)
    preview = installed.run("init", "--adopt-existing", "--dry-run", "--json", cwd=root)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert snapshot_tree(root) == before
    original = {
        name: (root / name).read_bytes()
        for name in (".heddle.yaml", "docs/workflow/engineering-principles.md")
    }
    applied = installed.run("init", "--adopt-existing", "--json", cwd=root)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    for name, content in original.items():
        assert (root / name).read_bytes() == content
    doctor = installed.run("doctor", "--json", cwd=root)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    adopted = snapshot_tree(root)
    rerun = installed.run("init", "--adopt-existing", "--json", cwd=root)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert snapshot_tree(root) == adopted
