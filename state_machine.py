from enum import StrEnum


class CaseState(StrEnum):
    INVESTIGATION = "INVESTIGATION"
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    HITL_REQUIRED = "HITL_REQUIRED"
    NO_ACTION = "NO_ACTION"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    UNKNOWN_EXECUTION = "UNKNOWN_EXECUTION"
    RECONCILING = "RECONCILING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


ALLOWED_TRANSITIONS = {
    CaseState.INVESTIGATION: {
        CaseState.RECOMMENDATION_READY,
        CaseState.HITL_REQUIRED,
    },
    CaseState.RECOMMENDATION_READY: {
        CaseState.POLICY_EVALUATED,
    },
    CaseState.POLICY_EVALUATED: {
        CaseState.HITL_REQUIRED,
        CaseState.EXECUTION_PENDING,
        CaseState.NO_ACTION,
    },
    CaseState.HITL_REQUIRED: set(),
    CaseState.NO_ACTION: set(),
    CaseState.EXECUTION_PENDING: {
        CaseState.EXECUTING,
    },
    CaseState.EXECUTING: {
        CaseState.EXECUTED,
        CaseState.UNKNOWN_EXECUTION,
        CaseState.RETRYABLE_FAILURE,
    },
    CaseState.EXECUTED: {
        CaseState.VERIFICATION_PENDING,
    },
    CaseState.UNKNOWN_EXECUTION: {
        CaseState.RECONCILING,
    },
    CaseState.RECONCILING: {
        CaseState.EXECUTED,
        CaseState.HITL_REQUIRED,
        CaseState.RETRYABLE_FAILURE,
    },
    CaseState.RETRYABLE_FAILURE: {
        CaseState.EXECUTION_PENDING,
        CaseState.HITL_REQUIRED,
    },
    CaseState.VERIFICATION_PENDING: {
        CaseState.VERIFIED,
        CaseState.VERIFICATION_FAILED,
    },
    CaseState.VERIFIED: set(),
    CaseState.VERIFICATION_FAILED: {
        CaseState.HITL_REQUIRED,
    },
}


def validate_transition(current: CaseState, target: CaseState) -> None:
    """Raise an error if the case is not allowed to move to target."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid transition: {current} -> {target}")
