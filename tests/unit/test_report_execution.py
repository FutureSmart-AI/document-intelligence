"""Unit tests for chat/report_execution.py — the in-process run executor at
the heart of the Worker-wiring decision
(docs/plan/batch-query-reports/execution.md).

Every DB write goes through utils/reports/db_utils.py, monkeypatched here;
`chat.graph.run_knowledge_chat_graph` is monkeypatched too, so no real chat
graph, LLM, or DB call happens in any test.
"""
import asyncio

import pytest

import chat.report_execution as report_execution


def run(coro):
    return asyncio.run(coro)


def _independent_definition():
    return {
        "run_id": "run_1",
        "report_id": "rep_1",
        "knowledge_id": "k-1",
        "owner_id": "owner@futuresmart.ai",
        "report_instructions": None,
        "analysis_prompt": None,
        "query_groups": [
            {
                "group_id": "g1",
                "type": "independent",
                "queries": [
                    {"query_id": "q1", "text": "What is X?"},
                    {"query_id": "q2", "text": "What is Y?"},
                ],
            }
        ],
    }


def _sequential_definition():
    return {
        "run_id": "run_2",
        "report_id": "rep_2",
        "knowledge_id": "k-1",
        "owner_id": "owner@futuresmart.ai",
        "report_instructions": None,
        "analysis_prompt": None,
        "query_groups": [
            {
                "group_id": "g1",
                "type": "sequential",
                "queries": [
                    {"query_id": "q1", "text": "First question"},
                    {"query_id": "q2", "text": "Follow-up question"},
                ],
            }
        ],
    }


@pytest.fixture
def status_calls(monkeypatch):
    calls = []

    async def _fake_status(run_id, status, **kwargs):
        calls.append((run_id, status, kwargs))
        return status
    monkeypatch.setattr(report_execution.db_utils, "update_run_status", _fake_status)
    return calls


@pytest.fixture
def upsert_calls(monkeypatch):
    calls = []

    async def _fake_upsert(run_id, results):
        calls.append((run_id, results))
        return len(results)
    monkeypatch.setattr(report_execution.db_utils, "upsert_results", _fake_upsert)
    return calls


def test_run_not_found_is_a_noop(monkeypatch, status_calls):
    async def _fake_def(run_id):
        return None
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    run(report_execution.execute_run("missing"))
    assert status_calls == []  # never even tried to mark running


def test_independent_group_runs_queries_in_parallel_own_sessions(monkeypatch, status_calls, upsert_calls):
    async def _fake_def(run_id):
        return _independent_definition()
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    seen_sessions = []

    async def _fake_graph(query, knowledge_id, owner_id=None, session_id=None, past_messages=None):
        seen_sessions.append(session_id)
        return {"response": f"answer to: {query}", "relevant_sources": ["src1"], "usage_by_model": []}
    monkeypatch.setattr(report_execution, "run_knowledge_chat_graph", _fake_graph)

    run(report_execution.execute_run("run_1"))

    # Both queries got their own session_id — independent means no shared conversation.
    assert len(seen_sessions) == 2
    assert len(set(seen_sessions)) == 2

    # One upsert call per query, each with a single result item.
    written_query_ids = {r["query_id"] for _, results in upsert_calls for r in results}
    assert written_query_ids == {"q1", "q2"}

    statuses = [c[1] for c in status_calls]
    assert statuses == ["running", "completed"]


def test_sequential_group_shares_session_and_accumulates_history(monkeypatch, status_calls, upsert_calls):
    async def _fake_def(run_id):
        return _sequential_definition()
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    seen_calls = []

    async def _fake_graph(query, knowledge_id, owner_id=None, session_id=None, past_messages=None):
        seen_calls.append({"query": query, "session_id": session_id, "past_messages": list(past_messages or [])})
        return {"response": f"answer to: {query}", "relevant_sources": [], "usage_by_model": []}
    monkeypatch.setattr(report_execution, "run_knowledge_chat_graph", _fake_graph)

    run(report_execution.execute_run("run_2"))

    assert len(seen_calls) == 2
    # Same session_id reused across the sequential group.
    assert seen_calls[0]["session_id"] == seen_calls[1]["session_id"]
    # First call has no history; second call sees the first Q&A.
    assert seen_calls[0]["past_messages"] == []
    assert seen_calls[1]["past_messages"] == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "answer to: First question"},
    ]


def test_query_failure_is_captured_as_error_not_raised(monkeypatch, status_calls, upsert_calls):
    async def _fake_def(run_id):
        return _independent_definition()
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    async def _fake_graph(query, knowledge_id, owner_id=None, session_id=None, past_messages=None):
        raise RuntimeError("LLM exploded")
    monkeypatch.setattr(report_execution, "run_knowledge_chat_graph", _fake_graph)

    # Must not raise — a per-query failure becomes a result row with `error` set.
    run(report_execution.execute_run("run_1"))

    all_results = [item for _, results in upsert_calls for item in results]
    assert len(all_results) == 2
    assert all(r["error"] == "LLM exploded" for r in all_results)
    assert all(r["answer"] is None for r in all_results)

    # The RUN itself still completes (individual query errors aren't whole-job failures).
    statuses = [c[1] for c in status_calls]
    assert statuses == ["running", "completed"]


def test_report_instructions_prefixed_onto_query(monkeypatch, status_calls, upsert_calls):
    definition = _independent_definition()
    definition["report_instructions"] = "Answer concisely."
    definition["query_groups"][0]["queries"] = [{"query_id": "q1", "text": "What is X?"}]

    async def _fake_def(run_id):
        return definition
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    seen_queries = []

    async def _fake_graph(query, knowledge_id, owner_id=None, session_id=None, past_messages=None):
        seen_queries.append(query)
        return {"response": "ok", "relevant_sources": [], "usage_by_model": []}
    monkeypatch.setattr(report_execution, "run_knowledge_chat_graph", _fake_graph)

    run(report_execution.execute_run("run_1"))

    assert seen_queries == ["Answer concisely.\n\nWhat is X?"]


def test_analysis_prompt_triggers_summary_generation(monkeypatch, status_calls, upsert_calls):
    definition = _independent_definition()
    definition["analysis_prompt"] = "Summarize the findings."

    async def _fake_def(run_id):
        return definition
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    async def _fake_graph(query, knowledge_id, owner_id=None, session_id=None, past_messages=None):
        return {"response": f"answer to: {query}", "relevant_sources": [], "usage_by_model": []}
    monkeypatch.setattr(report_execution, "run_knowledge_chat_graph", _fake_graph)

    async def _fake_full(run_id):
        return {
            "query_groups": [
                {"results": [
                    {"question": "What is X?", "answer": "answer to: What is X?"},
                    {"question": "What is Y?", "answer": "answer to: What is Y?"},
                ]}
            ]
        }
    monkeypatch.setattr(report_execution.db_utils, "get_run_full", _fake_full)

    class _FakeResponse:
        content = "Both questions were answered clearly."

    async def _fake_get_llm(owner_id=None, knowledge_id=None, max_tokens=800, **kwargs):
        class _FakeLLM:
            async def ainvoke(self, messages):
                return _FakeResponse()
        return _FakeLLM(), "openai", "platform-key"
    # chat/report_execution.py routes the LLM build + invoke through
    # chat.byok_fallback.run_with_key_fallback since BYOK invalid-key fallback —
    # it no longer imports get_llm itself, so the patch target moved to
    # byok_fallback's own bound name (`from chat.llm_factory import get_llm`
    # at byok_fallback's import time; patching chat.llm_factory.get_llm
    # afterward would not reach it).
    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    analysis_calls = []

    async def _fake_set_analysis(run_id, overall_summary):
        analysis_calls.append((run_id, overall_summary))
        return True
    monkeypatch.setattr(report_execution.db_utils, "set_run_analysis", _fake_set_analysis)

    run(report_execution.execute_run("run_1"))

    assert analysis_calls == [("run_1", "Both questions were answered clearly.")]


def test_whole_job_exception_marks_run_failed(monkeypatch, status_calls):
    async def _fake_def(run_id):
        raise RuntimeError("DB unreachable")
    monkeypatch.setattr(report_execution.db_utils, "get_run_definition", _fake_def)

    # Must not raise to the caller.
    run(report_execution.execute_run("run_1"))

    assert len(status_calls) == 1
    run_id, status, kwargs = status_calls[0]
    assert status == "failed"
    assert "DB unreachable" in kwargs.get("failure_reason", "")
