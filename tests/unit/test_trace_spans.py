"""Unit tests for the LangSmith instrumentation added so a chat turn shows up
as one complete trace (chat/rag_node.py, chat/chart_generator.py).

Background: a small knowledge base (<= SMALL_KB_DOC_THRESHOLD docs) skips the
ReAct retrieval agent and retrieves with two plain adapter calls. Those calls
are not LangChain runnables, so before this instrumentation the trace showed a
`rag` node whose only child was `rag_answer_node`, with the retrieval seconds
unattributed. See docs/plan/chat-and-rag/archive/design-rag-tracing.md.

Pure unit tests: retrieval, the answer node, and usage logging are all
monkeypatched — no network, no DB, no LLM call.
"""

import asyncio

import chat.chart_generator as chart_generator_module
import chat.rag_node as rag_node_module


# ── Span payload summarizers ─────────────────────────────────────────────
# These decide what actually leaves the process for LangSmith. The contract
# under test is: counts and ids, never chunk or document bodies.


def test_chunk_span_outputs_counts_chunks_and_documents():
    out = rag_node_module._chunk_span_outputs([
        {"chunk_text": "body one", "document_id": "doc-1"},
        {"chunk_text": "body two", "document_id": "doc-1"},
        {"chunk_text": "body three", "object_id": "doc-2"},
    ])

    assert out["chunk_count"] == 3
    assert out["document_count"] == 2
    assert sorted(out["document_ids"]) == ["doc-1", "doc-2"]
    assert "body one" not in str(out)


def test_chunk_span_outputs_tolerates_junk():
    assert rag_node_module._chunk_span_outputs(None)["chunk_count"] == 0
    assert rag_node_module._chunk_span_outputs(["not-a-dict"])["document_count"] == 0


def test_doc_details_span_outputs_counts_summaries():
    out = rag_node_module._doc_details_span_outputs([
        {"object_id": "doc-1", "summary": "a summary"},
        {"object_id": "doc-2", "summary": "   "},
        {"object_id": "doc-3"},
    ])

    assert out == {"document_count": 3, "with_summary": 1}


def test_direct_span_inputs_drops_queue_and_history():
    out = rag_node_module._direct_span_inputs({
        "query": "what is X?",
        "knowledge_id": "kb-1",
        "doc_count": 3,
        "token_queue": asyncio.Queue(),
        "past_messages": [{"role": "user", "content": "secret history"}],
    })

    assert out == {"query": "what is X?", "knowledge_id": "kb-1", "doc_count": 3}


def test_direct_span_outputs_summarizes_result():
    out = rag_node_module._direct_span_outputs({
        "response": "12345",
        "is_answered": "yes",
        "summaries": {"doc-1": {}},
        "rag_context": {"chunks": [{"chunk_text": "body"}, {"chunk_text": "body"}]},
    })

    assert out == {
        "chunk_count": 2,
        "document_count": 1,
        "is_answered": "yes",
        "answer_chars": 5,
    }
    assert rag_node_module._direct_span_outputs(None) == {"result": None}


# ── The gate ─────────────────────────────────────────────────────────────
# The gate is deliberately untraced, so an agentic turn emits no empty span.


def _patch_direct_body(monkeypatch, calls, result=None, error=None):
    async def _fake(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return result if result is not None else {"response": "ok"}

    monkeypatch.setattr(rag_node_module, "_small_kb_direct", _fake)


def test_gate_skips_when_kb_is_too_large(monkeypatch):
    calls = []
    _patch_direct_body(monkeypatch, calls)

    out = asyncio.run(rag_node_module._try_small_kb_direct(
        "q", "kb-1", {"document_count": 999},
    ))

    assert out is None
    assert calls == []


def test_gate_runs_body_for_small_kb(monkeypatch):
    calls = []
    _patch_direct_body(monkeypatch, calls)

    out = asyncio.run(rag_node_module._try_small_kb_direct(
        "q", "kb-1", {"document_count": 3},
    ))

    assert out == {"response": "ok"}
    assert calls[0]["doc_count"] == 3


def test_gate_falls_back_to_agentic_when_body_raises(monkeypatch):
    """The body raises so the span shows the error; the gate still degrades
    to the agentic path, which is what the caller expects."""
    calls = []
    _patch_direct_body(monkeypatch, calls, error=RuntimeError("qdrant down"))

    out = asyncio.run(rag_node_module._try_small_kb_direct(
        "q", "kb-1", {"document_count": 3},
    ))

    assert out is None


# ── The body ─────────────────────────────────────────────────────────────


def test_direct_body_goes_through_the_traced_wrappers(monkeypatch):
    """Retrieval must run through _traced_* — calling the adapters directly
    is exactly the bug this instrumentation fixes."""
    seen = {}

    async def _fake_search(**kwargs):
        seen["search"] = kwargs
        return [{"chunk_text": "body", "document_id": "doc-1"}]

    async def _fake_details(**kwargs):
        seen["details"] = kwargs
        return [{"object_id": "doc-1", "summary": "s", "file_name": "f.pdf"}]

    async def _fake_answer(**kwargs):
        seen["answer"] = kwargs
        return {"answer": "the answer", "is_answered": True, "usage_metrics": {}}

    monkeypatch.setattr(rag_node_module, "_traced_search_chunks", _fake_search)
    monkeypatch.setattr(rag_node_module, "_traced_get_document_details", _fake_details)
    monkeypatch.setattr(rag_node_module, "_log_usage", lambda *a, **k: None)
    monkeypatch.setattr(
        "chat.rag_agent.answer_node.generate_rag_answer", _fake_answer
    )

    result = asyncio.run(rag_node_module._small_kb_direct(
        query="what is X?", knowledge_id="kb-1", doc_count=2,
    ))

    assert seen["search"]["knowledge_id"] == "kb-1"
    assert seen["details"]["object_ids"] == ["doc-1"]
    assert result["response"] == "the answer"
    assert result["is_answered"] == "yes"
    assert result["is_direct_response"] is True


# ── Chart API span ───────────────────────────────────────────────────────
# The Chart API is a plain aiohttp POST — untraced until now, so a blank chart
# gave no way to tell whether the CSV we sent or the API's reply was at fault.


def test_chart_api_span_inputs_summarize_the_csv():
    csv_data = "name,revenue\nacme,10\nbeta,20\n"
    out = chart_generator_module._chart_api_span_inputs({
        "query": "revenue by company",
        "owner_id": "owner-1",
        "csv_data": csv_data,
    })

    assert out["csv_rows"] == 2  # header excluded
    assert out["csv_bytes"] == len(csv_data)
    assert out["csv_preview"].startswith("name,revenue")
    assert out["query"] == "revenue by company"


def test_chart_api_span_inputs_truncate_a_long_csv():
    csv_data = "\n".join(f"row-{i},{i}" for i in range(500))
    out = chart_generator_module._chart_api_span_inputs({"csv_data": csv_data})

    assert out["csv_preview"].count("\n") < 25
    assert out["csv_rows"] == 499


def test_chart_api_span_inputs_handle_empty_csv():
    out = chart_generator_module._chart_api_span_inputs({"csv_data": ""})

    assert out["csv_rows"] == 0
    assert out["csv_bytes"] == 0


def test_chart_api_span_outputs_truncate_long_values():
    out = chart_generator_module._chart_api_span_outputs({
        "chart_generated": True,
        "chart_type": "bar",
        "image": "x" * 50_000,
    })

    assert out["chart_generated"] is True
    assert out["chart_type"] == "bar"
    assert len(out["image"]) < 2_100
    assert "50000 chars total" in out["image"]


def test_chart_api_span_outputs_mark_a_failed_call():
    assert chart_generator_module._chart_api_span_outputs(None) == {"result": None}


def test_tag_span_never_raises_without_a_run():
    chart_generator_module._tag_span(http_status=500)
