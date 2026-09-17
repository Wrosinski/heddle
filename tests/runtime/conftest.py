"""
Shared fixtures for the heddle/runtime contract tests (core Contract Rails).

Behavior contract: core-contract-rails
Scaffolded in test scaffolding (preimplementation state): tests in this package import
from
``heddle.runtime``, which does not exist until Milestone 1 lands — every
non-skipped test must fail or error before implementation begins.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACES_ROOT = REPO_ROOT / "tests" / "fixtures" / "workspaces"
TINY = WORKSPACES_ROOT / "tiny"
GOLDEN = WORKSPACES_ROOT / "golden"

# The five wire keys of the result envelope (result envelope/native removal; Feature
# Spec REQ-1).
ENVELOPE_KEYS_SUCCESS = {"ok", "schema_version", "data", "diagnostics", "next_actions"}
ENVELOPE_KEYS_FAILURE = {"ok", "schema_version", "error", "diagnostics", "next_actions"}


def assert_envelope_shape(envelope: dict) -> None:
    """Assert the data-xor-error five-key invariant (AC-4; REQ-1/REQ-2).

    FAIL hint: a JSON response must carry exactly {ok, schema_version,
    data | error, diagnostics, next_actions} — data and error are mutually
    exclusive and exactly one is present.
    """
    keys = set(envelope.keys())
    assert keys in (ENVELOPE_KEYS_SUCCESS, ENVELOPE_KEYS_FAILURE), (
        f"FAIL: envelope keys {sorted(keys)} are not exactly the five wire keys "
        "(data xor error)"
    )
    if "data" in envelope:
        assert envelope["ok"] is True, "FAIL: data present but ok is not true"
    else:
        assert envelope["ok"] is False, "FAIL: error present but ok is not false"
        assert set(envelope["error"].keys()) == {
            "code",
            "message",
            "hint",
            "details",
        }, "FAIL: error object must serialize exactly {code, message, hint, details}"
    assert isinstance(envelope["diagnostics"], list)
    assert isinstance(envelope["next_actions"], list)


def parse_single_envelope(stdout: str) -> dict:
    """Parse stdout as exactly one JSON envelope (REQ-1: envelope-clean)."""
    stripped = stdout.strip()
    # raw_decode proves single-object cleanliness: nothing on stdout besides
    # the one serialized envelope (review — gate-reviewed fix).
    envelope, end = json.JSONDecoder().raw_decode(stripped)
    assert isinstance(envelope, dict), "FAIL: stdout is not a single JSON object"
    assert end == len(stripped), (
        "FAIL: stdout carries content besides the JSON envelope"
    )
    return envelope


@pytest.fixture
def run_cli(capsys):
    """Invoke the installed console-script entry in-process.

    Returns ``(exit_code, stdout, stderr)``. Mirrors tests/gate's in-process
    idiom (no subprocess); the acceptance test exercises the real subprocess
    path separately.
    """

    def _run(argv: list[str]) -> tuple[int, str, str]:
        from heddle.cli import main

        code = main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run


@pytest.fixture
def fake_gate_runner(monkeypatch):
    """Replace the engine for event, replay, cap and streaming contract tests.

    Callers author native findings explicitly. Role coverage and retained
    dispositions are supplied by the shared event-fixture builder. Provider
    acceptance tests replace only monitored provider I/O instead of this seam.
    The probe counts executions and surfaced progress checkpoints.
    """
    from heddle.gate.diff import resolve_diff as production_resolve_diff

    def install(*, review=None, exit_code=0, progress_chunks=1, prepare_owned=True):
        from tests.structured_review_helpers import scripted_review

        review = scripted_review() if review is None else review
        probe = _Probe()
        if prepare_owned:
            _ensure_current_owned_input(Path.cwd())
        _install_fixture_diff_projection(monkeypatch, production_resolve_diff)

        def fake_run(
            gate_type, context, *, feature, progress=None, iteration=1, max_iterations=5
        ):
            from heddle.contracts.review_assignments import ArtifactRef
            from heddle.gate.cli import GateArgs
            from heddle.gate.entry import machine_projection_from_result
            from heddle.gate.io import gate_artifact_location
            from heddle.gate.results import serialize_review_result
            from heddle.gate.types import GateCanonicalReview, GateEngineFailure
            from tests.structured_review_helpers import scripted_result

            prepared = context.prepared_run
            assert prepared is not None
            cli = prepared.invocation.exec_config.cli
            probe.invocations += 1
            probe.contexts.append((gate_type, context, cli))
            workspace = context.workspace_dir or context.plan_path.parent
            args = GateArgs(
                gate_name=gate_type.name,
                cli=cli,
                feature=feature,
                iteration=iteration,
                max_iterations=max_iterations,
            )
            directory, name = gate_artifact_location(context, args)
            directory.mkdir(parents=True, exist_ok=True)
            surfaced = 0
            if progress is not None:
                for index in range(progress_chunks):
                    progress({"chunk": index})
                    surfaced += 1
            probe.checkpoints_surfaced = surfaced

            failure = prepared.preflight.fatal_reason
            result = None
            warnings = ()
            if not failure and exit_code == 0:
                try:
                    result = scripted_result(review, prepared, feature)
                except ValueError as error:
                    failure, warnings = "invalid-output-structure", (str(error),)
            if result is None:
                diagnostic = directory / f"{name}.failed-input.json"
                diagnostic.write_text(json.dumps(review), encoding="utf-8")
                raw_diagnostic = diagnostic.read_bytes()
                return GateEngineFailure(
                    gate_exit=exit_code or 1,
                    reason=failure or "execution-failure",
                    rerun_recommended=None,
                    structure_warnings=warnings,
                    artifacts=(
                        ArtifactRef(
                            diagnostic.relative_to(workspace).as_posix(),
                            hashlib.sha256(raw_diagnostic).hexdigest(),
                            "temporary",
                        ),
                    ),
                )
            raw = serialize_review_result(result)
            digest = hashlib.sha256(raw).hexdigest()
            artifact = directory / f"{name}.{digest}.review.json"
            artifact.write_bytes(raw)
            projection = machine_projection_from_result(result)
            return GateCanonicalReview(
                result=result,
                artifact=ArtifactRef(
                    artifact.relative_to(workspace).as_posix(), digest, "canonical"
                ),
                structure_warnings=tuple(projection["structure_warnings"]),
            )

        monkeypatch.setattr("heddle.gate.entry.run_gate_for_runtime", fake_run)
        return probe

    return install


def _ensure_current_owned_input(root: Path) -> None:
    """Give fake-run workspaces one real reviewed byte unless a test opts out."""
    plans = root / "plans"
    if not plans.is_dir():
        return
    created: list[Path] = []
    for state_path in plans.glob("*/state.yaml"):
        document = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        for milestone in document.get("milestones", ()):
            if not isinstance(milestone, dict) or milestone.get("status") != "current":
                continue
            for relative in milestone.get("owns", ()):
                target = root / str(relative)
                if target.suffix:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        target.write_text("# fake reviewed input\n", encoding="utf-8")
                        created.append(target)
                else:
                    target.mkdir(parents=True, exist_ok=True)
                    if not any(path.is_file() for path in target.rglob("*")):
                        marker = target / "heddle_fake_reviewed_input.py"
                        marker.write_text("fixture\n", encoding="utf-8")
                        created.append(marker)
    if created and not (root / ".git").exists():
        subprocess.run(
            ["git", "init", "--quiet"], cwd=root, check=True, capture_output=True
        )
    if created and (root / ".git").is_dir():
        subprocess.run(
            ["git", "add", "--", *(path.relative_to(root) for path in created)],
            cwd=root,
            check=True,
            capture_output=True,
        )


def _install_fixture_diff_projection(monkeypatch, production_resolve_diff) -> None:
    """Project fixture-owned bytes as a diff when the copied host has no Git."""
    from heddle.gate.types import DiffResult

    def resolve_fixture_diff(
        diff_path,
        repo_root,
        temp_dir=None,
        *,
        owned_paths=None,
        feature_baseline_probe=None,
    ):
        resolved = production_resolve_diff(
            diff_path,
            repo_root,
            temp_dir,
            owned_paths=owned_paths,
            feature_baseline_probe=feature_baseline_probe,
        )
        if resolved.changed_files or not owned_paths:
            return resolved
        reviewed = [
            path
            for owned in owned_paths
            for path in sorted((Path(repo_root) / owned).rglob("*"))
            if path.is_file()
        ]
        if not reviewed:
            return resolved
        relatives = [path.relative_to(repo_root).as_posix() for path in reviewed]
        target_dir = Path(temp_dir) if temp_dir is not None else reviewed[0].parent
        target_dir.mkdir(parents=True, exist_ok=True)
        projected = target_dir / "fixture-owned.diff"
        projected.write_text(
            "".join(
                f"diff --git a/{relative} b/{relative}\n"
                "--- /dev/null\n"
                f"+++ b/{relative}\n"
                "@@ -0,0 +1 @@\n"
                f"+{path.read_bytes().hex()}\n"
                for path, relative in zip(reviewed, relatives, strict=True)
            ),
            encoding="utf-8",
        )
        return DiffResult(
            source="provided-file",
            source_detail="fixture-owned-input",
            diff_path=projected,
            changed_files=relatives,
            untracked_files=[],
            warnings=[],
        )

    monkeypatch.setattr("heddle.gate.diff.resolve_diff", resolve_fixture_diff)


class _Probe:
    """Two observables a streaming/cache test pins both sides of: how many
    times the runner actually executed (a cache hit leaves it unchanged) and
    how many monitor checkpoints the runtime surfaced (AC-9)."""

    def __init__(self) -> None:
        self.invocations = 0
        self.checkpoints_surfaced = 0
        self.contexts: list[tuple[object, object, str]] = []

    def __int__(self) -> int:  # legacy cache assertions use int(probe)
        return self.invocations


# ---------------------------------------------------------------------------
# Fixture-corpus helpers (AC-7/AC-8/AC-10/AC-13) — shared by
# test_fixtures.py (per-milestone) and test_acceptance.py (integrated).
# Validation against the pinned vocabularies imports heddle.contracts.schemas
# inside each helper so the corpus tests go red until Milestone 1 encodes
# the contract they assert against.
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def iter_state_files() -> list[Path]:
    """Every fixture workspace state.yaml, sorted for deterministic output."""
    return sorted(WORKSPACES_ROOT.rglob("state.yaml"))


def assert_state_conforms(state_path: Path) -> None:
    """Assert one state.yaml conforms to the §10.1 vocabulary (REQ-11).

    Covers: schema id, the current explicit top-level keys, stage slugs,
    integer revision, milestone/task statuses, and the pinned key
    vocabularies for every nested collection entry.
    """
    from heddle.contracts import schemas

    state = load_yaml(state_path)
    # authoring AC-3 consumes this against runtime-created states outside the
    # fixture corpus — the label falls back to the absolute path there.
    label = (
        state_path.relative_to(WORKSPACES_ROOT)
        if state_path.is_relative_to(WORKSPACES_ROOT)
        else state_path
    )

    known_state_schemas = getattr(
        schemas, "STATE_SCHEMA_VERSIONS", (schemas.STATE_SCHEMA_ID,)
    )
    assert state["schema"] in known_state_schemas, (
        f"FAIL [{label}]: schema must declare a known state schema, got "
        f"{state['schema']!r}; known={known_state_schemas!r}"
    )
    # authoring AC-3: seeded/native states may additionally carry the optional
    # top-level keys (flow, authorizations, ... — flow policy/autopilot additive
    # growth);
    # the fifteen required keys stay mandatory and nothing else is admitted.
    optional_keys = set(schemas.STATE_V7_OPTIONAL_TOP_LEVEL_KEYS)
    assert set(schemas.STATE_V7_TOP_LEVEL_KEYS) <= set(state.keys()), (
        f"FAIL [{label}]: state files carry every current required key "
        f"explicitly; got {sorted(state.keys())}"
    )
    extra_keys = set(state.keys()) - set(schemas.STATE_V7_TOP_LEVEL_KEYS)
    assert extra_keys <= optional_keys, (
        f"FAIL [{label}]: top-level keys outside the §10.1 vocabulary: "
        f"{sorted(extra_keys - optional_keys)}"
    )
    assert state["stage"] in schemas.STAGES, f"FAIL [{label}]: bad stage slug"
    assert state["authorized_through"] in schemas.STAGES, (
        f"FAIL [{label}]: bad authorized_through slug"
    )
    assert isinstance(state["revision"], int) and not isinstance(
        state["revision"], bool
    ), f"FAIL [{label}]: revision must be an integer CAS token (WM-6)"
    assert set(state["commands"].keys()) <= set(schemas.COMMAND_KEYS), (
        f"FAIL [{label}]: commands keys outside the §10.1 vocabulary"
    )

    for milestone in state["milestones"]:
        assert set(milestone.keys()) <= set(schemas.MILESTONE_KEYS), (
            f"FAIL [{label}]: milestone keys outside the §10.1 vocabulary"
        )
        assert milestone["status"] in schemas.MILESTONE_STATUSES
        for task in milestone.get("tasks", []):
            assert set(task.keys()) == set(schemas.TASK_KEYS)
            assert task["status"] in schemas.MILESTONE_STATUSES

    for gate in state["gates"]:
        assert set(gate.keys()) == set(schemas.GATE_KEYS), (
            f"FAIL [{label}]: gate fact keys must be exactly §6.2's"
        )
        for run in gate["runs"]:
            assert set(schemas.GATE_RUN_KEYS) <= set(run)
            assert set(run) <= set(
                schemas.GATE_RUN_KEYS + schemas.GATE_RUN_OPTIONAL_KEYS
            )

    verification_keys = schemas.VERIFICATION_KEYS
    for fact in state["verifications"]:
        assert set(fact.keys()) == set(verification_keys)

    for decision in state["decisions"]:
        assert set(decision.keys()) <= set(schemas.DECISION_KEYS)

    for session in state["sessions"]:
        assert set(session.keys()) <= set(schemas.SESSION_KEYS)


def assert_artifact_paths_resolve(state_path: Path) -> None:
    """Every artifact path inside a state.yaml resolves to a file (AC-8)."""
    state = load_yaml(state_path)
    workspace = state_path.parent
    for gate in state["gates"]:
        for run in gate["runs"]:
            artifact = workspace / run["artifact"]
            assert artifact.is_file(), (
                f"FAIL: gate artifact {run['artifact']} does not resolve "
                f"inside {workspace}"
            )
    for fact in state["verifications"]:
        log = workspace / fact["log"]
        assert log.is_file(), (
            f"FAIL: verification log {fact['log']} does not resolve inside {workspace}"
        )


def assert_heddle_yaml_conforms(config_path: Path) -> None:
    """Assert a .heddle.yaml uses only pinned sections/keys (AC-10; REQ-15)."""
    from heddle.contracts import schemas

    config = load_yaml(config_path)
    assert set(config.keys()) <= set(schemas.HEDDLE_YAML_SECTIONS), (
        f"FAIL [{config_path}]: sections outside the configuration vocabulary"
    )
    assert "layout" in config, (
        f"FAIL [{config_path}]: layout.* form is canonical (configuration)"
    )
    assert set(config["layout"].keys()) <= set(schemas.LAYOUT_KEYS)
    if "commands" in config:
        assert set(config["commands"].keys()) <= set(schemas.HOST_COMMAND_KEYS)
    if "agents" in config:
        assert set(config["agents"].keys()) <= set(schemas.AGENTS_KEYS)
    if "gates" in config:
        assert set(config["gates"].keys()) <= set(schemas.GATES_KEYS)


# With --import-mode=importlib and no __init__.py, test modules cannot import
# conftest by name — shared helpers are handed to tests through fixtures.


@pytest.fixture
def corpus():
    """Fixture-corpus paths and conformance helpers (AC-7/8/10/13)."""
    return SimpleNamespace(
        root=WORKSPACES_ROOT,
        tiny=TINY,
        golden=GOLDEN,
        load_yaml=load_yaml,
        iter_state_files=iter_state_files,
        assert_state_conforms=assert_state_conforms,
        assert_artifact_paths_resolve=assert_artifact_paths_resolve,
        assert_heddle_yaml_conforms=assert_heddle_yaml_conforms,
    )


@pytest.fixture
def envelope_tools():
    """Envelope parsing/shape helpers (AC-1/AC-4)."""
    return SimpleNamespace(
        parse=parse_single_envelope,
        assert_shape=assert_envelope_shape,
        success_keys=ENVELOPE_KEYS_SUCCESS,
        failure_keys=ENVELOPE_KEYS_FAILURE,
    )
