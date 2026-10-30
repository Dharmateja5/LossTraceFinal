"""Step 5B–5D integration tests (mock supported investigation; real registered PDFs).

Never interpret these fixtures as observed live model success or real claim approval.
"""
import json
import sqlite3
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from case import CanonicalCaseState
from financial_stage import build_price_review
from losstrace_workflow import (
    FinancialWorkflowError, WorkflowStore, authorize_financial, dispatch_financial,
    mark_execution_status, propose_financial, reconcile_financial, sandbox_apply,
    verify_financial, load_case_from_runtime, _financial_record,
)
from case import merge_finding
from adaptive_routing import RoutingAction, RoutingDecision
from models import SpecialistFinding, SpecialistRole, SpecialistStatus
from validated_coordinator import ValidatedInvestigation
from validation_agent import ValidationOutcome, ValidationResult, SemanticCheck


def fake_completed_workflow(state, *, outcome=ValidationOutcome.PROVISIONAL_SUPPORT):
    """Synthetic supported/HITL fixture, NOT an observed real model result."""
    merged = state
    for role in (SpecialistRole.COVERAGE, SpecialistRole.EVIDENCE):
        finding = SpecialistFinding(
            specialist=role,
            status=SpecialistStatus.SUFFICIENT if outcome == ValidationOutcome.PROVISIONAL_SUPPORT
                   else SpecialistStatus.INSUFFICIENT,
            basis_case_version=state.case_version,
            basis_evidence_hash=state.evidence_snapshot_hash,
        )
        m, merged = merge_finding(merged, finding, expected_revision=merged.revision)
        assert m == 'ACCEPTED'
    validation = ValidationResult(
        outcome=outcome,
        basis_case_version=merged.case_version,
        basis_evidence_hash=merged.evidence_snapshot_hash,
        basis_revision=merged.revision,
        source_checks=9,
        semantic_checks=(SemanticCheck(key='evidence:1', verdict='SUPPORTED',
                                       reason='Synthetic validator fixture'),),
        relationship_reported='CORROBORATING',
        relationship_validated='CORROBORATING',
    )
    action = RoutingAction.NEXT_VALUATION if outcome == ValidationOutcome.PROVISIONAL_SUPPORT \
             else RoutingAction.HITL_REQUIRED
    return ValidatedInvestigation(
        state=merged,
        next_decision=RoutingDecision(action, (), 'Synthetic test fixture'),
        validation=validation, investigations=(), followups_used=0,
    )

DOSSIER = 'd-11cd8b30545e'
SURVEY = 'a-61351105b8b94948'


class IntegratedFinancialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowStore(Path(self.tmp.name) / 'sandbox.sqlite')
        self.addCleanup(self.store.close)
        self.state, _ = load_case_from_runtime(DOSSIER)
        run_id = self.store.begin(DOSSIER, self.state)
        self.outcome = fake_completed_workflow(self.state)
        self.store.finish(DOSSIER, run_id, self.outcome.state, self.outcome, self.state.revision)
        self.opinion = build_price_review(DOSSIER, self.outcome.state)
        self.decisions = dict(
            reviewer='Adjuster Example', attestation=True,
            lines=[{
                'line_id': line.line_id,
                'decision': 'SUPPORTED' if line.line_id == 'L1' else 'EXCLUDED',
                'supported_amount': line.capped_candidate if line.line_id == 'L1' else '0.00',
                'reason': 'Human reviewed damage and coverage basis; illustrative attestation only.'
                          if line.line_id == 'L1' else 'Not claimed as supported under this example.',
                'support_passages': [{
                    'artifact_id': SURVEY, 'page': 1,
                    'excerpt': 'Newly opened gypsum is wet at the examination-room return.',
                }] if line.line_id == 'L1' else [],
            } for line in self.opinion.lines],
        )

    def _proposal(self):
        return propose_financial(self.store, DOSSIER, self.decisions)

    def _approved(self):
        self._proposal()
        return authorize_financial(self.store, DOSSIER, 'Supervisor Example')

    def test_supported_sandbox_end_to_end(self):
        p = self._proposal()
        self.assertEqual(p['status'], 'AWAITING_AUTHORIZATION')
        self.assertEqual(p['amount'], '12105.00')
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])
        self.assertIsNone(self.store.connection.execute('SELECT amount FROM sandbox_payments').fetchone())
        a = authorize_financial(self.store, DOSSIER, 'Supervisor Example')
        self.assertEqual(a['status'], 'AUTHORIZED')
        dispatch_financial(self.store, DOSSIER)
        sandbox_apply(self.store, DOSSIER)
        mark_execution_status(self.store, DOSSIER, 'EXECUTED')
        v = verify_financial(self.store, DOSSIER)
        self.assertEqual(v['status'], 'VERIFIED')
        self.assertEqual(v['actual_amount'], '12105.00')
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM sandbox_payments').fetchone()[0], 1)
        with self.assertRaises(FinancialWorkflowError):
            dispatch_financial(self.store, DOSSIER)

    def test_timeout_after_apply_reconciles_without_retry(self):
        self._approved()
        dispatch_financial(self.store, DOSSIER)
        sandbox_apply(self.store, DOSSIER)
        mark_execution_status(self.store, DOSSIER, 'UNKNOWN_EXECUTION')
        self.assertEqual(reconcile_financial(self.store, DOSSIER)['status'], 'EXECUTED')
        self.assertTrue(verify_financial(self.store, DOSSIER)['verified'])
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM sandbox_payments').fetchone()[0], 1)

    def test_timeout_before_apply_requires_hitl_no_retry(self):
        self._approved()
        dispatch_financial(self.store, DOSSIER)
        mark_execution_status(self.store, DOSSIER, 'UNKNOWN_EXECUTION')
        self.assertEqual(reconcile_financial(self.store, DOSSIER)['status'], 'HITL_REQUIRED')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM sandbox_payments').fetchone()[0], 0)
        with self.assertRaises(FinancialWorkflowError):
            dispatch_financial(self.store, DOSSIER)

    def test_crash_after_apply_is_reconcilable_from_executing(self):
        self._approved()
        dispatch_financial(self.store, DOSSIER)
        sandbox_apply(self.store, DOSSIER)
        # Simulate restart before a response/status update.
        self.assertEqual(reconcile_financial(self.store, DOSSIER)['status'], 'EXECUTED')
        self.assertTrue(verify_financial(self.store, DOSSIER)['verified'])

    def test_case_at_hitl_cannot_propose(self):
        from validation_agent import ValidationOutcome
        other = WorkflowStore(Path(self.tmp.name) / 'hitl.sqlite')
        self.addCleanup(other.close)
        rid = other.begin(DOSSIER, self.state)
        outcome = fake_completed_workflow(self.state, outcome=ValidationOutcome.HITL_REQUIRED)
        other.finish(DOSSIER, rid, outcome.state, outcome, self.state.revision)
        with self.assertRaises(FinancialWorkflowError):
            propose_financial(other, DOSSIER, self.decisions)

    def test_no_fabricated_approval_or_new_autopayment(self):
        self._proposal()
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM sandbox_payments').fetchone()[0], 0)
        self.assertEqual(_financial_record(self.store, DOSSIER)['approved_by'], None)

    def test_insufficient_line_details_block(self):
        self.decisions['lines'][0]['supported_amount'] = '999999.00'
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

    def test_unknown_support_source_block(self):
        self.decisions['lines'][0]['support_passages'][0]['artifact_id'] = 'a-nonexistent'
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

    def test_price_only_source_block(self):
        self.decisions['lines'][0]['support_passages'][0]['artifact_id'] = self.opinion.review_artifact_id
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

    def test_no_source_excerpt_blocks_financial_proposal(self):
        self.decisions['lines'][0]['support_passages'] = []
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

    def test_fabricated_quote_blocks_financial_proposal(self):
        self.decisions['lines'][0]['support_passages'][0]['excerpt'] = (
            'All damaged drywall was fully covered with no prior conditions.'
        )
        with self.assertRaisesRegex(FinancialWorkflowError, 'not on the cited page'):
            self._proposal()

    def test_wrong_quote_page_blocks_financial_proposal(self):
        self.decisions['lines'][0]['support_passages'][0]['page'] = 3
        with self.assertRaisesRegex(FinancialWorkflowError, 'not on the cited page'):
            self._proposal()

    def test_quote_passage_must_have_page(self):
        del self.decisions['lines'][0]['support_passages'][0]['page']
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

    def test_no_self_approval(self):
        self._proposal()
        with self.assertRaises(FinancialWorkflowError):
            authorize_financial(self.store, DOSSIER, 'Adjuster Example')

    def test_grant_expiration_blocks_execution(self):
        self._approved()
        expiration = datetime.fromisoformat(_financial_record(self.store, DOSSIER)['authorized_until'])
        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return expiration
        # Reach the expiry branch with an intact audit, including equality at expiry.
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])
        with patch('losstrace_workflow.datetime', ExpiredClock):
            with self.assertRaisesRegex(FinancialWorkflowError, 'Authorization expired'):
                dispatch_financial(self.store, DOSSIER)
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])
        self.assertEqual(_financial_record(self.store, DOSSIER)['grant_consumed'], 0)

    def test_proposal_tamper_detected_in_audit(self):
        self._proposal()
        self.store.connection.execute("UPDATE financial_workflows SET proposal_json='{}'")
        self.store.connection.commit()
        self.assertFalse(self.store.verify_audit(self.state.claim_id)[0])
        with self.assertRaises(FinancialWorkflowError):
            authorize_financial(self.store, DOSSIER, 'Supervisor Example')

    def test_ledger_tamper_detected(self):
        self._approved()
        dispatch_financial(self.store, DOSSIER)
        sandbox_apply(self.store, DOSSIER)
        mark_execution_status(self.store, DOSSIER, 'EXECUTED')
        self.store.connection.execute("UPDATE sandbox_payments SET amount='999.00'")
        self.store.connection.commit()
        self.assertFalse(self.store.verify_audit(self.state.claim_id)[0])
        with self.assertRaises(FinancialWorkflowError):
            verify_financial(self.store, DOSSIER)

    def test_duplicate_proposal_rejected(self):
        self._proposal()
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

    def test_approval_is_audited(self):
        self._approved()
        count = self.store.connection.execute("SELECT COUNT(*) FROM investigation_audit").fetchone()[0]
        self.assertEqual(count, 4)
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])

    def test_cli_commands_execute_full_sandbox_path(self):
        dbpath = str(Path(self.tmp.name) / 'sandbox.sqlite')
        decision_path = Path(self.tmp.name) / 'decision.json'
        decision_path.write_text(json.dumps(self.decisions), encoding='utf-8')

        def command(*args):
            return subprocess.run(
                [sys.executable, 'losstrace_workflow.py', '--database', dbpath, *args],
                cwd=Path(__file__).parent, text=True, capture_output=True,
            )

        create = command('propose-financial', '--dossier', DOSSIER,
                         '--decisions', str(decision_path))
        self.assertEqual(create.returncode, 0, create.stderr)
        authorize = command('authorize', '--dossier', DOSSIER,
                            '--approved-by', 'Supervisor Example', '--confirm')
        self.assertEqual(authorize.returncode, 0, authorize.stderr)
        execute = command('execute', '--dossier', DOSSIER, '--confirm',
                          '--simulate-timeout-after-apply')
        self.assertEqual(execute.returncode, 0, execute.stderr)
        self.assertIn('UNKNOWN_EXECUTION', execute.stdout)
        reconcile = command('reconcile', '--dossier', DOSSIER)
        self.assertEqual(reconcile.returncode, 0, reconcile.stderr)
        self.assertIn('EXECUTED', reconcile.stdout)
        verify = command('verify', '--dossier', DOSSIER)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn('VERIFIED', verify.stdout)
        audit = command('audit', '--dossier', DOSSIER)
        self.assertEqual(audit.returncode, 0, audit.stderr)
        self.assertIn('Audit integrity: PASS', audit.stdout)
        repeat = command('execute', '--dossier', DOSSIER, '--confirm')
        self.assertNotEqual(repeat.returncode, 0)

    def test_no_execution_without_explicit_approval(self):
        self._proposal()
        with self.assertRaises(FinancialWorkflowError):
            dispatch_financial(self.store, DOSSIER)

    def test_real_authority_threshold_blocks_authorization(self):
        # Independent price review on this benchmark dossier exceeds $250k.
        other_dossier = 'd-13b90651932a'
        state, _ = load_case_from_runtime(other_dossier)
        tmp_store = WorkflowStore(Path(self.tmp.name) / 'high_amount.sqlite')
        self.addCleanup(tmp_store.close)
        rid = tmp_store.begin(other_dossier, state)
        completed = fake_completed_workflow(state)
        tmp_store.finish(other_dossier, rid, completed.state, completed, state.revision)
        review = build_price_review(other_dossier, completed.state)
        decisions = {
            'reviewer': 'Adjuster Example', 'attestation': True,
            'lines': [{
                'line_id': line.line_id, 'decision': 'SUPPORTED',
                'supported_amount': line.capped_candidate,
                'reason': 'Human attested every reviewed line for policy threshold test.',
                'support_passages': [{
                    'artifact_id': 'a-58578006f075fc6a', 'page': 1,
                    'excerpt': 'North-wing consulting rooms have saturated wall bases and detached sheet-floor seams.',
                }],
            } for line in review.lines],
        }
        proposal = propose_financial(tmp_store, other_dossier, decisions)
        self.assertEqual(proposal['status'], 'HITL_REQUIRED')
        self.assertIn('AUTHORITY_THRESHOLD_EXCEEDED', proposal['policy_reasons'])
        with self.assertRaises(FinancialWorkflowError):
            authorize_financial(tmp_store, other_dossier, 'Supervisor Example')

    def test_all_excluded_cannot_create_action(self):
        for entry in self.decisions['lines']:
            entry['decision'] = 'EXCLUDED'
            entry['supported_amount'] = '0.00'
            entry['support_passages'] = []
            entry['reason'] = 'Reviewed line and excluded from this example.'
        with self.assertRaises(FinancialWorkflowError):
            self._proposal()

if __name__ == '__main__':
    unittest.main()
