"""LossTrace Checkpoint 4D: independent validation and one bounded follow-up.

Orchestration only: no Jev, state transition, HITL authorization, valuation,
financial decision, payment write, checkpoint persistence, or audit mutation.
The durable Case Workflow must enforce those gates separately.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Awaitable, Callable

from adaptive_coordinator import run_initial_routed
from adaptive_routing import (
    RoutingAction, RoutingDecision, RoutingTask, plan_after_validation,
)
from case import CanonicalCaseState
from coordinator import (
    AgentRunner, InvestigationBudget, InvestigationOutcome,
    InvestigationRun, run_investigation,
)
from models import SpecialistFinding, SpecialistRole
from validation_agent import ValidationResult, validate_investigation

ValidatorRunner = Callable[[str, CanonicalCaseState], Awaitable[ValidationResult]]
# This runner MUST consume the task objective, not simply rerun a generic agent.
TaskRunner = Callable[
    [str, CanonicalCaseState, RoutingTask], Awaitable[SpecialistFinding]
]
# Trusted orchestration policy establishes whether fresh investigation is useful.
FollowupPolicy = Callable[[CanonicalCaseState, ValidationResult], bool]


@dataclass(frozen=True)
class ValidatedInvestigation:
    state: CanonicalCaseState
    next_decision: RoutingDecision
    validation: ValidationResult | None
    investigations: tuple[InvestigationRun, ...]
    followups_used: int


def _stop(
    state: CanonicalCaseState,
    message: str,
    history: list[InvestigationRun],
    *,
    validation: ValidationResult | None = None,
    followups_used: int = 0,
) -> ValidatedInvestigation:
    return ValidatedInvestigation(
        state=state,
        next_decision=RoutingDecision(RoutingAction.HITL_REQUIRED, (), message),
        validation=validation,
        investigations=tuple(history),
        followups_used=followups_used,
    )


async def run_validated_routed(
    dossier_id: str,
    state: CanonicalCaseState,
    *,
    coverage_runner: AgentRunner | None = None,
    evidence_runner: AgentRunner | None = None,
    validator: ValidatorRunner | None = None,
    followup_policy: FollowupPolicy | None = None,
    task_runner: TaskRunner | None = None,
    budget: InvestigationBudget | None = None,
) -> ValidatedInvestigation:
    """Select specialists -> validate -> optional one targeted follow-up -> validate.

    Without *both* a trusted followup_policy and a task-aware task_runner,
    reinvestigation cannot execute. This is intentional and fail-closed.
    A single shared wall-clock budget applies across the entire invocation.
    """
    limits = budget if budget is not None else InvestigationBudget()
    deadline = monotonic() + limits.total_seconds
    history: list[InvestigationRun] = []
    current = state
    check = validator if validator is not None else validate_investigation

    def remaining() -> float:
        return deadline - monotonic()

    def stage_budget() -> InvestigationBudget:
        left = remaining()
        if left <= 0:
            raise TimeoutError("Investigation-wide budget exhausted")
        return InvestigationBudget(
            total_seconds=left, per_agent_seconds=limits.per_agent_seconds
        )

    async def validate(snapshot: CanonicalCaseState) -> ValidationResult:
        left = remaining()
        if left <= 0:
            raise TimeoutError("No time remaining for independent validation")
        raw = await asyncio.wait_for(
            check(dossier_id, snapshot), timeout=left
        )
        # Never trust an arbitrary object as a validation result.
        if not isinstance(raw, ValidationResult):
            raw = ValidationResult.model_validate(raw)
        return raw

    # First round: run only the missing initial specialists.
    try:
        first = await run_initial_routed(
            dossier_id, current,
            coverage_runner=coverage_runner,
            evidence_runner=evidence_runner,
            budget=stage_budget(),
        )
    except TimeoutError:
        return _stop(current, "Investigation budget exhausted", history)

    current = first.state
    if first.investigation is not None:
        history.append(first.investigation)
    if first.next_decision.action != RoutingAction.RUN_VALIDATION:
        # Includes tool failure, invalid finding, stale state and HITL plan.
        return ValidatedInvestigation(
            current, first.next_decision, None, tuple(history), 0
        )

    try:
        review = await validate(current)
    except TimeoutError:
        return _stop(current, "Independent validation exceeded time budget", history)
    except Exception as exc:
        return _stop(
            current, f"Independent validation failed: {type(exc).__name__}", history
        )

    # The follow-up policy is separate from the LLM and defaults to NO.
    possible = False
    if review.outcome == "REINVESTIGATE" and task_runner is not None:
        try:
            possible = bool(followup_policy(current, review)) if followup_policy else False
        except Exception as exc:
            return _stop(
                current, f"Follow-up policy failed: {type(exc).__name__}",
                history, validation=review,
            )

    decision = plan_after_validation(
        current, review,
        followups_used=0,
        targeted_followup_possible=possible,
    )
    if decision.action != RoutingAction.RUN_SPECIALISTS:
        return ValidatedInvestigation(
            current, decision, review, tuple(history), 0
        )

    # A task-aware runner is mandatory; the generic original agent runner
    # has no task argument and cannot be assumed to perform targeted work.
    if task_runner is None:
        return _stop(
            current, "Targeted investigation requires task-aware runner",
            history, validation=review,
        )

    # Check that all approved tasks are still tied to this evidence basis.
    if not decision.tasks or any(
        t.basis_case_version != current.case_version
        or t.basis_evidence_hash != current.evidence_snapshot_hash
        for t in decision.tasks
    ):
        return _stop(
            current, "Follow-up tasks have stale or missing evidence basis",
            history, validation=review,
        )

    async def evidence_followup(
        target: str, snapshot: CanonicalCaseState
    ) -> SpecialistFinding:
        task = next(t for t in decision.tasks if t.specialist == SpecialistRole.EVIDENCE)
        return await task_runner(target, snapshot, task)

    async def coverage_followup(
        target: str, snapshot: CanonicalCaseState
    ) -> SpecialistFinding:
        task = next(t for t in decision.tasks if t.specialist == SpecialistRole.COVERAGE)
        return await task_runner(target, snapshot, task)

    try:
        retry = await run_investigation(
            dossier_id, current,
            selected_roles=tuple(t.specialist for t in decision.tasks),
            evidence_runner=evidence_followup,
            coverage_runner=coverage_followup,
            budget=stage_budget(),
        )
    except TimeoutError:
        return _stop(
            current, "Investigation budget exhausted before follow-up",
            history, validation=review,
        )

    current = retry.state
    history.append(retry)
    if retry.outcome != InvestigationOutcome.PENDING_SPECIALISTS:
        return _stop(
            current,
            f"Targeted specialist failed: {retry.outcome.value}",
            history, validation=review, followups_used=1,
        )

    # The revision changed after merging follow-up findings. Revalidation
    # is mandatory: the old review is now stale.
    try:
        review = await validate(current)
    except TimeoutError:
        return _stop(
            current, "Independent revalidation exceeded time budget",
            history, followups_used=1,
        )
    except Exception as exc:
        return _stop(
            current, f"Independent revalidation failed: {type(exc).__name__}",
            history, followups_used=1,
        )

    final_decision = plan_after_validation(
        current, review,
        followups_used=1,
        targeted_followup_possible=False,
    )
    # Never automatically initiate a second follow-up in this checkpoint.
    if final_decision.action == RoutingAction.RUN_SPECIALISTS:
        return _stop(
            current, "Follow-up limit reached", history,
            validation=review, followups_used=1,
        )
    return ValidatedInvestigation(
        current, final_decision, review, tuple(history), 1
    )
