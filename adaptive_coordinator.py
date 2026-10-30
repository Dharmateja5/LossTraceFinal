"""Checkpoint 4C: execute initial rule-based routing using existing guards.

Does not call the independent Validator. Validation and targeted follow-up
will be wired in subsequent checkpoints.
"""
from __future__ import annotations

from dataclasses import dataclass

from adaptive_routing import RoutingAction, RoutingDecision, plan_initial
from case import CanonicalCaseState
from coordinator import (
    AgentRunner, InvestigationBudget, InvestigationOutcome,
    InvestigationRun, run_investigation,
)
from models import SpecialistRole


@dataclass(frozen=True)
class RoutedInvestigation:
    state: CanonicalCaseState
    next_decision: RoutingDecision
    investigation: InvestigationRun | None


async def run_initial_routed(
    dossier_id: str,
    state: CanonicalCaseState,
    *,
    coverage_runner: AgentRunner | None = None,
    evidence_runner: AgentRunner | None = None,
    budget: InvestigationBudget | None = None,
) -> RoutedInvestigation:
    """Choose missing specialists, run only those, then determine next step."""
    decision = plan_initial(state)

    if decision.action != RoutingAction.RUN_SPECIALISTS:
        return RoutedInvestigation(state, decision, None)

    # The plan is valid only for the exact evidence basis it was built for.
    if any(
        task.basis_case_version != state.case_version
        or task.basis_evidence_hash != state.evidence_snapshot_hash
        for task in decision.tasks
    ):
        raise ValueError("Routing tasks have stale evidence basis")

    roles = tuple(task.specialist for task in decision.tasks)
    if any(r not in (SpecialistRole.COVERAGE, SpecialistRole.EVIDENCE) for r in roles):
        raise ValueError("Initial routing currently supports Coverage and Evidence")

    investigation = await run_investigation(
        dossier_id, state,
        coverage_runner=coverage_runner,
        evidence_runner=evidence_runner,
        budget=budget,
        selected_roles=roles,
    )

    if investigation.outcome != InvestigationOutcome.PENDING_SPECIALISTS:
        return RoutedInvestigation(
            investigation.state,
            RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                            "Specialist execution failed; do not validate partial results"),
            investigation,
        )

    return RoutedInvestigation(
        investigation.state,
        plan_initial(investigation.state),
        investigation,
    )
