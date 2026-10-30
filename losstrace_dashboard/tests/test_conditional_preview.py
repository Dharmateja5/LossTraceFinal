"""Regression checks for the UI-only, fail-closed conditional preview adapter.

Does not start Streamlit, call Nebius or mutate any demonstration database.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / 'losstrace_dashboard'
for folder in (ROOT, UI):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from case import CanonicalCaseState
from validation_agent import ValidationResult
from financial_stage import FinancialGateError
from financial_preview import gated_conditional_preview

DOSSIER = 'd-e003cb27d820'
POSITIVE_DB = ROOT / 'data/demo_databases/scenario3-positive-live.sqlite3'
RUNTIME = ROOT / 'data/application_runtime'


def saved_snapshot():
    with sqlite3.connect(POSITIVE_DB.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        record = conn.execute('SELECT * FROM investigation_workflows WHERE dossier_id=?', (DOSSIER,)).fetchone()
        if record is None:
            raise AssertionError('Saved positive demonstration is missing')
        record = dict(record)
    return dict(record=record, audit='PASS', issues=[], financial=None,
                state=CanonicalCaseState.model_validate_json(record['state_json']),
                validation=ValidationResult.model_validate_json(record['validation_json']))


@unittest.skipUnless(POSITIVE_DB.exists(), 'Preserved positive database not distributed with public-source build')
class ConditionalPreviewTests(unittest.TestCase):
    def test_positive_preview_reads_real_sources_without_mutating_database(self):
        before = sha256(POSITIVE_DB.read_bytes()).hexdigest()
        result, error = gated_conditional_preview(saved_snapshot(), runtime_root=RUNTIME)
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(result.conditional_amount, '91250.00')
        self.assertEqual(result.price_opinion_base, '87500.00')
        self.assertEqual(result.conditional_o_and_p, '8750.00')
        self.assertEqual(result.policy_deductible, '5000.00')
        self.assertEqual(len(result.lines), 7)
        self.assertFalse(result.financial_action_permitted)
        self.assertEqual(sha256(POSITIVE_DB.read_bytes()).hexdigest(), before)

    def test_hitl_and_synthetic_finance_do_not_receive_positive_preview(self):
        for changes in (
            {'record': dict(saved_snapshot()['record'], status='HITL_REQUIRED')},
            {'financial': {'status':'VERIFIED'}},
            {'record': dict(saved_snapshot()['record'], next_action='HITL_REQUIRED')},
        ):
            snap = saved_snapshot()
            snap.update(changes)
            with self.subTest(changes=changes), patch('financial_preview.build_gated_financial_preview') as build:
                self.assertEqual(gated_conditional_preview(snap, runtime_root=RUNTIME), (None, None))
                build.assert_not_called()

    def test_bad_audit_or_missing_validator_fail_closed(self):
        for changes in ({'audit':'FAIL'}, {'issues':['checkpoint mismatch']}, {'validation':None}):
            snap = saved_snapshot()
            snap.update(changes)
            with self.subTest(changes=changes), patch('financial_preview.build_gated_financial_preview') as build:
                result, error = gated_conditional_preview(snap, runtime_root=RUNTIME)
                self.assertIsNone(result)
                self.assertTrue(error)
                build.assert_not_called()

    def test_backend_gate_error_never_produces_an_amount(self):
        with patch('financial_preview.build_gated_financial_preview', side_effect=FinancialGateError('Source SHA-256 mismatch')):
            result, error = gated_conditional_preview(saved_snapshot(), runtime_root=RUNTIME)
        self.assertIsNone(result)
        self.assertIn('Source SHA-256 mismatch', error)


if __name__ == '__main__':
    unittest.main()
