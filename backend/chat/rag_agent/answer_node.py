"""
RAG sub-agent — final-answer node.

Runs AFTER the retrieval ReAct agent. The retrieval agent produced:
  - ``summaries``        : {object_id: {summary, metadata, file_name, title, url, ...}}
  - ``chunks``           : list of chunk dicts with text + parent doc id
  - ``metadata_values``  : {field_name: [values]} from catalog calls
  - ``raw_output``       : whatever the retrieval agent last said (usually empty
                           in retrieval-only mode)

This node consumes that context and writes the user-facing answer using ONLY the
retrieved evidence. The prompt is a hardcoded module constant (no Agent-Registry
base-prompt lookup in this service — see _ANSWER_NODE_DEFAULT_PROMPT below).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langsmith import traceable

from chat.byok_fallback import ByokKeyRejected, astream_with_key_fallback, run_with_key_fallback
from chat.rag_agent.agent_output import IsAnsweredVerdict, RagAnswerOutput
from chat.utils.messages import build_history_messages
from chat.utils.usage import extract_cache_tokens
from utils.logger import logger
from utils.tracing import track_time
import utils.setup_env as setup_env


_MAX_SUMMARY_CHARS = 1200
# Chunk text is NOT truncated here — the retrieval layer already controls how
# many chunks come back via `num_chunks`, and per-chunk truncation silently
# drops content the agent specifically opened chunks to read (cited passages,
# tables, lists, code blocks). The chunk-count cap below is the only guard.
_MAX_CHUNKS_IN_CONTEXT = 30
_MAX_METADATA_VALUES_PER_FIELD = 50
_MAX_LIST_VALUES_PER_FIELD = 20
_MAX_LIST_FIELDS_PER_DOC = 6

# Hardcoded default prompt for this node — no Agent-Registry dependency in
# this service. Formerly resolved via get_base_prompt("rag_answer_node") with
# this exact string as the config-miss fallback; the fallback is now the only
# prompt.
_ANSWER_NODE_DEFAULT_PROMPT = (
    "You are the final-answer node of the RAG sub-agent. Answer the user's "
    "question using ONLY the retrieved evidence below. Do not invent facts. "
    "Do not mention tools, retrieval, object_ids, or scoring rubrics. "
    "{user_style_directives}"
)


def _looks_like_url(value: Any) -> bool:
    """True if ``value`` is a string starting with http:// or https://.

    Value-based detection — agnostic to the field name. Works for any KB
    schema (page_url, clickup_page_url, linkedin_url, product_link, etc.).
    """
    return (
        isinstance(value, str)
        and len(value) < 2048
        and value.strip().lower().startswith(("http://", "https://"))
    )


def _classify_metadata_fields(
    metadata: Dict[str, Any],
) -> tuple[Dict[str, str], Dict[str, List[str]], Dict[str, Any]]:
    """Sort a doc's metadata into three buckets by VALUE shape:

    - ``url_fields``   — string values that look like URLs (single value per
      field). Multiple URL fields per doc are supported.
    - ``list_fields``  — list-of-string fields whose values aren't all URLs
      (tags, entities, tools, categories, topics, …). The list members are
      visible candidates for matching names mentioned in the user's question.
    - ``scalar_fields`` — everything else (strings, numbers, dicts, lists of
      non-strings). Emitted as compact JSON.

    No field names are hardcoded — the bucket is decided entirely by what
    the value looks like. Generic across any KB schema.
    """
    if not isinstance(metadata, dict):
        return {}, {}, {}

    url_fields: Dict[str, str] = {}
    list_fields: Dict[str, List[str]] = {}
    scalar_fields: Dict[str, Any] = {}

    for key, value in metadata.items():
        if key in {"summary", "title"}:
            continue  # already surfaced as top-level lines

        if _looks_like_url(value):
            url_fields[key] = value.strip()
            continue

        if isinstance(value, list) and value:
            str_items = [v.strip() for v in value if isinstance(v, str) and v.strip()]
            if str_items and len(str_items) == len(value):
                # Pure list-of-string. If every item is a URL, treat as a
                # URL field group; otherwise it's an entity/tag-like list.
                if all(_looks_like_url(v) for v in str_items):
                    url_fields[key] = ", ".join(str_items[:_MAX_LIST_VALUES_PER_FIELD])
                else:
                    list_fields[key] = str_items[:_MAX_LIST_VALUES_PER_FIELD]
                continue

        scalar_fields[key] = value

    return url_fields, list_fields, scalar_fields


def _truncate(text: str, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_summaries(summaries: Dict[str, Any]) -> str:
    """Render document summaries + metadata as a numbered block.

    Each doc is rendered with discrete labeled lines per metadata bucket
    (URLs, list-typed fields, scalar fields), classified by VALUE shape — no
    field names are hardcoded. This lets the answer LLM follow style
    directives like "include a link for each mentioned tool" deterministically
    on any KB schema (resume, ClickUp, Notion, product catalog, …): scan the
    URL lines for link targets, scan the list-field lines for names the user
    might be asking about.
    """
    if not summaries:
        return "(no documents retrieved)"

    lines: List[str] = []
    for idx, (object_id, data) in enumerate(summaries.items(), start=1):
        if not isinstance(data, dict):
            data = {"summary": str(data)}

        title = data.get("title") or data.get("file_name") or f"Document {idx}"
        summary = _truncate(data.get("summary") or "", _MAX_SUMMARY_CHARS)
        metadata = data.get("metadata") or {}

        url_fields, list_fields, scalar_fields = _classify_metadata_fields(metadata)

        # Promote the top-level url field (set when include_urls=True at
        # retrieval) so it appears even if it's not in metadata.
        top_url = data.get("url")
        if isinstance(top_url, str) and top_url.strip() and "url" not in url_fields:
            url_fields = {"url": top_url.strip(), **url_fields}

        lines.append(f"[Doc {idx}] {title}")

        # URL block — emitted once per URL-shaped field so the agent can pick
        # the most-specific URL when multiple are available (e.g. per-tool
        # page beats a roundup page).
        for field_name, url_value in url_fields.items():
            lines.append(f"{field_name}: {url_value}")

        # List-field block — every list-of-string field is rendered as its
        # own line. This is where named entities, tools, tags, categories,
        # topics, etc. become visually distinct from a JSON dump.
        for field_name in list(list_fields.keys())[:_MAX_LIST_FIELDS_PER_DOC]:
            values = list_fields[field_name]
            lines.append(f"{field_name}: {', '.join(values)}")
        if len(list_fields) > _MAX_LIST_FIELDS_PER_DOC:
            omitted = len(list_fields) - _MAX_LIST_FIELDS_PER_DOC
            lines.append(f"(+{omitted} more list-typed metadata fields omitted)")

        if summary:
            lines.append(f"summary: {summary}")

        # Scalar block — everything that wasn't a URL or string-list. Kept
        # as compact JSON because the LLM can still parse it but it's less
        # visually noisy than the full metadata blob.
        if scalar_fields:
            try:
                meta_blob = json.dumps(scalar_fields, ensure_ascii=False, default=str)
            except Exception:
                meta_blob = str(scalar_fields)
            lines.append(f"other_metadata: {_truncate(meta_blob, 1500)}")

        lines.append("")  # blank line between docs

    return "\n".join(lines).rstrip()


def _format_chunks(chunks: List[Any], summaries: Dict[str, Any]) -> str:
    """Render retrieved chunks with their parent doc reference."""
    if not chunks:
        return "(no chunk excerpts retrieved)"

    title_by_id: Dict[str, str] = {}
    for doc_id, data in summaries.items():
        if isinstance(data, dict):
            title_by_id[doc_id] = data.get("title") or data.get("file_name") or doc_id

    lines: List[str] = []
    rendered = 0
    for chunk in chunks:
        if rendered >= _MAX_CHUNKS_IN_CONTEXT:
            lines.append(f"… (+{len(chunks) - rendered} more chunks omitted)")
            break
        if not isinstance(chunk, dict):
            continue

        doc_id = chunk.get("document_id") or chunk.get("object_id") or ""
        parent = title_by_id.get(doc_id, doc_id or "unknown")
        text = chunk.get("chunk_text") or chunk.get("text") or chunk.get("content") or ""
        if not isinstance(text, str) or not text.strip():
            continue

        rendered += 1
        lines.append(f"[Chunk {rendered}] (from: {parent})")
        lines.append(text)
        lines.append("")

    if not lines:
        return "(no chunk excerpts retrieved)"

    return "\n".join(lines).rstrip()


# Catalog-values cardinality ceiling — fields with more distinct values than
# this are effectively free-text (every doc has its own value: title,
# summary-like text, etc.) and dumping their full enumeration in the answer
# context is noise, not signal. A "category" with 94 distinct values for 94
# docs is not a category — it's an identifier field.
_CATALOG_VALUES_CARDINALITY_CEILING = 30


def _format_metadata_values(metadata_values: Dict[str, Any]) -> str:
    """Render catalog values fetched during retrieval as an AUTHORITATIVE block.

    These values come from ``resolve_filter_value`` / ``retrieve_metadata_catalog``
    calls — they are the complete canonical list for a field, straight from the
    catalog. For category-enumeration questions ("what services do we have",
    "list our X"), this list IS the answer — it supersedes any document-level
    enumeration the agent may also have fetched, which can silently truncate
    and drop items.

    **Filters out high-cardinality fields.** A field with >30 distinct values
    is not a meaningful "category" enumeration — it's effectively a free-text
    or identifier field (`title`, `email`, `summary`, …). Dumping all 94 titles
    when the user asks about ONE specific doc is pure context bloat.
    """
    if not metadata_values or not isinstance(metadata_values, dict):
        return ""
    lines: List[str] = []
    for field_name, values in metadata_values.items():
        if not isinstance(values, list):
            continue
        # Skip high-cardinality fields — they're not meaningful enumerations.
        if len(values) > _CATALOG_VALUES_CARDINALITY_CEILING:
            continue
        truncated = values[:_MAX_METADATA_VALUES_PER_FIELD]
        suffix = f" (+{len(values) - len(truncated)} more)" if len(values) > len(truncated) else ""
        # Render each value on its own bullet for visual clarity — a long
        # comma-separated list is easy to skim past.
        value_lines = "\n".join(f"  - `{v}`" for v in truncated)
        lines.append(
            f"- **{field_name}** ({len(values)} total{suffix.strip(' ()')}):\n{value_lines}"
        )
    if not lines:
        return ""
    return (
        "═══════════════════════════════════════════════════════════════════════\n"
        "**AUTHORITATIVE CATALOG VALUES — complete enumerations from the metadata catalog**\n"
        "═══════════════════════════════════════════════════════════════════════\n"
        "Each list below is the COMPLETE canonical value list for its field. For "
        "category-enumeration questions (\"what X do we have\", \"list our X\"), the "
        "matching list IS the answer — include EVERY value verbatim. Documents in "
        "the Retrieved Documents section may be a truncated sample and can SILENTLY "
        "OMIT values whose docs fell beyond the retrieval limit — never enumerate "
        "from those docs when an authoritative list is available here.\n\n"
        + "\n".join(lines)
    )


_URL_DIRECTIVE_KEYWORDS = ("url", "link", "hyperlink", "href")


def _directive_mentions_urls(directives: Optional[str]) -> bool:
    """True if a user style directive references URLs / links in any form."""
    if not isinstance(directives, str) or not directives.strip():
        return False
    lowered = directives.lower()
    return any(kw in lowered for kw in _URL_DIRECTIVE_KEYWORDS)


def _evidence_has_urls(retrieved_context: Dict[str, Any]) -> bool:
    """True if any retrieved doc carries at least one URL-shaped value.

    Walks the doc summaries and their metadata, classifying values by shape
    (no field-name assumptions). Used to decide whether URL-following
    guidance is worth appending to the system prompt — a resume KB with no
    URLs anywhere should not see URL instructions cluttering the prompt.
    """
    summaries = retrieved_context.get("summaries") or {}
    for data in summaries.values():
        if not isinstance(data, dict):
            continue
        if _looks_like_url(data.get("url")):
            return True
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        for v in metadata.values():
            if _looks_like_url(v):
                return True
            if isinstance(v, list) and any(_looks_like_url(x) for x in v):
                return True
    return False


def _format_agent_output(agent_output: Dict[str, Any]) -> str:
    """Render the RAG sub-agent's structured self-report for the answer LLM.

    Surfaces stop_reason, confidence, the agent's draft answer, open_questions,
    and relevant_docs so the answer node can ground its response in what the
    retrieval agent already synthesised.
    """
    if not agent_output:
        return ""
    lines: List[str] = [
        "## RAG Agent Self-Report",
        f"stop_reason: {agent_output.get('stop_reason')}",
        f"confidence: {agent_output.get('confidence')}",
    ]
    open_qs = agent_output.get("open_questions") or []
    if open_qs:
        lines.append(f"open_questions: {open_qs}")
    answer = (agent_output.get("answer") or "").strip()
    if answer:
        lines.append(f"agent_answer: {answer}")
    relevant_docs = agent_output.get("relevant_docs") or []
    if relevant_docs:
        lines.append(f"relevant_docs ({len(relevant_docs)}):")
        for doc in relevant_docs:
            if isinstance(doc, dict):
                lines.append(f"  - [{doc.get('doc_id', '?')}] {doc.get('why', '')}")
    return "\n".join(lines)


def _stringify_content(content: Any) -> str:
    """Normalize a LangChain message/chunk ``content`` value to plain text.

    Most providers stream ``content`` as a plain string, but some (Gemini,
    Anthropic) yield a list of part-dicts (e.g. ``[{"type": "text", "text":
    "..."}]``) even for plain text responses. ``"".join()`` on a list
    containing one of those raises ``TypeError`` — normalize here instead of
    assuming string content everywhere it's consumed.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p)
            for p in content
        )
    return str(content) if content else ""


def _cited_ids_from_agent_output(agent_output: Dict[str, Any]) -> tuple[set, set]:
    """Extract the agent's cited (doc_ids, chunk_ids) from ``relevant_docs``.

    These are the load-bearing citations: the answer step grounds ONLY on the
    evidence the retrieval agent says it actually used (retriever-curates /
    synthesizer-trusts-citations pattern). Empty sets mean "no citations" → the
    caller falls back to showing all evidence so the answer node is never
    starved.
    """
    doc_ids: set = set()
    chunk_ids: set = set()
    for d in (agent_output.get("relevant_docs") or []):
        if not isinstance(d, dict):
            continue
        did = d.get("doc_id")
        if did:
            doc_ids.add(str(did))
        for cid in (d.get("chunk_ids") or []):
            if cid:
                chunk_ids.add(str(cid))
    return doc_ids, chunk_ids


def _chunk_matches_citation(chunk: Any, cited_doc_ids: set, cited_chunk_ids: set) -> bool:
    """True if a chunk belongs to a cited doc OR is itself a cited chunk.

    Lenient on the chunk side: a chunk of a cited doc is kept even when its id
    isn't in ``cited_chunk_ids`` (chunk-id rendering varies across backends).
    The win is dropping every chunk that belongs to a NON-cited doc.
    """
    if not isinstance(chunk, dict):
        return False
    parent = chunk.get("document_id") or chunk.get("object_id")
    if parent and str(parent) in cited_doc_ids:
        return True
    cid = chunk.get("chunk_id") or chunk.get("id")
    if cid and str(cid) in cited_chunk_ids:
        return True
    idx = chunk.get("chunk_idx")
    if parent is not None and idx is not None and f"{parent}::{idx}" in cited_chunk_ids:
        return True
    return False


def _build_context_block(
    retrieved_context: Dict[str, Any],
) -> str:
    summaries = retrieved_context.get("summaries") or {}
    chunks = retrieved_context.get("chunks") or []
    metadata_values = retrieved_context.get("metadata_values") or {}
    agent_output = retrieved_context.get("agent_output") or {}

    # CITATION-GROUNDED CONTEXT. The retrieval agent already reasoned over the
    # full retrieval trail and cited the docs/chunks it actually used
    # (relevant_docs). Ground the answer ONLY on that cited evidence — drop the
    # docs it retrieved but never used, and the chunks of non-cited docs. This
    # is the retriever-curates / synthesizer-trusts-citations best practice and
    # is the main lever against answer-node context bloat. Fallback: if the
    # agent cited nothing (e.g. lookup/aggregate where the doc SET is the
    # answer, or it under-reported), show everything — never starve the answer.
    cited_doc_ids, cited_chunk_ids = _cited_ids_from_agent_output(agent_output)
    if cited_doc_ids:
        filtered_summaries = {
            oid: data for oid, data in summaries.items() if str(oid) in cited_doc_ids
        }
        filtered_chunks = [
            c for c in chunks if _chunk_matches_citation(c, cited_doc_ids, cited_chunk_ids)
        ]
        # Apply only if the filter left something to ground on (guards against
        # citation ids that don't match any retrieved evidence).
        if filtered_summaries or filtered_chunks:
            logger.info(
                f"[answer_node] citation filter: grounding on {len(filtered_summaries)} "
                f"cited doc(s) + {len(filtered_chunks)} chunk(s) "
                f"(dropped {len(summaries) - len(filtered_summaries)} uncited doc(s), "
                f"{len(chunks) - len(filtered_chunks)} uncited chunk(s))"
            )
            summaries = filtered_summaries
            chunks = filtered_chunks

    sections: List[str] = []

    # Agent self-report block — surfaces the retrieval agent's stop_reason,
    # confidence, draft answer, and grounding docs so the answer LLM has
    # the agent's synthesis alongside the raw evidence.
    agent_block = _format_agent_output(agent_output)
    if agent_block:
        sections.extend([agent_block, ""])

    # Catalog values block — authoritative enumerations of field values
    # from resolve_filter_value / retrieve_metadata_catalog calls.
    meta_block = _format_metadata_values(metadata_values)
    if meta_block:
        sections.extend([meta_block, ""])

    # Resolve the authoritative count from the most recent retrieve_documents
    # tool response. Surfacing it in the section header lets the answer node
    # follow its prompt rule ("use the tool's total_match_count, don't count
    # the doc list") — without this line the number isn't visible anywhere
    # in the context and the LLM has no choice but to count cards (and miscount
    # on long lists). Reads an existing field from existing tool_responses;
    # no new helper / no aggregation logic.
    docs_header = "## Retrieved Documents"
    backend_total: Optional[int] = None
    for record in retrieved_context.get("tool_responses") or []:
        if not isinstance(record, dict):
            continue
        result = record.get("result")
        if isinstance(result, dict):
            total = result.get("total_match_count")
            if isinstance(total, int) and (backend_total is None or total > backend_total):
                backend_total = total
    if backend_total is not None:
        docs_header = (
            f"## Retrieved Documents ({len(summaries)} shown · "
            f"**tool reported total_match_count = {backend_total}** — "
            f"use THIS number for any \"how many\" answer; do NOT count the cards below)"
        )

    sections.extend([
        docs_header,
        _format_summaries(summaries),
        "",
        "## Retrieved Chunks",
        _format_chunks(chunks, summaries),
    ])

    prior_turn_docs = retrieved_context.get("prior_turn_docs") or []
    # Citation gating for prior-turn evidence:
    #   • Agent cited docs → keep only the prior docs it actually cited. Uncited
    #     prior docs were not used → don't bloat the answer context with them.
    #   • Agent cited nothing BUT fresh docs/chunks were retrieved this turn →
    #     ground on that fresh evidence; drop prior-turn docs entirely. This
    #     closes the no-citation leak where ALL prior docs would otherwise flow
    #     into the answer (the structural stale-doc path for an unrelated query).
    #   • Agent cited nothing AND no fresh evidence (it reused prior docs / found
    #     nothing new) → keep prior-turn docs: they're the only grounding, and
    #     they're already relevance-gated upstream (see rag_node).
    if cited_doc_ids and prior_turn_docs:
        prior_turn_docs = [
            d for d in prior_turn_docs
            if isinstance(d, dict) and str(d.get("object_id")) in cited_doc_ids
        ]
    elif prior_turn_docs and (summaries or chunks):
        logger.info(
            f"[answer_node] dropping {len(prior_turn_docs)} prior-turn doc(s): "
            f"agent cited none but fresh evidence was retrieved this turn — "
            f"grounding on fresh docs/chunks"
        )
        prior_turn_docs = []
    if prior_turn_docs:
        lines = [
            "",
            f"## Prior-Turn Evidence ({len(prior_turn_docs)} doc(s) from earlier in this conversation)",
            "These documents were retrieved in a previous turn. Use them to ground your answer "
            "when they are relevant — treat them identically to Retrieved Documents above.",
        ]
        for doc in prior_turn_docs:
            oid = doc.get("object_id", "?")
            summary = (doc.get("summary") or "").strip()
            file_name = doc.get("file_name", "")
            metadata = doc.get("metadata") or {}
            lines.append(f"\n### [{oid}]" + (f" — {file_name}" if file_name else ""))
            if summary:
                lines.append(summary)
            for k, v in metadata.items():
                lines.append(f"- **{k}**: {v}")
        sections.extend(lines)

    return "\n".join(sections).rstrip()


def _build_system_prompt(
    generate_answer_instructions: Optional[str],
    *,
    include_url_guidance: bool = False,
) -> str:
    """Compose the answer-node system prompt.

    ``include_url_guidance`` controls whether the "How to follow URL/link
    directives" block is appended. Pass True only when the run actually
    needs it — i.e. the user's style directive mentions URLs/links, OR the
    retrieved evidence contains at least one URL. For URL-less KBs (resume,
    plain text wiki, etc.) the block is skipped to keep the prompt clean.
    """
    base = _ANSWER_NODE_DEFAULT_PROMPT

    style = ""
    if isinstance(generate_answer_instructions, str) and generate_answer_instructions.strip():
        style_parts = [
            "\n\n",
            "═══════════════════════════════════════════════════════════════════════\n",
            "**USER STYLE DIRECTIVES — HARD CONSTRAINTS on the final answer**\n",
            "═══════════════════════════════════════════════════════════════════════\n",
            "These are explicit user-configured rules, NOT suggestions. Apply ALL of ",
            "them to the final answer. Before finalizing, re-read the directives and ",
            "verify your answer complies with each one.\n\n",
            "Directives:\n",
            generate_answer_instructions.strip(),
        ]

        if include_url_guidance:
            style_parts.append(
                "\n\n"
                "**How to follow URL/link directives (MANDATORY when directives "
                "require links):**\n"
                "1. URLs live inside the `Retrieved Documents` cards and inside "
                "`Retrieved Chunks`. Any line under a doc card whose value starts "
                "with `http://` or `https://` is a URL field (the field name varies "
                "by KB — `url`, `page_url`, `clickup_page_url`, `linkedin_url`, "
                "`product_link`, etc. — read what's there, don't assume a name).\n"
                "2. To link a name mentioned in the answer (tool / product / person "
                "/ page / entity): find the doc whose title matches that name, OR "
                "the doc whose metadata list-fields contain that name as a value. "
                "Use a URL field from THAT doc.\n"
                "3. **Format:** render each linked name as `**[Name](URL)**`. "
                "Example: `**[Descript](https://aidemos.com/tools/descript)**` — "
                "reviewed as an AI storyboarding platform.\n"
                "4. INLINE the URL in the SAME sentence — do NOT offer to share the "
                "link, do NOT list links separately, do NOT say \"if you want\".\n"
                "5. If no doc carries a URL for a name you're mentioning, write the "
                "name without a link. NEVER fabricate a URL. When the same name "
                "appears in multiple docs, prefer the doc whose TITLE matches the "
                "name (per-item page beats a roundup page)."
            )

        style = "".join(style_parts)

    if "{user_style_directives}" in base:
        return base.replace("{user_style_directives}", style)
    return base + style


def _format_citations_section(relevant_docs: List[Dict[str, Any]]) -> str:
    """Render a `## Sources` section from RagAgentOutput.relevant_docs.

    Each row is a flat ``{doc_id, why}`` — one bullet per doc the agent used
    to ground its answer. Duplicate doc_ids are collapsed (first ``why`` wins).
    """
    if not relevant_docs:
        return ""

    lines: List[str] = ["", "## Sources"]
    seen: set = set()
    for doc in relevant_docs:
        if not isinstance(doc, dict):
            continue
        doc_id = (doc.get("doc_id") or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        why = (doc.get("why") or "").strip()
        lines.append(f"- [{doc_id}] {why}" if why else f"- [{doc_id}]")

    return "\n".join(lines) if len(lines) > 2 else ""


async def _judge_is_answered(
    llm: Any,
    system_content: str,
    query: str,
    answer_text: str,
    invoke_config: RunnableConfig,
) -> bool:
    """Second, minimal structured-output call on the streaming path: ask the
    same answer LLM to self-judge is_answered against the answer it just
    streamed. Reuses the same system content (question + evidence) so the
    verdict is grounded in the same context the answer itself was produced
    from. Fails open to True — a judge-call failure must not silently
    downgrade an otherwise-good answer to "not answered".
    """
    judge_messages: List[Any] = [
        SystemMessage(content=system_content),
        HumanMessage(content=query),
        HumanMessage(
            content=(
                "The answer below was already produced and shown to the user. "
                "Self-judge only: does it directly resolve the question using "
                "the retrieved evidence?\n\n"
                f"--- ANSWER ---\n{answer_text}"
            )
        ),
    ]
    try:
        structured_llm = llm.with_structured_output(IsAnsweredVerdict)
        verdict = await structured_llm.ainvoke(
            judge_messages,
            config={**invoke_config, "run_name": "rag_answer_is_answered_judge"},
        )
        return bool(verdict.is_answered)
    except Exception as e:
        logger.warning(f"[answer_node] is_answered judge call failed, defaulting True: {e}")
        return True


@track_time("RAG: Answer Node")
@traceable(name="rag_answer_node", run_type="chain")
async def generate_rag_answer(
    query: str,
    retrieved_context: Dict[str, Any],
    llm_provider: str = "openai",
    llm_model: str = "gpt-4o-mini",
    owner_id: Optional[str] = None,
    generate_answer_instructions: Optional[str] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    token_queue: Optional[Any] = None,
    max_tokens: Optional[int] = None,
    knowledge_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Produce the final user-facing answer from retrieved context.

    Wrapped with ``@traceable`` so this step appears as its own node in the
    LangSmith trace — a sibling of ``rag_sub_agent`` under the ``rag`` node —
    rather than as a bare ChatOpenAI span.

    Context placement: retrieved evidence is appended to the **system**
    message, not the human message. The system role carries grounding /
    instructions ("answer ONLY from this evidence"); the human message is
    reserved for the user's actual question. This matches the LangChain RAG
    convention (`{context}` rendered inside the system prompt) and keeps the
    model's view of "user input" clean.

    max_tokens is resolved by chat/config.py::resolve_chat_config for the
    generate_answer node (knowledge_chat_config.max_tokens / per-node
    override) and threaded straight into get_llm below — this is the one
    field in the resolution chain that is actually applied, unlike
    temperature (see chat/rag_node.py's note on that gap).

    Returns:
        ``{"answer": str, "usage_metrics": {input_tokens, output_tokens, total_tokens}}``
    """
    # URL guidance + entity→URL pre-computation are only added when the run
    # URL guidance is added when EITHER the directive mentions URLs OR the
    # retrieved evidence carries URLs. When the directive mentions URLs but
    # the evidence has none, the guidance is still appended so the LLM knows
    # to omit links gracefully instead of fabricating them.
    directive_wants_urls = _directive_mentions_urls(generate_answer_instructions)
    evidence_has_urls = _evidence_has_urls(retrieved_context)
    include_url_guidance = directive_wants_urls or evidence_has_urls

    base_system_prompt = _build_system_prompt(
        generate_answer_instructions,
        include_url_guidance=include_url_guidance,
    )
    context_block = _build_context_block(retrieved_context)

    system_content = (
        f"{base_system_prompt}\n\n"
        f"═══════════════════════════════════════════════════════════════════════\n"
        f"**RETRIEVED EVIDENCE — answer ONLY from what appears below**\n"
        f"═══════════════════════════════════════════════════════════════════════\n"
        f"{context_block}"
    )

    # This node does NOT bind any tools to the LLM — it's a plain streamed
    # text completion (below) or a with_structured_output(RagAnswerOutput)
    # call, run AFTER retrieval is already done. for_function_calling=False
    # so GPT-5 keeps reasoning_effort (no function-tools + reasoning_effort
    # 400 concern here — see chat/openai_reasoning.py).
    #
    # The model is built lazily, inside the callables below, rather than here:
    # if the owner's BYOK key has been revoked since it was stored, the rejected
    # call is retried on the platform key, and that retry needs a model built on
    # the new key (chat/byok_fallback.py). Holding one instance here would retry
    # with the same rejected credential.
    _llm_kwargs: Dict[str, Any] = dict(
        provider=llm_provider,
        model=llm_model,
        temperature=0,
        max_tokens=max_tokens or 1024,
        for_function_calling=False,
    )
    # Records the model instance that actually served the call — the judge call
    # below must reuse it, and after a fallback it is the platform-key one.
    _used: Dict[str, Any] = {}

    messages: List[Any] = [SystemMessage(content=system_content)]

    if past_messages:
        try:
            history = build_history_messages(
                past_messages,
                limit=setup_env.RAG_SESSION_HISTORY_TURNS * 2,
                skip_empty=True,
            )
            if history:
                messages.extend(history)
        except Exception as e:
            logger.debug(f"[answer_node] Could not attach session history: {e}")

    messages.append(HumanMessage(content=query))

    usage_metrics = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    invoke_config: RunnableConfig = {
        "run_name": "rag_answer_generation",
        "tags": ["rag_sub_agent", "rag_answer_node"],
        "metadata": {
            "node": "rag_answer_node",
            "provider": llm_provider,
            "model": llm_model,
        },
    }

    answer_text = ""
    is_answered = False

    if token_queue is not None:
        # ── Live-streaming path ───────────────────────────────────────────────
        # rag_node decided this RAG answer is the SOLE user-facing answer this
        # turn, so we stream it token-by-token into the queue (drained by
        # run_agent). We use a PLAIN text stream (NOT with_structured_output):
        # streaming structured output yields partial JSON, not clean prose. That
        # means we can't get RagAnswerOutput.is_answered inline with the answer
        # itself — instead, once streaming finishes, _judge_is_answered() below
        # makes a second minimal structured-output call so the verdict is still
        # authored by the answer LLM, not borrowed from the retrieval agent's
        # stop_reason. This verdict is NOT just debug — rag_node surfaces it as
        # the RAG service's answer verdict that join_results aggregates, so a
        # "not found" answer must not read as answered merely because it
        # produced (non-empty) text.
        # stream_usage is already set on the ChatOpenAI instance at construction
        # time (chat/llm_factory.py), so it doesn't need to be passed here too —
        # passing it again as a call-time kwarg bypasses langchain_openai's
        # _should_stream_usage translation and leaks straight into the raw
        # openai.AsyncCompletions.create() payload, which openai>=2.x rejects.
        astream_kwargs: Dict[str, Any] = {"config": invoke_config}
        answer_parts: List[str] = []
        final_chunk = None

        def _stream(llm):
            _used["llm"] = llm
            return llm.astream(messages, **astream_kwargs)

        try:
            # A dead BYOK key is rejected on the opening request, before any token
            # exists, so the stream can be restarted on the platform key without the
            # user seeing a partial answer. astream_with_key_fallback enforces that:
            # once a chunk has been yielded it re-raises instead of retrying.
            async for _kind, _item in astream_with_key_fallback(
                _stream, owner_id=owner_id, knowledge_id=knowledge_id,
                source="rag_answer_stream", **_llm_kwargs,
            ):
                if _kind == "outcome":
                    break
                chunk = _item
                try:
                    final_chunk = chunk if final_chunk is None else (final_chunk + chunk)
                except Exception:
                    final_chunk = chunk  # non-addable provider chunk — keep last for usage
                content = _stringify_content(getattr(chunk, "content", "") or "")
                if content:
                    answer_parts.append(content)
                    token_queue.put_nowait(content)
        except ByokKeyRejected as e:
            # Preserve the actionable message and code rather than the generic
            # str(e) below — this is the caller's cue to tell the user their key
            # needs fixing, not just that "something went wrong". Returned, not
            # raised: generate_rag_answer's contract (both call sites,
            # chat/rag_node.py and chat/rag_agent/agent_execution.py) is to never
            # raise, always hand back a dict with "error" set.
            logger.warning(f"[answer_node] BYOK key rejected, no fallback available: {e}")
            return {
                "answer": "", "is_answered": False, "usage_metrics": usage_metrics,
                "error": e.user_message, "error_code": "byok_key_rejected",
            }
        except Exception as e:
            logger.error(f"[answer_node] streaming LLM invocation failed: {e}", exc_info=True)
            return {
                "answer": "",
                "is_answered": False,
                "usage_metrics": usage_metrics,
                "error": str(e),
            }
        llm = _used.get("llm")
        answer_text = "".join(answer_parts).strip()
        # Ask the SAME answer LLM to self-judge is_answered in a second,
        # minimal structured-output call — the streamed text is plain prose
        # (structured output can't stream cleanly), so this is the only way
        # to get the verdict authored by the answer LLM itself rather than
        # borrowed from the retrieval agent's stop_reason. Empty answer_text
        # skips the call entirely (nothing to judge).
        if answer_text and llm is not None:
            is_answered = await _judge_is_answered(
                llm=llm,
                system_content=system_content,
                query=query,
                answer_text=answer_text,
                invoke_config=invoke_config,
            )
        else:
            is_answered = False

        usage_data = getattr(final_chunk, "usage_metadata", None) if final_chunk is not None else None
        if isinstance(usage_data, dict):
            usage_metrics["input_tokens"] = (
                usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0)) or 0
            )
            usage_metrics["output_tokens"] = (
                usage_data.get("output_tokens", usage_data.get("completion_tokens", 0)) or 0
            )
            usage_metrics["total_tokens"] = usage_data.get("total_tokens", 0) or 0
            if usage_metrics["total_tokens"] == 0:
                usage_metrics["total_tokens"] = (
                    usage_metrics["input_tokens"] + usage_metrics["output_tokens"]
                )
            _cached, _write = extract_cache_tokens(usage_data)
            usage_metrics["cached_input_tokens"] = _cached
            usage_metrics["cache_write_tokens"] = _write
    else:
        # ── Blocking structured-output path (unchanged) ───────────────────────
        # Bind structured output so the LLM emits both `answer` and `is_answered`
        # in one call. `include_raw=True` preserves access to the underlying
        # AIMessage (for usage_metadata) AND to any parsing error so we can fall
        # back to the raw text path if structured-output parsing fails.
        async def _invoke(llm):
            _used["llm"] = llm
            structured_llm = llm.with_structured_output(
                RagAnswerOutput, include_raw=True
            )
            return await structured_llm.ainvoke(messages, config=invoke_config)

        try:
            _outcome = await run_with_key_fallback(
                _invoke, owner_id=owner_id, knowledge_id=knowledge_id,
                source="rag_answer", **_llm_kwargs,
            )
            response = _outcome.value
        except ByokKeyRejected as e:
            logger.warning(f"[answer_node] BYOK key rejected, no fallback available: {e}")
            return {
                "answer": "", "is_answered": False, "usage_metrics": usage_metrics,
                "error": e.user_message, "error_code": "byok_key_rejected",
            }
        except Exception as e:
            logger.error(f"[answer_node] LLM invocation failed: {e}", exc_info=True)
            return {
                "answer": "",
                "is_answered": False,
                "usage_metrics": usage_metrics,
                "error": str(e),
            }

        # include_raw=True wraps the result as {"raw": AIMessage, "parsed": Model,
        # "parsing_error": Optional[Exception]}.
        raw_msg = response.get("raw") if isinstance(response, dict) else None
        parsed = response.get("parsed") if isinstance(response, dict) else None
        parsing_error = response.get("parsing_error") if isinstance(response, dict) else None

        if parsed is not None and not parsing_error:
            answer_text = parsed.answer or ""
            is_answered = bool(parsed.is_answered)
        else:
            # Structured-output parse failed — fall back to raw text content so
            # we don't drop the answer entirely. is_answered defaults to False
            # under uncertainty.
            logger.warning(
                f"[answer_node] structured output parse failed ({parsing_error}); "
                f"falling back to raw text"
            )
            if raw_msg is not None and hasattr(raw_msg, "content"):
                answer_text = _stringify_content(raw_msg.content)
            is_answered = bool(answer_text.strip())

        usage_data = getattr(raw_msg, "usage_metadata", None) if raw_msg is not None else None
        if isinstance(usage_data, dict):
            usage_metrics["input_tokens"] = (
                usage_data.get("input_tokens", usage_data.get("prompt_tokens", 0)) or 0
            )
            usage_metrics["output_tokens"] = (
                usage_data.get("output_tokens", usage_data.get("completion_tokens", 0)) or 0
            )
            usage_metrics["total_tokens"] = usage_data.get("total_tokens", 0) or 0
            if usage_metrics["total_tokens"] == 0:
                usage_metrics["total_tokens"] = (
                    usage_metrics["input_tokens"] + usage_metrics["output_tokens"]
                )
            _cached, _write = extract_cache_tokens(usage_data)
            usage_metrics["cached_input_tokens"] = _cached
            usage_metrics["cache_write_tokens"] = _write

    # Append citations from structured handoff (if any) AFTER the answer text.
    # In the streaming path the live tokens were already sent, so we also push the
    # citations block to the queue to keep the displayed text == the stored answer.
    # Rare path (agent emitted citations).
    final_text = answer_text.strip()
    agent_output = retrieved_context.get("agent_output") or {}
    citations_block = _format_citations_section(agent_output.get("citations") or [])
    if citations_block:
        final_text = final_text + "\n" + citations_block
        if token_queue is not None:
            token_queue.put_nowait("\n" + citations_block)

    logger.info(
        f"[answer_node] {'Streamed' if token_queue is not None else 'Generated'} answer "
        f"({len(final_text)} chars, is_answered={is_answered}), "
        f"tokens={usage_metrics['total_tokens']}"
    )

    return {
        "answer": final_text.strip(),
        "is_answered": is_answered,
        "usage_metrics": usage_metrics,
    }
