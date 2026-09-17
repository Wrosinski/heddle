"""Kernel transforms for feature and milestone authoring."""

from __future__ import annotations

import copy
from typing import Any

import pytest

AT = "2026-07-04T09:00Z"


def _import_transform(name: str):
    """
    Import one authoring transform, failing with a remediation hint until it
        lands (red-phase compass, not a collection error).
    """
    import heddle.kernel.write_path as kernel_write_path

    transform = getattr(kernel_write_path, name, None)
    if transform is None:
        pytest.fail(
            f"FAIL: heddle.kernel.write_path.{name} does not exist yet — "
            f"the m6a transform lands in kernel/write_path.py beside "
            f"advance_milestone (red phase until then)"
        )
    return transform


def _base_document(**overrides: Any) -> dict[str, Any]:
    """A minimal valid v1-shaped state document for transform input."""
    document: dict[str, Any] = {
        "schema": "heddle.state/v6",
        "completion": None,
        "feature_inputs": [],
        "feature": "demo",
        "spec": "docs/features/runtime/demo.md",
        "tier": 2,
        "revision": 4,
        "created": "2026-07-01T08:00Z",
        "updated": "2026-07-02T10:00Z",
        "stage": "specify",
        "authorized_through": "specify",
        "flow": "hitl",
        "authorizations": [
            {"through": "specify", "source": "user", "at": "2026-07-01T08:00Z"}
        ],
        "commands": {},
        "milestones": [],
        "gates": [],
        "verifications": [],
        "decisions": [],
        "sessions": [],
    }
    document.update(overrides)
    return document


def _milestone(mid: str, status: str = "todo", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": mid,
        "title": f"Milestone {mid}",
        "satisfies": [],
        "depends_on": [],
        "owns": [],
        "complexity": "low",
        "estimated_hours": [1, 2],
        "verification": {"command": "pytest -q", "expected": "all tests pass"},
        "status": status,
    }
    entry.update(overrides)
    return entry


def _add_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Wire the portfolio scan",
        "complexity": "medium",
        "estimated_hours": [2, 4],
        "verification": {
            "command": "pytest tests/runtime/test_portfolio_status.py -q",
            "expected": "all tests pass",
        },
    }
    payload.update(overrides)
    return payload


class TestAddMilestone:
    """AC-11 kernel half: monotonic id assignment + §4.2 invariants."""

    def test_append_assigns_max_plus_one_and_status_todo(self) -> None:
        add_milestone = _import_transform("add_milestone")
        document = _base_document(
            milestones=[_milestone("m1", "done"), _milestone("m2", "current")]
        )
        original = copy.deepcopy(document)

        result = add_milestone(document, _add_payload())

        assert document == original, (
            "FAIL AC-11: add_milestone must deepcopy — input never mutated"
        )
        added = result["milestones"][-1]
        assert added["id"] == "m3", (
            "FAIL AC-11: ids are m<max+1> — never reused, always monotonic"
        )
        assert added["status"] == "todo", (
            "FAIL AC-11: new milestones are created with status todo (REQ-16)"
        )
        assert added["title"] == "Wire the portfolio scan"

    def test_first_add_on_an_empty_skeleton_assigns_m1(self) -> None:
        add_milestone = _import_transform("add_milestone")
        document = _base_document(milestones=[])

        result = add_milestone(document, _add_payload())

        assert result["milestones"][0]["id"] == "m1", (
            "FAIL AC-11/SP-M1: the first add on an empty skeleton (the native "
            "birth state) assigns m1 — the empty-max base case must not crash"
        )

    def test_id_is_max_plus_one_even_with_a_gap(self) -> None:
        add_milestone = _import_transform("add_milestone")
        document = _base_document(
            milestones=[_milestone("m1"), _milestone("m4", "todo")]
        )

        result = add_milestone(document, _add_payload())

        assert result["milestones"][-1]["id"] == "m5", (
            "FAIL AC-11: id assignment is m<max+1> (never fills gaps — "
            "ids are never reused, WM §4.2)"
        )

    def test_optional_array_fields_default_to_empty_lists(self) -> None:
        add_milestone = _import_transform("add_milestone")
        document = _base_document(milestones=[])

        result = add_milestone(document, _add_payload())

        added = result["milestones"][0]
        for field in ("satisfies", "depends_on", "owns"):
            assert added[field] == [], (
                f"FAIL AC-11: {field} defaults to [] when omitted (payload "
                f"contract table, CP1 dependency references)"
            )
        assert "tasks" not in added, (
            "FAIL AC-11: tasks is not a skeleton field — absent reads as () "
            "(expansion owns it, CP1 dependency references)"
        )

    def test_added_milestone_carries_exactly_the_nine_skeleton_keys(self) -> None:
        add_milestone = _import_transform("add_milestone")
        document = _base_document(milestones=[])

        result = add_milestone(
            document, _add_payload(satisfies=["AC-13"], depends_on=[], owns=[])
        )

        added = result["milestones"][0]
        expected_keys = {
            "id",
            "title",
            "satisfies",
            "depends_on",
            "owns",
            "complexity",
            "estimated_hours",
            "verification",
            "status",
        }
        assert set(added.keys()) == expected_keys, (
            f"FAIL AC-11/R-P3-2: the resulting milestone carries exactly the "
            f"nine skeleton keys; got {sorted(added.keys())}"
        )

    def test_missing_depends_on_reference_raises_out_of_sequence(self) -> None:
        from heddle.kernel.project_config import KernelError

        add_milestone = _import_transform("add_milestone")
        document = _base_document(milestones=[_milestone("m1")])

        with pytest.raises(KernelError) as excinfo:
            add_milestone(document, _add_payload(depends_on=["m9"]))
        assert excinfo.value.code == "milestone-out-of-sequence", (
            "FAIL AC-11: a depends_on reference to an absent milestone is a "
            "§4.2 invariant violation → milestone-out-of-sequence"
        )


class TestEditMilestone:
    """AC-12 kernel half: shallow per-field merge, whole-array replace,
    done-milestone and depends_on guards."""

    def _two_milestone_document(self) -> dict[str, Any]:
        return _base_document(
            milestones=[
                _milestone("m1", "done"),
                _milestone(
                    "m2",
                    "current",
                    satisfies=["AC-2"],
                    owns=["src/example/cli.py"],
                ),
            ]
        )

    def test_named_fields_replace_wholly_and_absent_fields_are_untouched(
        self,
    ) -> None:
        edit_milestone = _import_transform("edit_milestone")
        document = self._two_milestone_document()
        original = copy.deepcopy(document)

        result = edit_milestone(
            document,
            "m2",
            {
                "title": "Wire and harden the portfolio scan",
                "estimated_hours": [3, 5],
                "depends_on": ["m1"],
            },
        )

        assert document == original, (
            "FAIL AC-12: edit_milestone must deepcopy — input never mutated"
        )
        edited = next(m for m in result["milestones"] if m["id"] == "m2")
        assert edited["title"] == "Wire and harden the portfolio scan"
        assert edited["estimated_hours"] == [3, 5]
        assert edited["depends_on"] == ["m1"], (
            "FAIL AC-12(a)/array replacement: a named array field replaces as a whole "
            "array"
        )
        assert edited["satisfies"] == ["AC-2"], (
            "FAIL AC-12(a): fields absent from the payload are untouched"
        )
        assert edited["owns"] == ["src/example/cli.py"], (
            "FAIL AC-12(a): unlisted arrays are untouched (array replacement shallow "
            "merge)"
        )
        assert edited["status"] == "current", (
            "FAIL AC-12: edit never touches status (INV-4 single-owner)"
        )

    def test_arrays_replace_never_element_merge(self) -> None:
        edit_milestone = _import_transform("edit_milestone")
        document = _base_document(
            milestones=[_milestone("m1", satisfies=["AC-1", "AC-2"])]
        )

        result = edit_milestone(document, "m1", {"satisfies": ["AC-3"]})

        edited = result["milestones"][0]
        assert edited["satisfies"] == ["AC-3"], (
            "FAIL AC-12/array replacement: array payload fields replace the stored "
            "array "
            "wholly — never an element-wise merge or union"
        )

    def test_editing_a_done_milestone_raises_out_of_sequence(self) -> None:
        from heddle.kernel.project_config import KernelError

        edit_milestone = _import_transform("edit_milestone")
        document = self._two_milestone_document()

        with pytest.raises(KernelError) as excinfo:
            edit_milestone(document, "m1", {"title": "rewrite history"})
        assert excinfo.value.code == "milestone-out-of-sequence", (
            "FAIL AC-12(c): done milestones are not editable (WM §4.2)"
        )

    def test_depends_on_reference_to_missing_milestone_raises(self) -> None:
        from heddle.kernel.project_config import KernelError

        edit_milestone = _import_transform("edit_milestone")
        document = self._two_milestone_document()

        with pytest.raises(KernelError) as excinfo:
            edit_milestone(document, "m2", {"depends_on": ["m9"]})
        assert excinfo.value.code == "milestone-out-of-sequence", (
            "FAIL AC-12(d): depends_on must reference existing milestones"
        )

    def test_identity_payload_is_a_convergent_noop(self) -> None:
        edit_milestone = _import_transform("edit_milestone")
        document = self._two_milestone_document()

        result = edit_milestone(document, "m2", {"title": "Milestone m2"})

        assert result == document, (
            "FAIL AC-12: an edit that changes nothing must return the "
            "unchanged document (convergent no-op — no revision bump)"
        )
