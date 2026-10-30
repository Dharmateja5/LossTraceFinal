# LossTrace — working judge test build (Windows)

This package contains **synthetic** saved investigation records, not any real insurer claim or real monetary transfer. No API key is needed to inspect the recorded results. A fresh live investigation makes external Nebius model calls and requires the tester's own `NEBIUS_API_KEY` and network access; it is optional and cannot be guaranteed to produce a specific decision.

## Setup and start

1. Extract the ZIP so the project root is `LossTraceFinal` (or open a terminal in the extracted folder containing `losstrace_workflow.py`).
2. Install `uv` if needed: <https://docs.astral.sh/uv/getting-started/installation/>. Install a supported Python version (the `.python-version` file requests Python 3.14).
3. Run in PowerShell from the project root:

```powershell
uv sync --frozen
$positiveDb = (Resolve-Path 'data/demo_databases/scenario3-positive-live.sqlite3').Path
$hitlDb = (Resolve-Path 'data/demo_databases/scenario1-live.sqlite3').Path
$financeDb = (Resolve-Path 'data/demo_databases/scenario2-finance.sqlite3').Path
powershell -NoProfile -ExecutionPolicy Bypass -File .\losstrace_dashboard\launch.ps1 -PositiveDb "$positiveDb" -LiveDb "$hitlDb" -FinanceDb "$financeDb"
```

Open **http://127.0.0.1:8501** in the same computer's browser. This is a local test build, **not a remotely hosted website**. Network access may be needed to install dependencies; a Nebius key is not needed for the preserved saved demos.

## Three demonstrations

- **Positive live recorded investigation** (`CLM-26-591035`): actual saved Nemotron investigation result `AWAITING_VALUATION`, eight recorded source checks; no payment or valuation completed. Its Controlled Settlement tab shows a read-only conditional building-repair price opinion (currently $91,250 on the preserved documents), explicitly NOT an approved settlement.
- **Completed human review investigation** (`CLM-26-876731`): saved `HITL_REQUIRED` after completed specialist and independent checks; no financial action.
- **Synthetic finance demonstration**: simulated sufficient findings and human attestations, real deterministic financial controls, a $12,105 **sandbox-only** payment and reconciliation. This is **not** a continuation of the positive live claim.

The application is read-only for inspecting saved results. A newly triggered live investigation uses the host computer's Nebius API key only if the operator explicitly requests it. No API key is bundled or provided.

See [`losstrace_dashboard/DEMO_GUIDE.md`](losstrace_dashboard/DEMO_GUIDE.md) for a 30-second judge tour. The positive result may require source-byte verification against the included registered PDFs; do not edit evidence files after the saved run.

## Reproducibility and limitations

Saved records are genuine previously recorded outcomes, not fresh model calls made by the judge test build. This hackathon prototype uses a bounded synthetic commercial property dataset and a local SQLite sandbox. It is not a production claim adjudication or payment system. The project has no external identity provider, no production insurer integration and no real payments.
