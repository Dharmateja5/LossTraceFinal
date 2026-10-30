from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from models import ClaimRecord, SpecialistFinding, SpecialistRole
from state_machine import CaseState, validate_transition


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceArtifact(StrictModel):
    """Runtime-safe metadata for one evidence artifact."""
    artifact_id: str
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_class: str


class CanonicalCaseState(StrictModel):
    """The one authoritative in-memory state for a claim."""

    claim_id: str

    # Changes only when the investigation/evidence basis changes.
    case_version: int

    # Changes on every successful state update.
    revision: int

    workflow_state: CaseState
    evidence_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    specialist_findings: tuple[SpecialistFinding, ...] = ()
    conflicts: tuple[str, ...] = ()
    unresolved_questions: tuple[str, ...] = ()

    updated_at: datetime


def _digest(value: Any) -> str:
    """Stable SHA-256 of JSON content."""
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_evidence_snapshot(artifacts: Iterable[EvidenceArtifact]) -> str:
    """Create one stable fingerprint for the current evidence set."""
    ordered = sorted(
        (artifact.model_dump(mode="json") for artifact in artifacts),
        key=lambda item: (item["artifact_id"], item["path"]),
    )
    return _digest(ordered)


def build_initial_case(
    claim: ClaimRecord,
    artifacts: Iterable[EvidenceArtifact],
) -> CanonicalCaseState:
    """Create the first authoritative state for a claim."""
    return CanonicalCaseState(
        claim_id=claim.claim_id,
        case_version=claim.case_version,
        revision=0,
        workflow_state=CaseState.INVESTIGATION,
        evidence_snapshot_hash=compute_evidence_snapshot(artifacts),
        updated_at=datetime.now(timezone.utc),
    )


def merge_finding(
    state: CanonicalCaseState,
    finding: SpecialistFinding,
    *,
    expected_revision: int,
) -> tuple[str, CanonicalCaseState]:
    """Merge a specialist result only if it is based on current evidence."""

    # Optimistic concurrency guard.
    if expected_revision != state.revision:
        raise ValueError(
            f"Revision changed: expected {expected_revision}, "
            f"current {state.revision}"
        )

    # Staleness guard.
    if (
        finding.basis_case_version != state.case_version
        or finding.basis_evidence_hash != state.evidence_snapshot_hash
    ):
        return "STALE_FINDING", state

    # Keep only the latest finding from each specialist.
    findings_by_role = {
        existing.specialist: existing
        for existing in state.specialist_findings
    }
    findings_by_role[finding.specialist] = finding

    new_state = state.model_copy(
        update={
            "revision": state.revision + 1,
            "specialist_findings": tuple(
                findings_by_role[role]
                for role in sorted(findings_by_role, key=lambda r: r.value)
            ),
            "conflicts": tuple(
                dict.fromkeys([*state.conflicts, *finding.conflicts])
            ),
            "unresolved_questions": tuple(
                dict.fromkeys([
                    *state.unresolved_questions,
                    *finding.unresolved_questions,
                ])
            ),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    return "ACCEPTED", new_state


def rebase_evidence(
    state: CanonicalCaseState,
    artifacts: Iterable[EvidenceArtifact],
    *,
    expected_revision: int,
) -> CanonicalCaseState:
    """Move to a new case version when the evidence set really changes."""

    if expected_revision != state.revision:
        raise ValueError(
            f"Revision changed: expected {expected_revision}, "
            f"current {state.revision}"
        )

    new_hash = compute_evidence_snapshot(artifacts)

    if new_hash == state.evidence_snapshot_hash:
        return state

    return state.model_copy(
        update={
            "case_version": state.case_version + 1,
            "revision": state.revision + 1,
            "workflow_state": CaseState.INVESTIGATION,
            "evidence_snapshot_hash": new_hash,

            # Old findings were based on old evidence.
            "specialist_findings": (),
            "conflicts": (),
            "unresolved_questions": (),

            "updated_at": datetime.now(timezone.utc),
        }
    )


def transition_case(
    state: CanonicalCaseState,
    target: CaseState,
    *,
    expected_revision: int,
) -> CanonicalCaseState:
    """Move the case through the deterministic lifecycle."""

    if expected_revision != state.revision:
        raise ValueError(
            f"Revision changed: expected {expected_revision}, "
            f"current {state.revision}"
        )

    validate_transition(state.workflow_state, target)

    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "workflow_state": target,
            "updated_at": datetime.now(timezone.utc),
        }
    )
