"""Unit tests for RAG evaluation — the owner gate and the scoring rules.

Covers utils/knowledge_health/rag_eval.py, which is pure by design, so nothing
here touches a DB, the network or an LLM.

Why these specifically. Two classes of bug in this module are SILENT — the code
runs, rows are written, and only the numbers are wrong:

  * a gate that leaks. Judge calls cost money per turn, so a gate that admits an
    owner it shouldn't spends someone else's budget with no error anywhere.
  * a withheld score written as 0.0. That reads as "the answer hallucinated" or
    "the extraction was entirely wrong" when the truth is "we did not measure
    it", and it drags every average it enters.

See docs/plan/quality-baselines/rag-metrics.md for what each metric means.
"""

import pytest

from utils.knowledge_health.rag_eval import (
    VERDICT_INSUFFICIENT,
    VERDICT_NOT_RUN,
    VERDICT_SUFFICIENT,
    compute_tier0,
    context_utilization,
    flatten_cited_context,
    parse_judge_response,
    rag_eval_enabled_for,
    rag_judge_enabled_for,
    score_rows_for_turn,
)


# ── the owner gate ───────────────────────────────────────────────────────────

def test_empty_allowlist_is_off_for_everyone():
    """The default. Shipping the feature must change nothing until an owner is
    named, or the first deploy silently starts spending."""
    assert rag_eval_enabled_for("owner@x.com", allowlist="") is False
    assert rag_eval_enabled_for(None, allowlist="") is False


def test_exact_match_admits_and_others_are_refused():
    assert rag_eval_enabled_for("owner@x.com", allowlist="owner@x.com") is True
    assert rag_eval_enabled_for("other@x.com", allowlist="owner@x.com") is False


def test_match_ignores_case_and_surrounding_space():
    """A hand-edited env var carries stray spaces and inconsistent case; neither
    should decide whether a tenant is billed."""
    assert rag_eval_enabled_for("Owner@X.com", allowlist=" owner@x.com , b@x.com ") is True


def test_blank_allowlist_entries_never_admit_anyone():
    """A trailing comma must not create an entry that an empty owner_id matches."""
    assert rag_eval_enabled_for("", allowlist="a@x.com,,  ,") is False
    assert rag_eval_enabled_for(None, allowlist=",,") is False


def test_absent_owner_cannot_match_an_allowlist():
    """A credential-less public caller has no owner. Defaulting one in would
    attribute the spend to whoever happened to be first in the list."""
    assert rag_eval_enabled_for(None, allowlist="a@x.com,b@x.com") is False


def test_wildcard_admits_everyone_including_anonymous():
    assert rag_eval_enabled_for("anyone@x.com", allowlist="*") is True
    assert rag_eval_enabled_for(None, allowlist="*") is True


def test_judge_needs_both_the_owner_gate_and_the_switch():
    """The paid tier is switchable independently, so an owner can get the free
    structural metrics with zero judge spend."""
    assert rag_judge_enabled_for("a@x.com", allowlist="a@x.com", judge_enabled=True) is True
    assert rag_judge_enabled_for("a@x.com", allowlist="a@x.com", judge_enabled=False) is False
    assert rag_judge_enabled_for("b@x.com", allowlist="a@x.com", judge_enabled=True) is False


# ── Tier 0 ───────────────────────────────────────────────────────────────────

def test_cited_ids_are_deduplicated_across_entries():
    """The agent can name the same doc twice with overlapping chunk ids; counting
    them twice would inflate utilization above what was really used."""
    tier0 = compute_tier0({
        "chunks": [1, 2, 3, 4, 5],
        "summaries": {"d1": {}, "d2": {}},
        "agent_output": {"relevant_docs": [
            {"doc_id": "d1", "chunk_ids": ["c1", "c2"]},
            {"doc_id": "d1", "chunk_ids": ["c2"]},
        ]},
    })
    assert tier0["cited_chunk_count"] == 2
    assert tier0["cited_doc_count"] == 1
    assert tier0["retrieved_chunk_count"] == 5
    assert tier0["retrieved_doc_count"] == 2


def test_malformed_relevant_docs_entry_does_not_raise():
    """A metric that only observes must never fail a chat turn, and a model can
    put a bare string where a dict belongs."""
    tier0 = compute_tier0({"agent_output": {"relevant_docs": ["garbage", None, {"doc_id": "d1"}]}})
    assert tier0["cited_doc_count"] == 1


def test_last_verifier_verdict_wins():
    """The loop can run insufficient -> re-plan -> sufficient. The turn's verdict
    is where it ended up, not where it started."""
    tier0 = compute_tier0({"verifier_verdicts": [
        {"sufficient": False, "missing": "owner attribute absent"},
        {"sufficient": True, "missing": ""},
    ]})
    assert tier0["verifier_verdict"] == VERDICT_SUFFICIENT
    assert tier0["verifier_missing"] is None


def test_insufficient_verdict_keeps_what_was_missing():
    tier0 = compute_tier0({"verifier_verdicts": [{"sufficient": False, "missing": "no docs matched"}]})
    assert tier0["verifier_verdict"] == VERDICT_INSUFFICIENT
    assert tier0["verifier_missing"] == "no docs matched"


def test_no_verdict_is_not_run_rather_than_null():
    """The verifier is deliberately skipped on the final attempt, so a
    single-attempt turn legitimately has no verdict. That is a different fact
    from "this column predates the migration"."""
    assert compute_tier0({})["verifier_verdict"] == VERDICT_NOT_RUN


def test_missing_retrieval_data_is_none_not_zero():
    """NULL means not recorded; 0 means nothing was retrieved. Conflating them
    invents a fleet of no-retrieval turns."""
    tier0 = compute_tier0({})
    assert tier0["retrieved_chunk_count"] is None
    assert tier0["cited_chunk_count"] is None


def test_every_path_returns_the_same_keys():
    """The caller spreads this dict straight into a DB row, so a key present on
    one path and absent on another would write inconsistent rows."""
    assert set(compute_tier0({})) == set(compute_tier0({
        "chunks": [1], "summaries": {"d": {}}, "agent_output": {"relevant_docs": []},
        "verifier_verdicts": [{"sufficient": True}], "retrieval_attempts": 1,
    }))


# ── context utilization ──────────────────────────────────────────────────────

def test_utilization_is_the_cited_share():
    assert context_utilization(2, 25) == 0.08


def test_nothing_retrieved_yields_no_opinion_not_zero():
    """A turn that retrieved nothing did not use 0% of its evidence — it had none
    to use. Reporting 0.0 would drag a fleet average down for turns that never
    retrieved at all."""
    assert context_utilization(0, 0) is None
    assert context_utilization(3, 0) is None


def test_utilization_cannot_exceed_one():
    """Defensive: cited ids come from the model, and a model can name a chunk id
    that is not in the retrieved set."""
    assert context_utilization(9, 5) == 1.0


# ── judge parsing and withholding ────────────────────────────────────────────

def test_fenced_json_is_parsed():
    raw = '```json\n{"faithfulness": 0.9, "answer_relevance": 1.0, "coherence": 0.8}\n```'
    assert parse_judge_response(raw, has_context=True)["faithfulness"] == 0.9


def test_out_of_range_scores_are_clamped():
    """A model that ignored the rubric must not pull an average outside the
    metric's own scale, which is harder to spot than a boundary value."""
    parsed = parse_judge_response(
        '{"faithfulness": 1.7, "answer_relevance": -2, "coherence": "not a number"}',
        has_context=True)
    assert parsed["faithfulness"] == 1.0
    assert parsed["answer_relevance"] == 0.0
    assert parsed["coherence"] is None


def test_faithfulness_is_withheld_when_no_context_was_cited():
    """An answer with nothing to check against is not a hallucination. The other
    two dimensions never read the context, so they still stand."""
    parsed = parse_judge_response(
        '{"faithfulness": 0.4, "answer_relevance": 0.9, "coherence": 0.9}',
        has_context=False)
    assert parsed["faithfulness"] is None
    assert parsed["answer_relevance"] == 0.9
    assert parsed["coherence"] == 0.9


def test_unparseable_judge_output_raises():
    """So the caller records a judge failure rather than a score. Failing to a
    number here would reintroduce the always-says-fine bug this area exists to
    fix."""
    with pytest.raises(ValueError):
        parse_judge_response("the answer looks good to me", has_context=True)


def test_unsupported_claims_are_capped():
    raw = ('{"faithfulness": 0.5, "answer_relevance": 1, "coherence": 1, '
           '"unsupported_claims": ["a", "b", "c", "d", "e"]}')
    assert len(parse_judge_response(raw, has_context=True)["unsupported_claims"]) == 3


# ── score rows ───────────────────────────────────────────────────────────────

def test_a_withheld_dimension_writes_no_row():
    """The whole reason for a row-per-score table: "not measured" and "measured
    as zero" become structurally impossible to confuse."""
    scores = {"faithfulness": None, "answer_relevance": 0.9, "coherence": 0.8, "reason": "r"}
    rows = score_rows_for_turn(knowledge_id="kb", chat_id="c1", scores=scores)
    assert {r["name"] for r in rows} == {"answer_relevance", "coherence"}


def test_a_zero_score_does_write_a_row():
    """0.0 is a real measurement — everything checked was unsupported — and must
    not be dropped as though it were absent."""
    rows = score_rows_for_turn(knowledge_id="kb", chat_id="c1",
                               scores={"faithfulness": 0.0})
    assert len(rows) == 1
    assert rows[0]["value"] == 0.0


def test_rows_record_the_grader_and_its_denominator():
    rows = score_rows_for_turn(knowledge_id="kb", chat_id="c1",
                               scores={"coherence": 1.0}, judge_model="claude-sonnet-5")
    assert rows[0]["grader_model"] == "claude-sonnet-5"
    assert rows[0]["source"] == "judge"
    assert rows[0]["observations"] == 1
    assert rows[0]["subject_type"] == "chat_turn"


def test_all_dimensions_withheld_writes_nothing():
    assert score_rows_for_turn(knowledge_id="kb", chat_id="c1", scores={}) == []


# ── context flattening ───────────────────────────────────────────────────────

def test_context_includes_summaries_and_chunks():
    """A turn can legitimately be answered from a document summary alone
    (granularity: doc), so a chunks-only context would mark a correct
    summary-grounded answer as unsupported."""
    ctx = flatten_cited_context({"d1": {"summary": "the summary"}},
                                {"d2": [{"chunk_text": "the chunk"}]})
    assert "[summary d1] the summary" in ctx
    assert "[chunk d2] the chunk" in ctx


def test_long_items_are_truncated_individually():
    """Per item, not globally: one long chunk must not crowd the others out of
    the judge's view, which would read as "unsupported" for claims they support."""
    ctx = flatten_cited_context({"d1": {"summary": "x" * 5000}},
                                {"d2": [{"chunk_text": "short"}]})
    assert "short" in ctx
    assert len(ctx) < 5000


def test_context_includes_document_metadata():
    """The regression that made this a documented failure, not a nicety.

    A large share of answers are grounded in document-level METADATA, not chunk
    text: "which state's law governs this agreement" is answered off the
    `governing_law` enrichment field with zero chunks cited. The value is on the
    turn; it was simply never handed to the judge, which then said — correctly,
    about the context it was given — that governing law is never mentioned, and
    scored a right answer 0.0 faithfulness (chat_id 672eb7e4, 2026-09-01).
    """
    ctx = flatten_cited_context(
        {"d1": {"summary": "A licensing arrangement between two companies.",
                "metadata": {"governing_law": "California",
                             "parties": ["WPT Enterprises, Inc.", "ZYNGA INC."]}}},
        {})
    assert "governing_law: California" in ctx
    assert "WPT Enterprises, Inc." in ctx
    assert "[summary d1] A licensing arrangement" in ctx


def test_metadata_drops_empty_values():
    """Empty fields are not evidence. Padding the context with `field: None`
    spends judge tokens and invites the model to treat an absent value as a
    stated one."""
    ctx = flatten_cited_context(
        {"d1": {"summary": "s", "metadata": {"governing_law": "Texas",
                                             "renewal_term": None,
                                             "topics": [], "notes": ""}}}, {})
    assert "governing_law: Texas" in ctx
    for absent in ("renewal_term", "topics", "notes"):
        assert absent not in ctx


def test_summary_without_metadata_is_unchanged():
    """The common shape must not regress: a summary carrying no metadata, and a
    summary stored as a bare string, both flatten exactly as before."""
    assert flatten_cited_context({"d": {"summary": "x", "metadata": {}}}, {}) == "[summary d] x"
    assert flatten_cited_context({"d": "plain text"}, {}) == "[summary d] plain text"


def test_empty_evidence_flattens_to_empty_string():
    assert flatten_cited_context(None, None) == ""
    assert flatten_cited_context({}, {"d": []}) == ""


# --- The window-bounds timezone mismatch (regression) -----------------------
#
# Production 500 on 2026-08-28, on the very first call made after the owner gate
# was actually open. `_resolve_window` returns tz-AWARE bounds, which are correct
# for `knowledge_health_events` (TIMESTAMPTZ, explicit `timezone=True`). But the
# judge selects from `knowledge_chat_logs`, whose `created_at` is a NAIVE
# timestamp — so reusing the shared helper crossed two conventions and asyncpg
# refused the comparison outright.
#
# Worth a test rather than just a fix, because the failure was loud only by luck:
# had Postgres coerced instead of raising, the window would have silently shifted
# by the UTC offset and the judge would have scored the wrong turns.

class TestNaiveUtc:
    def test_aware_input_becomes_naive(self):
        from datetime import datetime, timezone
        from utils.knowledge_health.rag_eval_runner import _naive_utc

        aware = datetime(2026, 8, 21, 10, 35, 1, tzinfo=timezone.utc)
        out = _naive_utc(aware)
        assert out.tzinfo is None
        # Same instant, not shifted.
        assert out == datetime(2026, 8, 21, 10, 35, 1)

    def test_non_utc_offset_is_converted_not_truncated(self):
        """The bug this guards against is dropping tzinfo WITHOUT converting,
        which moves the window by the offset and quietly changes which turns
        get judged."""
        from datetime import datetime, timedelta, timezone
        from utils.knowledge_health.rag_eval_runner import _naive_utc

        ist = timezone(timedelta(hours=5, minutes=30))
        out = _naive_utc(datetime(2026, 8, 21, 16, 5, 1, tzinfo=ist))
        assert out == datetime(2026, 8, 21, 10, 35, 1)  # 16:05 IST == 10:35 UTC

    def test_naive_input_passes_through(self):
        from datetime import datetime
        from utils.knowledge_health.rag_eval_runner import _naive_utc

        naive = datetime(2026, 8, 21, 10, 35, 1)
        assert _naive_utc(naive) is naive or _naive_utc(naive) == naive
        assert _naive_utc(naive).tzinfo is None


# --- The backfill that could not reach past its first page (regression) ------
#
# `_candidate_turns` used to fetch the newest `cap * 4` answered turns and drop
# the already-scored ones in Python. That made the unscored filter a property of
# ONE PAGE: once the newest `cap * 4` turns of a window were judged, every later
# call returned `judged=0` while `remaining` correctly reported dozens still
# unscored, and no number of re-runs could reach them.
#
# The fix is the filter living in the query, so these tests read the compiled
# SQL rather than a result set — the assertion is about what is asked of
# Postgres, which is exactly where the bug was.

class _StubResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _StubSession:
    """Records the statements handed to it. No DB, no rows — the point of these
    tests is the SQL, not the result."""

    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _StubResult()


def _compiled_candidate_sql(*, only_unscored: bool, cap: int = 25) -> str:
    import asyncio
    from datetime import datetime, timezone

    from sqlalchemy.dialects import postgresql

    from utils.knowledge_health.rag_eval_runner import _candidate_turns

    session = _StubSession()
    asyncio.run(_candidate_turns(
        session, "kb-1",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 28, tzinfo=timezone.utc),
        cap, only_unscored=only_unscored,
    ))
    return str(session.statements[0].compile(dialect=postgresql.dialect()))


class TestCandidateSelection:
    def test_unscored_turns_are_filtered_in_sql(self):
        sql = _compiled_candidate_sql(only_unscored=True)
        assert "NOT (EXISTS" in sql
        assert "knowledge_quality_scores" in sql

    def test_the_page_is_the_cap_not_a_multiple_of_it(self):
        """Over-fetching is what made the page and the filter disagree. The
        query now returns exactly the turns that will be judged."""
        sql = _compiled_candidate_sql(only_unscored=True, cap=25)
        assert "LIMIT" in sql
        assert "cap" not in sql  # no leftover arithmetic on the limit

    def test_rescoring_does_not_filter_anything_out(self):
        """`only_unscored=False` exists to re-judge after a rubric change, so
        the already-scored turns must stay in."""
        sql = _compiled_candidate_sql(only_unscored=False)
        assert "EXISTS" not in sql

    def test_only_answered_turns_are_candidates(self):
        sql = _compiled_candidate_sql(only_unscored=True)
        assert "is_answered IS true" in sql
        assert "response IS NOT NULL" in sql


# --- The read side: aggregates must not invent a denominator ----------------
#
# Both stores were write-only over HTTP until 2026-08-28: Tier 0 columns were
# written on every gated turn and selected by nothing, and Tier 1 scores could
# only be read back out of the POST response that produced them. The read paths
# added here are where a wrong denominator would do its damage silently, so the
# denominators are what these tests pin.

class TestScoreSummary:
    def test_bands_split_at_one_and_zero_point_seven(self):
        from utils.knowledge_health.rag_eval_runner import _summarise

        s = _summarise("faithfulness", [1.0, 0.99, 0.7, 0.69, 0.0])
        assert s["perfect"] == 1          # 1.0 only — 0.99 is not "every claim"
        assert s["partial"] == 2          # 0.99 and 0.70
        assert s["weak"] == 2             # 0.69 and 0.0
        assert s["observations"] == 5

    def test_median_of_an_even_count_is_the_midpoint(self):
        from utils.knowledge_health.rag_eval_runner import _summarise

        assert _summarise("coherence", [0.2, 0.4, 0.6, 0.8])["median"] == 0.5

    def test_a_zero_score_is_an_observation(self):
        """0.0 is a measurement — 'nothing in the answer was supported'. Only a
        WITHHELD dimension is absent, and it is absent as a missing row, so it
        never reaches this function at all."""
        from utils.knowledge_health.rag_eval_runner import _summarise

        s = _summarise("faithfulness", [0.0, 1.0])
        assert s["observations"] == 2 and s["mean"] == 0.5


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ReadResult:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _ReadSession:
    """Returns turns, score rows, and langsmith-run-id rows in the order
    `read_scores` issues them: turns, then scores, then (when there is at
    least one judged turn and `include_turns` is on) the per-page lookup of
    each turn's LangSmith run id via `_langsmith_run_ids`."""
    def __init__(self, turns, scores, ls_rows=None):
        self._queue = [turns, scores, ls_rows or []]
    async def execute(self, _stmt):
        return _ReadResult(self._queue.pop(0))


def _read(turns, scores, ls_rows=None, **kw):
    import asyncio
    from datetime import datetime, timezone
    from utils.knowledge_health.rag_eval_runner import read_scores

    return asyncio.run(read_scores(
        _ReadSession(turns, scores, ls_rows), "kb-1",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 28, tzinfo=timezone.utc), **kw,
    ))


class TestReadScores:
    def _turns(self, n):
        from datetime import datetime
        return [_Row(chat_id=f"turn-{i}", created_at=datetime(2026, 8, 20, 10, i),
                     query=f"q{i}") for i in range(n)]

    def test_coverage_is_judged_over_answered_not_over_judged(self):
        """The number that stops a mean over 2 turns reading like a mean over
        the window."""
        scores = [
            _Row(subject_id="turn-0", name="faithfulness", value=1.0, grader_model="m", comment=None),
            _Row(subject_id="turn-1", name="faithfulness", value=0.5, grader_model="m", comment=None),
        ]
        out = _read(self._turns(10), scores)
        assert out["answered_turns"] == 10
        assert out["judged_turns"] == 2
        assert out["coverage_pct"] == 20.0

    def test_each_dimension_carries_its_own_denominator(self):
        """Faithfulness is withheld on a turn that cited nothing while the other
        two are still scored, so one shared `n` would misreport all three."""
        scores = [
            _Row(subject_id="turn-0", name="faithfulness", value=1.0, grader_model="m", comment=None),
            _Row(subject_id="turn-0", name="coherence", value=1.0, grader_model="m", comment=None),
            _Row(subject_id="turn-1", name="coherence", value=0.5, grader_model="m", comment=None),
        ]
        out = _read(self._turns(2), scores)
        dims = {d["name"]: d for d in out["dimensions"]}
        assert dims["faithfulness"]["observations"] == 1
        assert dims["coherence"]["observations"] == 2

    def test_a_withheld_dimension_is_named_not_silently_missing(self):
        scores = [
            _Row(subject_id="turn-0", name="answer_relevance", value=0.9, grader_model="m", comment=None),
            _Row(subject_id="turn-0", name="coherence", value=1.0, grader_model="m", comment=None),
        ]
        out = _read(self._turns(1), scores)
        assert out["turns"][0]["withheld"] == ["faithfulness"]

    def test_an_unjudged_window_reports_zero_coverage_not_an_empty_success(self):
        out = _read(self._turns(5), [])
        assert out["judged_turns"] == 0 and out["coverage_pct"] == 0.0
        assert out["dimensions"] == []

    def test_no_turns_at_all_is_not_a_division_by_zero(self):
        out = _read([], [])
        assert out["answered_turns"] == 0 and out["coverage_pct"] == 0.0


class TestGroundingBlock:
    """Tier 0's aggregate. The failure to guard against is counting a NULL — an
    ungated turn — as a zero, which drags every average toward zero in exact
    proportion to how few owners are allowlisted."""

    def _sql(self):
        import asyncio
        from datetime import datetime, timezone
        from sqlalchemy.dialects import postgresql
        from utils.knowledge_health.analytics import _grounding_block

        captured = []

        class R:
            def one(self):
                row = _Row(total=0, measured=0, retrieved_chunks=None, cited_chunks=None,
                           retrieved_docs=None, cited_docs=None, avg_util=None, multi=None,
                           max_attempts=None, groups_loaded=None, groups_kept=None)
                return row
            def all(self): return []

        class S:
            async def execute(self, stmt):
                captured.append(stmt)
                return R()

        block = asyncio.run(_grounding_block(
            S(), "kb-1",
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 28, tzinfo=timezone.utc)))
        return block, str(captured[0].compile(dialect=postgresql.dialect()))

    def test_measured_counts_the_column_while_total_counts_the_rows(self):
        _, sql = self._sql()
        assert "count(*)" in sql
        assert "count(knowledge_health_events.retrieved_chunk_count)" in sql

    def test_zero_retrieval_turns_leave_the_average_alone(self):
        """nullif keeps a turn that retrieved nothing out of the utilization
        average instead of scoring it 0% — it had no evidence to use."""
        _, sql = self._sql()
        assert "nullif" in sql

    def test_an_unmeasured_window_returns_none_not_zero(self):
        block, _ = self._sql()
        assert block.measured_turns == 0
        assert block.avg_context_utilization is None
        assert block.multi_attempt_pct is None
        assert block.evidence_kept_pct is None


# --- Per-document rollup: the join a turn-level metric cannot make -----------
#
# A score belongs to a turn; a turn cites documents. Attributing the score back
# to the document is the only way to say "answers standing on THIS file keep
# failing". The tests below pin the two places that attribution can lie: a
# document cited by turns nobody judged must not report a score, and a document
# nothing cited must not silently vanish from the collection's inventory.

class _Doc:
    def __init__(self, object_id, file_name, mode=None, created_at=None):
        self.object_id, self.file_name = object_id, file_name
        self.document_processing_mode, self.created_at = mode, created_at


class _Turn:
    def __init__(self, chat_id, service_data, created_at=None, query="q"):
        self.chat_id, self.service_data = chat_id, service_data
        self.created_at, self.query = created_at, query


class _Score:
    def __init__(self, subject_id, name, value):
        self.subject_id, self.name, self.value = subject_id, name, value


class _RollupSession:
    """Answers the rollup's three queries in the order it issues them:
    turns, then scores, then the document inventory."""
    def __init__(self, turns, scores, docs):
        self._queue = [turns, scores, docs]

    async def execute(self, _stmt):
        rows = self._queue.pop(0)
        class R:
            def all(self_inner): return rows
        return R()


def _rollup(turns, scores, docs, **kw):
    import asyncio
    from datetime import datetime, timezone
    from utils.knowledge_health.rag_eval_runner import document_rollup

    return asyncio.run(document_rollup(
        _RollupSession(turns, scores, docs), "kb-1",
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 29, tzinfo=timezone.utc), **kw))


class TestDocumentRollup:
    def test_scores_follow_the_documents_their_turns_cited(self):
        from datetime import datetime
        turns = [
            _Turn("t1", {"detailed_chunks": {"docA": [1, 2]}}, datetime(2026, 8, 20)),
            _Turn("t2", {"detailed_chunks": {"docA": [1], "docB": [1]}}, datetime(2026, 8, 21)),
        ]
        scores = [_Score("t1", "faithfulness", 0.5), _Score("t2", "faithfulness", 1.0)]
        out = _rollup(turns, scores, [_Doc("docA", "a.pdf"), _Doc("docB", "b.pdf")])
        by = {d["object_id"]: d for d in out["documents"]}
        assert by["docA"]["cited_turns"] == 2 and by["docA"]["mean_faithfulness"] == 0.75
        assert by["docA"]["cited_chunks"] == 3
        assert by["docB"]["cited_turns"] == 1 and by["docB"]["mean_faithfulness"] == 1.0

    def test_a_summary_only_citation_still_counts_as_cited(self):
        """Answering from a document's summary alone is a real citation with
        zero chunks — dropping it would under-report the document's use."""
        turns = [_Turn("t1", {"summaries": {"docA": {"summary": "..."}}})]
        out = _rollup(turns, [], [_Doc("docA", "a.pdf")])
        d = out["documents"][0]
        assert d["cited_turns"] == 1 and d["cited_chunks"] == 0

    def test_an_unjudged_citation_reports_no_score_rather_than_zero(self):
        turns = [_Turn("t1", {"detailed_chunks": {"docA": [1]}})]
        out = _rollup(turns, [], [_Doc("docA", "a.pdf")])
        d = out["documents"][0]
        assert d["cited_turns"] == 1
        assert d["mean_faithfulness"] is None and d["scored_turns"] == 0

    def test_documents_nothing_cited_are_listed_not_dropped(self):
        turns = [_Turn("t1", {"detailed_chunks": {"docA": [1]}})]
        out = _rollup(turns, [], [_Doc("docA", "a.pdf"), _Doc("docB", "unused.pdf")])
        assert out["documents_in_collection"] == 2
        assert out["documents_cited"] == 1
        assert out["documents_never_cited"] == 1
        assert out["never_cited"][0]["file_name"] == "unused.pdf"

    def test_the_worst_citing_turn_is_named(self):
        turns = [
            _Turn("t1", {"detailed_chunks": {"docA": [1]}}, query="fine one"),
            _Turn("t2", {"detailed_chunks": {"docA": [1]}}, query="the bad one"),
        ]
        scores = [_Score("t1", "faithfulness", 1.0), _Score("t2", "faithfulness", 0.3)]
        out = _rollup(turns, scores, [_Doc("docA", "a.pdf")])
        assert out["documents"][0]["worst"]["query"] == "the bad one"
        assert out["documents"][0]["weak_turns"] == 1

    def test_a_citation_to_an_unknown_document_is_flagged_not_hidden(self):
        """Cited but absent from the document table — deleted since, or ingested
        elsewhere. Reporting it as a normal row would misstate the inventory."""
        turns = [_Turn("t1", {"detailed_chunks": {"ghost": [1]}})]
        out = _rollup(turns, [], [])
        assert out["documents"][0]["known_document"] is False
        assert out["documents"][0]["file_name"] is None

    def test_service_data_stored_as_a_json_string_is_still_read(self):
        import json as _json
        turns = [_Turn("t1", _json.dumps({"detailed_chunks": {"docA": [1, 2, 3]}}))]
        out = _rollup(turns, [], [_Doc("docA", "a.pdf")])
        assert out["documents"][0]["cited_chunks"] == 3
