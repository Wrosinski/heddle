"""Typed change explanations and basis manifests read through public payloads.

Synthetic hosts come from ``tests.tiering_review_helpers``; only the provider
transport is doubled. Nothing here reads recorded project evidence.

Activation seams, each asserted when a test reaches it, never at collection:

- ``changes``: the typed change list on every evidence explanation, both in a
  review assignment's ``evidence_explanations`` and on a ``verification_status``
  row. Each entry is a mapping with a ``kind``; a path change also names its
  ``path``. A named component change is accepted either as kind
  ``component:<name>`` or as kind ``component`` with ``name`` (or ``component``)
  because the component spelling is delegated.
- ``basis_evidence``: the reference a disposition row carries to the basis
  manifest its write published.

``typed_changes`` is the one shared reading of ``changes`` for every test; keep
its signature stable. Path change kinds partition as follows:

- a path in only one manifest's path set is ``membership``;
- a path in both sets is ``added`` or ``removed`` on a missing/present flip,
  ``kind`` on a file/symlink/directory change, ``mode`` on an executable-bit
  change, and ``content`` or ``formatting-only`` on a content change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from heddle.contracts import operations as ops
from heddle.runtime.application import execute
from tests.tiering_helpers import invoke
from tests.tiering_review_helpers import V7_FEATURE

PATH_CHANGE_KINDS = frozenset(
    {"content", "formatting-only", "added", "removed", "kind", "mode", "membership"}
)
MANIFEST_ROW_FIELDS = ("basis_evidence", "reference_equivalence", "rebound_by")
SOURCE_EVIDENCE_V2 = "heddle.source-evidence/v2"


def typed_changes(explanation, *, ac="AC-6") -> set[tuple[str, str]]:
    """Return ``{(kind, subject)}``: the path for a path change, or
    ``("component", <name>)`` for a named component change."""
    subject = explanation.get("subject") or explanation.get("scope")
    assert "changes" in explanation, (
        f"FAIL {ac}: the explanation for {subject} carries no typed `changes`: "
        f"{sorted(explanation)}"
    )
    pairs = set()
    for change in explanation["changes"]:
        assert isinstance(change, dict) and isinstance(change.get("kind"), str), (
            f"FAIL {ac}: a typed change is not a mapping with a kind: {change!r}"
        )
        kind = change["kind"]
        if kind == "component" or kind.startswith("component:"):
            name = (
                kind.partition(":")[2] or change.get("name") or change.get("component")
            )
            assert name, f"FAIL {ac}: a component change names no component: {change}"
            pairs.add(("component", name))
            continue
        assert kind in PATH_CHANGE_KINDS, f"FAIL {ac}: unknown change kind: {change}"
        assert isinstance(change.get("path"), str), (
            f"FAIL {ac}: a {kind} change names no path: {change!r}"
        )
        pairs.add((kind, change["path"]))
    return pairs


def assert_names_exactly(explanation, expected, *, ac="AC-6"):
    """Typed changes name exactly ``expected``; the path list stays beside them."""
    observed = typed_changes(explanation, ac=ac)
    assert observed == set(expected), (
        f"FAIL {ac}: {explanation.get('subject') or explanation.get('scope')} "
        f"names {sorted(observed)}, expected exactly {sorted(expected)}"
    )
    paths = sorted({path for kind, path in expected if kind != "component"})
    assert sorted(explanation["changed_dependencies"]) == paths, (
        f"FAIL {ac}: changed_dependencies stopped being the changed path list: "
        f"{explanation['changed_dependencies']} versus {paths}"
    )


def assignment_row(role, scope, *, operation=ops.Status):
    """One review assignment row from the public status or orient projection."""
    result = execute(operation(feature=V7_FEATURE))
    assert result.ok, result.to_envelope()
    return next(
        row
        for row in result.data["review_closure"]["assignments"]
        if row["role"] == role and row["scope"] == scope
    )


def explanation_for(row, subject):
    matches = [
        item for item in row["evidence_explanations"] if item["subject"] == subject
    ]
    assert len(matches) == 1, (
        f"test setup: expected one explanation for {subject}, got "
        f"{[item['subject'] for item in row['evidence_explanations']]}"
    )
    return matches[0]


def verification_row(scope, *, operation=ops.Status):
    result = execute(operation(feature=V7_FEATURE))
    assert result.ok, result.to_envelope()
    return next(
        row for row in result.data["verification_status"] if row["scope"] == scope
    )


def disposition_rows(state_path):
    return yaml.safe_load(state_path.read_text())["review_assignments"]["dispositions"]


def basis_manifest(state_path, row, *, ac="AC-9"):
    """The published manifest a disposition row names, checked by its digests."""
    reference = row.get("basis_evidence")
    assert isinstance(reference, dict), (
        f"FAIL {ac}: disposition {row['run_id']}#{row['finding_id']} names no "
        "basis manifest (basis_evidence)"
    )
    assert reference["source_sha256"] == row["evidence_basis"], (
        f"FAIL {ac}: the named manifest's logical digest differs from evidence_basis"
    )
    artifact = state_path.parent / reference["artifact"]
    assert artifact.is_file(), f"FAIL {ac}: named manifest is absent: {artifact}"
    content = artifact.read_bytes()
    assert hashlib.sha256(content).hexdigest() == reference["artifact_sha256"], (
        f"FAIL {ac}: named manifest bytes differ from its artifact digest"
    )
    assert yaml.safe_load(content)["schema"] == SOURCE_EVIDENCE_V2, (
        f"FAIL {ac}: the basis manifest is not {SOURCE_EVIDENCE_V2}"
    )
    return reference


def reviewer_basis_manifest(state_path, run_id, *, ac="AC-6"):
    """Decode the reviewer-basis manifest a recorded attempt carries."""
    from heddle.kernel.source_manifest import decode_source_evidence

    attempts = yaml.safe_load(state_path.read_text())["review_assignments"]["attempts"]
    matches = [row for row in attempts if row["attempt_id"] == run_id]
    assert len(matches) == 1, f"test setup: no single attempt for run {run_id}"
    manifests = []
    attempt = matches[0]
    for artifact in attempt["artifacts"]:
        if artifact["role"] != "evidence":
            continue
        content = (state_path.parent / artifact["path"]).read_bytes()
        if yaml.safe_load(content).get("schema") == SOURCE_EVIDENCE_V2:
            manifests.append(decode_source_evidence(content))
    assert len(manifests) == 1, (
        f"FAIL {ac}: attempt {run_id} carries {len(manifests)} reviewer-basis "
        f"manifests ({SOURCE_EVIDENCE_V2} evidence artifacts), expected one"
    )
    return manifests[0]


def _component_digests(manifest, ac):
    components = getattr(manifest, "components", None)
    assert components is not None, f"FAIL {ac}: source evidence has no components"
    digests = {}
    for item in components:
        name, digest = (
            (item.name, item.digest) if hasattr(item, "name") else tuple(item)
        )
        digests[name] = digest
    return digests


def changed_components(state_path, before_run, after_run, *, ac="AC-6"):
    """Component names whose digest differs between two recorded attempts.

    Requires both reviewer-basis manifests to observe the same paths, so the
    difference is in non-path basis records only.
    """
    before = reviewer_basis_manifest(state_path, before_run, ac=ac)
    after = reviewer_basis_manifest(state_path, after_run, ac=ac)
    assert before.observations == after.observations, (
        "test setup: the two reviewed bases must observe identical paths"
    )
    left = _component_digests(before, ac)
    right = _component_digests(after, ac)
    return {
        name for name in left.keys() | right.keys() if left.get(name) != right.get(name)
    }


def evidence_files(state_path):
    """Every file under the feature workspace's ``verification/`` directory."""
    root = state_path.parent / "verification"
    if not root.exists():
        return set()
    return {
        path.relative_to(state_path.parent).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def as_recorded_before_manifests(state_path: Path) -> None:
    """Give every disposition row the shape rows had before basis manifests.

    The three fields are optional and absent on rows recorded before them, so
    removing them yields exactly the earlier row shape.
    """
    value = yaml.safe_load(state_path.read_text())
    for row in value["review_assignments"]["dispositions"]:
        for field in MANIFEST_ROW_FIELDS:
            row.pop(field, None)
    state_path.write_text(yaml.safe_dump(value, sort_keys=False))


def open_milestone_round(state_path, *, scope="m1"):
    return invoke(
        "ReviewRoundOpen",
        feature=V7_FEATURE,
        payload={
            "schema": "heddle.review-round-input/v1",
            "role": "milestone-review",
            "scope": scope,
            "purpose": "verification",
            "reason": "Inspect the remaining original concern",
        },
        expect_revision=yaml.safe_load(state_path.read_text())["revision"],
    )
