from decimal import Decimal
from models import SpecialistStatus
from dataclasses import dataclass


def requires_human_review(
    payable_amount: Decimal,
    authority_threshold: Decimal,
) -> bool:
    """Return True when the amount exceeds automatic authority."""

    return payable_amount > authority_threshold


def finding_requires_human_review(
    status: SpecialistStatus,
) -> bool:
    """Require HITL when the investigation basis is not safely resolved."""

    return status in {
        SpecialistStatus.INSUFFICIENT,
        SpecialistStatus.CONFLICTING,
        SpecialistStatus.MULTIPLE_OPTIONS,
    }


@dataclass(frozen=True)
class PolicyDecision:
    hitl_required: bool
    reasons: tuple[str, ...]


def evaluate_policy(
    payable_amount: Decimal,
    authority_threshold: Decimal,
    finding_statuses: list[SpecialistStatus],
) -> PolicyDecision:
    """Evaluate the current deterministic HITL rules."""

    reasons: list[str] = []

    if requires_human_review(
        payable_amount,
        authority_threshold,
    ):
        reasons.append("AUTHORITY_THRESHOLD_EXCEEDED")

    for status in finding_statuses:
        if finding_requires_human_review(status):
            reasons.append(f"FINDING_{status.value}")

    return PolicyDecision(
        hitl_required=bool(reasons),
        reasons=tuple(reasons),
    )