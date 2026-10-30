"""Offline regressions: subprocess/model execution is mocked unless stated.

No Nebius calls, real approvals or production/saved user databases are used.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_dashboard import isolate_ui_history
from test_dashboard import ui, hitl_fixture, file_hash, DOSSIER, ROOT
from losstrace_workflow import WorkflowStore, load_case_from_runtime
from streamlit.testing.v1 import AppTest

flow = ui.interactive


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        source = ROOT.parent / 'data/application_runtime'
        shutil.copytree(source, self.project / 'data/application_runtime')
        self.claims, _ = flow.discover_claims(self.project)
        self.db = self.project / 'saved.sqlite3'
        self.process = Mock()
        self.process.poll.return_value = None
        self.popen = patch.object(flow.subprocess, 'Popen', return_value=self.process).start()
        self.addCleanup(patch.stopall)
        patch.object(flow, 'UV', '/trusted/bin/uv').start()
        self.addCleanup(lambda: flow._PROCESSES.pop(str(self.project / 'data/dashboard_runs'), None))

    def launch(self, dossier=DOSSIER, paths=(), click=True):
        return flow.launch(dossier, explicit_click=click, configured=paths,
                           list_cases=ui.list_cases, read_snapshot=ui.read_snapshot, project=self.project)

    def test_real_registered_claim_discovery_no_fabrication(self):
        self.assertEqual(len(self.claims), 15)
        claim = next(c for c in self.claims if c['dossier_id'] == DOSSIER)
        self.assertEqual(claim['claim_id'], 'CLM-26-876731')
        self.assertEqual(claim['case_version'], 3)
        self.assertIn('water', claim['loss_type'])
        self.popen.assert_not_called()

    def test_tampered_registered_claim_excluded(self):
        p = self.project / f'data/application_runtime/world/{DOSSIER}/records/claim.json'
        p.write_text('{}')
        claims, rejected = flow.discover_claims(self.project)
        self.assertEqual(rejected, 1)
        self.assertNotIn(DOSSIER, [c['dossier_id'] for c in claims])
        with self.assertRaises(flow.LaunchError): self.launch()
        self.popen.assert_not_called()

    def test_saved_hitl_is_complete_and_read_only_no_model_calls(self):
        hitl_fixture(self.db)
        before = file_hash(self.db)
        runs, errors = flow.saved_runs([self.db], ui.list_cases, ui.read_snapshot)
        self.assertFalse(errors)
        self.assertTrue(flow.is_completed(runs[0]['snapshot']))
        self.assertEqual(runs[0]['snapshot']['record']['status'], 'HITL_REQUIRED')
        self.assertEqual(runs[0]['snapshot']['validation'].source_checks, 9)
        self.assertTrue(flow.freshness(runs[0]['snapshot'], self.project).startswith('Verified'))
        with self.assertRaises(flow.LaunchError): self.launch(paths=[self.db])
        self.assertEqual(before, file_hash(self.db))
        self.popen.assert_not_called()

    def test_explicit_click_required(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(flow.LaunchError): self.launch(click=value)
        self.assertFalse(flow.owned_root(self.project).exists())
        self.popen.assert_not_called()

    def test_invalid_and_command_injection_dossiers_rejected(self):
        for value in ('../escape', DOSSIER + ';echo secret', 'd-000000000000', '--help', '$(whoami)'):
            with self.subTest(value=value), self.assertRaises(flow.LaunchError): self.launch(dossier=value)
        self.popen.assert_not_called()

    def test_fixed_command_shell_false_no_env_or_output_capture(self):
        started = self.launch()
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0][:6], ['/trusted/bin/uv', 'run', '--frozen', 'python', 'losstrace_workflow.py', '--database'])
        self.assertEqual(args[0][-3:], ['run', '--dossier', DOSSIER])
        self.assertEqual(Path(args[0][6]).parent, flow.owned_root(self.project))
        self.assertFalse(Path(args[0][6]).exists())  # Only backend creates its DB.
        self.assertFalse(kwargs['shell'])
        self.assertEqual(kwargs['cwd'], str(self.project))
        self.assertEqual(kwargs['stdout'], flow.subprocess.DEVNULL)
        self.assertEqual(kwargs['stderr'], flow.subprocess.DEVNULL)
        self.assertNotIn('env', kwargs)
        self.assertEqual(started['state'], 'INVESTIGATION STARTED')

    def test_duplicate_click_and_rerun_poll_do_not_relaunch(self):
        self.launch()
        with self.assertRaises(flow.LaunchError): self.launch()
        for _ in range(4):
            self.assertEqual(flow.launch_state(self.project)['state'], 'INVESTIGATION RUNNING')
        self.popen.assert_called_once()

    def test_concurrent_sessions_one_launch(self):
        def attempt():
            try: return self.launch()['state']
            except flow.LaunchError: return 'BLOCKED'
        with ThreadPoolExecutor(max_workers=2) as executor:
            values = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(values.count('INVESTIGATION STARTED'), 1)
        self.assertEqual(values.count('BLOCKED'), 1)
        self.popen.assert_called_once()

    def test_cross_process_marker_blocks_even_without_handle(self):
        self.launch()
        flow._PROCESSES.pop(str(flow.owned_root(self.project)))
        self.assertEqual(flow.launch_state(self.project)['state'], 'COMPLETION UNKNOWN')
        with self.assertRaises(flow.LaunchError): self.launch()
        self.popen.assert_called_once()

    def test_timeout_does_not_kill_or_retry(self):
        self.launch()
        with patch.object(flow, 'TIMEOUT_SECONDS', -1):
            self.assertEqual(flow.launch_state(self.project)['state'], 'TIMEOUT / STILL RUNNING')
        with self.assertRaises(flow.LaunchError): self.launch()
        self.process.kill.assert_not_called()
        self.process.terminate.assert_not_called()
        self.popen.assert_called_once()

    def test_failed_launch_locks_without_leaking_exception(self):
        self.popen.side_effect = RuntimeError('NEBIUS_API_KEY=CANARY')
        with self.assertRaises(flow.LaunchError) as error: self.launch()
        self.assertNotIn('CANARY', str(error.exception))
        self.assertEqual(flow.launch_state(self.project)['state'], 'COMPLETION UNKNOWN')
        with self.assertRaises(flow.LaunchError): self.launch()
        self.popen.assert_called_once()

    def test_new_runs_are_unique_and_previous_db_unchanged(self):
        first = self.launch()
        path = flow.owned_root(self.project) / first['database']
        hitl_fixture(path)
        before = file_hash(path)
        self.process.poll.return_value = 0
        self.assertEqual(flow.launch_state(self.project)['returncode'], 0)
        self.assertIsNone(flow.launch_state(self.project))
        second_id = next(c['dossier_id'] for c in self.claims if c['dossier_id'] != DOSSIER)
        second = self.launch(dossier=second_id)
        self.assertNotEqual(first['database'], second['database'])
        self.assertEqual(file_hash(path), before)

    def test_completed_process_without_checkpoint_is_not_success_or_retry(self):
        self.launch()
        self.process.poll.return_value = 7
        result = flow.launch_state(self.project)
        self.assertEqual(result['returncode'], 7)
        self.assertEqual(len(flow.result_receipts(self.project)), 1)
        with self.assertRaises(flow.LaunchError): self.launch()
        self.popen.assert_called_once()

    def test_existing_registered_unreadable_db_blocks_launch(self):
        with self.assertRaises(flow.LaunchError): self.launch(paths=[self.db])
        self.popen.assert_not_called()
        self.assertFalse(self.db.exists())

    def test_no_arbitrary_directory_scan_or_symlink_follow(self):
        outside = self.project / 'arbitrary'; outside.mkdir()
        hitl_fixture(outside / 'hidden.sqlite3')
        root = flow.owned_root(self.project); root.mkdir()
        try:
            (root / ('run-' + 'a'*32 + '.sqlite3')).symlink_to(outside / 'hidden.sqlite3')
        except OSError:
            self.skipTest('Symlink creation is unavailable for this account')
        self.assertEqual(flow.database_paths([], self.project), [])
        self.assertEqual(flow.database_paths([outside / 'hidden.sqlite3'], self.project), [outside / 'hidden.sqlite3'])

    def test_redirected_owned_directory_rejected(self):
        outside = self.project / 'elsewhere'; outside.mkdir()
        try:
            (self.project / 'data/dashboard_runs').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation is unavailable for this account')
        with self.assertRaises(flow.LaunchError): self.launch()
        self.popen.assert_not_called()

    def test_multiple_saved_runs_separate_identities_and_audit_preference(self):
        hitl_fixture(self.db)
        other = self.project / 'other.sqlite3'; hitl_fixture(other, source_checks=6)
        with sqlite3.connect(other) as c: c.execute("UPDATE investigation_workflows SET reason='tampered'")
        runs, _ = flow.saved_runs([self.db, other], ui.list_cases, ui.read_snapshot)
        self.assertEqual(len({r['key'] for r in runs}), 2)
        selected = flow.preferred_run(runs, self.project)
        chosen = next(r for r in runs if r['key'] == selected)
        self.assertEqual(chosen['snapshot']['validation'].source_checks, 9)
        self.assertEqual(chosen['snapshot']['audit'], 'PASS')
        self.assertEqual(next(r for r in runs if r['path'] == str(other))['snapshot']['audit'], 'FAIL')

    def test_failed_checkpoint_missing_validator_preserved(self):
        initial, _ = load_case_from_runtime(DOSSIER)
        with WorkflowStore(self.db) as store:
            run = store.begin(DOSSIER, initial)
            self.assertFalse(flow.is_completed(ui.read_snapshot(self.db, DOSSIER)))
            store.fail(DOSSIER, run, 'ValueError')
        s = ui.read_snapshot(self.db, DOSSIER)
        self.assertEqual(s['record']['status'], 'BLOCKED_FAILURE')
        self.assertIsNone(s['validation'])
        self.assertEqual(s['audit'], 'PASS')
        self.assertTrue(flow.is_completed(s))

    def test_changed_bytes_and_missing_sources_not_fresh(self):
        hitl_fixture(self.db)
        snapshot = ui.read_snapshot(self.db, DOSSIER)
        manifest = json.loads((self.project / f'data/application_runtime/manifests/{DOSSIER}.json').read_text())
        artifact = next(a for a in manifest['artifacts'] if a['path'].endswith('.pdf'))
        path = self.project / 'data/application_runtime' / artifact['path']
        path.write_bytes(b'changed')
        self.assertTrue(flow.freshness(snapshot, self.project).startswith('Freshness not verified'))
        path.unlink()
        self.assertTrue(flow.freshness(snapshot, self.project).startswith('Freshness not verified'))
        self.popen.assert_not_called()

    def test_changed_case_version_is_stale(self):
        hitl_fixture(self.db)
        snapshot = ui.read_snapshot(self.db, DOSSIER)
        current, count = load_case_from_runtime(DOSSIER)
        current = current.model_copy(update={'case_version': current.case_version + 1})
        with patch.object(flow, 'load_case_from_runtime', return_value=(current, count)):
            self.assertTrue(flow.freshness(snapshot, self.project).startswith('STALE'))


class InteractiveRenderTests(unittest.TestCase):
    def setUp(self):
        isolate_ui_history(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'saved.sqlite3'
        self.popen = patch.object(flow.subprocess, 'Popen', side_effect=AssertionError('Unexpected process')).start()
        self.addCleanup(patch.stopall)

    def app(self, live=''):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20)
        app.session_state['live_path'] = live
        app.session_state['finance_path'] = str(Path(self.tmp.name) / 'finance.sqlite3')
        app.session_state['selected_claim'] = DOSSIER
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def test_new_claim_offers_run_only_click_dispatches_once(self):
        started = dict(database='run-'+'c'*32+'.sqlite3', dossier_id=DOSSIER, state='INVESTIGATION STARTED')
        with patch.object(flow, 'launch', return_value=started) as launch:
            app = self.app()
            self.assertEqual(app.button(key='run_live').label, 'Run Live Investigation')
            launch.assert_not_called()
            app.button(key='refresh').click().run()
            launch.assert_not_called()
            app.button(key='run_live').click().run()
            launch.assert_called_once()
            self.assertIs(launch.call_args.kwargs['explicit_click'], True)
            app.run()
            launch.assert_called_once()
        self.popen.assert_not_called()

    def test_finished_process_reloads_persisted_failure_without_fallback(self):
        initial, _ = load_case_from_runtime(DOSSIER)
        with WorkflowStore(self.db) as store:
            run = store.begin(DOSSIER, initial)
            store.fail(DOSSIER, run, 'SchemaFailure')
        result = dict(database=self.db.name, dossier_id=DOSSIER,
                      state='PROCESS EXITED', returncode=1)
        with patch.object(flow, 'launch_state', side_effect=[result, None]):
            app = self.app(str(self.db))
        self.assertTrue(any('SchemaFailure' in t.value for t in app.text))
        self.assertNotIn('run_live', [b.key for b in app.button])
        self.popen.assert_not_called()

    def test_saved_hitl_has_no_run_button_refresh_has_no_effect(self):
        hitl_fixture(self.db)
        before = file_hash(self.db)
        app = self.app(str(self.db))
        self.assertEqual(len(app.tabs), 3)
        self.assertNotIn('run_live', [b.key for b in app.button])
        app.button(key='refresh').click().run()
        self.assertEqual(file_hash(self.db), before)
        self.popen.assert_not_called()

    def test_multiple_runs_selector_never_merges(self):
        hitl_fixture(self.db)
        other = Path(self.tmp.name) / 'other.sqlite3'; hitl_fixture(other, source_checks=6)
        app = self.app(str(self.db))
        app.text_area(key='additional_paths').set_value(str(other)).run()
        select = app.selectbox(key=f'saved_run_{DOSSIER}')
        self.assertEqual(len(select.options), 2)
        for key in select.options:
            self.assertIn('HITL_REQUIRED', key)
        self.assertFalse(app.exception)
        self.popen.assert_not_called()

    def test_failed_execution_renders_without_validator(self):
        initial, _ = load_case_from_runtime(DOSSIER)
        with WorkflowStore(self.db) as store:
            run = store.begin(DOSSIER, initial)
            store.fail(DOSSIER, run, 'ValueError')
        app = self.app(str(self.db))
        self.assertTrue(any('ValueError' in t.value for t in app.text))
        self.assertIsNone(ui.read_snapshot(self.db, DOSSIER)['validation'])
        self.assertNotIn('run_live', [b.key for b in app.button])
        self.popen.assert_not_called()

    def test_uncertain_process_blocks_button(self):
        with patch.object(flow, 'launch_state', return_value={'state':'COMPLETION UNKNOWN','message':'Unknown child'}):
            app = self.app()
        self.assertNotIn('run_live', [b.key for b in app.button])
        self.assertTrue(any('Unknown child' in i.value for i in app.info))
        self.popen.assert_not_called()

    def test_synthetic_scenario_never_polls_or_launches(self):
        hitl_fixture(self.db)
        app = self.app(str(self.db))
        with patch.object(flow, 'launch_state', side_effect=AssertionError('Polling in finance')):
            app.radio(key='scenario').set_value(ui.SCENARIOS[1]).run()
        self.assertFalse(app.exception)
        self.assertNotIn('run_live', [b.key for b in app.button])
        self.popen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
