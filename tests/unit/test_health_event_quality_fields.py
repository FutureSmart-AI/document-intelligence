"""Unit tests for the per-turn quality fields on a knowledge_health_events row.

Covers chat/db/health_events.py::build_health_event's handling of ``confidence``
and ``stop_reason``. Pure unit tests — build_health_event is a pure function, so
nothing here touches a DB, the network, or an LLM.

Why these specifically. Both columns existed from migration 034 and were
hardcoded ``None`` at the writer until 2026-08-27, which left the Analytics
screen with no per-turn quality signal beyond the binary ``is_answered`` even
though the retrieval agent reports both on every turn. A regression here is
silent — the row still writes, the endpoint still answers, and the column just
goes empty again — so the pass-through is asserted rather than assumed. See
docs/plan/quality-baselines/metrics.md for what these two feed.
"""

import uuid


def _build(final_state):
    from chat.db.health_events import build_health_event

    return build_health_event(
        knowledge_id="kb-1",
        owner_id="owner@futuresmart.ai",
        user_id=None,
        session_id=None,
        chat_id=str(uuid.uuid4()),
        query="what are the file size limits?",
        latency_ms=1234,
        key_type="master_key",
        final_state=final_state,
    )


def test_agent_self_report_reaches_the_row():
    """The values rag_node forwards are what the row stores — unmapped, so a
    reader can group by exactly the enum the agent emitted."""
    row = _build({
        "is_answered": "yes",
        "confidence": "high",
        "stop_reason": "answered",
    })

    assert row["confidence"] == "high"
    assert row["stop_reason"] == "answered"


def test_a_low_confidence_answered_turn_keeps_both_signals():
    """The case the metric exists for: is_answered says yes, the agent says it
    is not sure. Collapsing that into "success" is what hid it before."""
    row = _build({
        "is_answered": "yes",
        "confidence": "low",
        "stop_reason": "budget_exhausted",
    })

    assert row["is_answered"] is True
    assert row["status"] == "success"
    assert row["confidence"] == "low"
    assert row["stop_reason"] == "budget_exhausted"


def test_no_structured_output_stays_null_rather_than_defaulting():
    """A turn whose agent emitted no parseable structured output must record
    "did not report", not a fabricated "low" — the two mean different things to
    anyone reading the confidence distribution."""
    row = _build({"is_answered": "yes"})

    assert row["confidence"] is None
    assert row["stop_reason"] is None


def test_error_path_reports_no_confidence():
    row = _build({"error": "rag_node failed: boom"})

    assert row["status"] == "error"
    assert row["confidence"] is None
    assert row["stop_reason"] is None
