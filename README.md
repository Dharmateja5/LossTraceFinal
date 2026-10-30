# LossTrace — Evidence-first agentic claims investigation (Hackathon MVP)

**Status:** Hackathon prototype / local SQLite sandbox. This is **not** a production claims adjudication or payment service.

LossTrace supports commercial-property claims investigators and claims operations teams with evidence-grounded AI investigation, independent validation and controlled settlement. NVIDIA Nemotron 3 Super (`nvidia/nemotron-3-super-120b-a12b`) is served through Nebius Token Factory. Coverage and Evidence specialists use MCP retrieval; an independent Validator checks source references and bounded semantic interpretations. A deterministic Adaptive Coordinator owns routing.

The supplied preserved positive investigation for **CLM-26-591035 / Cypress Point Endoscopy LLC / d-e003cb27d820** records both specialists `SUFFICIENT`, four Evidence findings, `CORROBORATING` confirmed by the Validator, eight source checks and revision 2. Its decision is **AWAITING_VALUATION / NEXT_VALUATION**: investigation checks passed; valuation is still required. It has no financial workflow or sandbox payment. The read-only UI can also derive a conditional building-repair price opinion from registered financial sources (currently $91,250), which is not a financial proposal, coverage determination, settlement recommendation or payment authorization. This is a previously recorded live run, not a new live execution performed during UI verification.

A separate preserved investigation for **CLM-26-876731** records completed specialists and independent validation followed by **HITL_REQUIRED**. A third, explicitly **synthetic finance** demonstration exercises deterministic calculation, synthetic human attestations, named CLI authorization, one $12,105 sandbox payment, timeout-after-write reconciliation and final `VERIFIED`. It is not the financial continuation of the positive live case. All displayed values are read from the selected SQLite database; missing records are never reconstructed.

Start the local UI with [dashboard installation instructions](losstrace_dashboard/README.md) and use the [30-second demo guide](losstrace_dashboard/DEMO_GUIDE.md).

**Crucial distinction:** Source file hashes and exact passages verify *provenance*, not that the asserted damage is covered or caused by the loss. Financial scope decisions in this MVP are **human attestations**, **not** independent Valuation Agent conclusions. The names supplied to CLI approvals are **not authenticated identities**. No real money is moved.

## Architecture / trust boundaries

| Execution path | Actual behavior |
|---|---|
| Live investigation | Registered synthetic dossier → read-only MCP retrieval → Coverage Agent → canonical merge → Evidence Agent → canonical merge → independent source/semantic Validator → Adaptive Coordinator routing → persisted valuation handoff or human-review decision and audit. Technical failures stop earlier and may also route to HITL. |
| Controlled supported-finance tests | Explicitly simulated sufficient findings and supported validation → source-byte revalidation → synthetic human scope attestations and page-exact quotes → deterministic financial/policy gate → separate named approval → idempotent SQLite sandbox write → reconciliation, fresh verification and audit. |

The supplied finance demonstration is a separate test fixture, not a continuation of the saved positive live investigation. Read-only price opinions are available for HITL dossiers without making them financially eligible.

The deterministic workflow owns authoritative state transitions. The Adaptive Coordinator and LLM specialists **cannot** approve/deny claims, fabricate human decisions or initiate a payment. The independent Validator is present in the actual investigation application, not just a demo. The `NEXT_VALUATION` routing recommendation is a handoff only: the supported-finance fixtures exercise a human-attested financial scope review instead of an autonomous Valuation Agent. The current builders can emit `SUFFICIENT` when their bounded source and interpretation checks pass. `NEXT_VALUATION` is not payment authorization.

## Quick start (Windows PowerShell)

Requires [`uv`](https://docs.astral.sh/uv/), Python 3.14 (selected by `.python-version`; project minimum >=3.11), and a Nebius Token Factory key for *live model* operations. Use `pyproject.toml` and `uv.lock` as the dependency source of truth; the old learning-stage `requirements.txt` is in `Archive/learning_docs/`.

```powershell
cd C:\Projects\LossTraceFinal
uv sync --frozen
uv run --frozen python -m unittest discover -p "test_*.py" -v
uv run --frozen python losstrace_workflow.py preflight --dossier d-11cd8b30545e
```

`preflight` only inspects the registered synthetic dataset and makes **no model calls**; the current CLI also initializes an empty local SQLite database if none exists.

To run a **fresh** investigation, select an *unused* dossier from `data/application_runtime/manifests/`, set a valid API key and invoke:

```powershell
# Configure NEBIUS_API_KEY privately before running; do not record it.
uv run --frozen python losstrace_workflow.py run --dossier YOUR_UNUSED_DOSSIER_ID
uv run --frozen python losstrace_workflow.py status --dossier YOUR_UNUSED_DOSSIER_ID
uv run --frozen python losstrace_workflow.py audit --dossier YOUR_UNUSED_DOSSIER_ID
```

**Do not commit or screen-record API keys.** The `run` command invokes live Nemotron calls and stores results in `data/losstrace_workflow.sqlite3`. Rerunning the same dossier is intentionally rejected: there is not yet a durable resume/rebase path.

## Read-only HITL demonstration

`d-11cd8b30545e` reached `HITL_REQUIRED`. The optional separately labeled **judge test build** contains the preserved synthetic demonstration databases: `data/demo_databases/scenario1-live.sqlite3`, `scenario3-positive-live.sqlite3`, and `scenario2-finance.sqlite3`. The public **source package** does not contain local SQLite checkpoints. See [test-build instructions](TEST_BUILD.md) for launching the saved demos without API credentials.

Independent of persisted workflow, the following displays only the *conditional price opinion*, never an approved amount:

```powershell
uv run --frozen python losstrace_workflow.py price-review --dossier d-11cd8b30545e
```

For an actually persisted `HITL_REQUIRED` case, `scope-review` remains read-only and `financial-preview` and `propose-financial` must fail closed.

## Human financial scope and local sandbox

The gates require persisted `PROVISIONAL_SUPPORT`, both specialists sufficient, current evidence, valid policy/basis checks and an intact audit. The positive saved live investigation meets the investigation sufficiency handoff but still requires valuation and the remaining financial controls. The supplied settlement demonstration uses an explicitly labeled controlled fixture from `test_step5_complete.py`. The Streamlit dashboard exposes no financial mutation actions. An HITL case cannot be advanced with fabricated decision JSON.

Generate a read-only decision template for an eligible saved dossier:

```powershell
uv run --frozen python losstrace_workflow.py decision-template --dossier YOUR_ELIGIBLE_DOSSIER_ID > decision.json
```

The human must review **every** line, replace the placeholder reviewer and set `attestation: true` only after verifying scope, causation, coverage and duplicate economic events. A supported line must use this data contract:

```json
{
  "line_id": "L1",
  "decision": "SUPPORTED",
  "supported_amount": "1000.00",
  "reason": "Human-reviewed, line-specific scope and causation analysis explaining the decision.",
  "support_passages": [
    {
      "artifact_id": "REGISTERED_NON_PRICE_PDF_ARTIFACT_ID",
      "page": 1,
      "excerpt": "An exact passage of at least forty characters copied from that PDF page."
    }
  ]
}
```

This is a **schema illustration, not a factual claim citation**. For excluded lines, use `decision: "EXCLUDED"`, `supported_amount: "0.00"`, and `support_passages: []`. Quotes are checked for exact whitespace-normalized presence on the stated page, using the manifest SHA-256; wrong pages, unregistered files and fabricated excerpts are rejected. **No LLM currently judges whether the quote semantically supports that specific line**—that remains a human responsibility. Non-text-scannable PDF support is not implemented.

For an explicitly controlled supported fixture with reviewed synthetic human decisions (never relabel it as live approval):

```powershell
uv run --frozen python losstrace_workflow.py propose-financial --dossier YOUR_ELIGIBLE_DOSSIER_ID --decisions decision.json
uv run --frozen python losstrace_workflow.py authorize --dossier YOUR_ELIGIBLE_DOSSIER_ID --approved-by "Different Human Reviewer" --confirm
uv run --frozen python losstrace_workflow.py execute --dossier YOUR_ELIGIBLE_DOSSIER_ID --confirm
uv run --frozen python losstrace_workflow.py verify --dossier YOUR_ELIGIBLE_DOSSIER_ID
uv run --frozen python losstrace_workflow.py audit --dossier YOUR_ELIGIBLE_DOSSIER_ID
```

If execution is uncertain, do **not** retry blindly; invoke `reconcile` to inspect the persisted sandbox ledger. The local application supports explicit `--simulate-timeout-before-apply` and `--simulate-timeout-after-apply` on `execute` for controlled demonstrations. An unreconciled missing write fails closed to HITL.

## Main source files

| Responsibility | Source |
|---|---|
| Durable application, financial review, authorization, sandbox, reconciliation, audit | `losstrace_workflow.py` |
| Typed case/evidence basis, revision, state machine | `models.py`, `case.py`, `state_machine.py` |
| Bounded specialist orchestration and deterministic routing | `coordinator.py`, `adaptive_coordinator.py`, `adaptive_routing.py`, `validated_coordinator.py` |
| Model specialists, independent semantic/source validation | `evidence_agent.py`, `coverage_agent.py`, `validation_agent.py` |
| Registered evidence and MCP access | `gateway.py`, `mcp_server.py`, `source_passages.py`, `data_contract/` |
| Deterministic financial and policy/authorization primitives | `financial.py`, `financial_stage.py`, `policy.py`, `authorization.py` |
| Unit/integration regressions | `test_*.py` |
| Synthetic, manifest-registered runtime dossier data | `data/application_runtime/` |
| Historical demos, superseded components, deferred experiments | `Archive/` |

## Reproducibility and accepted limitations

- **Automated test results:** The release suite uses simulated model/Validator outcomes for the supported financial path. Passing it does not prove that a *live* Nemotron Validator would approve a particular dossier. Test fixtures and human names are synthetic.
- **Independent Validator:** Validates investigation findings and their referenced source semantics. It does **not** independently validate individual dollar amounts or human financial-scope decisions.
- **Sandbox authorization:** Short-lived, action-bound, single-use, second-*name* CLI approval; CLI input is not authenticated or backed by a real identity provider. Do not use with live payment systems.
- **Sandbox transactions:** Local SQLite `sandbox_payments`, exact idempotency key, fresh-state reread, fail-closed uncertainty; no network payment write, no multiuser transaction coordinator.
- **Audit:** Locally hash-linked and checkpoint/ledger-checked; tamper-evident under ordinary modifications, but **not externally immutable** against an attacker who can rewrite the entire database and chain.
- **Evidence freshness:** All registered investigation artifact bytes are re-read and SHA-256 checked before proposal, authorization and dispatch, not just selected quotes. Missing, changed or inconsistent sources block progression. Hashes do not prove semantic validity. The manifest and filesystem remain trusted; this is not an atomic filesystem snapshot against concurrent malicious edits.
- **Financial parser:** Text-PDF patterns specific to the supplied synthetic estimates and independent reviews. The supplied policy applies limits before the deductible. Other invoice/mold/BPP categories are excluded; any payment history or nonzero/incomplete usage blocks this MVP rather than being silently ignored.
- **Audit compatibility:** New started, completed and failed events bind the entire checkpoint. Older audit formats remain readable but fail integrity verification and cannot authorize new financial actions. Existing databases are not migrated or re-signed; preserve history and use a fresh disposable demonstration database.
- **Runtime root:** Run commands from the project root. Live `run` rejects an alternate `--runtime-root` before database/model activity because MCP uses the default `data/application_runtime` root. Offline source/finance commands retain their root option.
- **Resilience:** An interrupted `IN_PROGRESS` investigation is not resumable, and targeted live follow-up is deferred. No Jev or autonomous Valuation Agent is included in this scope.
- **Release hygiene:** The UI-only enhancement excludes `.venv`, API keys, caches and SQLite databases. Publish only reviewed synthetic fixtures and sanitized source code. Keep API keys, personal records and local investigation databases out of the public source repository; the optional separately labeled judge test build may include the supplied synthetic saved-result databases. Do not mistake this synthetic dataset for production insurer data.
- **Impact evidence:** This is a focused synthetic commercial-property dataset with no production insurer integration, real monetary transfers, measured customer adoption or real-world efficiency benchmark. No broad accuracy or production-readiness claim is made.

See [`RELEASE_NOTES.md`](RELEASE_NOTES.md) for approved hardening changes; earlier learning-stage archives are omitted from the public source release. For hackathon presentation, show the preserved positive valuation handoff, the completed HITL investigation, and the separately labeled synthetic finance fixture.

## Truthful demonstration commands

Use a fresh disposable database for the live investigation; a valid private Nebius key is required. This is a real invocation plan, not a claim that a live run passed during remediation.

```powershell
$demoDb = Join-Path $env:TEMP ("losstrace-" + [guid]::NewGuid().ToString() + ".sqlite3")
uv run --frozen python losstrace_workflow.py --database $demoDb run --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb status --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb audit --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb financial-preview --dossier d-11cd8b30545e
```

Read the actual persisted decision: supported investigations may reach `AWAITING_VALUATION`, uncertainty may require HITL, and tool/model failures are incomplete attempts. No specific result or source-check count is guaranteed for a fresh run. The saved historical HITL case remains blocked for financial progression. Show Validator output only when recorded; audit integrity is separate from investigation success.

For the supported **synthetic sandbox** demonstration, these tests create disposable databases, fabricate sufficient findings/Validator approval, and supply synthetic human scope decisions and approver names. The registered PDFs, financial arithmetic, policy checks, CLI, SQLite execution and verification are real:

```powershell
uv run --frozen python -m unittest -v test_step5_complete.IntegratedFinancialTests.test_cli_commands_execute_full_sandbox_path test_step5_complete.IntegratedFinancialTests.test_timeout_after_apply_reconciles_without_retry test_step5_complete.IntegratedFinancialTests.test_timeout_before_apply_requires_hitl_no_retry
```

See `REMEDIATION_REPORT.md` for F01–F07, exact verification results, file changes and release limitations.


## Judge test build and open-source license

The release provides a source-only package and a separately labeled **synthetic saved-demo test build**. For the complete Windows launch procedure, read [TEST_BUILD.md](TEST_BUILD.md). The test build displays preserved synthetic cases without a Nebius key; fresh live inference requires the judge’s own key. The public source is distributed under the [MIT license](LICENSE).
