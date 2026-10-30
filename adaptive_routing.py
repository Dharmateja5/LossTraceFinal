"""Checkpoint 4B: pure, rule-based routing for LossTrace.

Planning only: no agent calls, state mutations, HITL transitions, or writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from case import CanonicalCaseState
from models import SpecialistRole, SpecialistStatus
from state_machine import CaseState

if TYPE_CHECKING:
    from validation_agent import ValidationResult


class RoutingAction(StrEnum):
    RUN_SPECIALISTS = "RUN_SPECIALISTS"
    RUN_VALIDATION = "RUN_VALIDATION"
    NEXT_VALUATION = "NEXT_VALUATION"
    HITL_REQUIRED = "HITL_REQUIRED"


@dataclass(frozen=True)
class RoutingTask:
    specialist: SpecialistRole
    objective: str
    basis_case_version: int
    basis_evidence_hash: str


@dataclass(frozen=True)
class RoutingDecision:
    action: RoutingAction
    tasks: tuple[RoutingTask, ...]
    reason: str


def _task(state: CanonicalCaseState, role: SpecialistRole,
          objective: str) -> RoutingTask:
    return RoutingTask(role, objective, state.case_version,
                       state.evidence_snapshot_hash)


def plan_initial(state: CanonicalCaseState) -> RoutingDecision:
    """Choose missing Coverage/Evidence work; keep the existing two-agent gate."""
    if state.workflow_state != CaseState.INVESTIGATION:
        raise ValueError("Routing requires INVESTIGATION state")
    for finding in state.specialist_findings:
        if (finding.basis_case_version != state.case_version or
                finding.basis_evidence_hash != state.evidence_snapshot_hash):
            return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                                   "Stale finding present; require evidence rebase/review")

    existing = {finding.specialist for finding in state.specialist_findings}
    tasks = tuple(
        _task(state, role, objective)
        for role, objective in (
            (SpecialistRole.COVERAGE, "Investigate applicable policy terms"),
            (SpecialistRole.EVIDENCE, "Investigate source-backed loss evidence"),
        )
        if role not in existing
    )
    if tasks:
        return RoutingDecision(RoutingAction.RUN_SPECIALISTS, tasks,
                               "Required specialist findings missing")
    return RoutingDecision(RoutingAction.RUN_VALIDATION, (),
                           "Evidence and Coverage findings are present")


def plan_after_validation(
    state: CanonicalCaseState,
    result: ValidationResult,
    *,
    followups_used: int = 0,
    targeted_followup_possible: bool = False,
) -> RoutingDecision:
    """Plan a follow-up; never convert provisional support into authorization.

    targeted_followup_possible must be established by trusted workflow rules,
    not merely by the agent requesting another attempt.
    """
    if state.workflow_state != CaseState.INVESTIGATION:
        raise ValueError("Routing requires INVESTIGATION state")
    if followups_used < 0:
        raise ValueError("followups_used cannot be negative")

    # A validator result tied to an older revision is not current authority.
    if (result.basis_case_version != state.case_version or
            result.basis_evidence_hash != state.evidence_snapshot_hash or
            result.basis_revision != state.revision):
        return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                               "Validation result is stale; do not advance")

    if result.outcome == "HITL_REQUIRED":
        return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                               "Validator requires human review")

    if result.outcome == "PROVISIONAL_SUPPORT":
        required = {SpecialistRole.COVERAGE, SpecialistRole.EVIDENCE}
        findings = {f.specialist: f for f in state.specialist_findings}
        if not required <= findings.keys() or any(
            findings[role].status != SpecialistStatus.SUFFICIENT
            for role in required
        ):
            return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                                   "Provisional support does not resolve specialist insufficiency")
        return RoutingDecision(RoutingAction.NEXT_VALUATION, (),
                               "Evidence/Coverage checks passed; Valuation is still required")

    if result.outcome != "REINVESTIGATE":
        return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                               "Unknown validator outcome; fail closed")

    if followups_used >= 1 or not targeted_followup_possible:
        return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                               "No justified follow-up or investigation budget exhausted")

    roles = set()
    for check in result.semantic_checks:
        if check.verdict == "UNSUPPORTED":
            prefix = check.key.partition(":")[0]
            if prefix in (SpecialistRole.EVIDENCE.value, SpecialistRole.COVERAGE.value):
                roles.add(SpecialistRole(prefix))

    if result.relationship_reported != result.relationship_validated:
        if result.relationship_validated is not None:
            roles.add(SpecialistRole.EVIDENCE)

    if not roles:
        return RoutingDecision(RoutingAction.HITL_REQUIRED, (),
                               "No unambiguous specialist target; human triage required")

    tasks = tuple(
        _task(state, role, "Reassess unsupported interpretation with additional evidence")
        for role in (SpecialistRole.EVIDENCE, SpecialistRole.COVERAGE)
        if role in roles
    )
    return RoutingDecision(RoutingAction.RUN_SPECIALISTS, tasks,
                           "One targeted follow-up permitted")
