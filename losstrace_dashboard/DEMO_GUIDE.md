# LossTrace — judge demonstration guide

## First 30 seconds

**0–10 seconds — purpose and result.** Open the configured application. “LossTrace supports commercial-property investigators with evidence-grounded AI investigation, independent validation and controlled settlement. This saved Cypress Point Endoscopy investigation passed its checks and can proceed to valuation.” Point to the actual `AWAITING_VALUATION`, `NEXT_VALUATION`, source-check count and Validator outcome. It is not settlement approval.

**10–20 seconds — implementation.** Follow Coverage Agent → Evidence Agent → Independent Validator → Workflow Decision. “Nemotron specialists use MCP-retrieved sources through Nebius Token Factory. The independent Validator checks referenced text and bounded interpretations. A deterministic Coordinator controls progression.” Expand a source passage in Evidence & Validation: distinguish source quotation, specialist interpretation and Validator explanation. Current code selects NVIDIA Nemotron 3 Super; stored checkpoints do not themselves attest the historical provider/model version.

**20–30 seconds — bounded autonomy.** Choose Human review investigation. “When evidence remains uncertain, the system preserves the findings, escalates to human review and blocks unsupported financial progression.” Before switching, open the **Controlled Settlement** tab for the positive live claim: the existing deterministic engine derives a **conditional price opinion** (currently $91,250 from the preserved registered documents), explicitly **not** an approved payout. No financial proposal or payment is recorded. Then choose Synthetic sandbox settlement and Controlled Settlement: “This separate synthetic investigation demonstrates deterministic calculations, named authorization, one idempotent sandbox payment and reconciliation after an uncertain response. It is not a payment from the positive live case.”

## Preserved records independently inspected in this release

| Saved demo | Recorded facts | Boundary |
|---|---|---|
| CLM-26-591035, d-e003cb27d820 | Both specialists SUFFICIENT; four Evidence findings; CORROBORATING reported and validated; eight source checks; revision 2; AWAITING_VALUATION / NEXT_VALUATION; audit PASS | Valuation still required; no financial workflow or payment |
| CLM-26-876731, d-11cd8b30545e | Completed Coverage/Evidence execution steps; five Evidence findings; nine source checks; independent HITL_REQUIRED; audit PASS | Human review, not technical failure; no financial workflow or payment |
| Separate synthetic finance database | Simulated specialist/Validator sufficiency; actual $12,105 proposal/payment; action-bound named approval; timeout-after-write event; reconciliation; VERIFIED; audit PASS | Synthetic human attestations and investigation; local sandbox only |

These are observations of the supplied saved SQLite records, not new live calls. The UI has no constants imposing these counts, amounts or outcomes on other runs. Demo claim IDs serve only as navigation hints. A missing or unverified record yields an honest empty state; all historical attempts remain accessible from Claims Workspace.

## Judging story

- **Technological implementation:** real specialist/Validator code, MCP retrieval, verified source references, deterministic routing and persisted audit. Execution acceptance and evidence sufficiency are shown separately.
- **Design:** a clear decision first, compact KPIs, consistent stage cards and expandable supporting detail in the existing dark Streamlit design.
- **Potential impact:** supports defensible investigation and accountable escalation across policy, inspection and plumber evidence. No unsupported claim of measured savings, accuracy or ROI.
- **Idea quality:** bounded agentic autonomy: independently validated evidence inside deterministic financial and human-review boundaries.

## Demonstration discipline

Use the saved records during the judged walkthrough. If demonstrating a fresh live run, choose a genuine uninvestigated registered dossier and click explicitly; do not promise a particular outcome or number of checks. A technically failed HITL attempt is shown as incomplete, not as the completed human-review story. Missing Validator output is never success. Audit PASS means recorded audit-chain integrity, not correctness of every interpretation. Preserve the historical POTENTIAL_CONFLICT assessment without claiming it conclusively establishes a document conflict.

No payment, authorization or financial-decision buttons are present. No real monetary transfers, production insurer integration, autonomous Valuation Agent or Jev implementation is claimed. Local SQLite audit is not externally immutable. The dataset is synthetic and there is no measured customer adoption or real-world efficiency benchmark.
