"""
adoption installed-host portability lane (test scaffolding red scaffolding).

The module is the approved integrated acceptance instrument for
``adoption-adoption-core``.  It builds one non-editable wheel, runs commands only
through that installed console script, and creates a fresh throwaway Git host
for every scenario.  There is no network, model, or paid-call lane.

Every test is either:

* a red discriminator for behavior introduced by adoption; or
* an explicitly named survivor pin for an existing seam adoption relies on.

There are no skips or expected failures.  Milestone commands select ``foundation``,
then ``foundation or integration``, before the complete file becomes the gate acceptance
lane.

Behavior contract: adoption-adoption-core
"""

from __future__ import annotations

import errno
import hashlib
import importlib
import importlib.resources
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from heddle.kernel.managed_regions import begin_marker, end_marker
from heddle.kernel.project_config import KernelError
from heddle.runtime.sync import (
    SESSION_ENTRY_ID,
    install_projection,
    render_session_entry,
)
from tests.content_identity_helpers import git
from tests.runtime.verification_provenance_helpers import make_current_host
from tests.runtime.wheel_harness import (
    REPO_ROOT,
    InstalledWheel,
    admit_installed_feature,
    build_installed_wheel,
    file_hashes,
    make_git_host,
    parse_envelope,
    snapshot_tree,
    write_claude_shim,
    write_executable,
)
from tests.tiering_helpers import ASTRA, FABLE, ROLES, entry, prepare_input, wire_policy

pytestmark = pytest.mark.acceptance

TARGET_SPECS = (
    (".heddle.yaml", "scaffold-once"),
    ("AGENTS.md", "managed-region"),
    # search root-mirror increment (owner-ruled 2026-09-11): the default
    # `sync.mirror` follows AGENTS.md and is never lock-recorded.
    ("CLAUDE.md", "mirror"),
    ("docs/workflow/engineering-principles.md", "scaffold-once"),
    (".heddle.lock", "runtime-owned"),
)
FOOTPRINT = tuple(path for path, _class in TARGET_SPECS)
LOCKED_FOOTPRINT = tuple(
    path for path, class_ in TARGET_SPECS if class_ not in {"mirror", "runtime-owned"}
)
EXPECTED_CLASS_BY_PATH = dict(TARGET_SPECS)
SESSION_BEGIN = begin_marker(SESSION_ENTRY_ID)
SESSION_END = end_marker(SESSION_ENTRY_ID)
MANAGED_AGENTS = SESSION_BEGIN + "\n" + render_session_entry() + SESSION_END + "\n"
CONVERGED_ACTIONS = ("skip", "skip", "accept", "skip", "accept")


@pytest.fixture(scope="module")
def installed_wheel(tmp_path_factory: pytest.TempPathFactory) -> InstalledWheel:
    return build_installed_wheel(tmp_path_factory.mktemp("m8a-wheel"))


def _run_json(
    installed: InstalledWheel,
    host: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], subprocess.CompletedProcess[str]]:
    result = installed.run(*args, cwd=host, env=env)
    return result.returncode, parse_envelope(result), result


def _assert_success(
    installed: InstalledWheel,
    cwd: Path,
    *args: str,
) -> dict[str, Any]:
    code, envelope, result = _run_json(installed, cwd, *args)
    assert code == 0 and envelope["ok"] is True, (
        f"FAIL M8A prerequisite: {' '.join(args)} exited {code}\n"
        f"envelope={envelope!r}\nstderr={result.stderr!r}"
    )
    return envelope


def _admit_feature(
    installed: InstalledWheel,
    host: Path,
    cwd: Path,
    payload_dir: Path,
    *,
    reviewed: bool = False,
    flow: str | None = None,
) -> dict[str, Any]:
    _commit_source_baseline(host)
    overrides = {
        role: entry(role, mode="off", limit=None, minimum_rounds=0) for role in ROLES
    }
    if reviewed:
        overrides["spec-review"] = entry("spec-review", primary=FABLE, secondary=ASTRA)
    return admit_installed_feature(
        installed,
        host=host,
        cwd=cwd,
        payload_dir=payload_dir,
        feature="lane-probe",
        area="generators",
        prepare_payload=prepare_input(),
        policy_payload=wire_policy(overrides=overrides),
        flow=flow,
    )


def _commit_source_baseline(host: Path) -> None:
    git(host, "config", "user.name", "Installed Fixture")
    git(host, "config", "user.email", "fixture@example.invalid")
    git(host, "add", ".")
    git(host, "commit", "-qm", "installed host baseline")


def _require_init_module(ac_id: str) -> ModuleType:
    try:
        return importlib.import_module("heddle.runtime.init_host")
    except ModuleNotFoundError as error:
        if error.name != "heddle.runtime.init_host":
            raise
        raise AssertionError(
            f"FAIL {ac_id}: heddle.runtime.init_host is absent; the named init "
            "planner/executor behavior has not landed"
        ) from None


def _ratify_principles(host: Path) -> None:
    path = host / "docs" / "workflow" / "engineering-principles.md"
    text = path.read_text(encoding="utf-8")
    frontmatter = re.match(r"\A---\n(?P<body>.*?)\n---\n", text, flags=re.DOTALL)
    if frontmatter is None:
        text = "---\nstatus: ratified\n---\n\n" + text
    else:
        body = frontmatter.group("body")
        if re.search(r"^status:.*$", body, flags=re.MULTILINE):
            body = re.sub(
                r"^status:.*$",
                "status: ratified",
                body,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            body = "status: ratified\n" + body
        text = f"---\n{body}\n---\n" + text[frontmatter.end() :]
    text += "\n## Principles, ranked\n\n1. Preserve host-owned bytes.\n"
    path.write_text(text, encoding="utf-8")


def _write_manual_initialized_host(host: Path) -> None:
    """A zero-feature doctor host independent of the not-yet-landed init."""
    (host / ".heddle.yaml").write_text(
        "layout:\n  plans: journey-plans\n",
        encoding="utf-8",
    )
    (host / "AGENTS.md").write_text(MANAGED_AGENTS, encoding="utf-8")
    (host / "CLAUDE.md").write_text(MANAGED_AGENTS, encoding="utf-8")
    principles = host / "docs" / "workflow" / "engineering-principles.md"
    principles.parent.mkdir(parents=True, exist_ok=True)
    principles.write_text(
        "---\nstatus: ratified\n---\n\n"
        "# Engineering Principles\n\n"
        "## Inviolables\n\n- Preserve host-owned bytes.\n",
        encoding="utf-8",
    )
    rows = []
    for relative in LOCKED_FOOTPRINT:
        rows.append(
            {
                "path": relative,
                "class": EXPECTED_CLASS_BY_PATH[relative],
                "sha256": hashlib.sha256((host / relative).read_bytes()).hexdigest(),
            }
        )
    (host / ".heddle.lock").write_text(
        yaml.safe_dump(
            {"schema": "heddle.lock/v1", "targets": rows},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _diagnostic(envelope: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [row for row in envelope["diagnostics"] if row.get("code") == code]


def _assert_valid_lock(host: Path) -> dict[str, Any]:
    lock = yaml.safe_load((host / ".heddle.lock").read_text(encoding="utf-8"))
    assert lock["schema"] == "heddle.lock/v1"
    assert [row["path"] for row in lock["targets"]] == sorted(LOCKED_FOOTPRINT)
    for row in lock["targets"]:
        recorded_path = host / row["path"]
        assert row["class"] == EXPECTED_CLASS_BY_PATH[row["path"]]
        assert row["sha256"] == hashlib.sha256(recorded_path.read_bytes()).hexdigest()
    return lock


def _expected_doctor_resource_assets() -> set[str]:
    from heddle.contracts.schemas import STAGES
    from heddle.gate.registry import GATES
    from heddle.runtime.feature_start import _SCAFFOLD_ASSETS

    return {
        *(gate.prompt_template for gate in GATES.values()),
        *(f"{stage}.briefing.md" for stage in STAGES),
        *_SCAFFOLD_ASSETS,
        "autonomy-addendum.md",
        "gate-failure-guide.md",
        "prompt-authoring-standards.md",
        "heddle-yaml.scaffold.yaml",
        "engineering-principles.seed.md",
    }


def _paragraph_with_terms(text: str, needle: str, *terms: str) -> str:
    matches = [
        paragraph
        for paragraph in re.split(r"\n\s*\n", text)
        if needle.casefold() in paragraph.casefold()
    ]
    satisfying = [
        paragraph
        for paragraph in matches
        if all(term.casefold() in paragraph.casefold() for term in terms)
    ]
    assert satisfying, (
        f"FAIL documentation oracle: no paragraph containing {needle!r} "
        f"also contains all semantic terms {terms!r}"
    )
    return satisfying[0]


def _heading_before(text: str, needle: str) -> str:
    offset = text.index(needle)
    headings = re.findall(r"^#{2,6}\s+(.+)$", text[:offset], flags=re.MULTILINE)
    assert headings, f"FAIL documentation oracle: no heading precedes {needle!r}"
    return headings[-1]


def _runtime_within_bound(elapsed_s: float) -> bool:
    return elapsed_s < 10.0


def _pass_spec_review_event() -> str:
    from tests.structured_review_helpers import complete_fixture_coverage
    from tests.tiering_review_helpers import review_content

    payload = review_content("spec-review")
    complete_fixture_coverage(payload, ())
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "The structured result is the review.",
                "structured_output": payload,
            }
        )
        + "\n"
    )


@pytest.mark.toolchain
def test_ac01_red_dry_run_is_exact_ordered_and_write_free(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-1 red: installed init preview publishes one exact, write-free plan."""
    host, nested = make_git_host(tmp_path, "ac01")
    before = snapshot_tree(host)
    code, envelope, result = _run_json(
        installed_wheel,
        nested,
        "init",
        "--dry-run",
        "--json",
    )

    assert code == 0 and envelope["ok"] is True, (
        "FAIL AC-1: installed `heddle init --dry-run --json` must be live; "
        f"got exit {code}, envelope={envelope!r}, stderr={result.stderr!r}"
    )
    # The shared envelope keeps its own schema id; init's payload schema is
    # published by the command manifest as `heddle.init/v0`.
    assert envelope["schema_version"] == "heddle.envelope/v2"
    assert envelope["data"] == {
        "dry_run": True,
        "targets": [
            {"path": path, "class": class_, "action": "create"}
            for path, class_ in TARGET_SPECS
        ],
    }
    assert snapshot_tree(host) == before, (
        "FAIL AC-1: dry-run changed host topology, bytes, modes, inode, or mtime"
    )


@pytest.mark.toolchain
def test_ac02_red_apply_installs_exact_footprint_lock_and_journey(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-2 red: apply writes five LF files, lock last, then routes the journey."""
    host, nested = make_git_host(tmp_path, "ac02")
    gitignore = host / ".gitignore"
    gitignore.write_bytes(b"host-owned\n")
    before_files = {row[0] for row in snapshot_tree(host) if row[1] == "file"}

    code, envelope, result = _run_json(installed_wheel, nested, "init", "--json")
    assert code == 0 and envelope["ok"] is True, (
        "FAIL AC-2: apply must succeed through the installed command; "
        f"exit={code}, envelope={envelope!r}, stderr={result.stderr!r}"
    )
    assert [row["path"] for row in envelope["data"]["targets"]] == list(FOOTPRINT)
    assert [row["outcome"] for row in envelope["data"]["targets"]] == ["done"] * 5
    assert [row["action"] for row in envelope["data"]["targets"]] == ["create"] * 5
    assert [row["command"] for row in envelope["next_actions"]] == [
        "${EDITOR:-vi} docs/workflow/engineering-principles.md",
        "heddle doctor",
        "heddle feature start <slug> --area <area>",
    ]
    assert gitignore.read_bytes() == b"host-owned\n"

    after_files = {row[0] for row in snapshot_tree(host) if row[1] == "file"}
    assert after_files - before_files == set(FOOTPRINT), (
        "FAIL AC-2: apply's added-file footprint is not exactly the five "
        f"contract paths: {sorted(after_files - before_files)}"
    )
    resource_root = installed_wheel.site_packages / "heddle" / "resources"
    assert (host / ".heddle.yaml").read_bytes() == (
        resource_root / "heddle-yaml.scaffold.yaml"
    ).read_bytes()
    assert (host / "docs" / "workflow" / "engineering-principles.md").read_bytes() == (
        resource_root / "engineering-principles.seed.md"
    ).read_bytes()
    assert (host / "AGENTS.md").read_bytes() == MANAGED_AGENTS.encode("utf-8")
    mirror = host / "CLAUDE.md"
    assert not mirror.is_symlink() and mirror.read_bytes() == (
        MANAGED_AGENTS.encode("utf-8")
    ), "FAIL AC-2: the default mirror must be a regular byte-copy of AGENTS.md"
    assert all((host / relative).read_bytes().endswith(b"\n") for relative in FOOTPRINT)
    assert all(b"\r" not in (host / relative).read_bytes() for relative in FOOTPRINT)
    _assert_valid_lock(host)


def test_ac02_red_executor_uses_atomic_seams_and_writes_lock_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-2 red unit companion: the specified executor crosses only approved seams."""
    module = _require_init_module("AC-2")
    host, _nested = make_git_host(tmp_path, "ac02-seams")
    calls: list[tuple[str, bool]] = []
    real_install = module.install_projection

    def recording_install(path: Path, text: str, *, create: bool) -> None:
        calls.append((path.relative_to(host).as_posix(), create))
        real_install(path, text, create=create)

    monkeypatch.setattr(module, "install_projection", recording_install)
    module.apply_init(module.plan_init(host))
    assert calls == [
        (".heddle.yaml", True),
        ("AGENTS.md", True),
        ("CLAUDE.md", True),
        ("docs/workflow/engineering-principles.md", True),
        (".heddle.lock", False),
    ], (
        "FAIL AC-2: every projected write must cross install_projection in "
        "footprint order with exclusive creates for projected files and "
        "same-directory replacement for the lock"
    )


@pytest.mark.toolchain
def test_ac03_red_converged_rerun_is_exact_no_write_vector(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-3 red: rerun is exactly skip/skip/accept/skip/accept and byte-stable."""
    host, nested = make_git_host(tmp_path, "ac03")
    _assert_success(installed_wheel, nested, "init", "--json")
    before_tree = snapshot_tree(host)
    before_hashes = file_hashes(host, FOOTPRINT)

    envelope = _assert_success(installed_wheel, nested, "init", "--json")
    assert (
        tuple(row["action"] for row in envelope["data"]["targets"]) == CONVERGED_ACTIONS
    )
    assert all(row["outcome"] == "done" for row in envelope["data"]["targets"])
    assert file_hashes(host, FOOTPRINT) == before_hashes
    assert snapshot_tree(host) == before_tree, (
        "FAIL AC-3: converged init changed bytes or filesystem metadata"
    )


@pytest.mark.toolchain
def test_ac04_red_occupied_non_agents_targets_preview_and_refuse_without_writes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-4 red: all foreign targets preview, then atomically block apply."""
    cases = (
        (".heddle.yaml",),
        ("docs/workflow/engineering-principles.md",),
        (".heddle.yaml", "docs/workflow/engineering-principles.md"),
    )
    for index, occupied in enumerate(cases):
        host, nested = make_git_host(tmp_path, f"ac04-{index}")
        for relative in occupied:
            target = host / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == ".heddle.yaml":
                target.write_bytes(b"layout:\n  plans: host-plans\n")
            else:
                target.write_bytes(f"host bytes {relative}\n".encode())

        before = snapshot_tree(host)
        preview_code, preview, _result = _run_json(
            installed_wheel,
            nested,
            "init",
            "--dry-run",
            "--json",
        )
        assert preview_code == 0 and preview["ok"] is True, (
            f"FAIL AC-4: occupied preview must succeed for {occupied}"
        )
        assert {
            row["path"]
            for row in preview["data"]["targets"]
            if row["action"] == "refuse"
        } == set(occupied)
        assert preview["next_actions"] == []
        assert snapshot_tree(host) == before

        apply_code, apply, _result = _run_json(
            installed_wheel, nested, "init", "--json"
        )
        assert apply_code == 3 and apply["error"]["code"] == "workspace-invalid"
        assert "data" not in apply
        refusal_rows = [
            row for row in apply["diagnostics"] if row["code"] == "init-refused-target"
        ]
        assert all(row["severity"] == "fatal" for row in refusal_rows)
        assert sorted(row["source"] for row in refusal_rows) == sorted(occupied)
        assert apply["next_actions"] == [
            {
                "action": {
                    "kind": "command",
                    "operation": {
                        "name": "init",
                        "arguments": {"dry_run": True, "adopt_existing": False},
                    },
                },
                "command": "heddle init --dry-run",
                "reason": "review or remove every refused target before applying",
            }
        ]
        assert snapshot_tree(host) == before, (
            f"FAIL AC-4: apply changed host for occupied targets {occupied}"
        )


@pytest.mark.toolchain
def test_ac05_red_untracked_agents_integration_preserves_host_bytes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-5 red: markerless and stale-pair AGENTS hosts integrate byte-safely."""
    scenarios = {
        "unmarked": b"# Host instructions\n\n---\nKeep this exact.\n",
        "empty": b"",
        "stale-pair": (
            b"host-prefix\r\n"
            + SESSION_BEGIN.encode()
            + b"\r\nold body\r\n"
            + SESSION_END.encode()
            + b"\r\nhost-suffix-no-newline"
        ),
    }
    for name, original in scenarios.items():
        host, nested = make_git_host(tmp_path, f"ac05-{name}")
        agents = host / "AGENTS.md"
        agents.write_bytes(original)
        preview = _assert_success(
            installed_wheel, nested, "init", "--dry-run", "--json"
        )
        agents_row = next(
            row for row in preview["data"]["targets"] if row["path"] == "AGENTS.md"
        )
        assert agents_row["action"] == "integrate"

        _assert_success(installed_wheel, nested, "init", "--json")
        rendered = agents.read_bytes()
        if name == "unmarked":
            expected = original + MANAGED_AGENTS.encode("utf-8")
        elif name == "empty":
            expected = MANAGED_AGENTS.encode("utf-8")
        else:
            expected = (
                b"host-prefix\r\n"
                + SESSION_BEGIN.encode()
                + b"\r\n"
                + render_session_entry().encode("utf-8")
                + SESSION_END.encode()
                + b"\r\nhost-suffix-no-newline"
            )
        assert rendered == expected, (
            f"FAIL AC-5: {name} integration changed bytes outside the one "
            "managed region or emitted unexpected adjacent bytes"
        )
        assert rendered.count(SESSION_BEGIN.encode()) == 1
        assert rendered.count(SESSION_END.encode()) == 1
        begin_at = rendered.index(SESSION_BEGIN.encode())
        body_at = rendered.index(b"\n", begin_at) + 1
        end_at = rendered.index(SESSION_END.encode(), body_at)
        assert rendered[body_at:end_at] == render_session_entry().encode(), (
            "FAIL AC-5: the managed region is not the exact shared "
            "render_session_entry body"
        )
        assert b"old body" not in rendered

        lock = yaml.safe_load((host / ".heddle.lock").read_text(encoding="utf-8"))
        recorded = next(row for row in lock["targets"] if row["path"] == "AGENTS.md")

        assert recorded["sha256"] == hashlib.sha256(rendered).hexdigest()


@pytest.mark.toolchain
def test_ac05_red_recorded_agents_skip_preserves_current_and_lock_bytes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-5 red: lock membership makes managed AGENTS bytes host-owned.

    The runtime-owned mirror is the one exception: on an adopted host it
    follows the host's AGENTS.md edit (integrate) instead of refusing."""
    host, nested = make_git_host(tmp_path, "ac05-recorded")
    _assert_success(installed_wheel, nested, "init", "--json")
    agents = host / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            render_session_entry(), "host-edited managed body\n"
        ),
        encoding="utf-8",
    )
    before = tuple(row for row in snapshot_tree(host) if row[0] != "CLAUDE.md")
    lock_before = (host / ".heddle.lock").read_bytes()
    envelope = _assert_success(installed_wheel, nested, "init", "--json")
    assert tuple(row["action"] for row in envelope["data"]["targets"]) == (
        "skip",
        "skip",
        "integrate",
        "skip",
        "accept",
    )
    assert (host / ".heddle.lock").read_bytes() == lock_before
    assert (host / "CLAUDE.md").read_bytes() == agents.read_bytes()
    assert tuple(row for row in snapshot_tree(host) if row[0] != "CLAUDE.md") == before


@pytest.mark.toolchain
def test_ac05_red_agents_marker_and_shape_faults_refuse_without_writes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-5 red: malformed or non-regular AGENTS targets are never followed."""
    marker_faults = {
        "begin-only": SESSION_BEGIN + "\nbody\n",
        "end-only": "body\n" + SESSION_END + "\n",
        "duplicate-begin": (
            SESSION_BEGIN + "\n" + SESSION_BEGIN + "\nbody\n" + SESSION_END + "\n"
        ),
        "duplicate-end": (
            SESSION_BEGIN + "\nbody\n" + SESSION_END + "\n" + SESSION_END + "\n"
        ),
        "reversed": SESSION_END + "\nbody\n" + SESSION_BEGIN + "\n",
    }
    for fault, text in marker_faults.items():
        host, nested = make_git_host(tmp_path, f"ac05-{fault}")
        (host / "AGENTS.md").write_text(text, encoding="utf-8")
        before = snapshot_tree(host)
        preview = _assert_success(
            installed_wheel, nested, "init", "--dry-run", "--json"
        )
        agents_row = next(
            row for row in preview["data"]["targets"] if row["path"] == "AGENTS.md"
        )
        assert agents_row["action"] == "refuse"
        code, apply, _result = _run_json(installed_wheel, nested, "init", "--json")
        assert code == 3
        assert apply["error"]["code"] == "workspace-invalid"
        assert "data" not in apply
        integrity = _diagnostic(apply, "managed-block-integrity")
        assert len(integrity) == 1
        assert integrity[0]["severity"] == "fatal"
        assert integrity[0].get("source") == "AGENTS.md"
        assert fault in json.dumps(integrity[0]).lower()
        assert apply["next_actions"][0]["command"] == "heddle init --dry-run"
        assert snapshot_tree(host) == before

    for kind in ("symlink", "directory"):
        host, nested = make_git_host(tmp_path, f"ac05-{kind}")
        agents = host / "AGENTS.md"
        if kind == "symlink":
            target = host / "host-instructions.md"
            target.write_text("do not follow\n", encoding="utf-8")
            agents.symlink_to(target.name)
        else:
            agents.mkdir()
        before = snapshot_tree(host)
        preview = _assert_success(
            installed_wheel, nested, "init", "--dry-run", "--json"
        )
        agents_row = next(
            row for row in preview["data"]["targets"] if row["path"] == "AGENTS.md"
        )
        assert agents_row["action"] == "refuse"
        code, apply, _result = _run_json(installed_wheel, nested, "init", "--json")
        assert code == 3
        assert apply["error"]["code"] == "workspace-invalid"
        assert "data" not in apply
        refused = _diagnostic(apply, "init-refused-target")
        assert len(refused) == 1
        assert refused[0]["severity"] == "fatal"
        assert refused[0].get("source") == "AGENTS.md"
        assert apply["next_actions"][0]["command"] == "heddle init --dry-run"
        assert snapshot_tree(host) == before


@pytest.mark.toolchain
def test_mirror_first_adoption_refuses_foreign_claude_md_without_writes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """search mirror: an unadopted host's differing CLAUDE.md is host-authored."""
    host, nested = make_git_host(tmp_path, "mirror-foreign")
    (host / "CLAUDE.md").write_bytes(b"hand-written Claude instructions\n")
    before = snapshot_tree(host)

    preview = _assert_success(installed_wheel, nested, "init", "--dry-run", "--json")
    rows = {row["path"]: row for row in preview["data"]["targets"]}
    assert rows["CLAUDE.md"] == {
        "path": "CLAUDE.md",
        "class": "mirror",
        "action": "refuse",
    }
    assert rows["AGENTS.md"]["action"] == "create"

    code, apply, _result = _run_json(installed_wheel, nested, "init", "--json")
    assert code == 3 and apply["error"]["code"] == "workspace-invalid"
    drift = _diagnostic(apply, "mirror-drift")
    assert len(drift) == 1 and drift[0]["severity"] == "fatal"
    assert drift[0].get("source") == "CLAUDE.md"
    assert "mirror: null" in drift[0]["message"]
    assert snapshot_tree(host) == before, (
        "FAIL mirror: a refused mirror must block every write, AGENTS.md included"
    )


@pytest.mark.toolchain
def test_mirror_symlink_and_identical_copy_are_accepted(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """search mirror: a symlink to AGENTS.md or an equal copy already satisfies."""
    for shape in ("symlink", "copy"):
        host, nested = make_git_host(tmp_path, f"mirror-{shape}")
        mirror = host / "CLAUDE.md"
        if shape == "symlink":
            mirror.symlink_to("AGENTS.md")
        else:
            mirror.write_text(MANAGED_AGENTS, encoding="utf-8")
        envelope = _assert_success(installed_wheel, nested, "init", "--json")
        rows = {row["path"]: row for row in envelope["data"]["targets"]}
        assert rows["CLAUDE.md"]["action"] == "accept", shape
        if shape == "symlink":
            assert mirror.is_symlink() and os.readlink(mirror) == "AGENTS.md"
        assert mirror.read_bytes() == (host / "AGENTS.md").read_bytes()
        _assert_valid_lock(host)


def test_mirror_opt_out_and_declared_path_follow_the_host_config(
    tmp_path: Path,
) -> None:
    """search mirror: `sync.mirror` steers the planner; null removes the row."""
    module = _require_init_module("mirror")
    host, _nested = make_git_host(tmp_path, "mirror-config")
    module.apply_init(module.plan_init(host))
    config = host / ".heddle.yaml"
    scaffold = config.read_text(encoding="utf-8")

    config.write_text(scaffold + "\nsync:\n  mirror: null\n", encoding="utf-8")
    paths = [target.path.as_posix() for target in module.plan_init(host).targets]
    assert paths == [
        ".heddle.yaml",
        "AGENTS.md",
        "docs/workflow/engineering-principles.md",
        ".heddle.lock",
    ]

    config.write_text(
        scaffold + "\nsync:\n  mirror: .claude/CLAUDE.md\n", encoding="utf-8"
    )
    plan = module.plan_init(host)
    declared = next(t for t in plan.targets if t.class_ == "mirror")
    assert declared.path.as_posix() == ".claude/CLAUDE.md"
    assert declared.action == "create"
    module.apply_init(plan)
    assert (host / ".claude" / "CLAUDE.md").read_bytes() == (
        host / "AGENTS.md"
    ).read_bytes()
    lock = yaml.safe_load((host / ".heddle.lock").read_text(encoding="utf-8"))
    assert [row["path"] for row in lock["targets"]] == sorted(LOCKED_FOOTPRINT), (
        "FAIL mirror: the mirror must never enter the adoption lock"
    )


@pytest.mark.toolchain
def test_ac05_red_init_then_sync_is_byte_noop(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-5 red: init and sync share the exact session-entry renderer."""
    host, nested = make_git_host(tmp_path, "ac05-sync")
    _assert_success(installed_wheel, nested, "init", "--json")
    _ratify_principles(host)
    _admit_feature(installed_wheel, host, nested, tmp_path / "ac05-intake")
    before = (host / "AGENTS.md").read_bytes()
    _assert_success(
        installed_wheel, nested, "sync", "--feature", "lane-probe", "--json"
    )
    assert (host / "AGENTS.md").read_bytes() == before


@pytest.mark.toolchain
def test_ac06_red_seed_is_template_and_ratification_is_preserved_by_rerun(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-6 red: the seed is unratified and lock-owned edits remain untouched."""
    host, nested = make_git_host(tmp_path, "ac06")
    _assert_success(installed_wheel, nested, "init", "--json")
    seed = host / "docs" / "workflow" / "engineering-principles.md"
    projected = seed.read_text(encoding="utf-8")
    assert "status: ratified" not in projected
    assert projected != (
        REPO_ROOT / "docs" / "workflow" / "engineering-principles.md"
    ).read_text(encoding="utf-8"), (
        "FAIL AC-6: the projected seed copied the repository owner's ratified profile"
    )
    for owner_phrase in (
        "The owner's preference profile",
        "Point-in-time integrity",
        "Core first; scope is earned",
    ):
        assert owner_phrase not in projected, (
            f"FAIL AC-6: owner-profile content leaked into the seed: {owner_phrase!r}"
        )
    for heading in ("Inviolables", "Principles, ranked", "Defaults", "Editing"):
        assert heading in projected
    assert "author" in projected.lower() and "ratif" in projected.lower()

    from heddle.kernel.model import _principles_ratified

    assert _principles_ratified(host) is False
    _ratify_principles(host)
    assert _principles_ratified(host) is True
    before = file_hashes(host, FOOTPRINT)
    lock_before = (host / ".heddle.lock").read_bytes()
    _commit_source_baseline(host)
    admit_installed_feature(
        installed_wheel,
        host=host,
        cwd=nested,
        payload_dir=tmp_path / "ac06-intake",
        feature="ratification-probe",
        area="generators",
        prepare_payload=prepare_input(),
        policy_payload=wire_policy(
            overrides={
                role: entry(role, mode="off", limit=None, minimum_rounds=0)
                for role in ROLES
            }
        ),
    )
    status = _assert_success(
        installed_wheel,
        nested,
        "status",
        "--feature",
        "ratification-probe",
        "--json",
    )
    assert "principles-not-ratified" not in status["data"]["blocking_conditions"]
    envelope = _assert_success(installed_wheel, nested, "init", "--json")
    assert [row["action"] for row in envelope["data"]["targets"]][2:] == [
        "accept",
        "skip",
        "accept",
    ]
    assert file_hashes(host, FOOTPRINT) == before
    assert (host / ".heddle.lock").read_bytes() == lock_before


@pytest.mark.toolchain
def test_ac07_red_bootstrap_failures_route_without_writes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-7 red: non-Git and malformed-config failures are specific and safe."""
    non_git = tmp_path / "ac07-non-git"
    non_git.mkdir()
    before = snapshot_tree(non_git)
    for args in (("init", "--dry-run", "--json"), ("init", "--json")):
        code, envelope, _result = _run_json(installed_wheel, non_git, *args)
        assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
        assert "data" not in envelope
        assert envelope["diagnostics"] == []
        assert len(envelope["next_actions"]) == 1
        assert envelope["next_actions"][0]["command"] == "git init"
        assert "git" in envelope["next_actions"][0]["reason"].lower()
        assert snapshot_tree(non_git) == before

    invalid_configs = {
        "syntax": b"layout: [unterminated\n",
        "top-level-sequence": b"- layout\n- agents\n",
        "wrong-typed-section": b"agents: []\n",
    }
    for name, config_bytes in invalid_configs.items():
        malformed, nested = make_git_host(tmp_path, f"ac07-malformed-{name}")
        config = malformed / ".heddle.yaml"
        config.write_bytes(config_bytes)
        before = snapshot_tree(malformed)
        for args in (("init", "--dry-run", "--json"), ("init", "--json")):
            code, envelope, _result = _run_json(installed_wheel, nested, *args)
            assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
            assert "data" not in envelope
            assert envelope["diagnostics"] == [
                {
                    "severity": "fatal",
                    "code": "config-unparsable",
                    "message": envelope["error"]["message"],
                    "source": ".heddle.yaml",
                }
            ]
            assert envelope["next_actions"] == [
                {
                    "action": {
                        "kind": "manual",
                        "instruction": f"${{EDITOR:-vi}} {shlex.quote(str(config))}",
                    },
                    "command": f"${{EDITOR:-vi}} {shlex.quote(str(config))}",
                    "reason": "fix the malformed .heddle.yaml configuration",
                }
            ]
            assert snapshot_tree(malformed) == before


def test_ac07_survivor_unsupported_hard_link_is_specific_and_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-7 survivor: the shipped exclusive-create seam already fails loudly."""
    target = tmp_path / "projected.txt"

    def unsupported(_source: object, _target: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "unsupported by test filesystem")

    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(KernelError) as captured:
        install_projection(target, "content\n", create=True)
    error = captured.value
    assert error.code == "workspace-invalid"
    assert "does not support" in error.message
    assert "atomic exclusive hard-link" in error.message
    assert "supporting" in error.hint
    assert not target.exists()
    assert not list(tmp_path.glob(".projected.txt.*.tmp"))


def test_ac07_init_preserves_unsupported_filesystem_remediation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_cli,
    envelope_tools,
) -> None:
    """AC-7: init keeps dependency errors out of the config-failure route."""
    host, nested = make_git_host(tmp_path, "ac07-init-unsupported")
    monkeypatch.chdir(nested)

    def unsupported(_source: object, _target: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "unsupported by test filesystem")

    monkeypatch.setattr(os, "link", unsupported)
    before = snapshot_tree(host)
    code, out, error = run_cli(["init", "--json"])
    assert error == ""
    envelope = envelope_tools.parse(out)
    assert code == 3 and envelope["error"]["code"] == "workspace-invalid"
    assert "supporting" in envelope["error"]["hint"]
    assert "heddle init" in envelope["error"]["hint"]
    assert not _diagnostic(envelope, "config-unparsable")
    assert envelope["next_actions"][0]["command"] == "heddle init --dry-run"
    assert snapshot_tree(host) == before


def test_ac07_init_routes_unreadable_resource_to_same_mode_reinstall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_cli,
    envelope_tools,
) -> None:
    """AC-7: a broken package does not route back into its failing resource."""
    from heddle.runtime import init_host

    host, nested = make_git_host(tmp_path, "ac07-init-resource-unreadable")
    monkeypatch.chdir(nested)
    blocked = init_host._RESOURCE_ROOT / "heddle-yaml.scaffold.yaml"
    real_read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == blocked:
            raise PermissionError(errno.EACCES, "denied by test", path)
        return real_read_bytes(path)

    before = snapshot_tree(host)
    monkeypatch.setattr(Path, "read_bytes", unreadable)
    code, out, error = run_cli(["init", "--json"])
    assert error == ""
    envelope = envelope_tools.parse(out)

    assert code == 3
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "workspace-invalid"
    assert "heddle-yaml.scaffold.yaml" in envelope["error"]["message"]
    assert "denied by test" in envelope["error"]["message"]
    hint = envelope["error"]["hint"]
    for mode in ("editable checkout", "pinned Git commit", "reviewed wheel"):
        assert mode in hint
    assert envelope["diagnostics"] == []
    assert envelope["next_actions"] == []
    assert snapshot_tree(host) == before


@pytest.mark.toolchain
def test_ac08_red_missing_root_doctor_and_status_route_to_init(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-8 red: both missing-root envelopes point to the first legal move."""
    host = tmp_path / "ac08"
    host.mkdir()
    expected_action = [
        {
            "action": {
                "kind": "command",
                "operation": {
                    "name": "init",
                    "arguments": {"dry_run": True, "adopt_existing": False},
                },
            },
            "command": "heddle init --dry-run",
            "reason": (
                "initialize this Git host before running project-scoped commands"
            ),
        }
    ]
    for command in ("doctor", "status"):
        code, envelope, _result = _run_json(installed_wheel, host, command, "--json")
        assert code == 3 and envelope["ok"] is False
        assert envelope["error"]["code"] == "workspace-invalid"
        assert "data" not in envelope
        assert envelope["next_actions"] == expected_action
        if command == "doctor":
            package_root = installed_wheel.site_packages / "heddle"
            assert envelope["diagnostics"] == [
                {
                    "severity": "info",
                    "code": "install-mode",
                    "message": (
                        "running from an installed package; "
                        f"package={package_root}; "
                        f"interpreter={installed_wheel.python}"
                    ),
                    "source": "installed",
                }
            ]
        else:
            assert envelope["diagnostics"] == []


@pytest.mark.toolchain
def test_ac09_red_fresh_host_doctor_lock_matrix_and_zero_feature_route(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-9 red: healthy zero-feature hosts pass; corrupt locks fail specifically."""
    base, nested = make_git_host(tmp_path, "ac09-base")
    _assert_success(installed_wheel, nested, "init", "--json")
    _ratify_principles(base)

    healthy_code, healthy, _result = _run_json(
        installed_wheel, nested, "doctor", "--json"
    )
    assert healthy_code == 0 and healthy["ok"] is True
    assert healthy["data"]["feature"] is None
    assert healthy["data"]["healthy"] is True
    assert _diagnostic(healthy, "parse-zero-features")
    assert _diagnostic(healthy, "lock-ok")
    resource_rows = [
        row
        for row in healthy["diagnostics"]
        if row["code"].startswith("resource-")
        and row["code"] != "resource-spec-review-prompt"
    ]
    expected_assets = _expected_doctor_resource_assets()
    assert len(resource_rows) == len(expected_assets)
    assert {
        row["code"].removeprefix("resource-") for row in resource_rows
    } == expected_assets
    for row in resource_rows:
        asset = row["code"].removeprefix("resource-")
        assert row["severity"] == "info"
        assert row["message"].startswith(f"{asset} resolved from the ")
        assert row["message"].removeprefix(f"{asset} resolved from the ").removesuffix(
            " tier"
        ) in {"override", "generated", "packaged"}
    assert healthy["next_actions"] == [
        {
            "action": {
                "kind": "manual",
                "instruction": (
                    "heddle feature prepare <slug> --area <area> "
                    "--from-file <research-input.yaml>"
                ),
            },
            "command": (
                "heddle feature prepare <slug> --area <area> "
                "--from-file <research-input.yaml>"
            ),
            "reason": (
                "record research and explicitly confirm policy before feature start"
            ),
        }
    ]

    corruptions = (
        "invalid-yaml",
        "wrong-schema",
        "missing-lock",
        "missing-path",
        "content-drift",
    )
    for corruption in corruptions:
        host = tmp_path / f"ac09-{corruption}"
        shutil.copytree(base, host)
        nested_copy = host / "nested" / "deeper"
        if corruption == "invalid-yaml":
            (host / ".heddle.lock").write_text("[invalid\n", encoding="utf-8")
        elif corruption == "wrong-schema":
            lock = yaml.safe_load((host / ".heddle.lock").read_text())
            lock["schema"] = "heddle.lock/v999"
            (host / ".heddle.lock").write_text(
                yaml.safe_dump(lock, sort_keys=False), encoding="utf-8"
            )
        elif corruption == "missing-lock":
            (host / ".heddle.lock").unlink()
        elif corruption == "missing-path":
            (host / "AGENTS.md").unlink()
        else:
            principles = host / "docs" / "workflow" / "engineering-principles.md"
            principles.write_text(
                principles.read_text(encoding="utf-8") + "\nhost drift\n",
                encoding="utf-8",
            )
        code, envelope, _result = _run_json(
            installed_wheel, nested_copy, "doctor", "--json"
        )
        if corruption == "content-drift":
            assert code == 0 and _diagnostic(envelope, "lock-ok")
            assert not any(
                "drift" in row["code"] or "drift" in row["message"].lower()
                for row in envelope["diagnostics"]
            )
        elif corruption == "missing-lock":
            assert code == 3
            assert envelope["error"]["code"] == "workspace-invalid"
            assert "data" not in envelope
            missing_lock = _diagnostic(envelope, "lock-missing")
            assert len(missing_lock) == 1
            assert missing_lock[0]["severity"] == "fatal"
            assert envelope["next_actions"] == [
                {
                    "action": {
                        "kind": "command",
                        "operation": {
                            "name": "init",
                            "arguments": {"dry_run": False, "adopt_existing": False},
                        },
                    },
                    "command": "heddle init",
                    "reason": (
                        "complete or recover host adoption before rerunning doctor"
                    ),
                }
            ]
        elif corruption == "missing-path":
            assert code == 3
            assert envelope["error"]["code"] == "workspace-invalid"
            assert "data" not in envelope
            missing = _diagnostic(envelope, "lock-recorded-path-missing")
            assert len(missing) == 1
            assert missing[0]["severity"] == "fatal"
            assert missing[0].get("source") == "AGENTS.md"
            assert envelope["next_actions"][0]["command"] == ("heddle init --dry-run")
        else:
            assert code == 3
            assert envelope["error"]["code"] == "workspace-invalid"
            assert "data" not in envelope
            invalid = _diagnostic(envelope, "lock-unparseable")
            assert len(invalid) == 1
            assert invalid[0]["severity"] == "fatal"
            assert ".heddle.lock" in json.dumps(envelope["next_actions"])
            assert "doctor" in json.dumps(envelope["next_actions"])


@pytest.mark.toolchain
def test_ac09_recorded_target_io_failure_is_not_reported_as_missing(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """AC-9: public doctor preserves an inaccessible target's I/O diagnosis."""
    from heddle.runtime import doctor

    host, nested = make_git_host(tmp_path, "ac09-unreadable")
    _assert_success(installed_wheel, nested, "init", "--json")
    _ratify_principles(host)
    blocked = host / "AGENTS.md"
    real_lstat = Path.lstat

    def permission_denied(path: Path) -> os.stat_result:
        if path == blocked:
            raise PermissionError(errno.EACCES, "denied by test", path)
        return real_lstat(path)

    monkeypatch.chdir(nested)
    monkeypatch.setattr(Path, "lstat", permission_denied)
    exit_code = doctor.run_doctor([], json_mode=True)
    envelope = json.loads(capsys.readouterr().out)

    assert exit_code == 3
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "workspace-invalid"
    assert "data" not in envelope
    unreadable = _diagnostic(envelope, "lock-recorded-path-unreadable")
    assert len(unreadable) == 1
    assert unreadable[0]["severity"] == "fatal"
    assert unreadable[0]["source"] == "AGENTS.md"
    assert "PermissionError" in unreadable[0]["message"]
    assert "denied by test" in unreadable[0]["message"]
    assert not _diagnostic(envelope, "lock-recorded-path-missing")
    assert envelope["next_actions"] == [
        {
            "action": {
                "kind": "manual",
                "instruction": f"ls -ld -- {shlex.quote(str(blocked))}",
            },
            "command": f"ls -ld -- {shlex.quote(str(blocked))}",
            "reason": (
                "inspect and restore access to every unreadable "
                "lock-recorded target before rerunning doctor"
            ),
        }
    ]


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac10_red_installed_ratified_journey_honors_nondefault_plans_layout(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-10 red: the whole post-ratification journey uses installed assets."""
    host, nested = make_git_host(tmp_path, "ac10")
    _assert_success(installed_wheel, nested, "init", "--json")
    config = host / ".heddle.yaml"
    config.write_text("layout:\n  plans: journey-plans\n", encoding="utf-8")
    _ratify_principles(host)

    start = _admit_feature(
        installed_wheel,
        host,
        nested,
        tmp_path / "ac10-intake",
        flow="hitl",
    )
    assert start["data"]["feature"] == "lane-probe"
    assert start["data"]["workspace"] == "journey-plans/lane-probe/"
    assert start["data"]["validated"] is True
    assert start["data"]["effective_policy"]["revision"] == 1
    assert "tier" not in start["data"]
    assert (host / "docs" / "features" / "generators" / "lane-probe.md").is_file()

    observed: dict[str, dict[str, Any]] = {}
    for args in (
        ("sync", "--feature", "lane-probe", "--json"),
        ("orient", "--feature", "lane-probe", "--json"),
        ("status", "--feature", "lane-probe", "--json"),
        ("kickoff", "--feature", "lane-probe", "--json"),
        ("validate", "--json"),
    ):
        envelope = _assert_success(installed_wheel, nested, *args)
        observed[args[0]] = envelope
        if args[0] == "sync":
            assert [row["block"] for row in envelope["data"]["targets"]] == [
                "plan-status",
                "session-entry",
                "mirror",
            ]
            assert envelope["data"]["feature"] == "lane-probe"
        if args[0] in {"orient", "status"}:
            assert envelope["data"]["feature"] == "lane-probe"
            assert envelope["data"]["workspace"] == "journey-plans/lane-probe/"
        if args[0] == "kickoff":
            assert envelope["data"]["feature"] == "lane-probe"
            assert envelope["data"]["source"] == "packaged"
            assert envelope["data"]["stage"] == "specify"
        if args[0] == "validate":
            assert envelope["diagnostics"] == []
            assert any(
                row["feature"] == "lane-probe" for row in envelope["data"]["workspaces"]
            )
    assert set(observed) == {"sync", "orient", "status", "kickoff", "validate"}


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac11_red_path_shim_gate_records_artifact_run_fact_and_status(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-11 red: the default Claude lane crosses the real installed spawn seam."""
    event = _pass_spec_review_event()

    host, nested = make_git_host(tmp_path, "ac11")
    _assert_success(installed_wheel, nested, "init", "--json")
    (host / ".heddle.yaml").write_text(
        "layout:\n  plans: journey-plans\n",
        encoding="utf-8",
    )
    _ratify_principles(host)
    _admit_feature(
        installed_wheel,
        host,
        nested,
        tmp_path / "ac11-intake",
        reviewed=True,
    )
    _assert_success(
        installed_wheel, nested, "phase-exit", "--feature", "lane-probe", "--json"
    )

    shim_bin = tmp_path / "ac11-shim"
    invocation_log = tmp_path / "ac11-shim.jsonl"
    write_claude_shim(shim_bin, event, invocation_log)
    shim_env = dict(installed_wheel.env)
    shim_env["PATH"] = f"{shim_bin}:{shim_env['PATH']}"
    code, gate, result = _run_json(
        installed_wheel,
        nested,
        "run-gate",
        "spec-review",
        "--feature",
        "lane-probe",
        "--json",
        env=shim_env,
    )
    assert code == 0 and gate["ok"] is True, (
        f"FAIL AC-11: fake default gate failed: {gate!r}, {result.stderr!r}"
    )
    artifact = host / gate["data"]["artifact"]
    assert artifact.is_file()
    assert json.loads(artifact.read_text())["content"]["role"] == "spec-review"
    invocation = json.loads(invocation_log.read_text(encoding="utf-8"))
    unexpanded_partials = set(
        re.findall(r"\[partial-[a-z0-9-]+\]", invocation["stdin"])
    )
    assert not unexpanded_partials, (
        f"FAIL AC-11: unexpanded prompt partials remain: {unexpanded_partials}"
    )
    gate_log = (
        host / "journey-plans" / "lane-probe" / "reviews" / "spec-review.log"
    ).read_text(encoding="utf-8")
    command_line = next(
        line.removeprefix("ClaudeCmd: ")
        for line in gate_log.splitlines()
        if line.startswith("ClaudeCmd: ")
    )
    command = shlex.split(command_line)
    assert command[:3] == ["env", "-u", "CLAUDECODE"]
    assert command[3].startswith("HEDDLE_AGENT_SESSION=")
    assert command[4].startswith("CLAUDE_CODE_EFFORT_LEVEL=")
    assert command[5] == "claude" and "/" not in command[5]
    assert command[6:11] == [
        "--print",
        "--input-format",
        "text",
        "--model",
        command[10],
    ]
    assert command[11:14] == ["--output-format", "stream-json", "--verbose"]
    assert command[14] == "--append-system-prompt"
    assert command[16] == "--tools"
    assert command[18] == "--allowedTools"
    assert command[17] == command[19]
    assert command[20] == "--max-turns"
    assert command[22] == "--no-session-persistence"
    assert command[23] == "--max-budget-usd"
    assert command[25] == "--json-schema"
    assert json.loads(command[26])["properties"]["role"]["enum"] == ["spec-review"]
    assert len(command) == 27
    assert invocation["argv"] == command[6:]
    assert invocation["environment"] == {
        "CLAUDECODE": None,
        "HEDDLE_AGENT_SESSION": command[3].partition("=")[2],
        "CLAUDE_CODE_EFFORT_LEVEL": command[4].partition("=")[2],
    }

    status = _assert_success(
        installed_wheel, nested, "status", "--feature", "lane-probe", "--json"
    )
    gate_fact = next(
        row for row in status["data"]["gates"] if row["gate"] == "spec-review"
    )
    assert gate_fact["scope"] == "feature"
    run = gate_fact["runs"][0]
    assert run["cli"] == "claude"
    for field in ("input_hash", "review_basis_hash"):
        assert re.fullmatch(r"[0-9a-f]{64}", run[field]), (
            f"FAIL AC-11: {field} is not a complete lowercase SHA-256 hex digest"
        )
    assert run["verdict"]["status"] == "pass"
    assert {
        "gate": "spec-review",
        "scope": "feature",
        "path": run["artifact"],
    } in status["data"]["artifacts"]
    assert status["next_actions"][0]["command"] == (
        "heddle run-gate spec-review --cli codex --model gpt-6-astra "
        "--reasoning-effort high --feature lane-probe"
    )


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac12_red_lane_is_fast_scrubbed_offline_and_checkout_independent(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-12 red: installed commands stay fast, offline, and outside checkout."""
    host, nested = make_git_host(tmp_path, "ac12")
    started = time.monotonic()
    _assert_success(installed_wheel, nested, "init", "--json")
    (host / ".heddle.yaml").write_text(
        "layout:\n  plans: journey-plans\n",
        encoding="utf-8",
    )
    _ratify_principles(host)

    _admit_feature(
        installed_wheel,
        host,
        nested,
        tmp_path / "ac12-intake",
        reviewed=True,
    )
    for args in (
        ("sync", "--feature", "lane-probe", "--json"),
        ("orient", "--feature", "lane-probe", "--json"),
        ("status", "--feature", "lane-probe", "--json"),
        ("kickoff", "--feature", "lane-probe", "--json"),
    ):
        _assert_success(installed_wheel, nested, *args)
    _assert_success(
        installed_wheel, nested, "phase-exit", "--feature", "lane-probe", "--json"
    )
    shim_bin = tmp_path / "ac12-shim"
    invocation_log = tmp_path / "ac12-shim.jsonl"
    write_claude_shim(shim_bin, _pass_spec_review_event(), invocation_log)
    shim_env = dict(installed_wheel.env)
    shim_env["PATH"] = f"{shim_bin}:{shim_env['PATH']}"
    gate_code, gate, gate_result = _run_json(
        installed_wheel,
        nested,
        "run-gate",
        "spec-review",
        "--feature",
        "lane-probe",
        "--json",
        env=shim_env,
    )
    assert gate_code == 0 and gate["ok"] is True, gate_result.stderr
    _assert_success(
        installed_wheel, nested, "status", "--feature", "lane-probe", "--json"
    )
    _assert_success(installed_wheel, nested, "validate", "--json")
    elapsed = time.monotonic() - started

    probe = installed_wheel.python_probe(
        (
            "import json, pathlib, heddle, importlib.resources as r; "
            "print(json.dumps({'module': str(pathlib.Path(heddle.__file__).resolve()), "
            "'resources': str(r.files('heddle'))}))"
        ),
        cwd=nested,
    )
    assert probe.returncode == 0, probe.stderr
    origins = json.loads(probe.stdout)
    assert all(
        Path(value).is_relative_to(installed_wheel.environment)
        for value in origins.values()
    )
    assert all(not Path(value).is_relative_to(REPO_ROOT) for value in origins.values())
    assert _runtime_within_bound(elapsed), (
        f"FAIL AC-12: installed command interval was {elapsed:.3f}s"
    )
    assert _runtime_within_bound(9.999_999)
    assert not _runtime_within_bound(10.0)
    assert not installed_wheel.forbidden_log.exists() or (
        installed_wheel.forbidden_log.read_text(encoding="utf-8") == ""
    )
    assert not installed_wheel.network_log.exists() or (
        installed_wheel.network_log.read_text(encoding="utf-8") == ""
    )
    assert (
        host / "journey-plans" / ".gate-locks" / ".gitignore"
    ).read_bytes() == b"*\n"
    assert "PYTHONPATH" not in installed_wheel.env
    assert "PYTHONHOME" not in installed_wheel.env
    assert "VIRTUAL_ENV" not in installed_wheel.env
    assert installed_wheel.env["PYTHONNOUSERSITE"] == "1"

    forbidden_path = (REPO_ROOT / "pyproject.toml").resolve()
    forbidden_probe = installed_wheel.python_probe(
        f"open({str(forbidden_path)!r}, encoding='utf-8').read()",
        cwd=nested,
    )
    assert forbidden_probe.returncode != 0
    assert "forbidden checkout-root read" in forbidden_probe.stderr
    assert installed_wheel.forbidden_log.read_text(encoding="utf-8").splitlines() == [
        str(forbidden_path)
    ]

    network_probe = installed_wheel.python_probe(
        "import socket; socket.socket().connect(('127.0.0.1', 9))",
        cwd=nested,
    )
    assert network_probe.returncode != 0
    assert "network access forbidden" in network_probe.stderr
    network_events = installed_wheel.network_log.read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(network_events) == 1
    assert network_events[0].startswith("socket.connect:")


@pytest.mark.toolchain
@pytest.mark.e2e
def test_ac10_verification_evidence_is_installed_wheel_portable(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-10: installed verify and close publish durable, hermetic evidence."""
    audit_before = {
        path: path.read_bytes() if path.exists() else b""
        for path in (installed_wheel.forbidden_log, installed_wheel.network_log)
    }
    host, state_path = make_current_host(
        tmp_path,
        tier=1,
        milestones=True,
        name="provenance-wheel",
    )
    (state_path.parent / "brief.md").write_text(
        "# Brief\n\nExercise installed verification and completion.\n",
        encoding="utf-8",
    )
    initialized = subprocess.run(
        ["git", "init", "-q"], cwd=host, capture_output=True, text=True, check=False
    )
    assert initialized.returncode == 0, initialized.stderr
    for key, value in (
        ("user.email", "wheel@example.test"),
        ("user.name", "Wheel Test"),
    ):
        configured = subprocess.run(
            ["git", "config", key, value],
            cwd=host,
            capture_output=True,
            text=True,
            check=False,
        )
        assert configured.returncode == 0, configured.stderr
    for command in (["git", "add", "."], ["git", "commit", "-qm", "baseline"]):
        committed = subprocess.run(
            command, cwd=host, capture_output=True, text=True, check=False
        )
        assert committed.returncode == 0, committed.stderr

    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    state["stage"] = "complete"
    state["authorized_through"] = "complete"
    state["milestones"][0]["status"] = "done"
    state["authorizations"] = [
        {
            "through": "complete",
            "source": "user",
            "at": "2026-09-04T12:00Z",
        }
    ]
    state_path.write_text(yaml.safe_dump(state, sort_keys=False), encoding="utf-8")
    (host / "plans/sample-feature.friction-retrospective.md").write_text(
        "# Friction Retrospective\n\n"
        "## Tooling fights\n\nNone.\n\n"
        "## Prompt gaps\n\nNone.\n\n"
        "## Validator false-positives\n\nNone.\n\n"
        "## Escalations that should have been policy-resolvable\n\nNone.\n",
        encoding="utf-8",
    )
    (host / "plans/sample-feature.decision-journal.md").write_text(
        "# Decision Journal\n", encoding="utf-8"
    )

    verifications = []
    for scope in ("m1", "acceptance", "smoke"):
        verifications.append(
            _assert_success(
                installed_wheel,
                host,
                "verify",
                "--scope",
                scope,
                "--feature",
                "sample-feature",
                "--json",
            )
        )
    reference = verifications[0]["data"]["verification"]["evidence"]["before"]
    manifest = yaml.safe_load((state_path.parent / reference["artifact"]).read_text())
    assert manifest["definition"]["paths"] == ["src.py"]

    code, close, result = _run_json(
        installed_wheel,
        host,
        "feature",
        "complete",
        "--feature",
        "sample-feature",
        "--json",
    )
    assert code == 0 and close["data"]["accepted"], (close, result.stderr)
    archive = host / "docs/gate-trajectories/.raw/sample-feature/completion.tar.gz"
    before = state_path.read_bytes(), archive.read_bytes()
    from tests.content_identity_helpers import git

    git(host, "add", "-f", "plans", "docs")
    git(host, "commit", "-qm", "retain installed accepted history")
    retry = _assert_success(
        installed_wheel,
        host,
        "feature",
        "complete",
        "--feature",
        "sample-feature",
        "--json",
    )
    assert retry["data"]["effects"]["cleanup"]["status"] == "complete"
    assert (state_path.read_bytes(), archive.read_bytes()) == before
    assert (state_path.parent / "plan.md").exists()
    assert retry["data"]["effects"]["cleanup"]["preserved"] == []
    for path, before in audit_before.items():
        assert (path.read_bytes() if path.exists() else b"") == before


@pytest.mark.toolchain
def test_ac13_red_drift_is_diagnosed_without_overwriting_host_bytes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-13 survivor + red: validate pins markers; doctor adds lock presence."""
    host, nested = make_git_host(tmp_path, "ac13")
    _write_manual_initialized_host(host)
    _admit_feature(installed_wheel, host, nested, tmp_path / "ac13-intake")
    _assert_success(
        installed_wheel, nested, "sync", "--feature", "lane-probe", "--json"
    )

    agents = host / "AGENTS.md"
    complete = agents.read_text(encoding="utf-8")
    cases = (
        (
            "absent",
            complete.replace(
                SESSION_BEGIN + "\n" + render_session_entry() + SESSION_END + "\n",
                "",
            ),
            4,
            "has no `session-entry` managed block",
        ),
        (
            "begin-only",
            complete.replace(SESSION_END + "\n", ""),
            3,
            "begin-only",
        ),
    )
    mirror = host / "CLAUDE.md"
    for name, text, expected_exit, expected_term in cases:
        # Keep the default mirror in step so only the managed block drifts.
        agents.write_text(text, encoding="utf-8")
        mirror.write_text(text, encoding="utf-8")
        before = snapshot_tree(host)
        code, envelope, _result = _run_json(
            installed_wheel, nested, "validate", "--json"
        )
        assert code == expected_exit
        integrity = _diagnostic(envelope, "managed-block-integrity")
        assert len(integrity) == 1
        assert expected_term in json.dumps(envelope).lower()
        assert envelope["next_actions"] == []
        if name == "absent":
            assert envelope["ok"] is True
            assert integrity[0]["severity"] == "advisory"
        else:
            assert envelope["ok"] is False
            assert envelope["error"]["code"] == "ledger-drift"
            assert "data" not in envelope
            assert integrity[0]["severity"] == "fatal"
        assert snapshot_tree(host) == before, (
            f"FAIL AC-13 survivor: validate rewrote {name} drift"
        )
        agents.write_text(complete, encoding="utf-8")
        mirror.write_text(complete, encoding="utf-8")

    (host / "docs" / "workflow" / "engineering-principles.md").unlink()
    before = snapshot_tree(host)
    code, envelope, _result = _run_json(installed_wheel, nested, "doctor", "--json")
    assert code == 3
    assert envelope["error"]["code"] == "workspace-invalid"
    assert "data" not in envelope
    missing = _diagnostic(envelope, "lock-recorded-path-missing")
    assert len(missing) == 1
    assert missing[0]["severity"] == "fatal"
    assert missing[0].get("source") == ("docs/workflow/engineering-principles.md")
    assert envelope["next_actions"][0]["command"] == "heddle init --dry-run"
    assert snapshot_tree(host) == before


def test_ac14_red_readme_install_and_update_contract() -> None:
    """AC-14 red: operator docs replace the installer with three honest modes."""
    assert not (REPO_ROOT / "install.sh").exists(), (
        "FAIL AC-14: install.sh still exists; M8A deletes it without a shim"
    )
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    commands = (
        "uv tool install --force --editable --python 3.13 <checkout>",
        'uv tool install --force --refresh --python 3.13 "heddle @ '
        'git+https://github.com/Wrosinski/heddle.git@<full-commit-sha>"',
        "uv tool install --force --python 3.13 "
        "/absolute/path/to/dist/heddle-<version>-py3-none-any.whl",
    )
    assert all(readme.count(command) == 1 for command in commands)
    assert "editable" in _heading_before(readme, commands[0]).casefold()
    assert any(
        word in _heading_before(readme, commands[1]).casefold()
        for word in ("pinned", "commit", "git")
    )
    assert "wheel" in _heading_before(readme, commands[2]).casefold()

    update_steps = (
        "stop long-lived Heddle processes, especially `heddle drive`",
        "checkpoint the host in Git and record current Heddle provenance",
        "edit and verify Heddle in its own checkout",
        "start a fresh CLI process in the selected mode",
        "run `heddle doctor` and resolve fatal diagnostics",
        "follow the [guide's active-feature or no-active-feature update route]",
        "projection application requires an active feature",
        "resume after runtime/state compatibility is confirmed",
    )
    update_heading = re.search(
        r"^#{2,6}\s+.*update.*$", readme, flags=re.IGNORECASE | re.MULTILINE
    )
    assert update_heading is not None
    following_heading = re.search(
        r"^##\s+", readme[update_heading.end() :], flags=re.MULTILINE
    )
    update_end = (
        update_heading.end() + following_heading.start()
        if following_heading is not None
        else len(readme)
    )
    update_section = readme[update_heading.end() : update_end]
    positions = [update_section.index(step) for step in update_steps]
    assert positions == sorted(positions)

    journey_heading = re.search(r"^## Adopt a host$", readme, flags=re.MULTILINE)
    assert journey_heading is not None
    following_heading = re.search(
        r"^##\s+", readme[journey_heading.end() :], flags=re.MULTILINE
    )
    journey_end = (
        journey_heading.end() + following_heading.start()
        if following_heading is not None
        else len(readme)
    )
    journey_section = readme[journey_heading.end() : journey_end]
    journey_steps = (
        "heddle init --dry-run",
        "\nheddle init\n",
        "must author and ratify",
        "status: ratified",
        "heddle doctor",
        "heddle feature start",
    )
    positions = [journey_section.index(step) for step in journey_steps]
    assert positions == sorted(positions)
    _paragraph_with_terms(readme, "AGENTS.md", "integrat", "preserv")
    _paragraph_with_terms(
        readme,
        "status: ratified",
        "adopter",
        "author",
        "doctor",
    )
    _paragraph_with_terms(
        readme,
        "live Python processes do not reload package changes",
        "fresh",
    )
    _paragraph_with_terms(
        readme,
        "never silently rewrites lock-tracked host documents",
        "switch",
    )


@pytest.mark.toolchain
def test_ac15_red_installed_doctor_reports_exact_runner_presence_matrix(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
) -> None:
    """AC-15 red: the installed doctor publishes the exact runner rows."""
    from heddle.contracts.schemas import AGENTS_KEYS

    cases = (
        ("agents: {}\n", ()),
        ("agents:\n  claude: true\n", ("claude",)),
        ("agents:\n  codex: true\n", ("codex",)),
        (
            "agents:\n  codex: true\n  claude: true\n",
            ("claude", "codex"),
        ),
    )
    assert tuple(AGENTS_KEYS) == ("claude", "codex")
    for case_index, (agents_yaml, enabled) in enumerate(cases):
        host, nested = make_git_host(tmp_path, f"ac15-installed-{case_index}")
        _write_manual_initialized_host(host)
        (host / ".heddle.yaml").write_text(
            "layout:\n  plans: journey-plans\n" + agents_yaml,
            encoding="utf-8",
        )
        for present in (False, True):
            shim_bin = tmp_path / f"ac15-installed-{case_index}-{present}"
            shim_bin.mkdir()
            expected_paths: dict[str, str] = {}
            if present:
                for agent in enabled:
                    executable = write_executable(
                        shim_bin / agent,
                        "#!/bin/sh\nexit 99\n",
                    )
                    expected_paths[agent] = str(executable.resolve())
            env = dict(installed_wheel.env)
            env["PATH"] = f"{shim_bin}:{env['PATH']}"

            code, envelope, result = _run_json(
                installed_wheel,
                nested,
                "doctor",
                "--json",
                env=env,
            )
            assert code == 0 and envelope["ok"] is True, (
                f"FAIL AC-15: installed doctor exited {code}: "
                f"{envelope!r}\n{result.stderr}"
            )
            rows = [
                row
                for row in envelope["diagnostics"]
                if row["code"].startswith("runner-")
            ]
            assert rows == [
                {
                    "severity": "info",
                    "code": f"runner-{agent}",
                    "message": (
                        f"{agent} executable found at {expected_paths[agent]}"
                        if present
                        else f"{agent} executable not found on PATH"
                    ),
                    "source": "present" if present else "absent",
                }
                for agent in enabled
            ]
            assert envelope["data"]["healthy"] is True


def test_ac15_red_runner_builder_uses_which_only_in_schema_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_cli,
    envelope_tools,
) -> None:
    """AC-15 red companion: the doctor route uses one which and never spawns."""
    from heddle.contracts.schemas import AGENTS_KEYS

    host, _nested = make_git_host(tmp_path, "ac15")
    _write_manual_initialized_host(host)
    monkeypatch.chdir(host)
    binary = tmp_path / "bin" / "runner"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    def forbidden_subprocess(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("FAIL AC-15: doctor spawned a runner capability probe")

    monkeypatch.setattr(subprocess, "Popen", forbidden_subprocess)
    for config_body, enabled in (
        ("agents: {}\n", ()),
        ("agents:\n  claude: true\n", ("claude",)),
        ("agents:\n  codex: true\n", ("codex",)),
        (
            "agents:\n  codex: true\n  claude: true\n",
            ("claude", "codex"),
        ),
    ):
        config = host / ".heddle.yaml"
        config.write_text(
            "layout:\n  plans: journey-plans\n" + config_body,
            encoding="utf-8",
        )
        for present in (False, True):
            calls: list[str] = []

            def fake_which(
                name: str,
                *,
                _calls: list[str] = calls,
                _present: bool = present,
            ) -> str | None:
                _calls.append(name)
                return str(binary.resolve()) if _present else None

            monkeypatch.setattr(shutil, "which", fake_which)
            code, out, error = run_cli(["doctor", "--json"])
            assert error == ""
            envelope = envelope_tools.parse(out)
            assert code == 0 and envelope["ok"] is True
            diagnostics = envelope["diagnostics"]
            runner_rows = [
                row for row in diagnostics if row["code"].startswith("runner-")
            ]
            assert [row["code"] for row in runner_rows] == [
                f"runner-{name}" for name in enabled
            ]
            assert calls == list(enabled)
            assert [row["code"] for row in runner_rows] == [
                f"runner-{name}" for name in AGENTS_KEYS if name in enabled
            ]
            assert all(row["severity"] == "info" for row in runner_rows)
            assert all(
                row["source"] == ("present" if present else "absent")
                for row in runner_rows
            )


def test_ac16_red_config_scaffold_rail_matches_parser_vocabulary() -> None:
    """AC-16 red: the shipped comment-only scaffold mirrors parsed vocabulary."""
    resource = importlib.resources.files("heddle.resources").joinpath(
        "heddle-yaml.scaffold.yaml"
    )
    assert resource.is_file(), (
        "FAIL AC-16: packaged heddle-yaml.scaffold.yaml does not exist"
    )
    text = resource.read_text(encoding="utf-8")
    assert yaml.safe_load(text) is None, (
        "FAIL AC-16: the scaffold must be comment-only so empty parse means defaults"
    )
    from heddle.contracts.schemas import (
        AGENTS_KEYS,
        AUTOPILOT_KEYS,
        GATES_KEYS,
        HEDDLE_YAML_SECTIONS,
        HOST_COMMAND_KEYS,
        LAYOUT_KEYS,
        SYNC_KEYS,
    )

    expected = {
        "layout": set(LAYOUT_KEYS),
        "commands": set(HOST_COMMAND_KEYS),
        "agents": set(AGENTS_KEYS),
        "gates": set(GATES_KEYS),
        "autopilot": set(AUTOPILOT_KEYS),
        "sync": set(SYNC_KEYS),
    }
    observed: dict[str, set[str]] = {}
    section_order: list[str] = []
    active_section: str | None = None
    for line in text.splitlines():
        section_match = re.fullmatch(r"# ([a-z_]+):(?:\s.*)?", line)
        if section_match:
            active_section = section_match.group(1)
            section_order.append(active_section)
            observed[active_section] = set()
            continue
        key_match = re.fullmatch(r"# {3}([a-z_]+):(?:\s.*)?", line)
        if key_match:
            assert active_section is not None
            key = key_match.group(1)
            assert key not in observed[active_section]
            observed[active_section].add(key)
    assert section_order == list(HEDDLE_YAML_SECTIONS)
    assert observed == expected
    assert "defaults apply to every absent key" in text.lower()
    assert "an empty file is valid" in text.lower()
    assert not ("layout.prompts" in text and "prompt resolution" in text.lower()), (
        "FAIL AC-16: scaffold falsely claims layout.prompts changes resolution"
    )


@pytest.mark.toolchain
def test_ac17_red_interrupted_apply_leaves_complete_files_without_lock_and_recovers(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-17 red: every non-lock cut point is complete, lockless, and recoverable."""
    module = _require_init_module("AC-17")
    families = ("clean-1", "clean-2", "clean-3", "clean-4", "integrated")
    for family in families:
        host, nested = make_git_host(tmp_path, f"ac17-{family}")
        host_authored: bytes | None = None
        if family == "integrated":
            host_authored = b"# Host instructions\n\nkeep exact\n"
            (host / "AGENTS.md").write_bytes(host_authored)
            cut_after = 2
        else:
            cut_after = int(family[-1])
        plan = module.plan_init(host)
        real_install = module.install_projection
        successful: list[Path] = []

        class InjectedInterruption(RuntimeError):
            pass

        def interrupting_install(
            path: Path,
            text: str,
            *,
            create: bool,
            _real_install: Any = real_install,
            _successful: list[Path] = successful,
            _cut_after: int = cut_after,
        ) -> None:
            _real_install(path, text, create=create)
            if path.name != ".heddle.lock":
                _successful.append(path)
                if len(_successful) == _cut_after:
                    raise InjectedInterruption(path)

        monkeypatch.setattr(module, "install_projection", interrupting_install)
        with pytest.raises(InjectedInterruption):
            module.apply_init(plan)
        assert not (host / ".heddle.lock").exists()
        assert len(successful) == cut_after
        for target in plan.targets[:4]:
            target_path = host / target.path
            if target_path in successful:
                assert target.desired_bytes is not None
                assert target_path.read_bytes() == target.desired_bytes
            elif family != "integrated" or target.path.as_posix() != "AGENTS.md":
                assert not target_path.exists()
        assert not list(host.rglob("*.tmp"))
        if host_authored is not None:
            assert (host / "AGENTS.md").read_bytes().startswith(host_authored)

        monkeypatch.setattr(module, "install_projection", real_install)
        present = {path.relative_to(host).as_posix() for path in successful}
        expected_recovery = [
            ("integrate" if target.path.as_posix() == "AGENTS.md" else "accept")
            if target.path.as_posix() in present
            else "create"
            for target in plan.targets[:4]
        ] + ["create"]
        recovered = _assert_success(installed_wheel, nested, "init", "--json")
        assert [row["action"] for row in recovered["data"]["targets"]] == (
            expected_recovery
        )
        assert [row["outcome"] for row in recovered["data"]["targets"]] == ["done"] * 5
        _assert_valid_lock(host)
        assert not list(host.rglob("*.tmp"))
        if host_authored is not None:
            assert (host / "AGENTS.md").read_bytes().startswith(host_authored)

        before_tree = snapshot_tree(host)
        before = file_hashes(host, FOOTPRINT)
        converged = _assert_success(installed_wheel, nested, "init", "--json")
        assert (
            tuple(row["action"] for row in converged["data"]["targets"])
            == CONVERGED_ACTIONS
        )
        assert file_hashes(host, FOOTPRINT) == before
        assert snapshot_tree(host) == before_tree


@pytest.mark.toolchain
def test_ac18_red_concurrent_init_uses_only_ordinary_conflict_outcomes(
    installed_wheel: InstalledWheel,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-18 red: a synchronized first-link race has no special conflict code."""
    module = _require_init_module("AC-18")
    host, nested = make_git_host(tmp_path, "ac18")
    first_link = threading.Barrier(2)
    real_install = module.install_projection
    waited: set[int] = set()
    wait_lock = threading.Lock()

    def synchronized_install(path: Path, text: str, *, create: bool) -> None:
        if create and path.name == ".heddle.yaml":
            ident = threading.get_ident()
            with wait_lock:
                first_for_thread = ident not in waited
                waited.add(ident)
            if first_for_thread:
                first_link.wait(timeout=5)
        real_install(path, text, create=create)

    monkeypatch.setattr(module, "install_projection", synchronized_install)
    expected_plan = module.plan_init(host)
    monkeypatch.chdir(nested)
    outcomes: list[int] = []
    emitted: list[Any] = []
    unexpected: list[BaseException] = []
    result_lock = threading.Lock()

    def capture_envelope(result: Any, _json_mode: bool, _human: Any) -> int:
        with result_lock:
            emitted.append(result)
        return int(result.exit_code)

    monkeypatch.setattr(module, "emit_envelope", capture_envelope)

    def apply() -> None:
        try:
            outcome = module.run_init([], json_mode=True)
        except BaseException as error:
            unexpected.append(error)
        else:
            outcomes.append(outcome)

    threads = [threading.Thread(target=apply) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "FAIL AC-18: synchronized init deadlocked"

    assert not unexpected, f"FAIL AC-18: unexpected racer error(s): {unexpected!r}"
    assert tuple(sorted(outcomes)) in ((0, 0), (0, 3)), (
        f"FAIL AC-18: permitted exits are (0,0)/(0,3)/(3,0), got {outcomes}"
    )
    assert len(emitted) == 2
    assert any(result.ok for result in emitted)
    for result in emitted:
        envelope = result.to_envelope()
        if result.ok:
            assert result.exit_code == 0
            assert envelope["data"]["targets"]
            continue
        assert result.exit_code == 3
        assert envelope["error"]["code"] == "workspace-invalid"
        assert "data" not in envelope
        remediation = (
            f"{envelope['error']['message']}\n{envelope['error']['hint']}"
        ).casefold()
        assert "appeared during install" in remediation
        assert "heddle init" in remediation
        assert envelope["next_actions"][0]["command"] == "heddle init --dry-run"
    for target in expected_plan.targets:
        assert target.desired_bytes is not None
        assert (host / target.path).read_bytes() == target.desired_bytes
    _assert_valid_lock(host)
    assert not list(host.rglob("*.tmp"))
    monkeypatch.setattr(module, "install_projection", real_install)
    before_tree = snapshot_tree(host)
    before = file_hashes(host, FOOTPRINT)
    converged = _assert_success(installed_wheel, nested, "init", "--json")
    assert (
        tuple(row["action"] for row in converged["data"]["targets"])
        == CONVERGED_ACTIONS
    )
    assert file_hashes(host, FOOTPRINT) == before
    assert snapshot_tree(host) == before_tree
