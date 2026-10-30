# LossTrace MVP — focused audit remediation (2026-10-29)

F01–F07 are addressed in a separate working copy of the uploaded release candidate.
The original ZIP and existing real investigation databases were not modified.
See `REMEDIATION_REPORT.md` for the file-by-file changes, regression evidence,
compatibility limits, demonstration instructions and Windows PowerShell commands.

- Every registered investigation artifact is checked against its actual bytes at
  proposal, authorization and dispatch. Missing or inconsistent evidence blocks.
- Limits precede the deductible, following the supplied coverage form.
- Started, completed and failed audit events bind every checkpoint field.
- Historical payment entries and incomplete/nonzero usage records block finance.
- Live `run` rejects alternate runtime roots before opening a database.
- Live investigations remain mandatory HITL; supported finance uses explicit fixtures.
- Actual Validator/coordinator offline paths and untampered authorization expiry
  now have focused regression coverage. No live model approval is claimed.

**Compatibility:** Existing command names and the financial result field names are
preserved. `amount_after_policy_limit` now represents the capped gross loss;
`amount_after_deductible` is the subsequent deductible result. Old audit formats
without a complete checkpoint hash remain readable but fail verification and
cannot advance financial actions. There is no automatic migration or re-signing
of old history. Use fresh disposable databases for demonstrations and keep old
databases unchanged. Archived source and registered runtime data are unchanged.

## Earlier release notes — approved repository hardening (2026-10-28)

This update was produced from the user's uploaded `LossTrace.zip`. The original upload was left untouched. This package is a **clean release candidate**, not an independently certified production claims system.

## Application changes

1. **`losstrace_workflow.py`**: the financial proposal now requires `support_passages` on every `SUPPORTED` repair line. Each entry contains a registered `artifact_id`, a 1-based `page`, and an exact text `excerpt` of 40–600 characters. Before a proposal is recorded and again before authorization/execution, the source manifest, file SHA-256, page and excerpt are verified. No authenticated identity or autonomous semantic support decision is implied. The decision-template CLI now emits the new contract.
2. **`gateway.py`**: structured JSON records (claim, policy, ledger, site observations) are read only if exactly one manifest entry names the path, it is in the dossier boundary, and its SHA-256 matches. Files that fail verification are rejected before reaching agents.
3. **Tests**: updated the existing financial integration tests for the stricter citation contract, added fabricated/wrong-page/missing passage negative tests and a new `test_release_hardening.py` covering JSON record integrity.
4. **Documentation**: replaced the outdated learning-stage README; added `.gitignore` and release notes; retained `pyproject.toml` + `uv.lock` as the authoritative dependency workflow.

## Moved to Archive/ — zero active production imports at the time of review

- `demo/` (all 28 Python demonstration scripts) → `Archive/demo/`
- Seven root demonstrations (`coordinator_demo.py`, `coverage_context_demo.py`, `document_demo.py`, `financial_demo.py`, `real_evidence_demo.py`, `validation_demo.py`, `verification_demo.py`) → `Archive/legacy_demos/`
- `audit.py`, `storage.py`, `backend.py`, `execution.py`, `canonicalization.py`, `runtime.py` → `Archive/legacy_components/`
- `try_passages.py` → `Archive/experiments/`
- `followup_gate.py` → `Archive/deferred/` (deferred live task-aware reinvestigation)
- `STEP2_README.md` and old `requirements.txt` → `Archive/learning_docs/` (renamed `requirements_legacy.txt`)
- Existing files previously in `Archive/` retained unchanged.

Archived scripts are historical source code, not standalone runnable samples at their new relative paths. The live application files stay in the project root.

## Excluded from release ZIP; preserved outside it

- `.venv/` (machine-specific Windows environment, recreate with `uv sync`).
- `__pycache__/`, `*.pyc` and other generated build artifacts.
- Original `data/losstrace_workflow.sqlite3` and `losstrace.db` — **not public code**. A separate private backup ZIP contains these if restoration of existing local history is needed. The submitted release runs with new empty SQLite state by default.

## Intentional scope limits

- Human scope rationales are still *human-attested*; exact source text is authenticated but relevance, causation and coverage are not autonomously verified line by line.
- Named CLI approvers are not authenticated.
- No live Nemotron supported-claim acceptance was performed in this package; the supported financial test states are simulated.
- No real settlement, external payment integration, new agent, Jev routing, dashboard or resumed investigation implementation was added.

## Backward compatibility

**Breaking input change:** Existing `decision.json` documents using only `support_artifact_ids` are rejected. Regenerate using `decision-template` and supply `support_passages`. This is deliberate fail-closed behavior; there is no automatic migration of unsigned human attestations. Prior stored proposals can be inspected via the existing database, but do not treat an older proposal as meeting the strengthened new contract.
