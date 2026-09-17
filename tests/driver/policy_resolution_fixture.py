"""Shared AC-18 policy-resolution fixture for driver tests."""

from __future__ import annotations

from typing import Any

from heddle.contracts.schemas import POLICY_JOURNAL_FIELDS
from tests.driver.constants import AUTO_TIER2_PLAN

POLICY_RESOLUTION_FIXTURE: dict[str, Any] = {
    "id": "P1",
    "title": "Reversible naming choice",
    "question": "which name",
    "options": ("A", "B"),
    "choice": "A",
    "rationale": "Chose option A (most reversible)",
    "basis": "Reversibility beats optimality",
    "reversibility": "reversible",
    "confidence": "high",
    "review_flag": "[REVIEW]",
    "scope": "feature",
    "routes_to": (AUTO_TIER2_PLAN,),
}
assert set(POLICY_JOURNAL_FIELDS) <= set(POLICY_RESOLUTION_FIXTURE)


def policy_resolution_payload(*, routes_to: str) -> dict[str, Any]:
    payload = dict(POLICY_RESOLUTION_FIXTURE)
    payload["options"] = list(POLICY_RESOLUTION_FIXTURE["options"])
    payload["routes_to"] = [routes_to]
    return payload


def render_policy_journal(
    *,
    choice: str | None = None,
    review_flag: str | None = None,
) -> str:
    fixture = dict(POLICY_RESOLUTION_FIXTURE)
    fixture["choice"] = choice if choice is not None else str(fixture["choice"])
    fixture["review_flag"] = (
        review_flag if review_flag is not None else str(fixture["review_flag"])
    )
    fixture["options"] = ", ".join(str(option) for option in fixture["options"])
    fixture["routes_to"] = ", ".join(fixture["routes_to"])
    return (
        "# Decision Journal\n\n"
        f"## {fixture['id']} — {fixture['title']}\n\n"
        "- record_kind: policy\n"
        + "\n".join(f"- {key}: {fixture[key]}" for key in POLICY_JOURNAL_FIELDS)
        + "\n"
    )
