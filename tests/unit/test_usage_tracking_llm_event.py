"""Unit tests for utils/qdrant/usage_tracking.py's send_llm_usage_event /
_build_llm_usage_event (added in Phase 3c to log RAG-agent LLM spend — see
claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests: post_processing_event (the HTTP call out to the usage-events
API) is monkeypatched; no real network I/O.
"""

import asyncio

import utils.qdrant.usage_tracking as usage_tracking


def test_build_llm_usage_event_payload_shape():
    event = usage_tracking._build_llm_usage_event(
        label="rag_agent",
        provider="openai",
        model="gpt-4.1-mini",
        input_tokens=120,
        output_tokens=45,
        owner_id="owner-1",
        knowledge_id="kb-1",
        session_id="sess-1",
        key_type="platform-key",
    )

    assert event["service_id"] == "kb-1"
    assert event["owner_id"] == "owner-1"
    assert event["session_id"] == "sess-1"
    assert event["key_type"] == "platform-key"
    assert event["service_type"] == "knowledge"
    assert event["operation"] == "llm_generation"
    assert event["label"] == "rag_agent"
    assert event["metadata"] == {"provider": "openai", "model": "gpt-4.1-mini"}
    metrics = {m["name"]: m["value"] for m in event["metrics"]}
    assert metrics == {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}
    assert "timestamp" in event


def test_build_llm_usage_event_omits_owner_and_session_when_none():
    event = usage_tracking._build_llm_usage_event(
        label="refine_query",
        provider="openai",
        model="gpt-5.4-mini",
        input_tokens=10,
        output_tokens=5,
        owner_id=None,
    )

    assert event["owner_id"] is None
    assert event["session_id"] is None
    assert event["key_type"] is None
    assert event["service_id"] is None


def test_send_llm_usage_event_posts_single_event(monkeypatch):
    captured = {}

    def _fake_post(payload):
        captured["payload"] = payload
        return {"status": "success"}

    monkeypatch.setattr(usage_tracking, "post_processing_event", _fake_post)

    asyncio.run(usage_tracking.send_llm_usage_event(
        label="rag_agent",
        provider="openai",
        model="gpt-4.1-mini",
        input_tokens=100,
        output_tokens=50,
        owner_id="owner-1",
        knowledge_id="kb-1",
        session_id="sess-1",
        key_type="owner-key",
    ))

    assert "payload" in captured
    events = captured["payload"]["events"]
    assert len(events) == 1
    assert events[0]["label"] == "rag_agent"
    assert events[0]["key_type"] == "owner-key"


def test_send_llm_usage_event_swallows_post_error(monkeypatch):
    def _broken_post(payload):
        raise RuntimeError("usage API down")

    monkeypatch.setattr(usage_tracking, "post_processing_event", _broken_post)

    # Must not raise — fire-and-forget usage logging should never break the
    # caller (rag_node/query_refiner) on a usage-tracking outage.
    asyncio.run(usage_tracking.send_llm_usage_event(
        label="rag_agent",
        provider="openai",
        model="gpt-4.1-mini",
        input_tokens=10,
        output_tokens=5,
        owner_id="owner-1",
    ))


def test_send_llm_usage_event_does_not_call_owner_resolution_helpers(monkeypatch):
    """Unlike send_embedding_event, send_llm_usage_event must NOT re-derive
    owner_id/key_type — the caller already has both from get_llm's return
    tuple (see the docstring's explicit contrast with send_embedding_event)."""

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("should not be called — owner_id/key_type are caller-supplied")

    monkeypatch.setattr(usage_tracking, "get_owner_id_from_service_id", _fail_if_called, raising=False)
    monkeypatch.setattr(usage_tracking, "get_credential_for_knowledge", _fail_if_called)

    captured = {}
    monkeypatch.setattr(usage_tracking, "post_processing_event", lambda payload: captured.update(payload=payload))

    asyncio.run(usage_tracking.send_llm_usage_event(
        label="rag_agent",
        provider="openai",
        model="gpt-4.1-mini",
        input_tokens=10,
        output_tokens=5,
        owner_id="owner-1",
        key_type="platform-key",
    ))

    assert "payload" in captured
