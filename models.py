from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Reject unexpected fields and keep validated objects immutable."""
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClaimNote(StrictModel):
    id: str
    date: date
    text: str


class ClaimRecord(StrictModel):
    """The minimum claim record we need for the current step."""
    claim_id: str
    policy_id: str
    date_of_loss: date
    claim_status: str
    reported_cause: str
    submitted_amount: str = Field(pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$")
    case_version: int = Field(ge=0)
    assigned_adjuster: str
    as_of_date: date
    notes: tuple[ClaimNote, ...] = ()


class SpecialistRole(StrEnum):
    EVIDENCE = "evidence"
    COVERAGE = "coverage"
    VALUATION = "valuation"


class SpecialistStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTING = "CONFLICTING"
    CONDITIONAL = "CONDITIONAL"
    MULTIPLE_OPTIONS = "MULTIPLE_OPTIONS"
    SKIPPED_DEPENDENCY = "SKIPPED_DEPENDENCY"


class SpecialistFinding(StrictModel):
    """A typed result returned by one specialist agent."""
    specialist: SpecialistRole
    status: SpecialistStatus

    # The exact case/evidence basis the specialist used.
    basis_case_version: int = Field(ge=0)
    basis_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    findings: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()

class SubmissionItem(StrictModel):
    """An amount submitted to the claim before LossTrace evaluates it."""
    item_id: str
    economic_event_id: str
    description: str
    submitted_amount: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$"
    )


class LossComponent(StrictModel):
    """An amount LossTrace has determined is supportable."""
    component_id: str
    description: str
    category: str
    supported_amount: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$"
    )

class ActionIntent(StrictModel):
    """A proposed action that has not been executed yet."""

    action_type: str
    claim_id: str
    amount: str = Field(
        pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$"
    )

class AuthorizationGrant(StrictModel):
    """Approval for one exact proposed action."""

    grant_id: str
    action_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    approved_by: str
    expires_at: datetime
    used: bool = False