"""Read-only, fail-closed presentation adapter for the existing financial preview.

No database writes, model calls, settlement approval, financial workflow creation,
synthetic scope attestation, or money transfer. The authoritative gates remain in
financial_stage.py and are NOT duplicated or bypassed here.
"""
from __future__ import annotations

from pathlib import Path

from financial_stage import FinancialGateError, FinancialPreview, build_gated_financial_preview


def gated_conditional_preview(snapshot: dict, *, runtime_root: str | Path) -> tuple[FinancialPreview | None, str | None]:
    """Return a fresh conditional price opinion only for an intact positive checkpoint.

    Return (None, None) when not applicable, (None, reason) when gated/blocked.
    Never persist the computed opinion or represent it as an authorized payable.
    """
    record = snapshot.get("record") or {}
    if snapshot.get("financial") is not None:
        return None, None  # Recorded finance workflow has its own unchanged UI.
    if record.get("status") != "AWAITING_VALUATION" or record.get("next_action") != "NEXT_VALUATION":
        return None, None  # HITL and technical errors never receive a price opinion.
    if snapshot.get("audit") != "PASS" or snapshot.get("issues"):
        return None, "Audit integrity or canonical checkpoint could not be verified."
    if snapshot.get("state") is None or snapshot.get("validation") is None:
        return None, "Saved specialist/Validator result is incomplete."
    try:
        preview = build_gated_financial_preview(
            record["dossier_id"], record, audit_integrity=True,
            runtime_root=runtime_root,
        )
    except FinancialGateError as exc:
        return None, str(exc)
    except (OSError, KeyError, TypeError, ValueError):
        return None, "The registered source data or investigation checkpoint could not be read."
    if preview.financial_action_permitted:
        return None, "Preview unexpectedly permits financial action; do not display."
    if preview.claim_id != record.get("claim_id") or preview.dossier_id != record.get("dossier_id"):
        return None, "Preview identity does not match the saved investigation."
    return preview, None
