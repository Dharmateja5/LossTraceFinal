"""LossTrace hackathon MVP: investigation, validation and sandbox controls.

Runs the real Evidence/Coverage agents and independent Validation Agent through
an adaptive investigation coordinator, then records SQLite checkpoints/audit.
The financial stage accepts only independently gated investigations and explicit
human-attested, page-cited scope decisions. It uses an action-bound, single-use
CLI authorization and an idempotent LOCAL SQLite sandbox ledger.

This is NOT a production claims/payment system: no authenticated human identity,
real payment rail, autonomous Valuation Agent, or interrupted-agent resume.
A verified quotation proves that words appear in a registered source, NOT that
the described loss is causally attributable, eligible, or independently valued.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from decimal import Decimal
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from adaptive_routing import RoutingAction
from case import CanonicalCaseState, EvidenceArtifact, build_initial_case
from coordinator import AgentRunner, InvestigationBudget
from models import ClaimRecord
from validated_coordinator import ValidatorRunner, run_validated_routed

if TYPE_CHECKING:
    from validated_coordinator import ValidatedInvestigation


class WorkflowAlreadyExists(RuntimeError):
    """Block duplicate work until a controlled resume/rebase path exists."""


class WorkflowConflict(RuntimeError):
    """Checkpoint changed during this run, or the workflow run is no longer active."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkflowReceipt:
    dossier_id: str
    claim_id: str
    status: str
    next_action: str
    reason: str
    case_version: int
    revision: int
    evidence_hash: str
    run_id: str
    source_checks: int | None
    followups_used: int


class WorkflowStore:
    """SQLite checkpoint + per-claim tamper-evident audit in one transaction.

    One writer obtains BEGIN IMMEDIATE and commit uses a run-ID/revision CAS.
    This is not a distributed workflow engine and does not checkpoint each
    intermediary specialist merge yet.
    """

    def __init__(self, path: str | Path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(p), timeout=10)
        self.connection.execute("PRAGMA busy_timeout = 10000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS investigation_workflows (
                dossier_id TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                case_version INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                evidence_hash TEXT NOT NULL,
                state_json TEXT NOT NULL,
                status TEXT NOT NULL,
                next_action TEXT NOT NULL,
                reason TEXT NOT NULL,
                validation_json TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS investigation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                previous_hash TEXT,
                event_hash TEXT NOT NULL
            )
        """)
        self.connection.commit()
        _init_financial_tables(self)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "WorkflowStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _event(self, claim_id: str, event_type: str, details: dict) -> None:
        previous_row = self.connection.execute(
            "SELECT event_hash FROM investigation_audit WHERE claim_id=? ORDER BY id DESC LIMIT 1",
            (claim_id,),
        ).fetchone()
        previous = previous_row[0] if previous_row else None
        timestamp = _now()
        event = dict(claim_id=claim_id, event_type=event_type, details=details,
                     timestamp=timestamp, previous_hash=previous)
        digest = _sha256(_json(event))
        self.connection.execute("""
            INSERT INTO investigation_audit
                (claim_id, event_type, details_json, timestamp, previous_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (claim_id, event_type, _json(details), timestamp, previous, digest))

    def _checkpoint_binding(self, dossier_id: str) -> dict:
        """Bind every persisted checkpoint field, including routing decisions."""
        return {"dossier_id": dossier_id,
                "checkpoint_sha256": _sha256(_json(self.get(dossier_id)))}

    def begin(self, dossier_id: str, state: CanonicalCaseState) -> str:
        """Register a new run. Never silently replay a completed or interrupted one."""
        run_id = str(uuid4())
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT status FROM investigation_workflows WHERE dossier_id=? OR claim_id=?",
                (dossier_id, state.claim_id),
            ).fetchone()
            if existing:
                raise WorkflowAlreadyExists(
                    f"Claim/dossier already registered (status={existing[0]}). "
                    "Inspect it with 'status'; do not rerun automatically."
                )
            self.connection.execute("""
                INSERT INTO investigation_workflows
                  (dossier_id, claim_id, run_id, case_version, revision,
                   evidence_hash, state_json, status, next_action, reason,
                   validation_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'IN_PROGRESS', 'INVESTIGATE',
                        'Investigation started', NULL, ?)
            """, (dossier_id, state.claim_id, run_id, state.case_version,
                  state.revision, state.evidence_snapshot_hash,
                  state.model_dump_json(), _now()))
            self._event(state.claim_id, "INVESTIGATION_STARTED", {
                **self._checkpoint_binding(dossier_id),
                "run_id": run_id, "case_version": state.case_version,
                "revision": state.revision, "evidence_hash": state.evidence_snapshot_hash,
            })
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return run_id

    def finish(self, dossier_id: str, run_id: str, state: CanonicalCaseState,
               outcome: "ValidatedInvestigation", initial_revision: int) -> WorkflowReceipt:
        decision = outcome.next_decision
        if decision.action == RoutingAction.NEXT_VALUATION:
            status = "AWAITING_VALUATION"
        elif decision.action == RoutingAction.HITL_REQUIRED:
            status = "HITL_REQUIRED"
        else:
            # There must be no unhandled automatic continuation or payment.
            status = "BLOCKED_UNSUPPORTED_ROUTE"
        validation_json = (outcome.validation.model_dump_json()
                           if outcome.validation is not None else None)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("""
                SELECT claim_id, case_version, revision, evidence_hash
                FROM investigation_workflows
                WHERE dossier_id=? AND run_id=? AND status='IN_PROGRESS'
            """, (dossier_id, run_id)).fetchone()
            if not row or row[0] != state.claim_id or row[1] != state.case_version or \
                    row[2] != initial_revision or row[3] != state.evidence_snapshot_hash:
                raise WorkflowConflict("Run state or evidence basis changed before commit")
            if not self.verify_audit(state.claim_id)[0]:
                raise WorkflowConflict("Investigation checkpoint audit integrity failed")
            count = self.connection.execute("""
                UPDATE investigation_workflows
                SET revision=?, state_json=?, status=?, next_action=?, reason=?,
                    validation_json=?, updated_at=?
                WHERE dossier_id=? AND run_id=? AND status='IN_PROGRESS' AND revision=?
            """, (state.revision, state.model_dump_json(), status,
                  decision.action.value, decision.reason, validation_json,
                  _now(), dossier_id, run_id, initial_revision)).rowcount
            if count != 1:
                raise WorkflowConflict("Concurrent checkpoint update detected")
            checks = outcome.validation.source_checks if outcome.validation else None
            details = {
                **self._checkpoint_binding(dossier_id),
                "run_id": run_id, "status": status, "next_action": decision.action.value,
                "reason": decision.reason, "case_version": state.case_version,
                "revision": state.revision, "evidence_hash": state.evidence_snapshot_hash,
                "state_sha256": _sha256(state.model_dump_json()),
                "validation_sha256": _sha256(validation_json) if validation_json else None,
                "source_checks": checks, "followups_used": outcome.followups_used,
                "rounds": [
                    {"outcome": r.outcome.value,
                     "steps": [{"role": s.specialist.value, "outcome": s.outcome.value,
                                "detail": s.detail} for s in r.steps]}
                    for r in outcome.investigations
                ],
            }
            self._event(state.claim_id, "INVESTIGATION_COMPLETED", details)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return WorkflowReceipt(dossier_id, state.claim_id, status,
                               decision.action.value, decision.reason,
                               state.case_version, state.revision,
                               state.evidence_snapshot_hash, run_id,
                               checks, outcome.followups_used)

    def fail(self, dossier_id: str, run_id: str, error_type: str) -> None:
        """Unexpected exception: preserve the checkpoint and block all advancement."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("""
                SELECT claim_id FROM investigation_workflows
                WHERE dossier_id=? AND run_id=? AND status='IN_PROGRESS'
            """, (dossier_id, run_id)).fetchone()
            if row is None:
                raise WorkflowConflict("Cannot fail a run that is no longer active")
            if not self.verify_audit(row[0])[0]:
                raise WorkflowConflict("Investigation checkpoint audit integrity failed")
            self.connection.execute("""
                UPDATE investigation_workflows
                SET status='BLOCKED_FAILURE', next_action='HITL_REQUIRED',
                    reason=?, updated_at=?
                WHERE dossier_id=? AND run_id=? AND status='IN_PROGRESS'
            """, (f"Unexpected workflow error: {error_type}", _now(), dossier_id, run_id))
            self._event(row[0], "INVESTIGATION_FAILED", {
                **self._checkpoint_binding(dossier_id),
                "run_id": run_id, "error_type": error_type,
            })
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def get(self, dossier_id: str) -> dict | None:
        row = self.connection.execute("""
            SELECT dossier_id, claim_id, run_id, case_version, revision,
                   evidence_hash, status, next_action, reason, state_json,
                   validation_json, updated_at
            FROM investigation_workflows WHERE dossier_id=?
        """, (dossier_id,)).fetchone()
        if not row:
            return None
        keys = ("dossier_id", "claim_id", "run_id", "case_version", "revision",
                "evidence_hash", "status", "next_action", "reason", "state_json",
                "validation_json", "updated_at")
        return dict(zip(keys, row))

    def verify_audit(self, claim_id: str) -> tuple[bool, int]:
        rows = self.connection.execute("""
            SELECT event_type, details_json, timestamp, previous_hash, event_hash
            FROM investigation_audit WHERE claim_id=? ORDER BY id
        """, (claim_id,)).fetchall()
        previous = None
        latest_checkpoint_event = None
        latest_financial_event = None
        for kind, details_json, timestamp, expected_previous, digest in rows:
            if expected_previous != previous:
                return False, len(rows)
            try:
                details = json.loads(details_json)
            except (ValueError, TypeError):
                return False, len(rows)
            check = _sha256(_json(dict(
                claim_id=claim_id, event_type=kind, details=details,
                timestamp=timestamp, previous_hash=previous,
            )))
            if check != digest:
                return False, len(rows)
            if kind in {"INVESTIGATION_STARTED", "INVESTIGATION_COMPLETED", "INVESTIGATION_FAILED"}:
                latest_checkpoint_event = details
            if "finance_snapshot_hash" in details:
                latest_financial_event = details
            previous = digest
        # Old events without the complete binding remain readable but cannot
        # attest integrity or authorize new money. Never re-sign old history.
        if latest_checkpoint_event is None:
            return False, len(rows)
        dossier_id = latest_checkpoint_event.get("dossier_id")
        if not isinstance(dossier_id, str):
            return False, len(rows)
        checkpoint = self.get(dossier_id)
        if (checkpoint is None or checkpoint["claim_id"] != claim_id
                or latest_checkpoint_event.get("checkpoint_sha256") != _sha256(_json(checkpoint))):
            return False, len(rows)
        if latest_financial_event is not None:
            dossier_id = latest_financial_event.get("dossier_id")
            if not isinstance(dossier_id, str):
                return False, len(rows)
            if latest_financial_event.get("finance_snapshot_hash") != _finance_snapshot_hash(self, dossier_id):
                return False, len(rows)
            if latest_financial_event.get("payment_snapshot_hash") != _payment_snapshot_hash(self, dossier_id):
                return False, len(rows)
        elif self.connection.execute(
            "SELECT 1 FROM financial_workflows WHERE claim_id=?", (claim_id,)
        ).fetchone():
            return False, len(rows)
        return bool(rows), len(rows)

def load_case_from_runtime(dossier_id: str, *, runtime_root: str | Path = "data/application_runtime") -> tuple[CanonicalCaseState, int]:
    """Use trusted runtime manifest/claim inputs, not benchmark answer material."""
    if not re.fullmatch(r"d-[0-9a-f]{12}", dossier_id):
        raise ValueError("Invalid dossier ID")
    root = Path(runtime_root)
    from gateway import RuntimeGateway  # Lazy for status/audit and injected tests.
    claim = ClaimRecord.model_validate(RuntimeGateway(str(root)).get_claim_record(dossier_id))
    manifest = root / "manifests" / f"{dossier_id}.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = payload["artifacts"]
    artifacts = [EvidenceArtifact(
        artifact_id=x["artifact_id"], path=x["path"],
        sha256=x["sha256"], source_class=x["source_class"]
    ) for x in entries]
    if not artifacts or len({a.artifact_id for a in artifacts}) != len(artifacts):
        raise ValueError("Missing or duplicate evidence artifact IDs")
    return build_initial_case(claim, artifacts), len(artifacts)


async def run_case(
    dossier_id: str,
    initial_state: CanonicalCaseState,
    store: WorkflowStore,
    *,
    coverage_runner: AgentRunner | None = None,
    evidence_runner: AgentRunner | None = None,
    validator: ValidatorRunner | None = None,
    budget: InvestigationBudget | None = None,
) -> WorkflowReceipt:
    """Run the integrated investigation and persist its controlled handoff.

    No generic reinvestigation: follow-up policy and task-aware runner are not
    available in the live app yet. Fail closed to HITL instead.
    """
    run_id = store.begin(dossier_id, initial_state)
    try:
        outcome = await run_validated_routed(
            dossier_id, initial_state,
            coverage_runner=coverage_runner, evidence_runner=evidence_runner,
            validator=validator, budget=budget,
        )
        # No external state change between starting and finishing is assumed;
        # the DB CAS prevents competing checkpoint writers, not out-of-band edits.
        return store.finish(dossier_id, run_id, outcome.state, outcome, initial_state.revision)
    except Exception as exc:
        store.fail(dossier_id, run_id, type(exc).__name__)
        raise


# Integrated Step 5B: read-only financial scope review for a persisted case.
# No new financial_scope.py module is required.

@dataclass(frozen=True)
class FinancialScopeLine:
    line_id: str
    description: str
    submitted_amount: str
    independent_price_opinion: str
    price_candidate: str
    scope_decision: str
    questions: tuple[str, ...]
    price_sources: tuple[str, str]


@dataclass(frozen=True)
class FinancialScopeQueue:
    claim_id: str
    dossier_id: str
    case_version: int
    revision: int
    evidence_hash: str
    investigation_status: str
    validation_outcome: str
    next_action: str
    price_opinion_base: str
    conditional_amount: str
    lines: tuple[FinancialScopeLine, ...]
    unresolved_questions: tuple[str, ...]
    validation_reasons: tuple[str, ...]
    financial_action_permitted: bool
    notes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def prepare_financial_scope_queue(
    dossier_id: str,
    record: dict,
    *,
    audit_integrity: bool,
    runtime_root: str | Path = "data/application_runtime",
) -> FinancialScopeQueue:
    """Build a read-only list of issues requiring component-level scope review.

    Unlike a gated financial preview, this intentionally supports the HITL
    branch: reviewers need to see pricing evidence to resolve a blocked claim.
    It NEVER unlocks the financial-action path.
    """
    from financial_stage import FinancialGateError, build_price_review

    if not audit_integrity:
        raise FinancialGateError("Audit integrity failed; scope review blocked")
    if record.get("dossier_id") != dossier_id:
        raise FinancialGateError("Dossier does not match checkpoint")
    status = record.get("status")
    if status not in {"HITL_REQUIRED", "AWAITING_VALUATION"}:
        raise FinancialGateError("Workflow is not eligible for a scope-review queue")
    try:
        state = CanonicalCaseState.model_validate_json(record["state_json"])
        validation = json.loads(record["validation_json"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FinancialGateError("Stored state or validation missing/invalid") from exc
    if (
        record.get("claim_id") != state.claim_id
        or record.get("case_version") != state.case_version
        or record.get("revision") != state.revision
        or record.get("evidence_hash") != state.evidence_snapshot_hash
        or validation.get("basis_case_version") != state.case_version
        or validation.get("basis_evidence_hash") != state.evidence_snapshot_hash
        or validation.get("basis_revision") != state.revision
    ):
        raise FinancialGateError("Case, evidence, or validator basis mismatch")
    outcome = validation.get("outcome")
    if status == "HITL_REQUIRED":
        if record.get("next_action") != "HITL_REQUIRED" or outcome not in {
            "HITL_REQUIRED", "REINVESTIGATE"
        }:
            raise FinancialGateError("HITL checkpoint and validation are inconsistent")
        action = "HUMAN_SCOPE_AND_CAUSATION_REVIEW"
    else:
        if record.get("next_action") != "NEXT_VALUATION" or outcome != "PROVISIONAL_SUPPORT":
            raise FinancialGateError("Financial handoff checkpoint and validation are inconsistent")
        action = "COMPONENT_SCOPE_REVIEW"

    # Reuse the existing SHA-256, manifest, PDF, policy and arithmetic gates.
    opinion = build_price_review(
        dossier_id, state, runtime_root=runtime_root
    )
    if opinion.financial_action_permitted:
        raise FinancialGateError("Read-only price review unexpectedly authorized action")

    pending = tuple(dict.fromkeys([
        *state.unresolved_questions,
        *(q for f in state.specialist_findings for q in f.unresolved_questions),
    ]))
    # All lines stay unapproved: a price comparison is not proof of causation,
    # coverage, scope quantity, or absence of duplicate economic events.
    lines = tuple(
        FinancialScopeLine(
            line_id=line.line_id,
            description=line.description,
            submitted_amount=line.submitted,
            independent_price_opinion=line.reviewed_opinion,
            price_candidate=line.capped_candidate,
            scope_decision="UNDETERMINED",
            questions=(
                "Is this entire repair scope attributable to the reported occurrence?",
                "Is the scope supported by verified damage and applicable coverage?",
                "Is this economic event already billed, paid, or included elsewhere?",
            ),
            price_sources=(opinion.estimate_artifact_id, opinion.review_artifact_id),
        )
        for line in opinion.lines
    )
    return FinancialScopeQueue(
        claim_id=state.claim_id,
        dossier_id=dossier_id,
        case_version=state.case_version,
        revision=state.revision,
        evidence_hash=state.evidence_snapshot_hash,
        investigation_status=status,
        validation_outcome=outcome,
        next_action=action,
        price_opinion_base=opinion.price_opinion_base,
        conditional_amount=opinion.conditional_amount,
        lines=lines,
        unresolved_questions=pending,
        validation_reasons=tuple(str(x) for x in (validation.get("findings") or ())),
        financial_action_permitted=False,
        notes=(
            "Read-only queue: every line has UNDETERMINED scope support.",
            "Cost review verifies a price opinion, not damage causation or coverage.",
            "Conditional amount is NOT a settlement recommendation.",
            "A separate verified scope decision, policy/HITL review, and authorization are required before execution.",
        ),
    )



# ──────────────────────────────────────────────────────────────────────────────
# Hackathon MVP Step 5B–5D: real, fail-closed sandbox financial workflow.
# This code deliberately lives in the existing application, not a new service.
# ──────────────────────────────────────────────────────────────────────────────

class FinancialWorkflowError(RuntimeError):
    """Financial control stopped an unsafe or incomplete action."""


def _decimal_money(value: object) -> "Decimal":
    from decimal import Decimal, InvalidOperation
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.[0-9]{2}", value):
        raise FinancialWorkflowError("Amounts must be nonnegative USD strings with exactly two decimals")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise FinancialWorkflowError("Invalid USD amount") from exc
    if not amount.is_finite():
        raise FinancialWorkflowError("Nonfinite USD amount")
    return amount


def _usd(amount: "Decimal") -> str:
    return f"{amount:.2f}"


def _case_financial_fingerprint(preview: object) -> str:
    """Bind a proposal to the exact source-backed price opinion, not just an amount."""
    return _sha256(_json(preview.to_dict()))


def _check_support_sources(
    dossier_id: str,
    state: CanonicalCaseState,
    passages: list[dict],
    runtime_root: str | Path,
) -> None:
    """Recheck exact cited page text in registered source artifacts.

    A passage match confirms provenance, not semantics, covered scope, or
    damage attribution. The reviewer explicitly attests to those judgments.
    This intentionally supports only extractable PDFs in the hackathon MVP;
    images/scans without text must be escalated to HITL.
    """
    if not passages:
        raise FinancialWorkflowError("Supported items require source page quotations")
    root = Path(runtime_root).resolve()
    manifest_path = root / "manifests" / f"{dossier_id}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = [EvidenceArtifact(
        artifact_id=a["artifact_id"], path=a["path"],
        sha256=a["sha256"], source_class=a["source_class"]
    ) for a in manifest["artifacts"]]
    from case import compute_evidence_snapshot
    if compute_evidence_snapshot(artifacts) != state.evidence_snapshot_hash:
        raise FinancialWorkflowError("Source manifest no longer matches investigation snapshot")
    found = {a.artifact_id: a for a in artifacts}
    if len(found) != len(artifacts):
        raise FinancialWorkflowError("Duplicate artifact IDs in source manifest")
    dossier_boundary = (root / "world" / dossier_id).resolve()
    if not dossier_boundary.is_relative_to(root):
        raise FinancialWorkflowError("Invalid dossier boundary")

    import pymupdf
    for passage in passages:
        if not isinstance(passage, dict) or set(passage) != {"artifact_id", "page", "excerpt"}:
            raise FinancialWorkflowError("Support passage needs artifact_id, page and excerpt")
        artifact_id = passage["artifact_id"]
        page_number = passage["page"]
        excerpt = passage["excerpt"]
        if (not isinstance(artifact_id, str) or not isinstance(page_number, int)
                or isinstance(page_number, bool) or page_number < 1
                or not isinstance(excerpt, str) or not 40 <= len(excerpt.strip()) <= 600):
            raise FinancialWorkflowError("Invalid support passage/page/excerpt")
        if artifact_id not in found:
            raise FinancialWorkflowError("Unregistered support source")
        artifact = found[artifact_id]
        if artifact.source_class in {"contractor_estimate", "independent_cost_review"}:
            raise FinancialWorkflowError("Price evidence alone cannot establish scope or causation")
        if not artifact.path.lower().endswith(".pdf"):
            raise FinancialWorkflowError("Human support passages must cite a text-readable PDF")
        path = (root / artifact.path).resolve()
        if not path.is_relative_to(dossier_boundary):
            raise FinancialWorkflowError("Support source escapes dossier boundary")
        if not path.is_file():
            raise FinancialWorkflowError("Support source missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise FinancialWorkflowError("Support source hash mismatch")
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if page_number > len(document) or len(document) > 30:
                    raise FinancialWorkflowError("Unsupported source page number/count")
                page_text = document[page_number - 1].get_text(sort=True)
        except FinancialWorkflowError:
            raise
        except Exception as exc:
            raise FinancialWorkflowError("Support PDF extraction failed") from exc
        normalized_page = " ".join(page_text.split()).casefold()
        normalized_excerpt = " ".join(excerpt.split()).casefold()
        if normalized_excerpt not in normalized_page:
            raise FinancialWorkflowError("Quoted support excerpt is not on the cited page")


def _build_financial_proposal(
    dossier_id: str, record: dict, decisions: dict,
    *, audit_integrity: bool, runtime_root: str | Path,
) -> dict:
    """5B: human scope attestations + real deterministic calculation + policy gate.

    The model never decides which components enter the authorized payment.
    """
    from decimal import Decimal
    from financial import calculate_payable
    from financial_stage import build_gated_financial_preview
    from models import ActionIntent
    from authorization import hash_action_intent
    from policy import evaluate_policy

    if not audit_integrity:
        raise FinancialWorkflowError("Audit chain is not intact")
    if not isinstance(decisions, dict) or decisions.get("attestation") is not True:
        raise FinancialWorkflowError("Explicit human scope and duplicate-check attestation required")
    reviewer = decisions.get("reviewer")
    if not isinstance(reviewer, str) or not 3 <= len(reviewer.strip()) <= 128:
        raise FinancialWorkflowError("Human reviewer identity required")
    if reviewer.lower().startswith(("agent", "nemotron", "lossTrace".lower())):
        raise FinancialWorkflowError("LLM/agent cannot attest to scope")
    state = CanonicalCaseState.model_validate_json(record["state_json"])
    # This function independently rechecks validator result, specialist sufficiency,
    # source hashes, policy dates, prior ledger usage, and audit integrity.
    preview = build_gated_financial_preview(
        dossier_id, record, audit_integrity=audit_integrity, runtime_root=runtime_root
    )
    entries = decisions.get("lines")
    if not isinstance(entries, list) or len(entries) != len(preview.lines):
        raise FinancialWorkflowError("Every reviewed repair line needs one human decision")
    by_id = {line.line_id: line for line in preview.lines}
    if len(by_id) != len(preview.lines):
        raise FinancialWorkflowError("Duplicate source price line")
    reviewed: list[dict] = []
    cited_passages: list[dict] = []
    used: set[str] = set()
    supported_base = Decimal("0.00")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("line_id"), str):
            raise FinancialWorkflowError("Invalid line decision")
        line_id = entry["line_id"]
        if line_id not in by_id or line_id in used:
            raise FinancialWorkflowError("Unknown or duplicate line decision")
        used.add(line_id)
        line = by_id[line_id]
        decision = entry.get("decision")
        amount = _decimal_money(entry.get("supported_amount"))
        rationale = entry.get("reason")
        if not isinstance(rationale, str) or len(rationale.strip()) < 15:
            raise FinancialWorkflowError("Every scope decision needs a meaningful rationale")
        sources = entry.get("support_passages", [])
        if not isinstance(sources, list):
            raise FinancialWorkflowError("Support passages must be a list")
        if decision == "SUPPORTED":
            if amount <= 0 or amount > _decimal_money(line.capped_candidate):
                raise FinancialWorkflowError("Supported amount must be positive and within independently reviewed cap")
            if not sources:
                raise FinancialWorkflowError("Supported line needs at least one artifact/page/excerpt citation")
            # Excerpt is verified against the file. Human reviewer remains
            # accountable for scope relevance and coverage/causation claims.
            cited_passages.extend(sources)
            supported_base += amount
        elif decision == "EXCLUDED":
            if amount != 0:
                raise FinancialWorkflowError("Excluded line must have zero supported amount")
            if sources:
                raise FinancialWorkflowError("Excluded line must not claim positive supporting passages")
        else:
            raise FinancialWorkflowError("Decision must be SUPPORTED or EXCLUDED")
        reviewed.append({
            "line_id": line_id,
            "decision": decision,
            "supported_amount": _usd(amount),
            "reason": rationale.strip(),
            "support_passages": sources,
            "price_cap": line.capped_candidate,
        })
    if used != set(by_id) or supported_base <= 0:
        raise FinancialWorkflowError("All lines must be resolved and at least one supported")
    _check_support_sources(dossier_id, state, cited_passages, runtime_root)
    result = calculate_payable(
        supported_loss=supported_base,
        o_and_p_rate=Decimal(preview.reviewed_o_and_p_rate),
        deductible=_decimal_money(preview.policy_deductible),
        policy_limit=_decimal_money(preview.building_limit),
        prior_payments=Decimal("0.00"),  # verified as zero by financial_stage
    )
    if result.remaining_payable <= 0:
        raise FinancialWorkflowError("No positive amount may be proposed")
    finding_statuses = [f.status for f in state.specialist_findings]
    policy = evaluate_policy(
        result.remaining_payable, _decimal_money(preview.authority_threshold),
        finding_statuses,
    )
    amount = _usd(result.remaining_payable)
    intent = ActionIntent(action_type="SANDBOX_SETTLEMENT", claim_id=state.claim_id, amount=amount)
    plan: dict = {
        "type": "HUMAN_ATTESTED_BUILDING_REPAIR_MVP",
        "dossier_id": dossier_id, "claim_id": state.claim_id,
        "run_id": record["run_id"],
        "case_version": state.case_version, "basis_revision": state.revision,
        "evidence_hash": state.evidence_snapshot_hash,
        "reviewer": reviewer.strip(),
        "reviewed_lines": sorted(reviewed, key=lambda x: x["line_id"]),
        "price_fingerprint": _case_financial_fingerprint(preview),
        "supported_building_base": _usd(supported_base),
        "o_and_p": _usd(result.o_and_p),
        "deductible": preview.policy_deductible,
        "building_limit": preview.building_limit,
        "amount": amount,
        "policy_reasons": list(policy.reasons),
        "intent_hash": hash_action_intent(intent),
        "limitations": [
            "Page-exact source quotations verify document text, not semantic relevance or causation.",
            "Human scope attestation is not an independent Valuation Agent result.",
            "Building repair scope only; excluded invoices, mold/BPP and other categories.",
            "Authorization and financial execution require separate approval.",
        ],
    }
    plan["proposal_hash"] = _sha256(_json(plan))
    return plan


def _financial_action_hash(plan: dict) -> str:
    return _sha256(_json({
        "action": "SANDBOX_SETTLEMENT",
        "claim_id": plan["claim_id"],
        "run_id": plan["run_id"],
        "case_version": plan["case_version"],
        "revision": plan["basis_revision"],
        "evidence_hash": plan["evidence_hash"],
        "proposal_hash": plan["proposal_hash"],
        "amount": plan["amount"],
        "intent_hash": plan["intent_hash"],
    }))


def _financial_record(store: WorkflowStore, dossier_id: str) -> dict | None:
    row = store.connection.execute("""
        SELECT dossier_id, claim_id, run_id, case_version, basis_revision,
               evidence_hash, status, proposal_json, proposal_hash,
               action_hash, idempotency_key, approved_by, authorized_until,
               grant_consumed, updated_at
          FROM financial_workflows WHERE dossier_id=?
    """, (dossier_id,)).fetchone()
    if row is None:
        return None
    keys = ("dossier_id", "claim_id", "run_id", "case_version", "basis_revision",
            "evidence_hash", "status", "proposal_json", "proposal_hash",
            "action_hash", "idempotency_key", "approved_by", "authorized_until",
            "grant_consumed", "updated_at")
    return dict(zip(keys, row))


def _finance_snapshot_hash(store: WorkflowStore, dossier_id: str) -> str | None:
    row = _financial_record(store, dossier_id)
    return _sha256(_json(row)) if row is not None else None


def _payment_snapshot_hash(store: WorkflowStore, dossier_id: str) -> str | None:
    row = store.connection.execute("""
        SELECT idempotency_key, dossier_id, claim_id, action_hash, amount, created_at
          FROM sandbox_payments WHERE dossier_id=?
    """, (dossier_id,)).fetchone()
    return _sha256(_json(row)) if row is not None else None


def _finance_event(store: WorkflowStore, dossier_id: str, kind: str, details: dict) -> None:
    """Append event inside caller transaction, binding exact financial and ledger rows."""
    fin = _financial_record(store, dossier_id)
    if fin is None:
        raise FinancialWorkflowError("No financial workflow")
    store._event(fin["claim_id"], kind, {
        **details, "dossier_id": dossier_id,
        "finance_snapshot_hash": _finance_snapshot_hash(store, dossier_id),
        "payment_snapshot_hash": _payment_snapshot_hash(store, dossier_id),
    })


def _check_financial_basis(store: WorkflowStore, dossier_id: str,
                           financial: dict, runtime_root: str | Path) -> dict:
    """Reverify current investigator and price evidence before authority/action."""
    from financial_stage import FinancialGateError, build_gated_financial_preview
    stored = store.get(dossier_id)
    if stored is None or stored["status"] != "AWAITING_VALUATION":
        raise FinancialWorkflowError("Investigation is not cleared for financial action")
    valid, _ = store.verify_audit(stored["claim_id"])
    if not valid:
        raise FinancialWorkflowError("Audit integrity verification failed")
    plan = json.loads(financial["proposal_json"])
    proposal_hash = plan.pop("proposal_hash", None)
    if proposal_hash != _sha256(_json(plan)) or proposal_hash != financial["proposal_hash"]:
        raise FinancialWorkflowError("Financial proposal was altered")
    plan["proposal_hash"] = proposal_hash
    if (_financial_action_hash(plan) != financial["action_hash"]
            or financial["idempotency_key"] != financial["action_hash"]):
        raise FinancialWorkflowError("Action/idempotency binding changed")
    for field, expected in (("claim_id", stored["claim_id"]),
                            ("run_id", stored["run_id"]),
                            ("case_version", stored["case_version"]),
                            ("basis_revision", stored["revision"]),
                            ("evidence_hash", stored["evidence_hash"])):
        if financial[field] != expected or plan[field] != expected:
            raise FinancialWorkflowError("Financial proposal is stale")
    try:
        preview = build_gated_financial_preview(
            dossier_id, stored, audit_integrity=True, runtime_root=runtime_root
        )
    except FinancialGateError as exc:
        raise FinancialWorkflowError(str(exc)) from exc
    if _case_financial_fingerprint(preview) != plan["price_fingerprint"]:
        raise FinancialWorkflowError("Financial source opinions or policy changed")
    state = CanonicalCaseState.model_validate_json(stored["state_json"])
    supporting_passages = [
        passage
        for line in plan["reviewed_lines"]
        if line["decision"] == "SUPPORTED"
        for passage in line["support_passages"]
    ]
    _check_support_sources(dossier_id, state, supporting_passages, runtime_root)
    return plan


def propose_financial(store: WorkflowStore, dossier_id: str, decisions: dict,
                      *, runtime_root: str | Path = "data/application_runtime") -> dict:
    """5B: record a human-attested, source-linked, deterministic proposal."""
    record = store.get(dossier_id)
    if record is None:
        raise FinancialWorkflowError("Investigation is missing")
    valid, _ = store.verify_audit(record["claim_id"])
    try:
        plan = _build_financial_proposal(
            dossier_id, record, decisions, audit_integrity=valid, runtime_root=runtime_root
        )
    except FinancialWorkflowError:
        raise
    except Exception as exc:
        raise FinancialWorkflowError(f"Financial preparation blocked: {type(exc).__name__}") from exc
    action_hash = _financial_action_hash(plan)
    status = "HITL_REQUIRED" if plan["policy_reasons"] else "AWAITING_AUTHORIZATION"
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        if _financial_record(store, dossier_id) is not None:
            raise FinancialWorkflowError("Financial proposal already exists; never overwrite an existing action")
        current = store.get(dossier_id)
        if current is None or any(current[f] != record[f] for f in
                                  ("run_id", "case_version", "revision", "evidence_hash", "status", "validation_json")):
            raise FinancialWorkflowError("Investigation changed while proposing financial action")
        store.connection.execute("""
          INSERT INTO financial_workflows
            (dossier_id, claim_id, run_id, case_version, basis_revision,
             evidence_hash, status, proposal_json, proposal_hash,
             action_hash, idempotency_key, approved_by, authorized_until,
             grant_consumed, updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (dossier_id, record["claim_id"], record["run_id"],
              record["case_version"], record["revision"], record["evidence_hash"],
              status, _json(plan), plan["proposal_hash"], action_hash, action_hash,
              None, None, 0, _now()))
        _finance_event(store, dossier_id, "FINANCIAL_PROPOSAL_RECORDED", {
            "status": status, "amount": plan["amount"],
            "proposal_hash": plan["proposal_hash"],
            "policy_reasons": plan["policy_reasons"],
        })
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"status": status, "amount": plan["amount"],
            "policy_reasons": plan["policy_reasons"], "proposal_hash": plan["proposal_hash"]}


def authorize_financial(store: WorkflowStore, dossier_id: str, actor: str,
                        *, runtime_root: str | Path = "data/application_runtime",
                        expires_minutes: int = 30) -> dict:
    """5C: issue a short-lived, action-bound, single-use sandbox approval."""
    from datetime import timedelta
    if not isinstance(actor, str) or len(actor.strip()) < 3:
        raise FinancialWorkflowError("A named human approver is required")
    if actor.lower().startswith(("agent", "nemotron", "losstrace")):
        raise FinancialWorkflowError("AI agents cannot authorize financial actions")
    if not 1 <= expires_minutes <= 60:
        raise FinancialWorkflowError("Approval expiry must be 1–60 minutes")
    financial = _financial_record(store, dossier_id)
    if not financial or financial["status"] != "AWAITING_AUTHORIZATION":
        raise FinancialWorkflowError("No eligible financial proposal to authorize")
    plan = _check_financial_basis(store, dossier_id, financial, runtime_root)
    if actor.strip().casefold() == plan["reviewer"].casefold():
        raise FinancialWorkflowError("Second-person authorization required")
    expiration = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat()
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        # Keep optimistic concurrency within the transaction.
        count = store.connection.execute("""
          UPDATE financial_workflows SET status='AUTHORIZED', approved_by=?,
          authorized_until=?, updated_at=?
          WHERE dossier_id=? AND status='AWAITING_AUTHORIZATION'
            AND action_hash=? AND grant_consumed=0
        """, (actor.strip(), expiration, _now(), dossier_id, financial["action_hash"])).rowcount
        if count != 1:
            raise FinancialWorkflowError("Approval conflict or replay")
        _finance_event(store, dossier_id, "ACTION_AUTHORIZED", {
            "approved_by": actor.strip(), "action_hash": financial["action_hash"],
            "expires_at": expiration,
        })
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"status": "AUTHORIZED", "expires_at": expiration,
            "action_hash": financial["action_hash"]}


def dispatch_financial(store: WorkflowStore, dossier_id: str,
                       *, runtime_root: str | Path = "data/application_runtime") -> dict:
    """5C: durably consume authorization before attempting the sandbox write."""
    financial = _financial_record(store, dossier_id)
    if not financial or financial["status"] != "AUTHORIZED":
        raise FinancialWorkflowError("Action not authorized or already dispatched")
    _check_financial_basis(store, dossier_id, financial, runtime_root)
    if not financial["approved_by"] or not financial["authorized_until"]:
        raise FinancialWorkflowError("Missing explicit human authorization")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(financial["authorized_until"]):
        raise FinancialWorkflowError("Authorization expired; no execution")
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        count = store.connection.execute("""
          UPDATE financial_workflows SET status='EXECUTING', grant_consumed=1, updated_at=?
           WHERE dossier_id=? AND status='AUTHORIZED' AND grant_consumed=0
             AND action_hash=?
        """, (_now(), dossier_id, financial["action_hash"])).rowcount
        if count != 1:
            raise FinancialWorkflowError("Execution authorization was consumed or changed")
        _finance_event(store, dossier_id, "EXECUTION_DISPATCHED", {
            "action_hash": financial["action_hash"],
            "idempotency_key": financial["idempotency_key"],
        })
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"status": "EXECUTING", "idempotency_key": financial["idempotency_key"]}


def sandbox_apply(store: WorkflowStore, dossier_id: str) -> dict:
    """5C: persistent idempotent sandbox write; no real payment rails involved."""
    finance = _financial_record(store, dossier_id)
    if finance is None or finance["status"] != "EXECUTING" or finance["grant_consumed"] != 1:
        raise FinancialWorkflowError("Sandbox write requires consumed, authorized dispatch")
    if not store.verify_audit(finance["claim_id"])[0]:
        raise FinancialWorkflowError("Audit integrity failed before sandbox write")
    plan = json.loads(finance["proposal_json"])
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        current = _financial_record(store, dossier_id)
        if current is None or current["status"] != "EXECUTING" or current["action_hash"] != finance["action_hash"]:
            raise FinancialWorkflowError("Financial action changed during sandbox dispatch")
        existing = store.connection.execute("""
          SELECT amount, action_hash, claim_id FROM sandbox_payments WHERE idempotency_key=?
        """, (finance["idempotency_key"],)).fetchone()
        if existing is not None:
            if existing != (plan["amount"], finance["action_hash"], finance["claim_id"]):
                raise FinancialWorkflowError("Idempotency key collision with different business effect")
            # Never write a second payment.
        else:
            store.connection.execute("""
              INSERT INTO sandbox_payments
                (idempotency_key, dossier_id, claim_id, action_hash, amount, created_at)
              VALUES (?,?,?,?,?,?)
            """, (finance["idempotency_key"], dossier_id, finance["claim_id"],
                  finance["action_hash"], plan["amount"], _now()))
        _finance_event(store, dossier_id, "SANDBOX_WRITE_APPLIED", {
            "idempotency_key": finance["idempotency_key"],
            "already_present": existing is not None,
        })
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"idempotency_key": finance["idempotency_key"],
            "amount": plan["amount"], "applied": True}


def mark_execution_status(store: WorkflowStore, dossier_id: str,
                          status: str) -> None:
    """Record definitive ACK or unknown result; neither permits automatic replay."""
    if status not in {"EXECUTED", "UNKNOWN_EXECUTION"}:
        raise FinancialWorkflowError("Invalid execution status")
    financial = _financial_record(store, dossier_id)
    if not financial or not store.verify_audit(financial["claim_id"])[0]:
        raise FinancialWorkflowError("Cannot record execution status with invalid audit")
    if status == "EXECUTED":
        plan = json.loads(financial["proposal_json"])
        actual = store.connection.execute("""
            SELECT amount, action_hash, claim_id FROM sandbox_payments WHERE idempotency_key=?
        """, (financial["idempotency_key"],)).fetchone()
        if actual != (plan["amount"], financial["action_hash"], financial["claim_id"]):
            raise FinancialWorkflowError("Cannot acknowledge execution without matching backend effect")
    store.connection.execute("BEGIN IMMEDIATE")


    try:
        if store.connection.execute("""
          UPDATE financial_workflows SET status=?, updated_at=?
            WHERE dossier_id=? AND status='EXECUTING'
        """, (status, _now(), dossier_id)).rowcount != 1:
            raise FinancialWorkflowError("Execution state changed unexpectedly")
        _finance_event(store, dossier_id, "EXECUTION_" + status, {"status": status})
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise


def reconcile_financial(store: WorkflowStore, dossier_id: str) -> dict:
    """5D: a fresh durable read resolves an uncertain write; never retries."""
    financial = _financial_record(store, dossier_id)
    if not financial or financial["status"] not in {"UNKNOWN_EXECUTION", "EXECUTING"}:
        raise FinancialWorkflowError("No uncertain/dispatched action to reconcile")
    if not store.verify_audit(financial["claim_id"])[0]:
        raise FinancialWorkflowError("Audit integrity failure blocks reconciliation")
    plan = json.loads(financial["proposal_json"])
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        backend = store.connection.execute("""
          SELECT amount, action_hash, claim_id FROM sandbox_payments WHERE idempotency_key=?
        """, (financial["idempotency_key"],)).fetchone()
        if backend is None:
            result = "HITL_REQUIRED"
            reason = "No payment effect confirmed; do not blindly retry"
        elif backend != (plan["amount"], financial["action_hash"], financial["claim_id"]):
            result = "VERIFICATION_FAILED"
            reason = "Sandbox payment effect differs from authorized intent"
        else:
            result = "EXECUTED"
            reason = "Authorized effect found by fresh idempotency-key lookup"
        store.connection.execute("""
          UPDATE financial_workflows SET status=?, updated_at=?
            WHERE dossier_id=? AND status IN ('UNKNOWN_EXECUTION', 'EXECUTING')
        """, (result, _now(), dossier_id))
        _finance_event(store, dossier_id, "EXECUTION_RECONCILED", {
            "status": result, "reason": reason,
        })
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"status": result, "reason": reason}


def verify_financial(store: WorkflowStore, dossier_id: str) -> dict:
    """5D: independently read durable ledger and compare exact authorized effect."""
    financial = _financial_record(store, dossier_id)
    if not financial or financial["status"] != "EXECUTED":
        raise FinancialWorkflowError("Verification requires EXECUTED or reconciled action")
    if not store.verify_audit(financial["claim_id"])[0]:
        raise FinancialWorkflowError("Audit integrity failure blocks verification")
    plan = json.loads(financial["proposal_json"])
    store.connection.execute("BEGIN IMMEDIATE")
    try:
        backend = store.connection.execute("""
          SELECT amount, action_hash, claim_id FROM sandbox_payments WHERE idempotency_key=?
        """, (financial["idempotency_key"],)).fetchone()
        good = backend == (plan["amount"], financial["action_hash"], financial["claim_id"])
        status = "VERIFIED" if good else "VERIFICATION_FAILED"
        store.connection.execute("""
          UPDATE financial_workflows SET status=?, updated_at=?
            WHERE dossier_id=? AND status='EXECUTED'
        """, (status, _now(), dossier_id))
        _finance_event(store, dossier_id, "FRESH_STATE_VERIFICATION", {
            "verified": good, "status": status,
        })
        store.connection.commit()
    except Exception:
        store.connection.rollback()
        raise
    return {"status": status, "verified": good,
            "expected_amount": plan["amount"],
            "actual_amount": backend[0] if backend else None}


def _init_financial_tables(store: WorkflowStore) -> None:
    store.connection.execute("""
      CREATE TABLE IF NOT EXISTS financial_workflows (
        dossier_id TEXT PRIMARY KEY,
        claim_id TEXT NOT NULL UNIQUE,
        run_id TEXT NOT NULL,
        case_version INTEGER NOT NULL,
        basis_revision INTEGER NOT NULL,
        evidence_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        proposal_json TEXT NOT NULL,
        proposal_hash TEXT NOT NULL,
        action_hash TEXT NOT NULL UNIQUE,
        idempotency_key TEXT NOT NULL UNIQUE,
        approved_by TEXT,
        authorized_until TEXT,
        grant_consumed INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
      )
    """)
    store.connection.execute("""
      CREATE TABLE IF NOT EXISTS sandbox_payments (
        idempotency_key TEXT PRIMARY KEY,
        dossier_id TEXT NOT NULL UNIQUE,
        claim_id TEXT NOT NULL UNIQUE,
        action_hash TEXT NOT NULL,
        amount TEXT NOT NULL,
        created_at TEXT NOT NULL
      )
    """)
    store.connection.commit()


def _print_record(record: dict) -> None:
    for k in ("dossier_id", "claim_id", "run_id", "status", "next_action",
              "reason", "case_version", "revision", "evidence_hash", "updated_at"):
        print(f"{k}: {record[k]}")
    if record["validation_json"]:
        v = json.loads(record["validation_json"])
        print("validator_outcome:", v["outcome"])
        print("source_checks:", v["source_checks"])


def main() -> None:
    parser = argparse.ArgumentParser(description="LossTrace investigation application")
    parser.add_argument("--database", default="data/losstrace_workflow.sqlite3")
    parser.add_argument("--runtime-root", default="data/application_runtime")
    subs = parser.add_subparsers(dest="command", required=True)
    for cmd in ("preflight", "run", "status", "audit", "price-review",
                "financial-preview", "scope-review", "decision-template",
                "propose-financial", "authorize", "execute", "reconcile", "verify"):
        sub = subs.add_parser(cmd)
        sub.add_argument("--dossier", required=True)
        if cmd == "run":
            sub.add_argument("--budget-seconds", type=float, default=240.0)
        if cmd == "propose-financial":
            sub.add_argument("--decisions", type=Path, required=True)
        if cmd == "authorize":
            sub.add_argument("--approved-by", required=True)
            sub.add_argument("--expires-minutes", type=int, default=30)
            sub.add_argument("--confirm", action="store_true")
        if cmd == "execute":
            sub.add_argument("--confirm", action="store_true")
            sub.add_argument("--simulate-timeout-after-apply", action="store_true")
            sub.add_argument("--simulate-timeout-before-apply", action="store_true")
    args = parser.parse_args()
    # MCP's local server uses this fixed root. Reject a split evidence basis
    # before opening a database or starting any live model/tool work.
    if (args.command == "run" and Path(args.runtime_root).resolve()
            != Path("data/application_runtime").resolve()):
        parser.error("Live run requires the default data/application_runtime root; alternate roots are unsupported")
    with WorkflowStore(args.database) as store:
        if args.command == "preflight":
            state, count = load_case_from_runtime(args.dossier, runtime_root=args.runtime_root)
            print("Claim:", state.claim_id)
            print("Registered source artifacts:", count)
            print("Case version:", state.case_version)
            print("Evidence hash:", state.evidence_snapshot_hash)
            print("Ready for an explicit 'run' command. No model calls made.")
        elif args.command == "price-review":
            # Read-only price-evidence inspection is safe even when the case
            # has stopped at HITL. It must never authorize a financial action.
            from financial_stage import build_price_review
            state, _ = load_case_from_runtime(args.dossier, runtime_root=args.runtime_root)
            from financial_stage import FinancialGateError
            try:
                preview = build_price_review(
                    args.dossier, state, runtime_root=args.runtime_root
                )
            except FinancialGateError as exc:
                raise SystemExit(f"PRICE REVIEW BLOCKED: {exc}") from None
            print("READ-ONLY PRICE OPINIONS: NOT a settlement recommendation or payment authorization.")
            print(json.dumps(preview.to_dict(), indent=2))
        elif args.command == "scope-review":
            # Read-only reviewer preparation, including for HITL_REQUIRED cases.
            # No scope line is approved and no financial write is possible.
            from financial_stage import FinancialGateError
            record = store.get(args.dossier)
            if record is None:
                raise SystemExit("No saved investigation for that dossier")
            good, _ = store.verify_audit(record["claim_id"])
            try:
                queue = prepare_financial_scope_queue(
                    args.dossier, record, audit_integrity=good,
                    runtime_root=args.runtime_root,
                )
            except FinancialGateError as exc:
                raise SystemExit(f"SCOPE REVIEW BLOCKED: {exc}") from None
            print("READ-ONLY FINANCIAL SCOPE QUEUE: no support decision, settlement or write.")
            print(json.dumps(queue.to_dict(), indent=2))
        elif args.command == "decision-template":
            record = store.get(args.dossier)
            if record is None:
                raise SystemExit("No saved investigation for that dossier")
            valid, _ = store.verify_audit(record["claim_id"])
            from financial_stage import FinancialGateError
            try:
                queue = prepare_financial_scope_queue(
                    args.dossier, record, audit_integrity=valid,
                    runtime_root=args.runtime_root,
                )
            except FinancialGateError as exc:
                raise SystemExit(f"TEMPLATE BLOCKED: {exc}") from None
            template = {
                "reviewer": "REPLACE_WITH_HUMAN_REVIEWER",
                "attestation": False,
                "lines": [
                    {"line_id": x.line_id, "decision": "PENDING",
                     "supported_amount": "0.00", "reason": "",
                     "support_passages": []}
                    for x in queue.lines
                ],
            }
            print(json.dumps(template, indent=2))
        elif args.command == "propose-financial":
            try:
                decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
                outcome = propose_financial(
                    store, args.dossier, decisions, runtime_root=args.runtime_root
                )
            except (FinancialWorkflowError, ValueError, OSError) as exc:
                raise SystemExit(f"FINANCIAL PROPOSAL BLOCKED: {exc}") from None
            print(json.dumps(outcome, indent=2))
        elif args.command == "authorize":
            if not args.confirm:
                raise SystemExit("Authorization requires --confirm and a named human approver")
            try:
                outcome = authorize_financial(
                    store, args.dossier, args.approved_by,
                    runtime_root=args.runtime_root, expires_minutes=args.expires_minutes,
                )
            except FinancialWorkflowError as exc:
                raise SystemExit(f"AUTHORIZATION BLOCKED: {exc}") from None
            print(json.dumps(outcome, indent=2))
        elif args.command == "execute":
            if not args.confirm:
                raise SystemExit("Sandbox execution requires explicit --confirm")
            if args.simulate_timeout_after_apply and args.simulate_timeout_before_apply:
                raise SystemExit("Select at most one timeout simulation")
            try:
                dispatch_financial(store, args.dossier, runtime_root=args.runtime_root)
                if not args.simulate_timeout_before_apply:
                    sandbox_apply(store, args.dossier)
                if args.simulate_timeout_after_apply or args.simulate_timeout_before_apply:
                    mark_execution_status(store, args.dossier, "UNKNOWN_EXECUTION")
                    print("UNKNOWN_EXECUTION: simulate timeout; reconcile before any further action")
                else:
                    mark_execution_status(store, args.dossier, "EXECUTED")
                    print("EXECUTED in local SQLite SANDBOX only; run 'verify' next")
            except FinancialWorkflowError as exc:
                raise SystemExit(f"EXECUTION BLOCKED: {exc}") from None
        elif args.command == "reconcile":
            try:
                print(json.dumps(reconcile_financial(store, args.dossier), indent=2))
            except FinancialWorkflowError as exc:
                raise SystemExit(f"RECONCILIATION BLOCKED: {exc}") from None
        elif args.command == "verify":
            try:
                print(json.dumps(verify_financial(store, args.dossier), indent=2))
            except FinancialWorkflowError as exc:
                raise SystemExit(f"VERIFICATION BLOCKED: {exc}") from None
        elif args.command == "financial-preview":
            # This preview is blocked unless the recorded investigation is
            # provisionally supported, sufficient, current and audit-intact.
            from financial_stage import build_gated_financial_preview
            record = store.get(args.dossier)
            if record is None:
                raise SystemExit("No saved investigation for that dossier")
            good, _ = store.verify_audit(record["claim_id"])
            from financial_stage import FinancialGateError
            try:
                preview = build_gated_financial_preview(
                    args.dossier, record, audit_integrity=good,
                    runtime_root=args.runtime_root,
                )
            except FinancialGateError as exc:
                raise SystemExit(f"FINANCIAL PREVIEW BLOCKED: {exc}") from None
            print("CONDITIONAL PREVIEW ONLY: no policy approval, settlement or financial write.")
            print(json.dumps(preview.to_dict(), indent=2))
        elif args.command == "run":
            state, _ = load_case_from_runtime(args.dossier, runtime_root=args.runtime_root)
            receipt = asyncio.run(run_case(
                args.dossier, state, store,
                budget=InvestigationBudget(total_seconds=args.budget_seconds),
            ))
            print("Claim:", receipt.claim_id)
            print("Status:", receipt.status)
            print("Next action:", receipt.next_action)
            print("Reason:", receipt.reason)
            print("Revision:", receipt.revision)
            print("Source checks:", receipt.source_checks)
            print("No settlement authorization or financial write was performed.")
        else:
            record = store.get(args.dossier)
            if record is None:
                raise SystemExit("No saved workflow for that dossier")
            if args.command == "status":
                _print_record(record)
                financial = _financial_record(store, args.dossier)
                if financial is not None:
                    print("financial_status:", financial["status"])
                    print("proposal_hash:", financial["proposal_hash"])
                    print("authorization_consumed:", bool(financial["grant_consumed"]))
                    print("sandbox_amount:", json.loads(financial["proposal_json"])["amount"])
            else:
                good, n = store.verify_audit(record["claim_id"])
                print("Audit integrity:", "PASS" if good else "FAIL")
                print("Audit events:", n)
                if not good:
                    raise SystemExit(2)


if __name__ == "__main__":
    main()


