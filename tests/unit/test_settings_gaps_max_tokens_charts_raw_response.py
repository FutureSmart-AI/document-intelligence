"""Unit tests for the three Settings gaps closed by migration 030: max_tokens,
include_charts, show_raw_response — the "what answers can include" mock
controls with no backing field before this pass.

Pure unit tests: every DB read/write function and every LLM call is
monkeypatched; no real DB connection or LLM call anywhere, matching the
existing test_chat_config.py / test_settings_gaps_g1_g10.py convention.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import chat.config as chat_config_module
import chat.db.config_ops as config_ops_module
import chat.rag_node as rag_node_module
import routers.chat as chat_router_module


class _FakeConfigRow:
    def __init__(self, max_tokens=None, include_charts=True, show_raw_response=True, **kwargs):
        self.llm_provider = kwargs.get("llm_provider")
        self.llm_model = kwargs.get("llm_model")
        self.temperature = kwargs.get("temperature")
        self.system_prompt = kwargs.get("system_prompt")
        self.show_sources = kwargs.get("show_sources", True)
        self.chat_enabled = kwargs.get("chat_enabled", True)
        self.follow_up = kwargs.get("follow_up", True)
        self.suggested_questions = kwargs.get("suggested_questions")
        self.voice_input_enabled = kwargs.get("voice_input_enabled", False)
        self.voice_output_enabled = kwargs.get("voice_output_enabled", False)
        self.max_tokens = max_tokens
        self.include_charts = include_charts
        self.show_raw_response = show_raw_response


class _FakeGatingRow:
    """The row GET /chat/config reads directly for the embed-gating fields
    (is_public / allowed_domains / fullscreen_*), which do not come through
    resolve_chat_config."""

    def __init__(self, is_public=False, allowed_domains=None,
                 fullscreen_is_public=False, fullscreen_allowed_domains=None):
        self.is_public = is_public
        self.allowed_domains = allowed_domains
        self.fullscreen_is_public = fullscreen_is_public
        self.fullscreen_allowed_domains = fullscreen_allowed_domains


class _FakeNodeRow:
    def __init__(self, max_tokens=None, **kwargs):
        self.provider = kwargs.get("provider")
        self.model = kwargs.get("model")
        self.temperature = kwargs.get("temperature")
        self.prompt = kwargs.get("prompt")
        self.enabled = kwargs.get("enabled", True)
        self.max_tokens = max_tokens


def _patch_row(monkeypatch, row):
    async def _fake_get_config_row(knowledge_id):
        return row
    monkeypatch.setattr(chat_config_module, "_get_config_row", _fake_get_config_row)


def _patch_node_row(monkeypatch, row):
    async def _fake_get_node_config_row(knowledge_id, node_name):
        return row
    monkeypatch.setattr(chat_config_module, "_get_node_config_row", _fake_get_node_config_row)


# ---------------------------------------------------------------------------
# chat/config.py::resolve_chat_config resolves the three new fields
# ---------------------------------------------------------------------------

def test_defaults_without_row(monkeypatch):
    _patch_row(monkeypatch, None)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.max_tokens == 1024
    assert result.include_charts is True
    assert result.show_raw_response is True


def test_resolves_from_stored_row(monkeypatch):
    row = _FakeConfigRow(max_tokens=2048, include_charts=False, show_raw_response=False)
    _patch_row(monkeypatch, row)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.max_tokens == 2048
    assert result.include_charts is False
    assert result.show_raw_response is False


def test_request_max_tokens_overrides_row(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(max_tokens=2048))
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1", request_max_tokens=512))
    assert result.max_tokens == 512


def test_node_row_max_tokens_overrides_knowledge_row(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(max_tokens=2048))
    _patch_node_row(monkeypatch, _FakeNodeRow(max_tokens=256))
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1", node_name="generate_answer"))
    assert result.max_tokens == 256


def test_env_max_tokens_used_when_no_row_and_no_request(monkeypatch):
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "RAG_CHAT_MAX_TOKENS", "4096")
    _patch_row(monkeypatch, None)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.max_tokens == 4096


def test_include_charts_and_show_raw_response_have_no_request_override(monkeypatch):
    """Same posture as show_sources/follow_up — knowledge-level policy only."""
    row = _FakeConfigRow(include_charts=False, show_raw_response=False)
    _patch_row(monkeypatch, row)
    result = asyncio.run(chat_config_module.resolve_chat_config("kb-1"))
    assert result.include_charts is False
    assert result.show_raw_response is False


# ---------------------------------------------------------------------------
# chat/db/config_ops.py accepts the new fields on write
# ---------------------------------------------------------------------------

def test_new_fields_are_writable():
    for field in ("max_tokens", "include_charts", "show_raw_response"):
        assert field in config_ops_module.WRITABLE_FIELDS
    assert "max_tokens" in config_ops_module.WRITABLE_NODE_FIELDS


# ---------------------------------------------------------------------------
# GET/PUT /v1/{knowledge_id}/chat/config round-trips the new fields
# ---------------------------------------------------------------------------

def _make_chat_app():
    app = FastAPI()
    app.include_router(chat_router_module.router)
    for route in chat_router_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: None
    return app


@pytest.fixture(autouse=True)
def _patch_node_rows(monkeypatch):
    async def _fake_list(knowledge_id):
        return []
    monkeypatch.setattr(chat_router_module, "list_node_config_rows", _fake_list)


def test_get_chat_config_returns_new_fields(monkeypatch):
    async def _fake_resolve(knowledge_id, **kwargs):
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True, follow_up=True,
            max_tokens=2048, include_charts=False, show_raw_response=False,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve)

    async def _fake_get_row(knowledge_id):
        return _FakeGatingRow()
    monkeypatch.setattr(chat_router_module, "get_config_row", _fake_get_row)

    response = TestClient(_make_chat_app()).get("/v1/kb-1/chat/config")
    assert response.status_code == 200
    body = response.json()
    assert body["max_tokens"] == 2048
    assert body["include_charts"] is False
    assert body["show_raw_response"] is False


def test_put_chat_config_writes_new_fields(monkeypatch):
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured.update(updates)
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_chat_app()).put(
        "/v1/kb-1/chat/config",
        json={"max_tokens": 512, "include_charts": False, "show_raw_response": False},
    )
    assert response.status_code == 200
    assert captured == {"max_tokens": 512, "include_charts": False, "show_raw_response": False}


def test_put_chat_config_omitting_new_fields_leaves_them_untouched(monkeypatch):
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured.update(updates)
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_chat_app()).put(
        "/v1/kb-1/chat/config", json={"llm_model": "gpt-4o"},
    )
    assert response.status_code == 200
    assert "max_tokens" not in captured
    assert "include_charts" not in captured
    assert "show_raw_response" not in captured


def test_put_node_config_writes_max_tokens(monkeypatch):
    captured = {}

    async def _fake_upsert_node(knowledge_id, node_name, updates):
        captured[node_name] = updates
        return None
    monkeypatch.setattr(chat_router_module, "upsert_node_config_row", _fake_upsert_node)

    response = TestClient(_make_chat_app()).put(
        "/v1/kb-1/chat/config",
        json={"node_configs": {"generate_answer": {"max_tokens": 300}}},
    )
    assert response.status_code == 200
    assert captured["generate_answer"]["max_tokens"] == 300


# ---------------------------------------------------------------------------
# show_raw_response gates debug_info out of the client-facing response
# ---------------------------------------------------------------------------

def _final_state():
    return {
        "response": "The answer.",
        "is_answered": "yes",
        "rag_context": {"agent_output": {}},
        "service_calls": [],
    }


def test_debug_info_present_when_show_raw_response_true():
    payload = chat_router_module._build_response_payload(
        _final_state(), "q", "session-1", show_raw_response=True,
    )
    assert payload["debug_info"] is not None


def test_debug_info_omitted_when_show_raw_response_false():
    payload = chat_router_module._build_response_payload(
        _final_state(), "q", "session-1", show_raw_response=False,
    )
    assert payload["debug_info"] is None


def test_show_raw_response_defaults_true_preserving_existing_behavior():
    """Callers that don't pass show_raw_response at all must see the same
    debug_info they always did — this field must not be a breaking change
    for existing chat integrations."""
    payload = chat_router_module._build_response_payload(_final_state(), "q", "session-1")
    assert payload["debug_info"] is not None


# ---------------------------------------------------------------------------
# max_tokens is actually threaded into the answer node's get_llm call
# (unlike temperature, which resolves but is never applied — G11)
# ---------------------------------------------------------------------------

class _FakeStructuredLLM:
    async def ainvoke(self, *args, **kwargs):
        class _Parsed:
            answer = "An answer."
            is_answered = True

        class _Raw:
            content = "An answer."
            usage_metadata = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

        return {"raw": _Raw(), "parsed": _Parsed(), "parsing_error": None}


class _FakeLLM:
    def with_structured_output(self, *args, **kwargs):
        return _FakeStructuredLLM()


def test_generate_rag_answer_threads_max_tokens(monkeypatch):
    import chat.byok_fallback as byok_fallback
    import chat.rag_agent.answer_node as answer_node_module

    captured = {}

    async def _fake_get_llm(**kwargs):
        captured.update(kwargs)
        return _FakeLLM(), "openai", "platform-key"

    # answer_node.py routes the LLM build + invoke through
    # chat.byok_fallback.run_with_key_fallback since BYOK invalid-key
    # fallback — patch byok_fallback's own bound get_llm, not
    # answer_node_module's (it no longer imports get_llm directly).
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    asyncio.run(answer_node_module.generate_rag_answer(
        query="q", retrieved_context={}, owner_id="owner-1", max_tokens=333,
    ))
    assert captured["max_tokens"] == 333


def test_generate_rag_answer_falls_back_when_max_tokens_none(monkeypatch):
    import chat.byok_fallback as byok_fallback
    import chat.rag_agent.answer_node as answer_node_module

    captured = {}

    async def _fake_get_llm(**kwargs):
        captured.update(kwargs)
        return _FakeLLM(), "openai", "platform-key"

    # answer_node.py routes the LLM build + invoke through
    # chat.byok_fallback.run_with_key_fallback since BYOK invalid-key
    # fallback — patch byok_fallback's own bound get_llm, not
    # answer_node_module's (it no longer imports get_llm directly).
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    asyncio.run(answer_node_module.generate_rag_answer(
        query="q", retrieved_context={}, owner_id="owner-1",
    ))
    assert captured["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# include_charts=False appends a suppression directive to generate_answer's
# instructions; include_charts=True (default) leaves them untouched
# ---------------------------------------------------------------------------

def test_include_charts_false_appends_no_charts_directive(monkeypatch):
    captured = {}

    async def _fake_resolve_chat_config(knowledge_id, node_name=None, **kwargs):
        if node_name == "generate_answer":
            return chat_config_module.ResolvedChatConfig(
                llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
                system_prompt=None, show_sources=True, chat_enabled=True,
                max_tokens=1024, include_charts=False,
            )
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    async def _fake_get_node_config_row(knowledge_id, node_name):
        return None
    monkeypatch.setattr("chat.config._get_node_config_row", _fake_get_node_config_row)

    async def _fake_fetch_schema(knowledge_id):
        return {"document_count": -1}
    monkeypatch.setattr(rag_node_module, "_fetch_schema", _fake_fetch_schema)

    async def _fake_run_rag_agent(**kwargs):
        captured.update(kwargs)
        return {
            "summaries": {}, "chunks": [], "tool_responses": [],
            "usage_metrics": {}, "raw_output": "answer", "is_answered": True,
            "agent_output": {},
        }
    monkeypatch.setattr(
        "chat.rag_agent.agent_execution.run_rag_agent", _fake_run_rag_agent,
    )

    asyncio.run(rag_node_module.rag_node(query="q", knowledge_id="kb-1", owner_id="owner-1"))
    assert "Do not format the answer as a markdown table" in captured["generate_answer_instructions"]
    assert captured["answer_max_tokens"] == 1024


def test_include_charts_true_tells_the_answer_the_app_draws_the_chart(monkeypatch):
    """chart_node draws the real chart after the answer is written. Without this
    directive the answer drew ASCII bars beside it and offered 'I can also turn
    this into a simple bar chart' above the chart it already had (hosted resume
    sample, 2026-10-25)."""
    captured = {}

    async def _fake_resolve_chat_config(knowledge_id, node_name=None, **kwargs):
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
            max_tokens=1024, include_charts=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    async def _fake_get_node_config_row(knowledge_id, node_name):
        return None
    monkeypatch.setattr("chat.config._get_node_config_row", _fake_get_node_config_row)

    async def _fake_fetch_schema(knowledge_id):
        return {"document_count": -1}
    monkeypatch.setattr(rag_node_module, "_fetch_schema", _fake_fetch_schema)

    async def _fake_run_rag_agent(**kwargs):
        captured.update(kwargs)
        return {
            "summaries": {}, "chunks": [], "tool_responses": [],
            "usage_metrics": {}, "raw_output": "answer", "is_answered": True,
            "agent_output": {},
        }
    monkeypatch.setattr(
        "chat.rag_agent.agent_execution.run_rag_agent", _fake_run_rag_agent,
    )

    asyncio.run(rag_node_module.rag_node(query="q", knowledge_id="kb-1", owner_id="owner-1"))
    instructions = captured["generate_answer_instructions"]
    assert rag_node_module.CHART_RENDERED_SEPARATELY_DIRECTIVE in instructions
    assert "never offer to make or convert one" in instructions
    assert "Do not format the answer as a markdown table" not in instructions
