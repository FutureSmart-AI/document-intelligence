"""
Consolidated RAG tools (v2).

retrieve_documents   — replaces list-documents, get-document-summaries-filtered,
                       get-document-details, get-document-metadata
retrieve_metadata_catalog — replaces get-metadata-fields, get-metadata-values,
                            get-metadata-values-batch
retrieve_chunks      — renamed from search-chunks (same backend)
"""
import asyncio
from typing import Dict, List, Optional, Any

from langchain_core.tools import tool
from utils.logger import logger

from chat.knowledge_adapter import (
    get_metadata_fields as knowledge_get_metadata_fields,
    get_metadata_values as knowledge_get_metadata_values,
    get_document_details as knowledge_get_document_details,
    list_documents_with_metadata as knowledge_list_documents_with_metadata,
    search_chunks as knowledge_search_chunks,
    log_duplicate_object_ids,
)

from chat.rag_tools.schemas import (
    RetrieveDocumentsInput,
    RetrieveMetadataCatalogInput,
    RetrieveChunksInput,
)
from chat.rag_tools.helpers import (
    normalize_metadata_filter,
)
from chat.rag_tools.renderers import (
    render_chunks,
    render_documents,
    render_metadata_catalog,
)


# ---------------------------------------------------------------------------
# retrieve_documents
# ---------------------------------------------------------------------------

# Hard cap on how many docs we will return to the agent in a single call,
# regardless of what `limit` the LLM passes. Prevents runaway fetches
# (e.g. limit=1_000_000) and keeps prompt-token usage bounded.
MAX_DOCUMENTS_RETURNED = 500

# Question shapes where the answer correctness depends on having the COMPLETE
# matching population — predicates must be applied exhaustively, selections
# must consider every candidate, aggregates must count everything. For these
# shapes, the executor automatically paginates past the agent's `limit` so the
# LLM never sees a truncated population.
EXHAUSTIVE_SHAPES = frozenset({"predicate", "selection", "aggregate"})

# Shape-adaptive default ``limit`` for ``retrieve_documents``. Used when the
# agent does NOT explicitly pass ``limit`` (Pydantic ``limit`` is Optional and
# defaults to None — sentinel for "use shape default"). EXHAUSTIVE_SHAPES are
# omitted on purpose: they auto-paginate to ``MAX_DOCUMENTS_RETURNED``.
#
# Rationale: previously every shape defaulted to limit=20, which over-fetched
# for narrow shapes (fact_lookup, deep_read) and under-fetched for broad ones
# (multi_doc_synthesis). The values below are calibrated to typical evidence
# needs per shape — keep them in sync with the rag_agent base prompt's
# procedural descriptions.
SHAPE_DEFAULT_LIMITS: Dict[str, int] = {
    "fact_lookup": 5,
    "deep_read": 3,
    "compare": 10,
    "lookup": 10,
    "multi_doc_synthesis": 10,
    "metadata_lookup": 5,
}
DEFAULT_LIMIT_FALLBACK = 10  # used when shape is unknown


# Score-threshold cutoff for semantic retrieval. Set via env var
# ``RAG_DOCUMENT_SCORE_THRESHOLD`` (default 0.0 = off). When > 0, docs whose
# similarity score from the backend falls below the threshold are dropped
# before they reach the agent / answer node. Backends that don't return
# scores are unaffected (the field name lookup just yields None).
import os as _os
try:
    _DOCUMENT_SCORE_THRESHOLD = float(
        _os.environ.get("RAG_DOCUMENT_SCORE_THRESHOLD", "0.0")
    )
except ValueError:
    _DOCUMENT_SCORE_THRESHOLD = 0.0

# Field names the backend MIGHT use for similarity score. We check in this
# order; first hit wins. KB-agnostic — no specific backend assumed.
_SCORE_FIELDS = ("score", "relevance_score", "similarity", "rerank_score")


def _extract_score(doc: Dict[str, Any]) -> Optional[float]:
    """Return the first numeric score field found on a document dict, or None."""
    if not isinstance(doc, dict):
        return None
    for field in _SCORE_FIELDS:
        value = doc.get(field)
        if isinstance(value, (int, float)):
            return float(value)
    # Some backends nest scores inside metadata
    metadata = doc.get("metadata")
    if isinstance(metadata, dict):
        for field in _SCORE_FIELDS:
            value = metadata.get(field)
            if isinstance(value, (int, float)):
                return float(value)
    return None


SEMANTIC_QUERY_MODE = "hybrid"


def _dedupe_object_ids(docs: List[Dict[str, Any]]) -> List[str]:
    """First-seen-order list of object_ids from a backend doc list."""
    seen = set()
    ordered: List[str] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        oid = doc.get("object_id")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        ordered.append(str(oid))
    return ordered


def _detect_metadata_lookup_over_retrieval(
    question_shape: str,
    metadata_filter: Optional[Dict[str, Any]],
    object_ids: Optional[List[str]],
    limit: int,
) -> Optional[str]:
    """Flag the metadata_lookup + wide-$in over-retrieval anti-pattern.

    For category-enumeration questions ("what services do we have"), the
    correct procedure is `resolve_filter_value` → STOP — its return IS the
    complete authoritative list. A common failure is then calling
    retrieve_documents with the full value list in a $in filter to "enrich",
    which truncates at `limit` and silently drops values whose docs fall
    beyond it. We detect it by shape + a single field with a ≥5-value $in
    (conservative: small $in lists may be legitimate scoping). Returns the
    warning string, or None if the pattern doesn't apply.
    """
    if question_shape != "metadata_lookup" or not isinstance(metadata_filter, dict) or object_ids:
        return None

    wide_in_field: Optional[str] = None
    wide_in_count = 0
    for f_name, f_value in metadata_filter.items():
        in_values: List[Any] = []
        if isinstance(f_value, dict) and isinstance(f_value.get("$in"), list):
            in_values = f_value["$in"]
        elif isinstance(f_value, list):
            in_values = f_value
        if len(in_values) >= 5 and len(in_values) > wide_in_count:
            wide_in_count = len(in_values)
            wide_in_field = f_name

    if not wide_in_field:
        return None

    logger.warning(
        f"retrieve_documents: metadata_lookup over-retrieval on field='{wide_in_field}' "
        f"with {wide_in_count} $in values — call proceeded but flagged in artifact"
    )
    return (
        f"METADATA-LOOKUP OVER-RETRIEVAL WARNING: question_shape='metadata_lookup' "
        f"was called with metadata_filter={{`{wide_in_field}`: $in [{wide_in_count} values]}}. "
        f"For category-enumeration questions, `resolve_filter_value` already "
        f"returned the COMPLETE authoritative list for that field — that list IS "
        f"the answer. Re-fetching documents with the full $in is unnecessary "
        f"and risks silent data loss: this call will truncate at `limit` "
        f"({limit}), and any value whose document falls beyond the limit will be "
        f"DROPPED from the enumeration. Stop here and answer from the "
        f"`resolve_filter_value` output already in your conversation. Only "
        f"call `retrieve_documents` for descriptions per value when the user "
        f"explicitly asked for them — and even then, prefer the bare value list "
        f"over a partial enriched list."
    )


async def _validate_metadata_filter(
    knowledge_id: str, metadata_filter: Dict[str, Any]
) -> tuple[Optional[Dict[str, Any]], Optional[set]]:
    """Normalize a filter and drop keys not in the KB schema.

    Returns (validated_filter, valid_field_names). ``valid_field_names`` is
    None when the schema couldn't be fetched (validation skipped). The
    validated filter is empty when every key was invalid — the caller should
    return early in that case.
    """
    field_types: Dict[str, str] = {}
    try:
        fields_result = await knowledge_get_metadata_fields(
            knowledge_id=knowledge_id, include_values=False
        )
        field_types = {
            f.get("field_name"): f.get("field_type")
            for f in fields_result.get("fields", [])
            if isinstance(f, dict) and f.get("field_name")
        }
        logger.info(f"retrieve_documents: fetched {len(field_types)} field type definitions for filter validation")
    except Exception as e:
        logger.warning(f"Could not fetch field types for filter validation: {e}")

    normalized = normalize_metadata_filter(metadata_filter, field_types=field_types)
    logger.info(f"retrieve_documents: normalized filter={normalized}")

    if not normalized or not field_types:
        return normalized, (set(field_types) if field_types else None)

    valid_names = set(field_types.keys())
    validated = {}
    for key in normalized:
        base = key.split(".")[0].replace("[]", "") if ("." in key or "[]" in key) else key
        if base in valid_names:
            validated[key] = normalized[key]
        else:
            logger.warning(f"retrieve_documents: dropping invalid filter key '{key}' (not in schema)")
    return validated, valid_names


def _is_stub_detail(detail: Dict[str, Any], oid: str) -> bool:
    """True when a /summaries record carries no usable content.

    The backend sometimes returns a placeholder: metadata/summary/title all
    null and file_name set to the object_id. Such records have nothing for
    the answer node. Causes are backend-side (incomplete indexing, no
    filename at upload, failed summarization).
    """
    return (
        detail.get("metadata") in (None, {})
        and detail.get("summary") in (None, "")
        and detail.get("title") in (None, "")
        and (detail.get("file_name") or "") in ("", oid)
    )


# Per-field rendered-value cap for metadata projected into the agent's context.
# A field whose value renders longer than this is dropped from the in-context
# doc entry — these are bulky enrichment artifacts (URL lists, SEO keyword
# strings, long outlines, structured blobs) the agent never reasons over but
# that balloon ReAct input tokens (the whole thread is re-sent on every step).
# The full record stays in the backend and is re-fetchable by object_id; body
# facts are reached via retrieve_chunks, not metadata. Decided purely on VALUE
# SIZE, never on field name, so it stays KB-agnostic.
_METADATA_VALUE_CHAR_CAP = 250


def _value_is_url_shaped(value: Any) -> bool:
    """True if the value is a URL or a list of URLs.

    Value-shape detection (``http(s)://`` prefix), never field-name based — so
    it stays KB-agnostic. URL fields are always kept by the projection
    regardless of size: the answer node needs them for citations / links, and
    dropping them is what caused earlier 'inconsistent URL' answers.
    """
    if isinstance(value, str):
        return value.strip().lower().startswith(("http://", "https://"))
    if isinstance(value, list) and value:
        url_items = [
            v for v in value
            if isinstance(v, str) and v.strip().lower().startswith(("http://", "https://"))
        ]
        # Mostly-URL list (tolerate a stray non-URL entry).
        return len(url_items) >= max(1, len(value) // 2)
    return False


def _project_metadata(meta_blob: Any) -> Any:
    """Drop bulky metadata fields by value size; keep short fields AND all URLs.

    Generic across KBs — the keep/drop decision is the rendered length of each
    field's value (never the field name), with one value-shape exception: URL
    and URL-list fields are ALWAYS kept regardless of size, because the answer
    node needs them for citations/links. Short reasoning-useful fields (author,
    status, dates, page_type, topics, …) survive on size; large non-URL fields
    (seo_keywords, long outlines, big JSON blobs) are dropped. Anything dropped
    is re-fetchable by object_id.
    """
    if not isinstance(meta_blob, dict):
        return meta_blob
    projected: Dict[str, Any] = {}
    dropped: List[str] = []
    for key, value in meta_blob.items():
        try:
            rendered_len = len(str(value))
        except Exception:
            rendered_len = _METADATA_VALUE_CHAR_CAP + 1
        if rendered_len <= _METADATA_VALUE_CHAR_CAP or _value_is_url_shaped(value):
            projected[key] = value
        else:
            dropped.append(key)
    if dropped:
        logger.info(
            f"retrieve_documents: projected metadata — dropped {len(dropped)} "
            f"oversized non-URL field(s) (>{_METADATA_VALUE_CHAR_CAP} ch): {dropped}"
        )
    return projected


def _build_document_entry(
    oid: str,
    detail: Dict[str, Any],
    *,
    include_summary: bool,
    include_metadata: bool,
    include_urls: bool,
    score: Optional[float],
) -> Dict[str, Any]:
    """Project a backend detail record into the agent-facing doc entry."""
    entry: Dict[str, Any] = {"object_id": oid}

    if include_summary:
        entry["summary"] = detail.get("summary")

    if include_metadata:
        meta_blob = detail.get("metadata")
        # The backend stores summary as a metadata field, so a naive
        # passthrough makes include_summary=False a lie — strip it.
        if not include_summary and isinstance(meta_blob, dict) and "summary" in meta_blob:
            meta_blob = {k: v for k, v in meta_blob.items() if k != "summary"}
        # Drop bulky enrichment fields by value size (KB-agnostic) so the
        # in-context doc entry stays small and doesn't compound across ReAct
        # steps. Full record remains re-fetchable by object_id.
        meta_blob = _project_metadata(meta_blob)
        entry["metadata"] = meta_blob
        logger.info(f"retrieve_documents: doc[{oid}] metadata keys={list((meta_blob or {}).keys())}")

    entry["title"] = detail.get("title")
    entry["file_name"] = detail.get("file_name") or detail.get("filename")

    if include_urls:
        entry["url"] = detail.get("url")

    if score is not None:
        entry["score"] = round(score, 4)

    return entry


async def _enrich_documents(
    knowledge_id: str,
    resolved_ids: List[str],
    scores_by_id: Dict[str, float],
    *,
    include_summary: bool,
    include_metadata: bool,
    include_urls: bool,
) -> List[Dict[str, Any]]:
    """Fetch document details and project them into agent-facing entries.

    When no enrichment is requested, returns bare {object_id, score?} stubs.
    Otherwise fetches /summaries details, builds entries, and runs a single
    targeted retry for any stub records the backend returns (a known
    bulk-batch consistency gap that a per-ID re-fetch usually resolves).
    """
    if not (include_summary or include_metadata or include_urls):
        logger.info(
            f"retrieve_documents: skipping enrichment "
            f"(include_summary={include_summary}, include_metadata={include_metadata})"
        )
        documents = []
        for oid in resolved_ids:
            stub: Dict[str, Any] = {"object_id": oid}
            score = scores_by_id.get(oid)
            if score is not None:
                stub["score"] = round(score, 4)
            documents.append(stub)
        return documents

    raw_details = await knowledge_get_document_details(
        knowledge_id=knowledge_id, object_ids=resolved_ids,
    )
    logger.info(
        f"retrieve_documents: fetched details for {len(raw_details)} docs "
        f"(requested {len(resolved_ids)})"
    )
    details_map: Dict[str, Dict[str, Any]] = {
        str(doc["object_id"]): doc
        for doc in raw_details
        if isinstance(doc, dict) and "object_id" in doc
    }

    documents: List[Dict[str, Any]] = []
    stub_doc_ids: List[str] = []
    for oid in resolved_ids:
        detail = details_map.get(oid, {})
        if not detail:
            logger.warning(f"retrieve_documents: no detail returned for doc_id={oid}, fields will be empty")
        elif _is_stub_detail(detail, oid):
            stub_doc_ids.append(oid)
        documents.append(_build_document_entry(
            oid, detail,
            include_summary=include_summary,
            include_metadata=include_metadata,
            include_urls=include_urls,
            score=scores_by_id.get(oid),
        ))

    if stub_doc_ids:
        await _retry_stub_documents(
            knowledge_id, stub_doc_ids, documents,
            include_summary=include_summary,
            include_metadata=include_metadata,
            include_urls=include_urls,
            scores_by_id=scores_by_id,
        )

    return documents


async def _retry_stub_documents(
    knowledge_id: str,
    stub_doc_ids: List[str],
    documents: List[Dict[str, Any]],
    *,
    include_summary: bool,
    include_metadata: bool,
    include_urls: bool,
    scores_by_id: Dict[str, float],
) -> None:
    """Re-fetch stub docs by ID and patch recovered entries in place.

    The bulk /summaries endpoint sometimes returns empty records for a subset
    of IDs while returning full data for the rest. A targeted retry with ONLY
    the empty IDs usually resolves them (a backend bulk-batch consistency
    gap). Anything still empty after the retry is a genuine indexing/
    summarization gap and is logged loudly.
    """
    logger.info(
        f"retrieve_documents: detected {len(stub_doc_ids)} stub doc(s) — "
        f"retrying in a follow-up fetch"
    )
    try:
        retry_details = await knowledge_get_document_details(
            knowledge_id=knowledge_id, object_ids=stub_doc_ids,
        )
    except Exception as e:
        logger.warning(f"retrieve_documents: stub retry failed: {e}")
        retry_details = []

    retry_map: Dict[str, Dict[str, Any]] = {
        str(doc["object_id"]): doc
        for doc in retry_details
        if isinstance(doc, dict) and "object_id" in doc
    }
    doc_index = {e["object_id"]: i for i, e in enumerate(documents)}
    recovered_ids: List[str] = []
    for oid in stub_doc_ids:
        retry_detail = retry_map.get(oid)
        idx = doc_index.get(oid)
        # Recovery succeeded when the retry record is no longer a stub.
        if not retry_detail or idx is None or _is_stub_detail(retry_detail, oid):
            continue
        documents[idx] = _build_document_entry(
            oid, retry_detail,
            include_summary=include_summary,
            include_metadata=include_metadata,
            include_urls=include_urls,
            score=scores_by_id.get(oid),
        )
        recovered_ids.append(oid)

    still_stub = [oid for oid in stub_doc_ids if oid not in recovered_ids]
    if recovered_ids:
        logger.info(
            f"retrieve_documents: recovered {len(recovered_ids)}/{len(stub_doc_ids)} stub doc(s) via retry"
        )
    if still_stub:
        logger.warning(
            f"retrieve_documents: backend returned {len(still_stub)} STUB doc(s) "
            f"with no metadata/summary/title (file_name == object_id placeholder) "
            f"even after a targeted retry. Likely a genuine backend indexing or "
            f"summarization gap for these IDs. object_ids={still_stub}"
        )


@tool("retrieve_documents", args_schema=RetrieveDocumentsInput, response_format="content_and_artifact")
async def retrieve_documents_tool(
    question_shape: str,
    reasoning: str,
    knowledge_id: str,
    metadata_filter: Optional[Dict[str, Any]] = None,
    query: Optional[str] = None,
    object_ids: Optional[List[str]] = None,
    limit: Optional[int] = None,
    include_summary: bool = True,
    include_metadata: bool = True,
    include_urls: bool = False,
):
    """Retrieve documents from the knowledge base via hybrid semantic + filter ranking.

    Single-mode tool: every call ranks by semantic relevance to `query`. When
    `metadata_filter` is also provided the filter scopes the population first
    and `query` ranks within that scope (hybrid). When no filter applies
    `query` alone drives retrieval. `object_ids` bypasses ranking entirely
    and fetches the named docs directly (still no `query` interaction).

    Behavioral rules:
    - Two mutually exclusive modes: pass `query` (semantic search) OR `object_ids`
      (direct fetch), never both. Enforced by RetrieveDocumentsInput.
    - `metadata_filter` is OPTIONAL — add it when a catalog field maps to the
      user's constraint; omit when no field applies.
    - `object_ids` present → fetch those documents directly (skip ranking).
    - For exhaustive shapes (predicate / selection / aggregate) the effective
      limit is auto-overridden to MAX_DOCUMENTS_RETURNED so the agent always
      sees the full ranked population.
    - `limit` is hard-capped at MAX_DOCUMENTS_RETURNED (500).
    """
    query = query.strip() if isinstance(query, str) else query

    # Retrieval needs at least one driver: `query` (semantic ranking),
    # `metadata_filter` (exact filtered population), or `object_ids` (direct
    # fetch). A call with none of them has nothing to retrieve. (An empty
    # `metadata_filter={}` is normalized to None later, so treat it as absent
    # here too.)
    has_filter = isinstance(metadata_filter, dict) and bool(metadata_filter)
    if not query and not object_ids and not has_filter:
        err = (
            "retrieve_documents needs a retrieval driver: `query` for semantic "
            "search (e.g. query='owner'), `metadata_filter` for an exact filtered "
            "set, or `object_ids` to fetch documents by ID. The call had none."
        )
        logger.warning(f"retrieve_documents: rejected call without query — {err}")
        artifact = {"documents": [], "count": 0, "error": err}
        return render_documents(documents=[], error=err), artifact

    # Resolve the effective limit:
    #   1. If the caller passed an explicit ``limit`` → honor it (clamped to the
    #      hard cap below).
    #   2. Otherwise → use the shape-adaptive default. Narrow shapes
    #      (fact_lookup / deep_read / compare) get small defaults; broad shapes
    #      (multi_doc_synthesis) get larger ones. Exhaustive shapes still get
    #      overridden later to fetch the complete population.
    requested_limit = limit  # what the caller passed (may be None)
    if limit is None:
        limit = SHAPE_DEFAULT_LIMITS.get(question_shape, DEFAULT_LIMIT_FALLBACK)
        logger.info(
            f"retrieve_documents: limit unspecified → using shape default "
            f"{limit} for shape='{question_shape}'"
        )
    if limit > MAX_DOCUMENTS_RETURNED:
        logger.warning(
            f"retrieve_documents: requested limit={limit} exceeds cap "
            f"{MAX_DOCUMENTS_RETURNED}; clamping. total_match_count will still "
            f"reflect the true backend count."
        )
        limit = MAX_DOCUMENTS_RETURNED

    logger.info(
        f"retrieve_documents: shape={question_shape} | {reasoning} | "
        f"knowledge_id={knowledge_id}, object_ids={bool(object_ids)}, "
        f"filter={bool(metadata_filter)}, query={bool(query)}, "
        f"limit={limit} (requested {requested_limit}), summary={include_summary}, "
        f"metadata={include_metadata}, urls={include_urls}"
    )

    try:
        if object_ids and query:
            artifact = {
                "documents": [],
                "count": 0,
                "error": "query cannot be combined with object_ids",
            }
            return render_documents(documents=[], error=artifact["error"]), artifact

        # Flag the metadata_lookup over-retrieval anti-pattern (see helper).
        metadata_lookup_over_retrieval_warning = _detect_metadata_lookup_over_retrieval(
            question_shape, metadata_filter, object_ids, limit
        )

        # ------------------------------------------------------------------
        # Step 1: Resolve the set of document IDs to operate on
        # ------------------------------------------------------------------
        # Normalize an empty dict to None up front so we never hand the
        # backend `metadata_filter={}` (some backends treat that as "match
        # nothing" instead of "no filter"). With None the tool falls through
        # to "no filter → fetch full population", which is what an empty
        # filter was clearly intended to mean.
        if isinstance(metadata_filter, dict) and not metadata_filter:
            logger.info("retrieve_documents: empty metadata_filter={} normalized to None")
            metadata_filter = None

        total_match_count: Optional[int] = None  # only set for exhaustive shapes
        truncated_unknown_total: bool = False  # True when we hit the fetch ceiling and exact total is unknown
        applied_filter = metadata_filter
        retrieval_mode = "object_ids" if object_ids else "hybrid_query"
        semantic_candidate_count: Optional[int] = None
        # Per-doc similarity scores keyed by object_id; only populated on the
        # semantic-query path. The by-ID branch skips scoring and leaves it empty.
        scores_by_id: Dict[str, float] = {}
        score_threshold_dropped: List[Dict[str, Any]] = []

        if object_ids:
            # Caller supplied explicit IDs — cap to limit and skip filter resolution.
            log_duplicate_object_ids(
                [str(x) for x in object_ids if x is not None],
                "retrieve_documents[caller-supplied object_ids]",
            )
            resolved_ids = object_ids[:limit]
            logger.info(f"retrieve_documents: using {len(resolved_ids)} caller-supplied IDs (requested {len(object_ids)}, limit={limit})")
        else:
            normalized_filter = metadata_filter
            if metadata_filter:
                normalized_filter, valid_names = await _validate_metadata_filter(
                    knowledge_id, metadata_filter
                )
                # Every key invalid (schema known, nothing survived) → early exit.
                if valid_names is not None and not normalized_filter:
                    error = f"No valid filter keys. Valid fields: {sorted(valid_names)}"
                    logger.warning(f"retrieve_documents: all filter keys invalid, returning early. {error}")
                    artifact = {
                        "documents": [], "count": 0, "applied_filter": metadata_filter,
                        "retrieval_mode": "hybrid_query", "query": query, "error": error,
                    }
                    return render_documents(
                        documents=[], applied_filter=metadata_filter, error=error,
                        query=query, retrieval_mode="hybrid_query",
                    ), artifact
                applied_filter = normalized_filter

            is_exhaustive = question_shape in EXHAUSTIVE_SHAPES

            # Exhaustive shapes (predicate / selection / aggregate) must return
            # the COMPLETE matching population — semantic ranking with `query`
            # puts the backend in pool mode which silently drops docs outside
            # the ranked window. Strip `query` so the call becomes a pure
            # filtered fetch, guaranteeing every matching doc is returned.
            if is_exhaustive and query:
                logger.info(
                    f"retrieve_documents: exhaustive shape '{question_shape}' — "
                    f"stripping query to ensure complete population is returned"
                )
                query = None

            # Single hybrid path: every call is semantic ranking with `query`,
            # optionally scoped by `metadata_filter`. Exhaustive shapes use the
            # full cap so the agent sees the complete ranked population; all
            # other shapes use the shape-adaptive pool.
            async def _resolve_query_candidates(
                fetch_limit: int,
            ) -> tuple[List[str], Dict[str, float]]:
                # `mode` only governs semantic ranking; send it only when a
                # `query` is present. A filter-only call is an exact filtered
                # fetch with no ranking, so passing a semantic mode would be
                # meaningless (and risks backend-specific surprises).
                docs = await knowledge_list_documents_with_metadata(
                    knowledge_id=knowledge_id,
                    query=query,
                    metadata_filter=normalized_filter or None,
                    mode=SEMANTIC_QUERY_MODE if query else None,
                    include_metadata=False,
                    limit=fetch_limit,
                )
                candidate_ids = _dedupe_object_ids(docs)
                log_duplicate_object_ids(
                    candidate_ids,
                    f"retrieve_documents[semantic_query limit={fetch_limit}]",
                )

                # Harvest scores from the backend response (when present).
                # Backends differ on the field name (score / relevance_score /
                # similarity) — ``_extract_score`` checks all known variants.
                scores: Dict[str, float] = {}
                for d in docs:
                    if not isinstance(d, dict):
                        continue
                    oid = d.get("object_id")
                    if not oid:
                        continue
                    score = _extract_score(d)
                    if score is not None:
                        scores[str(oid)] = score

                logger.info(
                    f"retrieve_documents: semantic query "
                    f"(filter={'yes' if normalized_filter else 'no'}) "
                    f"returned {len(candidate_ids)} docs, fetch_limit={fetch_limit}, "
                    f"scores_captured={len(scores)}"
                )
                return candidate_ids, scores

            # Fetch directly at the target size: `limit` for normal shapes,
            # the full cap for exhaustive shapes (which need the complete
            # matching population). The backend ranks and truncates, so we
            # ask for exactly what we intend to return.
            fetch_limit = MAX_DOCUMENTS_RETURNED if is_exhaustive else limit
            resolved_ids, scores_by_id = await _resolve_query_candidates(fetch_limit)
            semantic_candidate_count = len(resolved_ids)

            # Score-threshold cutoff (off by default). Drops candidates
            # whose similarity score falls below the configured threshold
            # BEFORE we truncate to `limit`. Skipped silently when the
            # backend didn't return scores (scores_by_id empty).
            if _DOCUMENT_SCORE_THRESHOLD > 0.0 and scores_by_id:
                kept: List[str] = []
                dropped_ids: List[str] = []
                for oid in resolved_ids:
                    score = scores_by_id.get(oid)
                    if score is None or score >= _DOCUMENT_SCORE_THRESHOLD:
                        kept.append(oid)
                    else:
                        dropped_ids.append(oid)
                        score_threshold_dropped.append(
                            {"object_id": oid, "score": score}
                        )
                if dropped_ids:
                    logger.info(
                        f"retrieve_documents: score threshold "
                        f"{_DOCUMENT_SCORE_THRESHOLD} dropped {len(dropped_ids)} "
                        f"low-scoring candidates"
                    )
                resolved_ids = kept

            # Exhaustive shapes see the full population; everything else
            # is truncated to the requested limit. Track the total so the
            # agent can surface "N matched, showing top K".
            if is_exhaustive:
                total_match_count = len(resolved_ids)
                # If we hit the cap, the true total may be higher.
                truncated_unknown_total = len(resolved_ids) >= MAX_DOCUMENTS_RETURNED
                logger.info(
                    f"retrieve_documents: exhaustive shape={question_shape} — "
                    f"returning all {total_match_count} ranked docs "
                    f"(truncated_unknown_total={truncated_unknown_total})"
                )
            else:
                resolved_ids = resolved_ids[:limit]
                logger.info(
                    f"retrieve_documents: semantic query returned {len(resolved_ids)} "
                    f"ranked docs for limit={limit}"
                )

        if not resolved_ids:
            logger.info(f"retrieve_documents: no document IDs resolved, returning empty result")
            artifact = {
                "documents": [],
                "count": 0,
                "applied_filter": applied_filter,
                "retrieval_mode": retrieval_mode,
                "question_shape": question_shape,
            }
            if query:
                artifact["query"] = query
            if semantic_candidate_count is not None:
                artifact["semantic_candidate_count"] = semantic_candidate_count
            return render_documents(
                documents=[],
                applied_filter=applied_filter,
                query=query,
                retrieval_mode=retrieval_mode,
            ), artifact

        # ------------------------------------------------------------------
        # Step 2: Enrich documents based on include_* flags (see helper)
        # ------------------------------------------------------------------
        documents = await _enrich_documents(
            knowledge_id,
            resolved_ids,
            scores_by_id,
            include_summary=include_summary,
            include_metadata=include_metadata,
            include_urls=include_urls,
        )

        artifact: Dict[str, Any] = {
            "documents": documents,
            "count": len(documents),
            "applied_filter": applied_filter,
            "retrieval_mode": retrieval_mode,
            "question_shape": question_shape,
        }
        if query:
            artifact["query"] = query
        if semantic_candidate_count is not None:
            artifact["semantic_candidate_count"] = semantic_candidate_count
        if total_match_count is not None:
            artifact["total_match_count"] = total_match_count
            artifact["truncated"] = (
                truncated_unknown_total or total_match_count > len(documents)
            )
            # When True, the exact total is unknown — at least
            # `total_match_count` docs match, possibly more.
            artifact["total_is_lower_bound"] = truncated_unknown_total
        if metadata_lookup_over_retrieval_warning:
            artifact["metadata_lookup_over_retrieval_warning"] = True
            artifact.setdefault("warning", metadata_lookup_over_retrieval_warning)

        # Score telemetry — surface min/max/avg + threshold-drop count when
        # we captured scores from the backend. Cheap to compute, useful for
        # the verifier and for debugging retrieval quality.
        score_summary: Optional[Dict[str, Any]] = None
        kept_scores = [d.get("score") for d in documents if isinstance(d.get("score"), (int, float))]
        if kept_scores:
            score_summary = {
                "min": round(min(kept_scores), 4),
                "max": round(max(kept_scores), 4),
                "avg": round(sum(kept_scores) / len(kept_scores), 4),
                "count_with_score": len(kept_scores),
            }
            if _DOCUMENT_SCORE_THRESHOLD > 0.0:
                score_summary["threshold"] = _DOCUMENT_SCORE_THRESHOLD
                score_summary["threshold_dropped"] = len(score_threshold_dropped)
            artifact["score_summary"] = score_summary

        logger.info(
            f"retrieve_documents: returning {len(documents)} documents "
            f"(mode={retrieval_mode}, total_match_count={total_match_count}, "
            f"truncated={artifact.get('truncated')}, total_is_lower_bound={truncated_unknown_total})"
        )
        return render_documents(
            documents=documents,
            applied_filter=applied_filter,
            total_match_count=total_match_count,
            limit=limit,
            is_count_only=(not include_summary and not include_metadata and not include_urls and limit <= 1),
            total_is_lower_bound=truncated_unknown_total,
            query=query,
            retrieval_mode=retrieval_mode,
            metadata_lookup_over_retrieval_warning=metadata_lookup_over_retrieval_warning,
            score_summary=score_summary,
        ), artifact

    except Exception as e:
        logger.error(f"Error in retrieve_documents_tool: {e}", exc_info=True)
        artifact = {"documents": [], "count": 0, "error": str(e)}
        return render_documents(documents=[], error=str(e)), artifact


# ---------------------------------------------------------------------------
# retrieve_metadata_catalog
# ---------------------------------------------------------------------------

@tool("retrieve_metadata_catalog", args_schema=RetrieveMetadataCatalogInput, response_format="content_and_artifact")
async def retrieve_metadata_catalog_tool(
    question_shape: str,
    reasoning: str,
    knowledge_id: str,
    field_names: Optional[List[str]] = None,
    include_values: bool = False,
    metadata_filter: Optional[Dict[str, Any]] = None,
    object_ids: Optional[List[str]] = None,
    limit: int = 100,
):
    """Explore metadata fields and their values in the knowledge base.

    This is the unified metadata exploration tool. It can:
    - List all metadata fields (include_values=False)
    - Retrieve unique values for specific or all fields (include_values=True)

    Behavioral rules:
    - include_values=False → return fields only (field name, type, cardinality hint).
    - field_names + include_values=True → return values only for those fields.
    - include_values=True without field_names → return values for all fields.
    - Value fetches are capped per field by `limit` (default: 100).
    """
    logger.info(
        f"retrieve_metadata_catalog: shape={question_shape} | {reasoning} | "
        f"knowledge_id={knowledge_id}, field_names={field_names}, include_values={include_values}"
    )

    try:
        # Always fetch field definitions first
        fields_result = await knowledge_get_metadata_fields(
            knowledge_id=knowledge_id, include_values=False
        )
        raw_fields = fields_result.get("fields", [])

        field_type_map: Dict[str, str] = {}
        all_field_names: List[str] = []
        all_fields_meta: List[Dict[str, Any]] = []

        for f in raw_fields:
            if not isinstance(f, dict) or not f.get("field_name"):
                continue
            fname = f["field_name"]
            ftype = f.get("field_type", "unknown")
            fdesc = f.get("description", "")
            field_type_map[fname] = ftype
            all_field_names.append(fname)

            cardinality = "unknown"
            lower_type = str(ftype).lower()
            if any(t in lower_type for t in ("string", "enum", "category", "boolean")):
                from chat.schema import _looks_high_cardinality
                cardinality = "high" if _looks_high_cardinality(fname) else "low"

            all_fields_meta.append({
                "field_name": fname,
                "field_type": ftype,
                "description": fdesc,
                "cardinality_hint": cardinality,
            })

        # When the caller requested specific field_names, restrict the fields list
        # to those — no need to re-emit the entire schema (the agent already has
        # it in the prefetched catalog).
        # If the caller requested a LEAF PATH (e.g. "experience[].role"), emit it
        # as its own row so the renderer can attach values to it — without this,
        # only the parent ("experience") appears and the values_by_field entry
        # keyed on the leaf path is dropped from the rendered output.
        if field_names:
            parents = {fn.split(".")[0].replace("[]", "") for fn in field_names}
            fields_output = [f for f in all_fields_meta if f["field_name"] in parents]
            for fn in field_names:
                if "[]." in fn or ("." in fn and not fn.startswith(".")):
                    parent_base = fn.split(".")[0].replace("[]", "")
                    parent_meta = next(
                        (f for f in all_fields_meta if f["field_name"] == parent_base), {}
                    )
                    leaf_key = fn.split(".", 1)[1] if "." in fn else ""
                    fields_output.append({
                        "field_name": fn,
                        "field_type": "str",
                        "description": (
                            f"Leaf path of `{parent_base}` (key `{leaf_key}`)"
                            if leaf_key else f"Leaf path of `{parent_base}`"
                        ),
                        "cardinality_hint": parent_meta.get("cardinality_hint", "unknown"),
                        "parent_field": parent_base,
                        "leaf_key": leaf_key,
                    })
        else:
            fields_output = all_fields_meta

        result: Dict[str, Any] = {"fields": fields_output, "values_by_field": {}}
        prefetched_truncation_note = fields_result.get("truncation_note")
        if prefetched_truncation_note:
            result["truncation_note"] = prefetched_truncation_note

        if not include_values:
            return render_metadata_catalog(
                fields=fields_output,
                values_by_field=None,
                truncation_note=prefetched_truncation_note,
            ), result

        # Determine which fields to fetch values for
        target_fields = field_names if field_names else all_field_names

        # Validate requested field names
        valid_names = set(all_field_names)
        if field_names:
            invalid = [fn for fn in field_names if fn.split(".")[0].replace("[]", "") not in valid_names]
            if invalid:
                logger.warning(f"Invalid field names requested: {invalid}")
            target_fields = [fn for fn in field_names if fn.split(".")[0].replace("[]", "") in valid_names]
            if not target_fields:
                result["error"] = (
                    f"None of the requested fields exist. "
                    f"Valid: {sorted(valid_names)}"
                )
                return render_metadata_catalog(
                    fields=fields_output,
                    truncation_note=prefetched_truncation_note,
                    error=result["error"],
                ), result

        normalized_filter = normalize_metadata_filter(metadata_filter)

        def _convert_field_name(original: str) -> str:
            """Convert dot-notation to array notation for List[dict] fields."""
            if "[]." in original or original.endswith("[]"):
                return original
            if "." not in original:
                return original
            base, nested = original.split(".", 1)
            ftype = field_type_map.get(base, "")
            if isinstance(ftype, str) and "list" in ftype.lower() and "dict" in ftype.lower():
                return f"{base}[].{nested}"
            return original

        async def _fetch_values(field_name: str) -> tuple:
            api_name = _convert_field_name(field_name)
            try:
                res = await knowledge_get_metadata_values(
                    knowledge_id=knowledge_id,
                    field_name=api_name,
                    object_ids=object_ids,
                    metadata_filter=normalized_filter,
                    limit=limit,
                )
                return field_name, {
                    "values": res.get("values", []),
                    "field_type": res.get("field_type"),
                }
            except Exception as e:
                logger.error(f"Error fetching values for '{field_name}': {e}")
                return field_name, {"values": [], "field_type": None, "error": str(e)}

        tasks = [_fetch_values(fn) for fn in target_fields]
        fetched = await asyncio.gather(*tasks)
        for fname, fdata in fetched:
            result["values_by_field"][fname] = fdata

        logger.info(
            f"retrieve_metadata_catalog: {len(fields_output)} fields, "
            f"{len(result['values_by_field'])} with values"
        )
        return render_metadata_catalog(
            fields=fields_output,
            values_by_field=result["values_by_field"],
            truncation_note=result.get("truncation_note"),
        ), result

    except Exception as e:
        logger.error(f"Error in retrieve_metadata_catalog_tool: {e}", exc_info=True)
        artifact = {"fields": [], "values_by_field": {}, "error": str(e)}
        return render_metadata_catalog(fields=[], error=str(e)), artifact


# ---------------------------------------------------------------------------
# retrieve_chunks  (renamed search-chunks)
# ---------------------------------------------------------------------------

# Hard ceiling on chunks returned by a single retrieve_chunks call, regardless
# of what `num_chunks` the agent passes (and regardless of how many chunks a doc
# actually has on the object_ids deep-read path, which previously fetched the
# WHOLE document — the dominant answer-node token cost in observed traces).
# Chunk text is the heaviest per-item payload and accumulates across ReAct steps
# AND flows into the answer node, so the count must be bounded. Tune via env.
import os as _os_chunks
try:
    _MAX_CHUNKS_PER_CALL = int(_os_chunks.environ.get("RAG_MAX_CHUNKS_PER_CALL", "4"))
except ValueError:
    _MAX_CHUNKS_PER_CALL = 4
if _MAX_CHUNKS_PER_CALL < 1:
    _MAX_CHUNKS_PER_CALL = 1

# Elevated ceiling for the two cases that legitimately need more chunks:
#   - deep_read shape — reading a document end-to-end.
#   - the global chunk pass (Rung 3: a `query` with NO `object_ids`) — searches
#     the whole KB and is recall-critical for answers buried in unsurfaced docs.
# Every other case (scoped fact_lookup / lookup chunk reads) uses the base cap.
try:
    _MAX_CHUNKS_DEEP = int(_os_chunks.environ.get("RAG_MAX_CHUNKS_DEEP", "10"))
except ValueError:
    _MAX_CHUNKS_DEEP = 10
if _MAX_CHUNKS_DEEP < _MAX_CHUNKS_PER_CALL:
    _MAX_CHUNKS_DEEP = _MAX_CHUNKS_PER_CALL


@tool("retrieve_chunks", args_schema=RetrieveChunksInput, response_format="content_and_artifact")
async def retrieve_chunks_tool(
    question_shape: str,
    reasoning: str,
    knowledge_id: str,
    query: Optional[str] = None,
    num_chunks: int = 3,
    object_ids: Optional[List[str]] = None,
    mode: str = "hybrid",
):
    """Perform a hybrid semantic search over document chunks.

    Two modes:
    - With `query` (and optionally `object_ids`): semantic / hybrid search.
    - With `object_ids` and no `query`: returns chunks from those documents in
      original order (no semantic ranking). Use this for deep_read tasks.

    Both modes are bounded by `num_chunks`, hard-capped at _MAX_CHUNKS_PER_CALL.

    Returns:
        Tuple of (compact Markdown content, full structured artifact dict).
    """
    if not query and not object_ids:
        err = (
            "retrieve_chunks requires either `query` (for semantic search) "
            "or `object_ids` (to fetch all chunks from named documents)."
        )
        logger.warning(f"retrieve_chunks: {err}")
        artifact = {"chunks": [], "count": 0, "error": err}
        return render_chunks(chunks=[], query=query or "", mode=mode, error=err), artifact

    # Pick the ceiling by case: deep_read (end-to-end doc reading) and the
    # global chunk pass (query with no object_ids = Rung 3, recall-critical) get
    # the elevated cap; every other scoped chunk read uses the base cap. The
    # agent's num_chunks is respected up to whichever ceiling applies — anything
    # above (or the old "fetch the whole doc" object_ids behavior) is bounded.
    is_global_scan = bool(query and not object_ids)
    chunk_cap = (
        _MAX_CHUNKS_DEEP
        if (question_shape == "deep_read" or is_global_scan)
        else _MAX_CHUNKS_PER_CALL
    )
    effective_num_chunks = max(1, min(num_chunks, chunk_cap))

    logger.info(
        f"retrieve_chunks: shape={question_shape} | {reasoning} | "
        f"query={'<none>' if not query else repr(query)}, "
        f"num_chunks={num_chunks}->{effective_num_chunks} (cap {chunk_cap}), "
        f"mode={mode}, object_ids={'present' if object_ids else 'absent'}"
    )

    try:
        if not query and object_ids:
            # Fetch chunks from the named docs in document order — no semantic
            # ranking. Useful for deep_read tasks. The backend returns the whole
            # document, so we slice to effective_num_chunks PER DOC to keep the
            # payload bounded (previously this path returned every chunk and was
            # the main answer-node token blow-up).
            from chat.knowledge_adapter import get_document_chunks_paginated
            chunks: List[Dict[str, Any]] = []
            for oid in object_ids:
                try:
                    res = await get_document_chunks_paginated(
                        knowledge_id=knowledge_id, object_id=oid,
                    )
                    doc_chunks = res.get("chunks", []) if isinstance(res, dict) else (res or [])
                    chunks.extend(doc_chunks[:effective_num_chunks])
                except Exception as e:
                    logger.warning(f"retrieve_chunks: failed to fetch chunks for doc {oid}: {e}")
            logger.info(
                f"retrieve_chunks: fetched {len(chunks)} chunks across {len(object_ids)} "
                f"doc(s) in document order (≤{effective_num_chunks}/doc)"
            )
        else:
            chunks = await knowledge_search_chunks(
                knowledge_id=knowledge_id,
                query=query,
                num_chunks=effective_num_chunks,
                object_ids=object_ids,
                mode=mode,
            )
            logger.info(f"retrieve_chunks: returned {len(chunks)} chunks")

        artifact = {
            "chunks": chunks,
            "count": len(chunks),
            "question_shape": question_shape,
            # A "global chunk scan" = a query-driven pass with NO object_ids
            # (Rung 3): the agent searched the whole KB, not specific docs. We
            # flag it so build_grouped_evidence persists these hits as distinct
            # prior evidence (object_id + topic + chunk_ids), letting the next
            # turn scope a follow-up retrieve_chunks(object_ids=[...]) instead of
            # re-running the global pass. Scoped calls (object_ids present)
            # already know their docs, so they're not flagged.
            "global_chunk_scan": bool(query and not object_ids),
        }
        if query:
            artifact["query"] = query
        content = render_chunks(
            chunks=chunks,
            query=query or "",
            mode=mode,
            object_ids=object_ids,
        )
        return content, artifact
    except Exception as e:
        logger.error(f"Error in retrieve_chunks_tool: {e}", exc_info=True)
        artifact = {"chunks": [], "count": 0, "error": str(e)}
        content = render_chunks(chunks=[], query=query or "", mode=mode, error=str(e))
        return content, artifact
