"""RAG evaluation — the IO half: fetch stored turns, judge them, persist scores.

`rag_eval.py` holds the pure logic (the owner gate, Tier 0, the rubric, parsing);
this module does the fetching and the writing. Same split as
`validation_runner.py` against `extraction_validator.py`, and for the same
reason: the scoring rules stay testable with no database, no network and no LLM.

WHY THIS IS A SEPARATE PASS, NOT INLINE IN THE CHAT REQUEST
-----------------------------------------------------------
A judge call inline would add seconds to a user-facing chat turn for a number
only the analytics screen reads — the identical objection that put extraction
validation in its own pass. Three consequences follow, all of them wanted:

* Chat latency is untouched. Tier 0 is inline because it is free; Tier 1 is not.
* It is re-runnable. A better rubric, or a new metric, re-scores history rather
  than only applying to future turns.
* It can be sampled. Scoring every turn is neither affordable nor useful; this
  runner takes a window and a cap, and judges the NEWEST unscored turns first
  (`_candidate_turns`). Newest-first is recency, not importance — biasing the
  order toward suspicious turns (lowest `context_utilization` first) needs the
  Tier 0 columns populated, which is why it is still a known gap.

WHAT IT SCORES AGAINST
----------------------
The evidence the agent CITED, read back from `knowledge_chat_logs.service_data`
(`summaries` + `detailed_chunks` — what the live response returned). That is the
right denominator for faithfulness: "is this answer grounded in what it used".

It is the WRONG denominator for context relevance, which asks whether everything
retrieved was relevant — and the retrieved-but-uncited evidence is not stored
anywhere. So context relevance is NOT scored here. Tier 0's
retrieved/cited counts (migration 048) are what cover that ground, structurally
rather than by judgement.

THE OWNER GATE
--------------
Every path checks `rag_judge_enabled_for(owner_id)`, which requires BOTH the
owner allowlist and RAG_EVAL_JUDGE_ENABLED. Empty allowlist is the default, so
this module cannot spend anything until an owner is named.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

from sqlalchemy import String, and_, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from chat.byok_fallback import run_with_key_fallback
from langsmith import traceable

from utils.knowledge_health.rag_eval import (
    JUDGE_SCORE_KEYS,
    JUDGE_SYSTEM_PROMPT,
    RagJudgeResult,
    build_judge_prompt,
    flatten_cited_context,
    judge_provider_model,
    rag_judge_enabled_for,
    score_rows_for_turn,
    scores_from_judge_result,
)
from utils.logger import logger
from utils.models.db_tables import Document as DOC
from utils.models.db_tables import KnowledgeChatLogs as CL
from utils.models.db_tables import KnowledgeHealthEvent as HE
from utils.models.db_tables import KnowledgeConfig as KC
from utils.models.db_tables import KnowledgeQualityScore as QS
from utils.setup_env import RAG_EVAL_MAX_TURNS


async def resolve_owner_id(session, knowledge_id: str) -> Optional[str]:
    """The knowledge base's owner, for the gate.

    Resolved from `knowledge_config` rather than taken from the caller: the gate
    decides who is BILLED for judge calls, and a caller-supplied owner_id would
    let any authorised user opt a knowledge base they can read into someone
    else's spend. `knowledge_chat_logs` carries `user_id`, not `owner_id`, so the
    config row is the only authority here.
    """
    return (await session.execute(
        select(KC.owner_id).where(KC.knowledge_id == knowledge_id)
    )).scalars().first()


def _evidence_from_log(log: CL) -> str:
    """The cited evidence for one stored turn, as one text block.

    `service_data` is JSONB but has historically also been written as a JSON
    string, so both shapes are handled — a string that fails to parse yields no
    context, which withholds faithfulness rather than scoring it against nothing.
    """
    raw = log.service_data
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return ""
    if not isinstance(raw, dict):
        return ""
    return flatten_cited_context(raw.get("summaries"), raw.get("detailed_chunks"))


def _scored_exists():
    """EXISTS() correlated subquery: this chat log already carries a judge score.

    ONE definition of "already scored", used by both the counter
    (`_window_totals`) and the selector (`_candidate_turns`). They were two
    predicates in two places once — a SQL count and a Python set difference —
    and the selector's copy is what let an already-scored page hide the
    unscored turns behind it (see `_candidate_turns`). A denominator and the
    rows it counts must not be able to disagree.

    Matches ANY judged dimension rather than all three: a turn whose
    faithfulness was withheld still got its judge call, and paying for it twice
    to re-derive the same withheld result is waste.
    """
    return select(QS.subject_id).where(and_(
        QS.subject_type == "chat_turn",
        QS.source == "judge",
        QS.subject_id == cast(CL.chat_id, String),
    )).exists()


def _answered_in_window(knowledge_id: str, since: datetime, until: datetime):
    """The window predicate every query here shares: answered turns with text.

    An unanswered turn has no answer to score, and a judge asked to grade an
    empty string returns noise. Those turns are already counted by
    `quality.success_rate` on the scorecard, which is the right home for them.
    """
    return and_(
        CL.knowledge_id == knowledge_id,
        CL.created_at >= _naive_utc(since),
        CL.created_at < _naive_utc(until),
        CL.is_answered.is_(True),
        CL.response.isnot(None),
    )


def _naive_utc(dt: datetime) -> datetime:
    """Convert to UTC, then drop tzinfo — for comparing against a naive column.

    `KnowledgeChatLogs.created_at` is a NAIVE timestamp (db_tables.py, plain
    `default_factory=datetime.now`), while `_resolve_window` hands back tz-AWARE
    bounds. Those bounds are right for `knowledge_health_events`, whose column is
    TIMESTAMPTZ and carries an explicit `timezone=True` for exactly this reason —
    so reusing the shared window helper against the chat-logs table silently
    crosses the two conventions.

    Postgres does not tolerate the mix: asyncpg raises DataError, "can't subtract
    offset-naive and offset-aware datetimes", and the endpoint 500s. Hit on prod
    2026-08-28, on the first call after the gate was actually open. A 500 is the
    good outcome here — the alternative would have been a silently empty window.

    The stored naive values are UTC (the container runs UTC; the sessions API
    returns times matching the UTC wall clock), so converting before dropping
    tzinfo keeps the window aligned instead of shifting it by the offset.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


async def _window_totals(session, knowledge_id: str, since: datetime,
                         until: datetime) -> tuple:
    """True `(answered_turns, not_yet_scored)` for the window — counted, uncapped.

    Counted in the database rather than inferred from the fetched page, because
    a page is bounded by `cap` and the window is not. So `len(candidates)`
    answers "how many did we look at", not "how many exist", and the two
    diverge exactly when the window is bigger than one page.

    Reporting the page size instead produced a live wrong answer on 2026-08-28:
    with `limit=1` the endpoint fetched 4 rows, found all 4 already scored, and
    returned `matched=4, remaining=0` for a window that still held a dozen
    unscored turns. `remaining` exists precisely so a partial pass cannot be read
    as a complete one, so a `remaining` that under-reports is worse than none —
    it is a denominator that quietly lies, which is the failure this whole
    measurement effort is meant to remove rather than reproduce.
    """
    base = _answered_in_window(knowledge_id, since, until)
    total = (await session.execute(
        select(func.count()).select_from(CL).where(base)
    )).scalar_one()
    unscored = (await session.execute(
        select(func.count()).select_from(CL).where(and_(base, ~_scored_exists()))
    )).scalar_one()
    return int(total), int(unscored)


async def _candidate_turns(session, knowledge_id: str, since: datetime,
                           until: datetime, cap: int, *,
                           only_unscored: bool = True) -> List[CL]:
    """The turns to judge on this call: answered, in the window, newest first.

    THE UNSCORED FILTER IS IN SQL, NOT IN PYTHON AFTERWARDS. It used to
    over-fetch `cap * 4` newest turns and drop the scored ones from that page,
    which capped a backfill at the newest `cap * 4` turns of any window: once
    those were all judged, every later call returned `judged=0` while
    `remaining` correctly reported dozens still unscored, and no number of
    re-runs could reach them. Filtering in the query makes each call pick up
    exactly where the last one stopped, which is what `only_unscored=True`
    promises the caller.

    `only_unscored=False` deliberately re-scores — after a rubric change — so it
    takes the newest `cap` turns whether or not they carry a score.
    """
    where = _answered_in_window(knowledge_id, since, until)
    if only_unscored:
        where = and_(where, ~_scored_exists())
    return (await session.execute(
        select(CL).where(where).order_by(CL.created_at.desc()).limit(cap)
    )).scalars().all()


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    """`chat_id` as a UUID, or None when it is not one.

    `knowledge_health_events.chat_id` is a UUID column while the ids arrive here
    as strings, and a malformed one must skip the row rather than fail the batch:
    a trace annotation is best-effort and never worth losing scores over.
    """
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _langsmith_run_ids(session, chat_ids: List[str]) -> Dict[str, str]:
    """`chat_id -> langsmith_run_id`, for attaching scores to the chat turn's trace.

    The id lives on `knowledge_health_events` (migration 045), not on
    `knowledge_chat_logs`, so the judge has to look it up rather than read it off
    the turn it is scoring.

    Note this is a real LangSmith run id, NOT `trace_id` — that column holds an
    OpenTelemetry span id and resolves to nothing in LangSmith. The two look
    interchangeable and are not; see modules/analytics-data-sources.md.
    """
    if not chat_ids:
        return {}
    rows = (await session.execute(
        select(HE.chat_id, HE.langsmith_run_id).where(and_(
            HE.chat_id.in_([_as_uuid(c) for c in chat_ids if _as_uuid(c)]),
            HE.langsmith_run_id.isnot(None),
        ))
    )).all()
    return {str(cid): rid for cid, rid in rows if rid}


@lru_cache(maxsize=1)
def _langsmith_client():
    """One LangSmith client for the process, built on first use.

    Per-turn construction re-read the environment and re-built a connection pool
    for every score posted. Cached rather than module-level so importing this
    module still costs nothing when the judge never runs — which is the default
    configuration.
    """
    from langsmith import Client

    return Client()


def _post_feedback(run_id: str, scores: Dict[str, Any], *, chat_id: str) -> int:
    """The blocking half of the trace annotation: the actual HTTP posts."""
    posted = 0
    client = _langsmith_client()
    for key in JUDGE_SCORE_KEYS:
        value = scores.get(key)
        # A withheld dimension posts NO feedback, for the same reason it
        # writes no row: absent and 0.0 are different claims.
        if value is None:
            continue
        client.create_feedback(run_id, key=key, score=float(value),
                               comment=f"rag-eval tier1 · {scores.get('grader_model') or 'judge'}")
        posted += 1
    return posted


async def _attach_to_chat_trace(run_id: Optional[str], scores: Dict[str, Any],
                                *, chat_id: str) -> int:
    """Post the judge's scores as feedback on the turn's own KnowledgeChat run.

    Why feedback rather than a child span: Tier 1 runs from a separate HTTP
    request, minutes or hours after the turn. That trace is closed and its root
    run finished, so there is nothing left to nest under — a span created now
    would become its own floating root, which is exactly the "follow-up landed
    as a separate trace" problem this service already fixed once for the
    follow-up node. Feedback is how LangSmith represents a score attached to a
    finished run, so the numbers show up ON `KnowledgeChat` where someone
    reading the turn will actually find them.

    Off the event loop, via `to_thread`: `create_feedback` is the LangSmith SDK's
    SYNCHRONOUS client, so calling it directly from this async endpoint blocked
    the whole worker for up to three HTTP round trips PER JUDGED TURN — every
    other request served by that worker waited on an analytics annotation.

    Best-effort by design. The database row is the record of truth; a trace
    annotation that fails must never cost a score that was already computed and
    persisted. Returns how many were posted, so a silent no-op is countable
    rather than invisible.
    """
    if not run_id:
        return 0
    try:
        return await asyncio.to_thread(_post_feedback, run_id, scores, chat_id=chat_id)
    except Exception as e:
        logger.warning(
            f"[rag_eval] could not attach scores to run {run_id} "
            f"(chat_id={chat_id}): {type(e).__name__}: {e}"
        )
        return 0


async def judge_one_turn(log: CL, *, owner_id: Optional[str],
                         knowledge_id: str) -> Dict[str, Any]:
    """Score one stored turn. Returns the score dict, or an `error` key.

    Fails to NO SCORE, never to a passing one. The retrieval verifier fails
    *open* (defaults to sufficient) because its job is deciding whether to retry
    and a wrong default costs one unverified answer. Here the opposite applies:
    this whole area exists because a metric that always said "fine" hid a real
    defect, so a judge failure that recorded a score would reintroduce exactly
    that bug.
    """
    answer = (log.response or "").strip()
    if not answer:
        return {"error": "no answer text"}

    context = _evidence_from_log(log)
    provider, model = judge_provider_model()
    prompt = f"{JUDGE_SYSTEM_PROMPT}\n\n{build_judge_prompt(log.query or '', answer, context)}"

    # Structured output built inside the callable so a retry after a rejected
    # BYOK key rebuilds the wrapper on the platform-key model — same reason
    # extraction_validator.py and retrieval_verifier.py both do it this way.
    async def _invoke(llm):
        return await llm.with_structured_output(RagJudgeResult).ainvoke(prompt)

    @traceable(name="RagEval.Tier1Judge", run_type="chain",
               tags=["rag-eval", "tier1"],
               # The prompt already contains the query, answer and cited evidence,
               # and the nested chat-model run uploads all of it. Recording it on
               # the parent too would store every judged answer twice.
               process_inputs=lambda _i: {
                   "chat_id": str(log.chat_id),
                   "knowledge_id": knowledge_id,
                   "prompt_chars": len(prompt),
                   "context_chars": len(context),
                   "has_context": bool(context.strip()),
               })
    async def _judge_traced():
        return await run_with_key_fallback(
            _invoke, owner_id=owner_id, knowledge_id=knowledge_id,
            source="rag_eval",
            provider=provider, model=model,
            temperature=0, for_function_calling=False,
        )

    t0 = time.perf_counter()
    try:
        outcome = await _judge_traced()
    except Exception as e:
        logger.error(
            f"[rag_eval] judge failed for chat_id={log.chat_id}: "
            f"{type(e).__name__}: {e}"
        )
        return {"error": f"{type(e).__name__}: {e}"}

    scores = scores_from_judge_result(outcome.value, has_context=bool(context))
    scores["judge_ms"] = int((time.perf_counter() - t0) * 1000)
    # The model that ACTUALLY served the call, not the one requested — a BYOK
    # fallback can substitute, and attributing a score to the wrong grader is
    # the same class of error as attributing an extraction to the wrong model.
    scores["grader_model"] = getattr(outcome, "model", None) or model
    if not context:
        logger.info(
            f"[rag_eval] chat_id={log.chat_id} cited no evidence — faithfulness "
            f"withheld, relevance and coherence still scored"
        )
    return scores


async def _persist(session, knowledge_id: str, chat_id: str,
                   scores: Dict[str, Any]) -> int:
    """Upsert one turn's score rows. Returns how many rows were written.

    ON CONFLICT UPDATE on (subject_type, subject_id, name, source): a re-run with
    an improved rubric replaces the old opinion rather than appending a second
    one that every average would double-count.

    `created_at` is deliberately in the SET list, so it reads as "when this
    opinion was produced" rather than "when this subject was first judged".
    The row IS the current opinion; there is no history here to date. If a
    re-scoring history is ever wanted, it needs a second row (a distinct
    `source`), not a reinterpretation of this timestamp.
    """
    rows = score_rows_for_turn(
        knowledge_id=knowledge_id, chat_id=chat_id, scores=scores,
        judge_model=scores.get("grader_model"),
    )
    for row in rows:
        stmt = pg_insert(QS.__table__).values(**row)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=["subject_type", "subject_id", "name", "source"],
            set_={
                "value": stmt.excluded.value,
                "grader_model": stmt.excluded.grader_model,
                "comment": stmt.excluded.comment,
                "observations": stmt.excluded.observations,
                "created_at": stmt.excluded.created_at,
            },
        ))
    return len(rows)


async def judge_window(
    session,
    knowledge_id: str,
    since: datetime,
    until: datetime,
    *,
    owner_id: Optional[str] = None,
    only_unscored: bool = True,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Judge the answered turns in a window, newest first.

    `only_unscored=True` (the default) is what makes this safe to call
    repeatedly: already-judged turns are skipped, so a second call picks up only
    what is new. Pass False to deliberately re-score — after a rubric change, for
    instance.

    The response always reports `remaining`, so a caller can see a window was
    only partly covered rather than reading a capped pass as a complete one.
    """
    # Resolve from the KB, not the caller — see resolve_owner_id. An explicit
    # owner_id is honoured only as a fallback for a KB with no config row.
    resolved_owner = await resolve_owner_id(session, knowledge_id) or owner_id
    if not rag_judge_enabled_for(resolved_owner):
        # Not an error: the default configuration is "off for everyone", and a
        # caller asking for a disabled feature should get a clear no-op rather
        # than a 500 or a silent empty success.
        return {
            "enabled": False,
            "judged": 0, "skipped": 0, "failed": 0,
            "matched": 0, "remaining": 0, "scores_written": 0,
            "note": ("RAG judge disabled for this owner — set RAG_EVAL_OWNER_IDS "
                     "and RAG_EVAL_JUDGE_ENABLED"),
            "results": [],
        }

    cap = limit or RAG_EVAL_MAX_TURNS
    # Uncapped truth for the denominators, before the bounded page below.
    total_turns, unscored_turns = await _window_totals(session, knowledge_id, since, until)
    # Already-scored turns are excluded IN SQL, so this page is `cap` turns that
    # will actually be judged rather than `cap` turns that might be — see
    # `_candidate_turns` for the backfill that stalled when it was the latter.
    batch = await _candidate_turns(session, knowledge_id, since, until, cap,
                                   only_unscored=only_unscored)

    # One lookup for the whole batch: each turn's KnowledgeChat run id, so its
    # scores can be attached to the trace a reader already opens to inspect that
    # turn, instead of living only in a table they would have to know about.
    run_ids = await _langsmith_run_ids(session, [str(l.chat_id) for l in batch])
    judged = failed = scores_written = traced = 0
    results: List[Dict[str, Any]] = []

    for log in batch:
        scores = await judge_one_turn(log, owner_id=resolved_owner, knowledge_id=knowledge_id)
        if scores.get("error"):
            failed += 1
            results.append({"chat_id": str(log.chat_id), "error": scores["error"]})
            continue
        written = await _persist(session, knowledge_id, str(log.chat_id), scores)
        # Committed per turn, not once at the end: the trace annotation below
        # claims a score exists, so the row it refers to has to be durable
        # before it is posted. A batch-level commit also meant a failure on turn
        # 25 discarded the 24 judge calls already paid for.
        await session.commit()
        judged += 1
        scores_written += written
        # After committing, never before: the row is the record, the trace
        # annotation is a convenience layered on top of it.
        run_id = run_ids.get(str(log.chat_id))
        traced += await _attach_to_chat_trace(run_id, scores, chat_id=str(log.chat_id))
        results.append({
            "chat_id": str(log.chat_id),
            "langsmith_run_id": run_id,
            "faithfulness": scores.get("faithfulness"),
            "answer_relevance": scores.get("answer_relevance"),
            "coherence": scores.get("coherence"),
            "refused": scores.get("refused"),
            "unsupported_claims": scores.get("unsupported_claims"),
            "grader_model": scores.get("grader_model"),
            "judge_ms": scores.get("judge_ms"),
        })

    logger.info(
        f"[rag_eval] knowledge_id={knowledge_id} judged={judged} failed={failed} "
        f"scores_written={scores_written} traced={traced} "
        f"remaining={max(0, unscored_turns - judged)}"
    )
    return {
        "enabled": True,
        "judged": judged,
        # Judge calls that errored. ABSENT FROM THIS DICT UNTIL 2026-08-28, while
        # both the module docstring and the flow doc told readers to check it
        # before concluding a window had nothing to judge — so the one counter
        # that distinguishes "a quiet window" from "a judge failing on every
        # call" was computed, logged, and then dropped on the way out. A
        # systematic outage read as `judged=0` with no explanation anywhere in
        # the response.
        "failed": failed,
        # Turns in the WINDOW that already carried a score, so this call passed
        # over them — a window count like `matched` and `remaining` beside it,
        # never a page count, or the three would be answering questions about
        # different populations. A large number here means the window is
        # covered, not that the call failed. Zero when `only_unscored=False`,
        # which skips nothing by definition.
        "skipped": (total_turns - unscored_turns) if only_unscored else 0,
        # Window totals, not page sizes — see _window_totals for why these are
        # counted separately. `matched` is every answered turn in the window;
        # `remaining` is what is still unscored after this call.
        "matched": total_turns,
        "remaining": max(0, unscored_turns - judged) if only_unscored else max(0, total_turns - judged),
        "scores_written": scores_written,
        # Feedback entries attached to the turns' own KnowledgeChat runs. Lower
        # than scores_written when a turn predates migration 045, when tracing
        # was off for that turn, or when a dimension was withheld — all of which
        # are expected, so this is reported rather than asserted.
        "scores_traced": traced,
        "grader_model": judge_provider_model()[1],
        "results": results,
    }

# ── the read side ─────────────────────────────────────────────────────────────
# POST above PRODUCES scores; everything below only READS them. Kept in this
# module rather than analytics.py because both halves query
# `knowledge_quality_scores` through the same two predicates (`_scored_exists`,
# `_answered_in_window`) — splitting them across modules is how a writer and a
# reader end up disagreeing about which turns count.

_BAND_PERFECT = 1.0
_BAND_WEAK = 0.7


def _summarise(name: str, values: List[float]) -> Dict[str, Any]:
    """One dimension's aggregate. Bands, not a fixed histogram: 1.0 means every
    claim was supported and under 0.7 is the band worth opening turns from, so
    those two edges carry meaning that arbitrary deciles would not."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {
        "name": name,
        "observations": n,
        "mean": round(sum(ordered) / n, 4),
        "median": round(median, 4),
        "min": ordered[0],
        "max": ordered[-1],
        "perfect": sum(1 for v in ordered if v >= _BAND_PERFECT),
        "partial": sum(1 for v in ordered if _BAND_WEAK <= v < _BAND_PERFECT),
        "weak": sum(1 for v in ordered if v < _BAND_WEAK),
    }


async def read_scores(session, knowledge_id: str, since: datetime, until: datetime,
                      *, include_turns: bool = True, limit: int = 50) -> Dict[str, Any]:
    """Read back the Tier 1 scores for a window. Never calls a judge.

    Ungated on purpose. The owner allowlist exists to control SPEND, and this
    path spends nothing — gating a read behind it would hide numbers already
    paid for from the person who paid for them.

    The window is applied to the CHAT TURN's `created_at`, not the score row's:
    a re-scored turn keeps its place in the window it happened in, or a rubric
    change would silently move history between periods.
    """
    turns = (await session.execute(
        select(CL.chat_id, CL.created_at, CL.query)
        .where(_answered_in_window(knowledge_id, since, until))
        .order_by(CL.created_at.desc())
    )).all()
    answered = len(turns)
    by_id = {str(t.chat_id): t for t in turns}
    if not by_id:
        return {"answered_turns": 0, "judged_turns": 0, "coverage_pct": 0.0,
                "dimensions": [], "grader_models": {}, "turns": []}

    rows = (await session.execute(
        select(QS.subject_id, QS.name, QS.value, QS.grader_model, QS.comment)
        .where(and_(
            QS.subject_type == "chat_turn",
            QS.source == "judge",
            QS.subject_id.in_(list(by_id.keys())),
        ))
    )).all()

    per_dim: Dict[str, List[float]] = {}
    per_turn: Dict[str, Dict[str, Any]] = {}
    graders: Dict[str, int] = {}
    for r in rows:
        if r.value is None:          # the CHECK constraint forbids it; belt and braces
            continue
        per_dim.setdefault(r.name, []).append(float(r.value))
        t = per_turn.setdefault(r.subject_id, {"scores": {}, "grader_model": None, "comment": None})
        t["scores"][r.name] = float(r.value)
        t["grader_model"] = t["grader_model"] or r.grader_model
        t["comment"] = t["comment"] or r.comment
        if r.grader_model:
            graders[r.grader_model] = graders.get(r.grader_model, 0) + 1

    dimensions = [_summarise(name, vals) for name, vals in
                  sorted(per_dim.items(), key=lambda kv: JUDGE_SCORE_KEYS.index(kv[0])
                         if kv[0] in JUDGE_SCORE_KEYS else 99)]

    out_turns: List[Dict[str, Any]] = []
    if include_turns:
        page = list(per_turn.items())[:limit]
        # The judge attaches its scores to the chat turn's LangSmith run as
        # FEEDBACK (`_attach_to_chat_trace`), so there is no separate eval
        # trace to link to — this one id opens the turn and the scores
        # together. Reading it costs one lookup for the page, and without it
        # a score is unauditable: nothing else says which run produced it.
        ls_ids = await _langsmith_run_ids(session, [c for c, _ in page])
        for chat_id, t in page:
            log = by_id.get(chat_id)
            out_turns.append({
                "chat_id": chat_id,
                "created_at": log.created_at.isoformat() if log is not None else None,
                "query": log.query if log is not None else None,
                "scores": t["scores"],
                "grader_model": t["grader_model"],
                "comment": t["comment"],
                # Named explicitly rather than left to the reader to notice a
                # missing key: a dimension absent from `scores` was WITHHELD,
                # which is a measurement decision, not a gap in the data.
                "withheld": [k for k in JUDGE_SCORE_KEYS if k not in t["scores"]],
                # NOT `trace_id`, which is an OpenTelemetry span id and
                # resolves to nothing in LangSmith.
                "langsmith_run_id": ls_ids.get(chat_id),
            })
        out_turns.sort(key=lambda t: t["created_at"] or "", reverse=True)

    judged = len(per_turn)
    return {
        "answered_turns": answered,
        "judged_turns": judged,
        "coverage_pct": round(judged / answered * 100, 1) if answered else 0.0,
        "dimensions": dimensions,
        "grader_models": graders,
        "turns": out_turns,
    }

def _cited_doc_ids(log: CL) -> Dict[str, int]:
    """`{doc_id: chunks cited from it}` for one stored turn.

    Read from the same `service_data` the judge scores against — `summaries`
    and `detailed_chunks`, both keyed by document id. A document appearing only
    under `summaries` was answered from its summary alone (the agent reports
    that as `granularity: doc`) and counts as cited with zero chunks, which is
    why the value can be 0 and the key still present.

    RETRIEVED-BUT-UNCITED DOCUMENTS ARE NOT RECOVERABLE HERE, and that is a
    property of the store, not an omission: only cited evidence is persisted.
    So this answers "which documents did answers actually stand on", never
    "which documents did retrieval consider".
    """
    raw = log.service_data
    if isinstance(raw, str):
        import json as _json
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for doc_id in (raw.get("summaries") or {}):
        out.setdefault(str(doc_id), 0)
    for doc_id, chunks in (raw.get("detailed_chunks") or {}).items():
        out[str(doc_id)] = out.get(str(doc_id), 0) + len(chunks or [])
    return out


async def document_rollup(session, knowledge_id: str, since: datetime, until: datetime,
                          *, include_uncited: bool = True) -> Dict[str, Any]:
    """Per-document view of a window: which documents answers stood on, and how
    those answers scored.

    THE POINT OF THIS IS THE JOIN. A score belongs to a turn, and a turn cites
    documents, so a document's "score" is the distribution of the answers that
    used it. A document whose answers are consistently unfaithful is either
    badly chunked, badly parsed, or being retrieved for questions it cannot
    answer — and none of the turn-level metrics can point at it.

    `never_cited` is the other half, and on a real collection it is usually the
    louder number: documents ingested, indexed, and never once used to answer
    anything in the window. It costs storage and retrieval noise and returns
    nothing. Reported here rather than left to be noticed.
    """
    turns = (await session.execute(
        select(CL.chat_id, CL.created_at, CL.query, CL.service_data)
        .where(_answered_in_window(knowledge_id, since, until))
    )).all()

    scores_by_turn: Dict[str, Dict[str, float]] = {}
    if turns:
        rows = (await session.execute(
            select(QS.subject_id, QS.name, QS.value).where(and_(
                QS.subject_type == "chat_turn",
                QS.source == "judge",
                QS.subject_id.in_([str(t.chat_id) for t in turns]),
            ))
        )).all()
        for r in rows:
            if r.value is not None:
                scores_by_turn.setdefault(r.subject_id, {})[r.name] = float(r.value)

    per_doc: Dict[str, Dict[str, Any]] = {}
    for t in turns:
        cited = _cited_doc_ids(t)
        scores = scores_by_turn.get(str(t.chat_id), {})
        for doc_id, chunk_count in cited.items():
            d = per_doc.setdefault(doc_id, {
                "object_id": doc_id, "cited_turns": 0, "judged_turns": 0,
                "cited_chunks": 0, "faithfulness": [], "worst": None, "last_cited": None,
            })
            d["cited_turns"] += 1
            d["cited_chunks"] += chunk_count
            when = t.created_at.isoformat() if t.created_at else None
            if when and (d["last_cited"] is None or when > d["last_cited"]):
                d["last_cited"] = when
            f = scores.get("faithfulness")
            if scores:
                d["judged_turns"] += 1
            if f is not None:
                d["faithfulness"].append(f)
                if d["worst"] is None or f < d["worst"]["faithfulness"]:
                    d["worst"] = {"chat_id": str(t.chat_id), "query": t.query, "faithfulness": f}

    names = {r.object_id: r for r in (await session.execute(
        select(DOC.object_id, DOC.file_name, DOC.created_at, DOC.document_processing_mode)
        .where(and_(DOC.knowledge_id == knowledge_id, DOC.deleted.is_(False)))
    )).all()}

    documents = []
    for doc_id, d in per_doc.items():
        vals = d.pop("faithfulness")
        meta = names.get(doc_id)
        documents.append({
            **d,
            "file_name": meta.file_name if meta else None,
            "processing_mode": meta.document_processing_mode if meta else None,
            # Absent, not zero, when the citing turns were never judged — the
            # document was used, we simply have no opinion on those answers.
            "mean_faithfulness": round(sum(vals) / len(vals), 4) if vals else None,
            "min_faithfulness": min(vals) if vals else None,
            "scored_turns": len(vals),
            "weak_turns": sum(1 for v in vals if v < 0.7),
            # A document the store knows nothing about: cited by a turn but
            # absent from `document` (deleted since, or ingested elsewhere).
            "known_document": meta is not None,
        })
    documents.sort(key=lambda d: (d["mean_faithfulness"] is None,
                                  d["mean_faithfulness"] if d["mean_faithfulness"] is not None else 1,
                                  -d["cited_turns"]))

    never = []
    if include_uncited:
        for object_id, meta in names.items():
            if object_id not in per_doc:
                never.append({"object_id": object_id, "file_name": meta.file_name,
                              "processing_mode": meta.document_processing_mode,
                              "created_at": meta.created_at.isoformat() if meta.created_at else None})
        never.sort(key=lambda d: d["file_name"] or "")

    return {
        "documents_in_collection": len(names),
        "documents_cited": len(per_doc),
        "documents_never_cited": len(never),
        "turns_in_window": len(turns),
        "documents": documents,
        "never_cited": never,
    }

