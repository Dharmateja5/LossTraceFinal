"""F01–F07 regressions. Offline fixtures are NOT observed live model approvals.

All database/dataset mutations are confined to TemporaryDirectory instances.
"""
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from financial import calculate_payable
from financial_stage import FinancialGateError, build_price_review
from gateway import RuntimeGateway
from losstrace_workflow import (
    FinancialWorkflowError, WorkflowConflict, WorkflowStore, authorize_financial, dispatch_financial,
    load_case_from_runtime, main, propose_financial, _financial_record,
    _json, _sha256,
)
from test_step5_complete import DOSSIER, fake_completed_workflow

PROJECT = Path(__file__).resolve().parent
RUNTIME = PROJECT / 'data/application_runtime'


class FinancialFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runtime'
        # Copy only the registered dossier and its configuration; originals stay read-only.
        (self.root / 'manifests').mkdir(parents=True)
        shutil.copy2(RUNTIME / 'manifests' / f'{DOSSIER}.json', self.root / 'manifests')
        shutil.copytree(RUNTIME / 'world' / DOSSIER, self.root / 'world' / DOSSIER)
        shutil.copytree(RUNTIME / 'runtime_config', self.root / 'runtime_config')
        self.store = WorkflowStore(Path(self.tmp.name) / 'sandbox.sqlite')
        self.addCleanup(self.store.close)
        self.state, _ = load_case_from_runtime(DOSSIER, runtime_root=self.root)
        self.rid = self.store.begin(DOSSIER, self.state)
        self.outcome = fake_completed_workflow(self.state)
        self.store.finish(DOSSIER, self.rid, self.outcome.state, self.outcome, 0)
        preview = build_price_review(DOSSIER, self.outcome.state, runtime_root=self.root)
        self.decisions = {
            'reviewer': 'Fixture Adjuster', 'attestation': True,
            'lines': [{
                'line_id': line.line_id,
                'decision': 'SUPPORTED' if line.line_id == 'L1' else 'EXCLUDED',
                'supported_amount': line.capped_candidate if line.line_id == 'L1' else '0.00',
                'reason': 'Synthetic human attestation for control testing only.',
                'support_passages': [{
                    'artifact_id': 'a-61351105b8b94948', 'page': 1,
                    'excerpt': 'Newly opened gypsum is wet at the examination-room return.',
                }] if line.line_id == 'L1' else [],
            } for line in preview.lines],
        }

    def propose(self):
        return propose_financial(self.store, DOSSIER, self.decisions, runtime_root=self.root)

    def approve(self):
        return authorize_financial(self.store, DOSSIER, 'Fixture Supervisor', runtime_root=self.root)

    def dispatch(self):
        return dispatch_financial(self.store, DOSSIER, runtime_root=self.root)


class EvidenceFreshnessTests(FinancialFixture):
    def check_mutation(self, stage, relative, missing=False):
        if stage in {'authorize', 'dispatch'}:
            self.propose()
        if stage == 'dispatch':
            self.approve()
        path = self.root / 'world' / DOSSIER / relative
        if missing:
            path.unlink()
        else:
            path.write_bytes(path.read_bytes() + b'\nChanged after investigation\n')
        with self.assertRaises((FinancialWorkflowError, FinancialGateError)):
            {'propose': self.propose, 'authorize': self.approve, 'dispatch': self.dispatch}[stage]()
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM sandbox_payments').fetchone()[0], 0)
        fin = _financial_record(self.store, DOSSIER)
        if fin:
            self.assertEqual(fin['grant_consumed'], 0)
            self.assertEqual(fin['status'], 'AUTHORIZED' if stage == 'dispatch' else 'AWAITING_AUTHORIZATION')

    def test_changed_service_blocks_proposal(self):
        self.check_mutation('propose', 'evidence/service_report.pdf')

    def test_missing_policy_wording_blocks_authorization(self):
        self.check_mutation('authorize', 'policy/coverage_form.pdf', missing=True)

    def test_changed_service_blocks_dispatch(self):
        self.check_mutation('dispatch', 'evidence/service_report.pdf')

    def test_missing_unselected_email_blocks_proposal(self):
        self.check_mutation('propose', 'correspondence/notice.eml', missing=True)

    def test_changed_unselected_spreadsheet_blocks_authorization(self):
        self.check_mutation('authorize', 'valuation/repair_estimate.xlsx')

    def test_missing_observations_blocks_dispatch(self):
        self.check_mutation('dispatch', 'records/site_observations.json', missing=True)

    def test_every_registered_artifact_is_checked(self):
        manifest = json.loads((self.root / 'manifests' / f'{DOSSIER}.json').read_text())
        for artifact in manifest['artifacts']:
            path = self.root / artifact['path']
            original = path.read_bytes()
            with self.subTest(path=artifact['path']):
                try:
                    path.write_bytes(original + b'changed')
                    with self.assertRaises(FinancialGateError):
                        build_price_review(DOSSIER, self.outcome.state, runtime_root=self.root)
                finally:
                    path.write_bytes(original)

    def test_inconsistent_manifest_identity_blocks_proposal(self):
        path = self.root / 'manifests' / f'{DOSSIER}.json'
        manifest = json.loads(path.read_text())
        manifest['claim_id'] = 'CLM-OTHER'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(FinancialWorkflowError):
            self.propose()

    def test_unchanged_evidence_allows_dispatch(self):
        self.assertEqual(self.propose()['amount'], '12105.00')
        self.approve()
        self.assertEqual(self.dispatch()['status'], 'EXECUTING')

    def test_changed_registered_hash_blocks_authorization(self):
        self.propose()
        path = self.root / 'manifests' / f'{DOSSIER}.json'
        manifest = json.loads(path.read_text())
        manifest['artifacts'][0]['sha256'] = 'b' * 64
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(FinancialWorkflowError, 'persisted evidence snapshot'):
            self.approve()
        self.assertEqual(_financial_record(self.store, DOSSIER)['status'], 'AWAITING_AUTHORIZATION')


class PolicyOrderTests(unittest.TestCase):
    def test_supplied_wording_requires_limit_before_deductible(self):
        wording = RuntimeGateway(str(RUNTIME)).get_policy_wording(DOSSIER)
        text = ' '.join(' '.join(p['text'] for p in wording['pages']).split())
        self.assertIn('Apply available sublimits and remaining coverage-part limits first, then', text)

    def test_limit_and_deductible_boundaries(self):
        cases = [
            ('99999.99', '100000.00', '5000.00', '94999.99'),
            ('100000.00', '100000.00', '5000.00', '95000.00'),
            ('100000.01', '100000.00', '5000.00', '95000.00'),
            ('120000.00', '100000.00', '5000.00', '95000.00'),
            ('1000.00', '100000.00', '5000.00', '0.00'),
            ('120000.00', '3000.00', '5000.00', '0.00'),
            ('4999.99', '500000.00', '5000.00', '0.00'),
            ('5000.00', '500000.00', '5000.00', '0.00'),
            ('5000.01', '500000.00', '5000.00', '0.01'),
            ('500000.01', '500000.00', '5000.00', '495000.00'),
            ('120000.00', '0.00', '5000.00', '0.00'),
            ('120000.00', '100000.00', '0.00', '100000.00'),
        ]
        for gross, limit, deductible, expected in cases:
            with self.subTest(gross=gross, limit=limit):
                result = calculate_payable(Decimal(gross), Decimal('0'), Decimal(deductible), Decimal(limit), Decimal('0'))
                self.assertEqual(result.remaining_payable, Decimal(expected))
                self.assertEqual(result.amount_after_policy_limit, min(Decimal(gross), Decimal(limit)))
                self.assertEqual(result.amount_after_deductible, Decimal(expected))

    def test_op_rounding_then_limit_deductible_and_prior_payment(self):
        result = calculate_payable(Decimal('99999.95'), Decimal('0.10'), Decimal('5000.00'), Decimal('100000.00'), Decimal('1000.00'))
        self.assertEqual(result.o_and_p, Decimal('10000.00'))
        self.assertEqual(result.remaining_payable, Decimal('94000.00'))


class CheckpointAuditTests(FinancialFixture):
    def test_tampered_started_checkpoint_cannot_be_rebound_by_finish_or_fail(self):
        with WorkflowStore(Path(self.tmp.name) / 'tampered-start.sqlite') as other:
            rid = other.begin(DOSSIER, self.state)
            other.connection.execute("UPDATE investigation_workflows SET reason='tampered'")
            other.connection.commit()
            before = other.connection.execute('SELECT * FROM investigation_audit').fetchall()
            with self.assertRaises(WorkflowConflict):
                other.finish(DOSSIER, rid, self.outcome.state, self.outcome, 0)
            with self.assertRaises(WorkflowConflict):
                other.fail(DOSSIER, rid, 'SyntheticFailure')
            self.assertFalse(other.verify_audit(self.state.claim_id)[0])
            self.assertEqual(before, other.connection.execute('SELECT * FROM investigation_audit').fetchall())

    def test_all_completed_checkpoint_fields_are_bound(self):
        original = self.store.get(DOSSIER)
        changes = {
            'dossier_id': 'd-000000000000', 'claim_id': 'CLM-OTHER',
            'run_id': 'replacement-run', 'case_version': 99, 'revision': 99,
            'evidence_hash': 'b'*64, 'status': 'HITL_REQUIRED',
            'next_action': 'HITL_REQUIRED', 'reason': 'Unauthorized change',
            'state_json': '{}', 'validation_json': '{}', 'updated_at': '2000-01-01',
        }
        for field, changed in changes.items():
            with self.subTest(field=field):
                # Column names are hardcoded test constants, never external input.
                self.store.connection.execute(f'UPDATE investigation_workflows SET {field}=?', (changed,))
                self.store.connection.commit()
                try:
                    self.assertFalse(self.store.verify_audit(self.state.claim_id)[0])
                finally:
                    self.store.connection.execute(f'UPDATE investigation_workflows SET {field}=?', (original[field],))
                    self.store.connection.commit()
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])

    def test_started_and_failed_checkpoints_are_bound(self):
        with WorkflowStore(Path(self.tmp.name) / 'unfinished.sqlite') as other:
            rid = other.begin(DOSSIER, self.state)
            for stage in ('started', 'failed'):
                if stage == 'failed':
                    other.fail(DOSSIER, rid, 'SyntheticFailure')
                self.assertTrue(other.verify_audit(self.state.claim_id)[0])
                original = other.get(DOSSIER)['reason']
                other.connection.execute("UPDATE investigation_workflows SET reason='tampered'")
                other.connection.commit()
                with self.subTest(stage=stage):
                    self.assertFalse(other.verify_audit(self.state.claim_id)[0])
                other.connection.execute('UPDATE investigation_workflows SET reason=?', (original,))
                other.connection.commit()

    def test_status_tamper_blocks_financial_proposal_even_when_gate_values_unchanged(self):
        self.store.connection.execute("UPDATE investigation_workflows SET reason='tampered'")
        self.store.connection.commit()
        with self.assertRaises(FinancialWorkflowError):
            self.propose()

    def test_legacy_audit_is_readable_but_cannot_authorize_new_money(self):
        # Reconstruct the original audit format in a disposable DB only.
        previous = None
        for event_id, kind, details_json, timestamp in self.store.connection.execute(
                'SELECT id,event_type,details_json,timestamp FROM investigation_audit ORDER BY id').fetchall():
            details = json.loads(details_json)
            details.pop('checkpoint_sha256', None)
            details.pop('dossier_id', None)
            event = dict(claim_id=self.state.claim_id, event_type=kind, details=details,
                         timestamp=timestamp, previous_hash=previous)
            digest = _sha256(_json(event))
            self.store.connection.execute('UPDATE investigation_audit SET details_json=?, previous_hash=?, event_hash=? WHERE id=?',
                                          (_json(details), previous, digest, event_id))
            previous = digest
        self.store.connection.commit()
        before = self.store.connection.execute('SELECT * FROM investigation_audit').fetchall()
        self.assertEqual(self.store.get(DOSSIER)['status'], 'AWAITING_VALUATION')
        self.assertFalse(self.store.verify_audit(self.state.claim_id)[0])
        with self.assertRaises(FinancialWorkflowError):
            self.propose()
        self.assertEqual(before, self.store.connection.execute('SELECT * FROM investigation_audit').fetchall())


class HistoricalPaymentTests(FinancialFixture):
    def check_ledger(self, change):
        path = self.root / 'world' / DOSSIER / 'records/ledger.json'
        ledger = json.loads(path.read_text())
        change(ledger)
        path.write_text(json.dumps(ledger))
        mp = self.root / 'manifests' / f'{DOSSIER}.json'
        manifest = json.loads(mp.read_text())
        for artifact in manifest['artifacts']:
            if artifact['path'].endswith('/records/ledger.json'):
                artifact['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        mp.write_text(json.dumps(manifest))
        # A new registered input, NOT a stale-manifest test.
        state, _ = load_case_from_runtime(DOSSIER, runtime_root=self.root)
        with self.assertRaises(FinancialGateError):
            build_price_review(DOSSIER, state, runtime_root=self.root)

    def test_paid_entry_with_zero_aggregates_is_blocked(self):
        self.check_ledger(lambda x: x.update(payments=[dict(
            payment_id='p-existing', claim_id=self.state.claim_id, amount='12105.00',
            status='PAID', effect_key='prior-effect', action_hash='a'*64, created_sequence=1)]))

    def test_missing_payment_history_is_blocked(self):
        self.check_ledger(lambda x: x.pop('payments'))

    def test_invalid_payment_history_type_is_blocked(self):
        self.check_ledger(lambda x: x.update(payments={}))

    def test_empty_coverage_usage_map_is_blocked(self):
        self.check_ledger(lambda x: x.update(coverage_used={}))

    def test_missing_sublimit_usage_is_blocked(self):
        self.check_ledger(lambda x: x.pop('sublimit_used'))

    def test_unknown_coverage_usage_is_blocked(self):
        self.check_ledger(lambda x: x['coverage_used'].update(UNRECOGNIZED='0.00'))

    def test_invalid_aggregate_is_blocked(self):
        for value in (None, 0, '-1.00', 'NaN'):
            with self.subTest(value=value):
                self.check_ledger(lambda x: x.update(paid_to_date=value))

    def test_nonzero_aggregate_is_blocked(self):
        self.check_ledger(lambda x: x.update(paid_to_date='1.00'))


class RuntimeAndNarrativeTests(unittest.TestCase):
    def test_equivalent_absolute_default_root_remains_supported(self):
        with tempfile.TemporaryDirectory() as td:
            argv = ['losstrace_workflow.py', '--database', str(Path(td) / 'sandbox.sqlite'),
                    '--runtime-root', str(RUNTIME), 'run', '--dossier', DOSSIER]
            receipt = SimpleNamespace(claim_id='fixture', status='HITL_REQUIRED',
                                      next_action='HITL_REQUIRED', reason='fixture', revision=0, source_checks=0)
            with patch.object(sys, 'argv', argv), \
                    patch('losstrace_workflow.run_case', new=AsyncMock(return_value=receipt)) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                main()
                run.assert_awaited_once()

    def test_alternate_root_rejected_before_database_or_model_work(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'should_not_exist.sqlite'
            argv = ['losstrace_workflow.py', '--database', str(db), '--runtime-root', td,
                    'run', '--dossier', DOSSIER]
            state, _ = load_case_from_runtime(DOSSIER)
            receipt = SimpleNamespace(claim_id=state.claim_id, status='HITL_REQUIRED',
                                      next_action='HITL_REQUIRED', reason='fixture', revision=0, source_checks=0)
            with patch.object(sys, 'argv', argv), patch('losstrace_workflow.load_case_from_runtime', return_value=(state, 21)), \
                    patch('losstrace_workflow.run_case', new=AsyncMock(return_value=receipt)) as run, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    main()
                self.assertEqual(stopped.exception.code, 2)
                run.assert_not_called()
            self.assertFalse(db.exists())

    def test_readme_explicitly_distinguishes_live_and_fixture_routes(self):
        text = (PROJECT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('Live investigations always stop at HITL', text)
        self.assertIn('Supported finance is fixture-backed', text)


class ExpiryTests(FinancialFixture):
    def test_expiry_without_audit_tampering(self):
        self.propose()
        self.approve()
        expiration = datetime.fromisoformat(_financial_record(self.store, DOSSIER)['authorized_until'])
        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return expiration + timedelta(seconds=1)
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])
        with patch('losstrace_workflow.datetime', ExpiredClock):
            with self.assertRaisesRegex(FinancialWorkflowError, 'Authorization expired'):
                self.dispatch()
        self.assertTrue(self.store.verify_audit(self.state.claim_id)[0])
        self.assertEqual(_financial_record(self.store, DOSSIER)['grant_consumed'], 0)
