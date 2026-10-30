"""LossTrace investigation coordinator — first implementation increment.

This module orchestrates the existing Coverage and Evidence specialists.
It deliberately does not perform Jev routing, valuation, final policy decisions,
settlement, or execution. Those remain part of the agreed full architecture.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Awaitable, Callable

from case import CanonicalCaseState, merge_finding
from models import SpecialistFinding, SpecialistRole, SpecialistStatus
from state_machine import CaseState


AgentRunner = Callable[[str, CanonicalCaseState], Awaitable[SpecialistFinding]]


class StepOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    TOOL_FAILURE = "TOOL_FAILURE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STALE_FINDING = "STALE_FINDING"
    INVALID_FINDING = "INVALID_FINDING"
    REVISION_CONFLICT = "REVISION_CONFLICT"


class InvestigationOutcome(StrEnum):
    PENDING_SPECIALISTS = "PENDING_SPECIALISTS"
    TOOL_FAILURE = "TOOL_FAILURE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STALE_FINDING = "STALE_FINDING"
    INVALID_FINDING = "INVALID_FINDING"
    REVISION_CONFLICT = "REVISION_CONFLICT"


@dataclass(frozen=True)
class InvestigationBudget:
    """Bounds this coordinator invocation, not individual MCP tool counts."""

    total_seconds: float = 240.0
    per_agent_seconds: float = 180.0

    def __post_init__(self) -> None:
        if not (0 < self.total_seconds < float("inf")):
            raise ValueError("total_seconds must be positive and finite")
        if not (0 < self.per_agent_seconds < float("inf")):
            raise ValueError("per_agent_seconds must be positive and finite")


@dataclass(frozen=True)
class AgentStep:
    specialist: SpecialistRole
    outcome: StepOutcome
    detail: str = ""


@dataclass(frozen=True)
class InvestigationRun:
    """A partial investigation result. It is NOT a settlement authorization."""

    state: CanonicalCaseState
    outcome: InvestigationOutcome
    steps: tuple[AgentStep, ...]
    preliminary_review_signal: bool


async def _coverage_runner(
    dossier_id: str, state: CanonicalCaseState
) -> SpecialistFinding:
    # Lazy import allows deterministic coordinator tests without MCP or Nebius.
    from coverage_agent import (
        build_coverage_finding,
        interpret_coverage_context,
        load_coverage_context,
    )

    context = await load_coverage_context(dossier_id, state)
    interpretation = await interpret_coverage_context(context)
    return build_coverage_finding(context, interpretation, state)


async def _evidence_runner(
    dossier_id: str, state: CanonicalCaseState
) -> SpecialistFinding:
    from evidence_agent import run_evidence_agent

    return await run_evidence_agent(dossier_id, state)


def _needs_review(state: CanonicalCaseState) -> bool:
    """Preliminary safety signal only, NOT the final HITL policy decision."""
    return (
        bool(state.conflicts or state.unresolved_questions)
        or any(
            finding.status != SpecialistStatus.SUFFICIENT
            for finding in state.specialist_findings
        )
    )


async def run_investigation(
    dossier_id: str,
    state: CanonicalCaseState,
    *,
    coverage_runner: AgentRunner | None = None,
    evidence_runner: AgentRunner | None = None,
    budget: InvestigationBudget | None = None,
    selected_roles: tuple[SpecialistRole, ...] | None = None,
) -> InvestigationRun:
    """Run the selected specialists, merging accepted findings one at a time.

    On failure, stop and return an explicit outcome with any previously merged
    in-memory findings. Do not advance workflow state or issue financial actions.
    The caller owns persistence and any later lifecycle transitions.
    """
    if not re.fullmatch(r"d-[0-9a-f]{12}", dossier_id):
        raise ValueError("Invalid dossier ID")
    if state.workflow_state != CaseState.INVESTIGATION:
        raise ValueError("Coordinator requires INVESTIGATION workflow state")

    limits = budget if budget is not None else InvestigationBudget()
    started = monotonic()
    steps: list[AgentStep] = []
    current = state

    available = {
        SpecialistRole.COVERAGE: coverage_runner or _coverage_runner,
        SpecialistRole.EVIDENCE: evidence_runner or _evidence_runner,
    }
    roles = selected_roles if selected_roles is not None else (
        SpecialistRole.COVERAGE, SpecialistRole.EVIDENCE,
    )
    if not roles or len(roles) != len(set(roles)) or any(
        role not in available for role in roles
    ):
        raise ValueError("Invalid selected specialist roles")

    for expected_role in roles:
        agent = available[expected_role]
        remaining = limits.total_seconds - (monotonic() - started)
        if remaining <= 0:
            steps.append(AgentStep(expected_role, StepOutcome.BUDGET_EXCEEDED))
            return InvestigationRun(
                current, InvestigationOutcome.BUDGET_EXCEEDED,
                tuple(steps), preliminary_review_signal=True,
            )

        try:
            finding = await asyncio.wait_for(
                agent(dossier_id, current),
                timeout=min(limits.per_agent_seconds, remaining),
            )
        except TimeoutError:
            steps.append(AgentStep(expected_role, StepOutcome.BUDGET_EXCEEDED))
            return InvestigationRun(
                current, InvestigationOutcome.BUDGET_EXCEEDED,
                tuple(steps), preliminary_review_signal=True,
            )
        except Exception as exc:
            import traceback
            traceback.print_exception(exc)
            # Return the error *type*, not raw exception text containing
            # potentially sensitive claim or service information.
            steps.append(AgentStep(
                expected_role, StepOutcome.TOOL_FAILURE, type(exc).__name__,
            ))
            return InvestigationRun(
                current, InvestigationOutcome.TOOL_FAILURE,
                tuple(steps), preliminary_review_signal=True,
            )

        if not isinstance(finding, SpecialistFinding) or finding.specialist != expected_role:
            steps.append(AgentStep(expected_role, StepOutcome.INVALID_FINDING))
            return InvestigationRun(
                current, InvestigationOutcome.INVALID_FINDING,
                tuple(steps), preliminary_review_signal=True,
            )

        try:
            merge_outcome, updated = merge_finding(
                current, finding, expected_revision=current.revision
            )
        except ValueError:
            steps.append(AgentStep(expected_role, StepOutcome.REVISION_CONFLICT))
            return InvestigationRun(
                current, InvestigationOutcome.REVISION_CONFLICT,
                tuple(steps), preliminary_review_signal=True,
            )

        if merge_outcome != "ACCEPTED":
            steps.append(AgentStep(expected_role, StepOutcome.STALE_FINDING))
            return InvestigationRun(
                current, InvestigationOutcome.STALE_FINDING,
                tuple(steps), preliminary_review_signal=True,
            )

        current = updated
        steps.append(AgentStep(expected_role, StepOutcome.ACCEPTED))

    # Valuation, Jev routing, independent validation, final policy evaluation,
    # authorization and settlement are NOT complete at this stage.
    return InvestigationRun(
        current,
        InvestigationOutcome.PENDING_SPECIALISTS,
        tuple(steps),
        preliminary_review_signal=_needs_review(current),
    )
