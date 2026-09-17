"""Audit helpers for autopilot close artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heddle.contracts.decisions import AcceptDegradedSmoke, PolicyResolution
from heddle.contracts.schemas import POLICY_JOURNAL_FIELDS
from heddle.kernel.project_config import ProjectConfig
from heddle.kernel.smoke_disposition import ACCEPT_DEGRADED_SMOKE
from heddle.kernel.state import DecisionFact, StateFile

_FIELD_RE = re.compile(r"\s*-\s*([^:]+)\s*:\s*(.*)\s*$")
AuditStatus = Literal["missing", "incomplete", "complete"]


@dataclass(frozen=True)
class DecisionJournalAudit:
    status: AuditStatus
    missing: tuple[str, ...] = ()
    malformed: tuple[str, ...] = ()
    unbacked: tuple[str, ...] = ()


def decision_journal_path(config: ProjectConfig, feature: str) -> Path:
    return config.root / config.layout.plans / f"{feature}.decision-journal.md"


def friction_retrospective_path(config: ProjectConfig, feature: str) -> Path:
    return config.root / config.layout.plans / f"{feature}.friction-retrospective.md"


def policy_decision_facts(state: StateFile) -> tuple[DecisionFact, ...]:
    return tuple(
        decision
        for decision in state.decisions
        if getattr(decision, "resolution_source", None) == "policy"
    )


def journal_decision_facts(state: StateFile) -> tuple[DecisionFact, ...]:
    return tuple(
        decision
        for decision in state.decisions
        if decision.resolution_source == "policy"
        or (
            decision.smoke_disposition is not None
            and decision.status == "resolved"
            and decision.resolution_source == "user"
            and isinstance(decision.resolution, AcceptDegradedSmoke)
        )
    )


def decision_journal_audit(
    config: ProjectConfig, feature: str, state: StateFile
) -> DecisionJournalAudit:
    journal = decision_journal_path(config, feature)
    facts = journal_decision_facts(state)
    if not journal.is_file():
        return DecisionJournalAudit(
            status="missing",
            missing=tuple(str(getattr(fact, "id", "")) for fact in facts),
        )
    try:
        text = journal.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DecisionJournalAudit(
            status="missing",
            missing=tuple(str(getattr(fact, "id", "")) for fact in facts),
        )

    return decision_journal_text_audit(text, state)


def decision_journal_text_audit(text: str, state: StateFile) -> DecisionJournalAudit:
    """Audit already-read journal text without changing read-surface statuses."""

    facts = journal_decision_facts(state)
    sections = journal_sections(text)
    accepted_policy_ids = {fact.id for fact in policy_decision_facts(state)}
    unbacked = tuple(
        identifier
        for identifier, entries in sections.items()
        if identifier not in accepted_policy_ids
        and any(
            _section_fields(entry).get("record_kind") == "policy" for entry in entries
        )
    )
    missing: list[str] = []
    malformed: list[str] = []
    for fact in facts:
        decision_id = str(getattr(fact, "id", ""))
        entries = sections.get(decision_id, ())
        if not entries:
            missing.append(decision_id)
            continue
        if len(entries) != 1 or not _section_matches_fact(entries[0], fact):
            malformed.append(decision_id)
    if missing or malformed:
        return DecisionJournalAudit(
            status="incomplete",
            missing=tuple(missing),
            malformed=tuple(malformed),
            unbacked=unbacked,
        )
    return DecisionJournalAudit(status="complete", unbacked=unbacked)


def should_surface_decision_journal_audit(
    config: ProjectConfig, feature: str, state: StateFile
) -> bool:
    return (
        bool(journal_decision_facts(state))
        or decision_journal_path(config, feature).exists()
    )


def surfaced_decision_journal_audit(
    config: ProjectConfig, feature: str, state: StateFile
) -> DecisionJournalAudit | None:
    if not should_surface_decision_journal_audit(config, feature, state):
        return None
    return decision_journal_audit(config, feature, state)


def journal_sections(text: str) -> dict[str, list[str]]:
    """Index authored sections without hiding repeated decision IDs."""
    sections: dict[str, list[str]] = {}
    for section in re.split(r"(?m)^##\s+", text):
        stripped = section.lstrip()
        if not stripped:
            continue
        heading = stripped.splitlines()[0]
        decision_id = heading.split(maxsplit=1)[0]
        sections.setdefault(decision_id, []).append(section)
    return sections


def _section_matches_fact(section: str, fact: DecisionFact) -> bool:
    fields = _section_fields(section)
    if fact.smoke_disposition is not None and fact.resolution_source == "user":
        disposition = fact.smoke_disposition
        expected = {
            "choice": ACCEPT_DEGRADED_SMOKE,
            "attribution": disposition["attribution"],
            "smoke_fact_sha256": disposition["bindings"]["smoke"],
        }
        return all(
            _normalize_ws(fields.get(key, "")) == _normalize_ws(value)
            for key, value in expected.items()
        )
    for field in POLICY_JOURNAL_FIELDS:
        if field == "review_flag":
            # Optional marker (autonomy addendum step 4 records no tag); a
            # journal entry need not carry [REVIEW] to be well-formed.
            continue
        if not fields.get(field):
            return False
    resolution = fact.resolution
    if not isinstance(resolution, PolicyResolution):
        return False
    expected = {
        "record_kind": "policy",
        "title": fact.title,
        "rationale": fact.rationale or "",
        "choice": resolution.choice,
        "basis": resolution.basis,
        "reversibility": resolution.reversibility,
        "confidence": resolution.confidence,
        "review_flag": resolution.review_flag,
        "scope": resolution.scope,
        "routes_to": ", ".join(fact.routes_to),
    }
    if any(
        _normalize_ws(fields.get(key, "")) != _normalize_ws(value)
        for key, value in expected.items()
    ):
        return False

    # min-b: compare on whitespace-normalized values so a rendered entry that
    # differs only in incidental spacing still round-trips against the fact.
    question = getattr(fact, "question", None)
    if question is not None and _normalize_ws(fields["question"]) != _normalize_ws(
        str(question)
    ):
        return False

    options = tuple(
        _normalize_ws(str(option)) for option in getattr(fact, "options", ())
    )
    if options:
        # The renderer emits options as a single
        # ``, ``-joined line. The old audit re-split that line into a tuple and
        # compared tuple-equality, so an option containing a comma split into
        # the wrong arity and never matched its fact — wedging the fact-backed
        # close permanently
        # (AC-9/AC-18). Instead compare the rendered line against the canonical
        # ``, ``-join of the fact's options, running both through the SAME
        # normalization (whitespace + comma-separator spacing). Identical
        # serialization on both sides keeps the round-trip lossless for internal
        # commas while still tolerating incidental spacing (min-b).
        canonical = _normalize_option_line(", ".join(options))
        if _normalize_option_line(fields["options"]) != canonical:
            return False

    return True


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def _normalize_option_line(value: str) -> str:
    # Canonicalize a rendered options line so incidental spacing around the comma
    # separator ("A ,  B", "A,B") and the renderer's own ", " both collapse to one
    # form. Applied identically to both sides of the comparison, this is lossless
    # for a comma *inside* an option: that comma normalizes to itself on both
    # sides, so the two serializations still match.
    parts = [part.strip() for part in _normalize_ws(value).split(",")]
    return ", ".join(parts)


def _section_fields(section: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in section.splitlines():
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        fields[match.group(1).strip().lower()] = match.group(2).strip()
    return fields
