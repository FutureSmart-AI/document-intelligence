"""
Renderers for RAG tool outputs.

Each tool returns a tuple ``(content, artifact)`` when configured with
``response_format="content_and_artifact"``:

* ``content`` (str)   — Markdown shown to the LLM. LangSmith's tool **Output**
  is almost always this string only — it does **not** reliably surface
  ``artifact``, so anything you need in traces should appear here too.
* ``artifact`` (dict) — full structured payload for ``agent_execution.py`` /
  graph state; do not depend on LangSmith displaying it.

Chunk renders append a bounded JSON block of merged chunk metadata so traces
stay inspectable without relying on ``artifact`` visibility.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

from utils.logger import logger
from chat.rag_tools.chunk_utils import normalized_chunk_metadata


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _truncate(text: Any, limit: int = 800) -> str:
    """Stringify ``text`` and truncate to ``limit`` chars with an ellipsis."""
    if text is None:
        return ""
    s = str(text).strip()
    if len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"


def _fmt_score(score: Any) -> str:
    try:
        return f"{float(score):.2f}"
    except (TypeError, ValueError):
        return str(score) if score is not None else "?"


def _pick_title(meta: Dict[str, Any], fallback: Optional[str] = None) -> str:
    if not isinstance(meta, dict):
        return fallback or "?"
    for key in ("title", "doc_title", "file_name", "filename", "name"):
        v = meta.get(key)
        if v:
            return str(v)
    return fallback or meta.get("object_id") or "?"


def _error_block(prefix: str, error: str) -> str:
    return f"{prefix}\n\n**Error:** {error}"


def _chunk_metadata_json_snippet(meta: Dict[str, Any], limit: int) -> Optional[str]:
    """Serialize merged chunk metadata for LangSmith / LLM-visible tool content."""
    if not meta or limit <= 0:
        return None
    try:
        raw = json.dumps(meta, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if raw in ("{}", "null"):
        return None
    if len(raw) > limit:
        raw = raw[: limit - 1].rstrip() + "…"
    return raw


def _normalize_chunk_body_text(text: Any) -> str:
    """Make ClickUp-exported chunk text easier to read in traces and for the LLM.

    Does not add missing facts — only normalizes common HTML line breaks that
    otherwise look like broken / empty table cells in Markdown.
    """
    if text is None:
        return ""
    s = str(text)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ---------------------------------------------------------------------------
# retrieve_chunks / search-chunks / get-document-chunks / get-chunks-batch
# ---------------------------------------------------------------------------

def render_chunks(
    *,
    chunks: List[Dict[str, Any]],
    query: Optional[str] = None,
    mode: Optional[str] = None,
    object_ids: Optional[List[str]] = None,
    parent_object_id: Optional[str] = None,
    error: Optional[str] = None,
    chunk_text_limit: int = 1000,
    metadata_json_limit: int = 1200,
) -> str:
    """Compact Markdown for any tool that returns a list of chunks."""
    header_bits: List[str] = []
    if query is not None:
        header_bits.append(f"query={query!r}")
    if mode:
        header_bits.append(f"mode={mode}")
    if object_ids:
        header_bits.append(f"scoped_to={len(object_ids)} doc(s)")
    if parent_object_id:
        header_bits.append(f"document={parent_object_id}")
    header_bits.append(f"count={len(chunks)}")

    lines: List[str] = [f"**Chunks** — {' · '.join(header_bits)}"]

    if error:
        return _error_block(lines[0], error)

    if not chunks:
        lines.append("\n_No chunks returned._")
        return "\n".join(lines)

    for i, c in enumerate(chunks, 1):
        if not isinstance(c, dict):
            continue
        meta = normalized_chunk_metadata(c)
        title = _pick_title(meta, fallback=c.get("document_id") or c.get("object_id"))
        score = c.get("relevance_score")
        chunk_id = c.get("chunk_id") or c.get("id") or "?"
        doc_id = c.get("document_id") or c.get("object_id") or meta.get("object_id") or "?"
        raw_text = c.get("chunk_text") or c.get("text") or c.get("content") or ""
        text = _normalize_chunk_body_text(raw_text)

        lines.append("")
        lines.append(f"**[{i}] {title}** · score={_fmt_score(score)} · doc=`{doc_id}` · chunk=`{chunk_id}`")
        if isinstance(meta, dict):
            page_link = meta.get("clickup_page_url") or meta.get("url")
            topics = meta.get("chunk_topic")
            artifact_type = meta.get("artifact_type")
            capabilities = meta.get("capabilities")
            product_area = meta.get("product_area")
            if page_link:
                lines.append(f"_page link:_ {page_link}")
            if topics:
                topics_str = ", ".join(topics) if isinstance(topics, list) else str(topics)
                lines.append(f"_topics:_ {topics_str}")
            if artifact_type:
                lines.append(f"_artifact_type:_ {artifact_type}")
            if capabilities:
                caps_str = ", ".join(capabilities) if isinstance(capabilities, list) else str(capabilities)
                lines.append(f"_capabilities:_ {caps_str}")
            if product_area:
                lines.append(f"_product_area:_ {product_area}")
        lines.append(text)
        meta_snip = _chunk_metadata_json_snippet(meta, metadata_json_limit)
        if meta_snip:
            lines.append("")
            lines.append("_chunk metadata (JSON, for traces):_")
            lines.append("```json")
            lines.append(meta_snip)
            lines.append("```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# retrieve_documents
# ---------------------------------------------------------------------------

def render_documents(
    *,
    documents: List[Dict[str, Any]],
    applied_filter: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    summary_limit: int = 600,
    metadata_json_limit: int = 1500,
    total_match_count: Optional[int] = None,
    limit: Optional[int] = None,
    is_count_only: bool = False,
    total_is_lower_bound: bool = False,
    query: Optional[str] = None,
    retrieval_mode: Optional[str] = None,
    metadata_lookup_over_retrieval_warning: Optional[str] = None,
    score_summary: Optional[Dict[str, Any]] = None,
) -> str:
    """Compact Markdown for retrieve_documents / get-document-details output.

    Args:
        total_is_lower_bound: when True, ``total_match_count`` represents only
            the number of docs we fetched (we hit the API fetch ceiling), so
            the real total may be higher. Rendered as ``≥N`` instead of ``N``.
    """
    if query:
        header = f"**Documents** — top semantic results count={len(documents)}"
    else:
        header = f"**Documents** — count={len(documents)}"
    if total_match_count is not None and total_match_count != len(documents):
        prefix = "≥" if total_is_lower_bound else ""
        header += f" of {prefix}{total_match_count} total matches"
    if query:
        header += f" · query={query!r}"
    if applied_filter:
        header += f" · filter={applied_filter}"
    if retrieval_mode and retrieval_mode not in ("hybrid_query", "object_ids"):
        header += f" · mode={retrieval_mode}"

    if metadata_lookup_over_retrieval_warning:
        header = (
            f"⚠️ **METADATA-LOOKUP OVER-RETRIEVAL WARNING**\n\n"
            f"{metadata_lookup_over_retrieval_warning}\n\n---\n\n"
        ) + header

    if score_summary and isinstance(score_summary, dict):
        s_min = score_summary.get("min")
        s_max = score_summary.get("max")
        s_avg = score_summary.get("avg")
        line = (
            f"\n\n**Relevance scores:** min={s_min}, max={s_max}, avg={s_avg} "
            f"across {score_summary.get('count_with_score', 0)} docs."
        )
        if "threshold" in score_summary:
            line += (
                f" Score threshold {score_summary['threshold']} active — "
                f"{score_summary.get('threshold_dropped', 0)} low-scoring docs dropped."
            )
        header += line

    if error:
        return _error_block(header, error)

    if not documents:
        return header + "\n\n_No documents matched._"

    # Truncation warning: tells the agent (and LLM) that more docs match
    # than were returned. Prevents drawing conclusions from a partial set.
    # Skip for count-only requests (limit=1, no summary/metadata) — the
    # truncation there is intentional.
    truncation_lines: List[str] = []
    if (
        not query
        and
        not is_count_only
        and total_match_count is not None
        and limit is not None
        and total_match_count > len(documents)
    ):
        missing = total_match_count - len(documents)
        truncation_lines.append(
            f"\n⚠️ **Truncated**: {missing} more document(s) match this filter but were not returned "
            f"(limit={limit}). Either narrow the filter or raise `limit` if you need the full set."
        )

    logger.info(f"render_documents: rendering {len(documents)} docs, filter={applied_filter}")
    lines: List[str] = [header] + truncation_lines
    for i, doc in enumerate(documents, 1):
        if not isinstance(doc, dict):
            logger.warning(f"render_documents: skipping non-dict entry at index {i}: {type(doc)}")
            continue

        # Top-level fields take precedence; fall back into metadata for file_name/summary.
        meta = doc.get("metadata") or {}
        title = doc.get("title") or _pick_title(meta, fallback=doc.get("object_id"))
        oid = doc.get("object_id") or "?"
        file_name = doc.get("file_name") or doc.get("filename") or meta.get("file_name") or meta.get("filename")
        summary = doc.get("summary") or (meta.get("summary") if isinstance(meta, dict) else None)

        lines.append("")
        # Per-doc header — include the relevance score when the backend
        # returned one. A scored doc renders as "**[1] Title** · id=`x` ·
        # score=0.83", giving the agent a quick visual cue for retrieval
        # quality without having to read the score_summary.
        score = doc.get("score")
        header_line = f"**[{i}] {title}** · id=`{oid}`"
        if isinstance(score, (int, float)):
            header_line += f" · score={score}"
        lines.append(header_line)
        if file_name:
            lines.append(f"_file:_ `{file_name}`")
        # top-level url is only populated when include_urls=True was passed to the tool
        if doc.get("url"):
            lines.append(f"_url:_ {doc['url']}")
        if summary:
            lines.append(_truncate(summary, summary_limit))

        # Emit the full metadata dict as a single JSON block so every field —
        # clickup_page_url, topics, artifact_type, capabilities, product_area,
        # and domain-specific keys (current_role, skills, experience, …) — reaches
        # the LLM in one place. Only strip keys that already have their own
        # dedicated lines above (title/summary/file_name surfaced separately).
        if isinstance(meta, dict):
            logger.info(
                f"render_documents: doc[{oid}] metadata keys={list(meta.keys())}"
            )
            already_rendered = {
                "summary", "file_name", "filename", "title", "object_id", "url",
            }
            extra_meta = {k: v for k, v in meta.items() if k not in already_rendered and v not in (None, "", [], {})}
            meta_snip = _chunk_metadata_json_snippet(extra_meta, metadata_json_limit)
            if meta_snip:
                lines.append("")
                lines.append("_metadata (JSON):_")
                lines.append("```json")
                lines.append(meta_snip)
                lines.append("```")
        else:
            logger.warning(f"render_documents: doc[{oid}] metadata missing or not a dict")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# retrieve_metadata_catalog
# ---------------------------------------------------------------------------

def render_metadata_catalog(
    *,
    fields: List[Dict[str, Any]],
    values_by_field: Optional[Dict[str, Dict[str, Any]]] = None,
    truncation_note: Optional[str] = None,
    error: Optional[str] = None,
    values_per_field_limit: int = 30,
) -> str:
    """Compact Markdown for the metadata catalog tool."""
    header = "**Metadata Catalog**"

    if error:
        return _error_block(header, error)

    if not fields:
        return header + "\n\n_No metadata fields found._"

    lines: List[str] = [header, f"fields: {len(fields)}"]
    if values_by_field:
        lines.append(f"with_values: {len(values_by_field)}")
    lines.append("")

    rendered_value_keys: set = set()
    for f in fields:
        if not isinstance(f, dict):
            continue
        fname = f.get("field_name", "?")
        ftype = f.get("field_type", "unknown")
        fdesc = (f.get("description") or "").strip()
        cardinality = f.get("cardinality_hint", "unknown")
        line = f"- **{fname}**"
        meta_bits = [f"type: `{ftype}`", f"cardinality: `{cardinality}`"]
        line += f" _({', '.join(meta_bits)})_"
        if fdesc:
            line += f"\n  Description: {_truncate(fdesc, 300)}"

        if values_by_field and fname in values_by_field:
            entry = values_by_field[fname] or {}
            values = entry.get("values") or []
            rendered_value_keys.add(fname)
            if values:
                shown = values[:values_per_field_limit]
                more = len(values) - len(shown)
                vals_str = ", ".join(f"`{_truncate(v, 120)}`" for v in shown)
                if more > 0:
                    vals_str += f", … (+{more} more)"
                line += f"\n  Values: {vals_str}"
            elif "error" in entry:
                line += f"\n  _Error fetching values: {entry['error']}_"

        lines.append(line)

    # Safety net: render any values_by_field entries whose key didn't match a
    # field row above (typically leaf paths where only the parent is in `fields`).
    if values_by_field:
        for vkey, entry in values_by_field.items():
            if vkey in rendered_value_keys:
                continue
            entry = entry or {}
            values = entry.get("values") or []
            ftype = entry.get("field_type", "unknown")
            line = f"- **{vkey}** _(type: `{ftype}`)_"
            if values:
                shown = values[:values_per_field_limit]
                more = len(values) - len(shown)
                vals_str = ", ".join(f"`{_truncate(v, 120)}`" for v in shown)
                if more > 0:
                    vals_str += f", … (+{more} more)"
                line += f"\n  Values: {vals_str}"
            elif "error" in entry:
                line += f"\n  _Error fetching values: {entry['error']}_"
            lines.append(line)

    if truncation_note:
        lines.extend([
            "",
            "**Truncation Note**",
            _truncate(truncation_note, 600),
        ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get-chunk-metadata (single chunk)
# ---------------------------------------------------------------------------

def render_chunk_metadata(
    *,
    chunk_id: str,
    metadata: Dict[str, Any],
    error: Optional[str] = None,
) -> str:
    header = f"**Chunk metadata** — chunk=`{chunk_id}`"
    if error:
        return _error_block(header, error)
    if not metadata:
        return header + "\n\n_No metadata returned._"

    lines = [header, ""]
    for k, v in metadata.items():
        lines.append(f"- **{k}:** {_truncate(v, 300)}")
    return "\n".join(lines)
