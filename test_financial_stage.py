"""Checkpoint 5A: source authenticity, financial scenario and fail-closed gates.

All outcomes here are offline. Mocked supported-claim workflow states are
TEST FIXTURES, not claims about completed real investigations.
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from adaptive_routing import RoutingAction, RoutingDecision
from case import merge_finding
from financial_stage import (
    FinancialGateError, build_gated_financial_preview, build_price_review,
)
from losstrace_workflow import WorkflowStore, load_case_from_runtime
from models import SpecialistFinding, SpecialistRole, SpecialistStatus
from validated_coordinator import ValidatedInvestigation
from validation_agent import ValidationOutcome, ValidationResult, SemanticCheck


CURRENT = 'd-11cd8b30545e'
DISPUTED_PRICE = 'd-13b90651932a'
RUNTIME = Path('data/application_runtime')


def fake_completed_workflow(state, *, outcome=ValidationOutcome.PROVISIONAL_SUPPORT):
    merged = state
    for role in (SpecialistRole.COVERAGE, SpecialistRole.EVIDENCE):
        status = (SpecialistStatus.SUFFICIENT if outcome == ValidationOutcome.PROVISIONAL_SUPPORT
                  else SpecialistStatus.INSUFFICIENT)
        finding = SpecialistFinding(
            specialist=role, status=status,
            basis_case_version=state.case_version,
            basis_evidence_hash=state.evidence_snapshot_hash,
        )
        merge_outcome, merged = merge_finding(merged, finding, expected_revision=merged.revision)
        assert merge_outcome == 'ACCEPTED'
    result = ValidationResult(
        outcome=outcome,
        basis_case_version=merged.case_version,
        basis_evidence_hash=merged.evidence_snapshot_hash,
        basis_revision=merged.revision,
        source_checks=9,
        semantic_checks=(SemanticCheck(key="evidence:1", verdict="SUPPORTED", reason="Source confirms observation"),),
        relationship_reported="CORROBORATING",
        relationship_validated="CORROBORATING",
    )
    next_action = (RoutingAction.NEXT_VALUATION
                   if outcome == ValidationOutcome.PROVISIONAL_SUPPORT
                   else RoutingAction.HITL_REQUIRED)
    workflow = ValidatedInvestigation(
        state=merged,
        next_decision=RoutingDecision(next_action, (), 'Test fixture'),
        validation=result, investigations=(), followups_used=0,
    )
    return workflow


class FinancialStageTests(unittest.TestCase):
    def setUp(self):
        self.state, _ = load_case_from_runtime(CURRENT)

    def test_real_registered_cost_review_without_execution(self):
        result = build_price_review(CURRENT, self.state)
        self.assertEqual(len(result.lines), 7)
        self.assertEqual(result.price_opinion_base, '81000.00')
        self.assertEqual(result.conditional_o_and_p, '8100.00')
        self.assertEqual(result.conditional_amount, '84100.00')
        self.assertEqual(result.financial_action_permitted, False)

    def test_independent_price_reductions_and_threshold(self):
        state, _ = load_case_from_runtime(DISPUTED_PRICE)
        result = build_price_review(DISPUTED_PRICE, state)
        self.assertEqual(result.price_opinion_base, '480000.00')
        self.assertEqual(result.conditional_amount, '455000.00')
        self.assertTrue(result.authority_review_signal)
        self.assertTrue(all(float(l.capped_candidate) <= float(l.submitted) for l in result.lines))

    def test_invoices_deliberately_excluded(self):
        result = build_price_review(CURRENT, self.state)
        self.assertTrue(all(l.line_id.startswith('L') for l in result.lines))
        self.assertNotIn('invoice_01', str(result.lines))

    def test_old_case_version_blocked(self):
        stale = self.state.model_copy(update={'case_version': self.state.case_version - 1})
        with self.assertRaisesRegex(FinancialGateError, 'Claim version'):
            build_price_review(CURRENT, stale)

    def test_manifest_basis_mismatch_blocked(self):
        stale = self.state.model_copy(update={'evidence_snapshot_hash': 'a'*64})
        with self.assertRaisesRegex(FinancialGateError, 'snapshot'):
            build_price_review(CURRENT, stale)

    def test_historical_hitl_status_blocks_financial_preview(self):
        record = {'status': 'HITL_REQUIRED', 'next_action': 'HITL_REQUIRED'}
        with self.assertRaisesRegex(FinancialGateError, 'not eligible'):
            build_gated_financial_preview(CURRENT, record, audit_integrity=True)

    def test_forged_status_cannot_override_audit_integrity(self):
        record = {'status': 'AWAITING_VALUATION', 'next_action': 'NEXT_VALUATION'}
        with self.assertRaisesRegex(FinancialGateError, 'Audit integrity'):
            build_gated_financial_preview(CURRENT, record, audit_integrity=False)

    def test_missing_validation_result_blocked(self):
        record = {
            'status': 'AWAITING_VALUATION', 'next_action': 'NEXT_VALUATION',
            'claim_id': self.state.claim_id, 'case_version': self.state.case_version,
            'revision': self.state.revision,
            'evidence_hash': self.state.evidence_snapshot_hash,
            'state_json': self.state.model_dump_json(),
            'validation_json': None,
        }
        with self.assertRaisesRegex(FinancialGateError, 'validation missing'):
            build_gated_financial_preview(CURRENT, record, audit_integrity=True)

    def test_inconclusive_semantic_review_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            with WorkflowStore(str(Path(folder)/'workflow.db')) as store:
                run_id = store.begin(CURRENT, self.state)
                outcome = fake_completed_workflow(self.state)
                store.finish(CURRENT, run_id, outcome.state, outcome, self.state.revision)
                record = store.get(CURRENT)
                review = json.loads(record['validation_json'])
                review['semantic_checks'][0]['verdict'] = 'INCONCLUSIVE'
                record['validation_json'] = json.dumps(review)
                with self.assertRaisesRegex(FinancialGateError, 'semantic validation'):
                    build_gated_financial_preview(CURRENT, record, audit_integrity=True)

    def test_relationship_disagreement_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            with WorkflowStore(str(Path(folder)/'workflow.db')) as store:
                run_id = store.begin(CURRENT, self.state)
                outcome = fake_completed_workflow(self.state)
                store.finish(CURRENT, run_id, outcome.state, outcome, self.state.revision)
                record = store.get(CURRENT)
                review = json.loads(record['validation_json'])
                review['relationship_validated'] = 'POTENTIAL_CONFLICT'
                record['validation_json'] = json.dumps(review)
                with self.assertRaisesRegex(FinancialGateError, 'Cross-document'):
                    build_gated_financial_preview(CURRENT, record, audit_integrity=True)

    def test_real_source_hash_tampering_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'runtime'
            (root / 'manifests').mkdir(parents=True)
            shutil.copytree(RUNTIME / 'world' / CURRENT, root / 'world' / CURRENT)
            shutil.copyfile(RUNTIME / 'manifests' / (CURRENT + '.json'),
                            root / 'manifests' / (CURRENT + '.json'))
            original = RUNTIME / 'world' / CURRENT / 'valuation' / 'repair_estimate.pdf'
            target = root / 'world' / CURRENT / 'valuation' / 'repair_estimate.pdf'
            target.write_bytes(original.read_bytes() + b'rogue')
            with self.assertRaisesRegex(FinancialGateError, 'SHA-256 mismatch'):
                build_price_review(CURRENT, self.state, runtime_root=root)

    def test_modified_review_amount_without_estimate_agreement_blocked(self):
        from financial_stage import _pdf_text
        calls = []
        def tampered_pdf(data):
            text = _pdf_text(data)
            calls.append(text)
            if len(calls) == 2:
                return text.replace('Submitted line: $15,550.00.', 'Submitted line: $15,551.00.')
            return text
        with patch('financial_stage._pdf_text', side_effect=tampered_pdf):
            with self.assertRaisesRegex(FinancialGateError, 'disagree'):
                build_price_review(CURRENT, self.state)

    def test_supported_mock_workflow_can_read_only_preview(self):
        with tempfile.TemporaryDirectory() as folder:
            with WorkflowStore(str(Path(folder)/'workflow.db')) as store:
                run_id = store.begin(CURRENT, self.state)
                outcome = fake_completed_workflow(self.state)
                receipt = store.finish(CURRENT, run_id, outcome.state, outcome, self.state.revision)
                self.assertEqual(receipt.status, 'AWAITING_VALUATION')
                record = store.get(CURRENT)
                integrity, events = store.verify_audit(self.state.claim_id)
                self.assertTrue(integrity)
                self.assertEqual(events, 2)
                result = build_gated_financial_preview(CURRENT, record, audit_integrity=integrity)
                self.assertEqual(result.conditional_amount, '84100.00')
                self.assertFalse(result.financial_action_permitted)
                self.assertEqual(store.get(CURRENT)['status'], 'AWAITING_VALUATION')
                self.assertEqual(store.verify_audit(self.state.claim_id)[1], 2)

    def test_insufficient_mock_workflow_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            with WorkflowStore(str(Path(folder)/'workflow.db')) as store:
                run_id = store.begin(CURRENT, self.state)
                outcome = fake_completed_workflow(self.state, outcome=ValidationOutcome.HITL_REQUIRED)
                store.finish(CURRENT, run_id, outcome.state, outcome, self.state.revision)
                integrity, _ = store.verify_audit(self.state.claim_id)
                with self.assertRaises(FinancialGateError):
                    build_gated_financial_preview(CURRENT, store.get(CURRENT), audit_integrity=integrity)

    def test_stale_validator_revision_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            with WorkflowStore(str(Path(folder)/'workflow.db')) as store:
                run_id = store.begin(CURRENT, self.state)
                outcome = fake_completed_workflow(self.state)
                store.finish(CURRENT, run_id, outcome.state, outcome, self.state.revision)
                record = store.get(CURRENT)
                review = json.loads(record['validation_json'])
                review['basis_revision'] -= 1
                record['validation_json'] = json.dumps(review)
                with self.assertRaisesRegex(FinancialGateError, 'stale'):
                    build_gated_financial_preview(CURRENT, record, audit_integrity=True)


if __name__ == '__main__':
    unittest.main()
