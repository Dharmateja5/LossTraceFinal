"""UI-only regression tests. All data here is synthetic; no live model calls.

Existing backend tests and code are imported, never modified. Every test database
is disposable. No fixture is created by app.py or bundled as a demonstration DB.
"""
import hashlib
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
spec = importlib.util.spec_from_file_location("dashboard_under_test", ROOT / "app.py")
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)

from losstrace_workflow import (
    WorkflowStore, load_case_from_runtime, dispatch_financial, sandbox_apply,
    mark_execution_status, reconcile_financial, verify_financial,
)
import test_step5_complete as backend_fixtures
from test_step5_complete import fake_completed_workflow, DOSSIER
from validation_agent import ValidationOutcome
from streamlit.testing.v1 import AppTest


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def isolate_ui_history(test):
    """AppTest fixtures cannot accidentally select the operator's saved databases."""
    original_paths = ui.interactive.database_paths
    original_state = ui.interactive.launch_state
    original_receipts = ui.interactive.result_receipts
    def paths(configured, project=ui.interactive.PROJECT):
        if Path(project).resolve() == ROOT.parent.resolve():
            return list(dict.fromkeys(Path(p).expanduser().resolve() for p in configured if str(p).strip()))
        return original_paths(configured, project)
    for name, side_effect in (
        ('database_paths', paths),
        ('launch_state', lambda project=ui.interactive.PROJECT: None if Path(project).resolve() == ROOT.parent.resolve() else original_state(project)),
        ('result_receipts', lambda project=ui.interactive.PROJECT: [] if Path(project).resolve() == ROOT.parent.resolve() else original_receipts(project)),
    ):
        patcher = patch.object(ui.interactive, name, side_effect=side_effect)
        patcher.start()
        test.addCleanup(patcher.stop)


def hitl_fixture(path, source_checks=9):
    """Controlled UI data only, NOT genuine Nebius execution."""
    state, _ = load_case_from_runtime(DOSSIER)
    outcome = fake_completed_workflow(state, outcome=ValidationOutcome.HITL_REQUIRED)
    outcome = replace(outcome, validation=outcome.validation.model_copy(update={'source_checks': source_checks}))
    with WorkflowStore(path) as store:
        run = store.begin(DOSSIER, state)
        store.finish(DOSSIER, run, outcome.state, outcome, 0)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'view.sqlite3'
        hitl_fixture(self.db)

    def test_missing_database_and_parent_are_not_created(self):
        path = Path(self.tmp.name) / 'missing' / 'uncreated.sqlite3'
        with self.assertRaises(ui.ReadError):
            ui.list_cases(path)
        self.assertFalse(path.parent.exists())

    def test_empty_database_remains_empty(self):
        path = Path(self.tmp.name) / 'empty.sqlite3'
        with WorkflowStore(path):
            pass
        before = file_hash(path)
        self.assertEqual(ui.list_cases(path), [])
        self.assertEqual(before, file_hash(path))

    def test_wrong_schema_is_not_migrated(self):
        path = Path(self.tmp.name) / 'unrelated.sqlite3'
        with sqlite3.connect(path) as c:
            c.execute('CREATE TABLE unrelated (id INTEGER)')
        before = file_hash(path)
        with self.assertRaises(ui.ReadError):
            ui.list_cases(path)
        self.assertEqual(before, file_hash(path))

    def test_unreadable_input_has_sanitized_error(self):
        path = Path(self.tmp.name) / 'invalid.sqlite3'
        path.write_text('NEBIUS_API_KEY=private-test-canary', encoding='utf-8')
        with self.assertRaises(ui.ReadError) as error:
            ui.list_cases(path)
        self.assertNotIn('private-test-canary', str(error.exception))

    def test_backend_constructor_is_never_called_by_reader(self):
        with patch.object(WorkflowStore, '__init__', side_effect=AssertionError('Mutating constructor called')):
            self.assertEqual(ui.read_snapshot(self.db, DOSSIER)['audit'], 'PASS')

    def test_mutations_and_attach_are_denied(self):
        before = file_hash(self.db)
        with ui.open_reader(self.db) as store:
            for sql in (
                "UPDATE investigation_workflows SET reason='illegal'",
                "DELETE FROM investigation_audit",
                "CREATE TABLE illegal (id INTEGER)",
                "INSERT INTO sandbox_payments VALUES ('x','x','x','x','1.00','now')",
                "ATTACH DATABASE ':memory:' AS illegal",
                "PRAGMA query_only=OFF",
            ):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    store.connection.execute(sql)
        self.assertEqual(before, file_hash(self.db))

    def test_audit_and_fields_use_actual_record(self):
        snapshot = ui.read_snapshot(self.db, DOSSIER)
        self.assertEqual(snapshot['record']['status'], 'HITL_REQUIRED')
        self.assertEqual(snapshot['validation'].source_checks, 9)
        self.assertEqual(snapshot['audit'], 'PASS')
        self.assertEqual(snapshot['audit_count'], 2)
        self.assertEqual(snapshot['total_writes'], 0)
        self.assertIsNone(snapshot['financial'])
        self.assertEqual(dict(ui.recorded_indicators(snapshot))['Investigation status'], 'HITL_REQUIRED')
        self.assertEqual(dict(ui.recorded_indicators(snapshot))['Source checks'], 9)

    def test_source_count_is_not_forced_to_nine(self):
        path = Path(self.tmp.name) / 'six.sqlite3'
        hitl_fixture(path, 6)
        snapshot = ui.read_snapshot(path, DOSSIER)
        self.assertEqual(snapshot['validation'].source_checks, 6)
        self.assertEqual(dict(ui.recorded_indicators(snapshot))['Source checks'], 6)

    def test_tampered_checkpoint_audit_fails(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE investigation_workflows SET reason='Changed outside the audit'")
        self.assertEqual(ui.read_snapshot(self.db, DOSSIER)['audit'], 'FAIL')

    def test_bad_json_is_not_silently_replaced_with_results(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE investigation_workflows SET state_json='{}',validation_json='{}'")
        s = ui.read_snapshot(self.db, DOSSIER)
        self.assertIsNone(s['state'])
        self.assertIsNone(s['validation'])
        self.assertEqual(s['audit'], 'FAIL')
        self.assertEqual(len(s['issues']), 2)

    def test_repeated_reads_do_not_mutate_database(self):
        before = file_hash(self.db)
        for _ in range(4):
            ui.list_cases(self.db)
            ui.read_snapshot(self.db, DOSSIER)
        self.assertEqual(before, file_hash(self.db))

    def test_wal_commits_are_visible_on_next_snapshot(self):
        with sqlite3.connect(self.db) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            self.assertEqual(ui.read_snapshot(self.db, DOSSIER)['audit'], 'PASS')
            writer.execute("UPDATE investigation_workflows SET reason='Writer committed a changed reason'")
            writer.commit()
            self.assertEqual(ui.read_snapshot(self.db, DOSSIER)['audit'], 'FAIL')

    def test_display_escapes_markup_and_redacts_credentials(self):
        value = '<script>alert(1)</script> NEBIUS_API_KEY=canary-secret-value Bearer secret-token-value'
        safe = ui.html(value)
        self.assertNotIn('<script>', safe)
        self.assertNotIn('canary-secret-value', safe)
        self.assertNotIn('secret-token-value', safe)
        self.assertIn('&lt;script&gt;', safe)

    def test_passage_parsing_preserves_stored_quote_and_reference(self):
        from test_investigation_paths import source_fixture
        _, state, _, _ = source_fixture()
        raw = state.specialist_findings[0].findings[0]
        parsed = ui.parse_passage(raw)
        self.assertIsNotNone(parsed)
        self.assertIn(parsed['quote'], raw)
        self.assertTrue(parsed['passage'])
        self.assertTrue(parsed['artifact'].startswith('a-'))
        self.assertIsNone(ui.parse_passage('No passage was recorded'))


class DashboardSmokeTests(unittest.TestCase):
    def setUp(self):
        isolate_ui_history(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.live = Path(self.tmp.name) / 'test-hitl.sqlite3'
        self.finance = Path(self.tmp.name) / 'missing-finance.sqlite3'
        hitl_fixture(self.live)

    def app(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
        app.session_state['live_path'] = str(self.live)
        app.session_state['finance_path'] = str(self.finance)
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def test_hitl_render_refresh_and_scenario_switch_are_read_only(self):
        before = file_hash(self.live)
        app = self.app()
        self.assertEqual([t.label for t in app.tabs], ['Investigation Overview', 'Evidence & Validation', 'Controlled Settlement'])
        app.button(key='refresh').click().run()
        app.radio(key='scenario').set_value(ui.SCENARIOS[1]).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Database not found' in item.value for item in app.info))
        self.assertFalse(self.finance.exists())
        app.radio(key='scenario').set_value(ui.SCENARIOS[0]).run()
        self.assertFalse(app.exception)
        self.assertEqual(before, file_hash(self.live))
        self.assertEqual(len(app.button), 1)  # Refresh is the only action.

    def test_no_database_has_honest_empty_state(self):
        self.live.unlink()
        app = self.app()
        self.assertTrue(any('Database not found' in item.value for item in app.info))
        self.assertFalse(app.tabs)
        self.assertFalse(self.live.exists())

    def test_same_database_cannot_be_labeled_as_both_scenarios(self):
        self.finance = self.live
        app = self.app()
        self.assertTrue(any('same database' in item.value for item in app.error))
        self.assertFalse(app.tabs)

    def test_integrity_failure_is_visible(self):
        with sqlite3.connect(self.live) as c:
            c.execute("UPDATE investigation_workflows SET reason='tampered'")
        app = self.app()
        self.assertTrue(any('Audit integrity is FAIL' in item.value for item in app.error))

    def test_malformed_state_and_validator_render_without_crashing(self):
        with sqlite3.connect(self.live) as c:
            c.execute("UPDATE investigation_workflows SET state_json='{}',validation_json='not-json'")
        app = self.app()
        self.assertTrue(any('Canonical state cannot be parsed' in x.value for x in app.error))
        self.assertTrue(any('Validator result cannot be parsed' in x.value for x in app.error))
        self.assertFalse(app.exception)

    def test_rendered_findings_preserve_passage_references(self):
        from test_investigation_paths import source_fixture
        initial, state, _, _ = source_fixture()
        outcome = fake_completed_workflow(initial, outcome=ValidationOutcome.HITL_REQUIRED)
        outcome = replace(outcome, state=state)
        path = Path(self.tmp.name) / 'quoted-fixture.sqlite3'
        with WorkflowStore(path) as store:
            run = store.begin(DOSSIER, initial)
            store.finish(DOSSIER, run, state, outcome, 0)
        self.live = path  # Synthetic test data, not a live validation claim.
        before = file_hash(path)
        app = self.app()
        self.assertTrue(any('passage:' in x.value for x in app.text))
        self.assertTrue(any('artifact: a-' in x.value for x in app.text))
        self.assertFalse(app.exception)
        self.assertEqual(before, file_hash(path))

    def test_real_finance_fixture_displays_execution_and_reconciliation_without_writes(self):
        fixture = backend_fixtures.IntegratedFinancialTests('test_supported_sandbox_end_to_end')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._approved()
        dispatch_financial(fixture.store, DOSSIER)
        sandbox_apply(fixture.store, DOSSIER)
        mark_execution_status(fixture.store, DOSSIER, 'UNKNOWN_EXECUTION')
        reconcile_financial(fixture.store, DOSSIER)
        verify_financial(fixture.store, DOSSIER)
        self.finance = Path(fixture.tmp.name) / 'sandbox.sqlite'
        before = file_hash(self.finance)
        app = self.app()
        app.radio(key='scenario').set_value(ui.SCENARIOS[1]).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('Persisted final verification: VERIFIED' in x.value for x in app.text))
        self.assertTrue(any('Reconciliation: EXECUTED' in x.value for x in app.text))
        self.assertTrue(any('simulated' in x.value.lower() or 'synthetic' in x.value.lower() for x in app.caption))
        app.button(key='refresh').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(before, file_hash(self.finance))
        self.assertEqual(ui.read_snapshot(self.finance, DOSSIER)['total_writes'], 1)


if __name__ == '__main__':
    unittest.main()
