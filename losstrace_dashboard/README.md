# LossTrace — judging-ready investigation workspace

Evidence-grounded AI claims investigation with independent validation and controlled settlement, for commercial-property claims investigators and claims operations teams.

This release updates the **supplied LossTraceFinal.zip dashboard**, preserving its dark navy/teal design, registered claim summaries, read-only database adapter and secure explicit investigation launcher. The attachment contained backend, UI and preserved databases in one archive; no older UI or Evidence Agent was substituted.

## Install on Windows

Stop the current Streamlit server with Ctrl+C. Leave all backend files and saved databases in place. Download `LossTrace_Judging_Ready_UI.zip` to Downloads, then run:

```powershell
Set-Location C:\Projects\LossTraceFinal
$zip = Join-Path $env:USERPROFILE 'Downloads\LossTrace_Judging_Ready_UI.zip'
if (-not (Test-Path -LiteralPath $zip)) { throw 'Download the UI ZIP first.' }
if (-not (Test-Path .\losstrace_workflow.py)) { throw 'Use the existing LossTraceFinal project root.' }
$backup = Join-Path (Get-Location) ('ui_backup_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
Copy-Item .\losstrace_dashboard $backup -Recurse
Copy-Item .\README.md $backup
Expand-Archive -LiteralPath $zip -DestinationPath . -Force
Write-Host "Rollback backup: $backup"
```

The package contains `losstrace_dashboard/` and the corrected root `README.md` only. It does not contain backend Python, databases, `.env`, `.venv`, caches, `pyproject.toml` or `uv.lock`. Do not replace the latest Nemotron 3 Super backend. The launcher supplies Streamlit 1.58.0 as a `uv --with` overlay using the existing frozen environment; no dependency file edits are needed.

## Connect the three preserved databases and launch

These exact files were present in the supplied project. Use your existing copies, not newly generated fixtures:

```powershell
Set-Location C:\Projects\LossTraceFinal
$positiveDb = Join-Path (Get-Location) 'data\demo_databases\scenario3-positive-live.sqlite3'
$hitlDb = Join-Path (Get-Location) 'data\demo_databases\scenario1-live.sqlite3'
$financeDb = Join-Path (Get-Location) 'data\demo_databases\scenario2-finance.sqlite3'
foreach ($db in @($positiveDb, $hitlDb, $financeDb)) {
    if (-not (Test-Path -LiteralPath $db -PathType Leaf)) { throw "Preserved demonstration database missing: $db" }
}
powershell -NoProfile -ExecutionPolicy Bypass -File .\losstrace_dashboard\launch.ps1 `
    -PositiveDb "$positiveDb" -LiveDb "$hitlDb" -FinanceDb "$financeDb"
```

Open **http://127.0.0.1:8501**. This is a local demo, not a public deployment. Existing private Nebius configuration is inherited only when an explicit live run starts; do not print or screen-record keys.

Equivalent direct command, in the same PowerShell session:

```powershell
uv run --frozen --with "streamlit==1.58.0" python -m streamlit run losstrace_dashboard/app.py `
    --server.address=127.0.0.1 --server.port=8501 --server.headless=true `
    --browser.gatherUsageStats=false --client.showErrorDetails=false `
    --theme.base=dark --theme.primaryColor="#3BE0CC" `
    --theme.backgroundColor="#071324" --theme.secondaryBackgroundColor="#102740" `
    --theme.textColor="#EDF5FF" -- `
    --positive-db "$positiveDb" --live-db "$hitlDb" --finance-db "$financeDb"
```

If files live elsewhere, change only those paths. `--investigation-db` can be repeated for additional explicitly registered investigation databases. The launcher also accepts `-InvestigationDb` as an array. Sidebar **Database connections** exposes the same registrations. The finance database must remain separate from live registrations and outside `data/dashboard_runs`. No arbitrary filesystem scan is performed.

## Use the workspace

- **Claims Workspace**: on the first visit, features the positive demonstration only if its matching persisted record, audit and current source verification qualify. Otherwise normal claim browsing remains available. This does not override an existing user selection.
- **Browse & search registered claims**: expand the compact table, filter by claim ID, summary or cause, and select a row. Verified local runtime claim/site data supplies summaries; verified policy data supplies the insured name. Missing fields say `Not available`. Native table selection falls back to a searchable selectbox if the installed environment cannot render it.
- **Positive live investigation**: opens the qualified saved CLM-26-591035 record. The green panel means investigation checks passed and valuation is next, never settlement approval.
- **Human review investigation**: opens a completed, independently validated CLM-26-876731 HITL investigation. A technically failed attempt is not silently substituted.
- **Synthetic sandbox settlement**: separate database and clear synthetic label. View **Controlled Settlement** for actual proposal, named approval, payment, idempotency, timeout/reconciliation and final verification. No financial mutation controls exist.
- **Saved runs & claim details**: select a historical run by timestamp, run ID, state and database filename. Different database/run identities remain separate, even if a preserved copy contains the same run ID. History is never overwritten or merged.
- The three existing tabs remain: **Investigation Overview**, **Evidence & Validation**, **Controlled Settlement**. Quotes, model interpretations and independent Validator explanations are explicitly distinguished.

If a requested demo is absent, stale, incomplete or unverifiable, its shortcut explains the missing qualification. Use Claims Workspace to inspect historical records, including failures and audit warnings. No successful substitute is manufactured. The latest run may differ from a selected historical run; both timestamps/statuses are labeled.

## Start a new live investigation

1. Choose **Claims Workspace**, expand the table and select an actually registered claim with no saved investigation or prior launch receipt.
2. Click **Run Live Investigation** explicitly. Merely selecting, refreshing, navigating or reopening saved records never launches a model.
3. The unchanged UI launcher invokes the fixed command from the fixed project root:
   `uv run --frozen python losstrace_workflow.py --database <unique_new_db> run --dossier <verified_id>`.
4. The child uses a unique persistent SQLite file in `data/dashboard_runs`. Polling observes the original process; on exit the UI reads its saved result, including conservative HITL or failure. Process exit alone is not validation success.

The integration retains an exclusive cross-session marker, duplicate-click prevention, `shell=False`, trusted dossier validation, no arbitrary commands and no existing-database reuse. A timeout leaves a potentially running process blocked from relaunch. Server restart with a lost process handle reports completion unknown. Do not blindly delete `active.json` or retry: stop and establish the original process's completion, inspect its database, and resolve the marker manually outside the UI only after the process is confirmed stopped. UI-triggered fresh reruns of previously investigated claims remain intentionally unavailable.

Read paths retain SQLite `mode=ro`, `query_only=ON`, authorizer restrictions and the backend's actual read-only audit verifier. The UI never initializes schemas for viewing. Audit integrity, evidence freshness and semantic correctness remain distinct.

## Tests

```powershell
Set-Location C:\Projects\LossTraceFinal
uv run --frozen --with "streamlit==1.58.0" python -m unittest discover -s losstrace_dashboard/tests -p "test_*.py" -v
uv run --frozen python -m unittest discover -p "test_*.py" -v
```

Frontend fixture tests isolate their discovery from an operator's existing run directory. The supplied-record test reads the three named demo databases if present and otherwise explicitly skips; it never creates replacement demonstration data. External model calls are mocked. Existing backend tests are unchanged. See **ENHANCEMENT_REPORT.md** and `verification/` for tests independently executed during this release, not historical logs.

## Roll back

Stop Streamlit. Use the backup path printed during installation:

```powershell
Set-Location C:\Projects\LossTraceFinal
$backup = 'C:\Projects\LossTraceFinal\ui_backup_YYYYMMDD_HHMMSS' # replace with the actual path
if (-not (Test-Path "$backup\losstrace_dashboard\app.py")) { throw 'Choose the actual UI backup.' }
Remove-Item .\losstrace_dashboard -Recurse -Force
Copy-Item "$backup\losstrace_dashboard" . -Recurse
Copy-Item "$backup\README.md" .\README.md -Force
```

Then run the restored launch command. Rollback touches only UI and README, never `data/`, backend modules or the frozen dependency files.

## Limits

This is a focused synthetic commercial-property dataset and a local hackathon prototype. There is no production insurer integration, real money transfer, measured customer adoption, real-world efficiency benchmark, Jev model or autonomous Valuation Agent. The positive live case still requires valuation; the finance demo uses simulated specialist/Validator sufficiency and synthetic human attestations. CLI approver names are not authenticated identities. Local hash-linked SQLite history is not externally immutable storage. Source quotations and hashes establish provenance, not final coverage approval or semantic truth; the historical POTENTIAL_CONFLICT interpretation remains open to review.

The saved records do not independently prove the model endpoint or model version used historically; current source code selects Nemotron 3 Super. No new live Nebius investigation was run during UI testing. Windows PowerShell execution and browser pixel/layout verification must still be checked locally; actual screenshots were unavailable because the browser binary download failed in the verification environment.


### Conditional price review for positive investigations

When a saved claim truly reaches `AWAITING_VALUATION` and passes the existing audit, sufficiency, semantic-validation and registered source-byte gates, the Controlled Settlement tab shows an **on-demand, read-only conditional price opinion**, not a financial proposal or settlement. It uses `financial_stage.build_gated_financial_preview` unchanged and does not store the preview, approve line scope or execute a payment. On the preserved positive claim, current registered reviewed repair prices yield **$91,250**; the number is recalculated from the actual source documents and may be unavailable if the sources or gates change. The separate synthetic finance demo still shows its own persisted $12,105 sandbox payment.
