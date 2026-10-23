"""Chat Service client for batch query reports.

Sends a single query to the agent and returns a normalized result envelope
the runner can hand straight to the dashboard's `/internal/runs/{run_id}/results`.

ENDPOINT — confirmed via the chat service Swagger UI:
  POST {CHAT_SERVICE_BASE}{CHAT_SERVICE_QUERY_PATH}
       = https://agent.futuresmart.ai/api/chat/v1/chat_completion
Request schema: agent_id, query, session_id, no_of_chunks, use_user_info,
stream, from_widget, chart_output. The endpoint STREAMS by default
(Content-Type: text/event-stream), so we send stream=false to get a single
JSON response. Parsing still tolerates an SSE reply as a fallback.

Confirmed non-streaming response fields: answer in `response`, session in
`session_id`, retrieved knowledge in `data.knowledge.summaries`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from services import celery_setup_env
from services.celery_logger import logger


def _url() -> str:
    base = (celery_setup_env.CHAT_SERVICE_BASE or "").rstrip("/")
    path = celery_setup_env.CHAT_SERVICE_QUERY_PATH or "/v1/chat_completion"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _headers() -> Dict[str, str]:
    key = celery_setup_env.FASTAPI_SECRET_KEY or ""
    return {"x-api-key": key, "Content-Type": "application/json"}


def _parse_sse_stream(raw: str) -> Dict[str, Any]:
    """Reconstruct a response body from an SSE (text/event-stream) reply.

    Fallback for when the endpoint streams despite stream=False. We concatenate
    any textual token fields from each `data:` line into one answer and keep any
    metadata fields (session_id, sources, usage) we recognize. Best-effort:
    shapes vary, so non-JSON data lines are treated as raw answer text.
    """
    answer_parts: List[str] = []
    meta: Dict[str, Any] = {}
    _meta_keys = (
        "session_id", "conversation_id", "sources_used", "sources",
        "retrieved_documents", "retrieved_chunks", "documents",
        "token_usage", "total_tokens", "usage",
    )
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except ValueError:
            answer_parts.append(data)  # plain-text token
            continue
        if isinstance(obj, dict):
            token = (obj.get("answer") or obj.get("content")
                     or obj.get("delta") or obj.get("text") or "")
            if isinstance(token, str):
                answer_parts.append(token)
            meta.update({k: v for k, v in obj.items() if k in _meta_keys})
        elif isinstance(obj, str):
            answer_parts.append(obj)
    if answer_parts and not meta.get("answer"):
        meta["answer"] = "".join(answer_parts)
    return meta


def _chunks_and_relevance(
    body: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """Pull the retrieved text chunks and a relevance score out of the response.

    The chat service returns `data.knowledge.chunks` — each chunk has `text`,
    `document_id`, `topic`, a `relevance_score`, and a huge `metadata` blob. We
    keep only the useful fields (dropping the metadata blob to stay small) and
    derive a top-level `relevance_score` as the highest chunk relevance_score
    (best piece of evidence) — same field name the chat service uses per chunk.
    Returns ([], None) when no chunks are present.
    """
    raw = (((body.get("data") or {}).get("knowledge") or {}).get("chunks") or [])
    if not isinstance(raw, list) or not raw:
        return [], None
    chunks: List[Dict[str, Any]] = []
    scores: List[float] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        meta = c.get("metadata") or {}
        score = c.get("relevance_score")
        if isinstance(score, (int, float)):
            scores.append(float(score))
        chunks.append({
            "chunk_id": meta.get("id"),
            "document_id": c.get("document_id"),
            "file_name": c.get("file_name"),
            "topic": c.get("topic") or c.get("chunk_topic"),
            "text": c.get("text"),
            "relevance_score": score,
        })
    relevance_score = max(scores) if scores else None
    return chunks, relevance_score


def _build_documents(body: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Build the flat source-document list (one entry per document).

    Feeds the dashboard's Sources table. Documents are collected from BOTH
    `data.knowledge.summaries` and `data.knowledge.chunks` (deduped by document
    id), so the list is populated regardless of which field the chat service
    fills for a given query shape (lookup → summaries, fact_lookup → chunks).
    Each entry is flat: id, title, file_name, source_id, page_url. The chunk
    text/relevance is exposed separately via `retrieved_chunks`.

    The source URL field differs by knowledge type — web docs use `page_url`,
    ClickUp docs use `clickup_page_url`, uploaded files have only `file_name` —
    so we fall back through those. Returns (documents, sources) where `sources`
    is the flat label list (URL, else file name).
    """
    knowledge = ((body.get("data") or {}).get("knowledge") or {})
    summaries = knowledge.get("summaries")
    raw_chunks = knowledge.get("chunks")

    docs_by_key: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []

    def _ensure(doc_id: Any, file_name: Optional[str], meta: Dict[str, Any]) -> None:
        key = doc_id or file_name
        if key in docs_by_key:
            return
        url = meta.get("page_url") or meta.get("clickup_page_url") or meta.get("url")
        docs_by_key[key] = {
            "id": doc_id,
            "title": meta.get("title") or file_name,
            "file_name": file_name,
            "source_id": meta.get("source_id"),
            "page_url": url,
        }
        order.append(key)

    if isinstance(summaries, dict):
        for doc_id, entry in summaries.items():
            entry = entry or {}
            meta = entry.get("metadata") or {}
            _ensure(doc_id, entry.get("file_name") or meta.get("file_name"), meta)

    if isinstance(raw_chunks, list):
        for c in raw_chunks:
            if not isinstance(c, dict):
                continue
            meta = c.get("metadata") or {}
            _ensure(c.get("document_id"), c.get("file_name") or meta.get("file_name"), meta)

    docs = [docs_by_key[k] for k in order]
    sources: List[str] = []
    for d in docs:
        label = d.get("page_url") or d.get("file_name")
        if label:
            sources.append(label)
    return docs, sources


def _parse_response(body: Dict[str, Any]) -> Dict[str, Any]:
    """Map the chat service reply to the result envelope the dashboard expects.

    Confirmed against the real /v1/chat_completion response: the answer is in
    `response`, the session in `session_id`, retrieved knowledge in
    `data.knowledge.summaries`, and text chunks (with relevance_score) in
    `data.knowledge.chunks`. Still defensive — falls back to alternate field
    names and empty defaults so a partial response never crashes the run.
    """
    answer = body.get("answer") or body.get("response") or body.get("output") or ""
    session_id = body.get("session_id") or body.get("conversation_id") or body.get("chat_id")

    sources = list(body.get("sources_used") or body.get("sources") or [])
    retrieved = list(
        body.get("retrieved_documents")
        or body.get("documents")
        or []
    )
    if not retrieved:
        # One flat entry per document, built from summaries AND chunks so it's
        # never empty when docs exist (covers every query shape / agent type).
        retrieved, doc_sources = _build_documents(body)
        if not sources:
            sources = doc_sources

    token_usage = (
        body.get("token_usage")
        or body.get("total_tokens")
        or (body.get("usage") or {}).get("total_tokens")
    )

    retrieved_chunks, relevance_score = _chunks_and_relevance(body)
    # Honor an explicit relevance_score (or confidence) field if the service
    # ever sends one at the top level.
    for _k in ("relevance_score", "confidence"):
        if isinstance(body.get(_k), (int, float)):
            relevance_score = float(body[_k])
            break

    # SQL/database (nl2sql) agents put their execution details in data.nl2sql,
    # NOT data.knowledge — so knowledge-based parsing above finds nothing for
    # them. Capture the executed SQL, tables, DB result, export link and chart
    # so batch reports show the same "Data Sources & Execution Details" the
    # live chat does. Requires chart_output=True in the request for chart_data.
    nl2sql = ((body.get("data") or {}).get("nl2sql") or {})
    execution_details = None
    if isinstance(nl2sql, dict) and nl2sql:
        execution_details = {
            "sql_query": nl2sql.get("sql_query"),
            "tables_used": nl2sql.get("tables_used") or [],
            "db_result": nl2sql.get("db_result"),
            "export_url": nl2sql.get("export_url"),
            "chart_suggestion": nl2sql.get("chart_suggestion"),
            "chart_data": nl2sql.get("chart_data"),
        }
        # NOTE: SQL tables are NOT added to `sources_used`. That field feeds the
        # dashboard's knowledge-document Sources table (which labels entries as
        # "file" with a relevance score) — a DB table doesn't belong there. The
        # queried tables are already surfaced via execution_details.tables_used
        # and shown in the "Data Sources & Execution Details" panel.

    return {
        "answer": answer,
        "sources_used": sources,
        "retrieved_documents": retrieved,
        "retrieved_chunks": retrieved_chunks,
        "relevance_score": relevance_score,
        "session_id": session_id,
        "token_usage": token_usage,
        "execution_details": execution_details,
    }


def ask_agent(
    *,
    agent_id: str,
    question: str,
    session_id: Optional[str] = None,
    source_filter: Optional[Dict[str, Any]] = None,
    report_instructions: Optional[str] = None,
    **_ignored: Any,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Send one question to the agent.

    `**_ignored` absorbs arguments that only the NL2SQL client understands (e.g. `user_id`,
    which the chat service derives from the session instead). It exists so `runner.execute_run`
    can call either client through one signature rather than branching per backend.

    Returns (envelope, error). `envelope` is always a dict with the keys the
    dashboard contract expects (answer/sources_used/retrieved_documents/
    session_id/token_usage/response_time_ms). On failure, `error` is a
    non-empty string and `envelope.answer` is empty — the runner forwards
    both to the dashboard so the row records as a failed query (spec §5.3).
    """
    # Matches the chat service's POST /v1/chat_completion schema. stream=False
    # forces a single response instead of the default SSE stream, which is what
    # a batch worker wants (one final answer per query).
    payload: Dict[str, Any] = {
        "agent_id": agent_id,
        "query": question,
        "stream": False,
        "no_of_chunks": celery_setup_env.no_of_chunks,
        "use_user_info": False,
        "from_widget": False,
        # Ask the chat service to also generate chart data (data.nl2sql.chart_data)
        # when the query warrants it, so batch reports can render charts too.
        "chart_output": True,
    }
    if session_id:
        payload["session_id"] = session_id
    # Best-effort extras: the schema allows additional properties, so these are
    # passed through but may be ignored if the chat service doesn't support them.
    if source_filter:
        payload["source_filter"] = source_filter
    if report_instructions:
        # Reports may pin instructions like "Answer concisely, cite sources."
        payload["instructions"] = report_instructions

    start = time.monotonic()
    error: Optional[str] = None
    body: Dict[str, Any] = {}
    try:
        resp = requests.post(
            _url(),
            headers=_headers(),
            json=payload,
        )
        if resp.status_code != 200:
            error = f"chat service {resp.status_code}: {resp.text[:300]}"
            logger.warning(f"[reports.chat] ask_agent failed ({error})")
        elif "text/event-stream" in resp.headers.get("Content-Type", ""):
            # Endpoint streamed despite stream=False — rebuild the answer.
            body = _parse_sse_stream(resp.text)
        else:
            try:
                body = resp.json() or {}
            except ValueError as e:
                error = f"chat service returned non-JSON: {e}"
                logger.warning(f"[reports.chat] {error}")
    except requests.RequestException as e:
        error = f"chat service request error: {e}"
        logger.warning(f"[reports.chat] {error}")
    elapsed_ms = int((time.monotonic() - start) * 1000)

    envelope = _parse_response(body)
    envelope["response_time_ms"] = elapsed_ms
    return envelope, error
