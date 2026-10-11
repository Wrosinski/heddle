"""User-owned waivers of a default witness rerun, bound to its passing fact."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from heddle.contracts.schemas import WITNESS_WAIVER_OPTIONAL_KEYS
from heddle.kernel.project_config import KernelError
from heddle.kernel.smoke_disposition import verification_fact_identity
from heddle.kernel.source_manifest import (
    EvidenceReference,
    decode_evidence_reference,
    normalize_source_paths,
)

if TYPE_CHECKING:
    from heddle.kernel.state import StateFile
    from heddle.kernel.verification import VerificationFreshness

ACCEPT_PRIOR_WITNESS = "accept-prior-witness"
WITNESS_SCOPES = frozenset({"acceptance", "live"})
# Later boundaries, after review-driven changes, may accept the earlier passing
# witness in place of its default rerun.
WAIVER_STAGES = frozenset({"peer-review", "robustness", "complete"})
# The pre-review run at implement exit is mandatory unless only formatting
# changed since a passing witness; the runtime observes that condition.
IMPLEMENT_EXIT_STAGE = "implement"
WAIVABLE_STATUSES = frozenset({"content-stale", "source-set-stale"})
WAIVER_INPUT_KEYS = frozenset({"scope"})
WAIVER_BINDING_KEYS = WAIVER_INPUT_KEYS | {"fact", "ownership", "source_sha256"}
# The manifest of the bound source. A binding that names it stays current
# across review-equivalent changes; one recorded without it compares exactly.
WAIVER_EVIDENCE_KEY = WITNESS_WAIVER_OPTIONAL_KEYS[0]


def waivable_witness_rerun(stage: str, status: VerificationFreshness) -> bool:
    """Whether a stale lane awaits its default post-review rerun."""
    return (
        stage in WAIVER_STAGES
        and status.scope in WITNESS_SCOPES
        and status.status in WAIVABLE_STATUSES
        and status.waived_by is None
    )


def acceptable_prior_witness(stage: str, status: VerificationFreshness) -> bool:
    """Whether implement exit may accept a lane's earlier passing witness.

    Only a content-stale fact qualifies, so the fact passed, was stable and ran
    the current command over the current source set; every change since its run
    must be formatting-only.
    """
    return (
        stage == IMPLEMENT_EXIT_STAGE
        and status.scope in WITNESS_SCOPES
        and status.formatting_only
        and status.waived_by is None
    )


def verification_refresh_reason(stage: str, status: VerificationFreshness) -> str:
    """Name a stale scope's rerun, and the owner's waiver when one is open."""
    return f"refresh {status.scope} verification ({status.status})" + witness_offer(
        stage, status
    )


def witness_offer(stage: str, status: VerificationFreshness, lane: str = "this") -> str:
    """The owner's alternative to a witness lane's rerun, or nothing."""
    if waivable_witness_rerun(stage, status):
        return (
            f"; {lane} post-review rerun is the default and the owner may waive it "
            "with a witness-waiver decision (see `heddle decisions add --help`)"
        )
    if acceptable_prior_witness(stage, status):
        return (
            f"; only formatting changed since {lane} witness passed, so the owner "
            "may accept it in place of the rerun with a witness-waiver decision "
            "(see `heddle decisions add --help`)"
        )
    return ""


def validate_witness_waiver(value: Any, *, bound: bool) -> dict[str, Any]:
    keys = WAIVER_BINDING_KEYS if bound else WAIVER_INPUT_KEYS
    if (
        not isinstance(value, dict)
        or not keys <= set(value)
        or not set(value) <= keys | ({WAIVER_EVIDENCE_KEY} if bound else set())
    ):
        raise _invalid(
            "must contain exactly "
            + ", ".join(sorted(keys))
            + (f", and optionally {WAIVER_EVIDENCE_KEY}" if bound else "")
        )
    scope = value["scope"]
    if not isinstance(scope, str) or scope not in WITNESS_SCOPES:
        raise _invalid(
            f"scope must name one witness lane, acceptance or live; got {scope!r}"
        )
    if bound:
        ownership = value["ownership"]
        if (
            not isinstance(ownership, list)
            or not ownership
            or list(normalize_source_paths(ownership)) != ownership
        ):
            raise _invalid("ownership must retain the normalized full union")
        for key in ("fact", "source_sha256"):
            digest = value[key]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise _invalid(f"{key} must be an exact SHA-256 value")
        if WAIVER_EVIDENCE_KEY in value:
            reference = waiver_source_evidence(value)
            assert reference is not None
            if reference.source_sha256 != value["source_sha256"]:
                raise _invalid(
                    f"{WAIVER_EVIDENCE_KEY} must name the bound source_sha256"
                )
    return dict(value)


def waiver_source_evidence(value: Any) -> EvidenceReference | None:
    """The manifest a bound waiver names, or None for an exact binding."""
    if not isinstance(value, Mapping) or WAIVER_EVIDENCE_KEY not in value:
        return None
    try:
        return decode_evidence_reference(value[WAIVER_EVIDENCE_KEY])
    except KernelError as error:
        raise _invalid(f"{WAIVER_EVIDENCE_KEY} {error.message}") from error


def validate_witness_waiver_proposal(state: StateFile, value: object) -> dict[str, Any]:
    """Validate the stage and lane without observing source files."""
    proposal = validate_witness_waiver(value, bound=False)
    if state.stage not in WAIVER_STAGES | {IMPLEMENT_EXIT_STAGE}:
        raise _invalid(
            "applies only from implement exit on; the pre-review witness run "
            "is mandatory"
        )
    if (
        proposal["scope"] == "live"
        and not state.commands.get("live_e2e_test", "").strip()
    ):
        raise _invalid("live scope requires a declared live_e2e_test command")
    return proposal


def validate_witness_bindings(state: StateFile, value: object) -> None:
    """Validate immutable ledger relationships, without requalifying source bytes."""
    from heddle.kernel.verification import (
        resolve_source_declaration,
        verification_command_for_scope,
    )

    bound = validate_witness_waiver(value, bound=True)
    scope = bound["scope"]
    if tuple(bound["ownership"]) != resolve_source_declaration(state, scope).paths:
        raise _invalid("ownership differs from the full ownership union")
    fact = next(
        (fact for fact in reversed(state.verifications) if fact.scope == scope),
        None,
    )
    if fact is None or verification_fact_identity(fact) != bound["fact"]:
        raise _invalid(f"{scope} binding must match the latest scoped fact")
    if (
        fact.exit_code != 0
        or fact.command != verification_command_for_scope(state, scope)
        or fact.evidence.before.source_sha256 != fact.evidence.after.source_sha256
    ):
        raise _invalid(
            f"{scope} binding requires a stable passing fact with the current command"
        )


def _invalid(message: str) -> KernelError:
    return KernelError(
        code="workspace-invalid",
        message="witness_waiver " + message,
        hint="repair the witness waiver and obtain an explicit user resolution",
    )
