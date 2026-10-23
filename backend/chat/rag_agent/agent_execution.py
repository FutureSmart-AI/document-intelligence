"""
RAG agent execution logic.
"""
import json
import time
from typing import Dict, Any, List, Optional
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chat.byok_fallback import (
    CHAT_OPERATION,
    ByokKeyRejected,
    build_warning,
    collect_warning,
)
from chat.llm_factory import normalize_provider_and_model
from chat.rag_agent.agent_creation import create_rag_agent_graph
from utils.usage.credential_health import (
    classify_provider_error,
    is_terminal,
    mark_credential_failed,
    platform_fallback_allowed,
)
from utils.usage.credentials import resolve_owner_id_for_knowledge
from chat.utils.service_calls import build_service_call
from utils.logger import logger
from utils.tracing import track_time


def _chunk_identity(chunk: Dict[str, Any]) -> Optional[str]:
    """Stable identity for a chunk across repeated ``retrieve_chunks`` calls.

    Backend chunks use ``id`` (or sometimes ``chunk_id``); fall back to
    ``(document_id, chunk_idx)`` or a hash of the raw text. Returns ``None``
    when no identity can be derived — caller should drop or de-prioritize.
    """
    cid = chunk.get("chunk_id") or chunk.get("id")
    if cid:
        return str(cid)
    doc_ref = chunk.get("document_id") or chunk.get("object_id")
    idx = chunk.get("chunk_idx")
    if doc_ref is not None and idx is not None:
        return f"{doc_ref}::{idx}"
    text = chunk.get("chunk_text") or chunk.get("text") or chunk.get("content")
    if text:
        return f"text::{hash(text)}"
    return None


def _render_doc_metadata(metadata: Dict[str, Any]) -> str:
    """Render a doc's stored metadata as a full inline string.

    Nested dicts/lists are surfaced (a follow-up may need ``experience[].role``
    or ``specs.cpu``), flattened into dotted keys. No truncation: scalars,
    recursion depth, and total length are all rendered in full. Returns ``""``
    when nothing renderable remains.

    Flattening examples::

        {"specs": {"cpu": "M3"}}              -> specs.cpu=M3
        {"experience": [{"role": "lead"}]}    -> experience[0].role=lead
        {"tags": ["a", "b"]}                  -> tags=[a, b]
    """
    if not isinstance(metadata, dict) or not metadata:
        return ""

    parts: List[str] = []

    def _scalar(value: Any) -> str:
        return str(value).strip().replace("\n", " ")

    def _walk(prefix: str, value: Any) -> None:
        if value is None or value == "" or value == [] or value == {}:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(value, list):
            # Lists of scalars render inline; lists of dicts get indexed and
            # recursed to full depth.
            if all(not isinstance(v, (dict, list)) for v in value):
                rendered = ", ".join(_scalar(v) for v in value if v not in (None, ""))
                if rendered:
                    parts.append(f"{prefix}=[{rendered}]")
            else:
                # List indices are not a semantic nesting level — descend into
                # each element so `experience[].role` expands one dict level
                # (matching `specs.cpu`), not two.
                for i, v in enumerate(value):
                    _walk(f"{prefix}[{i}]", v)
        else:
            parts.append(f"{prefix}={_scalar(value)}")

    for key, val in metadata.items():
        _walk(str(key), val)

    if not parts:
        return ""
    return ", ".join(parts)


def _render_prior_evidence(
    prior_evidence: List[Dict[str, Any]],
    max_total_docs: int = 200,
) -> str:
    """Render cross-turn evidence groups as a compact guidance block.

    One section per group, labeled with its query/filter, listing each doc as
    ``[object_id] <summary> (file_name) {metadata}``. Summaries and metadata are
    rendered in full (no per-value truncation).

    A turn can make many ``retrieve_documents`` calls (different queries/filters)
    → many groups. We ALWAYS emit every group's header (so the agent sees every
    query/filter that ran) and every doc within a group, but stop emitting doc
    *lines* once ``max_total_docs`` is reached across all groups, noting the
    remainder by count. There is NO per-group cap — only this global budget
    bounds the block. Every object_id stays in the DB for re-fetch regardless.
    Returns ``""`` when there's nothing to render.
    """
    lines: List[str] = []
    chunk_groups: List[Dict[str, Any]] = []
    docs_emitted = 0
    for group in prior_evidence:
        if not isinstance(group, dict):
            continue
        docs = group.get("documents") or []
        if not docs:
            continue
        # Global chunk-scan groups render in their OWN section (below) with a
        # "not full documents" caveat — they're chunk hits (pointers), not read
        # documents, and must not be confused with the doc block.
        if group.get("source") == "global_chunk_scan":
            chunk_groups.append(group)
            continue
        label_parts = []
        if group.get("question_shape"):
            label_parts.append(f'shape={group["question_shape"]}')
        if group.get("query"):
            label_parts.append(f'query="{group["query"]}"')
        if group.get("metadata_filter"):
            label_parts.append(f'filter={group["metadata_filter"]}')
        # Completeness marker: present only when the prior call was an exhaustive
        # shape that enumerated the FULL population (see build_grouped_evidence).
        # The agent may answer exhaustive counts (e.g. "how many missing X")
        # directly from a `complete=N` group; absence of the marker means SAMPLE.
        if isinstance(group.get("complete_population"), int):
            label_parts.append(f'complete={group["complete_population"]}')
        header = " · ".join(label_parts) if label_parts else "(prior retrieval)"
        lines.append(f"[{header}]")
        valid_docs = [d for d in docs if isinstance(d, dict) and d.get("object_id")]
        # No per-group cap — show every doc in the group, bounded only by the
        # remaining global doc budget so a turn full of retrieve_documents calls
        # can't blow the prompt.
        remaining_global = max(0, max_total_docs - docs_emitted)
        shown = valid_docs[:remaining_global]
        for doc in shown:
            summary = (doc.get("summary") or "").strip().replace("\n", " ")
            file_name = doc.get("file_name")
            suffix = f"  ({file_name})" if file_name else ""
            meta_str = _render_doc_metadata(doc.get("metadata") or {})
            meta_suffix = f"  {{{meta_str}}}" if meta_str else ""
            lines.append(f"  - [{doc['object_id']}] {summary}{suffix}{meta_suffix}")
        docs_emitted += len(shown)
        overflow = len(valid_docs) - len(shown)
        if overflow > 0:
            lines.append(
                f"  … and {overflow} more doc(s) in this group "
                f"(re-fetch by object_id if you need them)"
            )
    sections: List[str] = []
    if lines:
        sections.append(
            "## Documents already retrieved earlier in this conversation\n"
            + "\n".join(lines)
            + "\n\nThese docs are already in play. Apply Core Principle #1 Step 0 (the "
            "reuse gate, = Core Principle #4) BEFORE any retrieval: if the summaries above "
            "are about the SAME subject the current question asks about AND already cover it, "
            "answer straight from them and call no tool. "
            "If the doc is listed but the attribute is in its body, scope "
            "`retrieve_chunks(object_ids=...)` to these object_ids instead of re-resolving "
            "the entity — skip pre-flight and field resolution entirely. Only run pre-flight "
            "and retrieve fresh when the question is NOT COVERED by the docs above — i.e. the "
            "current question is about a DIFFERENT subject / entity / topic than these docs (a "
            "subject or topic shift is NOT covered, even when the prior docs come from this "
            "same knowledge base), no doc here answers it, or the evidence comes from a "
            "different agent/corpus than the "
            "one now being queried (a switch of source, not of subject — the same question can "
            "be uncovered simply because the prior docs belong elsewhere). When unsure whether "
            "the block covers the question, prefer fresh retrieval over reuse. Do not re-run a "
            "filter that already appears above."
        )

    chunk_block = _render_global_chunk_scans(chunk_groups)
    if chunk_block:
        sections.append(chunk_block)

    return "\n\n".join(sections)


def _render_global_chunk_scans(chunk_groups: List[Dict[str, Any]]) -> str:
    """Render documents that surfaced in an earlier whole-KB search.

    These came up as relevant in a prior search but were NOT read in full — so
    they render in their own section, distinct from the read-document block.
    Each line shows the ``object_id`` (read it by id to use it), a topic/title
    label, how many sections matched, and the best score. The rendered text
    deliberately avoids "chunk"/"scan" vocabulary — those are internal storage
    terms; the agent sees only documents + object_ids. Returns ``""`` when
    nothing to render.
    """
    lines: List[str] = []
    for group in chunk_groups:
        docs = [d for d in (group.get("documents") or []) if isinstance(d, dict) and d.get("object_id")]
        if not docs:
            continue
        header = f'query="{group["query"]}"' if group.get("query") else "(earlier search)"
        lines.append(f"[{header}]")
        for doc in docs:
            # Topic preferred; fall back to enriched page/category; bare otherwise.
            if doc.get("chunk_topic"):
                label = f'"{doc["chunk_topic"]}"'
            elif doc.get("page_title"):
                cat = f' · category: {doc["category"]}' if doc.get("category") else ""
                label = f'"{doc["page_title"]}"{cat}'
            else:
                label = "(not yet read)"
            n = len(doc.get("chunk_ids") or [])
            count_str = f"{n} match{'es' if n != 1 else ''}" if n else "matched"
            score = doc.get("score")
            score_str = f" · score {score}" if score is not None else ""
            lines.append(f"  - [{doc['object_id']}] {label}  ({count_str}{score_str})")
    if not lines:
        return ""
    return (
        "## Documents surfaced in an earlier search (not yet read in full)\n"
        + "\n".join(lines)
        + "\n\nThese documents came up as relevant earlier but were not read in full. "
        "If one of them likely answers the current question, retrieve it by object_id to "
        "read it in full before searching again."
    )


def _flatten_prior_docs(prior_evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten prior-turn evidence groups into a deduplicated doc list.

    Each doc keeps object_id + summary + file_name + metadata as stored by
    build_grouped_evidence — no chunk text, full metadata. Deduplication is
    by object_id (first occurrence wins so the freshest turn's copy is kept,
    since get_recent_evidence returns newest-first).
    """
    seen: set = set()
    docs: List[Dict[str, Any]] = []
    for group in prior_evidence:
        if not isinstance(group, dict):
            continue
        for doc in (group.get("documents") or []):
            if not isinstance(doc, dict):
                continue
            oid = doc.get("object_id")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            docs.append(doc)
    return docs


def _render_resolved_docs_for_retry(ctx: Dict[str, Any], max_docs: int = 30) -> str:
    """Compact list of the docs run 1 already resolved, for the verifier retry.

    The verifier retry restarts from a SLIM thread (see the retrieval loop) —
    instead of carrying every accumulated retrieve_documents ToolMessage
    forward (which made run 2's input balloon to ~run-1 size before doing any
    work), we hand the agent just the object_ids + titles + truncated
    summaries it already found. That lets it scope retrieve_chunks straight at
    those docs rather than re-running the document-layer search. Returns "" when
    nothing was resolved.
    """
    summaries = ctx.get("summaries") or {}
    if not isinstance(summaries, dict) or not summaries:
        return ""
    lines = [
        "## Documents already resolved this turn",
        "Scope `retrieve_chunks(object_ids=[...])` to these — do NOT re-run "
        "`retrieve_documents` at the document layer; the answer is most likely "
        "in the body of one of these docs.",
    ]
    for oid, data in list(summaries.items())[:max_docs]:
        if not isinstance(data, dict):
            continue
        title = (data.get("title") or "").strip()
        summary = (data.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 200:
            summary = summary[:200].rstrip() + "…"
        label = f" {title}" if title else ""
        lines.append(f"  - [{oid}]{label} — {summary}")
    return "\n".join(lines) if len(lines) > 2 else ""


def _parse_agent_output(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract RagAgentOutput from result as a plain dict.

    Tries ``result["structured_response"]`` first (set when the agent graph
    uses response_format). Falls back to parsing the last AIMessage content
    as JSON — the agent writes its final answer as a JSON blob matching the
    RagAgentOutput schema even without structured-output binding.
    Returns None only when no parseable output is found.
    """
    structured_response = result.get("structured_response")
    if structured_response is not None:
        return (
            structured_response.model_dump()
            if hasattr(structured_response, "model_dump")
            else dict(structured_response)
        )

    messages = result.get("messages") or []
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        content = msg.content
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            ).strip()
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and "stop_reason" in parsed:
            logger.info(
                f"[agent_output] Parsed RagAgentOutput from last AIMessage — "
                f"stop_reason={parsed.get('stop_reason')!r} "
                f"confidence={parsed.get('confidence')!r}"
            )
            return parsed
    logger.warning("[agent_output] No parseable RagAgentOutput found in result")
    return None


def _log_tool_errors(messages: List[Any]) -> None:
    """Surface tool failures buried inside ``ToolMessage``s to the app log.

    Two failure paths never reach a tool's own ``logger`` calls and are
    otherwise invisible to log scraping / tracing:
      1. **Schema-validation rejections** — Pydantic ``args_schema`` validation
         runs inside LangGraph's ``ToolNode`` *before* the tool function body.
         The ToolNode catches the error and emits a ``ToolMessage`` with
         ``status == "error"``; the function never runs, so nothing is logged.
      2. **Runtime tool errors** — the tool returns an ``artifact`` dict with an
         ``"error"`` key (the function ran but rejected its inputs).

    We walk the freshly produced messages and emit one ``warning`` per failing
    ToolMessage so these show up in the same stream as the rest of the agent.
    Pure logging side-effect — never raises.
    """
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        tool_name = getattr(msg, "name", "unknown")
        # Path 1: framework-level validation / execution error.
        if getattr(msg, "status", None) == "error":
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            logger.warning(
                f"[tool-error] '{tool_name}' rejected before/at execution "
                f"(status=error): {content[:500]}"
            )
            continue
        # Path 2: tool ran but returned an error artifact.
        artifact = getattr(msg, "artifact", None)
        if isinstance(artifact, dict) and artifact.get("error"):
            logger.warning(
                f"[tool-error] '{tool_name}' returned error artifact: "
                f"{str(artifact['error'])[:500]}"
            )


def _extract_retrieved_context_from_messages(messages: List[Any]) -> Dict[str, Any]:
    """Walk the ReAct agent's message thread and build ``retrieved_context``.

    Pulls per-tool results out of every ``ToolMessage``, normalizes them into:
      - ``summaries``       — ``{object_id: {summary, metadata, file_name, ...}}``
      - ``chunks``          — list of chunk dicts (deduplicated by identity)
      - ``metadata_values`` — ``{field_name: [values]}`` from catalog calls
      - ``metadata_fields`` — list of field descriptors (when returned)
      - ``document_ids``    — legacy ID-only list responses
      - ``tool_responses``  — verbatim list of every tool call's result
      - ``raw_output``      — empty here; filled by ``run_rag_agent`` from
        the RagAgentOutput.answer mirror after the agent loop completes.

    Pure function — extraction is idempotent over the same message list, so
    the verifier-retry loop can call it multiple times without state issues.
    """
    # ``raw_output`` is preserved as a key for back-compat with downstream
    # readers (rag_node direct-answer mode, _build_relevant_context) but is
    # populated by the structured-handoff mirror in run_rag_agent, not here.
    retrieved_context: Dict[str, Any] = {
        "raw_output": "",
        "messages": messages,
        "summaries": {},
        "chunks": [],
        "tool_responses": [],
    }

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        try:
            artifact = getattr(msg, "artifact", None)
            if artifact is not None:
                tool_result = artifact
            elif isinstance(msg.content, str):
                try:
                    tool_result = json.loads(msg.content)
                except (json.JSONDecodeError, ValueError):
                    tool_result = msg.content
            else:
                tool_result = msg.content

            tool_name = getattr(msg, "name", "unknown")
            retrieved_context["tool_responses"].append({
                "tool_name": tool_name,
                "tool_call_id": getattr(msg, "tool_call_id", None),
                "result": tool_result,
            })

            if not isinstance(tool_result, dict):
                continue

            # ── retrieve_documents / legacy get-document-details ────────
            if "documents" in tool_result and isinstance(tool_result["documents"], list):
                for doc in tool_result["documents"]:
                    if not (isinstance(doc, dict) and "object_id" in doc):
                        continue
                    object_id = doc["object_id"]
                    metadata = doc.get("metadata") or {}
                    if not isinstance(metadata, dict):
                        metadata = {}

                    excluded = {"summary", "object_id", "metadata", "file_name", "filename"}
                    for key, value in doc.items():
                        if key not in excluded and key not in metadata:
                            metadata[key] = value

                    top_summary = doc.get("summary", "")
                    summary_data: Dict[str, Any] = {
                        "summary": top_summary or metadata.get("summary", ""),
                        "metadata": metadata,
                        "object_id": object_id,
                    }
                    if "url" in doc:
                        summary_data["url"] = doc["url"]

                    top_file_name = doc.get("file_name") or doc.get("filename")
                    meta_file_name = metadata.get("file_name") or metadata.get("filename")
                    file_name_used: Optional[str] = None
                    # Treat a 36-char UUID-looking top-level file_name as a fallback;
                    # prefer the metadata file_name if available.
                    if top_file_name and len(top_file_name) == 36 and "-" in top_file_name:
                        file_name_used = meta_file_name or top_file_name
                    elif top_file_name:
                        file_name_used = top_file_name
                    elif meta_file_name:
                        file_name_used = meta_file_name
                    if file_name_used:
                        summary_data["file_name"] = file_name_used
                        if "filename" in metadata:
                            del metadata["filename"]
                        metadata["file_name"] = file_name_used

                    summary_data["title"] = doc.get("title") or metadata.get("title")
                    handled = {"summary", "metadata", "url", "file_name", "filename", "title", "object_id"}
                    for key in doc.keys():
                        if key not in handled and key not in summary_data:
                            summary_data[key] = doc[key]

                    retrieved_context["summaries"][object_id] = summary_data

            # ── legacy "summaries" dict response ────────────────────────
            if "summaries" in tool_result and isinstance(tool_result["summaries"], dict):
                for obj_id, summary_val in tool_result["summaries"].items():
                    existing = retrieved_context["summaries"].get(obj_id)
                    if existing is None:
                        if isinstance(summary_val, dict):
                            summary_val.setdefault("metadata", {})
                            retrieved_context["summaries"][obj_id] = summary_val
                        else:
                            retrieved_context["summaries"][obj_id] = {
                                "summary": summary_val,
                                "metadata": {},
                            }
                    else:
                        if isinstance(summary_val, dict):
                            existing.update(summary_val)
                            existing.setdefault("metadata", {})
                        else:
                            existing["summary"] = summary_val
                            existing.setdefault("metadata", {})

            # ── legacy single-doc metadata response ─────────────────────
            if "object_id" in tool_result and "metadata" in tool_result:
                obj_id = tool_result["object_id"]
                entry = retrieved_context["summaries"].setdefault(obj_id, {})
                entry["metadata"] = tool_result.get("metadata", {})
                if "summary" in tool_result:
                    entry["summary"] = tool_result["summary"]

            # ── chunks (from retrieve_chunks / deep-read) ───────────────
            if "chunks" in tool_result and isinstance(tool_result["chunks"], list):
                parent_object_id = tool_result.get("object_id")
                existing_chunk_ids = {
                    _chunk_identity(c) for c in retrieved_context["chunks"]
                    if isinstance(c, dict)
                }
                existing_chunk_ids.discard(None)

                for chunk in tool_result["chunks"]:
                    if not isinstance(chunk, dict):
                        retrieved_context["chunks"].append(chunk)
                        continue
                    key = _chunk_identity(chunk)
                    if key is not None and key in existing_chunk_ids:
                        continue
                    if (
                        "object_id" not in chunk
                        and "document_id" not in chunk
                        and parent_object_id
                    ):
                        chunk["object_id"] = parent_object_id
                        chunk["document_id"] = parent_object_id
                    elif "object_id" in chunk and "document_id" not in chunk:
                        chunk["document_id"] = chunk["object_id"]
                    elif "document_id" in chunk and "object_id" not in chunk:
                        chunk["object_id"] = chunk["document_id"]
                    retrieved_context["chunks"].append(chunk)
                    if key:
                        existing_chunk_ids.add(key)

            # ── legacy object_ids list ──────────────────────────────────
            if "object_ids" in tool_result:
                retrieved_context.setdefault("document_ids", []).extend(
                    tool_result["object_ids"]
                )

            # ── retrieve_metadata_catalog values_by_field ───────────────
            vbf = tool_result.get("values_by_field")
            if isinstance(vbf, dict) and vbf:
                mv = retrieved_context.setdefault("metadata_values", {})
                for field_name, field_data in vbf.items():
                    if isinstance(field_data, dict):
                        mv[field_name] = field_data.get("values", [])

            # ── retrieve_metadata_catalog fields (dict or list) ─────────
            fields_data = tool_result.get("fields")
            if isinstance(fields_data, dict):
                mv = retrieved_context.setdefault("metadata_values", {})
                for field_name, field_data in fields_data.items():
                    if isinstance(field_data, dict):
                        mv[field_name] = field_data.get("values", [])
            elif isinstance(fields_data, list):
                retrieved_context["metadata_fields"] = fields_data

            # ── legacy single-field values response ─────────────────────
            if "values" in tool_result and "field_name" in tool_result:
                retrieved_context.setdefault("metadata_values", {})[
                    tool_result["field_name"]
                ] = tool_result["values"]

        except (json.JSONDecodeError, TypeError) as e:
            logger.debug(f"Could not parse tool message content: {e}")

    return retrieved_context


@track_time("RAG: Run Agent")
async def run_rag_agent(
    query: str,
    knowledge_id: str,
    user_id: str,
    metadata_filter: Optional[Dict[str, Any]] = None,
    object_ids: Optional[list] = None,
    llm_provider: str = "openai",
    llm_model: str = "gpt-4o-mini",
    user_instructions: Optional[str] = None,
    owner_id: Optional[str] = None,
    metadata_schema: Optional[Dict[str, Any]] = None,
    past_messages: Optional[List[Dict[str, Any]]] = None,
    prior_evidence: Optional[List[Dict[str, Any]]] = None,
    generate_answer_instructions: Optional[str] = None,
    answer_llm_provider: Optional[str] = None,
    answer_llm_model: Optional[str] = None,
    answer_max_tokens: Optional[int] = None,
    enable_retrieval_verifier: bool = True,
    max_retrieval_retries: int = 1,
    verifier_llm_provider: Optional[str] = None,
    verifier_llm_model: Optional[str] = None,
    answer_token_queue: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Run the RAG agent to retrieve context using modern LangGraph approach.

    Returns:
        Dictionary containing retrieved context:
        {
            "context": {...},
            "summaries": {...},
            "chunks": [...],
            "messages": [...]
        }
    """
    schema_fields = list((metadata_schema or {}).get("fields", {}).keys())
    schema_enumerable = list((metadata_schema or {}).get("enumerable_values", {}).keys())
    logger.info(
        f"[agent_execution] run_rag_agent called — "
        f"schema_fields={len(schema_fields)} {schema_fields} | "
        f"enumerable={len(schema_enumerable)} {schema_enumerable}"
    )

    try:
        rag_agent, key_type = await create_rag_agent_graph(
            knowledge_id=knowledge_id,
            llm_provider=llm_provider,
            llm_model=llm_model,
            user_instructions=user_instructions,
            owner_id=owner_id,
            generate_answer_instructions=generate_answer_instructions,
        )

        # ── BYOK guard around the agent invoke ────────────────────────────────
        # Not chat/byok_fallback.run_with_key_fallback: that helper builds ONE chat
        # model, whereas this path builds a whole agent (tools + middleware +
        # structured output) whose model is bound at construction. So recovering
        # from a rejected owner key means rebuilding the graph. The policy — only
        # terminal failures, demote the key, gate the fallback on the owner's usage
        # limit — is shared with that module rather than re-decided here.
        async def _ainvoke_agent(messages: list):
            nonlocal rag_agent, key_type
            try:
                return await rag_agent.ainvoke({"messages": messages})
            except Exception as exc:
                if key_type != "owner-key":
                    raise
                error_type = classify_provider_error(exc)
                if not is_terminal(error_type):
                    raise

                # get_llm normalizes provider/model (a Groq-hosted OSS model id
                # tagged "openai" routes to groq), and the credential row is keyed
                # by the NORMALIZED provider — so demoting the raw llm_provider
                # could mark the wrong row and leave the dead key in service.
                effective_provider, _ = normalize_provider_and_model(llm_provider, llm_model)
                resolved_owner = owner_id or await resolve_owner_id_for_knowledge(knowledge_id)
                logger.warning(
                    f"[rag_agent] {effective_provider} key rejected ({error_type}) for "
                    f"owner_id={resolved_owner} — demoting and rebuilding the agent on "
                    f"the platform key"
                )
                if resolved_owner:
                    await mark_credential_failed(
                        resolved_owner, effective_provider, error_type, str(exc)
                    )

                allowed, reason = await platform_fallback_allowed(resolved_owner, CHAT_OPERATION)
                if not allowed:
                    raise ByokKeyRejected(effective_provider, error_type, reason) from exc

                rag_agent, key_type = await create_rag_agent_graph(
                    knowledge_id=knowledge_id,
                    llm_provider=llm_provider,
                    llm_model=llm_model,
                    user_instructions=user_instructions,
                    owner_id=owner_id,
                    generate_answer_instructions=generate_answer_instructions,
                    force_platform_key=True,
                )
                collect_warning(build_warning(effective_provider, error_type))
                return await rag_agent.ainvoke({"messages": messages})

        initial_messages: list = []

        # Inject recent session history so the agent can resolve references
        # like "tell me more about that" or "the company I mentioned".
        if past_messages:
            from utils.setup_env import RAG_SESSION_HISTORY_TURNS
            from chat.utils.messages import build_history_messages

            history = build_history_messages(
                past_messages, limit=RAG_SESSION_HISTORY_TURNS * 2, skip_empty=True,
            )
            if history:
                initial_messages.extend(history)
                logger.info(f"Injected {len(history)} session history messages into RAG agent")

        # The retrieval agent is always retrieval-only. The final user-facing
        # answer is always produced by ``generate_rag_answer`` after the ReAct
        # loop returns (see end of this function).

        # Pre-synthesized retrieve_metadata_catalog tool call: we already
        # fetched the lightweight schema upstream, so we inject it as a
        # completed tool call. The agent sees the result directly and can
        # skip this first metadata-exploration hop.
        # Must be injected BEFORE the query HumanMessage to maintain valid
        # message ordering: AIMessage(tool_calls) → ToolMessage → HumanMessage.
        if metadata_schema and metadata_schema.get("fields"):
            from chat.rag_agent.context_utils import build_metadata_catalog_tool_result

            prefetched_result = build_metadata_catalog_tool_result(metadata_schema)
            if prefetched_result:
                prefetched_content, prefetched_response = prefetched_result
                prefetch_call_id = "call_prefetched_metadata_catalog"
                # Args reflect what was actually fetched:
                # - low-cardinality fields: complete value lists (include_values=true equivalent)
                # - high-cardinality fields: 5 sample values only (is_sample=true in response)
                # Do NOT set include_values=true here — that would mislead the agent into
                # thinking all high-cardinality values are exhaustive and skip further lookups.
                prefetch_tool_args = {
                    "knowledge_id": knowledge_id,
                }
                # Gemini enforces strict turn order: a function-call turn must
                # come immediately after a USER turn or a function-response
                # turn. This synthetic pair often lands at the very start of
                # the thread (or right after an assistant history message),
                # which 400s EVERY model call on Google models ("Please ensure
                # that function call turn comes immediately after a user
                # turn..." — every call then rides the fallback model). A short
                # user turn in front satisfies the constraint and is harmless
                # for all other providers.
                initial_messages.append(
                    HumanMessage(
                        content="First, load the metadata catalog for this knowledge base."
                    )
                )
                initial_messages.append(
                    AIMessage(
                        content="",
                        tool_calls=[{
                            "id": prefetch_call_id,
                            "name": "retrieve_metadata_catalog",
                            "args": prefetch_tool_args,
                        }],
                    )
                )
                initial_messages.append(
                    ToolMessage(
                        content=prefetched_content,
                        name="retrieve_metadata_catalog",
                        tool_call_id=prefetch_call_id,
                    )
                )
                logger.info(
                    f"[prefetch] Injected retrieve_metadata_catalog into agent history — "
                    f"fields={len(prefetched_response.get('fields', []))} | "
                    f"with_values={len(prefetched_response.get('values_by_field', {}))} "
                    f"{list(prefetched_response.get('values_by_field', {}).keys())}"
                )
            else:
                logger.warning("[prefetch] build_metadata_catalog_tool_response returned empty — skipping injection")
        else:
            logger.warning(
                f"[prefetch] Skipping injection — metadata_schema empty or has no fields "
                f"(schema keys: {list((metadata_schema or {}).keys())})"
            )

        # Inject cross-turn evidence: documents retrieved in the last N valid
        # turns of this session. Lets the agent reuse docs already in play and
        # scope follow-up chunk retrieval to them. Guidance only — see
        # _render_prior_evidence. No-evidence path is a no-op.
        if prior_evidence:
            evidence_block = _render_prior_evidence(prior_evidence)
            if evidence_block:
                initial_messages.append(HumanMessage(content=evidence_block))
                rendered_headers = evidence_block.count("\n[")
                logger.info(
                    f"[prior_evidence] Injected {len(prior_evidence)} group(s) "
                    f"({rendered_headers} rendered header(s)) of cross-turn evidence "
                    f"into RAG agent"
                )
                # Full rendered block at DEBUG so we can confirm grouping in a
                # live turn instead of inferring it from the DB.
                logger.debug(f"[prior_evidence] rendered block:\n{evidence_block}")

        # Operator instructions: injected as the LAST HumanMessage before the
        # query, so they sit at the END of context. Anthropic's long-context
        # guidance: critical instructions belong at the beginning or end of
        # context; middle placement loses recall. No Agent-Registry default to
        # fall back to here — when the caller-supplied instructions are empty,
        # there is simply no operator-instructions injection.
        effective_instructions = user_instructions

        if isinstance(effective_instructions, str) and effective_instructions.strip():
            initial_messages.append(
                HumanMessage(content=(
                    "## Operator instructions\n"
                    "Apply these to HOW you retrieve and what you prioritize for the "
                    "question that follows:\n\n"
                    + effective_instructions.strip()
                ))
            )
            logger.info("Injected operator instructions as HumanMessage before query")

        initial_messages.append(
            HumanMessage(content=query)
        )

        usage_metrics = {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cached_input_tokens": 0, "cache_write_tokens": 0,
        }
        already_counted_msg_count = 0  # tracks how many messages we've already accumulated usage for

        # ── Retrieval loop with CRAG-style verifier ───────────────────────────
        # On each attempt: invoke the ReAct agent, accumulate usage only for
        # NEW messages, then (if verifier enabled and retries remain) run the
        # verifier. If the verdict is "insufficient", append the verifier's
        # feedback as a HumanMessage and continue the loop. Cap by
        # ``max_retrieval_retries``.
        current_messages = initial_messages
        result: Dict[str, Any] = {"messages": []}
        verifier_verdicts: List[Any] = []

        _retrieval_t0 = time.perf_counter()
        for attempt in range(max_retrieval_retries + 1):
            result = await _ainvoke_agent(current_messages)

            new_messages = result.get("messages", []) or []

            # Accumulate usage only for messages added on THIS turn — avoids
            # double-counting prior messages that the loop re-sees.
            for msg in new_messages[already_counted_msg_count:]:
                if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    usage_data = msg.usage_metadata
                    if isinstance(usage_data, dict):
                        usage_metrics["input_tokens"] += usage_data.get(
                            "input_tokens", usage_data.get("prompt_tokens", 0)
                        ) or 0
                        usage_metrics["output_tokens"] += usage_data.get(
                            "output_tokens", usage_data.get("completion_tokens", 0)
                        ) or 0
                        usage_metrics["total_tokens"] += usage_data.get("total_tokens", 0) or 0
                        # Cache-read/creation subsets of input, for cost pricing.
                        from chat.utils.usage import extract_cache_tokens
                        _cached, _write = extract_cache_tokens(usage_data)
                        usage_metrics["cached_input_tokens"] += _cached
                        usage_metrics["cache_write_tokens"] += _write
            # Surface any tool failures (schema-validation rejections or error
            # artifacts) that LangGraph buried inside ToolMessages — these never
            # reach the tool's own logger. Scope to messages added this attempt.
            _log_tool_errors(new_messages[already_counted_msg_count:])

            already_counted_msg_count = len(new_messages)

            logger.info(
                f"LangGraph RAG agent completed (attempt {attempt + 1}/"
                f"{max_retrieval_retries + 1}, {len(new_messages)} messages)."
            )

            if (
                not enable_retrieval_verifier
                or attempt >= max_retrieval_retries
            ):
                # Verifier disabled OR retry budget exhausted — accept
                # whatever the agent produced. We intentionally do NOT run
                # the verifier on the final attempt: its job is to decide
                # whether to retry, and we've already decided we can't.
                # Running it again would just be a wasted LLM call.
                break

            # Build a preliminary retrieved_context from the messages so far
            # so the verifier can judge sufficiency. The final extraction
            # below uses the same messages — it's safe to extract twice.
            preliminary_context = _extract_retrieved_context_from_messages(new_messages)
            agent_output = _parse_agent_output(result)
            if agent_output is not None:
                preliminary_context["agent_output"] = agent_output
            if prior_evidence:
                preliminary_context["prior_turn_docs"] = _flatten_prior_docs(prior_evidence)

            try:
                from chat.rag_agent.retrieval_verifier import (
                    verify_retrieval,
                    format_replan_message,
                )

                # Verifier LLM: prefer the verifier-specific provider/model
                # (env-configured upstream, defaults to a small fast OSS model
                # since the verifier only renders a sufficiency judgement, not
                # a full answer). Falls back to the answer-node model, then to
                # the retrieval-agent model.
                verdict = await verify_retrieval(
                    query=query,
                    retrieved_context=preliminary_context,
                    llm_provider=verifier_llm_provider or answer_llm_provider or llm_provider,
                    llm_model=verifier_llm_model or answer_llm_model or llm_model,
                    owner_id=owner_id,
                    knowledge_id=knowledge_id,
                    # Pass the agent's tool-call thread so the verifier sees
                    # WHAT was tried (shape/query/filter per call), not just
                    # what came back — needed for the decomposition check.
                    messages=new_messages,
                )

                verifier_verdicts.append(verdict)
                logger.info(
                    f"[verifier] attempt {attempt + 1} verdict — "
                    f"sufficient={verdict.sufficient} confidence={verdict.confidence}"
                )
            except Exception as e:
                # Fail-open: a verifier failure must not break the pipeline.
                logger.warning(
                    f"[verifier] verifier crashed ({e}); proceeding to answer node "
                    f"with current retrieval"
                )
                break

            if verdict.sufficient:
                break

            # Insufficient + retries remain — restart from a SLIM thread, NOT
            # the full accumulated run-1 message pile. Carrying every prior
            # retrieve_documents ToolMessage forward made run 2's input balloon
            # to ~run-1 size before it did any work (the dominant token cost in
            # observed traces). We keep the original setup context
            # (history + catalog + prior evidence + operator instructions +
            # query = initial_messages), add a COMPACT pointer to the docs run 1
            # already resolved, and the verifier feedback — which already lists
            # every tool call tried (format_replan_message), so the agent won't
            # repeat them. The agent keeps full reasoning ability; it just isn't
            # re-reading rounds of stale tool output.
            feedback = format_replan_message(verdict, prior_messages=new_messages)
            resolved_block = _render_resolved_docs_for_retry(preliminary_context)
            retry_messages = list(initial_messages)
            if resolved_block:
                retry_messages.append(HumanMessage(content=resolved_block))
            retry_messages.append(HumanMessage(content=feedback))
            current_messages = retry_messages
            # Reset the usage watermark to the slim-thread length: the next
            # attempt's NEW model output starts after these input messages, not
            # after run 1's (now-discarded) full thread.
            already_counted_msg_count = len(current_messages)
            logger.info(
                f"[verifier] retrieval insufficient — re-planning from slim thread "
                f"({len(current_messages)} msgs vs {len(new_messages)} carried before) "
                f"(missing={verdict.missing!r})"
            )

        if usage_metrics["total_tokens"] == 0:
            usage_metrics["total_tokens"] = usage_metrics["input_tokens"] + usage_metrics["output_tokens"]

        # Retrieval-loop latency (ReAct agent + verifier retries, our end) —
        # the answer node below is timed separately so the two nodes never
        # share one latency number (see chat/rag_node.py's Agent Health
        # node-tagging, which needs each node's own timing).
        _retrieval_latency_ms = int((time.perf_counter() - _retrieval_t0) * 1000)

        messages = result.get("messages", []) or []

        retrieved_context = _extract_retrieved_context_from_messages(messages)
        # How many passes the retrieval loop actually ran. `attempt` is the
        # loop's own index and survives the loop, so this is the real count
        # (1 on the common path), not the configured budget. Free RAG-eval
        # signal — see utils/knowledge_health/rag_eval.py.
        retrieved_context["retrieval_attempts"] = attempt + 1
        # key_type comes from create_rag_agent_graph's get_llm resolution above
        # (owner-key vs platform-key) — surfaced here so rag_node's usage-logging
        # call can report the real value instead of always defaulting to
        # "platform-key" (see chat/rag_node.py::_log_usage's docstring).
        retrieved_context["key_type"] = key_type

        if prior_evidence:
            prior_docs = _flatten_prior_docs(prior_evidence)
            retrieved_context["prior_turn_docs"] = prior_docs
            logger.info(f"[rag_agent] Attached {len(prior_docs)} prior-turn doc(s) to retrieved_context")

        agent_output = _parse_agent_output(result)
        if agent_output is not None:
            retrieved_context["agent_output"] = agent_output
            logger.info(
                f"[rag_agent] agent_output: stop_reason={agent_output.get('stop_reason')!r} "
                f"confidence={agent_output.get('confidence')!r}"
            )

        if verifier_verdicts:
            # Surface verifier history for trace / debug. Final verdict is at [-1].
            retrieved_context["verifier_verdicts"] = [
                v.model_dump() if hasattr(v, "model_dump") else dict(v.__dict__)
                for v in verifier_verdicts
            ]

        logger.info(f"Retrieved {len(retrieved_context['summaries'])} summaries and {len(retrieved_context['chunks'])} chunks")
        logger.info(f"Tool responses captured: {len(retrieved_context['tool_responses'])}")

        # Per-(provider, model) cost breakdown. The ReAct retrieval loop ran on
        # (llm_provider, llm_model); snapshot its tokens NOW — before the answer
        # node (which may use a different model) adds its own below — so the two
        # are priced as separate models, not lumped onto one rate.
        usage_by_model = []
        service_calls: List[Dict[str, Any]] = []
        if usage_metrics["input_tokens"] or usage_metrics["output_tokens"]:
            usage_by_model.append({
                "provider": llm_provider, "model": llm_model,
                "input_tokens": usage_metrics["input_tokens"],
                "output_tokens": usage_metrics["output_tokens"],
                **({"cached_input_tokens": usage_metrics["cached_input_tokens"]}
                   if usage_metrics.get("cached_input_tokens") else {}),
                **({"cache_write_tokens": usage_metrics["cache_write_tokens"]}
                   if usage_metrics.get("cache_write_tokens") else {}),
            })
            # Agent Health node breakdown — the retrieval ReAct loop, tagged
            # "rag_agent" (distinct from the answer writer below, since 27/52
            # production agents run the two roles on different models).
            service_calls.append(build_service_call(
                service="rag_agent", latency_ms=_retrieval_latency_ms,
                provider=llm_provider, model=llm_model,
                input_tokens=usage_metrics["input_tokens"],
                output_tokens=usage_metrics["output_tokens"],
            ))

        # Invoke the separate answer node now that retrieval is complete. Its
        # output replaces ``raw_output`` so the downstream ``rag_node`` can
        # surface it as the final response.
        from chat.rag_agent.answer_node import generate_rag_answer

        _answer_t0 = time.perf_counter()
        answer_result = await generate_rag_answer(
            query=query,
            retrieved_context=retrieved_context,
            llm_provider=answer_llm_provider or llm_provider,
            llm_model=answer_llm_model or llm_model,
            owner_id=owner_id,
            generate_answer_instructions=generate_answer_instructions,
            past_messages=past_messages,
            token_queue=answer_token_queue,
            max_tokens=answer_max_tokens,
            knowledge_id=knowledge_id,
        )
        _answer_latency_ms = int((time.perf_counter() - _answer_t0) * 1000)

        answer_text = answer_result.get("answer", "") or ""
        answer_usage = answer_result.get("usage_metrics") or {}

        retrieved_context["raw_output"] = answer_text
        retrieved_context["answer_node_output"] = answer_text
        # Surface the answer node's self-evaluation for debug surfaces /
        # downstream retry logic. True only when the answer node's LLM
        # judged its own answer as actually resolving the user's question.
        retrieved_context["is_answered"] = bool(answer_result.get("is_answered", False))

        a_in = answer_usage.get("input_tokens", 0) or 0
        a_out = answer_usage.get("output_tokens", 0) or 0
        a_cached = answer_usage.get("cached_input_tokens", 0) or 0
        a_write = answer_usage.get("cache_write_tokens", 0) or 0
        usage_metrics["input_tokens"] += a_in
        usage_metrics["output_tokens"] += a_out
        usage_metrics["total_tokens"] += answer_usage.get("total_tokens", 0) or 0
        usage_metrics["cached_input_tokens"] += a_cached
        usage_metrics["cache_write_tokens"] += a_write

        # Answer node may run a different model than the retrieval loop — its
        # own breakdown line (merged by (provider, model) downstream).
        if a_in or a_out:
            usage_by_model.append({
                "provider": answer_llm_provider or llm_provider,
                "model": answer_llm_model or llm_model,
                "input_tokens": a_in, "output_tokens": a_out,
                **({"cached_input_tokens": a_cached} if a_cached else {}),
                **({"cache_write_tokens": a_write} if a_write else {}),
            })
            service_calls.append(build_service_call(
                service="generate_answer", latency_ms=_answer_latency_ms,
                provider=answer_llm_provider or llm_provider,
                model=answer_llm_model or llm_model,
                input_tokens=a_in, output_tokens=a_out,
            ))

        retrieved_context["usage_metrics"] = usage_metrics
        retrieved_context["usage_by_model"] = usage_by_model
        retrieved_context["service_calls"] = service_calls

        return retrieved_context

    except ByokKeyRejected:
        # The owner's key is dead AND they are over their usage limit, so no
        # platform-key fallback was attempted (chat/byok_fallback.py). Must
        # propagate as an exception, not the error-dict shape below: this is the
        # one failure mode with an action the user can take (fix the key, or
        # upgrade), and routers/chat.py needs the real exception type to render
        # that as a 422 instead of a generic 500/error frame.
        raise
    except Exception as e:
        logger.error(f"Error in LangGraph RAG agent: {e}", exc_info=True)
        return {
            "error": str(e),
            "summaries": {},
            "chunks": [],
        }
