# LossTrace focused release remediation — 2026-10-29

**Verdict: READY WITH DOCUMENTED LIMITATIONS for a labeled local hackathon sandbox.**

The approved F01–F07 changes are complete. All **99 regression tests pass** in the frozen Python 3.14 environment; the same 99 also pass in the available Python 3.12 environment. Actual local MCP retrieval and the Validator's MCP source-loading path pass. **No live Nebius/Nemotron model call was made** because no API credential was configured. A Windows machine and a live model rehearsal remain unverified.

The Evidence Agent, Coverage Agent, Independent Validation Agent, Adaptive Coordinator and financial architecture are preserved. No live finding was promoted to `SUFFICIENT`, no HITL/policy control was bypassed, and no new agent, framework or dependency was added. Only three application modules changed. Registered datasets, archived files, dependency declarations and lockfile are unchanged.

## Source preservation and compatibility

The working copy came from `LossTrace_Hackathon_Release_Candidate(1).zip`. Its original SHA-256 remains:

```text
b3e4a7ea6b85dddcdb804225139ddc58a20bad4efdccdfb2541ec71da83d74f0
```

The original extraction was compared byte-for-byte with the original ZIP after remediation. No original files were removed or renamed. Tests use temporary databases and copied datasets; no existing real investigation database was modified. The release preserves the `LossTrace/` project root, including `Archive/` and all runtime data.

Existing CLI command names and flags are preserved. Live `run` now rejects an alternate runtime root; an absolute path resolving to the default root is accepted. Other commands retain their runtime-root option.

**Deliberate fail-closed compatibility limit:** old audit events do not bind every checkpoint field. They remain readable but fail the new integrity check and cannot advance financial actions. No automatic migration, re-signing or deletion of history is performed. The compatibility regression reconstructs the old event format only in a disposable database. Preserve historical databases; use a fresh disposable database for demonstrations.

Financial result field names are unchanged. Their stage values now follow policy order: `amount_after_policy_limit` is capped gross loss; `amount_after_deductible` is the subsequent net amount. At/above-limit results intentionally change.

## F01–F07 implementation and evidence

| Finding | Implemented correction and source | Regression evidence | Before → after |
|---|---|---|---|
| **F01 P0** | `financial_stage.py:92`, `_manifest_and_state`: validate manifest identity, uniqueness and snapshot, then read **every registered artifact's actual bytes**, enforce dossier boundaries and compare SHA-256. The existing proposal/authorization/dispatch calls all reach this check. `losstrace_workflow.py:812`, `_check_financial_basis`, translates source gate failures into financial workflow errors. | `EvidenceFreshnessTests`: changed/missing survey/service/policy/email/spreadsheet/record inputs across all three financial boundaries; tamper each of the 21 registered artifacts in the test dossier; manifest identity/hash mismatches; intact positive dispatch. Denials preserve unconsumed grants and write no payments. The smoke check also verifies all 305 artifacts across 15 dossiers. | Previously accepted non-financial-source changes now reject. Existing stale-metadata rejection is preserved with a controlled CLI error. |
| **F02 P1** | `financial.py:94`, `calculate_payable`: O&P → policy limit → deductible → prior-payment subtraction. The supplied coverage form explicitly orders limits before the remaining deductible. The MVP financial stage still rejects all prior-payment histories. | `PolicyOrderTests`: supplied wording; 12 limit/deductible boundary cases; zero limit/deductible; cent boundaries; O&P half-up rounding and low-level prior-payment arithmetic. | Example: gross $120,000, limit $100,000, deductible $5,000 changes from $100,000 to **$95,000**. Original positive financial amounts remain passing. |
| **F03 P1** | `README.md`, architecture, supported-finance section and demonstration commands; `RELEASE_NOTES.md`. Explicit mandatory live HITL and separately labeled fixture finance. | `RuntimeAndNarrativeTests.test_readme_explicitly_distinguishes_live_and_fixture_routes`, plus manual comparison against unchanged specialist builders and actual controlled routing tests. | Misleading live-to-finance implication removed. No runtime gate weakened. |
| **F04 P1** | `losstrace_workflow.py:1157`, `main`: reject a live `run` root differing from MCP's default before opening a DB or running agents. | `RuntimeAndNarrativeTests`: alternate root exits with code 2, no DB and no run invocation; equivalent absolute default path is accepted. Actual default-root MCP smoke passes. | Alternate-root split basis previously entered the workflow; now rejected. |
| **F05 P1** | `WorkflowStore._checkpoint_binding` at `losstrace_workflow.py:143`; `begin`, `finish`, `fail`, `verify_audit`. Bind all 12 checkpoint fields in started/completed/failed audit events. Reject tampered active checkpoints before finish/failure can bind them again. | `CheckpointAuditTests`: independently tamper every persisted field, check started/failed stages, block finance after reason edits, prevent rebinding a tampered start, and reject old unbound history without rewriting it. Existing proposal/ledger tamper tests still pass. | Status, next action, reason, case version, dossier identity and timestamp changes are now detected. |
| **F06 P1** | `financial_stage.py:178`, `_verified_context`: require an explicitly empty payment list; require usage-map keys to match policy coverage/sublimits; validate required money fields and require all usage to be zero. | `HistoricalPaymentTests`: payment entry despite zero aggregates; missing/non-list history; missing/empty/unknown usage; nonzero/missing/invalid totals. Tests re-register ledger bytes to isolate business-rule enforcement from stale hashes. | Nonempty/inconsistent history formerly accepted or raised incidental type/key errors; now deliberately blocked. |
| **F07 P1** | `test_investigation_paths.py` exercises actual Validator and Coordinator functions. `test_step5_complete.py:201` tests expiry with a patched clock and intact audit; `ExpiryTests` tests after expiry. | 9 Validator tests, 7 Coordinator tests, exact-expiry and after-expiry rejection; actual local MCP smoke with simulated semantic judgments. | Existing Validator/coordinator and expiry behavior already passed these controlled checks. This closes a coverage gap; no agent implementation change was needed. |

## File-by-file change log

| File | Change |
|---|---|
| `financial_stage.py` | All-artifact byte revalidation, manifest identity/path checks, strict empty-history and usage-map gates. |
| `financial.py` | Limit-before-deductible calculation order; stable result schema. |
| `losstrace_workflow.py` | Complete checkpoint audit binding and verification; protect active checkpoint commits; controlled financial source errors; live runtime-root rejection. |
| `test_financial_stage.py` | Hash-tamper test now copies the complete dossier so the intended changed estimate, rather than an unrelated missing artifact, causes rejection. |
| `test_step5_complete.py` | Replace expiry test's database tampering with clock advancement, exact expiry error assertion, intact audit and unconsumed grant assertions. |
| `test_remediation.py` — new | 30 focused test methods for evidence, financial order, checkpoint integrity, historical ledger, roots/narrative and expiry; disposable mutations only. |
| `test_investigation_paths.py` — new | 16 tests using actual Validator/coordinator/merge/routing/persistence functions with explicitly controlled responses. |
| `README.md` | Accurate live/fixture architecture, mandatory HITL, frozen setup, old-audit limitation, runtime-root rule and truthful demonstration commands. |
| `RELEASE_NOTES.md` | New remediation summary and compatibility notes; prior release notes retained. |
| `REMEDIATION_REPORT.md` — new | This report, finding mapping, results, remaining risks and exact Windows commands. |
| `verification/runtime_smoke.py` — new | Reproducible imports, nine real local MCP tools, Coverage context loader, Validator source path with fake semantic judge, all 305 artifact hashes and all 15 policy wordings. No model calls or database writes. |
| `verification/BEFORE_LOCKED_TESTS.txt` — new | Raw 46-test regression run against a separate original-source copy. |
| `verification/FOCUSED_LOCKED_TESTS.txt` — new | Raw 46-test corrected focused run. |
| `verification/REGRESSION_LOCKED_TESTS.txt` — new | Raw final 99-test frozen-environment run. |
| `verification/REGRESSION_HOST_TESTS.txt` — new | Raw final 99-test host-environment run. |
| `verification/LOCKED_SETUP.txt` — new | Successful frozen dependency installation in an isolated environment. |
| `verification/RUNTIME_SMOKE.txt` — new | Successful final import, local MCP, Validator-source and dataset smoke output. |

All other original files are unchanged. No archive restoration, source movement, dependency update or general refactor was performed.

## Executed verification

| Check | Actual result |
|---|---|
| Regression proof against original source plus the final new tests | **46 methods run; 25 methods had failures/errors, 21 passed.** `unittest` reports 54 failed assertions/subtests and 4 errors. These are expected pre-fix failures, not 58 distinct tests. |
| Focused corrected suite, locked environment | **46/46 PASS**, 3.986 s. |
| Complete corrected suite, locked environment | **99/99 PASS**, 13.905 s. |
| Complete corrected suite, host environment | **99/99 PASS**, 10.787 s. |
| Frozen environment | `uv sync --frozen` succeeded, Python **3.14.8**, MCP **2.3.0**, OpenAI **3.26.1**, Pydantic **2.13.5**, PyMuPDF **1.28.2**. Environment and cache are outside the release directory. |
| Host compatibility check | Python **3.12.14**, Pydantic **2.14.1**, PyMuPDF **1.26.6**. This is supplementary evidence, not a replacement for the locked dependencies. |
| Import/runtime smoke | **PASS**: all 18 active modules import; all nine MCP tool calls succeed; actual Coverage context loader succeeds; actual Validator uses MCP and checks six source items with five simulated semantic verdicts. |
| Full registered dataset | **PASS**: all 305 registered artifacts in all 15 dossiers match their bytes; every supplied coverage form contains limit-before-deductible wording. |
| Original preservation | **PASS**: original ZIP SHA-256 unchanged and original extracted files byte-identical to it. |
| Live Nebius/Nemotron | **NOT RUN**: no configured API key. No claim of live semantic approval or live end-to-end investigation success. |
| Windows execution | **NOT RUN**: execution environment was Linux. Commands below use PowerShell syntax; Windows-specific behavior needs local rehearsal. |

The original suite had 53 methods. This release retains them and adds 46. Earlier incremental runs progressed from failing regression proofs to 40/40 focused and 93/93 full before the final six edge-case methods were added; the table and bundled logs record the final test set. One initial ad hoc MCP/Validator smoke invocation used an incorrect keyword argument and failed before validation; the shipped smoke script uses the actual `judge` API and passed. That invocation required no application change.

The complete suite includes real SQLite/CLI positive and negative finance paths: supported synthetic payment, no authorization, self-name approval rejection, authority threshold, fabricated/wrong-page/missing citations, HITL denial, all-excluded denial, duplicate proposal/execution, proposal/ledger tampering, timeout before/after apply, reconciliation and fresh verification. The positive fixture still calculates **$12,105.00**. The new evidence failures leave the grant unconsumed and payment table empty.

Test classification: financial arithmetic and gate tests are unit/component tests; gateway/PDF/SQLite tests are local integration tests; the subprocess CLI test is controlled end-to-end. Coordinator tests use controlled specialist responses and a simulated semantic judge while executing real orchestration. Supported financial fixtures directly create sufficient specialist findings and supported validation; they are **not** live Validator approval. The smoke test uses actual in-process MCP transport, not a remote authenticated MCP deployment.

## Remaining limitations and readiness boundaries

1. **Live mandatory HITL is intentional.** The existing specialist builders return `INSUFFICIENT`; a favorable semantic verdict does not make the investigation financially eligible. Targeted live follow-up and durable investigation resume remain deferred.
2. **Financial scope is human-attested.** An authentic excerpt can be irrelevant to a particular dollar line. Exact quotes and SHA-256 demonstrate provenance, not causal/coverage sufficiency. No new semantic finance validator was introduced.
3. **Approval names are not authenticated identities.** Name separation, action binding, expiry and single use are useful sandbox controls, not enterprise identity/separation-of-duties enforcement.
4. **The local database and manifest remain trust boundaries.** Hash chains detect ordinary checkpoint/ledger changes; someone able to rewrite the entire chain can forge local history. Evidence checks occur at proposal, authorization and dispatch; they are not an atomic filesystem snapshot against concurrent malicious edits. `sandbox_apply` consumes an already-dispatched intent and does not independently reopen the filesystem basis.
5. **Narrow financial scope.** Text-PDF patterns target the supplied synthetic data. Historical payment allocation, mold/BPP/invoice components, other policy forms and nonzero prior usage remain unsupported and blocked/excluded as documented.
6. **Legacy audit records fail closed.** Their prior integrity cannot be retroactively certified. Reconciliation/verification commands requiring a valid audit may also reject an old database; no real old database was upgraded during this work.
7. **No production payment/security claim.** There is no real payment rail, authenticated multiuser service, external immutable audit or live penetration-test certification. Dependency locks were successfully installed; a current external vulnerability advisory scan was not performed.

No approved finding remains knowingly unimplemented. Submission is credible when presented as a live HITL investigation plus explicitly synthetic supported-finance controls. A live credentialed rehearsal and Windows rehearsal remain the final environment-dependent checks; neither is represented as passed.

## Exact Windows PowerShell setup and verification

Prerequisite: `uv` is installed and on PATH. Place the release ZIP at the path below, or change only that path. Use a new destination directory; do not extract over an existing investigation installation. Run all commands from the project root. No new dependencies beyond the existing lockfile are required.

```powershell
$zip = Join-Path $env:USERPROFILE "Downloads\LossTrace_Hackathon_Remediated_Release.zip"
$releaseDir = Join-Path $env:USERPROFILE ("LossTrace-Remediated-" + [guid]::NewGuid().ToString())
Expand-Archive -LiteralPath $zip -DestinationPath $releaseDir
Set-Location (Join-Path $releaseDir "LossTrace")

uv sync --frozen
if ($LASTEXITCODE -ne 0) { throw "Locked setup failed" }

uv run --frozen python -m unittest test_remediation test_investigation_paths -v
if ($LASTEXITCODE -ne 0) { throw "Focused regressions failed" }

uv run --frozen python -m unittest discover -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { throw "Full regressions failed" }

uv run --frozen python verification/runtime_smoke.py
if ($LASTEXITCODE -ne 0) { throw "Imports/MCP/dataset smoke failed" }
```

Expected: 46 focused tests, 99 full tests, then the actual local MCP/dataset smoke passes. None requires a Nebius API key. No original investigation DB is referenced.

### Demonstration 1: real investigation that remains at HITL

Configure `NEBIUS_API_KEY` privately in the shell before these commands; never record it. Use a fresh database. This performs billable live model calls and real local MCP retrieval.

```powershell
if (-not $env:NEBIUS_API_KEY) { throw "Configure NEBIUS_API_KEY privately first" }
$demoDb = Join-Path $env:TEMP ("losstrace-live-" + [guid]::NewGuid().ToString() + ".sqlite3")
uv run --frozen python losstrace_workflow.py --database $demoDb preflight --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb run --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb status --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb audit --dossier d-11cd8b30545e
uv run --frozen python losstrace_workflow.py --database $demoDb financial-preview --dossier d-11cd8b30545e
if ($LASTEXITCODE -eq 0) { throw "Unexpected financial eligibility in live HITL demo" }
```

Present the actual finding/Validator output and persisted HITL reason. Expected finance rejection is part of the demonstration. If retrieval/model validation fails early, report a blocked attempt and its actual reason; do not label it successful live agent validation. Do not edit the DB, findings or status to force an outcome.

### Demonstration 2: supported synthetic sandbox finance

```powershell
uv run --frozen python -m unittest -v test_step5_complete.IntegratedFinancialTests.test_cli_commands_execute_full_sandbox_path test_step5_complete.IntegratedFinancialTests.test_timeout_after_apply_reconciles_without_retry test_step5_complete.IntegratedFinancialTests.test_timeout_before_apply_requires_hitl_no_retry
if ($LASTEXITCODE -ne 0) { throw "Controlled finance demonstration failed" }
```

Label this demonstration: **simulated supported findings and validation; synthetic human scope approval and supervisor name; real financial/CLI/SQLite controls; no real money.** The CLI test exercises proposal, authorization, a write with simulated lost response, reconciliation, fresh verification, audit and repeat-execution rejection. The missing-write case stops at HITL. All demonstration databases are temporary.

## Final acceptance checklist

- [x] F01–F07 implemented with focused regressions; original failures demonstrated where practical.
- [x] Frozen dependencies install; 46 focused and 99 full regressions pass.
- [x] Actual module imports, local MCP and all registered source hashes pass.
- [x] Existing positive and negative sandbox finance paths remain passing.
- [x] Live sufficiency/HITL rules, original ZIP and original data are preserved.
- [x] Legacy audit incompatibility and synthetic approvals are explicitly disclosed.
- [ ] Rehearse the commands on the submission Windows machine.
- [ ] Perform one credentialed live investigation; present only its actual outcome.
- [ ] Confirm the submission narrative makes no production identity/payment or live supported-finance claim.

Release packaging must contain no virtual environment, API key, generated cache or local investigation database. The release ZIP is a new artifact; it does not replace the original upload.
