"""LossTrace MVP: source-authenticated, read-only BUILDING repair price review.

This is NOT a Valuation Agent, coverage approval, authoritative supported-loss
finding, settlement recommendation, or authorization to execute a payment.
Authenticity (SHA-256) verifies source identity, not truth or causation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from case import CanonicalCaseState, EvidenceArtifact, compute_evidence_snapshot
from financial import calculate_payable
from models import SpecialistRole, SpecialistStatus
from policy import evaluate_policy


class FinancialGateError(ValueError):
    """A source, workflow or business gate failed closed."""


_AMOUNT = re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{2}$")
_REVIEW = re.compile(
    r"Reviewed Line\s+(L\d+)\s*\n+\s*([^\n]+?)\s*\n+\s*"
    r"Submitted line:\s*\$([0-9,]+\.\d{2})\.\s*"
    r"Independently reviewed lump-sum allowance:\s*\$([0-9,]+\.\d{2})\.",
    re.MULTILINE,
)
_ESTIMATE = re.compile(
    r"(?:^|\n)\s*Line\s+(L\d+)\s*\n+\s*([^\n]+?)\s*\n+\s*"
    r"Quantity\s+1\s+LS;\s*unit rate\s*\$([0-9,]+\.\d{2});\s*"
    r"amount\s*\$([0-9,]+\.\d{2})\.",
    re.MULTILINE,
)
_OP_RATE = re.compile(r"Accept\s+(\d+(?:\.\d+)?)%\s+combined overhead and profit", re.I)


def _money(text: str) -> Decimal:
    cleaned = text.replace(",", "")
    if not _AMOUNT.fullmatch(cleaned):
        raise FinancialGateError("Invalid two-decimal monetary field")
    return Decimal(cleaned)


def _money_str(amount: Decimal) -> str:
    return f"{amount:.2f}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PriceOpinionLine:
    line_id: str
    description: str
    submitted: str
    reviewed_opinion: str
    capped_candidate: str


@dataclass(frozen=True)
class FinancialPreview:
    claim_id: str
    dossier_id: str
    case_version: int
    evidence_hash: str
    basis: str
    review_artifact_id: str
    estimate_artifact_id: str
    lines: tuple[PriceOpinionLine, ...]
    price_opinion_base: str
    reviewed_o_and_p_rate: str
    conditional_o_and_p: str
    policy_deductible: str
    building_limit: str
    conditional_amount: str
    authority_threshold: str
    authority_review_signal: bool
    financial_action_permitted: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _manifest_and_state(root: Path, dossier_id: str, state: CanonicalCaseState) -> dict:
    if not re.fullmatch(r"d-[0-9a-f]{12}", dossier_id):
        raise FinancialGateError("Invalid dossier ID")
    path = root / "manifests" / f"{dossier_id}.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if (manifest["runtime_id"] != dossier_id
                or manifest["claim_id"] != state.claim_id
                or not isinstance(manifest["artifacts"], list)):
            raise FinancialGateError("Manifest identity or artifact list is inconsistent")
        artifacts = [EvidenceArtifact(
            artifact_id=a["artifact_id"], path=a["path"],
            sha256=a["sha256"], source_class=a["source_class"]
        ) for a in manifest["artifacts"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FinancialGateError("Investigation manifest is missing or invalid") from exc
    if (not artifacts
            or len({a.artifact_id for a in artifacts}) != len(artifacts)
            or len({a.path for a in artifacts}) != len(artifacts)
            or compute_evidence_snapshot(artifacts) != state.evidence_snapshot_hash):
        raise FinancialGateError("Manifest does not match persisted evidence snapshot")
    # The snapshot binds registered metadata. Independently re-read every actual
    # artifact, including sources not selected for a financial quotation.
    dossier_root = (root / "world" / dossier_id).resolve()
    if not dossier_root.is_relative_to(root.resolve()):
        raise FinancialGateError("Dossier path escapes runtime boundary")
    for artifact in artifacts:
        relative = Path(artifact.path)
        source = (root / relative).resolve()
        if (relative.is_absolute() or ".." in relative.parts or "\\" in artifact.path
                or not artifact.path.startswith(f"world/{dossier_id}/")
                or not source.is_relative_to(dossier_root)):
            raise FinancialGateError("Registered source path escapes dossier boundary")
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise FinancialGateError("Registered investigation source is missing or unreadable") from exc
        if _sha(data) != artifact.sha256:
            raise FinancialGateError("Registered investigation source SHA-256 mismatch")
    return manifest


def _read_registered(root: Path, dossier_id: str, manifest: dict,
                     suffix: str, source_class: str) -> tuple[dict, bytes]:
    candidates = [a for a in manifest["artifacts"]
                  if a["path"] == f"world/{dossier_id}/{suffix}"
                  and a["source_class"] == source_class]
    if len(candidates) != 1:
        raise FinancialGateError("Required authorized source missing or ambiguous")
    artifact = candidates[0]
    dossier_root = (root / "world" / dossier_id).resolve()
    path = (root / artifact["path"]).resolve()
    if not path.is_relative_to(dossier_root):
        raise FinancialGateError("Source path escapes dossier boundary")
    data = path.read_bytes()
    if _sha(data) != artifact["sha256"]:
        raise FinancialGateError("Registered source SHA-256 mismatch")
    return artifact, data


def _pdf_text(data: bytes) -> str:
    # Imported lazily so the financial module itself does not make network/tool calls.
    import pymupdf
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if not 1 <= len(pdf) <= 12:
                raise FinancialGateError("Unexpected financial document page count")
            return "\n".join(page.get_text(sort=True) for page in pdf)
    except FinancialGateError:
        raise
    except Exception as exc:
        raise FinancialGateError("Financial PDF cannot be read") from exc


def _line_values(text: str, pattern: re.Pattern, group: int) -> dict[str, tuple[str, Decimal, Decimal]]:
    records = {}
    for match in pattern.finditer(text):
        line, description, left, right = match.groups()
        if line in records:
            raise FinancialGateError("Duplicate financial line identifier")
        records[line] = (description.strip(), _money(left), _money(right))
    if not records:
        raise FinancialGateError(f"No parseable financial line items in {group} source")
    return records


def _verified_context(root: Path, dossier_id: str, manifest: dict, claim_id: str) -> tuple[dict, dict, dict, dict]:
    sources = {}
    for key, suffix, source_class in (
        ("claim", "records/claim.json", "carrier_claim_record"),
        ("policy", "records/policy.json", "carrier_policy_record"),
        ("ledger", "records/ledger.json", "carrier_ledger"),
    ):
        _, data = _read_registered(root, dossier_id, manifest, suffix, source_class)
        sources[key] = json.loads(data)
    if (sources["claim"].get("claim_id") != claim_id
            or sources["ledger"].get("claim_id") != claim_id
            or sources["policy"].get("policy_id") != sources["claim"].get("policy_id")):
        raise FinancialGateError("Claim/policy/ledger identity mismatch")
    policy = sources["policy"]
    when = date.fromisoformat(sources["claim"]["date_of_loss"])
    if not date.fromisoformat(policy["effective"]) <= when < date.fromisoformat(policy["expires"]):
        raise FinancialGateError("Policy not active on loss date")
    if policy.get("currency") != "USD":
        raise FinancialGateError("Unsupported currency")
    if policy["deductible"].get("basis") != "PER_OCCURRENCE":
        raise FinancialGateError("Unsupported deductible basis")
    if policy.get("op_basis") != "ACCEPTED_BUILDING_REPAIR_ONLY":
        raise FinancialGateError("Unsupported O&P basis")
    ledger = sources["ledger"]
    # Demo financial engine has not implemented allocation of historical payments
    # or previously satisfied deductibles. Never silently double-count them.
    if not isinstance(ledger.get("payments"), list) or ledger["payments"]:
        raise FinancialGateError("Historical payments must be explicitly empty in this MVP")
    for usage_key, policy_key in (("coverage_used", "coverage_parts"),
                                  ("sublimit_used", "sublimits")):
        usage = ledger.get(usage_key)
        if not isinstance(usage, dict) or usage.keys() != policy[policy_key].keys():
            raise FinancialGateError("Historical usage map does not match policy")
    amounts = (ledger.get("paid_to_date"), ledger.get("deductible_satisfied"),
               *ledger["coverage_used"].values(), *ledger["sublimit_used"].values())
    if any(not isinstance(v, str) for v in amounts):
        raise FinancialGateError("Missing or invalid historical monetary field")
    if any(_money(v) != 0 for v in amounts):
        raise FinancialGateError("Nonzero prior usage requires full allocation engine")
    config = json.loads((root / "runtime_config" / "control_policy.json").read_text(encoding="utf-8"))
    if config.get("currency") != "USD":
        raise FinancialGateError("Control policy currency mismatch")
    return sources["claim"], policy, ledger, config


def build_price_review(dossier_id: str, state: CanonicalCaseState, *,
                       runtime_root: str | Path = "data/application_runtime") -> FinancialPreview:
    """Compare registered estimate vs independent price opinion.

    No inference that any line is causally related to this loss; no financial
    authorization. Included categories: primary BUILDING repair lines only.
    Separate invoices, mold, BPP and prior payments are explicitly excluded.
    """
    root = Path(runtime_root).resolve()
    manifest = _manifest_and_state(root, dossier_id, state)
    estimate_meta, estimate_data = _read_registered(
        root, dossier_id, manifest, "valuation/repair_estimate.pdf", "contractor_estimate")
    review_meta, review_data = _read_registered(
        root, dossier_id, manifest, "evidence/cost_review.pdf", "independent_cost_review")
    estimate = _pdf_text(estimate_data)
    review = _pdf_text(review_data)
    if state.claim_id not in estimate or state.claim_id not in review:
        raise FinancialGateError("Financial document claim ID mismatch")
    estimate_items = _line_values(estimate, _ESTIMATE, 1)
    review_items = _line_values(review, _REVIEW, 2)
    if estimate_items.keys() != review_items.keys():
        raise FinancialGateError("Independent cost review does not cover every estimate line")
    lines = []
    for line_id in sorted(estimate_items, key=lambda x: int(x[1:])):
        estimate_desc, estimate_rate, estimate_total = estimate_items[line_id]
        reviewed_desc, claimed_in_review, reviewed_amount = review_items[line_id]
        if (estimate_desc.casefold() != reviewed_desc.casefold()
                or estimate_rate != estimate_total
                or estimate_total != claimed_in_review):
            raise FinancialGateError("Estimate and review disagree on submitted line")
        if any(token in estimate_desc.casefold() for token in ("mold", "fungi", "rot ", "business interruption")):
            raise FinancialGateError("Special-category financial line requires separate controls")
        lines.append(PriceOpinionLine(
            line_id, estimate_desc, _money_str(estimate_total),
            _money_str(reviewed_amount), _money_str(min(estimate_total, reviewed_amount)),
        ))
    if len(lines) > 30:
        raise FinancialGateError("Unexpected financial line count")
    op_rates = _OP_RATE.findall(review)
    if len(op_rates) > 1:
        raise FinancialGateError("Ambiguous overhead/profit instruction")
    op_rate = Decimal(op_rates[0]) / Decimal("100") if op_rates else Decimal("0")
    if op_rate < 0 or op_rate > Decimal("0.20"):
        raise FinancialGateError("Unsupported O&P rate")
    claim, policy, _ledger, control = _verified_context(root, dossier_id, manifest, state.claim_id)
    if claim.get("case_version") != state.case_version:
        raise FinancialGateError("Claim version differs from persisted investigation basis")
    base = sum((Decimal(x.capped_candidate) for x in lines), Decimal("0.00"))
    deductible = _money(policy["deductible"]["amount"])
    building_limit = _money(policy["coverage_parts"]["BUILDING"]["limit"])
    threshold = _money(control["authority_threshold"])
    # Reuse existing deterministic financial arithmetic. Its 'supported_loss'
    # parameter is a conditional price-review amount here, NOT supported loss.
    calc = calculate_payable(
        supported_loss=base, o_and_p_rate=op_rate,
        deductible=deductible, policy_limit=building_limit,
        prior_payments=Decimal("0.00"),
    )
    policy_signal = evaluate_policy(calc.remaining_payable, threshold, [])
    return FinancialPreview(
        claim_id=state.claim_id, dossier_id=dossier_id,
        case_version=state.case_version, evidence_hash=state.evidence_snapshot_hash,
        basis="CONDITIONAL_BUILDING_REPAIR_PRICE_OPINION_ONLY",
        review_artifact_id=review_meta["artifact_id"],
        estimate_artifact_id=estimate_meta["artifact_id"],
        lines=tuple(lines), price_opinion_base=_money_str(base),
        reviewed_o_and_p_rate=str(op_rate), conditional_o_and_p=_money_str(calc.o_and_p),
        policy_deductible=_money_str(deductible), building_limit=_money_str(building_limit),
        conditional_amount=_money_str(calc.remaining_payable),
        authority_threshold=_money_str(threshold),
        authority_review_signal=policy_signal.hitl_required,
        financial_action_permitted=False,
        warnings=(
            "Hash-verified document authenticity is not independent proof of damage attribution or coverage.",
            "Only repair-estimate lines reviewed in the independent cost memorandum are included.",
            "Separate invoices, mold/BPP, prior-usage allocations and other claim components are excluded.",
            "Conditional amount is NOT an authorized payable or settlement recommendation.",
        ),
    )


def build_gated_financial_preview(dossier_id: str, record: dict, *,
                                  audit_integrity: bool, runtime_root: str | Path = "data/application_runtime") -> FinancialPreview:
    """Read-only preview only after validated and sufficient investigation.

    Does not commit state, add audit events or authorize execution.
    """
    if not audit_integrity:
        raise FinancialGateError("Audit integrity failure blocks financial preview")
    if record.get("status") != "AWAITING_VALUATION" or record.get("next_action") != "NEXT_VALUATION":
        raise FinancialGateError("Workflow is not eligible for financial preview")
    state = CanonicalCaseState.model_validate_json(record["state_json"])
    if (state.claim_id != record["claim_id"]
            or state.case_version != record["case_version"]
            or state.revision != record["revision"]
            or state.evidence_snapshot_hash != record["evidence_hash"]):
        raise FinancialGateError("Checkpoint basis mismatch")
    review_json = record.get("validation_json")
    if not review_json:
        raise FinancialGateError("Independent validation missing")
    review = json.loads(review_json)
    if (review.get("outcome") != "PROVISIONAL_SUPPORT"
            or review.get("basis_case_version") != state.case_version
            or review.get("basis_evidence_hash") != state.evidence_snapshot_hash
            or review.get("basis_revision") != state.revision
            or review.get("source_checks", 0) <= 0):
        raise FinancialGateError("Validation missing, stale or not provisionally supported")
    checks = review.get("semantic_checks") or []
    if not checks or any(check.get("verdict") != "SUPPORTED" for check in checks):
        raise FinancialGateError("Independent semantic validation is incomplete")
    if (review.get("relationship_reported") != review.get("relationship_validated")
            or review.get("relationship_validated") == "POTENTIAL_CONFLICT"):
        raise FinancialGateError("Cross-document validation is unresolved")
    by_role = {f.specialist: f for f in state.specialist_findings}
    for role in (SpecialistRole.EVIDENCE, SpecialistRole.COVERAGE):
        if role not in by_role or by_role[role].status != SpecialistStatus.SUFFICIENT:
            raise FinancialGateError("Required specialist finding is not sufficient")
    if state.conflicts or state.unresolved_questions or any(
            f.conflicts or f.unresolved_questions for f in state.specialist_findings):
        raise FinancialGateError("Unresolved investigation issues block preview")
    return build_price_review(dossier_id, state, runtime_root=runtime_root)
