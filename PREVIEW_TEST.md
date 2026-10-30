# LossTrace — Conditional financial preview demo (local Windows test)

This test build adds **one read-only UI feature** to the positive live investigation. The authoritative backend, financial gates, audit, and all saved SQLite databases are unchanged.

## Launch on Windows PowerShell

From a working Windows 11 terminal, with `uv` installed:

```powershell
$folder = 'C:\Projects\LossTracePreview'
Expand-Archive -LiteralPath "$HOME\Downloads\LossTrace_Conditional_Price_Preview_Test_Build.zip" -DestinationPath $folder -Force
cd "$folder\LossTraceFinal"
uv sync --frozen
$positiveDb = (Resolve-Path 'data\demo_databases\scenario3-positive-live.sqlite3').Path
$hitlDb = (Resolve-Path 'data\demo_databases\scenario1-live.sqlite3').Path
$financeDb = (Resolve-Path 'data\demo_databases\scenario2-finance.sqlite3').Path
powershell -NoProfile -ExecutionPolicy Bypass -File .\losstrace_dashboard\launch.ps1 -PositiveDb "$positiveDb" -LiveDb "$hitlDb" -FinanceDb "$financeDb" -Port 8502
```

Visit **http://127.0.0.1:8502**. The preview uses no Nebius API calls.

## Three things to inspect

1. Choose **Positive live investigation** (`CLM-26-591035`) and the **Controlled Settlement** tab. It should show **Conditional price opinion $91,250**, breakdown ($87,500 reviewed repair base + $8,750 O&P − $5,000 deductible), seven line items and **Financial action permitted: NO**. It must also show **no financial proposal** and **zero sandbox payment rows**. This value is calculated on demand from the registered PDFs, not hardcoded in the UI.
2. Choose **Human review investigation** (`CLM-26-876731`). No conditional preview or payment should appear.
3. Choose **Synthetic sandbox settlement**. The separate original $12,105 sandbox payment and reconciliation should remain visible.

The backend gate revalidates evidence-source hashes and rejects invalid/stale investigation bases. A blocked preview is not silently replaced by a dollar amount.

No financial decision, policy coverage, settlement approval or payment is implied by the positive claim's conditional price opinion. Keep your previous test build untouched for rollback.
