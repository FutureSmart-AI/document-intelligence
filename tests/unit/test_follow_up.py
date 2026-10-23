"""Unit tests for chat/follow_up.py (Follow-up generation — see
claude-logs/plan/chat-migration/knowledge-owned-chat.md's "Follow-up
questions — IN scope" section).

Pure unit tests: get_llm is monkeypatched with a fake chain; no real LLM
call or network I/O.
"""

import asyncio

import chat.follow_up as follow_up_module


class _FakeStructuredChain:
    def __init__(self, follow_ups=None, raise_error=None):
        self._follow_ups = follow_ups if follow_ups is not None else ["Q1?", "Q2?"]
        self._raise_error = raise_error
        self.last_invoke_payload = None

    async def ainvoke(self, inputs):
        self.last_invoke_payload = inputs
        if self._raise_error:
            raise self._raise_error
        return follow_up_module.FollowUpResponse(follow_up=self._follow_ups)


class _FakePrompt:
    def __init__(self, chain):
        self._chain = chain

    def __or__(self, other):
        return self._chain


class _FakeLLM:
    def __init__(self, chain):
        self._chain = chain

    def with_structured_output(self, schema):
        return self._chain


def _patch_prompt_template(monkeypatch, chain):
    fake_prompt = _FakePrompt(chain)
    monkeypatch.setattr(
        follow_up_module.ChatPromptTemplate, "from_messages", staticmethod(lambda messages: fake_prompt)
    )


def _patch_get_llm(monkeypatch, chain, provider="openai", key_type="platform-key"):
    """Patches chat.byok_fallback.get_llm, not chat.llm_factory.get_llm — since
    BYOK invalid-key fallback, chat/follow_up.py routes the LLM build + invoke
    through chat.byok_fallback.run_with_key_fallback, which calls get_llm bound
    into ITS OWN module namespace at import time (`from chat.llm_factory import
    get_llm`). Patching chat.llm_factory.get_llm after that import does not
    change byok_fallback's already-bound name, so this is the seam that
    actually intercepts the call."""
    async def _fake(*args, **kwargs):
        return _FakeLLM(chain), provider, key_type
    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake)


def test_generates_two_follow_up_questions(monkeypatch):
    chain = _FakeStructuredChain(follow_ups=["What about X?", "How does Y work?"])
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    result = asyncio.run(follow_up_module.generate_follow_up_questions(
        query="what is the ECR link?",
        response="The ECR link is 123.dkr.ecr.us-east-1.amazonaws.com",
        owner_id="owner-1",
        llm_provider="openai",
        llm_model="gpt-5.4-mini",
    ))

    assert result == ["What about X?", "How does Y work?"]


def test_falls_back_on_llm_error(monkeypatch):
    chain = _FakeStructuredChain(raise_error=RuntimeError("LLM exploded"))
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    result = asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="some answer", owner_id=None,
        llm_provider="openai", llm_model="gpt-5.4-mini",
    ))

    assert result == follow_up_module._FALLBACK_FOLLOW_UPS


def test_falls_back_when_result_has_empty_list(monkeypatch):
    chain = _FakeStructuredChain(follow_ups=[])
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    result = asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="some answer", owner_id=None,
        llm_provider="openai", llm_model="gpt-5.4-mini",
    ))

    assert result == follow_up_module._FALLBACK_FOLLOW_UPS


def test_uses_resolved_provider_and_model(monkeypatch):
    """Follow-up generation must reuse the turn's resolved rag_answer_node
    model, not a hardcoded model — see the module docstring. Isolated from
    whatever FOLLOW_UP_LLM_PROVIDER/_MODEL the real environment (.env.local
    sets these) happens to have, since this test is specifically about the
    no-override fallback path."""
    chain = _FakeStructuredChain()
    _patch_prompt_template(monkeypatch, chain)

    captured = {}

    async def _fake_get_llm(*args, provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        return _FakeLLM(chain), provider, "platform-key"

    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "FOLLOW_UP_LLM_PROVIDER", None)
    monkeypatch.setattr(setup_env, "FOLLOW_UP_LLM_MODEL", None)

    asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="answer", owner_id=None,
        llm_provider="anthropic", llm_model="claude-haiku-4-5",
    ))

    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-haiku-4-5"


def test_env_override_replaces_resolved_model(monkeypatch):
    """FOLLOW_UP_LLM_PROVIDER/_MODEL, when both set, must override the
    passed-in generate_answer model."""
    chain = _FakeStructuredChain()
    _patch_prompt_template(monkeypatch, chain)

    captured = {}

    async def _fake_get_llm(*args, provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        return _FakeLLM(chain), provider, "platform-key"

    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "FOLLOW_UP_LLM_PROVIDER", "groq")
    monkeypatch.setattr(setup_env, "FOLLOW_UP_LLM_MODEL", "llama-3.3-70b")

    asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="answer", owner_id=None,
        llm_provider="anthropic", llm_model="claude-haiku-4-5",
    ))

    assert captured["provider"] == "groq"
    assert captured["model"] == "llama-3.3-70b"


def test_env_override_ignored_when_only_one_is_set(monkeypatch):
    """A half-override (only provider or only model set) must fall through
    to the passed-in generate_answer model for both, not silently pair an
    overridden provider with a model id from a different catalogue."""
    chain = _FakeStructuredChain()
    _patch_prompt_template(monkeypatch, chain)

    captured = {}

    async def _fake_get_llm(*args, provider=None, model=None, **kwargs):
        captured["provider"] = provider
        captured["model"] = model
        return _FakeLLM(chain), provider, "platform-key"

    import chat.byok_fallback as byok_fallback
    monkeypatch.setattr(byok_fallback, "get_llm", _fake_get_llm)
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "FOLLOW_UP_LLM_PROVIDER", "groq")
    monkeypatch.setattr(setup_env, "FOLLOW_UP_LLM_MODEL", None)

    asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="answer", owner_id=None,
        llm_provider="anthropic", llm_model="claude-haiku-4-5",
    ))

    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-haiku-4-5"


def test_includes_context_in_invoke_payload_when_supplied(monkeypatch):
    chain = _FakeStructuredChain()
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="answer", owner_id=None,
        llm_provider="openai", llm_model="gpt-5.4-mini",
        context="Some retrieved context text.",
    ))

    assert chain.last_invoke_payload["context"] == "Some retrieved context text."


def test_omits_context_from_invoke_payload_when_not_supplied(monkeypatch):
    chain = _FakeStructuredChain()
    _patch_prompt_template(monkeypatch, chain)
    _patch_get_llm(monkeypatch, chain)

    asyncio.run(follow_up_module.generate_follow_up_questions(
        query="hello", response="answer", owner_id=None,
        llm_provider="openai", llm_model="gpt-5.4-mini",
    ))

    assert "context" not in chain.last_invoke_payload


# ── follow_up_node — the graph node wrapper (gate, timeout, never raise) ──
# Moved here from routers/chat.py's _maybe_generate_follow_up (2026-08-25) so
# the generation call lands inside the turn's LangSmith trace. These tests
# cover the gate; generate_follow_up_questions itself is covered above.


class _FakeChatConfig:
    def __init__(self, follow_up=True):
        self.follow_up = follow_up
        self.llm_provider = "openai"
        self.llm_model = "gpt-5.4-mini"


def _patch_generation(monkeypatch, calls, result=None):
    async def _fake(**kwargs):
        calls.append(kwargs)
        return result if result is not None else ["Q1?", "Q2?"]

    monkeypatch.setattr(follow_up_module, "generate_follow_up_questions", _fake)


def test_node_generates_when_answered_and_enabled(monkeypatch):
    calls = []
    _patch_generation(monkeypatch, calls)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="what is X?", response="X is a thing.", is_answered="yes",
        chat_config=_FakeChatConfig(),
    ))

    assert out == {"follow_up": ["Q1?", "Q2?"]}
    assert calls[0]["query"] == "what is X?"


def test_node_skips_when_flag_disabled(monkeypatch):
    calls = []
    _patch_generation(monkeypatch, calls)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="q", response="an answer", is_answered="yes",
        chat_config=_FakeChatConfig(follow_up=False),
    ))

    assert out == {"follow_up": None}
    assert calls == []


def test_node_skips_without_chat_config(monkeypatch):
    """chat/report_execution.py runs the same graph for batch reports and
    resolves no chat config — there is no user to suggest questions to."""
    calls = []
    _patch_generation(monkeypatch, calls)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="q", response="an answer", is_answered="yes", chat_config=None,
    ))

    assert out == {"follow_up": None}
    assert calls == []


def test_node_skips_unanswered_turn(monkeypatch):
    calls = []
    _patch_generation(monkeypatch, calls)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="q", response="some text", is_answered="no",
        chat_config=_FakeChatConfig(),
    ))

    assert out == {"follow_up": None}
    assert calls == []


def test_node_skips_empty_answer(monkeypatch):
    calls = []
    _patch_generation(monkeypatch, calls)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="q", response="   ", is_answered="yes",
        chat_config=_FakeChatConfig(),
    ))

    assert out == {"follow_up": None}
    assert calls == []


def test_node_swallows_generation_failure(monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(follow_up_module, "generate_follow_up_questions", _boom)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="q", response="an answer", is_answered="yes",
        chat_config=_FakeChatConfig(),
    ))

    assert out == {"follow_up": None}


def test_node_swallows_timeout(monkeypatch):
    async def _slow(**kwargs):
        await asyncio.sleep(5)
        return ["never"]

    monkeypatch.setattr(follow_up_module, "generate_follow_up_questions", _slow)
    monkeypatch.setattr("utils.setup_env.RAG_FOLLOW_UP_TIMEOUT_SEC", 0.01)

    out = asyncio.run(follow_up_module.follow_up_node(
        query="q", response="an answer", is_answered="yes",
        chat_config=_FakeChatConfig(),
    ))

    assert out == {"follow_up": None}
