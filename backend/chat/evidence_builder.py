"""Cross-turn evidence grouping — the producer half of the cross-turn memory
feature (chat/db/chat_logs.py::get_recent_evidence is the consumer half,
chat/evidence_relevance.py is the relevance gate on what get_recent_evidence
loads).

Ported from fs-agent-chat/chat_utils/response_builder/context_processors.py
(``build_grouped_evidence``, ``_build_global_chunk_group``, and their small
helpers) — found missing during Phase 5 (routers/chat.py) when wiring turn
persistence: chat/rag_agent/agent_execution.py::_flatten_prior_docs already
CONSUMES this exact grouped shape (its own docstring says so — "as stored by
build_grouped_evidence"), but nothing in this repo PRODUCED it. Phase 3a/b/c
ported get_recent_evidence + evidence_relevance.py per the plan's step 20,
but not build_grouped_evidence itself — without it, prior-turn evidence
persisted to knowledge_chat_logs.service_data would always be empty, and
cross-turn document reuse (the reason Option B was chosen over Option A in
the plan) would silently never work.

Repointed: the donor's ``chat_utils.knowledge_client::get_document_details``
import becomes ``chat.knowledge_adapter::get_document_details`` (same
signature: ``knowledge_id, object_ids -> List[Dict]``, Phase 1). No other
call-site changes — the input shape (``rag_context["tool_responses"]``,
``rag_context["agent_output"]``) is byte-identical between the two repos'
``_extract_retrieved_context_from_messages``.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from utils.logger import logger

# Bound the per-turn enrichment fetch (object_ids whose chunks carried no
# chunk_topic → one concurrent get_document_details batch). Runs AFTER the
# answer is already returned/streamed (turn persistence happens post-answer
# in routers/chat.py), so this only delays how soon the log row is written;
# on timeout we degrade to bare object_id + score + chunk_ids.
_ENRICH_TIMEOUT_S = 4.0

# Cross-turn evidence is re-injected into the NEXT turn's prompt and re-sent
# on every ReAct step within that turn, so its stored size is paid many times
# over. Keep each persisted doc compact: truncate the summary to this many
# chars (the full doc is always re-fetchable by object_id).
_EVIDENCE_SUMMARY_CHAR_CAP = 200


def _chunk_topic(chunk: Dict[str, Any]) -> str:
    """Representative topic for a chunk, checking direct + nested locations."""
    if not isinstance(chunk, dict):
        return ""
    meta = chunk.get("chunk_metadata")
    if not isinstance(meta, dict):
        meta = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return (
        chunk.get("chunk_topic")
        or chunk.get("topic")
        or meta.get("chunk_topic")
        or ""
    )


def _chunk_score(chunk: Dict[str, Any]) -> Optional[float]:
    """Relevance score for a chunk (``relevance_score`` -> ``score``)."""
    if not isinstance(chunk, dict):
        return None
    raw = chunk.get("relevance_score")
    if raw is None:
        raw = chunk.get("score")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _chunk_id(chunk: Dict[str, Any]) -> Optional[str]:
    """Stable chunk identity."""
    cid = chunk.get("chunk_id") or chunk.get("id")
    if cid:
        return str(cid)
    doc_ref = chunk.get("document_id") or chunk.get("object_id")
    return str(doc_ref) if doc_ref else None


async def _build_global_chunk_group(
    result: Dict[str, Any],
    tool_name: str,
    knowledge_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Build ONE prior-evidence group from a global chunk-scan artifact.

    Per parent ``object_id`` we keep: ``chunk_ids`` (which sections matched —
    recall), best ``score``, and a ``chunk_topic`` when the chunk carried one.
    object_ids whose chunks had NO topic are enriched in a single concurrent
    ``get_document_details`` batch -> ``page_title`` + ``category``. Score and
    chunk_ids are captured BEFORE enrichment and survive every path, so an
    enriched (or degraded) entry is never missing its score. Deliberately NO
    ``summary`` key — that field means "doc summary I read", and these are
    chunk hits, not full documents. The group is marked ``global_chunk_scan``.
    """
    chunks = result.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return None

    # Aggregate per object_id, preserving first-seen order.
    by_oid: Dict[str, Dict[str, Any]] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        oid = chunk.get("object_id") or chunk.get("document_id")
        if not oid:
            continue
        entry = by_oid.get(oid)
        if entry is None:
            entry = {"object_id": oid, "chunk_ids": [], "score": None, "chunk_topic": ""}
            by_oid[oid] = entry
        cid = _chunk_id(chunk)
        if cid and cid not in entry["chunk_ids"]:
            entry["chunk_ids"].append(cid)
        score = _chunk_score(chunk)
        if score is not None and (entry["score"] is None or score > entry["score"]):
            entry["score"] = score
        if not entry["chunk_topic"]:
            topic = _chunk_topic(chunk)
            if topic:
                entry["chunk_topic"] = topic

    if not by_oid:
        return None

    # Enrich object_ids that have no chunk_topic — one concurrent batch.
    missing_ids = [oid for oid, e in by_oid.items() if not e["chunk_topic"]]
    if missing_ids and knowledge_id:
        try:
            from chat.knowledge_adapter import get_document_details
            details = await asyncio.wait_for(
                get_document_details(knowledge_id=knowledge_id, object_ids=missing_ids),
                timeout=_ENRICH_TIMEOUT_S,
            )
            detail_map = {
                str(d["object_id"]): d
                for d in (details or [])
                if isinstance(d, dict) and d.get("object_id")
            }
            for oid in missing_ids:
                detail = detail_map.get(oid)
                if not isinstance(detail, dict):
                    continue
                title = detail.get("title") or detail.get("file_name")
                meta = detail.get("metadata") if isinstance(detail.get("metadata"), dict) else {}
                if title:
                    by_oid[oid]["page_title"] = title
                category = meta.get("category")
                if category:
                    by_oid[oid]["category"] = category
        except asyncio.TimeoutError:
            logger.warning(
                f"[evidence] global-chunk enrichment timed out after {_ENRICH_TIMEOUT_S}s "
                f"for {len(missing_ids)} doc(s); persisting bare object_id + score + chunk_ids"
            )
        except Exception as e:
            logger.warning(f"[evidence] global-chunk enrichment failed: {e}; persisting bare entries")
    elif missing_ids and not knowledge_id:
        logger.info("[evidence] global-chunk: no knowledge_id available, skipping topic enrichment")

    # Finalize entries — drop empty placeholder keys so the stored shape stays clean.
    docs: List[Dict[str, Any]] = []
    for entry in by_oid.values():
        clean: Dict[str, Any] = {"object_id": entry["object_id"]}
        if entry["chunk_ids"]:
            clean["chunk_ids"] = entry["chunk_ids"]
        if entry["score"] is not None:
            clean["score"] = round(entry["score"], 4)
        if entry["chunk_topic"]:
            clean["chunk_topic"] = entry["chunk_topic"]
        if entry.get("page_title"):
            clean["page_title"] = entry["page_title"]
        if entry.get("category"):
            clean["category"] = entry["category"]
        docs.append(clean)

    group: Dict[str, Any] = {
        "tool": tool_name,
        "source": "global_chunk_scan",
        "documents": docs,
    }
    if result.get("query"):
        group["query"] = result["query"]
    if result.get("question_shape"):
        group["question_shape"] = result["question_shape"]
    return group


async def build_grouped_evidence(
    rag_context: Dict[str, Any],
    knowledge_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Group retrieved documents per tool-call for cross-turn reuse.

    Walks ``rag_context["tool_responses"]`` (one entry per ToolMessage,
    preserved verbatim by ``_extract_retrieved_context_from_messages``) and
    builds one evidence group per ``retrieve_documents`` call that returned
    documents. Grouping key is the tool-call itself — we deliberately read
    from ``tool_responses`` rather than ``summaries`` because the latter
    flattens all docs into one object_id-keyed dict and loses the (filter,
    query) grouping.

    Each stored doc keeps ``object_id`` + ``summary`` (+ ``file_name`` when
    known) only — no chunk text. ``retrieve_chunks``-only object_ids are
    attributed to a query-only group so they aren't lost.

    Returns a newest-last list of groups, e.g.::

        [{"tool": "retrieve_documents", "query": "...", "metadata_filter": {...},
          "retrieval_mode": "hybrid_query",
          "documents": [{"object_id": "abc", "summary": "...", "file_name": "x.pdf"}]}]

    Empty/error artifacts produce no group (so they don't pollute the
    evidence count downstream). This is the function
    chat/rag_agent/agent_execution.py::_flatten_prior_docs's docstring
    refers to when it says "as stored by build_grouped_evidence".
    """
    groups: List[Dict[str, Any]] = []
    tool_responses = rag_context.get("tool_responses") or []
    if not isinstance(tool_responses, list):
        return groups

    # Citation-curated persistence: the retrieval agent cited the docs it
    # actually used (agent_output.relevant_docs). When it did, persist ONLY
    # those docs as cross-turn evidence — not the entire retrieval trail — so
    # the next turn's prior-evidence block carries just what mattered. Empty
    # citations (e.g. lookup/aggregate where the doc SET is the answer) ->
    # keep all docs (fallback). Exhaustive groups (those carrying a
    # total_match_count) are NEVER cited-filtered, so their completeness
    # guarantee survives.
    agent_output = rag_context.get("agent_output") or {}
    cited_doc_ids = {
        str(d.get("doc_id"))
        for d in (agent_output.get("relevant_docs") or [])
        if isinstance(d, dict) and d.get("doc_id")
    }

    for tr in tool_responses:
        if not isinstance(tr, dict):
            continue
        result = tr.get("result")
        if not isinstance(result, dict) or result.get("error"):
            continue
        tool_name = tr.get("tool_name") or "unknown"

        # -- retrieve_documents / legacy get-document-details --
        raw_docs = result.get("documents")
        if isinstance(raw_docs, list) and raw_docs:
            docs: List[Dict[str, Any]] = []
            for doc in raw_docs:
                if not isinstance(doc, dict) or not doc.get("object_id"):
                    continue
                metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
                summary_text = doc.get("summary") or metadata.get("summary", "") or ""
                # Truncate for cross-turn storage — full text re-fetchable by id.
                if len(summary_text) > _EVIDENCE_SUMMARY_CHAR_CAP:
                    summary_text = summary_text[:_EVIDENCE_SUMMARY_CHAR_CAP].rstrip() + "…"
                entry: Dict[str, Any] = {
                    "object_id": doc["object_id"],
                    "summary": summary_text,
                }
                file_name = (
                    doc.get("file_name") or doc.get("filename")
                    or metadata.get("file_name") or metadata.get("filename")
                )
                if file_name:
                    entry["file_name"] = file_name
                # Persist the structured metadata that's ALREADY in the
                # artifact (include_metadata=True path) — no extra fetch.
                # Follow-up turns that need attributes (author, date,
                # status, category…) read it back from here. Docs from
                # chunks-only or include_metadata=False calls simply have no
                # metadata; we accept that gap. Strip the summary key out
                # (already stored as `summary`) to avoid dupes.
                if metadata:
                    clean_meta = {k: v for k, v in metadata.items() if k != "summary"}
                    if clean_meta:
                        entry["metadata"] = clean_meta
                docs.append(entry)
            # Narrow to cited docs only — but never for exhaustive groups (a
            # total_match_count present means the doc set is the answer and
            # its completeness must be preserved). When citations exist and
            # this is a content group, drop the docs the agent didn't use; a
            # group left with no cited docs is dropped entirely by the
            # `if docs:` guard.
            apply_cited_filter = (
                bool(cited_doc_ids)
                and not isinstance(result.get("total_match_count"), int)
            )
            if apply_cited_filter:
                kept = [d for d in docs if str(d["object_id"]) in cited_doc_ids]
                if len(kept) != len(docs):
                    logger.info(
                        f"[evidence] citation filter: persisting {len(kept)}/{len(docs)} "
                        f"cited doc(s) from a {tool_name} group"
                    )
                docs = kept
            if docs:
                group: Dict[str, Any] = {"tool": tool_name, "documents": docs}
                if result.get("query"):
                    group["query"] = result["query"]
                if result.get("applied_filter"):
                    group["metadata_filter"] = result["applied_filter"]
                if result.get("retrieval_mode"):
                    group["retrieval_mode"] = result["retrieval_mode"]
                # `question_shape` of the prior call: lets a follow-up turn
                # see WHAT KIND of retrieval produced this group (fact_lookup
                # vs. aggregate vs. lookup, etc.). Combined with
                # `complete_population`, it tells the agent whether an
                # exhaustive answer is safe to read straight from the block.
                if result.get("question_shape"):
                    group["question_shape"] = result["question_shape"]
                # Completeness signal for exhaustive shapes: `total_match_count`
                # is set ONLY for exhaustive calls (aggregate/predicate/
                # selection). A group is PROVABLY COMPLETE — every matching
                # doc is listed, so a follow-up exhaustive count can be
                # answered straight from it — when the total is known (not a
                # lower bound / not truncated) AND every matched doc is
                # present. Recorded as `complete_population` so the injected
                # block can mark it and the agent can safely reuse it.
                tmc = result.get("total_match_count")
                if isinstance(tmc, int):
                    is_complete = (
                        not result.get("total_is_lower_bound")
                        and not result.get("truncated")
                        and tmc == len(docs)
                    )
                    if is_complete:
                        group["complete_population"] = tmc
                groups.append(group)
            continue

        # -- global chunk scan (retrieve_chunks, no object_ids) -- distinct group --
        # A query-driven pass over the whole KB. Persist the parent doc ids
        # with which chunks matched (chunk_ids), best score, and a
        # topic/title label — so the next turn can scope
        # retrieve_chunks(object_ids=[...]) instead of re-running the global
        # pass. Marked `source=global_chunk_scan` and kept WITHOUT a
        # `summary` key so it's never mistaken for a read document. Scoped
        # chunk calls (object_ids present) are NOT persisted here: the agent
        # already had those ids, so there's nothing to discover.
        chunks = result.get("chunks")
        if result.get("global_chunk_scan") and isinstance(chunks, list) and chunks:
            group = await _build_global_chunk_group(result, tool_name, knowledge_id)
            if group and group.get("documents"):
                groups.append(group)

    answer_group = _answer_result_group(rag_context)
    if answer_group is not None:
        # First, so the newest turn's result set is the first one a follow-up
        # finds (get_recent_evidence returns turns newest-first).
        groups.insert(0, answer_group)

    logger.info(f"[evidence] build_grouped_evidence: {len(groups)} group(s) from {len(tool_responses)} tool response(s)")
    return groups


def _answer_result_group(rag_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The documents this turn's answer presented (``rag_context["answer_result"]``,
    named by the answer node), as one evidence group the next turn can carry
    forward exactly (chat/rag_agent/result_set.py). Each document keeps the
    summary / file_name / metadata it had this turn, from whichever evidence
    showed it: this turn's retrieval, a carried result set, or prior-turn docs."""
    from chat.rag_agent.result_set import ANSWER_RESULT_SOURCE

    answer_result = rag_context.get("answer_result") or {}
    object_ids = [str(oid) for oid in answer_result.get("object_ids") or [] if oid]
    if not object_ids:
        return None

    known: Dict[str, Dict[str, Any]] = {}
    for doc in list(rag_context.get("prior_turn_docs") or []) + list(rag_context.get("carried_population") or []):
        if isinstance(doc, dict) and doc.get("object_id"):
            known[str(doc["object_id"])] = {"summary": doc.get("summary"), "file_name": doc.get("file_name"),
                                            "metadata": doc.get("metadata") or {}}
    for response in rag_context.get("tool_responses") or []:
        result = response.get("result") if isinstance(response, dict) else None
        if not isinstance(result, dict):
            continue
        for doc in result.get("documents") or []:
            if isinstance(doc, dict) and doc.get("object_id"):
                metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
                known[str(doc["object_id"])] = {
                    "summary": doc.get("summary") or metadata.get("summary"),
                    "file_name": doc.get("file_name") or doc.get("filename") or metadata.get("file_name"),
                    "metadata": metadata,
                }
    for oid, data in (rag_context.get("summaries") or {}).items():
        if isinstance(data, dict):
            prev = known.get(str(oid)) or {}
            known[str(oid)] = {
                "summary": data.get("summary") or prev.get("summary"),
                "file_name": data.get("file_name") or prev.get("file_name"),
                "metadata": {**(prev.get("metadata") or {}), **(data.get("metadata") or {})},
            }

    docs: List[Dict[str, Any]] = []
    for oid in object_ids:
        data = known.get(oid) or {}
        summary_text = str(data.get("summary") or "")
        if len(summary_text) > _EVIDENCE_SUMMARY_CHAR_CAP:
            summary_text = summary_text[:_EVIDENCE_SUMMARY_CHAR_CAP].rstrip() + "…"
        entry: Dict[str, Any] = {"object_id": oid, "summary": summary_text}
        if data.get("file_name"):
            entry["file_name"] = data["file_name"]
        metadata = {k: v for k, v in (data.get("metadata") or {}).items() if k != "summary"}
        if metadata:
            entry["metadata"] = metadata
        docs.append(entry)

    group: Dict[str, Any] = {
        "tool": "answer",
        "source": ANSWER_RESULT_SOURCE,
        "documents": docs,
        "complete_population": len(docs),
    }
    if answer_result.get("query"):
        group["query"] = answer_result["query"]
    return group
