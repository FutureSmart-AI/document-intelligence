# Original baseline and published sources

Source check:21 October 2026 (IST). The original extraction and retrieval case
baselines are preserved privately. This document publishes only sources already
released and authorized summaries of our own engineering observations.

## Published context, not the later standalone benchmark

The [AI Demos FutureSmart Agent Platform review](https://aidemos.com/tools/futuresmart-agent) labels its update
**April 2026**. It records **Passed** for Agentic RAG Creation and for Resume
Parser and Candidate Search. These are broad earlier-platform feature results,
with published setup/output artifacts. They do not establish per-field extraction
accuracy or the later standalone Document Intelligence SDE/RAG case verdicts.

| Public source | Literal original result | Valid inference |
| --- | --- | --- |
| [Agentic RAG Creation, on the published review](https://aidemos.com/tools/futuresmart-agent) | Passed | Earlier platform workflow observed; current standalone exact-case replay not established. |
| [Resume Parser and Candidate Search, on the published review](https://aidemos.com/tools/futuresmart-agent) | Passed | Earlier schema/extraction workflow observed; not a current field-completeness score. |
| Later standalone SDE/RAG scenario/result pages | Not publicly verifiable on 21 October 2026 | See the published standalone results below. |

The source page is linked as published context, not as proof that a particular
later failure or fix passed.

## Published standalone benchmark results

Source check: 23 October 2026 (IST), live AI Demos benchmark pages.

| Benchmark | Published Document Intelligence results | Literal published result |
| --- | --- | --- |
| [Structured Document Extraction](https://aidemos.com/benchmarks/structured-document-extraction/tools/futuresmart-document-intelligence) | 1 of 19 scenarios | [A cleanly scanned document](https://aidemos.com/benchmarks/structured-document-extraction/results/futuresmart-document-intelligence/a-cleanly-scanned-document): **Pass** (evidence 18 September, graded 23 September 2026). An image-only invoice with no text layer returned the same invoice number, site, line items, subtotal, VAT and total due as its digital twin. |
| [Managed RAG](https://aidemos.com/benchmarks/managed-rag/tools/futuresmart-document-intelligence) | 0 of 15 scenarios | No published result yet. |

AI Demos does not combine these results into an overall score. Other scenarios
for this tool were tested but not yet published; they are not summarized here.
When a result is published, link it in the relevant history entry and keep its
literal verdict. Exact historical deployed configuration, schema and
corpus identity must not be reconstructed from screenshots or broad prose.

## Original research retained privately

The later original SDE/RAG reports, accepted task verdicts, researcher corrections
and original artifacts were reviewed. Their full case-by-case Pass/Fail outcomes
and evidence limitations are preserved in private records, with an explicit map
to each relevant implementation or decision. Unreleased research, discussions,
fixture contents, competitor material and private task URLs are not copied here.

Not verifiable is an evidence/publication state, not a rewritten original
verdict. Examples of distinctions we preserve:

- A recorded Pass can coexist with missing fields or a narrower successful
  question. A threshold query does not prove exhaustive collection coverage.
- A current document replay using an explicitly new schema is not an exact
  historical rerun; success does not establish an extraction-engine fix.
- Hard object-ID scope and rejection of unsupported filters do not implement an
  older arbitrary metadata query language or automatically pass that old test.
- Correct answer abstention and misleading search rank can coexist. Extraction
  context does not establish precise field provenance or definition history.
- Ordinary export success does not establish corrected-value delivery or
  learning from labelled examples. Missing capabilities remain separate gaps.

No current full-suite accuracy percentage or benchmark-score improvement is
claimed. [Engineering history](history.md) records actual scoped changes and
unaccepted work. [Maintenance](MAINTAINING.md) defines future comparable evidence.
