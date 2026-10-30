"""Presentation regressions. All investigation data is a controlled fixture.
No real model execution, user databases, or financial actions are triggered.
"""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from test_dashboard import isolate_ui_history
from test_dashboard import ui, hitl_fixture, file_hash, DOSSIER, ROOT, fake_completed_workflow
from losstrace_workflow import WorkflowStore, load_case_from_runtime
from validation_agent import ValidationOutcome


class ClaimDataTests(unittest.TestCase):
    def setUp(self):
        self.claims, _ = ui.interactive.discover_claims(ROOT.parent)

    def test_metadata_exactly_matches_verified_runtime_claims(self):
        for claim in self.claims:
            raw = ui.interactive.RuntimeGateway(str(ROOT.parent/'data/application_runtime')).get_claim_record(claim['dossier_id'])
            self.assertEqual(claim['loss_type'], raw['reported_cause'])
            self.assertTrue(claim['claim_summary'])
            self.assertEqual(claim['loss_date'], raw['date_of_loss'])
            self.assertEqual(claim['location'], 'Not available')
        self.assertEqual(len(self.claims), 15)

    def test_case_summaries_only_use_verified_case_information(self):
        by_id = {c['claim_id']: c for c in self.claims}
        self.assertEqual(len({c['claim_summary'] for c in self.claims}), len(self.claims))
        self.assertEqual(by_id['CLM-26-876731']['claim_summary'],
                         'Opened wet-wall area with affected electrical devices · $121,600 submitted')
        self.assertEqual(by_id['CLM-26-437673']['claim_summary'],
                         'Fungal-type growth observed · $148,000 submitted')
        self.assertEqual(by_id['CLM-26-786122']['claim_summary'],
                         'Site inspection pending · $31,000 submitted')
        for c in self.claims:
            self.assertEqual(c['loss_type'], 'Plumbing supply-line water release')
            self.assertIn('submitted', c['claim_summary'])
        # The 13 electrical-device cases have exactly five stable display phrasings.
        electrical = [c for c in self.claims if 'electrical' in c['claim_summary'].lower()]
        self.assertEqual(len(electrical), 13)
        distinct_phrases = {c['claim_summary'].split(' · $')[0] for c in electrical}
        self.assertEqual(len(distinct_phrases), 5)
        self.assertEqual(by_id['CLM-26-876731']['claim_summary'],
                         'Opened wet-wall area with affected electrical devices · $121,600 submitted')

    def test_summary_fallback_has_no_fabricated_information(self):
        self.assertEqual(ui.interactive.claim_summary({'reported_cause': 'A water claim', 'submitted_amount': 'not known'}),
                         'A water claim')
        self.assertEqual(ui.interactive.claim_summary({'reported_cause': 'A water claim', 'submitted_amount': '12.50'}),
                         'A water claim · $12.50 submitted')
        self.assertEqual(ui.interactive.claim_summary({'reported_cause': 'A water claim', 'submitted_amount': '-50'}),
                         'A water claim')
        self.assertEqual(ui.interactive.claim_summary({'claim_id': 'c', 'reported_cause': 'Water', 'submitted_amount': '13'},
                         {'claim_id': 'other', 'observations': [{'text': 'Fungal-type growth'}]}),
                         'Water · $13 submitted')

    def test_missing_metadata_has_honest_placeholder(self):
        claim = dict(self.claims[0], claim_summary=None, loss_type=None, loss_date='', location={})
        row = ui.claim_rows([claim], [])[0]
        for field in ('description','loss_date','location','last_investigated'):
            self.assertEqual(row[field], 'Not available')
        self.assertEqual(row['status'], 'NOT INVESTIGATED')
        self.assertEqual(row['saved_count'], 0)

    def test_literal_case_insensitive_id_and_description_search(self):
        rows = ui.claim_rows(self.claims, [])
        target = rows[0]
        self.assertEqual(ui.filter_claims(rows, '  '+target['claim_id'].lower()+' '), [target])
        self.assertTrue(ui.filter_claims(rows, 'WATER'))
        self.assertEqual(ui.filter_claims(rows, '.*'), [])
        self.assertEqual(ui.filter_claims(rows, ''), rows)

    def test_unreadable_registration_does_not_assert_uninvestigated(self):
        row = ui.claim_rows(self.claims[:1], [], lookup_incomplete=True)[0]
        self.assertEqual(row['status'], 'UNKNOWN — database unavailable')

    def test_row_selection_maps_to_dossier_without_accepting_invalid_indices(self):
        rows = ui.claim_rows(self.claims, [])
        initial = rows[0]['dossier_id']
        self.assertEqual(ui.resolve_row_selection(rows, initial, (2,), ()), rows[2]['dossier_id'])
        for indices in ((-1,), (999,), ('1',), (True,), (0,1)):
            self.assertEqual(ui.resolve_row_selection(rows, initial, indices, ()), initial)
        self.assertEqual(ui.resolve_row_selection(rows, initial, (2,), (2,)), initial)
        self.assertIsNone(ui.resolve_row_selection([], initial, (), ()))

    def test_finding_categories_preserve_business_source_distinctions(self):
        expected = {'site_observations':'Site observations','survey':'Inspection report',
                    'service':'Plumber report','policy_terms':'Policy terms','policy_wording':'Policy wording',
                    'endorsement':'Endorsement','cross_document_model_assessment':'Cross-document reasoning'}
        for prefix, category in expected.items():
            self.assertEqual(ui.finding_category(prefix+':unchanged payload'), category)
        self.assertEqual(ui.finding_category('unknown:payload'), 'Recorded finding')


class WorkspaceRenderTests(unittest.TestCase):
    def setUp(self):
        isolate_ui_history(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'saved.sqlite3'
        self.popen = patch.object(ui.interactive.subprocess, 'Popen', side_effect=AssertionError('Unrequested process')).start()
        self.addCleanup(patch.stopall)

    def app(self, live=None):
        app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=20)
        app.session_state['live_path'] = str(self.db) if live is None else live
        app.session_state['finance_path'] = str(Path(self.tmp.name)/'finance.sqlite3')
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def html(self, app):
        return '\n'.join(x.proto.body for x in app.get('html'))

    def select_row(self, app, index):
        table = app.dataframe[0]
        app.session_state[table.key] = {'selection': {'rows': [index], 'columns': [], 'cells': []}}
        app.run()
        self.assertFalse(app.exception, str(app.exception))
        return app

    def test_workspace_table_has_required_columns_and_real_values(self):
        hitl_fixture(self.db)
        app = self.app()
        rows = app.dataframe[0].value
        self.assertEqual(list(rows.columns), ['Claim ID','Claim Summary','Loss Date','Investigation Status','Last Investigated'])
        self.assertEqual(len(rows), 15)
        selected = rows[rows['Claim ID'] == 'CLM-26-876731'].iloc[0]
        self.assertEqual(selected['Claim Summary'], 'Opened wet-wall area with affected electrical devices · $121,600 submitted')
        self.assertEqual(selected['Loss Date'], '2026-08-19')
        self.assertEqual(selected['Investigation Status'], 'HITL_REQUIRED')
        self.assertNotEqual(selected['Last Investigated'], 'Not available')
        self.assertIn('Not available', self.html(app))
        self.assertNotIn('Select claim', [x.label for x in app.sidebar.selectbox])
        self.assertTrue(any(x.value == 'View Saved Investigation' for x in app.subheader))
        self.popen.assert_not_called()

    def test_native_row_selection_new_and_saved_never_launches_or_changes_db(self):
        hitl_fixture(self.db)
        before = file_hash(self.db)
        app = self.app()
        table = app.dataframe[0].value
        new_index = next(i for i in range(len(table)) if table.iloc[i]['Investigation Status']=='NOT INVESTIGATED')
        saved_index = next(i for i in range(len(table)) if table.iloc[i]['Claim ID']=='CLM-26-876731')
        self.select_row(app, new_index)
        self.assertEqual(app.button(key='run_live').label, 'Run Live Investigation')
        self.assertFalse(app.tabs)
        self.select_row(app, saved_index)
        self.assertEqual(app.session_state['selected_claim'], DOSSIER)
        self.assertEqual(len(app.tabs), 3)
        self.assertNotIn('run_live', [b.key for b in app.button])
        app.run()
        self.assertEqual(file_hash(self.db), before)
        self.popen.assert_not_called()

    def test_search_filters_visible_claims_and_empty_search_has_no_stale_action(self):
        hitl_fixture(self.db)
        app = self.app()
        app.text_input(key='claim_search').set_value('clm-26-876731').run()
        self.assertEqual(len(app.dataframe[0].value), 1)
        self.assertEqual(app.session_state['selected_claim'], DOSSIER)
        app.text_input(key='claim_search').set_value('unmatched incident').run()
        self.assertTrue(any('No registered claims match' in x.value for x in app.info))
        self.assertFalse(app.tabs)
        self.assertNotIn('run_live', [b.key for b in app.button])
        app.text_input(key='claim_search').set_value('water').run()
        self.assertTrue(len(app.dataframe[0].value)>0)
        self.assertFalse(app.exception)
        self.popen.assert_not_called()

    def test_seven_source_checks_are_displayed_without_demo_failure(self):
        hitl_fixture(self.db, source_checks=7)
        app = self.app()
        values = dict(ui.recorded_indicators(ui.read_snapshot(self.db,DOSSIER)))
        self.assertEqual(values['Source checks'], 7)
        self.assertEqual(values['Validator outcome'], 'HITL_REQUIRED')
        self.assertIn('>7<', self.html(app))
        self.assertNotIn('Nine recorded', self.html(app))
        self.assertFalse(any('Scenario 1 checkpoint' in x.value for x in app.warning))
        self.assertFalse(app.error)

    def test_different_valid_outcome_not_flagged_as_execution_failure(self):
        state, _ = load_case_from_runtime(DOSSIER)
        result = fake_completed_workflow(state, outcome=ValidationOutcome.PROVISIONAL_SUPPORT)
        result = replace(result, validation=result.validation.model_copy(update={'source_checks':7}))
        with WorkflowStore(self.db) as store:
            run = store.begin(DOSSIER,state)
            store.finish(DOSSIER,run,result.state,result,0)
        app = self.app()
        snapshot = ui.read_snapshot(self.db,DOSSIER)
        self.assertEqual(snapshot['audit'],'PASS')
        self.assertNotEqual(snapshot['record']['status'],'HITL_REQUIRED')
        self.assertIn('PROVISIONAL_SUPPORT', self.html(app))
        self.assertFalse(app.error)
        self.assertFalse(any('Scenario 1' in x.value for x in app.warning))
        self.assertIn('eligibility not established',self.html(app))

    def test_recorded_financial_workflow_is_not_a_zero_records_failure(self):
        from test_dashboard import backend_fixtures
        fixture = backend_fixtures.IntegratedFinancialTests('test_supported_sandbox_end_to_end')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture._approved()
        self.db = Path(fixture.tmp.name)/'sandbox.sqlite'
        before = file_hash(self.db)
        app = self.app()
        snapshot = ui.read_snapshot(self.db, DOSSIER)
        self.assertEqual(snapshot['audit'], 'PASS')
        self.assertIn(snapshot['financial']['status'], dict(ui.recorded_indicators(snapshot))['Financial state'])
        self.assertFalse(app.error)
        self.assertTrue(any('does not attest' in x.value for x in app.info))
        self.assertEqual(file_hash(self.db), before)
        self.assertEqual([b.key for b in app.button], ['refresh'])
        self.popen.assert_not_called()

    def test_missing_validator_is_not_success_and_status_is_exact(self):
        state, _ = load_case_from_runtime(DOSSIER)
        with WorkflowStore(self.db) as store:
            run = store.begin(DOSSIER,state)
            store.fail(DOSSIER,run,'ValueError')
        app = self.app()
        values = dict(ui.recorded_indicators(ui.read_snapshot(self.db,DOSSIER)))
        self.assertEqual(values['Validator outcome'],'Not available')
        self.assertEqual(values['Source checks'],'Not available')
        self.assertIn('Missing:',values['Execution information'])
        self.assertIn('BLOCKED_FAILURE',self.html(app))
        self.assertTrue(any('No Validator result recorded' in x.value for x in app.info))
        self.assertNotIn('run_live',[b.key for b in app.button])

    def test_audit_failure_remains_prominent_and_read_only(self):
        hitl_fixture(self.db)
        with sqlite3.connect(self.db) as c:c.execute("UPDATE investigation_workflows SET reason='edited'")
        before=file_hash(self.db)
        app=self.app()
        self.assertTrue(any('Audit integrity is FAIL' in x.value for x in app.error))
        self.assertEqual(file_hash(self.db),before)
        self.popen.assert_not_called()

    def test_searchable_dropdown_fallback_on_unsupported_selection_api(self):
        hitl_fixture(self.db)
        original=ui.st.dataframe
        def compatible(*args,**kwargs):
            if kwargs.get('on_select')=='rerun':raise TypeError('Selection API unavailable')
            return original(*args,**kwargs)
        with patch.object(ui.st,'dataframe',side_effect=compatible):
            app=self.app()
        fallback=app.selectbox(key='claim_fallback')
        self.assertTrue(any('$121,600 submitted' in x for x in fallback.options))
        self.assertEqual(len(app.tabs),3)
        self.popen.assert_not_called()

    def test_source_text_and_model_interpretation_labeled_separately(self):
        from test_investigation_paths import source_fixture
        initial,state,_,_=source_fixture()
        result=replace(fake_completed_workflow(initial,outcome=ValidationOutcome.HITL_REQUIRED),state=state)
        with WorkflowStore(self.db) as store:
            run=store.begin(DOSSIER,initial)
            store.finish(DOSSIER,run,state,result,0)
        app=self.app()
        captions=[x.value for x in app.caption]
        self.assertIn('Source quotation — recorded passage text',captions)
        self.assertIn('Model interpretation — not source text',captions)
        self.assertTrue(any('Inspection report' in x.label for x in app.expander))
        self.assertTrue(any('Plumber report' in x.label for x in app.expander))
        self.popen.assert_not_called()


if __name__=='__main__':unittest.main()
