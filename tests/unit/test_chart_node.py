"""Unit tests for chat/chart_node.py (G16 — chart generation).

Pure unit tests: resolve_chat_config and process_chart_visualization are
monkeypatched with fakes — no real DB/LLM/Chart-API call in any test.
"""

import asyncio
from types import SimpleNamespace

import chat.chart_node as chart_node_module


def _run(coro):
    return asyncio.run(coro)


def test_chart_disabled_skips_generation(monkeypatch):
    """include_charts=False must short-circuit before any chunk processing."""
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=False)

    called = {"process": False}

    async def _fake_process(*args, **kwargs):
        called["process"] = True
        return {"chart_generated": True}, None

    monkeypatch.setattr("chat.config.resolve_chat_config", _fake_resolve_chat_config)
    monkeypatch.setattr("chat.chart_generator.process_chart_visualization", _fake_process)

    result = _run(chart_node_module.chart_node(
        query="q", knowledge_id="k1", owner_id="owner-1",
        detailed_chunks={"doc1": [{"text": "hello"}]},
    ))

    assert result == {"chart_output": None}
    assert called["process"] is False


def test_no_chunks_skips_generation(monkeypatch):
    """include_charts=True but no chunks retrieved must also skip."""
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=True)

    called = {"process": False}

    async def _fake_process(*args, **kwargs):
        called["process"] = True
        return {"chart_generated": True}, None

    monkeypatch.setattr("chat.config.resolve_chat_config", _fake_resolve_chat_config)
    monkeypatch.setattr("chat.chart_generator.process_chart_visualization", _fake_process)

    result = _run(chart_node_module.chart_node(
        query="q", knowledge_id="k1", owner_id="owner-1", detailed_chunks={},
    ))

    assert result == {"chart_output": None}
    assert called["process"] is False


def test_chart_generated_when_enabled_with_chunks(monkeypatch):
    """include_charts=True with chunks present must call the pipeline and
    surface its result verbatim as chart_output."""
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=True)

    async def _fake_process(query, owner_id, chunks, knowledge_id=None, documents=None, population_complete=True):
        assert query == "q"
        assert owner_id == "owner-1"
        assert chunks == [{"text": "hello"}, {"text": "world"}]
        assert knowledge_id == "k1"
        return {"chart_generated": True, "chart_data": {"type": "bar"}}, None

    monkeypatch.setattr("chat.config.resolve_chat_config", _fake_resolve_chat_config)
    monkeypatch.setattr("chat.chart_generator.process_chart_visualization", _fake_process)
    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "https://chart.example/api")

    result = _run(chart_node_module.chart_node(
        query="q", knowledge_id="k1", owner_id="owner-1",
        detailed_chunks={"doc1": [{"text": "hello"}, {"text": "world"}]},
    ))

    assert result == {"chart_output": {"chart_generated": True, "chart_data": {"type": "bar"}}}


def test_chart_reports_node_usage_when_llm_calls_are_made(monkeypatch):
    """When process_chart_visualization reports usage, chart_node must surface
    it as a "chart"-tagged usage_by_model/service_calls entry (Agent Health
    node breakdown) — this pipeline runs real LLM calls that were previously
    invisible to cost/token tracking entirely."""
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=True)

    async def _fake_process(query, owner_id, chunks, knowledge_id=None, documents=None, population_complete=True):
        return (
            {"chart_generated": True},
            {"provider": "openai", "model": "gpt-4o-mini", "input_tokens": 120, "output_tokens": 40},
        )

    monkeypatch.setattr("chat.config.resolve_chat_config", _fake_resolve_chat_config)
    monkeypatch.setattr("chat.chart_generator.process_chart_visualization", _fake_process)
    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "https://chart.example/api")

    result = _run(chart_node_module.chart_node(
        query="q", knowledge_id="k1", owner_id="owner-1",
        detailed_chunks={"doc1": [{"text": "hello"}]},
    ))

    assert result["chart_output"] == {"chart_generated": True}
    assert result["usage_by_model"] == [
        {"provider": "openai", "model": "gpt-4o-mini", "input_tokens": 120, "output_tokens": 40}
    ]
    assert len(result["service_calls"]) == 1
    call = result["service_calls"][0]
    assert call["service"] == "chart"
    assert call["provider"] == "openai" and call["model"] == "gpt-4o-mini"
    assert call["input_tokens"] == 120 and call["output_tokens"] == 40
