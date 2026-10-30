from decimal import Decimal, ROUND_HALF_UP

from models import LossComponent

from dataclasses import dataclass

CENT = Decimal("0.01")


def total_supported_amount(
    components: list[LossComponent],
) -> Decimal:
    """Add the supported loss components deterministically."""

    total = Decimal("0.00")

    for component in components:
        total += Decimal(component.supported_amount)

    return total


def calculate_o_and_p(
    supported_amount: Decimal,
    rate: Decimal,
) -> Decimal:
    """Calculate supported overhead and profit."""

    return (supported_amount * rate).quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )

def apply_deductible(
    amount: Decimal,
    deductible: Decimal,
) -> Decimal:
    """Apply the deductible without allowing a negative result."""

    payable = amount - deductible

    return max(
        payable,
        Decimal("0.00"),
    )

def apply_sublimit(
    supported_amount: Decimal,
    sublimit: Decimal,
) -> Decimal:
    """Cap a supported amount at the applicable policy sublimit."""

    return min(
        supported_amount,
        sublimit,
    )

def apply_policy_limit(
    amount: Decimal,
    policy_limit: Decimal,
) -> Decimal:
    """Cap the payable amount at the overall policy limit."""

    return min(
        amount,
        policy_limit,
    )


def subtract_prior_payments(
    payable_amount: Decimal,
    prior_payments: Decimal,
) -> Decimal:
    """Subtract amounts already paid without going below zero."""

    return max(
        payable_amount - prior_payments,
        Decimal("0.00"),
    )

@dataclass(frozen=True)
class FinancialResult:
    supported_loss: Decimal
    o_and_p: Decimal
    amount_after_o_and_p: Decimal
    deductible: Decimal
    amount_after_deductible: Decimal
    policy_limit: Decimal
    amount_after_policy_limit: Decimal
    prior_payments: Decimal
    remaining_payable: Decimal


def calculate_payable(
    supported_loss: Decimal,
    o_and_p_rate: Decimal,
    deductible: Decimal,
    policy_limit: Decimal,
    prior_payments: Decimal,
) -> FinancialResult:
    o_and_p = calculate_o_and_p(
        supported_loss,
        o_and_p_rate,
    )

    amount_after_o_and_p = supported_loss + o_and_p

    # Supplied coverage form: limits first, then the remaining deductible.
    amount_after_policy_limit = apply_policy_limit(
        amount_after_o_and_p,
        policy_limit,
    )

    amount_after_deductible = apply_deductible(
        amount_after_policy_limit,
        deductible,
    )

    remaining_payable = subtract_prior_payments(
        amount_after_deductible,
        prior_payments,
    )

    return FinancialResult(
        supported_loss=supported_loss,
        o_and_p=o_and_p,
        amount_after_o_and_p=amount_after_o_and_p,
        deductible=deductible,
        amount_after_deductible=amount_after_deductible,
        policy_limit=policy_limit,
        amount_after_policy_limit=amount_after_policy_limit,
        prior_payments=prior_payments,
        remaining_payable=remaining_payable,
    )


def total_after_sublimits(
    components: list[LossComponent],
    sublimits: dict[str, Decimal],
) -> Decimal:
    """Apply category-specific sublimits before totaling the loss."""

    totals_by_category: dict[str, Decimal] = {}

    for component in components:
        amount = Decimal(component.supported_amount)

        totals_by_category[component.category] = (
            totals_by_category.get(
                component.category,
                Decimal("0.00"),
            )
            + amount
        )

    total = Decimal("0.00")

    for category, amount in totals_by_category.items():
        sublimit = sublimits.get(category)

        if sublimit is not None:
            amount = apply_sublimit(
                amount,
                sublimit,
            )

        total += amount

    return total


def calculate_claim_payable(
    components: list[LossComponent],
    sublimits: dict[str, Decimal],
    o_and_p_rate: Decimal,
    deductible: Decimal,
    policy_limit: Decimal,
    prior_payments: Decimal,
) -> FinancialResult:
    """Calculate the claim payable starting from supported loss components."""

    supported_loss = total_after_sublimits(
        components,
        sublimits,
    )

    return calculate_payable(
        supported_loss=supported_loss,
        o_and_p_rate=o_and_p_rate,
        deductible=deductible,
        policy_limit=policy_limit,
        prior_payments=prior_payments,
    )
