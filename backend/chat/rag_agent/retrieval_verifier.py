"""
Retrieval verifier — CRAG-style quality gate between retrieval and answer
generation.

After the retrieval ReAct agent finishes its loop, this node runs a small
LLM call that judges: *is the retrieved evidence sufficient to answer the
user's question correctly and completely?*

When the verdict is **sufficient**, the answer node proceeds as normal.
When **insufficient** AND a retry budget remains, the orchestrator feeds the
verdict back into the retrieval agent as a HumanMessage and re-invokes it,
giving the agent a directed re-plan signal. When the budget is exhausted,
the answer node still runs but the verdict is surfaced so logs / traces
show the gap.

The verifier is **bias-toward-sufficient**: it only flags insufficient when
there's a clear gap — empty results, topical mismatch, an entity that the
refiner extracted but no tagged doc covers, an in-flight warning from the
retrieval tools. This keeps cost bounded and avoids ping-pong loops.

Compact context: the verifier does NOT see the full evidence block. It sees
the user's question, a per-doc title + summary preview, any catalog-value
enumerations, and any guardrail warnings from tool responses. This is
enough for topical / coverage / warning checks at a fraction of the token
cost of the answer node.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langsmith import traceable
from pydantic import BaseModel, Field

from chat.byok_fallback import run_with_key_fallback
from utils.logger import logger
from utils.tracing import track_time


_MAX_DOCS_IN_PREVIEW = 25
_MAX_SUMMARY_PREVIEW_CHARS = 400
_MAX_CATALOG_VALUES_PER_FIELD = 30
_MAX_CHUNKS_IN_PREVIEW = 10
_MAX_CHUNK_PREVIEW_CHARS = 600

# Hardcoded default prompt for this node — no Agent-Registry dependency in
# this service. Formerly resolved via get_base_prompt("retrieval_verifier")
# with this exact string as the config-miss fallback; the fallback is now the
# only prompt.
_VERIFIER_DEFAULT_PROMPT = (
    "You are the retrieval verifier in a RAG system. Your job: judge "
    "whether the retrieved evidence is sufficient to answer the user's "
    "question correctly and completely. Output a structured verdict. "
    "Bias toward sufficient=true when coverage is reasonable; only flag "
    "insufficient when there is a clear gap (empty results, topical "
    "mismatch, missing entity coverage, in-flight guardrail warning)."
)


class VerifierResult(BaseModel):
    """Structured output of the retrieval verifier."""

    sufficient: bool = Field(
        description=(
            "True if the retrieved evidence is enough to answer the user's "
            "question correctly and completely. False if there is a clear gap "
            "(empty results, topical mismatch, missing entity coverage, "
            "in-flight guardrail warning)."
        )
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="How confident you are in the sufficiency judgment."
    )
    missing: str = Field(
        default="",
        description=(
            "Short description of what's missing when insufficient (e.g. "
            "'no docs about the Memory Service', 'population truncated at 20', "
            "'owner attribute not in any doc's metadata'). Empty when sufficient."
        ),
    )
    reason: str = Field(
        description="One-sentence rationale for the sufficiency verdict."
    )
    suggested_action: str = Field(
        default="",
        description=(
            "Concrete next action for the retrieval agent when insufficient — "
            "e.g. 'call resolve_filter_value on clickup_doc_name and rebuild "
            "the filter', 'open chunks of doc IDs [...]', 'broaden filter to "
            "include synonyms X, Y'. Empty when sufficient."
        ),
    )


def _format_docs_preview(summaries: Dict[str, Any]) -> str:
    """Render a compact per-doc preview for the verifier.

    Includes title + (truncated) summary + METADATA KEY NAMES present on the
    doc. Surfacing the key names matters for shapes like ``aggregate`` /
    ``predicate`` where the answer needs to group-by or filter on a field —
    without the keys, the verifier can't tell whether the data is even
    available (e.g. when the caller passed ``include_summary=False`` and the
    doc cards look empty even though `metadata.current_role` is populated).
    We deliberately show only the key NAMES, not values: enough to judge
    sufficiency, cheap on tokens, no risk of polluting the verifier with raw
    metadata.

    A trailing "fields seen across the retrieved set" aggregate line gives
    the verifier the union of metadata keys with their doc-coverage count.
    """
    if not summaries:
        return "(no documents retrieved)"
    lines: List[str] = []
    field_coverage: Dict[str, int] = {}
    for idx, (object_id, data) in enumerate(summaries.items(), start=1):
        if not isinstance(data, dict):
            data = {"summary": str(data)}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        # Count coverage across ALL docs, even the ones omitted from preview.
        for key in metadata.keys():
            field_coverage[key] = field_coverage.get(key, 0) + 1

        if idx > _MAX_DOCS_IN_PREVIEW:
            # Still keep counting field coverage, but stop emitting per-doc
            # lines after the preview cap.
            continue

        title = data.get("title") or data.get("file_name") or f"Document {idx}"
        summary = (data.get("summary") or "").strip()
        if len(summary) > _MAX_SUMMARY_PREVIEW_CHARS:
            summary = summary[: _MAX_SUMMARY_PREVIEW_CHARS - 1].rstrip() + "…"
        lines.append(f"[{idx}] {title}")
        meta_keys = sorted(metadata.keys()) if metadata else []
        if meta_keys:
            lines.append(f"    metadata keys: {', '.join(meta_keys)}")
        if summary:
            lines.append(f"    summary: {summary}")

    if len(summaries) > _MAX_DOCS_IN_PREVIEW:
        lines.append(f"… (+{len(summaries) - _MAX_DOCS_IN_PREVIEW} more docs omitted from preview)")

    if field_coverage:
        coverage_sorted = sorted(field_coverage.items(), key=lambda kv: -kv[1])
        coverage_str = ", ".join(f"{k} ({c}/{len(summaries)})" for k, c in coverage_sorted)
        lines.append("")
        lines.append(
            f"**Metadata fields present across the retrieved set "
            f"(field → doc coverage):** {coverage_str}"
        )

    return "\n".join(lines)


def _format_chunks_preview(
    chunks: List[Any],
    summaries: Dict[str, Any],
) -> str:
    """Render chunk excerpts for the verifier.

    For ``fact_lookup`` / ``deep_read`` / ``multi_doc_synthesis`` queries,
    the answer often lives in chunk text — NOT in document summary. Hiding
    chunks from the verifier makes it blind to those shapes' main evidence
    path: 0 docs + 10 chunks-with-the-answer would render as "(no documents
    retrieved)" and trigger an `insufficient` verdict even when the answer
    is fully there.

    We render the top N chunks with: chunk index, parent doc title, score
    (when present), and a ~600-char text excerpt. That's enough for the
    verifier to detect "yes, the requested attribute is in chunk text" or
    "no, none of the chunks mention X" without paying the answer-node's
    full token cost.
    """
    if not chunks:
        return "(no chunk excerpts retrieved)"

    title_by_doc_id: Dict[str, str] = {}
    for doc_id, data in summaries.items():
        if isinstance(data, dict):
            title_by_doc_id[doc_id] = (
                data.get("title") or data.get("file_name") or doc_id
            )

    lines: List[str] = []
    rendered = 0
    for chunk in chunks:
        if rendered >= _MAX_CHUNKS_IN_PREVIEW:
            lines.append(f"… (+{len(chunks) - rendered} more chunks omitted)")
            break
        if not isinstance(chunk, dict):
            continue
        text = chunk.get("chunk_text") or chunk.get("text") or chunk.get("content") or ""
        if not isinstance(text, str) or not text.strip():
            continue

        doc_id = chunk.get("document_id") or chunk.get("object_id") or ""
        parent = title_by_doc_id.get(doc_id) or chunk.get("clickup_page_name") or "unknown doc"
        score = chunk.get("relevance_score") or chunk.get("score")

        rendered += 1
        header = f"[Chunk {rendered}] from: {parent}"
        if isinstance(score, (int, float)):
            header += f" · score={round(score, 3)}"
        lines.append(header)

        excerpt = text.strip()
        if len(excerpt) > _MAX_CHUNK_PREVIEW_CHARS:
            excerpt = excerpt[: _MAX_CHUNK_PREVIEW_CHARS - 1].rstrip() + "…"
        lines.append(f"    {excerpt}")

    if not lines:
        return "(no chunk excerpts retrieved)"
    return "\n".join(lines)


def _format_catalog_values_preview(metadata_values: Dict[str, Any]) -> str:
    if not metadata_values or not isinstance(metadata_values, dict):
        return ""
    lines: List[str] = []
    for field_name, values in metadata_values.items():
        if not isinstance(values, list):
            continue
        truncated = values[:_MAX_CATALOG_VALUES_PER_FIELD]
        suffix = f" (+{len(values) - len(truncated)} more)" if len(values) > len(truncated) else ""
        lines.append(f"- {field_name} ({len(values)} total{suffix.strip(' ()')}): {truncated}")
    if not lines:
        return ""
    return "Catalog value enumerations available:\n" + "\n".join(lines)


def _collect_tool_warnings(tool_responses: List[Dict[str, Any]]) -> List[str]:
    """Pull any soft guardrail warnings that fired during retrieval.

    These are the strongest single signal that retrieval went wrong —
    surface them to the verifier verbatim so the verdict can cite them.
    """
    warnings: List[str] = []
    if not tool_responses:
        return warnings

    warning_keys = (
        "metadata_lookup_over_retrieval_warning",
    )
    for record in tool_responses:
        if not isinstance(record, dict):
            continue
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        for key in warning_keys:
            if result.get(key):
                msg = result.get("warning") or f"{key} fired"
                tool_name = record.get("tool_name") or "unknown"
                warnings.append(f"[{tool_name}] {key}: {msg[:300]}")
                break
    return warnings


def _build_verifier_context(
    query: str,
    retrieved_context: Dict[str, Any],
    messages: Optional[List[Any]] = None,
) -> str:
    """Build a compact evidence preview for the verifier — titles + summary
    excerpts only, plus catalog values and any guardrail warnings.

    Deliberately omits chunk text and full metadata blobs — the verifier
    judges topical match + coverage + warnings, not factual recall.

    When ``messages`` is provided, a per-call "Retrieval attempts" summary
    (shape · query · filter · outcome) is rendered near the top so the
    verifier can judge WHAT was tried — needed to tell a phrasing-miss apart
    from a wrong-filter, and to avoid suggesting a sub-query already fired.
    """
    summaries = retrieved_context.get("summaries") or {}
    chunks = retrieved_context.get("chunks") or []
    metadata_values = retrieved_context.get("metadata_values") or {}
    tool_responses = retrieved_context.get("tool_responses") or []
    agent_output = retrieved_context.get("agent_output") or {}

    # Align the verifier's evidence view with what the ANSWER NODE will actually
    # ground on. The answer node now keeps only the docs/chunks the agent cited
    # (relevant_docs); if the verifier kept judging the FULL retrieval trail it
    # could pass on a doc the answer node then drops — making "sufficient" not
    # predict answer quality. Same citation filter, same helpers, so the two
    # stay in lockstep. Empty citations → judge everything (fallback).
    from chat.rag_agent.answer_node import (
        _cited_ids_from_agent_output,
        _chunk_matches_citation,
    )
    _cited_doc_ids, _cited_chunk_ids = _cited_ids_from_agent_output(agent_output)
    if _cited_doc_ids:
        _c_summaries = {
            oid: d for oid, d in summaries.items() if str(oid) in _cited_doc_ids
        }
        _c_chunks = [
            c for c in chunks if _chunk_matches_citation(c, _cited_doc_ids, _cited_chunk_ids)
        ]
        if _c_summaries or _c_chunks:
            summaries = _c_summaries
            chunks = _c_chunks

    sections: List[str] = [f"User question: {query}"]

    # Surface the agent's structured self-report near the top so the LLM
    # verifier weighs it alongside the raw evidence preview. The verifier
    # always runs its own LLM judgment — the agent's self-reported confidence
    # is evidence, not a gate.
    if agent_output:
        sr_open = agent_output.get("open_questions") or []
        sr_answer = (agent_output.get("answer") or "").strip()
        sr_docs = agent_output.get("relevant_docs") or []
        lines = [
            "Agent self-report:",
            f"  stop_reason={agent_output.get('stop_reason')!r}",
            f"  confidence={agent_output.get('confidence')!r}",
            f"  open_questions={sr_open}",
        ]
        if sr_answer:
            lines.append(f"  answer: {sr_answer}")
        if sr_docs:
            lines.append(f"  relevant_docs ({len(sr_docs)}):")
            for doc in sr_docs:
                if isinstance(doc, dict):
                    lines.append(f"    - [{doc.get('doc_id', '?')}] {doc.get('why', '')}")
        sections.append("\n".join(lines))

    sections.append("")
    sections.append(
        f"Retrieval summary: {len(summaries)} document(s), {len(chunks)} chunk(s)."
    )

    # Per-call attempts (shape · query · filter · outcome) — lets the verifier
    # reason about WHAT was tried, not just what came back. Reuses the same
    # deterministic walk that feeds the agent's replan message.
    if messages:
        attempts = _summarize_prior_tool_calls(messages)
        if attempts:
            sections.append("")
            sections.append(
                "Retrieval attempts this turn (tool · args incl. shape/query/filter → outcome) — "
                "use this to judge WHY a gap exists and to avoid suggesting a query/filter already tried:"
            )
            sections.extend(attempts)

    warnings = _collect_tool_warnings(tool_responses)
    if warnings:
        sections.append("")
        sections.append("GUARDRAIL WARNINGS fired during retrieval:")
        for w in warnings:
            sections.append(f"- {w}")

    catalog_preview = _format_catalog_values_preview(metadata_values)
    if catalog_preview:
        sections.append("")
        sections.append(catalog_preview)

    sections.append("")
    sections.append("Retrieved documents (title + summary excerpt):")
    sections.append(_format_docs_preview(summaries))

    # Chunks are critical evidence for fact_lookup / deep_read /
    # multi_doc_synthesis — the requested attribute (URL, owner, date, …)
    # commonly lives in chunk text and is NOT mirrored in the document
    # summary. Showing chunks to the verifier lets it judge "yes, the
    # attribute is in the retrieved evidence" instead of declaring the
    # retrieval insufficient just because the docs block was empty.
    sections.append("")
    sections.append(
        "Retrieved chunk excerpts (often where the answer actually lives "
        "for fact_lookup / deep_read / multi_doc_synthesis):"
    )
    sections.append(_format_chunks_preview(chunks, summaries))

    prior_turn_docs = retrieved_context.get("prior_turn_docs") or []
    if prior_turn_docs:
        sections.append("")
        sections.append(
            f"Prior-turn evidence ({len(prior_turn_docs)} doc(s) already retrieved "
            f"earlier in this conversation — may cover the question without fresh retrieval):"
        )
        for doc in prior_turn_docs:
            oid = doc.get("object_id", "?")
            summary = (doc.get("summary") or "").strip().replace("\n", " ")
            file_name = doc.get("file_name", "")
            suffix = f"  ({file_name})" if file_name else ""
            sections.append(f"  - [{oid}] {summary}{suffix}")

    return "\n".join(sections)


def _trivially_insufficient(retrieved_context: Dict[str, Any]) -> Optional[VerifierResult]:
    """Cheap-path early-out — when retrieval is patently empty we don't need
    an LLM call to know it's insufficient.
    """
    summaries = retrieved_context.get("summaries") or {}
    chunks = retrieved_context.get("chunks") or []
    metadata_values = retrieved_context.get("metadata_values") or {}
    prior_turn_docs = retrieved_context.get("prior_turn_docs") or []
    if summaries or chunks or metadata_values or prior_turn_docs:
        return None
    # A follow-up turn can answer from session history / prior-turn reuse with
    # ZERO fresh tool calls — so this turn's extracted evidence is empty even
    # though the agent grounded its answer. Don't declare "zero evidence" over
    # the agent's own high-confidence, doc-cited answer; let the LLM verifier judge.
    agent_output = retrieved_context.get("agent_output") or {}
    if (
        agent_output.get("stop_reason") == "answered"
        and agent_output.get("confidence") == "high"
        and (agent_output.get("relevant_docs") or agent_output.get("citations"))
    ):
        return None
    return VerifierResult(
        sufficient=False,
        confidence="high",
        missing="Retrieval returned zero documents, zero chunks, zero catalog values, and no prior-turn evidence.",
        reason="No evidence was retrieved at all — the answer node cannot ground an answer in the knowledge base.",
        suggested_action=(
            "Re-plan from the user's question: broaden the filter (drop one constraint), "
            "try semantic mode with the bare query, or call retrieve_metadata_catalog to "
            "confirm the schema covers what the user is asking about."
        ),
    )


def _global_chunk_call_made(messages: Optional[List[Any]]) -> bool:
    """True if any `retrieve_chunks` call this turn was GLOBAL (Rung 3) —
    i.e. had a `query` but NO `object_ids`. That's the last rung of the
    escalation ladder; its presence is the signal the agent descended past
    scoped chunks before stopping.

    Deterministic walk of the AIMessage tool_calls — no LLM, no artifact
    needed (we judge on the call ARGS, not the result).
    """
    if not messages:
        return False
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            if tc.get("name") != "retrieve_chunks":
                continue
            args = tc.get("args") or {}
            object_ids = args.get("object_ids")
            has_query = bool(args.get("query"))
            # Global = a query-driven chunk search with no object_ids scope.
            if has_query and not object_ids:
                return True
    return False


def _ladder_gate(
    retrieved_context: Dict[str, Any],
    messages: Optional[List[Any]],
) -> Optional[VerifierResult]:
    """Deterministic escalation-ladder gate (relevance-based Rung 3 trigger).

    Forces ONE global chunk pass (Rung 3) whenever a CHUNK-level query stopped
    short of it. The query needs the chunk layer — the agent self-reported
    ``granularity == 'chunk'`` (a specific-detail / named-entity-detail question
    per Core Principle #3); a ``doc`` (topic-level) query answered at medium
    confidence is a legitimate terminal state and is NOT forced down.

    For a chunk query, these stops are all illegitimate while Rung 3 is untried:
    - ``answered`` below ``high`` confidence — scoped chunks (Rung 2) didn't
      squarely answer it (a weak Rung 2 by definition).
    - ``not_found`` / ``budget_exhausted`` — the agent gave up mid-ladder; a
      chunk query cannot be "exhausted" while global chunks were never tried.
      (This is the case the gate previously missed: a chunk-level ``not_found``
      reached after only Rung 1 + Rung 2 — see the QA-agent-name regression.)

    Only a high-confidence ``answered`` is a license to stop before Rung 3.
    When the stop is illegitimate AND no global chunk call was made yet, the
    ladder isn't exhausted → return an ``insufficient`` verdict to force one
    more pass. Otherwise return None (defer to the LLM verifier). Cheap,
    deterministic, runs before the LLM call.
    """
    agent_output = retrieved_context.get("agent_output") or {}
    stop_reason = agent_output.get("stop_reason")
    confidence = agent_output.get("confidence")
    granularity = agent_output.get("granularity")

    # Gate any non-terminal-by-design outcome on a CHUNK-level query that never
    # reached Rung 3. `answered` below high confidence, AND `not_found` /
    # `budget_exhausted` — all three are illegitimate on a chunk query while the
    # global chunk pass is still untried (the agent gave up mid-ladder). Only a
    # high-confidence `answered` means the ladder did its job; a doc-level query
    # never needed chunks; refused / needs_clarification are handled upstream.
    if stop_reason not in ("answered", "not_found", "budget_exhausted"):
        return None
    if granularity != "chunk":
        return None
    # A high-confidence answer is the one license to stop before Rung 3.
    # not_found / budget_exhausted are NEVER a license — a chunk query cannot be
    # "exhausted" while global chunks remain untried, regardless of confidence.
    if stop_reason == "answered" and confidence == "high":
        return None
    if _global_chunk_call_made(messages):
        # Ladder already walked to Rung 3 — medium/low is now a legitimate
        # terminal state; let the LLM verifier make the final call.
        return None

    logger.info(
        "[verifier] ladder gate tripped — chunk-level query stopped with "
        "stop_reason=%r confidence=%r without a global chunk call; forcing Rung 3.",
        stop_reason,
        confidence,
    )
    return VerifierResult(
        sufficient=False,
        confidence="high",
        missing=(
            "Scoped chunks did not squarely answer this chunk-level query, "
            "and no global chunk search (Rung 3) was tried."
        ),
        reason=(
            f"Chunk-level query stopped at stop_reason={stop_reason!r} "
            f"confidence={confidence!r} after scoped chunks only — the "
            "escalation ladder requires a global chunk search before stopping "
            "(a chunk query is not exhausted while Rung 3 is untried)."
        ),
        suggested_action=(
            "Fire ONE global chunk search: retrieve_chunks(query=<the precise "
            "thing asked>) with NO object_ids. This reaches the body of docs "
            "that never surfaced in retrieve_documents."
        ),
    )


def _build_system_prompt() -> str:
    return _VERIFIER_DEFAULT_PROMPT


@track_time("RAG: Retrieval Verifier")
@traceable(name="retrieval_verifier", run_type="chain")
async def verify_retrieval(
    query: str,
    retrieved_context: Dict[str, Any],
    llm_provider: str = "openai",
    llm_model: str = "gpt-4o-mini",
    owner_id: Optional[str] = None,
    messages: Optional[List[Any]] = None,
    knowledge_id: Optional[str] = None,
) -> VerifierResult:
    """Run the CRAG-style sufficiency check.

    Returns a ``VerifierResult``; never raises — on internal failure, returns
    a permissive ``sufficient=True, confidence='low'`` verdict so the
    pipeline doesn't ping-pong because the verifier itself broke.
    """
    # ── Agent self-report — terminal states only ──────────────────────────
    # For states where a retry genuinely cannot help (refused, needs_clarification),
    # short-circuit without an LLM call. All other states (answered, not_found,
    # budget_exhausted, or missing self-report) fall through to the full LLM
    # verifier so it independently judges sufficiency.
    agent_output = retrieved_context.get("agent_output") or {}
    stop_reason = agent_output.get("stop_reason")
    confidence = agent_output.get("confidence")
    open_questions = agent_output.get("open_questions") or []

    if stop_reason in ("needs_clarification", "refused"):
        return VerifierResult(
            sufficient=True,
            confidence="high",
            missing="",
            reason=f"agent self-reported stop_reason={stop_reason}; no retry would help",
            suggested_action="",
        )

    # Cheap path: empty retrieval → trivially insufficient, no LLM call.
    early = _trivially_insufficient(retrieved_context)
    if early is not None:
        logger.info(
            "[verifier] trivially insufficient — no evidence retrieved, skipping LLM call"
        )
        return early

    # Cheap path (inverse): strong self-citation → trivially SUFFICIENT, no LLM
    # call. When the agent confidently answered AND named the docs it grounded
    # on, there is no gap for the verifier to find — and relevant_docs is the
    # exact set the answer node now uses, so a high-confidence cited answer is
    # precisely what we want to wave through. The inverse case (answered with NO
    # citations) deliberately falls through to the full LLM as a suspicion check.
    relevant_docs = agent_output.get("relevant_docs") or []
    if stop_reason == "answered" and confidence == "high" and relevant_docs:
        logger.info(
            f"[verifier] trivially sufficient — agent answered at high confidence "
            f"with {len(relevant_docs)} cited doc(s), skipping LLM call"
        )
        return VerifierResult(
            sufficient=True,
            confidence="high",
            missing="",
            reason=(
                f"agent self-reported stop_reason=answered, confidence=high, with "
                f"{len(relevant_docs)} cited doc(s); no gap to verify"
            ),
            suggested_action="",
        )

    # Deterministic escalation-ladder gate: a content shape answered below
    # high confidence without a global chunk call means Rung 3 was skipped.
    # Force the global pass before the answer node runs — no LLM call needed.
    ladder = _ladder_gate(retrieved_context, messages)
    if ladder is not None:
        return ladder

    system_prompt = _build_system_prompt()
    user_content = _build_verifier_context(query, retrieved_context, messages=messages)

    # This node does NOT bind any retrieval tools to the LLM — it only calls
    # with_structured_output(VerifierResult), a single-purpose sufficiency
    # judgment run AFTER retrieval is already done, same shape as answer_node's
    # non-streaming path. for_function_calling=False so GPT-5 keeps
    # reasoning_effort (no function-tools + reasoning_effort 400 concern here
    # — see chat/openai_reasoning.py, which gates on function TOOLS being in
    # the request, not on with_structured_output's internal implementation).
    invoke_config: RunnableConfig = {
        "run_name": "retrieval_verifier_llm",
        "tags": ["rag_sub_agent", "retrieval_verifier"],
        "metadata": {
            "node": "retrieval_verifier",
            "provider": llm_provider,
            "model": llm_model,
        },
    }

    verifier_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    # This node does NOT bind any retrieval tools to the LLM — it only calls
    # with_structured_output(VerifierResult), a single-purpose sufficiency
    # judgment run AFTER retrieval is already done, same shape as answer_node's
    # non-streaming path. for_function_calling=False so GPT-5 keeps
    # reasoning_effort (no function-tools + reasoning_effort 400 concern here
    # — see chat/openai_reasoning.py, which gates on function TOOLS being in
    # the request, not on with_structured_output's internal implementation).
    #
    # Built inside the callable so a retry after a rejected BYOK key rebuilds the
    # structured wrapper on the platform-key model.
    async def _invoke(llm):
        structured_llm = llm.with_structured_output(VerifierResult)
        return await structured_llm.ainvoke(verifier_messages, config=invoke_config)

    try:
        outcome = await run_with_key_fallback(
            _invoke, owner_id=owner_id, knowledge_id=knowledge_id, source="retrieval_verifier",
            provider=llm_provider, model=llm_model, temperature=0, for_function_calling=False,
        )
        verdict = outcome.value
    except Exception as e:
        # Fail-open on everything, including an unsupported-structured-output
        # provider (previously handled by its own try/except around
        # with_structured_output) and a BYOK rejection we could not cover for. The
        # verifier's whole job is deciding whether to retry retrieval; defaulting
        # to "sufficient" costs one un-verified answer, whereas raising here would
        # lose the answer entirely.
        logger.error(f"[verifier] LLM invocation failed: {e}", exc_info=True)
        return VerifierResult(
            sufficient=True,
            confidence="low",
            reason=f"Verifier LLM call failed ({type(e).__name__}); defaulting to sufficient.",
        )

    if not isinstance(verdict, VerifierResult):
        # with_structured_output should always return the right type, but be
        # defensive about it.
        try:
            verdict = VerifierResult.model_validate(verdict)
        except Exception:
            logger.warning(
                f"[verifier] Got unexpected output type {type(verdict)}; defaulting to sufficient"
            )
            return VerifierResult(
                sufficient=True,
                confidence="low",
                reason="Verifier returned unexpected output shape — defaulting to sufficient.",
            )

    logger.info(
        f"[verifier] verdict — sufficient={verdict.sufficient} "
        f"confidence={verdict.confidence} reason={verdict.reason!r}"
    )
    return verdict


_MAX_TOOL_ARGS_PREVIEW_CHARS = 180


def _summarize_prior_tool_calls(messages: List[Any]) -> List[str]:
    """Walk the message thread and produce a concise per-call summary.

    Each entry is one line like:
        - retrieve_documents({"metadata_filter": {...}, "query": "..."}) → 0 docs

    Used by ``format_replan_message`` to tell the agent on its retry pass
    EXACTLY what was already tried — so it doesn't re-invent the same tool
    call with the same args and burn another iteration. Deterministic walk
    of the existing thread; no LLM call.
    """
    if not messages:
        return []

    # Collect (call_id → args) from AIMessage tool_calls
    call_args: Dict[str, Dict[str, Any]] = {}
    call_name: Dict[str, str] = {}
    for msg in messages:
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            cid = tc.get("id")
            if not cid:
                continue
            call_args[cid] = tc.get("args") or {}
            call_name[cid] = tc.get("name") or "unknown"

    # Match each ToolMessage back to its call, summarize the outcome
    summaries: List[str] = []
    for msg in messages:
        tool_call_id = getattr(msg, "tool_call_id", None)
        if not tool_call_id or tool_call_id not in call_args:
            continue
        name = call_name.get(tool_call_id, "unknown")
        args = call_args[tool_call_id]
        # Compact args view — drop knowledge_id (always present, never informative
        # for the agent's re-plan) and truncate the rest.
        view_args = {k: v for k, v in args.items() if k != "knowledge_id"}
        try:
            args_blob = json.dumps(view_args, ensure_ascii=False, default=str)
        except Exception:
            args_blob = str(view_args)
        if len(args_blob) > _MAX_TOOL_ARGS_PREVIEW_CHARS:
            args_blob = args_blob[: _MAX_TOOL_ARGS_PREVIEW_CHARS - 1].rstrip() + "…"

        # Outcome — pull a tiny shape hint from the artifact / content
        outcome = _summarize_tool_outcome(msg)
        summaries.append(f"- `{name}({args_blob})` → {outcome}")

    return summaries


def _summarize_tool_outcome(msg: Any) -> str:
    """One-clause outcome summary for a ToolMessage (count of docs/chunks,
    error, warning, etc.). Deterministic, no LLM.
    """
    artifact = getattr(msg, "artifact", None)
    result: Any = artifact
    if result is None:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            try:
                result = json.loads(content)
            except (json.JSONDecodeError, ValueError):
                result = content

    if isinstance(result, dict):
        if result.get("error"):
            return f"ERROR: {str(result['error'])[:100]}"
        if "documents" in result and isinstance(result["documents"], list):
            n = len(result["documents"])
            total = result.get("total_match_count")
            warnings = []
            if result.get("filter_first_warning"):
                warnings.append("filter_first_warning")
            if result.get("metadata_lookup_over_retrieval_warning"):
                warnings.append("metadata_lookup_over_retrieval_warning")
            warn_str = f" [{', '.join(warnings)}]" if warnings else ""
            if total is not None and total != n:
                return f"{n} docs (of {total} total){warn_str}"
            return f"{n} docs{warn_str}"
        if "chunks" in result and isinstance(result["chunks"], list):
            return f"{len(result['chunks'])} chunks"
        if "values" in result and isinstance(result["values"], list):
            return f"{len(result['values'])} catalog values"
        if "fields" in result:
            n = len(result["fields"]) if isinstance(result["fields"], (list, dict)) else "?"
            return f"metadata catalog ({n} fields)"

    if isinstance(result, str):
        return result[:120].rstrip()
    return "(no result)"


def format_replan_message(
    verdict: VerifierResult,
    prior_messages: Optional[List[Any]] = None,
) -> str:
    """Format the verifier's verdict as a HumanMessage for the retrieval
    agent's re-plan turn.

    Includes (when ``prior_messages`` is provided) a per-call summary of
    what the agent ALREADY tried — tool name + args + outcome. That stops
    the common failure mode where the agent re-calls the same tool with
    the same arguments because it doesn't notice the prior attempt in
    its own message thread.
    """
    parts = [
        "RETRIEVAL VERIFIER FEEDBACK — the evidence you retrieved is not yet "
        "sufficient to answer the question. Please re-plan and retrieve what's "
        "missing before stopping.",
        "",
        f"Reason: {verdict.reason}",
    ]
    if verdict.missing:
        parts.append(f"Missing: {verdict.missing}")
    if verdict.suggested_action:
        parts.append(f"Suggested next action: {verdict.suggested_action}")

    # Deterministic ladder-state directive. If the GLOBAL chunk pass (Rung 3)
    # was never fired this turn, it is the untried rung — and for a missing
    # named entity it's the ONLY rung that can help: doc retrieval (Rung 1)
    # missed the entity and scoped chunks (Rung 2) only read the docs Rung 1
    # found, which aren't about the entity. Surface this regardless of what the
    # LLM verifier's suggested_action said (it may steer toward re-querying
    # documents — the weaker move). We don't depend on the suggestion being
    # right; the message corrects itself from the actual tool-call history.
    global_untried = not _global_chunk_call_made(prior_messages)
    if global_untried:
        parts.append("")
        parts.append(
            "LADDER STATE — the GLOBAL chunk pass (Rung 3) has NOT been fired "
            "this turn. That is your untried rung. When the missing thing is a "
            "named entity that never surfaced as a document subject, re-querying "
            "`retrieve_documents` is the WEAK move (it already missed) and scoped "
            "`retrieve_chunks` searched the wrong docs — the entity may be the "
            "subject of a doc that never surfaced. Fire ONE global pass FIRST: "
            "`retrieve_chunks(query=<the precise thing asked>)` with NO `object_ids`."
        )

    if prior_messages:
        prior = _summarize_prior_tool_calls(prior_messages)
        if prior:
            parts.append("")
            parts.append(
                "Tool calls you ALREADY made this turn (do NOT repeat any of "
                "these with identical arguments — that would be a wasted retry):"
            )
            parts.extend(prior)
    parts.append("")
    closing = (
        "Make ONE more targeted retrieval pass. The retry MUST differ from "
        "the prior attempts above in at least one of: (a) the tool you call, "
        "(b) the `metadata_filter` keys/values, or (c) the `query` text. "
        "Calling the SAME tool with the SAME args is a wasted iteration and "
        "will produce the same insufficient verdict."
    )
    if global_untried:
        # Don't invite `not_found` while Rung 3 is still untried — that's the
        # exact forbidden stop. Direct the agent to the global pass instead.
        closing += (
            " You have NOT yet exhausted the ladder — the global chunk pass "
            "above is still untried, so do NOT stop at `not_found` yet; fire it."
        )
    else:
        closing += (
            " If you've exhausted plausible variations (including the global "
            "chunk pass), stop here and the answer node will surface 'not "
            "found' to the user — that's better than a third identical call."
        )
    parts.append(closing)
    return "\n".join(parts)
