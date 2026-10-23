"""Relevance gate for cross-turn RAG evidence.

``get_recent_evidence`` loads the documents retrieved in the last N turns of a
session purely by recency — it has no idea what the *current* query is about.
Without a relevance check, an unrelated follow-up query is handed the previous
query's documents as ``## Documents already retrieved earlier in this
conversation`` and the reuse-gate prompt then steers the agent into answering
from those stale docs instead of retrieving fresh ones.

This module closes that gap: given the current query and the loaded evidence
groups, it keeps only the groups that are semantically related to the query
(embedding cosine over each group's label + doc summaries) and drops the rest.

Design choices:
  - **Group granularity, OR semantics.** A group is kept when ANY of its facets
    (its query/filter label OR any of its document summaries) clears the
    threshold. A legitimate follow-up ("tell me more about that", "list their
    names") shares the prior group's entities/topic and so always clears the
    bar; a genuinely unrelated query clears nothing and the group is dropped.
  - **Fail-open.** Embedding is an optimization, never a correctness gate for
    retrieval. Any error (no API key, network, import) → return the groups
    unchanged and log a warning, so the worst case is the pre-fix behavior.
  - **One batch call.** The query and every candidate string are embedded in a
    single ``aembed_documents`` request to keep the added latency to ~one round
    trip per turn.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# A group's doc summaries are capped before embedding — the topic signal lives
# in the first few hundred chars; the rest is dilution.
_SUMMARY_CHAR_CAP = 500
# Per-group and global caps on how many strings we embed, so a turn that
# retrieved a large population can't blow up the embedding request.
_MAX_DOC_TEXTS_PER_GROUP = 8
_MAX_TOTAL_TEXTS = 80


def _config() -> tuple[bool, float, str]:
    """(enabled, threshold, embed_model) from canonical config, with safe fallbacks."""
    try:
        from utils.setup_env import (
            RAG_EVIDENCE_RELEVANCE_ENABLED,
            RAG_EVIDENCE_RELEVANCE_THRESHOLD,
            RAG_EVIDENCE_EMBED_MODEL,
        )
        return (
            bool(RAG_EVIDENCE_RELEVANCE_ENABLED),
            float(RAG_EVIDENCE_RELEVANCE_THRESHOLD),
            (RAG_EVIDENCE_EMBED_MODEL or "text-embedding-3-small"),
        )
    except Exception:
        # Fall back to env directly so the gate still works if config import fails.
        enabled = os.environ.get(
            "RAG_EVIDENCE_RELEVANCE_ENABLED", "true"
        ).strip().lower() not in {"0", "false", "no", "off"}
        try:
            threshold = float(os.environ.get("RAG_EVIDENCE_RELEVANCE_THRESHOLD", "0.30"))
        except ValueError:
            threshold = 0.30
        model = os.environ.get("RAG_EVIDENCE_EMBED_MODEL", "text-embedding-3-small") or "text-embedding-3-small"
        return enabled, threshold, model


def _group_label(group: Dict[str, Any]) -> str:
    """Short human-readable label for logging which group was kept/dropped."""
    if group.get("query"):
        return f'query="{group["query"]}"'
    if group.get("metadata_filter"):
        return f'filter={group["metadata_filter"]}'
    if group.get("source") == "global_chunk_scan":
        return "(earlier chunk search)"
    return "(prior retrieval)"


def _group_texts(group: Dict[str, Any]) -> List[str]:
    """Independent comparison strings for a group (OR semantics).

    Returns the group's query/filter/shape label plus a capped set of per-doc
    strings (file_name / chunk_topic / page_title / summary). Each is compared
    to the query separately and the group's score is the MAX — so a group is
    relevant if any single facet is. An empty result means "no usable text to
    judge" → the caller keeps the group (fail-open).
    """
    texts: List[str] = []

    label_parts: List[str] = []
    if group.get("query"):
        label_parts.append(str(group["query"]))
    if group.get("question_shape"):
        label_parts.append(str(group["question_shape"]))
    if group.get("metadata_filter"):
        label_parts.append(str(group["metadata_filter"]))
    if label_parts:
        texts.append(" ".join(label_parts))

    for doc in (group.get("documents") or [])[:_MAX_DOC_TEXTS_PER_GROUP]:
        if not isinstance(doc, dict):
            continue
        # Covers both read-document groups (file_name/summary) and
        # global_chunk_scan groups (chunk_topic/page_title).
        parts = [
            str(doc[k]).strip()
            for k in ("file_name", "chunk_topic", "page_title", "summary")
            if isinstance(doc.get(k), str) and doc[k].strip()
        ]
        if parts:
            texts.append(" ".join(parts)[:_SUMMARY_CHAR_CAP])

    return texts


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def filter_evidence_by_relevance(
    query: str,
    groups: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep only the prior-evidence groups semantically related to ``query``.

    Embedding-based; fail-open. Returns the input ``groups`` unchanged when the
    gate is disabled, when there's nothing to judge, or on any error.
    """
    enabled, threshold, embed_model = _config()
    if not enabled:
        return groups
    if not query or not isinstance(query, str) or not query.strip():
        return groups
    if not groups:
        return groups

    try:
        from utils.setup_env import OPENAI_API_KEY

        if not OPENAI_API_KEY:
            logger.warning(
                "[evidence_relevance] No OPENAI_API_KEY available — skipping "
                "relevance gate (keeping all prior evidence)"
            )
            return groups

        # Build the flat list of strings to embed, tracking which group each
        # belongs to. Groups with no usable text are kept unconditionally.
        flat_texts: List[str] = []
        owners: List[int] = []  # owners[i] = index of group that flat_texts[i] belongs to
        no_text_group_idxs: set = set()
        for gi, group in enumerate(groups):
            if not isinstance(group, dict):
                no_text_group_idxs.add(gi)
                continue
            gtexts = _group_texts(group)
            if not gtexts:
                no_text_group_idxs.add(gi)
                continue
            for t in gtexts:
                if len(flat_texts) >= _MAX_TOTAL_TEXTS:
                    break
                flat_texts.append(t)
                owners.append(gi)

        if not flat_texts:
            # Nothing judgeable — keep everything (fail-open).
            return groups

        from langchain_openai import OpenAIEmbeddings

        embeddings = OpenAIEmbeddings(model=embed_model, api_key=OPENAI_API_KEY)
        # One round trip: query first, then every candidate string.
        vectors = await embeddings.aembed_documents([query] + flat_texts)
        query_vec = vectors[0]
        text_vecs = vectors[1:]

        best_score: Dict[int, float] = {}
        for vec, gi in zip(text_vecs, owners):
            score = _cosine(query_vec, vec)
            if score > best_score.get(gi, -1.0):
                best_score[gi] = score

        kept: List[Dict[str, Any]] = []
        dropped_labels: List[str] = []
        for gi, group in enumerate(groups):
            if gi in no_text_group_idxs:
                kept.append(group)  # couldn't judge → keep
                continue
            score = best_score.get(gi, 0.0)
            if score >= threshold:
                kept.append(group)
            else:
                dropped_labels.append(f"{_group_label(group)} (score={score:.2f})")

        if dropped_labels:
            logger.info(
                f"[evidence_relevance] Dropped {len(dropped_labels)}/{len(groups)} "
                f"prior-evidence group(s) below threshold {threshold:.2f} for current "
                f"query: {dropped_labels}"
            )
        else:
            logger.info(
                f"[evidence_relevance] All {len(groups)} prior-evidence group(s) "
                f"relevant to current query (threshold {threshold:.2f})"
            )
        return kept

    except Exception as e:
        logger.warning(
            f"[evidence_relevance] Relevance gate failed ({e}); keeping all prior "
            f"evidence (fail-open)"
        )
        return groups
