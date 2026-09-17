"""Escalation registry for autopilot.

The taxonomy itself is single-sourced in ``heddle.contracts.schemas`` so the
runtime validators, the driver, and the published
`decision-batch/v0` input schema all read one vocabulary; this module
re-exports the driver-facing names unchanged.
"""

from heddle.contracts.schemas import (  # noqa: F401 — re-exports
    DECISION_BATCH_CLASSES,
    DERIVED_BLOCKER_CLASSES,
    ESCALATION_REGISTRY,
)
