"""F07: actual offline Validator/coordinator execution with simulated model judgments.

Gateway/PDF/passages/merging/routing/persistence are real. Model decisions and
specialist responses are controlled fixtures, never live Nemotron approval.
"""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from adaptive_routing import RoutingAction
from case import merge_finding
from coordinator import InvestigationBudget, InvestigationOutcome, run_investigation
from gateway import RuntimeGateway
from losstrace_workflow import WorkflowStore, load_case_from_runtime, run_case
from models import SpecialistFinding, SpecialistRole, SpecialistStatus
from source_passages import build_passages
from validated_coordinator import run_validated_routed
from validation_agent import (
    SemanticCheck, SemanticReview, ValidationOutcome, _section_text,
    validate_investigation,
)

DOSSIER = 'd-11cd8b30545e'
ROOT = Path(__file__).resolve().parent / 'data/application_runtime'


def source_fixture():
    gateway = RuntimeGateway(str(ROOT))
    initial, _ = load_case_from_runtime(DOSSIER, runtime_root=ROOT)
    sources = {name: getattr(gateway, 'get_policy_record' if name == 'get_policy_terms' else name)(DOSSIER)
               for name in ('get_claim_record', 'get_site_observations', 'get_policy_terms',
                            'get_inspection_report', 'get_plumber_report', 'get_policy_wording', 'get_endorsements')}
    texts = {SpecialistRole.EVIDENCE: [], SpecialistRole.COVERAGE: []}
    documents = [
        ('survey', sources['get_inspection_report'], 'SEC.PRIOR_CONDITIONS', SpecialistRole.EVIDENCE),
        ('service', sources['get_plumber_report'], 'SEC.CAUSE_ASSESSMENT', SpecialistRole.EVIDENCE),
        ('policy_wording', sources['get_policy_wording'], 'SEC.COVERED_CAUSES', SpecialistRole.COVERAGE),
        ('policy_wording', sources['get_policy_wording'], 'SEC.CONDITIONS', SpecialistRole.COVERAGE),
    ]
    for document in sources['get_endorsements']:
        section = next(s for s in document['locators'] if s != 'SEC.SCHEDULE')
        documents.append(('endorsement', document, section, SpecialistRole.COVERAGE))
    for kind, document, section, role in documents:
        passage = build_passages(document, section, _section_text(document, section))[0]
        texts[role].append(
            f'{kind}:{document["artifact_id"]} | sha256:{document["sha256"]} | '
            f'page:{passage.page} | section:{section} | passage_id:{passage.passage_id} | '
            f'offsets:{passage.start_offset}-{passage.end_offset} | quote:{passage.text} | '
            'model_interpretation:Controlled interpretation; semantic verdict is simulated.'
        )
    texts[SpecialistRole.EVIDENCE].append(
        'cross_document_model_assessment:CORROBORATING | explanation:Controlled relationship fixture')
    findings = {role: SpecialistFinding(
        specialist=role, status=SpecialistStatus.INSUFFICIENT,
        basis_case_version=initial.case_version, basis_evidence_hash=initial.evidence_snapshot_hash,
        findings=tuple(raw),
    ) for role, raw in texts.items()}
    state = initial
    for role in (SpecialistRole.COVERAGE, SpecialistRole.EVIDENCE):
        _, state = merge_finding(state, findings[role], expected_revision=state.revision)
    return initial, state, findings, sources


async def supported_judge(payload):
    return SemanticReview(
        checks=tuple(SemanticCheck(key=item['key'], verdict='SUPPORTED', reason='Simulated semantic verdict')
                     for item in payload['claims']),
        relationship='CORROBORATING', relationship_reason='Simulated relationship verdict',
    )


class ValidatorExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.initial, self.state, self.findings, self.sources = source_fixture()
        self.loader = AsyncMock(return_value=self.sources)
        self.judge = AsyncMock(side_effect=supported_judge)

    async def validate(self, state=None, judge=None):
        return await validate_investigation(DOSSIER, state or self.state,
                                           loader=self.loader, judge=judge or self.judge)

    async def test_actual_source_reconstruction_and_complete_judgments(self):
        result = await self.validate()
        self.assertEqual(result.outcome, ValidationOutcome.PROVISIONAL_SUPPORT)
        self.assertEqual(result.source_checks, 6)
        self.assertEqual(len(result.semantic_checks), 5)
        self.loader.assert_awaited_once()
        self.judge.assert_awaited_once()
        self.assertTrue(all(f.status == SpecialistStatus.INSUFFICIENT for f in self.state.specialist_findings))

    async def test_fabricated_quote_rejected_before_semantic_judge(self):
        findings = tuple(f.model_copy(update={'findings': tuple(
            raw.replace('quote:', 'quote:FABRICATED ') if raw.startswith('survey:') else raw
            for raw in f.findings)}) for f in self.state.specialist_findings)
        result = await self.validate(self.state.model_copy(update={'specialist_findings': findings}))
        self.assertEqual(result.outcome, ValidationOutcome.REINVESTIGATE)
        self.judge.assert_not_awaited()

    async def test_unknown_passage_rejected_before_semantic_judge(self):
        findings = tuple(f.model_copy(update={'findings': tuple(
            raw.replace('passage_id:p-', 'passage_id:unknown-') for raw in f.findings)})
            for f in self.state.specialist_findings)
        result = await self.validate(self.state.model_copy(update={'specialist_findings': findings}))
        self.assertEqual(result.outcome, ValidationOutcome.REINVESTIGATE)
        self.judge.assert_not_awaited()

    async def test_stale_finding_rejected_before_retrieval(self):
        stale = self.state.specialist_findings[0].model_copy(update={'basis_evidence_hash': 'b'*64})
        result = await self.validate(self.state.model_copy(update={'specialist_findings': (stale, *self.state.specialist_findings[1:])}))
        self.assertEqual(result.outcome, ValidationOutcome.REINVESTIGATE)
        self.loader.assert_not_awaited()

    async def test_missing_source_fails_closed(self):
        self.loader.side_effect = FileNotFoundError('controlled missing source')
        self.assertEqual((await self.validate()).outcome, ValidationOutcome.HITL_REQUIRED)
        self.judge.assert_not_awaited()

    async def test_missing_or_duplicate_judgment_keys_fail_closed(self):
        for duplicate in (False, True):
            async def invalid(payload):
                review = await supported_judge(payload)
                checks = review.checks[:-1]
                if duplicate:
                    checks = (*checks, checks[0])
                return review.model_copy(update={'checks': checks})
            with self.subTest(duplicate=duplicate):
                self.assertEqual((await self.validate(judge=invalid)).outcome, ValidationOutcome.HITL_REQUIRED)

    async def test_invalid_model_schema_fails_closed(self):
        self.assertEqual((await self.validate(judge=AsyncMock(return_value={'unexpected': True}))).outcome,
                         ValidationOutcome.HITL_REQUIRED)

    async def test_unsupported_interpretation_requires_reinvestigation(self):
        async def reject(payload):
            review = await supported_judge(payload)
            return review.model_copy(update={'checks': (
                review.checks[0].model_copy(update={'verdict': 'UNSUPPORTED'}), *review.checks[1:])})
        self.assertEqual((await self.validate(judge=reject)).outcome, ValidationOutcome.REINVESTIGATE)

    async def test_relationship_disagreement_requires_reinvestigation(self):
        async def disagree(payload):
            review = await supported_judge(payload)
            return review.model_copy(update={'relationship': 'INCONCLUSIVE'})
        self.assertEqual((await self.validate(judge=disagree)).outcome, ValidationOutcome.REINVESTIGATE)


class CoordinatorExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.initial, self.merged, self.findings, self.sources = source_fixture()
        self.calls = []

    async def coverage(self, dossier, state):
        self.calls.append(('coverage', state.revision))
        return self.findings[SpecialistRole.COVERAGE]

    async def evidence(self, dossier, state):
        self.calls.append(('evidence', state.revision))
        return self.findings[SpecialistRole.EVIDENCE]

    async def validator(self, dossier, state):
        self.calls.append(('validator', state.revision))
        return await validate_investigation(dossier, state,
            loader=AsyncMock(return_value=self.sources), judge=supported_judge)

    async def test_real_orchestration_merges_validates_and_persists_hitl(self):
        with tempfile.TemporaryDirectory() as td, WorkflowStore(Path(td)/'workflow.sqlite') as store:
            receipt = await run_case(DOSSIER, self.initial, store,
                coverage_runner=self.coverage, evidence_runner=self.evidence, validator=self.validator)
            self.assertEqual(self.calls, [('coverage', 0), ('evidence', 1), ('validator', 2)])
            self.assertEqual(receipt.status, 'HITL_REQUIRED')
            self.assertEqual(receipt.source_checks, 6)
            self.assertTrue(store.verify_audit(self.initial.claim_id)[0])
            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM financial_workflows').fetchone()[0], 0)

    async def test_specialist_failure_never_calls_validator(self):
        async def broken(*args):
            raise ValueError('controlled failure')
        check = AsyncMock()
        with contextlib.redirect_stderr(io.StringIO()):
            result = await run_validated_routed(DOSSIER, self.initial,
                coverage_runner=broken, evidence_runner=self.evidence, validator=check)
        self.assertEqual(result.next_decision.action, RoutingAction.HITL_REQUIRED)
        check.assert_not_awaited()
        self.assertEqual(result.investigations[0].outcome, InvestigationOutcome.TOOL_FAILURE)

    async def test_stale_specialist_stops_before_merge(self):
        async def stale(*args):
            return self.findings[SpecialistRole.COVERAGE].model_copy(update={'basis_evidence_hash': 'b'*64})
        result = await run_investigation(DOSSIER, self.initial,
            coverage_runner=stale, evidence_runner=self.evidence)
        self.assertEqual(result.outcome, InvestigationOutcome.STALE_FINDING)
        self.assertEqual(result.state.revision, 0)

    async def test_wrong_specialist_role_stops_before_merge(self):
        result = await run_investigation(DOSSIER, self.initial,
            coverage_runner=self.evidence, evidence_runner=self.evidence)
        self.assertEqual(result.outcome, InvestigationOutcome.INVALID_FINDING)
        self.assertEqual(result.state.revision, 0)

    async def test_timeout_stops_before_validator(self):
        async def slow(*args):
            await asyncio.sleep(0.1)
        check = AsyncMock()
        result = await run_validated_routed(DOSSIER, self.initial,
            coverage_runner=slow, evidence_runner=self.evidence, validator=check,
            budget=InvestigationBudget(total_seconds=0.01, per_agent_seconds=0.001))
        self.assertEqual(result.next_decision.action, RoutingAction.HITL_REQUIRED)
        check.assert_not_awaited()

    async def test_stale_validator_result_cannot_advance(self):
        async def stale(dossier, state):
            result = await self.validator(dossier, state)
            return result.model_copy(update={'basis_revision': state.revision - 1})
        result = await run_validated_routed(DOSSIER, self.initial,
            coverage_runner=self.coverage, evidence_runner=self.evidence, validator=stale)
        self.assertEqual(result.next_decision.action, RoutingAction.HITL_REQUIRED)
        self.assertIn('stale', result.next_decision.reason)

    async def test_reinvestigate_does_not_trigger_unwired_followup(self):
        async def reinvestigate(dossier, state):
            result = await self.validator(dossier, state)
            return result.model_copy(update={'outcome': ValidationOutcome.REINVESTIGATE})
        result = await run_validated_routed(DOSSIER, self.initial,
            coverage_runner=self.coverage, evidence_runner=self.evidence, validator=reinvestigate)
        self.assertEqual(result.next_decision.action, RoutingAction.HITL_REQUIRED)
        self.assertEqual(result.followups_used, 0)
        self.assertEqual(len(result.investigations), 1)
