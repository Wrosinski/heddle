"""Authored completion boundaries with real verification; never live ledgers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.content_identity_helpers import git
from tests.operational_model_helpers import FEATURE, SPEC, STAMP, application, document


def pending_completion_document():
    value = document(schema="heddle.state/v6", completion=None, stage="complete")
    value["authorizations"] = [{"through": "complete", "source": "user", "at": STAMP}]
    for milestone in value["milestones"]:
        milestone["status"] = "done"
    return value


def accepted_completion_document():
    value = pending_completion_document()
    paths = sorted([SPEC, "src/example.py", "tests/check.py"])
    value["commands"] = {
        "acceptance_test": "python3 tests/check.py",
        "smoke_test": "python3 tests/check.py",
    }
    value["verifications"] = [
        {
            "scope": scope,
            "command": "python3 tests/check.py",
            "exit_code": 0,
            "at": STAMP,
            "log": f"verification/{scope}.log",
            "provenance": {
                "schema": "heddle.verification-source/v2",
                "source_set_kind": "feature-owned-union",
                "declaration_paths": paths,
                "paths": paths,
                "before_sha256": "c" * 64,
                "after_sha256": "c" * 64,
                "git_head": None,
                "differs_from_head": None,
            },
        }
        for scope in ("acceptance", "smoke")
    ]
    value["completion"] = {
        "accepted_at": STAMP,
        "authorization_index": 0,
        "verification_indexes": [0, 1],
        "review_run_ids": [],
        "accepted_smoke_decision_id": None,
        "spec_stamp": {
            "before_sha256": "a" * 64,
            "after_sha256": "b" * 64,
            "executable": False,
        },
        "close_suite": None,
    }
    return value


def completion_operation():
    app, ops = application()
    operation = getattr(ops, "FeatureComplete", None)
    assert operation is not None, "FAIL W5: explicit FeatureComplete is missing"
    return app, operation


@dataclass(frozen=True)
class CompletionHost:
    root: Path
    state: Path
    calls: Path

    @property
    def spec(self):
        return self.root / SPEC

    @property
    def archive(self):
        return self.root / "docs/gate-trajectories/.raw" / FEATURE / "completion.tar.gz"

    def complete(self, **kwargs):
        app, operation = completion_operation()
        return app.execute(operation(feature=FEATURE, **kwargs))

    def suite_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def commit_retention(self):
        git(self.root, "add", "-f", "plans", "docs/gate-trajectories", SPEC)
        git(self.root, "commit", "-qm", "retain accepted ledger and complete archive")


def tree_bytes(root):
    """Observe bytes and mode without following links; ignore Git lock internals."""
    return {
        path.relative_to(root).as_posix(): (
            path.readlink().as_posix() if path.is_symlink() else path.read_bytes(),
            path.lstat().st_mode,
        )
        for path in root.rglob("*")
        if ".git" not in path.relative_to(root).parts
        and (path.is_file() or path.is_symlink())
    }


def assert_accepted(result, *, pending=True):
    assert result.ok, result
    assert result.data["accepted"] is True, result
    assert result.error is None
    assert int(result.exit_code) == (4 if pending else 0), result
    assert set(result.data["effects"]) >= {"stamp", "archive", "cleanup"}
