"""Ask an NL2SQL agent one question, for a report run.

The counterpart of `chat_client` for NL2SQL agents. It exists because the chat service cannot
answer for them at all: NL2SQL is the system of record for agents created through
`POST /nl2sql/api/v1/agents`, and the chat service 404s on any such agent
(`No agent found for service_id …`).

ENDPOINT: `POST {NL2SQL_CHAT_BASE}/v1/agents/{agent_id}/chat`

Three differences from the chat-service client, each forced by the endpoint:

1. **agent_id is in the PATH**, not the body — so `CHAT_SERVICE_BASE` + a static
   `CHAT_SERVICE_QUERY_PATH` cannot express this URL.
2. **`debug_info: true` is required.** Without it the response carries prose only; the executed
   SQL, the tables and the result table are all inside `debug_info`, so a report built without
   it would show answers with no evidence behind them.
3. **The response shape is NL2SQL's own** — `response` / `debug_info` / `chart_output`, not the
   chat service's `data.nl2sql` / `data.knowledge`.

The endpoint returns a single JSON body unless `?stream=true`, which we never send, so there is
no SSE fallback to handle here.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import requests

from services import celery_setup_env
from services.celery_logger import logger


def _url(agent_id: str) -> str:
    base = (celery_setup_env.NL2SQL_CHAT_BASE or "").rstrip("/")
    return f"{base}/v1/agents/{agent_id}/chat"


def _headers() -> Dict[str, str]:
    # NL2SQL's own master key. `FASTAPI_SECRET_KEY` here is the dashboard's, and NL2SQL validates
    # against its own — in production they are different values, so reusing it would 401.
    key = celery_setup_env.NL2SQL_API_KEY or ""
    return {"x-api-key": key, "Content-Type": "application/json"}


def _compose_query(question: str, report_instructions: Optional[str]) -> str:
    """Fold report-level instructions into the question.

    NL2SQL's `ChatRequest` has no instructions field and ignores unknown ones, so prefixing the
    query is the only way to apply them — and it keeps them visible in `nl2sql_logs`, where the
    turn is recorded exactly as the agent saw it.
    """
    if not report_instructions:
        return question
    return f"{report_instructions.strip()}\n\n{question}"


def _execution_details(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Assemble the execution trace from `debug_info` + `chart_output`.

    Returns None when the response carries neither — which also happens when the agent has
    `show_sql_query` / `show_db_result` turned off. That is intended: a report gets no more
    visibility into an agent than its chat UI has.
    """
    debug = body.get("debug_info") or {}
    chart = body.get("chart_output") or {}
    if not isinstance(debug, dict):
        debug = {}
    if not isinstance(chart, dict):
        chart = {}
    if not debug and not chart:
        return None

    return {
        "sql_query": debug.get("sql_query"),
        # Already narrowed to the tables the emitted SQL actually references.
        "tables_used": debug.get("selected_tables") or [],
        "db_result": debug.get("db_result"),
        "export_url": debug.get("export_url"),
        "refined_query": debug.get("refined_query"),
        "selected_db_id": debug.get("selected_db_id"),
        "selected_db_name": debug.get("selected_db_name"),
        "cross_db": debug.get("cross_db"),
        "chart_suggestion": chart.get("chart_suggestion"),
        "chart_data": chart.get("chart_data"),
    }


def _empty_envelope() -> Dict[str, Any]:
    return {
        "answer": "",
        "session_id": None,
        "chat_id": None,
        "execution_details": None,
        "is_answered": None,
        "token_usage": None,
    }


def ask_agent(
    *,
    agent_id: str,
    question: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    report_instructions: Optional[str] = None,
    **_ignored: Any,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Send one question to an NL2SQL agent.

    Returns `(envelope, error)` with the same convention as `chat_client.ask_agent`: the envelope
    always carries every key the result contract needs, and on failure `error` is a non-empty
    string with an empty `answer`, so the runner records a failed query rather than a missing one.

    `**_ignored` absorbs arguments meaningful only to the chat-service client (e.g.
    `source_filter`), so the runner can call either client through one signature.
    """
    payload: Dict[str, Any] = {
        "query": _compose_query(question, report_instructions),
        # Required — see the module docstring. Without it there is no SQL and no result table.
        "debug_info": True,
        "chart_output": True,
        # Follow-up suggestions are for interactive users; generating them would spend a model
        # call per query on something no report reads.
        "follow_up": False,
    }
    if session_id:
        payload["session_id"] = session_id
    if user_id:
        # Honored because we authenticate with the master key: NL2SQL attributes the turn to the
        # report's owner instead of to "the worker".
        payload["user_id"] = user_id

    start = time.monotonic()
    error: Optional[str] = None
    body: Dict[str, Any] = {}
    timeout = celery_setup_env.NL2SQL_REPORTS_QUERY_TIMEOUT_SECONDS
    try:
        resp = requests.post(
            _url(agent_id),
            headers=_headers(),
            json=payload,
            # A ceiling, unlike the chat-service client: a hung request would otherwise hold a
            # concurrency slot for the whole SQS visibility timeout.
            timeout=(10, timeout),
        )
        if resp.status_code != 200:
            error = f"nl2sql chat {resp.status_code}: {resp.text[:300]}"
            logger.warning(f"[reports.nl2sql-chat] ask_agent failed ({error})")
        else:
            try:
                body = resp.json() or {}
            except ValueError as e:
                error = f"nl2sql chat returned non-JSON: {e}"
                logger.warning(f"[reports.nl2sql-chat] {error}")
    except requests.Timeout:
        error = f"nl2sql chat timed out after {timeout}s"
        logger.warning(f"[reports.nl2sql-chat] {error}")
    except requests.RequestException as e:
        error = f"nl2sql chat request error: {e}"
        logger.warning(f"[reports.nl2sql-chat] {error}")

    elapsed_ms = int((time.monotonic() - start) * 1000)

    envelope = _empty_envelope()
    if body:
        envelope.update({
            "answer": body.get("response") or "",
            "session_id": body.get("session_id"),
            "chat_id": body.get("chat_id"),
            "execution_details": _execution_details(body),
            "is_answered": body.get("is_answered"),
            # Token usage is not in the chat response — NL2SQL writes it to nl2sql_logs when the
            # turn is persisted, and backfills it from there by `chat_id` when storing the
            # result. Sending null is correct; the worker has no access to that table.
            "token_usage": None,
        })
        # An empty answer on HTTP 200 is still a failed query: a blank row counted as a success
        # is the one outcome nobody can debug from the report.
        if not error and not envelope["answer"]:
            error = "agent returned an empty answer"

    envelope["response_time_ms"] = elapsed_ms
    return envelope, error
