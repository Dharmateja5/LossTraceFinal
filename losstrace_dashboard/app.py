"""LossTrace saved-result viewer with explicit, isolated live investigation launch.

Place this folder beside losstrace_workflow.py. Launch instructions are in README.md.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape
import json
import hashlib
from pathlib import Path
import re
import sqlite3
import sys

import streamlit as st

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

UI_DIRECTORY = Path(__file__).resolve().parent
if str(UI_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(UI_DIRECTORY))
import investigation as interactive
from financial_preview import gated_conditional_preview

from case import CanonicalCaseState
from losstrace_workflow import WorkflowStore, _financial_record
from validation_agent import ValidationResult

SCENARIOS = ("Claims Workspace", "Synthetic sandbox settlement", "Positive live investigation", "Human review investigation")
# Navigation hints only. Outcomes are always read from the selected database.
DEMO_CLAIMS = {SCENARIOS[2]: ("CLM-26-591035", "d-e003cb27d820"),
               SCENARIOS[3]: ("CLM-26-876731", "d-11cd8b30545e")}
TABLES = {"investigation_workflows", "investigation_audit", "financial_workflows", "sandbox_payments"}
EVENT_LABELS = {
    "INVESTIGATION_STARTED": "Investigation started",
    "INVESTIGATION_COMPLETED": "Investigation checkpoint saved",
    "INVESTIGATION_FAILED": "Investigation stopped",
    "FINANCIAL_PROPOSAL_RECORDED": "Financial proposal recorded",
    "ACTION_AUTHORIZED": "Named approval recorded",
    "EXECUTION_DISPATCHED": "Authorization consumed; action dispatched",
    "SANDBOX_WRITE_APPLIED": "Sandbox ledger write applied",
    "EXECUTION_UNKNOWN_EXECUTION": "Execution response uncertain",
    "EXECUTION_EXECUTED": "Execution acknowledged",
    "EXECUTION_RECONCILED": "Execution reconciled",
    "FRESH_STATE_VERIFICATION": "Fresh-state verification recorded",
}


class ReadError(Exception):
    """A safe, operator-facing read error without raw database/exception contents."""


def safe_text(value: object) -> str:
    """Render selected fields only; redact common credential forms, never read env."""
    text = str(value if value is not None else "Not recorded")
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)\b(?:[a-z_]*api[_-]?key|access_token|secret|password|authorization)\s*[:=]\s*[\"']?[^\s,;\"']+",
                  "[REDACTED CREDENTIAL]", text)
    return re.sub(r"\b(?:sk|nk|nb|nebius)[_-][A-Za-z0-9_-]{12,}", "[REDACTED]", text)


def html(value: object) -> str:
    return escape(safe_text(value), quote=True)


def tone(value: object) -> str:
    text = str(value or "").upper()
    if text in {"PASS", "VERIFIED", "SUPPORTED", "ACCEPTED", "EXECUTED", "SUFFICIENT", "PROVISIONAL_SUPPORT", "AWAITING_VALUATION", "NEXT_VALUATION", "CORROBORATING"}:
        return "good"
    if "POTENTIAL_CONFLICT" in text:
        return "warn"
    if any(x in text for x in ("FAIL", "CONFLICT", "UNSUPPORTED")):
        return "bad"
    if any(x in text for x in ("HITL", "INSUFFICIENT", "UNKNOWN", "INCONCLUSIVE", "REINVESTIGATE", "EXPIRED")):
        return "warn"
    return "neutral"


def pill(value: object) -> str:
    return f'<span class="lt-pill {tone(value)}">{html(value)}</span>'


def _authorize_read(action, arg1, arg2, database, trigger):
    # In addition to mode=ro and query_only: reject DML, DDL, ATTACH and PRAGMA.
    return sqlite3.SQLITE_OK if action in {
        sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION,
    } else sqlite3.SQLITE_DENY


class ReadOnlyStore(WorkflowStore):
    """Reuse get/verify_audit without invoking the backend's mutating constructor."""
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.set_authorizer(_authorize_read)
            self.connection.execute("BEGIN")  # One consistent read snapshot, including audit.
        except Exception:
            self.connection.close()
            raise


@contextmanager
def open_reader(path: str | Path):
    try:
        target = Path(path).expanduser().resolve()
        if not target.is_file():
            raise ReadError("Database not found. Choose an existing SQLite file; this dashboard will not create one.")
        with ReadOnlyStore(target) as store:
            schema = dict(store.connection.execute(
                "SELECT name, type FROM sqlite_master WHERE name IN (?,?,?,?)", tuple(sorted(TABLES))))
            if any(schema.get(name) != "table" for name in TABLES):
                raise ReadError("This file does not contain the complete LossTrace release schema. No migration was attempted.")
            yield store
    except ReadError:
        raise
    except (sqlite3.Error, OSError, ValueError):
        raise ReadError("Database cannot be read. Check the file, access permissions and writer lock, then refresh.") from None


def list_cases(path: str | Path) -> list[dict]:
    with open_reader(path) as store:
        rows = store.connection.execute(
            "SELECT dossier_id, claim_id, status FROM investigation_workflows ORDER BY updated_at DESC, dossier_id").fetchall()
    return [dict(zip(("dossier_id", "claim_id", "status"), row)) for row in rows]


def read_snapshot(path: str | Path, dossier_id: str) -> dict:
    """Only SELECTs and existing read APIs; never calls verification that writes."""
    with open_reader(path) as store:
        record = store.get(dossier_id)
        if record is None:
            raise ReadError("The selected investigation is no longer present. Refresh the case list.")
        issues = []
        try:
            audit_valid, audit_count = store.verify_audit(record["claim_id"])
            audit_status = "PASS" if audit_valid else "FAIL"
        except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
            audit_status, audit_count = "UNVERIFIABLE", None
        financial = _financial_record(store, dossier_id)
        payments = [dict(zip(("idempotency_key", "dossier_id", "claim_id", "action_hash", "amount", "created_at"), row))
                    for row in store.connection.execute(
                        "SELECT idempotency_key,dossier_id,claim_id,action_hash,amount,created_at FROM sandbox_payments WHERE dossier_id=?",
                        (dossier_id,)).fetchall()]
        total_writes = store.connection.execute("SELECT COUNT(*) FROM sandbox_payments").fetchone()[0]
        events = []
        for row in store.connection.execute(
                "SELECT id,event_type,details_json,timestamp,event_hash FROM investigation_audit WHERE claim_id=? ORDER BY id",
                (record["claim_id"],)).fetchall():
            event_id, kind, raw, timestamp, event_hash = row
            try:
                details = json.loads(raw)
                if not isinstance(details, dict):
                    raise ValueError
            except (ValueError, TypeError):
                details = {}
                issues.append("An audit event has unreadable details.")
            events.append(dict(id=event_id, kind=kind, details=details, timestamp=timestamp, event_hash=event_hash))
    state, validation, proposal = None, None, None
    try:
        state = CanonicalCaseState.model_validate_json(record["state_json"])
        if (state.claim_id, state.case_version, state.revision, state.evidence_snapshot_hash) != (
                record["claim_id"], record["case_version"], record["revision"], record["evidence_hash"]):
            issues.append("Canonical state and checkpoint basis differ.")
    except (ValueError, TypeError):
        issues.append("Canonical state cannot be parsed with the release data model.")
    if record["validation_json"]:
        try:
            validation = ValidationResult.model_validate_json(record["validation_json"])
            if (validation.basis_case_version, validation.basis_revision, validation.basis_evidence_hash) != (
                    record["case_version"], record["revision"], record["evidence_hash"]):
                issues.append("Validator basis differs from the persisted checkpoint.")
        except (ValueError, TypeError):
            issues.append("Validator result cannot be parsed with the release data model.")
    if financial:
        try:
            proposal = json.loads(financial["proposal_json"])
            if not isinstance(proposal, dict) or not isinstance(proposal.get("reviewed_lines"), list):
                raise ValueError
            if any(not isinstance(line, dict) for line in proposal["reviewed_lines"]):
                raise ValueError
        except (ValueError, TypeError):
            proposal = None
            issues.append("Financial proposal is malformed; its details are not displayed.")
    return dict(record=record, state=state, validation=validation, financial=financial,
                proposal=proposal, payments=payments, total_writes=total_writes,
                events=events, audit=audit_status, audit_count=audit_count,
                issues=issues, read_at=datetime.now(timezone.utc).isoformat())


def parse_passage(raw: str) -> dict | None:
    """Display-only extraction. Never labels a quote as newly source-verified."""
    match = re.fullmatch(
        r"(?P<source>survey|service|policy_wording|endorsement):(?P<artifact>[^ |]+) \| "
        r"sha256:(?P<sha>[0-9a-f]{64}) \| page:(?P<page>\d+) \| section:(?P<section>[^|]+) \| "
        r"passage_id:(?P<passage>[^ |]+) \| (?:offsets:(?P<offsets>\d+-\d+) \| )?"
        r"quote:(?P<quote>.*?) \| model_interpretation:(?P<interpretation>.*)", raw, re.S)
    return match.groupdict() if match else None


def money(value: object) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
            raise ValueError
        amount = Decimal(value)
        return f"${amount:,.2f}" if amount.is_finite() else "Not recorded"
    except (ValueError, InvalidOperation):
        return "Not recorded"


def latest_event(snapshot: dict, kind: str) -> dict | None:
    return next((event for event in reversed(snapshot["events"]) if event["kind"] == kind), None)


def financial_indicator(snapshot: dict) -> str:
    if snapshot['financial'] is not None:
        return f"Recorded workflow: {snapshot['financial']['status']}"
    r = snapshot['record']
    if r['status'] == 'HITL_REQUIRED' or r['next_action'] == 'HITL_REQUIRED':
        return 'Blocked by recorded human-review handoff; no financial workflow'
    return 'No financial workflow recorded; eligibility not established'


def recorded_indicators(snapshot: dict) -> list[tuple[str, object]]:
    """Display stored facts, not scenario-specific expected outcomes."""
    validation = snapshot['validation']
    state = snapshot['state']
    roles = {f.specialist.value for f in state.specialist_findings} if state else set()
    missing = [f'{role.title()} Agent finding' for role in ('evidence', 'coverage') if role not in roles]
    if validation is None:
        missing.append('Validator result')
    return [
        ('Investigation status', snapshot['record']['status']),
        ('Validator outcome', validation.outcome.value if validation is not None else 'Not available'),
        ('Source checks', validation.source_checks if validation is not None else 'Not available'),
        ('Audit integrity', snapshot['audit']),
        ('Financial state', financial_indicator(snapshot)),
        ('Execution information', 'Missing: ' + ', '.join(missing) if missing else 'Specialist and Validator records available'),
    ]


def metadata_text(value) -> str:
    return safe_text(value.strip()) if isinstance(value, str) and value.strip() else 'Not available'


def claim_rows(claims: list[dict], runs: list[dict], *, lookup_incomplete=False) -> list[dict]:
    rows = []
    for claim in claims:
        matching = [r['snapshot']['record'] for r in runs
                    if (r['snapshot']['record']['dossier_id'], r['snapshot']['record']['claim_id']) ==
                    (claim['dossier_id'], claim['claim_id'])]
        latest = max(matching, key=lambda r: r['updated_at'], default=None)
        rows.append(dict(claim, description=metadata_text(claim.get('claim_summary') or claim.get('loss_type')),
                         loss_date=metadata_text(claim.get('loss_date')),
                         location=metadata_text(claim.get('location')),
                         status=latest['status'] if latest else ('UNKNOWN — database unavailable' if lookup_incomplete else 'NOT INVESTIGATED'),
                         last_investigated=latest['updated_at'] if latest else 'Not available',
                         saved_count=len(matching)))
    return rows


def filter_claims(rows: list[dict], query: str) -> list[dict]:
    needle = query.strip().casefold()
    return [r for r in rows if not needle or needle in r['claim_id'].casefold() or needle in r['description'].casefold() or needle in metadata_text(r.get('loss_type')).casefold()]


def resolve_row_selection(rows, current, selected, previous):
    """Map native original-row indices to dossiers; ignore unchanged events."""
    ids = [r['dossier_id'] for r in rows]
    if not ids:
        return None
    if current not in ids:
        current = next((r['dossier_id'] for r in rows if r['saved_count']), ids[0])
    if selected != previous and len(selected) == 1:
        index = selected[0]
        if type(index) is int and 0 <= index < len(rows):
            return rows[index]['dossier_id']
    return current


def show_claim_workspace(claims, runs, *, lookup_incomplete=False):
    st.subheader('Claims Workspace')
    query = st.text_input('Search claims', placeholder='Claim ID, claim summary or reported cause', key='claim_search')
    all_rows = claim_rows(claims, runs, lookup_incomplete=lookup_incomplete)
    rows = filter_claims(all_rows, query)
    saved_count = sum(bool(r['saved_count']) for r in all_rows)
    st.caption(f'{len(rows)} of {len(all_rows)} registered claims · {saved_count} with saved investigations. Select a row to inspect its records.')
    if not rows:
        st.info('No registered claims match your search. Clear or change the search to continue.')
        return None
    visible = [{'Claim ID': safe_text(r['claim_id']), 'Claim Summary': r['description'],
                'Loss Date': r['loss_date'], 'Investigation Status': safe_text(r['status']),
                'Last Investigated': safe_text(r['last_investigated'])} for r in rows]
    identity = hashlib.sha256(json.dumps([r['dossier_id'] for r in rows]).encode()).hexdigest()[:16]
    key = 'claims_table_' + identity
    events = st.session_state.setdefault('claim_table_events', {})
    try:
        event = st.dataframe(visible, hide_index=True, width='stretch',
                             height=min(245, 38 + 35 * len(rows)),
                             on_select='rerun', selection_mode='single-row', key=key,
                             column_config={'Claim Summary': st.column_config.TextColumn(width='large'),
                                            'Last Investigated': st.column_config.TextColumn(width='medium')})
        selected = tuple(event.selection.rows)
        dossier = resolve_row_selection(rows, st.session_state.get('selected_claim'), selected, events.get(key, ()))
        events[key] = selected
    except (TypeError, ImportError):
        # Older Streamlit or blocked optional pandas DLLs on managed Windows builds without native selection: one searchable control.
        labels = {r['dossier_id']: f"{r['claim_id']} · {r['description']} · {r['status']}" for r in rows}
        current = resolve_row_selection(rows, st.session_state.get('selected_claim'), (), ())
        dossier = st.selectbox('Select claim', list(labels), index=list(labels).index(current),
                               format_func=labels.get, key='claim_fallback')
    st.session_state['selected_claim'] = dossier
    return next(r for r in rows if r['dossier_id'] == dossier)


def show_claim_details(claim: dict, snapshot=None):
    r = snapshot['record'] if snapshot else None
    values = [('Claim ID', claim['claim_id']), ('Claim summary', claim['description']),
              ('Reported cause', metadata_text(claim.get('loss_type'))),
              ('Dossier ID', claim['dossier_id']), ('Date of loss', claim['loss_date']),
              ('Location', claim['location']), ('Registered case version', claim['case_version']),
              ('Latest investigation', claim['last_investigated']),
              ('Selected investigation status' if r else 'Investigation status', r['status'] if r else claim['status'])]
    with st.container(border=True):
        st.subheader('Claim Details')
        st.html('<div class="lt-claim-details">' + ''.join(
            f'<div><span>{html(label)}</span><strong>{html(value)}</strong></div>' for label, value in values) + '</div>')
        if r:
            st.caption(f"Selected run {safe_text(r['run_id'])} · recorded case version {r['case_version']} · {safe_text(r['updated_at'])}")


def card(label: str, value: object, note: object = ""):
    st.html(f'<div class="lt-card"><div class="lt-eyebrow">{html(label)}</div>'
            f'<div class="lt-value">{html(value)}</div><div class="lt-note">{html(note)}</div></div>')


def section_title(number: str, title: str, subtitle: str):
    st.html(f'<div class="lt-section"><span>{html(number)}</span><div><h2>{html(title)}</h2>'
            f'<p>{html(subtitle)}</p></div></div>')


def recorded_steps(snapshot: dict) -> dict:
    """Latest recorded execution step per role; finding sufficiency is separate."""
    steps = {}
    event = latest_event(snapshot, "INVESTIGATION_COMPLETED")
    for round_ in (event or {}).get("details", {}).get("rounds", []):
        if isinstance(round_, dict):
            for step in round_.get("steps", []):
                if isinstance(step, dict) and step.get("role") in {"coverage", "evidence"}:
                    steps[step["role"]] = step.get("outcome", "NOT RECORDED")
    return steps


def decision_kind(snapshot: dict) -> str:
    """Presentation categories only: never change a persisted decision or eligibility."""
    r, v, state = snapshot["record"], snapshot["validation"], snapshot["state"]
    steps = recorded_steps(snapshot)
    failures = {"TOOL_FAILURE", "BUDGET_EXCEEDED", "STALE_FINDING", "INVALID_FINDING", "REVISION_CONFLICT"}
    if r["status"] == "BLOCKED_FAILURE" or latest_event(snapshot, "INVESTIGATION_FAILED") or failures.intersection(steps.values()):
        return "failure"
    if snapshot["audit"] != "PASS" or snapshot["issues"]:
        return "unverified"
    findings = {f.specialist.value: f for f in state.specialist_findings} if state else {}
    if v is None or not all(role in findings for role in ("coverage", "evidence")):
        return "incomplete"
    if (r["status"] == "AWAITING_VALUATION" and r["next_action"] == "NEXT_VALUATION"
            and v.outcome.value == "PROVISIONAL_SUPPORT"
            and all(findings[role].status.value == "SUFFICIENT" for role in ("coverage", "evidence"))):
        return "positive"
    if r["status"] == "HITL_REQUIRED" or r["next_action"] == "HITL_REQUIRED":
        return "review"
    return "recorded"


def demo_run(runs, scenario):
    """Choose a completed verified demonstration, never substitute a failed attempt."""
    identity = DEMO_CLAIMS.get(scenario)
    category = "positive" if scenario == SCENARIOS[2] else "review"
    return next((run for run in runs
                 if (run["snapshot"]["record"]["claim_id"], run["snapshot"]["record"]["dossier_id"]) == identity
                 and decision_kind(run["snapshot"]) == category
                 and run["snapshot"]["financial"] is None
                 and interactive.freshness(run["snapshot"], PROJECT).startswith("Verified")), None)


def show_flow(snapshot: dict):
    state, validation = snapshot["state"], snapshot["validation"]
    findings = {f.specialist.value: f for f in state.specialist_findings} if state else {}
    steps = recorded_steps(snapshot)
    flow = []
    for role, purpose in (("coverage", "Interpret policy & endorsements"), ("evidence", "Compare inspection & plumber evidence")):
        finding = findings.get(role)
        status = finding.status.value if finding else "NOT RECORDED"
        execution = steps.get(role)
        note = ("Execution accepted" if execution == "ACCEPTED" else "Execution: " + execution) if execution else "Execution not recorded"
        note += f" · {len(finding.findings)} findings" if finding else " · no finding"
        flow.append((role.title() + " Agent", purpose, status, note))
    flow.extend([("Independent Validator", "Check sources & interpretation",
                  validation.outcome.value if validation else "NOT RECORDED",
                  f"{validation.source_checks} recorded source checks" if validation else "No validation is assumed"),
                 ("Workflow Decision", "Deterministic Coordinator routing", snapshot["record"]["next_action"], "Persisted handoff; no payment approval")])
    st.html('<div class="lt-flow">' + ''.join(
        f'<div class="lt-flow-node"><span class="lt-step">0{i} · {html(purpose)}</span><strong>{html(name)}</strong>'
        f'<span class="lt-note">{html(note)}</span>{pill(status)}</div>'
        for i, (name, purpose, status, note) in enumerate(flow, 1)) + '</div>')
    st.caption("Coverage → Evidence → independent validation → deterministic routing. Execution, sufficiency and validation are separate recorded facts.")


def show_decision_banner(snapshot: dict):
    """Read-only business explanation of the actual selected checkpoint."""
    r, v = snapshot["record"], snapshot["validation"]
    kind = decision_kind(snapshot)
    synthetic = st.session_state.get("scenario") == SCENARIOS[1]
    if synthetic:
        title, detail, color = "Synthetic finance demonstration — controlled sandbox execution", "Simulated investigation sufficiency. Review the separate persisted authorization, sandbox payment and reconciliation history.", "synthetic"
    elif kind == "positive":
        title, detail, color = "Investigation Checks Passed", "Evidence and coverage checks passed. This claim can proceed to valuation. Valuation is still required; this is not settlement approval.", "positive"
    elif kind == "failure":
        title, detail, color = "Investigation incomplete — technical execution failure", "Agent execution failed or was interrupted. Partial findings are not treated as validated; financial progression remains blocked.", "failure"
    elif kind == "review":
        title, detail, color = "Human review required", "Human review is required before this claim can proceed. Review the recorded reason and unresolved requirements below.", "review"
    elif kind == "unverified":
        title, detail, color = "Record integrity not established", "Inspect the recorded outcome below. Audit or checkpoint issues prevent presenting it as a trusted result.", "failure"
    elif kind == "incomplete":
        title, detail, color = "Investigation incomplete", "Specialist or Validator output is not recorded. No successful validation is assumed.", "neutral"
    else:
        title, detail, color = "Recorded workflow decision", r.get("reason") or "Review the saved outcome and next action.", "neutral"
    if synthetic and snapshot["financial"]:
        detail += " Recorded financial state: " + str(snapshot["financial"]["status"]) + "."
        detail += " Sandbox payment rows: " + str(len(snapshot["payments"])) + "."
        if len(snapshot["payments"]) == 1:
            detail += " Persisted payment: " + money(snapshot["payments"][0]["amount"]) + "."
    checks = v.source_checks if v else "Not recorded"
    validation = v.outcome.value if v else "Not recorded"
    metadata = [("Claim", r["claim_id"]), ("Status", r["status"]), ("Next action", r["next_action"]),
                ("Source checks", checks), ("Revision", r["revision"]), ("Validator", validation)]
    st.html('<div class="lt-decision ' + color + '"><div class="lt-eyebrow">SELECTED SAVED INVESTIGATION</div>'
            '<h2>' + html(title) + '</h2><p>' + html(detail) + '</p><div class="lt-decision-facts">' + ''.join(
                '<div><span>' + html(label) + '</span><strong>' + html(value) + '</strong></div>' for label, value in metadata) + '</div></div>')
    if kind in {"review", "failure", "incomplete"} and not synthetic:
        st.caption("Recorded reason: " + safe_text(r.get("reason")))
    if kind == "review" and v and not synthetic:
        for reason in v.findings:
            st.caption("Validator: " + safe_text(reason))
        st.caption("Financial progression: " + safe_text(financial_indicator(snapshot)))
    if snapshot["financial"] is not None and not synthetic:
        st.caption("A separate financial workflow is recorded. Inspect its state in Controlled Settlement.")


def show_overview_sources_and_audit(snapshot: dict):
    """A readable, factual summary of saved sources and audit events (no invented checks)."""
    state = snapshot["state"]
    sources = []
    relationship = None
    if state:
        for finding in state.specialist_findings:
            for raw in finding.findings:
                passage = parse_passage(raw)
                if passage is not None:
                    title = {"survey": "Inspection report", "service": "Plumber report",
                             "policy_wording": "Policy wording", "endorsement": "Policy endorsement"}.get(
                                 passage["source"], "Recorded source")
                    source_key = (title, passage["artifact"], passage["passage"])
                    if source_key not in sources:
                        sources.append(source_key)
                if isinstance(raw, str) and raw.startswith("cross_document_model_assessment:"):
                    relationship = raw.split(" | ", 1)[0].split(":", 1)[-1]
    left, right = st.columns([1.4, 1])
    with left, st.container(border=True):
        st.subheader("Evidence & interpretation")
        if sources:
            for title, artifact, passage_id in sources[:6]:
                st.html('<div class="lt-verdict"><strong>' + html(title) + '</strong>'
                        '<span class="lt-note">Recorded source reference</span><p>'
                        + html("Passage " + passage_id + " · Artifact " + artifact) + '</p></div>')
            st.caption("Read the actual source text and model interpretations in Evidence & Validation. A recorded reference is not proof of semantic correctness.")
        else:
            st.info("No extractable passage references recorded for this investigation.")
        if relationship:
            st.html('<div class="lt-verdict"><strong>Model relationship assessment</strong>'
                    + pill(relationship) + '<p>Recorded model interpretation; human review may be required.</p></div>')
    with right, st.container(border=True):
        st.subheader("Audit trail")
        st.html(pill("PASS" if snapshot["audit"] == "PASS" else snapshot["audit"]))
        show_timeline(snapshot)
        st.caption("Financial controls: " + safe_text(financial_indicator(snapshot)))


def show_overview(snapshot: dict, scenario: str):
    r, state, validation = snapshot["record"], snapshot["state"], snapshot["validation"]
    st.caption("INVESTIGATION OVERVIEW · Recorded evidence, independent validation and a controlled next step")
    cols = st.columns(4)
    values = [("Coverage findings", next((len(f.findings) for f in state.specialist_findings if f.specialist.value == "coverage"), "—") if state else "—", "Recorded policy interpretations"),
              ("Evidence findings", next((len(f.findings) for f in state.specialist_findings if f.specialist.value == "evidence"), "—") if state else "—", "Recorded evidence interpretations"),
              ("Source checks", validation.source_checks if validation else "—", "Recorded by the Validator"),
              ("Audit integrity", snapshot["audit"], f'{snapshot["audit_count"]} events checked' if snapshot["audit_count"] is not None else "Could not verify")]
    for col, args in zip(cols, values):
        with col:
            card(*args)
    show_flow(snapshot)
    left, right = st.columns([1.4, 1])
    with left, st.container(border=True):
        st.subheader("Decision & next action")
        st.html(pill(r["next_action"]))
        st.text(safe_text(r["reason"]))
        st.caption(f'Case version {r["case_version"]} · Revision {r["revision"]} · Updated {safe_text(r["updated_at"])}')
        if state:
            for finding in state.specialist_findings:
                st.html(f'<div class="lt-summary"><strong>{html(finding.specialist.value.title())} Agent</strong>'
                        f'{pill(finding.status.value)}<small>{len(finding.findings)} recorded findings</small></div>')
        else:
            st.info("No readable specialist results are available.")
    with right, st.container(border=True):
        st.subheader("Recorded investigation outcome")
        for label, value in recorded_indicators(snapshot):
            st.html(f'<div class="lt-check"><strong>{html(label)}</strong><span>{html(value)}</span></div>')
        st.caption("Recorded findings show available outputs; they do not independently attest model HTTP success or prove the interpretation correct.")
        if scenario == SCENARIOS[1]:
            st.caption("Specialist sufficiency, Validator approval and human attestations in this scenario are simulated. Financial controls and SQLite effects are real.")
    show_overview_sources_and_audit(snapshot)
    with st.expander("Checkpoint identifiers"):
        for name in ("dossier_id", "run_id", "evidence_hash"):
            st.text(f"{name}: {safe_text(r[name])}")


def finding_category(raw: str) -> str:
    prefix = raw.split(':', 1)[0]
    return {'site_observations': 'Site observations', 'survey': 'Inspection report',
            'service': 'Plumber report', 'policy_terms': 'Policy terms',
            'policy_wording': 'Policy wording', 'endorsement': 'Endorsement',
            'cross_document_model_assessment': 'Cross-document reasoning'}.get(prefix, 'Recorded finding')


def show_evidence(snapshot: dict):
    section_title("02", "Evidence & Validation", "Recorded passages, independent assessment and unresolved questions.")
    st.caption("Source text below is preserved from the saved passage references. The separate freshness indicator checks current registered bytes; neither a hash nor source text proves the model interpretation correct.")
    state, validation = snapshot["state"], snapshot["validation"]
    left, right = st.columns([1.45, 1])
    with left:
        st.subheader("Specialist findings")
        if not state or not state.specialist_findings:
            st.info("No specialist findings recorded.")
        else:
            for finding in state.specialist_findings:
                st.html(f'<div class="lt-agent-heading"><strong>{html(finding.specialist.value.title())} Agent</strong>{pill(finding.status.value)}</div>')
                if not finding.findings:
                    st.info("This saved result has no passage-level findings. Sufficiency alone is not a citation.")
                for index, raw in enumerate(finding.findings, 1):
                    passage = parse_passage(raw)
                    category = finding_category(raw)
                    label = (f'{category} · {index:02d} · page {passage["page"]}'
                             if passage else f'{category} · {index:02d}')
                    with st.expander(label, expanded=index == 1):
                        if passage:
                            st.caption("Source quotation — recorded passage text")
                            st.html(f'<blockquote class="lt-quote">{html(passage["quote"])}</blockquote>')
                            st.caption("Model interpretation — not source text")
                            st.text(safe_text(passage["interpretation"]))
                            for key in ("artifact", "page", "section", "passage", "offsets", "sha"):
                                if passage.get(key):
                                    st.text(f'{key}: {safe_text(passage[key])}')
                        else:
                            if category == "Cross-document reasoning":
                                st.caption("Model-generated relationship assessment — preserved as recorded, not a proven fact.")
                            st.text(safe_text(raw))
                for item in (*finding.conflicts, *finding.unresolved_questions):
                    st.warning(safe_text(item))
    with right, st.container(border=True):
        st.subheader("Independent Validator")
        if validation is None:
            st.info("No Validator result recorded. A stopped investigation may not have reached validation.")
        else:
            st.html(pill(validation.outcome.value))
            st.caption(f'{validation.source_checks} source checks · basis revision {validation.basis_revision}')
            st.text(f'Reported relationship: {safe_text(validation.relationship_reported)}')
            st.text(f'Validator relationship: {safe_text(validation.relationship_validated)}')
            for check in validation.semantic_checks:
                st.html(f'<div class="lt-verdict"><strong>{html(check.key)}</strong>{pill(check.verdict)}<p>{html(check.reason)}</p></div>')
            if not validation.semantic_checks:
                st.caption("No semantic verdicts recorded.")
            for reason in validation.findings:
                st.text(safe_text(reason))
        st.divider()
        st.subheader("Decision rationale & unresolved requirements")
        st.text(safe_text(snapshot["record"]["reason"]))
        if state:
            for item in dict.fromkeys((*state.conflicts, *state.unresolved_questions)):
                st.warning(safe_text(item))
        st.caption("Recorded uncertainty and routing are preserved. Source provenance does not establish final coverage approval.")


def show_timeline(snapshot: dict):
    st.subheader("Audit timeline")
    st.caption("Recorded events in commit order. Reading this timeline creates no audit event.")
    if not snapshot["events"]:
        st.info("No audit events recorded.")
        return
    rows = []
    for event in snapshot["events"]:
        details = event["details"]
        summary = details.get("reason") or details.get("status") or details.get("next_action") or "Recorded"
        rows.append(f'<div class="lt-event"><div class="lt-dot"></div><div><div class="lt-event-title">'
                    f'{html(EVENT_LABELS.get(event["kind"], event["kind"]))}</div><p>{html(summary)}</p>'
                    f'<small>#{event["id"]} · {html(event["timestamp"])}</small></div></div>')
    st.html('<div class="lt-timeline">' + ''.join(rows) + '</div>')
    with st.expander("Audit event hashes"):
        for event in snapshot["events"]:
            st.text(f'#{event["id"]} {safe_text(event["kind"])}\n{safe_text(event["event_hash"])}')


def show_settlement(snapshot: dict, scenario: str):
    section_title("03", "Controlled Settlement", "Human scope attestations. Deterministic controls. Local sandbox effects.")
    finance, proposal = snapshot["financial"], snapshot["proposal"]
    left, right = st.columns([1.35, 1])
    with left:
        if finance is None:
            # This is a read-only *price opinion*, not a persisted financial proposal.
            # The existing backend rechecks the source bytes, audit and investigation gates.
            preview, preview_issue = gated_conditional_preview(
                snapshot, runtime_root=PROJECT / "data" / "application_runtime"
            )
            if preview is not None:
                st.subheader("Conditional financial preview")
                st.caption("Source-checked building repair price opinion · no autonomous valuation, scope approval or payment")
                amount_col, gate_col = st.columns([1.2, 1])
                with amount_col:
                    card("Conditional price opinion", money(preview.conditional_amount),
                         "USD · illustrative only, NOT an approved settlement")
                with gate_col:
                    card("Financial action permitted", "NO",
                         "Valuation, human scope attestations and authorization are still required")
                base_col, op_col, deductible_col = st.columns(3)
                with base_col:
                    card("Reviewed repair base", money(preview.price_opinion_base),
                         f"{len(preview.lines)} price-reviewed building lines")
                with op_col:
                    card("Conditional O&P", "+" + money(preview.conditional_o_and_p),
                         f"Reviewed rate: {Decimal(preview.reviewed_o_and_p_rate):.0%}")
                with deductible_col:
                    card("Policy deductible", "−" + money(preview.policy_deductible),
                         "Building limit applied before deductible")
                st.caption("The estimate and independent cost opinion establish prices, not whether each repair is caused by this loss or covered by the policy.")
                with st.expander("Price-review sources, seven repair lines & limitations"):
                    st.text("Estimate artifact: " + safe_text(preview.estimate_artifact_id))
                    st.text("Independent cost-review artifact: " + safe_text(preview.review_artifact_id))
                    st.text("Building limit: " + money(preview.building_limit))
                    for item in preview.lines:
                        st.text(safe_text(item.line_id) + " · " + safe_text(item.description) +
                                " · conditional line cap " + money(item.capped_candidate))
                    for warning in preview.warnings:
                        st.caption(safe_text(warning))
            elif preview_issue:
                st.warning("Conditional financial preview unavailable: " + safe_text(preview_issue))
            st.info("No financial proposal recorded. There is no authorization or execution to display.")
            if snapshot["record"]["status"] == "HITL_REQUIRED":
                st.warning("Investigation is at HITL. The financial handoff remains blocked.")
            card("Sandbox payment rows", len(snapshot["payments"]), "Observed for this dossier; no rows are created by the dashboard")
            if snapshot["payments"]:
                st.error("Payment rows exist without a financial workflow. Inspect this database outside the dashboard.")
        else:
            a, b = st.columns(2)
            with a:
                card("Proposed sandbox amount", money(proposal.get("amount")) if proposal else "Not readable", "USD · no real payment rail")
            with b:
                card("Financial status", finance["status"], "Persisted; this is not a new verification")
            if proposal:
                st.subheader("Human-attested scope")
                st.caption(f'Recorded reviewer: {safe_text(proposal.get("reviewer"))} · a CLI name, not authenticated identity')
                if scenario == SCENARIOS[1]:
                    st.caption("The reviewer and line decisions are synthetic attestations for this demonstration.")
                lines = proposal["reviewed_lines"]
                rows = [{"Line": safe_text(x.get("line_id")), "Decision": safe_text(x.get("decision")),
                         "Supported": money(x.get("supported_amount")), "Price cap": money(x.get("price_cap")),
                         "Rationale": safe_text(x.get("reason"))} for x in lines]
                if rows:
                    try:
                        st.dataframe(rows, hide_index=True, width="stretch")
                    except ImportError:
                        # Windows Application Control may block pandas DLLs.
                        # Show saved financial scope as plain text, never edit it.
                        for row in rows:
                            st.text(' | '.join(f'{key}: {value}' for key, value in row.items()))
                for line in lines:
                    passages = line.get("support_passages")
                    if isinstance(passages, list) and passages:
                        with st.expander(f'Recorded scope references · {safe_text(line.get("line_id"))}'):
                            for passage in passages:
                                if isinstance(passage, dict):
                                    st.text(f'Artifact {safe_text(passage.get("artifact_id"))} · page {safe_text(passage.get("page"))}')
                                    st.text(safe_text(passage.get("excerpt")))
                with st.expander("Calculation & policy record"):
                    for key in ("supported_building_base", "o_and_p", "building_limit", "deductible", "amount"):
                        st.text(f'{key}: {money(proposal.get(key))}')
                    st.caption("Engine order: supported loss + O&P → limit → deductible. This viewer does not recalculate.")
                    reasons = proposal.get("policy_reasons")
                    if isinstance(reasons, list) and reasons:
                        for reason in reasons:
                            st.text(safe_text(reason))
                    else:
                        st.caption("No policy-block reasons recorded in the proposal.")
            st.subheader("Authorization & execution")
            st.text(f'Named approver: {safe_text(finance["approved_by"])}')
            st.text(f'Authorization expires: {safe_text(finance["authorized_until"])}')
            st.text(f'Grant consumed: {"Yes" if finance["grant_consumed"] == 1 else "No" if finance["grant_consumed"] == 0 else "Invalid value"}')
            st.caption("Names are not authenticated identities. This display cannot authorize or execute an action.")
            card("Sandbox payment rows", len(snapshot["payments"]), f'{snapshot["total_writes"]} across the selected database')
            for payment in snapshot["payments"]:
                st.text(f'Persisted amount: {money(payment["amount"])} · Created: {safe_text(payment["created_at"])}')
            with st.expander("Action binding & idempotency"):
                for key in ("proposal_hash", "action_hash", "idempotency_key"):
                    st.text(f'{key}: {safe_text(finance[key])}')
            rec = latest_event(snapshot, "EXECUTION_RECONCILED")
            verified = latest_event(snapshot, "FRESH_STATE_VERIFICATION")
            st.subheader("Reconciliation & final verification")
            if rec:
                st.text(f'Reconciliation: {safe_text(rec["details"].get("status"))}')
                st.text(safe_text(rec["details"].get("reason")))
            else:
                st.caption("No reconciliation event recorded. It may not have been needed or performed.")
            if verified:
                st.text(f'Persisted final verification: {safe_text(verified["details"].get("status"))}')
                st.caption(f'Recorded {safe_text(verified["timestamp"])}. Refreshing the UI does not run verification again.')
            else:
                st.caption("No final verification event recorded.")
    with right, st.container(border=True):
        show_timeline(snapshot)


@st.fragment(run_every="2s")
def show_running_process():
    """Poll an already launched process only. Never start or retry a workflow."""
    try:
        current = interactive.launch_state(PROJECT)
    except (interactive.LaunchError, OSError):
        st.warning("Process completion is uncertain. New investigations remain blocked.")
        return
    if current is None:
        st.rerun()
    if current['state'] == 'PROCESS EXITED':
        st.session_state['latest_process_database'] = str(interactive.owned_root(PROJECT) / current['database'])
        st.rerun()
    st.info(safe_text(current['state'] + ' · ' + current.get('message', '')))
    st.caption("Checking the existing process every two seconds. Individual agent completion is shown only when persisted.")


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live-db", default="")
    parser.add_argument("--positive-db", default="")
    parser.add_argument("--investigation-db", action="append", default=[])
    parser.add_argument("--finance-db", default=str(PROJECT / "data/losstrace_finance_demo.sqlite3"))
    args, _ = parser.parse_known_args()
    st.set_page_config(page_title="LossTrace | Investigation Workspace", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
    st.html('<style>' + Path(__file__).with_name("styles.css").read_text(encoding="utf-8") + '</style>')
    with st.sidebar:
        st.html('<div class="lt-brand"><span class="lt-mark">▤</span><div>LossTrace<small>INVESTIGATION WORKSPACE</small></div></div>')
        st.caption("DEMONSTRATION")
        scenario = st.radio("Select demonstration", SCENARIOS, label_visibility="collapsed", key="scenario")
        st.divider()
        st.html('<div class="lt-readonly">SAVED RESULTS · READ ONLY</div>')
        st.caption("Live investigations require an explicit click and a fresh database. No financial actions.")
        with st.expander("How LossTrace works"):
            st.write("Nebius Token Factory · NVIDIA Nemotron 3 Super")
            st.caption("Coverage and Evidence specialists use MCP retrieval. Independent validation checks source references and bounded interpretations. The deterministic Adaptive Coordinator routes the case; it is not another model.")
            st.caption("Bounded autonomy: human review, deterministic financial gates, action-bound authorization and hash-linked audit. Jev and an autonomous Valuation Agent are deferred.")
        with st.expander("Database connections", expanded=False):
            live_path = st.text_input("Human-review / live SQLite database", value=args.live_db, key="live_path")
            positive_path = st.text_input("Positive investigation SQLite database", value=args.positive_db, key="positive_path")
            more_paths = st.text_area("Additional investigation databases (one path per line)",
                                      value="\n".join(args.investigation_db), key="additional_paths")
            finance_path = st.text_input("Synthetic finance SQLite database", value=args.finance_db, key="finance_path")
            st.caption("Register only known files. New runs are discovered only in data/dashboard_runs. Clear unused paths before launching.")
        st.button("Refresh saved data", width="stretch", key="refresh")
        st.caption("Refresh checks saved data and any existing process; it never launches an investigation.")
    st.html('<div class="lt-topline"><span>◈&nbsp; CLAIMS / INVESTIGATION &nbsp;·&nbsp; COMMERCIAL PROPERTY INTELLIGENCE</span><span>● LOCAL SANDBOX</span></div>')
    st.html('<div class="lt-hero"><h1>Claim Investigation Workspace</h1><p>Evidence-grounded AI claims investigation with independent validation and controlled settlement.</p></div>')
    st.caption("For commercial-property claims investigators and claims operations teams.")
    decision_slot = st.container()
    configured = [p.strip() for p in [live_path, positive_path, *more_paths.splitlines()] if p.strip()]
    try:
        finance = Path(finance_path).expanduser().resolve() if finance_path.strip() else None
        paths = interactive.database_paths(configured, PROJECT)
        if finance is not None and (finance in paths or finance.is_relative_to(interactive.owned_root(PROJECT))):
            st.error("Both scenarios point to the same database or the investigation-only directory. Use separate databases; synthetic finance must remain outside data/dashboard_runs.")
            return
    except (OSError, ValueError, RuntimeError, interactive.LaunchError):
        st.error("A database path or investigation directory is invalid. Correct Database connections.")
        return
    if scenario == SCENARIOS[1]:
        st.session_state['last_demo_scenario'] = scenario
        st.caption("SYNTHETIC INVESTIGATION / REAL SANDBOX FINANCIAL CONTROLS · No real monetary transfer. This is separate from the positive live claim.")
        try:
            cases = list_cases(finance_path)
            if not cases:
                st.info("No persisted synthetic finance investigation is available.")
                return
            labels = {r["dossier_id"]: f'{safe_text(r["claim_id"])} · {safe_text(r["dossier_id"])}' for r in cases}
            with st.sidebar:
                dossier = st.selectbox("Saved investigation", list(labels), format_func=labels.get, key=f"case_{scenario}")
            snapshot = read_snapshot(finance_path, dossier)
        except ReadError as exc:
            st.info(str(exc))
            return
    else:

        claims, rejected = interactive.discover_claims(PROJECT)
        if rejected:
            st.warning(f"{rejected} runtime registration(s) could not be verified and are unavailable for launch.")
        if not claims:
            st.info("No verified registered claims are available. No claim was fabricated.")
            return
        runs, problems = interactive.saved_runs(paths, list_cases, read_snapshot)
        for problem in problems:
            st.info(safe_text(problem))
        try:
            execution = interactive.launch_state(PROJECT)
        except (interactive.LaunchError, OSError):
            execution = {"state": "COMPLETION UNKNOWN", "message": "Investigation lock cannot be verified. New launches are blocked."}
        featured = demo_run(runs, SCENARIOS[2])
        demo = demo_run(runs, scenario) if scenario in DEMO_CLAIMS else None
        if scenario in DEMO_CLAIMS and demo is None:
            st.info("The requested completed demonstration is not available with verified integrity and current sources. Register its preserved SQLite database in Database connections. Failed or unrelated runs are not substituted.")
            st.caption("Choose Claims Workspace to browse all registered claims and inspect historical attempts.")
            return
        if scenario in DEMO_CLAIMS:
            claim = next((row for row in claim_rows(claims, runs) if row['dossier_id'] == DEMO_CLAIMS[scenario][1]), None)
        else:
            if 'selected_claim' not in st.session_state and featured:
                st.session_state['selected_claim'] = featured['snapshot']['record']['dossier_id']
                st.session_state['saved_run_' + st.session_state['selected_claim']] = featured['key']
            with st.expander("Browse & search registered claims", expanded=not bool(featured)):
                claim = show_claim_workspace(claims, runs, lookup_incomplete=bool(problems))
            if featured:
                st.caption("Featured saved investigation available in Positive live investigation. All historical runs remain selectable.")
        if claim is None:
            return
        dossier = claim['dossier_id']
        matching = [r for r in runs if (r['snapshot']['record']['dossier_id'], r['snapshot']['record']['claim_id']) == (dossier, claim['claim_id'])]
        receipts = [r for r in interactive.result_receipts(PROJECT) if r.get('dossier_id') == dossier]
        if execution:
            st.info(safe_text(execution['state'] + ' · ' + execution.get('message', 'Read the persisted result below.')))
            if execution.get('dossier_id'):
                st.caption(safe_text(f"Process dossier: {execution['dossier_id']} · started {execution.get('started_at', 'Not recorded')}"))
            if execution.get('state') == 'PROCESS EXITED':
                st.session_state['latest_process_database'] = str(interactive.owned_root(PROJECT) / execution['database'])
                st.rerun()
            elif execution.get('state') in {'INVESTIGATION RUNNING', 'TIMEOUT / STILL RUNNING'}:
                show_running_process()
        for receipt in receipts[:1]:
            st.caption(safe_text(f"Latest UI launch: {receipt['started_at']} · process exit code {receipt.get('returncode', 'unknown')}. Exit alone does not establish agent completion."))
        if not matching:
            show_claim_details(claim)
            if execution and execution.get('state') != 'PROCESS EXITED':
                st.warning("A process may still be running. Use Refresh saved data; no second launch is permitted.")
            elif receipts:
                st.error("A prior process exited without a readable investigation checkpoint. No agent or Validator result is assumed. Inspect the local run database; retry is disabled.")
            else:
                st.subheader(safe_text(claim["status"]))
                st.info("No saved investigation was found for this registered claim. Viewing this page does not call Nebius.")
                if st.button("Run Live Investigation", key="run_live", disabled=bool(problems), type="primary"):
                    try:
                        with st.spinner("Starting the existing investigation workflow…"):
                            started = interactive.launch(dossier, explicit_click=True, configured=configured,
                                                         list_cases=list_cases, read_snapshot=read_snapshot, project=PROJECT)
                        st.session_state['latest_process_database'] = str(interactive.owned_root(PROJECT) / started['database'])
                        st.rerun()
                    except interactive.LaunchError as exc:
                        st.error(safe_text(exc))
            return
        st.subheader(safe_text(claim.get('insured') or claim['claim_id']))
        st.caption(safe_text(claim['claim_id'] + ' · ' + claim['description']))
        with st.expander("Saved runs & claim details", expanded=False):
            st.caption(f"{len(matching)} saved investigation(s) · latest timestamp {safe_text(matching[0]['snapshot']['record']['updated_at'])}")
            bad_runs = sum(r['snapshot']['audit'] != 'PASS' for r in matching)
            if bad_runs:
                st.error(f"{bad_runs} saved run(s) have failed or unverifiable audit integrity. Select them for inspection; they are not trusted results.")
            keys = [r['key'] for r in matching]
            selected_key = f"saved_run_{dossier}"
            preferred = demo['key'] if demo else interactive.preferred_run(matching, PROJECT)
            # Scenario entry selects the qualified demo; subsequent history choices persist.
            if demo and st.session_state.get('last_demo_scenario') != scenario:
                st.session_state[selected_key] = preferred
            st.session_state['last_demo_scenario'] = scenario
            pending = st.session_state.get('latest_process_database')
            forced = next((r['key'] for r in matching if r['path'] == pending), None)
            if forced:
                st.session_state[selected_key] = forced
                del st.session_state['latest_process_database']
            elif st.session_state.get(selected_key) not in keys:
                st.session_state[selected_key] = preferred
            options = {r['key']: r for r in matching}
            def run_label(k):
                r = options[k]['snapshot']['record']
                return safe_text(f"{r['updated_at']} · {r['run_id']} · {r['status']} · audit {options[k]['snapshot']['audit']} · {Path(options[k]['path']).name}")
            choice = st.selectbox("Saved investigation run", keys, format_func=run_label, key=selected_key)
            snapshot = options[choice]['snapshot']
            if demo and choice != demo['key']:
                st.info('Historical run selected. Its recorded outcome may differ from the featured demonstration.')
            show_claim_details(claim, snapshot)
            st.subheader("View Saved Investigation")
            st.caption("Saved result loaded. No model calls or workflow writes occur when selecting or refreshing this record.")
        state = interactive.freshness(snapshot, PROJECT)
        if state.startswith('Verified'):
            st.caption(state + ". Source provenance does not establish semantic correctness.")
        else:
            st.warning(state + ". Historical findings are preserved; no automatic reinvestigation.")
        if not interactive.is_completed(snapshot):
            st.warning("The stored checkpoint is IN_PROGRESS. Final findings and Validator output may not yet exist.")
    if snapshot["audit"] != "PASS":
        st.error("Audit integrity is " + snapshot["audit"] + ". Records below are displayed for inspection only; their integrity is not established.")
    for issue in snapshot["issues"]:
        st.error(issue)
    if scenario != SCENARIOS[1] and snapshot["financial"]:
        st.info("This database includes a recorded financial workflow. Database selection does not attest whether its investigation was live or simulated; inspect the recorded provenance.")
    with decision_slot:
        show_decision_banner(snapshot)
    tabs = st.tabs(["Investigation Overview", "Evidence & Validation", "Controlled Settlement"])
    with tabs[0]:
        show_overview(snapshot, scenario)
    with tabs[1]:
        show_evidence(snapshot)
    with tabs[2]:
        show_settlement(snapshot, scenario)
    st.html(f'<div class="lt-footer">LossTrace · Hackathon sandbox<span>Snapshot read {html(snapshot["read_at"])} · UTC</span></div>')


if __name__ == "__main__":
    main()
