import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from case import CanonicalCaseState
from models import SpecialistFinding, SpecialistRole, SpecialistStatus
from state_machine import CaseState
from adaptive_routing import plan_initial, plan_after_validation, RoutingAction

H = 'a'*64

def make_state(status=SpecialistStatus.SUFFICIENT, roles=()):
    return CanonicalCaseState(claim_id='CLM-1', case_version=1, revision=len(roles),
        workflow_state=CaseState.INVESTIGATION, evidence_snapshot_hash=H,
        specialist_findings=tuple(SpecialistFinding(specialist=x, status=status,
          basis_case_version=1, basis_evidence_hash=H) for x in roles),
        updated_at=datetime.now(timezone.utc))

def result(state, outcome, checks=(), **other):
    return SimpleNamespace(outcome=outcome, semantic_checks=checks,
      relationship_reported=other.get('reported'), relationship_validated=other.get('validated'),
      basis_case_version=other.get('v',state.case_version), basis_evidence_hash=state.evidence_snapshot_hash,
      basis_revision=other.get('revision',state.revision))

class RoutingTests(unittest.TestCase):
  def test_initial(self):
    d=plan_initial(make_state()); self.assertEqual(d.action,RoutingAction.RUN_SPECIALISTS)
    self.assertEqual([t.specialist for t in d.tasks],[SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE])
  def test_missing_only(self):
    d=plan_initial(make_state(roles=(SpecialistRole.EVIDENCE,)))
    self.assertEqual([t.specialist for t in d.tasks],[SpecialistRole.COVERAGE])
  def test_validate_when_both(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    self.assertEqual(plan_initial(s).action,RoutingAction.RUN_VALIDATION)
  def test_validated_not_money(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    self.assertEqual(plan_after_validation(s,result(s,'PROVISIONAL_SUPPORT')).action,RoutingAction.NEXT_VALUATION)
  def test_insufficient_even_with_support(self):
    s=make_state(status=SpecialistStatus.INSUFFICIENT,roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    self.assertEqual(plan_after_validation(s,result(s,'PROVISIONAL_SUPPORT')).action,RoutingAction.HITL_REQUIRED)
  def test_targeted_unsupported(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    r=result(s,'REINVESTIGATE',checks=[SimpleNamespace(key='evidence:2',verdict='UNSUPPORTED')])
    d=plan_after_validation(s,r,targeted_followup_possible=True)
    self.assertEqual([x.specialist for x in d.tasks],[SpecialistRole.EVIDENCE])
  def test_no_redundant_retry(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    r=result(s,'REINVESTIGATE',checks=[SimpleNamespace(key='evidence:2',verdict='UNSUPPORTED')])
    self.assertEqual(plan_after_validation(s,r).action,RoutingAction.HITL_REQUIRED)
    self.assertEqual(plan_after_validation(s,r,targeted_followup_possible=True,followups_used=1).action,RoutingAction.HITL_REQUIRED)
  def test_unknown_target(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    self.assertEqual(plan_after_validation(s,result(s,'REINVESTIGATE'),targeted_followup_possible=True).action,RoutingAction.HITL_REQUIRED)
  def test_stale(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    self.assertEqual(plan_after_validation(s,result(s,'PROVISIONAL_SUPPORT',revision=1)).action,RoutingAction.HITL_REQUIRED)
  def test_validation_hitl(self):
    s=make_state(roles=(SpecialistRole.COVERAGE,SpecialistRole.EVIDENCE))
    self.assertEqual(plan_after_validation(s,result(s,'HITL_REQUIRED')).action,RoutingAction.HITL_REQUIRED)

if __name__=='__main__': unittest.main()
