"""UI-only outcome/navigation regressions; model calls never run.
Optional supplied-record tests read existing demo DBs without mutation.
"""
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from test_dashboard import ui, ROOT, file_hash, hitl_fixture, fake_completed_workflow
from losstrace_workflow import WorkflowStore, load_case_from_runtime

POSITIVE = 'd-e003cb27d820'
HITL = 'd-11cd8b30545e'


def positive_fixture(path, checks=8):
    state, _ = load_case_from_runtime(POSITIVE)
    outcome = fake_completed_workflow(state)
    outcome = replace(outcome, validation=outcome.validation.model_copy(update={'source_checks': checks}))
    with WorkflowStore(path) as store:
        run = store.begin(POSITIVE, state)
        store.finish(POSITIVE, run, outcome.state, outcome, 0)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.positive = Path(self.temp.name) / 'positive.sqlite3'
        self.hitl = Path(self.temp.name) / 'hitl.sqlite3'
        positive_fixture(self.positive)
        hitl_fixture(self.hitl)
        self.popen = patch.object(ui.interactive.subprocess, 'Popen', side_effect=AssertionError('No model process allowed')).start()
        self.addCleanup(patch.stopall)
        patch.object(ui.interactive, 'database_paths', side_effect=lambda configured, project: [Path(p).resolve() for p in configured]).start()
        patch.object(ui.interactive, 'launch_state', return_value=None).start()
        patch.object(ui.interactive, 'result_receipts', return_value=[]).start()

    def app(self, positive=True):
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30)
        app.session_state['positive_path'] = str(self.positive) if positive else ''
        app.session_state['live_path'] = str(self.hitl)
        app.session_state['finance_path'] = str(Path(self.temp.name)/'missing.sqlite3')
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def body(self, app):
        return '\n'.join(x.proto.body for x in app.get('html'))

    def test_positive_feature_is_data_driven_and_read_only(self):
        before = file_hash(self.positive)
        app = self.app()
        self.assertIn('Investigation Checks Passed', self.body(app))
        self.assertIn('NEXT_VALUATION', self.body(app))
        self.assertIn('Cypress Point Endoscopy LLC', [s.value for s in app.subheader])
        app.button(key='refresh').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(file_hash(self.positive), before)
        self.popen.assert_not_called()

    def test_variable_checks_are_shown_without_demo_expectations(self):
        snap = ui.read_snapshot(self.positive, POSITIVE)
        for count in (0, 7, 8, 12):
            changed = dict(snap, validation=snap['validation'].model_copy(update={'source_checks': count}))
            self.assertEqual(ui.decision_kind(changed), 'positive')
            self.assertEqual(dict(ui.recorded_indicators(changed))['Source checks'], count)

    def test_missing_validator_is_incomplete_not_positive(self):
        s = ui.read_snapshot(self.positive, POSITIVE)
        self.assertEqual(ui.decision_kind(dict(s, validation=None)), 'incomplete')
        self.assertEqual(dict(ui.recorded_indicators(dict(s, validation=None)))['Validator outcome'], 'Not available')

    def test_integrity_failure_cannot_get_positive_treatment(self):
        s = ui.read_snapshot(self.positive, POSITIVE)
        for field in ({'audit':'FAIL'}, {'issues':['basis mismatch']}):
            self.assertEqual(ui.decision_kind(dict(s, **field)), 'unverified')

    def test_technical_failure_stored_as_hitl_is_distinct(self):
        s = ui.read_snapshot(self.hitl, HITL)
        event = {'kind':'INVESTIGATION_COMPLETED', 'details':{'rounds':[{'steps':[{'role':'coverage','outcome':'TOOL_FAILURE'}]}]}}
        failed = dict(s, events=[event], validation=None)
        self.assertEqual(ui.decision_kind(s), 'review')
        self.assertEqual(ui.decision_kind(failed), 'failure')
        self.assertEqual(ui.recorded_steps(failed)['coverage'], 'TOOL_FAILURE')

    def test_demo_switch_preserves_identity_and_does_not_launch(self):
        app = self.app()
        for scenario, claim in ((ui.SCENARIOS[3], 'CLM-26-876731'), (ui.SCENARIOS[2], 'CLM-26-591035')):
            app.radio(key='scenario').set_value(scenario).run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertIn(claim, self.body(app))
        self.popen.assert_not_called()

    def test_missing_positive_demo_does_not_substitute_hitl(self):
        app = self.app(positive=False)
        app.radio(key='scenario').set_value(ui.SCENARIOS[2]).run()
        self.assertFalse(app.exception)
        self.assertTrue(any('requested completed demonstration is not available' in e.value for e in app.info))
        self.assertNotIn('Investigation Checks Passed', self.body(app))
        self.assertFalse(app.tabs)

    def test_latest_failed_attempt_not_selected_as_demo(self):
        runs, _ = ui.interactive.saved_runs([self.hitl], ui.list_cases, ui.read_snapshot)
        s = runs[0]['snapshot']
        failed = dict(runs[0], key='failed', snapshot=dict(s, validation=None))
        chosen = ui.demo_run([failed, *runs], ui.SCENARIOS[3])
        self.assertEqual(chosen['key'], runs[0]['key'])

    def test_stale_demo_not_featured_but_records_preserved(self):
        runs, _ = ui.interactive.saved_runs([self.positive], ui.list_cases, ui.read_snapshot)
        with patch.object(ui.interactive, 'freshness', return_value='STALE — changed'):
            self.assertIsNone(ui.demo_run(runs, ui.SCENARIOS[2]))
        self.assertEqual(len(runs), 1)

    def test_existing_claim_selection_survives_feature_refresh(self):
        app = self.app()
        app.text_input(key='claim_search').set_value('876731').run()
        self.assertIn('Human review required', self.body(app))
        app.button(key='refresh').click().run()
        self.assertIn('Human review required', self.body(app))
        self.assertNotIn('Investigation Checks Passed', self.body(app))

    def test_model_and_document_markup_escaped(self):
        snap = ui.read_snapshot(self.hitl, HITL)
        snap['record']['claim_id'] = '<script>secret()</script> NEBIUS_API_KEY=canary-key'
        with patch.object(ui.st, 'html') as render, patch.object(ui.st, 'caption'):
            ui.show_decision_banner(snap)
        output = ''.join(str(x) for x in render.call_args_list)
        self.assertNotIn('<script>', output)
        self.assertNotIn('canary-key', output)
        self.assertIn('&lt;script&gt;', output)

    def test_execution_not_inferred_from_fixture_sufficiency(self):
        self.assertEqual(ui.recorded_steps(ui.read_snapshot(self.positive, POSITIVE)), {})


class SuppliedRecordTests(unittest.TestCase):
    """Skipped on installations without the named saved demos; no DBs generated here."""
    def test_three_actual_saved_demos_render_and_remain_unchanged(self):
        folder = ROOT.parent/'data/demo_databases'
        paths = [folder/name for name in ('scenario3-positive-live.sqlite3','scenario1-live.sqlite3','scenario2-finance.sqlite3')]
        if not all(p.is_file() for p in paths):
            self.skipTest('Preserved demonstration databases not available')
        before = {str(p):file_hash(p) for p in paths}
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30)
        app.session_state['positive_path'],app.session_state['live_path'],app.session_state['finance_path'] = map(str, paths)
        with patch.object(ui.interactive.subprocess, 'Popen', side_effect=AssertionError('No launch')) as process:
            app.run()
            for scenario, expected in ((ui.SCENARIOS[2], 'Investigation Checks Passed'),
                                       (ui.SCENARIOS[3], 'Human review required'),
                                       (ui.SCENARIOS[1], 'Synthetic finance demonstration')):
                app.radio(key='scenario').set_value(scenario).run()
                self.assertFalse(app.exception, str(app.exception))
                body = '\n'.join(x.proto.body for x in app.get('html'))
                self.assertIn(expected, body)
                self.assertFalse(any('Audit integrity is' in x.value for x in app.error))
                if scenario == ui.SCENARIOS[1]:
                    self.assertIn('$12,105.00', body)
                    self.assertIn('VERIFIED', body)
                self.assertNotIn('run_live', [b.key for b in app.button])
            process.assert_not_called()
        self.assertEqual(before, {str(p):file_hash(p) for p in paths})
        for path in paths:
            c = ui.list_cases(path)[0]
            self.assertEqual(ui.read_snapshot(path,c['dossier_id'])['audit'], 'PASS')


if __name__ == '__main__':
    unittest.main()
