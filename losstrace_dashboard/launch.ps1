param(
    [string]$LiveDb = "",
    [string]$PositiveDb = "",
    [string]$FinanceDb = "",
    [string[]]$InvestigationDb = @(),
    [ValidateRange(1024, 65535)]
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $projectRoot "losstrace_workflow.py"))) {
    throw "Place losstrace_dashboard beside losstrace_workflow.py in your existing release."
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv must be installed and available on PATH."
}
if (-not $FinanceDb) { $FinanceDb = Join-Path $projectRoot "data\losstrace_finance_demo.sqlite3" }

Push-Location $projectRoot
try {
    # --with supplies a UI overlay; --frozen leaves backend dependency files intact.
    # Backend subprocess inherits existing local credentials. Never print them.
    $appArgs = @()
    if ($LiveDb) { $appArgs += @("--live-db", $LiveDb) }
    if ($PositiveDb) { $appArgs += @("--positive-db", $PositiveDb) }
    if ($FinanceDb) { $appArgs += @("--finance-db", $FinanceDb) }
    foreach ($db in $InvestigationDb) { $appArgs += @("--investigation-db", $db) }
    & uv run --frozen --with "streamlit==1.58.0" python -m streamlit run `
        "losstrace_dashboard/app.py" `
        "--server.address=127.0.0.1" "--server.port=$Port" `
        "--server.headless=true" "--browser.gatherUsageStats=false" `
        "--client.showErrorDetails=false" `
        "--theme.base=dark" "--theme.primaryColor=#3BE0CC" `
        "--theme.backgroundColor=#071324" "--theme.secondaryBackgroundColor=#102740" `
        "--theme.textColor=#EDF5FF" `
        -- @appArgs
    if ($LASTEXITCODE -ne 0) { throw "Dashboard process exited with code $LASTEXITCODE." }
}
finally {
    Pop-Location
}
