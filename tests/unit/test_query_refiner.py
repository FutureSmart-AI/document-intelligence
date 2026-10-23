"""Unit tests for chat/query_refiner.py (Phase 3c — see
claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests: get_llm and send_llm_usage_event are monkeypatched with
fakes; no real LLM call or network I/O.
"""

import asyncio

import pytest

import chat.byok_fallback as byok_fallback
import chat.query_refiner as refiner


@pytest.fixture(autouse=True)
def _no_real_chat_config_lookup(monkeypatch):
    """chat/config.py::resolve_chat_config (Phase 3d) does a real DB lookup
    when knowledge_id is supplied — patch it to a safe openai/gpt-4.1-mini
    default for every test in this file so no test needs a live DB."""
    import chat.config as chat_config_module

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)


class _FakeStructuredChain:
    """Stands in for `prompt | llm.with_structured_output(...)`."""

    def __init__(self, parsed_query, raise_error=None):
        self._parsed_query = parsed_query
        self._raise_error = raise_error

    async def ainvoke(self, inputs):
        if self._raise_error:
            raise self._raise_error
        return {
            "parsed": refiner.RefinedQuery(refined_query=self._parsed_query),
            "raw": None,
        }


class _FakePrompt:
    def __init__(self, chain):
        self._chain = chain

    def __or__(self, other):
        return self._chain


class _FakeLLM:
    def __init__(self, chain):
        self._chain = chain

    def with_structured_output(self, schema, include_raw=True):
        return self._chain


def _patch_prompt_template(monkeypatch, chain):
    """query_refiner_node builds its own ChatPromptTemplate; short-circuit
    `prompt | llm.with_structured_output(...)` to return our fake chain by
    patching ChatPromptTemplate.from_messages to hand back a passthrough."""
    fake_prompt = _FakePrompt(chain)
    monkeypatch.setattr(
        refiner.ChatPromptTemplate, "from_messages", staticmethod(lambda messages: fake_prompt)
    )


def _patch_get_llm(monkeypatch, chain, provider="openai", key_type="platform-key"):
    """Patches chat.byok_fallback.get_llm, not refiner.get_llm — query_refiner.py
    no longer imports get_llm directly. Since Phase "BYOK invalid-key fallback",
    _run_refinement_chain routes the LLM build + invoke through
    chat.byok_fallback.run_with_key_fallback, which calls get_llm from its OWN
    module namespace (chat/byok_fallback.py's `from chat.llm_factory import
    get_llm`) — so that is the seam a test has to patch to intercept it."""
    async def _fake(*args, **kwargs):
        return _FakeLLM(chain), provider, key_type
    monkeypatch.setattr(byok_fallback, "get_llm", _fake)


def _patch_usage_event(monkeypatch):
    async def _fake(**kwargs):
        return None
    monkeypatch.setattr("utils.qdrant.usage_tracking.send_llm_usage_event", _fake)


# ---------------------------------------------------------------------------
# No-history skip
# ---------------------------------------------------------------------------

def test_skips_refinement_when_no_history(monkeypatch):
    result = asyncio.run(
        refiner.query_refiner_node(
            query="what about it", past_messages=None, owner_id=None, knowledge_id="kb-1"
        )
    )
    assert result == {"refined_query": "what about it"}


def test_skips_refinement_when_history_is_empty_list(monkeypatch):
    result = asyncio.run(
        refiner.query_refiner_node(
            query="what about it", past_messages=[], owner_id=None, knowledge_id="kb-1"
        )
    )
    assert result == {"refined_query": "what about it"}


# ---------------------------------------------------------------------------
# Phase 3d — provider/model resolved via chat/config.py, not hardcoded
# ---------------------------------------------------------------------------

def test_refiner_uses_resolved_provider_and_model_from_config(monkeypatch):
    import chat.config as chat_config_module

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return chat_config_module.ResolvedChatConfig(
            llm_provider="anthropic", llm_model="claude-haiku-4-5", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    chain = _FakeStructuredChain(parsed_query="refined")
    _patch_prompt_template(monkeypatch, chain)
    _patch_usage_event(monkeypatch)

    captured = {}

    async def _fake_get_llm(*args, provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        return _FakeLLM(chain), provider, "platform-key"

    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    past_messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    result = asyncio.run(
        refiner.query_refiner_node(
            query="follow up", past_messages=past_messages, owner_id=None, knowledge_id="kb-1"
        )
    )

    assert result["refined_query"] == "refined"
    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-haiku-4-5"


def test_refiner_passes_none_provider_model_when_no_knowledge_id(monkeypatch):
    """No knowledge_id -> resolve_chat_config is skipped entirely -> get_llm
    falls back to its own hardcoded default, same as before Phase 3d."""
    chain = _FakeStructuredChain(parsed_query="refined")
    _patch_prompt_template(monkeypatch, chain)
    _patch_usage_event(monkeypatch)

    captured = {}

    async def _fake_get_llm(*args, provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        return _FakeLLM(chain), "openai", "platform-key"

    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    past_messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    result = asyncio.run(
        refiner.query_refiner_node(
            query="follow up", past_messages=past_messages, owner_id=None, knowledge_id=None
        )
    )

    assert result["refined_query"] == "refined"
    assert captured["provider"] is None
    assert captured["model"] is None


def test_refiner_forwards_request_chat_config_provider_and_model(monkeypatch):
    """Level-1 config passthrough: query_refiner_node's request_chat_config
    dict must reach resolve_chat_config as request_provider/request_model.
    system_prompt is deliberately NOT part of this node's request_chat_config
    consumption (see the node's docstring) — not asserted here."""
    import chat.config as chat_config_module
    captured_kwargs = {}

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        captured_kwargs.update(kwargs)
        return chat_config_module.ResolvedChatConfig(
            llm_provider=kwargs.get("request_provider") or "openai",
            llm_model=kwargs.get("request_model") or "gpt-4.1-mini",
            temperature=0.0, system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    chain = _FakeStructuredChain(parsed_query="refined")
    _patch_prompt_template(monkeypatch, chain)
    _patch_usage_event(monkeypatch)
    _patch_get_llm(monkeypatch, chain)

    past_messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    asyncio.run(
        refiner.query_refiner_node(
            query="follow up", past_messages=past_messages, owner_id=None, knowledge_id="kb-1",
            request_chat_config={"provider": "anthropic", "model": "claude-haiku-4-5", "system_prompt": "ignored here"},
        )
    )

    assert captured_kwargs["request_provider"] == "anthropic"
    assert captured_kwargs["request_model"] == "claude-haiku-4-5"


def test_refiner_request_chat_config_none_forwards_no_overrides(monkeypatch):
    import chat.config as chat_config_module
    captured_kwargs = {}

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        captured_kwargs.update(kwargs)
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    chain = _FakeStructuredChain(parsed_query="refined")
    _patch_prompt_template(monkeypatch, chain)
    _patch_usage_event(monkeypatch)
    _patch_get_llm(monkeypatch, chain)

    past_messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    asyncio.run(
        refiner.query_refiner_node(
            query="follow up", past_messages=past_messages, owner_id=None, knowledge_id="kb-1",
        )
    )

    assert captured_kwargs["request_provider"] is None
    assert captured_kwargs["request_model"] is None


# ---------------------------------------------------------------------------
# Successful refinement
# ---------------------------------------------------------------------------

def test_refines_query_using_history(monkeypatch):
    chain = _FakeStructuredChain(parsed_query="Tell me more about invoices from March")
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)
    _patch_usage_event(monkeypatch)

    past_messages = [
        {"role": "user", "content": "What invoices do we have from March?"},
        {"role": "assistant", "content": "There are 3 invoices from March."},
    ]

    result = asyncio.run(
        refiner.query_refiner_node(
            query="tell me more",
            past_messages=past_messages,
            owner_id="owner-1",
            knowledge_id="kb-1",
            session_id="sess-1",
        )
    )

    assert result["refined_query"] == "Tell me more about invoices from March"
    assert "usage_by_model" in result
    assert "service_calls" in result
    assert result["service_calls"][0]["service"] == "refiner"


# ---------------------------------------------------------------------------
# Fallback-on-primary-failure
# ---------------------------------------------------------------------------

def test_falls_back_to_secondary_llm_when_primary_fails(monkeypatch):
    failing_chain = _FakeStructuredChain(parsed_query=None, raise_error=RuntimeError("primary down"))
    fallback_chain = _FakeStructuredChain(parsed_query="Refined via fallback")

    calls = {"n": 0}

    async def _fake_get_llm(*args, provider=None, model=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeLLM(failing_chain), "openai", "platform-key"
        return _FakeLLM(fallback_chain), "openai", "platform-key"

    # The prompt template must route to whichever chain get_llm most recently returned.
    class _RoutingPrompt:
        def __or__(self, other):
            return failing_chain if calls["n"] == 1 else fallback_chain

    monkeypatch.setattr(
        refiner.ChatPromptTemplate, "from_messages", staticmethod(lambda messages: _RoutingPrompt())
    )
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)
    _patch_usage_event(monkeypatch)

    past_messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    result = asyncio.run(
        refiner.query_refiner_node(
            query="follow up", past_messages=past_messages, owner_id=None, knowledge_id="kb-1"
        )
    )

    assert result["refined_query"] == "Refined via fallback"
    assert calls["n"] == 2


def test_returns_original_query_when_both_llms_fail(monkeypatch):
    failing_chain = _FakeStructuredChain(parsed_query=None, raise_error=RuntimeError("down"))
    _patch_prompt_template(monkeypatch, failing_chain)

    async def _fake_get_llm(*args, **kwargs):
        return _FakeLLM(failing_chain), "openai", "platform-key"

    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)

    past_messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    result = asyncio.run(
        refiner.query_refiner_node(
            query="original query", past_messages=past_messages, owner_id=None, knowledge_id="kb-1"
        )
    )

    assert result == {"refined_query": "original query"}


# ---------------------------------------------------------------------------
# Long-history truncation (25-message cap on user turns)
# ---------------------------------------------------------------------------

def test_truncates_history_to_last_25_user_turns(monkeypatch):
    chain = _FakeStructuredChain(parsed_query="refined")
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)
    _patch_usage_event(monkeypatch)

    # 30 user/assistant turn pairs -> 60 messages, well past the 25-user-turn cap.
    past_messages = []
    for i in range(30):
        past_messages.append({"role": "user", "content": f"question {i}"})
        past_messages.append({"role": "assistant", "content": f"answer {i}"})

    result = asyncio.run(
        refiner.query_refiner_node(
            query="latest question", past_messages=past_messages, owner_id=None, knowledge_id="kb-1"
        )
    )

    assert result["refined_query"] == "refined"
