# Hosted verification: synthetic invoice sample

Run on **2026-10-25** against **app.futuresmart.ai** (ECS `fs-agent-knowledge:230`). The live sample collection is
`87396d15-d4b7-43b7-83ab-2ac0297711c3` ("Sample: supplier invoices", owner
`document-intelligence-samples@futuresmart.ai`). It was provisioned by `scripts/provision_sample.py` with
`field_template: "invoice"`. All 18 documents reached `completed`, and none failed. The chat model was the
collection default (`openai` / `gpt-5.4-mini`, charts on).

Each candidate question in `questions.json` was asked once with `stream: false`. The answers as returned are in
`hosted_run/answers.json`. The check is mechanical: invoice numbers, dates and amounts are compared against the values
`build.py` computes.

| Id | Question | Expected | Answer | Verdict |
|---|---|---|---|---|
| q01 | Which invoices are billed in euros, and what is each one's total? | 6 invoices (BCH ×3, TDS ×3) with totals | the same 6, every total to the cent | **verified** |
| q02 | Which invoices include cloud infrastructure charges? | BCH-2026-0418/0519/0620, QSL-89590 | the same 4 on `:230`; **3 of 4 on `:234`** (misses QSL-89590's cloud backup) | **dropped** |
| q03 | Which invoices are due on or after 1 October 2026? | LRA-1189, TDS-2026-077, HPC/26-27/061, QSL-89590 | the same 4, with correct dates and totals | **verified** |
| q04 | Which invoices were due on receipt? | CO-55120, CO-55388 | the same 2 | **verified** |
| q05 | What is the total billed by each US-dollar vendor? Show it as a chart. | LRA 35,106.00 · QSL 27,950.00 · CO 7,225.70 | totals right, but the chart is ASCII text with no chart object | **not used** |
| q06 | What did Lumen Ridge Analytics bill for, invoice by invoice? | 3 invoices with their line items | every line and total right | **verified** |

## Re-check on `fs-agent-knowledge:234`

Rev 234 changed how chat answers structured questions. After it deployed, every suggested question was asked again. q01, q03, q04 and q06 were still exact. q02 now listed 3 of the 4 invoices, so it was removed. `sample.json` carries q01, q03, q04 and q06.

## q05 on `fs-agent-knowledge:238` (chart fix)

Two runs: a real chart object (Bar Chart) with Lumen Ridge Analytics LLC 35,106.00, Quillmark Software Licensing 27,950.00 and Copperleaf Office Supply Co. 7,225.70, exactly the computed totals. **Verified**; `sample.json` now carries q01, q03, q04, q05 and q06. The answer text also draws an ASCII bar block beside the chart (an answer-prompt issue reported to the chart session).
