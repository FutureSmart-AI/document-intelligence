# Document Intelligence: engineering history

Documented 2026-10-21 (IST); earlier dates are dates supported by the saved work records.
Benchmark names which AI Demos benchmark an entry came from: SDE is Structured
Document Extraction. The two benchmarks keep separate histories and verdicts.

| Entry | Benchmark | Recorded date (IST) | State |
| --- | --- | --- | --- |
| [DI-001: Keep explicit document scope hard across retrieval branches](#di-001) | Managed RAG | 2026-10-20 | released |
| [DI-002: Expose recorded extraction context without inventing history](#di-002) | None | 2026-10-20 | released |
| [DI-003: Record a nonreproducing nested-field issue instead of claiming a fix](#di-003) | SDE | 2026-10-20 | nonreproducing_current |
| [DI-004: Exhaustive collection answers need complete record accounting](#di-004) | SDE | 2026-10-20 | candidate_unaccepted |
| [DI-005: Keep the usable UI and frontend-independent service together](#di-005) | None | 2026-10-20 | source_accepted |
| [DI-DEC-001: Do not add learning and correction workflows just to fill capability rows](#di-dec-001) | SDE | 2026-10-21 | deferred |
| [DI-DEC-002: Keep source provenance and search confidence as separate open problems](#di-dec-002) | SDE; Managed RAG | 2026-10-21 | deferred |

<a id="di-001"></a>
## DI-001: Keep explicit document scope hard across retrieval branches

Recorded date: **2026-10-20 IST**. State: **released**.

**Benchmark.** Managed RAG.

**Research source.** Unpublished retrieval research retained privately. https://aidemos.com/tools/futuresmart-agent is older platform context, not the same execution.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** Explicit scope could broaden after empty retrieval and return an excluded answer-bearing document.

**Root cause and uncertainty.** Scope was being treated as a relevance hint along fallback paths; unimplemented filter keys could also be silently ignored.

**Engineering decision.** Treat explicit object IDs as a hard boundary, including empty/unknown/deleted/foreign IDs. Reject unsupported filters instead of pretending to honor them.

**Implementation.** Kept scope in dense/hybrid/queryless paths; added request validation. Internal relevance expansion remains an explicit separate opt-in.

**Validation evidence.** Real-Qdrant focused scope contracts, independent staged/direct API controls, a fresh cited browser answer and changed-production controls passed.

**Limits and what remains unproved.** The historical arbitrary metadata-filter interface is not now implemented. Rejecting unsupported filters is safer but does not turn that historical test into a benchmark Pass. Not a full RAG rerun.

Recorded source identifiers: `7c14af4`, `de3c85a`.

- [tests/unit/test_search_scope.py](../../tests/unit/test_search_scope.py)
- [docs/api/rag-service.md](../../docs/api/rag-service.md)

<a id="di-002"></a>
## DI-002: Expose recorded extraction context without inventing history

Recorded date: **2026-10-20 IST**. State: **released**.

**Benchmark.** None (researcher report and maintainer reproduction, not a benchmark scenario).

**Research source.** Unpublished researcher report and a maintainer schema-evolution reproduction; original report details withheld.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** A blank old field did not explain whether extraction had processed that field. Current field definitions could be confused with the historical extraction contract.

**Root cause and uncertainty.** Existing successful enrichment events recorded some processed field names, but table/CSV consumers lacked that context; complete historical field definitions were not stored.

**Engineering decision.** Add bounded, separate extraction context. Keep missing history/definition history unknown; do not backfill invented schemas or rewrite existing values.

**Implementation.** Added context to tabular APIs and CSV from existing successful events, with timeout/failure fallback to unknown.

**Validation evidence.** Focused context/paging/failure tests and independent staged/production API/browser/CSV checks passed. Existing exported columns and cell strings were preserved; context was additive.

**Limits and what remains unproved.** This is observability and schema-history honesty, not improved extraction accuracy, exact provenance, reprocessing, or full historical schema versioning.

Recorded source identifiers: `3fffb69`, `32133fe`, `a000802`.

- [backend/utils/knowledge_health/extraction_context.py](../../backend/utils/knowledge_health/extraction_context.py)
- [tests/unit/test_extraction_context.py](../../tests/unit/test_extraction_context.py)
- [docs/api/extraction-context.md](../../docs/api/extraction-context.md)

<a id="di-003"></a>
## DI-003: Record a nonreproducing nested-field issue instead of claiming a fix

Recorded date: **2026-10-20 IST**. State: **nonreproducing_current**.

**Benchmark.** Structured Document Extraction.

**Research source.** Unpublished extraction research; historical schema details unavailable. Raw document/case data remain private.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** Historical evidence had omitted source-present fields, but did not preserve the exact saved schema needed to attribute the loss.

**Root cause and uncertainty.** Unknown historically: configuration omission, schema construction, model output and serialization were not distinguished by the retained record.

**Engineering decision.** Replay the original document with an explicitly NEW recorded schema. If that works, preserve the historical uncertainty and do not patch a presumed OCR/extraction bug.

**Implementation.** No extraction/model code change. Added a private reproduction record and retained the original failure.

**Validation evidence.** A current browser/API/source/export replay captured all required nested fields under the new schema.

**Limits and what remains unproved.** Not an exact historical replay, broad extraction score, or proof that all nested documents work. Historical baseline verdict stays unchanged.

Recorded source identifiers: `36a0735`, `73629ca`.


<a id="di-004"></a>
## DI-004: Exhaustive collection answers need complete record accounting

Recorded date: **2026-10-20 IST**. State: **candidate_unaccepted**.

**Benchmark.** Structured Document Extraction.

**Research source.** Unpublished collection-query feedback plus a later maintainer reproduction on a separately frozen population.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** An exhaustive question omitted registered objects; answer and chart did not account for the same complete object set.

**Root cause and uncertainty.** Bounded retrieval and answer construction do not establish exhaustive coverage. The exact historical runtime/configuration is unknown; current first loss was observed in answer output, not assumed to be parsing.

**Engineering decision.** Account for registered object IDs, qualify partial results, and keep numeric/currency aggregation honest. Preserve duplicates as records unless explicitly asked to deduplicate.

**Implementation.** A separate candidate explores authoritative typed-record handling and explicit coverage.

**Validation evidence.** Current failure evidence and focused candidate work were retained. Independent candidate browser acceptance and production release were not completed before work paused.

**Limits and what remains unproved.** No completeness/currency fix accepted and no benchmark result changed. A correct threshold query does not establish correctness for an exhaustive follow-up.

Recorded source identifiers: `b6315e7`.


<a id="di-005"></a>
## DI-005: Keep the usable UI and frontend-independent service together

Recorded date: **2026-10-20 IST**. State: **source_accepted**.

**Benchmark.** None (maintainer installation review).

**Research source.** Maintainer installation and service-boundary review, not an extraction benchmark.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** A service guide and sibling-worker dependencies did not provide a coherent one-repository application.

**Root cause and uncertainty.** Deployment and source ownership did not match the complete product users needed to install.

**Engineering decision.** Keep UI, API, required worker, opinionated datastores and deployment guidance together. API users can stop the frontend and use the same ingestion/retrieval/chat engine.

**Implementation.** Consolidated source and supported installation, service example and deployment documentation.

**Validation evidence.** Fresh-clone UI/API and frontend-stopped ingestion/search/chat checks, source retrieval, original-byte download and restart/persistence were independently checked.

**Limits and what remains unproved.** No general OCR/answer-accuracy or load-capacity claim; no separate duplicate library engine introduced.

Recorded source identifiers: `2c309a0`.

- [README.md](../../README.md)
- [docs/api/rag-service.md](../../docs/api/rag-service.md)
- [docs/installation.md](../../docs/installation.md)

<a id="di-dec-001"></a>
## DI-DEC-001: Do not add learning and correction workflows just to fill capability rows

Recorded date: **2026-10-21 IST**. State: **deferred**.

**Benchmark.** Structured Document Extraction.

**Research source.** Project-purpose decision; private capability findings remain preserved separately.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** A correct zero-shot classification or a working ordinary CSV export does not demonstrate example-based learning or corrected-value delivery.

**Root cause and uncertainty.** Those are different product capabilities with different validation requirements.

**Engineering decision.** Defer labeled-output learning, scalar/set correction and corrected-delivery workflows while prioritizing core extraction/retrieval reliability and reproducibility. Do not claim they are implemented.

**Implementation.** Documentation decision only, with no runtime/model change.

**Validation evidence.** Private research records remain unchanged; public history claims no benchmark upgrade.

**Limits and what remains unproved.** Deferred is a scope decision, not a correctness Pass. Revisit only with a concrete core-product need and held-out validation.


<a id="di-dec-002"></a>
## DI-DEC-002: Keep source provenance and search confidence as separate open problems

Recorded date: **2026-10-21 IST**. State: **deferred**.

**Benchmark.** Structured Document Extraction and Managed RAG, recorded separately.

**Research source.** Published platform review is general context; detailed research provenance is unpublished.

**Original benchmark result.** Not publicly verifiable: case-level source is unpublished; no new benchmark verdict

**Failure or limitation.** A first matching text location is not a guaranteed field-level source span. A high retrieval rank is not proof that an answer exists.

**Root cause and uncertainty.** Ranking, source matching, extraction context and claim support serve different contracts.

**Engineering decision.** Do not relabel the extraction-context release as precise field provenance, a confidence-calibration fix or support for all document structures.

**Implementation.** No new capability implemented; preserve the gaps and validation prerequisites.

**Validation evidence.** Current source/report review only; no new product requests or benchmark reruns.

**Limits and what remains unproved.** Precise claim/field evidence, confidence calibration and untested complex documents remain unverified.
