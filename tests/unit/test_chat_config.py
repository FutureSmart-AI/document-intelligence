"""Unit tests for chat/config.py::resolve_chat_config (Phase 3d — see
claude-logs/plan/knowledge-owned-chat-progress.md and
claude-logs/plan/knowledge-owned-chat.md's Config section).

Pure unit tests: _get_config_row (the DB read) is monkeypatched with a fake
row or None; no real DB access in any test.
"""

import asyncio

import chat.config as chat_config


class _FakeConfigRow:
    def __init__(self, llm_provider=None, llm_model=None, temperature=None,
                 system_prompt=None, show_sources=True, chat_enabled=True, follow_up=True,
                 suggested_questions=None, voice_input_enabled=False, voice_output_enabled=False,
                 max_tokens=None, include_charts=True, show_raw_response=True):
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.show_sources = show_sources
        self.chat_enabled = chat_enabled
        self.follow_up = follow_up
        self.suggested_questions = suggested_questions
        self.voice_input_enabled = voice_input_enabled
        self.voice_output_enabled = voice_output_enabled
        self.max_tokens = max_tokens
        self.include_charts = include_charts
        self.show_raw_response = show_raw_response


def _patch_row(monkeypatch, row):
    async def _fake_get_config_row(knowledge_id):
        return row
    monkeypatch.setattr(chat_config, "_get_config_row", _fake_get_config_row)


def _patch_env_defaults(monkeypatch, provider=None, model=None, temperature=None, system_prompt=None):
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_PROVIDER", provider)
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_MODEL", model)
    monkeypatch.setattr(setup_env, "RAG_CHAT_LLM_TEMPERATURE", temperature)
    monkeypatch.setattr(setup_env, "RAG_CHAT_SYSTEM_PROMPT", system_prompt)
    # Per-node env tier cleared too, so these tests are deterministic no
    # matter what RAG_CHAT_*_LLM_* vars the host environment carries.
    monkeypatch.setattr(setup_env, "RAG_CHAT_NODE_LLM_ENV", {})


# ---------------------------------------------------------------------------
# Level 4 — hardcoded default (no row, no env, no request field)
# ---------------------------------------------------------------------------

def test_falls_back_to_hardcoded_default_when_nothing_else_set(monkeypatch):
    _patch_row(monkeypatch, None)
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.llm_provider == chat_config.HARDCODED_DEFAULT_PROVIDER
    assert result.llm_model == chat_config.HARDCODED_DEFAULT_MODEL
    assert result.temperature == chat_config.HARDCODED_DEFAULT_TEMPERATURE


def test_node_hardcoded_default_is_per_node_when_nothing_else_set(monkeypatch):
    """No knowledge row, no node row, no env — each node falls back to its
    own hardcoded floor, not the shared HARDCODED_DEFAULT_MODEL."""
    _patch_row(monkeypatch, None)
    _patch_node_row(monkeypatch, None)
    _patch_env_defaults(monkeypatch)

    for node, (provider, model) in chat_config.NODE_HARDCODED_DEFAULTS.items():
        result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name=node))
        assert result.llm_provider == provider
        assert result.llm_model == model
    assert result.system_prompt is None
    assert result.show_sources is True
    assert result.chat_enabled is True


# ---------------------------------------------------------------------------
# Level 3 — env default (no row, no request field)
# ---------------------------------------------------------------------------

def test_uses_env_default_when_no_row_and_no_request_field(monkeypatch):
    _patch_row(monkeypatch, None)
    _patch_env_defaults(monkeypatch, provider="groq", model="llama-3.3-70b-versatile", temperature="0.2")

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.llm_provider == "groq"
    assert result.llm_model == "llama-3.3-70b-versatile"
    assert result.temperature == 0.2


def test_env_temperature_falls_back_to_hardcoded_on_bad_value(monkeypatch):
    _patch_row(monkeypatch, None)
    _patch_env_defaults(monkeypatch, temperature="not-a-float")

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.temperature == chat_config.HARDCODED_DEFAULT_TEMPERATURE


# ---------------------------------------------------------------------------
# Level 2 — knowledge_chat_config DB row
# ---------------------------------------------------------------------------

def test_uses_db_row_over_env_and_hardcoded(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(
        llm_provider="anthropic", llm_model="claude-haiku-4-5", temperature=0.5,
        system_prompt="Be concise.", show_sources=False, chat_enabled=True,
    ))
    _patch_env_defaults(monkeypatch, provider="groq", model="llama-3.3-70b-versatile")

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.llm_provider == "anthropic"
    assert result.llm_model == "claude-haiku-4-5"
    assert result.temperature == 0.5
    assert result.system_prompt == "Be concise."
    assert result.show_sources is False
    assert result.chat_enabled is True


def test_db_row_partial_fields_fall_through_to_env_for_the_rest(monkeypatch):
    """A row with llm_provider set but llm_model=None should resolve provider
    from the row and model from the env/hardcoded level — independent
    per-field resolution, not all-or-nothing."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="anthropic", llm_model=None, temperature=None))
    _patch_env_defaults(monkeypatch, model="gpt-5.4-mini")

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.llm_provider == "anthropic"
    assert result.llm_model == "gpt-5.4-mini"
    assert result.temperature == chat_config.HARDCODED_DEFAULT_TEMPERATURE


def test_db_row_zero_temperature_is_respected_not_treated_as_falsy(monkeypatch):
    """temperature=0.0 is a legitimate explicit value, not 'unset' — must not
    be treated as falsy and skipped in favor of env/hardcoded."""
    _patch_row(monkeypatch, _FakeConfigRow(temperature=0.0))
    _patch_env_defaults(monkeypatch, temperature="0.9")

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.temperature == 0.0


# ---------------------------------------------------------------------------
# Level 1 — request field (highest priority)
# ---------------------------------------------------------------------------

def test_request_field_overrides_db_row_and_env(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(
        llm_provider="anthropic", llm_model="claude-haiku-4-5", temperature=0.5,
        system_prompt="Be concise.",
    ))
    _patch_env_defaults(monkeypatch, provider="groq", model="llama-3.3-70b-versatile")

    result = asyncio.run(chat_config.resolve_chat_config(
        "kb-1",
        request_provider="openai",
        request_model="gpt-5.4-mini",
        request_temperature=0.9,
        request_system_prompt="Answer only in bullet points.",
    ))

    assert result.llm_provider == "openai"
    assert result.llm_model == "gpt-5.4-mini"
    assert result.temperature == 0.9
    assert result.system_prompt == "Answer only in bullet points."


def test_request_temperature_zero_is_respected(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(temperature=0.7))

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", request_temperature=0.0))

    assert result.temperature == 0.0


# ---------------------------------------------------------------------------
# show_sources / chat_enabled / follow_up — no request-level override, no env level
# ---------------------------------------------------------------------------

def test_show_sources_and_chat_enabled_default_true_without_row(monkeypatch):
    _patch_row(monkeypatch, None)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.show_sources is True
    assert result.chat_enabled is True
    assert result.follow_up is True


def test_show_sources_and_chat_enabled_come_from_row_when_present(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(show_sources=False, chat_enabled=False, follow_up=False))

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert result.show_sources is False
    assert result.chat_enabled is False
    assert result.follow_up is False


# ---------------------------------------------------------------------------
# _get_config_row — DB failure degrades to None, never raises
# ---------------------------------------------------------------------------

def test_get_config_row_returns_none_on_db_error(monkeypatch):
    from utils.db import session as db_session

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_session, "async_session", lambda: _BrokenSession())

    result = asyncio.run(chat_config._get_config_row("kb-1"))

    assert result is None


def test_get_config_row_returns_none_for_empty_knowledge_id():
    result = asyncio.run(chat_config._get_config_row(""))
    assert result is None


# ---------------------------------------------------------------------------
# Per-node tier (knowledge_chat_node_config) — sits between the request body
# and the knowledge-level row. Only consulted when node_name is supplied.
# ---------------------------------------------------------------------------

class _FakeNodeRow:
    def __init__(self, provider=None, model=None, temperature=None, max_tokens=None,
                 prompt=None, enabled=True):
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prompt = prompt
        self.enabled = enabled


def _patch_node_row(monkeypatch, row):
    async def _fake_get_node_config_row(knowledge_id, node_name):
        return row
    monkeypatch.setattr(chat_config, "_get_node_config_row", _fake_get_node_config_row)


def test_node_row_overrides_knowledge_row(monkeypatch):
    """The whole point of the tier: one node runs a different model from the
    knowledge-level default."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="openai", llm_model="gpt-5.4-mini"))
    _patch_node_row(monkeypatch, _FakeNodeRow(provider="anthropic", model="claude-opus-4-6"))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="rag_agent"))

    assert result.llm_provider == "anthropic"
    assert result.llm_model == "claude-opus-4-6"


def test_node_tier_skipped_entirely_without_node_name(monkeypatch):
    """Backward compatibility: existing callers that pass no node_name must
    not pick up a node row, even when one exists for that knowledge_id."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="openai", llm_model="gpt-5.4-mini"))
    _patch_env_defaults(monkeypatch)

    called = {"n": 0}

    async def _should_not_be_called(knowledge_id, node_name):
        called["n"] += 1
        return _FakeNodeRow(provider="anthropic", model="claude-opus-4-6")
    monkeypatch.setattr(chat_config, "_get_node_config_row", _should_not_be_called)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1"))

    assert called["n"] == 0
    assert result.llm_provider == "openai"
    assert result.llm_model == "gpt-5.4-mini"


def test_node_row_partial_fields_inherit_the_rest(monkeypatch):
    """A node row setting only prompt/temperature still inherits
    provider+model from the knowledge-level row — null means inherit.
    generate_answer is the one node the knowledge row's LLM select applies to."""
    _patch_row(monkeypatch, _FakeConfigRow(
        llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.7,
    ))
    _patch_node_row(monkeypatch, _FakeNodeRow(temperature=0.2, prompt="Be brief."))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="generate_answer"))

    assert result.llm_provider == "openai"
    assert result.llm_model == "gpt-5.4-mini"
    assert result.temperature == 0.2
    assert result.system_prompt == "Be brief."


def test_knowledge_row_llm_select_is_answer_only(monkeypatch):
    """PUT /chat/config's llm_provider/llm_model (the Settings model picker)
    select the ANSWER model only. refined_query_node and rag_agent must not
    inherit it — with no env set they land on their own hardcoded floors,
    while every non-LLM field (temperature here) still reads the row."""
    _patch_row(monkeypatch, _FakeConfigRow(
        llm_provider="anthropic", llm_model="claude-opus-4-6", temperature=0.3,
    ))
    _patch_node_row(monkeypatch, None)
    _patch_env_defaults(monkeypatch)

    answer = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="generate_answer"))
    assert answer.llm_provider == "anthropic"
    assert answer.llm_model == "claude-opus-4-6"

    for node in ("refined_query_node", "rag_agent"):
        result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name=node))
        provider, model = chat_config.NODE_HARDCODED_DEFAULTS[node]
        assert result.llm_provider == provider
        assert result.llm_model == model
        assert result.temperature == 0.3  # non-LLM fields still read the row


def test_non_answer_nodes_skip_knowledge_row_llm_and_use_env(monkeypatch):
    """With the knowledge row set AND env set, refined_query_node/rag_agent
    resolve provider/model from env (the row is skipped for them); a no-node
    call still reads the row, so knowledge-level reads round-trip unchanged."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="anthropic", llm_model="claude-opus-4-6"))
    _patch_node_row(monkeypatch, None)
    _patch_env_defaults(monkeypatch, provider="groq", model="llama-3.3-70b-versatile")

    for node in ("refined_query_node", "rag_agent"):
        result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name=node))
        assert result.llm_provider == "groq"
        assert result.llm_model == "llama-3.3-70b-versatile"

    no_node = asyncio.run(chat_config.resolve_chat_config("kb-1"))
    assert no_node.llm_provider == "anthropic"
    assert no_node.llm_model == "claude-opus-4-6"


def test_node_row_still_overrides_for_non_answer_nodes(monkeypatch):
    """The per-node DB tier (knowledge_chat_node_config) is unaffected by the
    answer-only rule — an explicit node row still wins for rag_agent."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="anthropic", llm_model="claude-opus-4-6"))
    _patch_node_row(monkeypatch, _FakeNodeRow(provider="google", model="gemini-2.5-flash"))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="rag_agent"))

    assert result.llm_provider == "google"
    assert result.llm_model == "gemini-2.5-flash"


def test_node_row_zero_temperature_overrides_nonzero_knowledge_row(monkeypatch):
    """0.0 is an explicit value, not "unset" — a truthiness check here would
    silently leak the knowledge-level 0.7 through."""
    _patch_row(monkeypatch, _FakeConfigRow(temperature=0.7))
    _patch_node_row(monkeypatch, _FakeNodeRow(temperature=0.0))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="rag_agent"))

    assert result.temperature == 0.0


def test_node_row_provider_without_model_is_ignored_as_a_pair(monkeypatch):
    """Half a pair would combine one level's provider with another's model —
    e.g. anthropic + gpt-5.4-mini — which is not a valid combination."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="openai", llm_model="gpt-5.4-mini"))
    _patch_node_row(monkeypatch, _FakeNodeRow(provider="groq"))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="generate_answer"))

    assert result.llm_provider == "openai"
    assert result.llm_model == "gpt-5.4-mini"


def test_node_row_model_without_provider_is_ignored_as_a_pair(monkeypatch):
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="openai", llm_model="gpt-5.4-mini"))
    _patch_node_row(monkeypatch, _FakeNodeRow(model="claude-opus-4-6"))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="generate_answer"))

    assert result.llm_provider == "openai"
    assert result.llm_model == "gpt-5.4-mini"


def test_request_field_overrides_node_row(monkeypatch):
    """Precedence order: request > node row > knowledge row."""
    _patch_row(monkeypatch, _FakeConfigRow(llm_provider="openai", llm_model="gpt-5.4-mini"))
    _patch_node_row(monkeypatch, _FakeNodeRow(provider="anthropic", model="claude-opus-4-6"))
    _patch_env_defaults(monkeypatch)

    result = asyncio.run(chat_config.resolve_chat_config(
        "kb-1", request_provider="groq", request_model="openai/gpt-oss-120b",
        node_name="rag_agent",
    ))

    assert result.llm_provider == "groq"
    assert result.llm_model == "openai/gpt-oss-120b"


def test_node_row_falls_through_to_env_when_no_knowledge_row(monkeypatch):
    """A node row with only a prompt, no knowledge row at all — provider and
    model come from env, two levels down."""
    _patch_row(monkeypatch, None)
    _patch_node_row(monkeypatch, _FakeNodeRow(prompt="Node suffix."))
    _patch_env_defaults(monkeypatch, provider="google", model="gemini-2.5-flash")

    result = asyncio.run(chat_config.resolve_chat_config("kb-1", node_name="rag_agent"))

    assert result.llm_provider == "google"
    assert result.llm_model == "gemini-2.5-flash"
    assert result.system_prompt == "Node suffix."


def test_get_node_config_row_returns_none_on_db_error(monkeypatch):
    from utils.db import session as db_session

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(db_session, "async_session", lambda: _BrokenSession())

    result = asyncio.run(chat_config._get_node_config_row("kb-1", "rag_agent"))

    assert result is None


def test_get_node_config_row_returns_none_for_empty_args():
    assert asyncio.run(chat_config._get_node_config_row("", "rag_agent")) is None
    assert asyncio.run(chat_config._get_node_config_row("kb-1", "")) is None
