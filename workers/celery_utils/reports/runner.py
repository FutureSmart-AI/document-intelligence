"""Orchestrate execution of a Batch Query Reports run.

Semantics (spec §3):
- `independent` group → queries run **in parallel**, each its own session.
- `sequential`  group → queries run **in order**, sharing one session_id so
  follow-ups see prior turns.
- Different groups run **concurrently** with each other.

Counts/progress are derived by the owning service from stored results — we don't
report them. We just submit results (in batches) as queries complete.

**Two backends share this engine.** The semantics above are identical for dashboard
reports and NL2SQL reports; only *who to ask* and *where to write* differ. So
`execute_run` takes them as arguments — `chat` (a module exposing `ask_agent`) and
`results_client` (one exposing `submit_results`) — defaulting to the dashboard pair so
every existing caller and test behaves exactly as before.

`max_concurrent_queries` / `max_concurrent_groups` default to None = unlimited, which
is what the dashboard path has always done (the chat service absorbs the load). The
NL2SQL path passes real caps, because there each query runs a full agent pipeline
holding a source-database connection inside the NL2SQL API process.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from typing import Any, Dict, List, Optional

from services.celery_logger import logger

from celery_utils.reports import chat_client, dashboard_client


def _pool_size(requested: int, cap: Optional[int]) -> int:
    """Threads for `requested` units of work, honoring an optional cap.

    Always at least 1: ThreadPoolExecutor rejects max_workers=0, which an empty group would
    otherwise produce.
    """
    size = max(1, requested)
    if cap:
        size = min(size, max(1, cap))
    return size


CANCELLED = "skipped: run was cancelled"


def _ask(chat, query_slot, *, user_id=None, is_cancelled=None, **kwargs):
    """Call `chat.ask_agent`, holding a concurrency slot if one is in force.

    The slot is a semaphore shared by every group of the run, so the cap bounds the run's
    TOTAL in-flight queries. Capping each group's thread pool alone would not: two groups
    of four would still put eight queries on the target service at once.

    `user_id` is forwarded only when the run definition actually carries one (NL2SQL does,
    the dashboard does not), so a dashboard run calls `ask_agent` with exactly the arguments
    it always has.

    The cancel check happens AFTER acquiring the slot, not before waiting for it: on a capped
    run a query can sit in the queue for minutes, and starting it because it was cleared to run
    back then would ignore a cancel issued in between. `is_cancelled` is None for the dashboard
    path, which skips the check entirely.
    """
    if user_id is not None:
        kwargs["user_id"] = user_id
    with (query_slot or nullcontext()):
        if is_cancelled is not None and is_cancelled():
            return {"response_time_ms": 0}, CANCELLED
        return chat.ask_agent(**kwargs)


def _build_result_row(
    *,
    query_id: str,
    group_id: str,
    question: str,
    envelope: Dict[str, Any],
    error: Optional[str],
    session_id: Optional[str],
) -> Dict[str, Any]:
    """Match the spec §5.3 result envelope. `question` is kept locally for
    the analysis transcript but is **not** sent to the dashboard — the
    dashboard already has the query text indexed by query_id.
    """
    row = {
        "query_id": query_id,
        "group_id": group_id,
        "session_id": session_id or envelope.get("session_id"),
        "answer": envelope.get("answer") or None,
        "answer_ref": None,  # inline by default; see spec §7
        "sources_used": envelope.get("sources_used") or [],
        "retrieved_documents": envelope.get("retrieved_documents") or [],
        "retrieved_chunks": envelope.get("retrieved_chunks") or [],
        "relevance_score": envelope.get("relevance_score"),
        "response_time_ms": envelope.get("response_time_ms"),
        "token_usage": envelope.get("token_usage"),
        # SQL/database (nl2sql) agents: executed SQL, tables, DB result and chart
        # data, so the report can show the same execution details as the live chat.
        "execution_details": envelope.get("execution_details"),
        "error": error,
    }
    # NL2SQL-only fields, added only when its client actually supplied them, so the
    # payload posted to the dashboard is byte-for-byte what it has always been.
    for optional_key in ("chat_id", "is_answered"):
        if optional_key in envelope:
            row[optional_key] = envelope[optional_key]
    return row


def _flush(
    run_id: str,
    pending: List[Dict[str, Any]],
    results_client=dashboard_client,
) -> List[Dict[str, Any]]:
    """Strip the local-only `_question` field, submit, return a fresh buffer."""
    to_send: List[Dict[str, Any]] = []
    for row in pending:
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        to_send.append(clean)
    ok = results_client.submit_results(run_id, to_send)
    if not ok:
        # The endpoint is idempotent — keep these in the buffer so
        # the next flush retries them rather than losing them on the floor.
        logger.warning(
            f"[reports.runner] submit_results failed for run {run_id} "
            f"({len(to_send)} rows) — will retry on next flush"
        )
        return pending
    return []


def _execute_independent_group(
    *,
    agent_id: str,
    group: Dict[str, Any],
    report_instructions: Optional[str],
    chat=chat_client,
    user_id: Optional[str] = None,
    max_concurrent_queries: Optional[int] = None,
    query_slot: Optional[threading.BoundedSemaphore] = None,
    is_cancelled=None,
) -> List[Dict[str, Any]]:
    """Run all queries in parallel. Each query gets its own session (no
    shared context). Returns rows in input order so the analysis transcript
    stays readable.
    """
    queries = group.get("queries", []) or []
    group_id = group.get("group_id")
    rows: List[Optional[Dict[str, Any]]] = [None] * len(queries)

    # Uncapped by default — the chat service handles the load, so every query in the
    # group runs in parallel. A cap is passed for NL2SQL, where each query occupies an
    # agent pipeline and a source-database connection.
    workers = _pool_size(len(queries), max_concurrent_queries)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {}
        for idx, q in enumerate(queries):
            fut = pool.submit(
                _ask,
                chat,
                query_slot,
                is_cancelled=is_cancelled,
                agent_id=agent_id,
                question=q.get("text", ""),
                session_id=None,
                user_id=user_id,
                report_instructions=report_instructions,
            )
            future_to_idx[fut] = idx
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            q = queries[idx]
            try:
                envelope, error = fut.result()
            except Exception as e:
                envelope, error = ({"response_time_ms": 0}, f"runner exception: {e}")
            row = _build_result_row(
                query_id=q.get("query_id"),
                group_id=group_id,
                question=q.get("text", ""),
                envelope=envelope,
                error=error,
                session_id=None,
            )
            row["_question"] = q.get("text", "")  # for analysis transcript
            rows[idx] = row
    return [r for r in rows if r is not None]


def _execute_sequential_group(
    *,
    agent_id: str,
    group: Dict[str, Any],
    report_instructions: Optional[str],
    chat=chat_client,
    user_id: Optional[str] = None,
    query_slot: Optional[threading.BoundedSemaphore] = None,
    is_cancelled=None,
) -> List[Dict[str, Any]]:
    """Run queries in order, reusing one session_id so each step sees prior
    turns. Bails out of the remaining steps if any step errors — those rows
    are recorded with a skip error so the user sees what happened.
    """
    queries = group.get("queries", []) or []
    group_id = group.get("group_id")
    rows: List[Dict[str, Any]] = []
    session_id: Optional[str] = None
    aborted_reason: Optional[str] = None
    for q in queries:
        if aborted_reason:
            row = _build_result_row(
                query_id=q.get("query_id"),
                group_id=group_id,
                question=q.get("text", ""),
                envelope={"response_time_ms": 0},
                error=aborted_reason,
                session_id=session_id,
            )
            row["_question"] = q.get("text", "")
            rows.append(row)
            continue
        envelope, error = _ask(
            chat,
            query_slot,
            is_cancelled=is_cancelled,
            agent_id=agent_id,
            question=q.get("text", ""),
            session_id=session_id,
            user_id=user_id,
            report_instructions=report_instructions,
        )
        # Pin the session for downstream steps once we know it
        if not session_id and envelope.get("session_id"):
            session_id = envelope["session_id"]
        row = _build_result_row(
            query_id=q.get("query_id"),
            group_id=group_id,
            question=q.get("text", ""),
            envelope=envelope,
            error=error,
            session_id=session_id,
        )
        row["_question"] = q.get("text", "")
        rows.append(row)
        if error == CANCELLED:
            # Cancelled, not broken — say so rather than blaming the previous step.
            aborted_reason = CANCELLED
        elif error:
            aborted_reason = "skipped: previous step in sequential group failed"
    return rows


def execute_run(
    definition: Dict[str, Any],
    *,
    chat=chat_client,
    results_client=dashboard_client,
    max_concurrent_queries: Optional[int] = None,
    max_concurrent_groups: Optional[int] = None,
    is_cancelled=None,
) -> List[Dict[str, Any]]:
    """Execute every group of a run; flush results back in batches as they
    complete. Returns the per-group transcript used by the analysis step.

    `chat` / `results_client` select the backend — which service to ask and which to write
    to — and default to the dashboard pair, so an existing call with one argument behaves
    exactly as before. The caps default to None = unlimited, also as before.

    `is_cancelled` is an optional zero-arg predicate polled before each query, so cancelling a
    run stops the work rather than only discarding its results. None (the dashboard default)
    means no polling at all.
    """
    run_id = definition["run_id"]
    agent_id = definition.get("agent_id")
    # Only NL2SQL run definitions carry this; it lets the turn be attributed to the report's
    # owner instead of to the worker. The chat-service client ignores it.
    user_id = definition.get("user_id")
    report_instructions = definition.get("report_instructions")
    groups = definition.get("query_groups", []) or []
    if not groups:
        logger.warning(f"[reports.runner] run {run_id} has no groups; nothing to do")
        return []

    group_workers = _pool_size(len(groups), max_concurrent_groups)
    # One slot pool shared by every group, so the query cap bounds the whole run rather
    # than each group separately. None when uncapped (the dashboard path).
    query_slot = (
        threading.BoundedSemaphore(max_concurrent_queries) if max_concurrent_queries else None
    )
    pending: List[Dict[str, Any]] = []
    transcript_by_idx: List[Optional[List[Dict[str, Any]]]] = [None] * len(groups)
    group_types: List[str] = [g.get("type", "independent") for g in groups]
    group_ids: List[Optional[str]] = [g.get("group_id") for g in groups]

    def _run_group(idx: int) -> List[Dict[str, Any]]:
        g = groups[idx]
        if g.get("type") == "sequential":
            return _execute_sequential_group(
                agent_id=agent_id,
                group=g,
                report_instructions=report_instructions,
                chat=chat,
                user_id=user_id,
                query_slot=query_slot,
                is_cancelled=is_cancelled,
            )
        return _execute_independent_group(
            agent_id=agent_id,
            group=g,
            report_instructions=report_instructions,
            chat=chat,
            user_id=user_id,
            max_concurrent_queries=max_concurrent_queries,
            query_slot=query_slot,
            is_cancelled=is_cancelled,
        )

    with ThreadPoolExecutor(max_workers=group_workers) as pool:
        future_to_idx = {pool.submit(_run_group, i): i for i in range(len(groups))}
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                rows = fut.result()
            except Exception as e:
                logger.error(
                    f"[reports.runner] group {group_ids[idx]} crashed: {e}",
                    exc_info=True,
                )
                # Record an error row per query so they count as FAILED rather
                # than silently missing — otherwise the run could be marked
                # 'completed' with these answers absent and no error shown.
                rows = [
                    {
                        "query_id": q.get("query_id"),
                        "group_id": group_ids[idx],
                        "error": f"group execution failed: {e}",
                        "_question": q.get("text", ""),
                    }
                    for q in (groups[idx].get("queries") or [])
                ]
            transcript_by_idx[idx] = rows
            pending.extend(rows)
            # Best-effort mid-stream flush: send each group's results as the
            # group completes so the dashboard's derived progress moves. If the
            # flush fails, leave the rows in `pending` and try again at the end
            # — don't retry in a tight loop here, that would just hammer a sick
            # endpoint.
            pending = _flush(run_id, pending, results_client)

    if pending:
        pending = _flush(run_id, pending)
        if pending:
            # Do NOT return normally — that would let the task mark the run
            # 'completed' while results are missing. Raise so the Celery task
            # retries and re-upserts idempotently (dashboard upsert is safe).
            raise RuntimeError(
                f"run {run_id}: {len(pending)} result rows undelivered after final flush"
            )

    # Build the ordered transcript the analysis step consumes.
    transcript: List[Dict[str, Any]] = []
    for i, rows in enumerate(transcript_by_idx):
        if rows is None:
            continue
        transcript.append({
            "group_id": group_ids[i],
            "type": group_types[i],
            "results": [
                {
                    "question": r.get("_question"),
                    "answer": r.get("answer"),
                    "error": r.get("error"),
                }
                for r in rows
            ],
        })
    return transcript
