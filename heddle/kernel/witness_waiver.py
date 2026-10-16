"""User-owned waivers of a default witness rerun, bound to its passing fact."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
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


# How the owner may answer a witness lane's routed rerun.
WAIVABLE = "waivable"
ACCEPT_PRIOR = "accept-prior"
RERUN_ONLY = "rerun-only"
# Why only a rerun can answer a lane whose evidence never stood as a witness.
_RERUN_ONLY_CAUSES = {
    "missing": "no run is recorded",
    "failed": "the last run failed",
    "unstable": "the source changed during the last run",
    "command-stale": "the stored command changed since the last run",
}


@dataclass(frozen=True)
class WitnessRerunRoute:
    """A witness lane's owner alternative to its routed rerun.

    It states eligibility, not permission to run now.
    """

    route: str
    reason: str

    @property
    def offers_waiver(self) -> bool:
        return self.route != RERUN_ONLY

    def payload(self) -> dict[str, str]:
        return {"route": self.route, "reason": self.reason}

    def lapsed(self, decision_id: str) -> WitnessRerunRoute:
        """This route, noting a recorded waiver of the lane that no longer binds."""
        return replace(
            self,
            reason=f"witness waiver {decision_id} stopped binding when the lane "
            f"changed after it; {self.reason}",
        )


def waiver_stage(stage: str) -> bool:
    """Whether a witness waiver may be proposed at ``stage``."""
    return stage in WAIVER_STAGES or stage == IMPLEMENT_EXIT_STAGE


def witness_rerun_route(
    stage: str, status: VerificationFreshness
) -> WitnessRerunRoute | None:
    """How the owner may answer a witness lane's rerun at ``stage``.

    None for a fresh or waived lane and for every other scope: smoke has its
    own degraded acceptance, and milestone scopes have no owner alternative.
    """
    if (
        status.scope not in WITNESS_SCOPES
        or status.status == "fresh"
        or status.waived_by is not None
    ):
        return None
    if status.status not in WAIVABLE_STATUSES:
        return WitnessRerunRoute(
            RERUN_ONLY,
            f"{_RERUN_ONLY_CAUSES[status.status]}, and only a stable passing run "
            "of the current command can stand in for a rerun",
        )
    if stage in WAIVER_STAGES:
        return WitnessRerunRoute(
            WAIVABLE,
            "the post-review rerun is the default, and the owner may waive it with "
            "a witness-waiver decision",
        )
    if stage != IMPLEMENT_EXIT_STAGE:
        return WitnessRerunRoute(
            RERUN_ONLY,
            "the pre-review witness run is mandatory, and a waiver applies only "
            "from implement exit on",
        )
    if status.formatting_only:
        # Only a content-stale fact qualifies, so the fact passed, was stable
        # and ran the current command over the current source set.
        return WitnessRerunRoute(
            ACCEPT_PRIOR,
            "only formatting changed since the witness passed, so the owner may "
            "accept it in place of the rerun with a witness-waiver decision",
        )
    changes = getattr(status.explanation, "changes", ())
    named = ", ".join(
        f"{change.kind} {change.path}" if change.path else change.kind
        for change in changes
        if change.kind != "formatting-only"
    )
    return WitnessRerunRoute(
        RERUN_ONLY,
        "at implement exit only formatting-only changes let the owner accept the "
        f"earlier witness, and its source has {named or 'an unexplained change'}",
    )


def waivable_witness_rerun(status: VerificationFreshness) -> bool:
    """Whether a stale lane awaits its default post-review rerun."""
    return status.rerun_route is not None and status.rerun_route.route == WAIVABLE


def acceptable_prior_witness(status: VerificationFreshness) -> bool:
    """Whether implement exit may accept a lane's earlier passing witness."""
    return status.rerun_route is not None and status.rerun_route.route == ACCEPT_PRIOR


def verification_refresh_reason(status: VerificationFreshness) -> str:
    """Name a stale scope's rerun, and the owner's waiver when one is open."""
    return f"refresh {status.scope} verification ({status.status})" + witness_offer(
        status
    )


def witness_offer(status: VerificationFreshness, lane: str | None = None) -> str:
    """The owner's alternative to a witness lane's rerun, or nothing."""
    route = status.rerun_route
    if route is None or not route.offers_waiver:
        return ""
    named = f"{lane}: " if lane else ""
    return f"; {named}{route.reason} (see `heddle decisions add --help`)"


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
    if not waiver_stage(state.stage):
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
