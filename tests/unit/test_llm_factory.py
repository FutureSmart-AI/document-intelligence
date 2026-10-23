"""Unit tests for chat/llm_factory.py and chat/credentials.py (Phase 2 —
see claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests: no real network, no real LLM clients constructed with real
keys. httpx.AsyncClient is monkeypatched with a fake response; provider
clients are exercised only through construction (langchain client __init__
does not call out to the network), verifying the right class/kwargs were
chosen.
"""

import asyncio

import httpx
import pytest

import chat.credentials as credentials
import chat.llm_factory as factory


# ---------------------------------------------------------------------------
# normalize_provider_and_model
# ---------------------------------------------------------------------------

def test_normalize_infers_groq_from_oss_model_id_over_wrong_provider():
    provider, model = factory.normalize_provider_and_model("openai", "llama-3.3-70b-versatile")
    assert (provider, model) == ("groq", "llama-3.3-70b-versatile")


def test_normalize_infers_provider_from_model_when_missing():
    provider, model = factory.normalize_provider_and_model(None, "gemini-2.5-flash-lite")
    assert (provider, model) == ("google", "gemini-2.5-flash-lite")


def test_normalize_falls_back_to_openai_when_nothing_inferable():
    provider, model = factory.normalize_provider_and_model(None, "gpt-4.1-mini")
    assert (provider, model) == ("openai", "gpt-4.1-mini")


def test_normalize_applies_gemini_alias():
    provider, model = factory.normalize_provider_and_model("gemini", "gemini-2.5-flash-lite")
    assert provider == "google"


def test_normalize_keeps_unknown_provider_when_not_inferable():
    provider, model = factory.normalize_provider_and_model("bogus", "gpt-4.1-mini")
    assert provider == "bogus"  # caught later by _create_llm's else branch, not here


# ---------------------------------------------------------------------------
# get_llm — provider/model dispatch + owner credentials
# ---------------------------------------------------------------------------

def _no_owner_key(monkeypatch):
    # force_platform_key: get_llm always passes it now (BYOK invalid-key
    # fallback's chat/byok_fallback.py retry path sets it True); the fake must
    # accept it even though this helper's tests don't exercise it.
    async def _fake(owner_id, provider, default_key, knowledge_id=None, force_platform_key=False):
        return default_key, "platform-key"
    monkeypatch.setattr(factory, "get_api_key_async", _fake)


def test_get_llm_defaults_to_openai_hardcoded_model(monkeypatch):
    _no_owner_key(monkeypatch)
    llm, provider, key_type = asyncio.run(factory.get_llm(owner_id=None))
    assert provider == "openai"
    assert key_type == "platform-key"
    assert llm.model_name == factory.HARDCODED_DEFAULT_MODEL


def test_get_llm_builds_groq_client_for_groq_provider(monkeypatch):
    _no_owner_key(monkeypatch)
    llm, provider, key_type = asyncio.run(
        factory.get_llm(owner_id=None, provider="groq", model="llama-3.3-70b-versatile")
    )
    assert provider == "groq"
    assert llm.model_name == "llama-3.3-70b-versatile"


def test_get_llm_uses_owner_key_when_available(monkeypatch):
    async def _fake(owner_id, provider, default_key, knowledge_id=None, force_platform_key=False):
        return "sk-owner-key", "owner-key"
    monkeypatch.setattr(factory, "get_api_key_async", _fake)

    llm, provider, key_type = asyncio.run(factory.get_llm(owner_id="owner-1", provider="openai", model="gpt-4.1-mini"))
    assert key_type == "owner-key"
    assert llm.openai_api_key.get_secret_value() == "sk-owner-key"


def test_get_llm_attaches_rate_limiter(monkeypatch):
    _no_owner_key(monkeypatch)
    llm, _, _ = asyncio.run(factory.get_llm(owner_id=None))
    assert llm.rate_limiter is factory.apply_llm_limits.__globals__["LLM_RATE_LIMITER"]


def test_get_llm_unknown_provider_falls_back_to_openai_default(monkeypatch):
    _no_owner_key(monkeypatch)
    llm, provider, _ = asyncio.run(
        factory.get_llm(owner_id=None, provider="bogus", model="some-model")
    )
    # normalize_provider_and_model can't infer "some-model" -> provider stays "bogus",
    # _create_llm's else branch then forces the hardcoded openai default model.
    assert llm.model_name == factory.HARDCODED_DEFAULT_MODEL


def test_get_llm_gpt5_gets_reasoning_effort_when_not_function_calling(monkeypatch):
    _no_owner_key(monkeypatch)
    llm, _, _ = asyncio.run(
        factory.get_llm(owner_id=None, provider="openai", model="gpt-5-mini", for_function_calling=False)
    )
    assert llm.reasoning_effort == "low"


def test_get_llm_gpt5_sends_explicit_none_reasoning_effort_for_function_calling(monkeypatch):
    # Omitting reasoning_effort is NOT equivalent to "none" for every GPT-5
    # snapshot (some, like gpt-5.6-luna, 400 on a tool-bound call with the
    # parameter absent) — function-calling call sites must always send the
    # explicit value, never omit it.
    _no_owner_key(monkeypatch)
    llm, _, _ = asyncio.run(
        factory.get_llm(owner_id=None, provider="openai", model="gpt-5-mini", for_function_calling=True)
    )
    assert llm.reasoning_effort == "none"


# ---------------------------------------------------------------------------
# chat/credentials.py — async owner credential lookup
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, response, raise_error=None):
        self._response = response
        self._raise_error = raise_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, endpoint, headers=None):
        if self._raise_error:
            raise self._raise_error
        return self._response


def _patch_client(monkeypatch, response=None, raise_error=None):
    def _factory(*args, **kwargs):
        return _FakeAsyncClient(response, raise_error)
    monkeypatch.setattr(credentials.httpx, "AsyncClient", _factory)


def test_get_owner_credential_async_returns_key_on_valid_response(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse(200, {"is_valid": True, "api_key": "sk-abc"}))
    monkeypatch.setattr(credentials, "USAGE_TRACKING_BASE_URL", "http://usage.local")

    result = asyncio.run(credentials.get_owner_credential_async("owner-1", "openai"))
    assert result == "sk-abc"


def test_get_owner_credential_async_returns_none_on_invalid_flag(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse(200, {"is_valid": False, "api_key": "sk-abc"}))
    monkeypatch.setattr(credentials, "USAGE_TRACKING_BASE_URL", "http://usage.local")

    result = asyncio.run(credentials.get_owner_credential_async("owner-1", "openai"))
    assert result is None


def test_get_owner_credential_async_returns_none_on_404(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse(404))
    monkeypatch.setattr(credentials, "USAGE_TRACKING_BASE_URL", "http://usage.local")

    result = asyncio.run(credentials.get_owner_credential_async("owner-1", "openai"))
    assert result is None


def test_get_owner_credential_async_swallows_http_error(monkeypatch):
    _patch_client(monkeypatch, raise_error=httpx.ConnectTimeout("timed out"))
    monkeypatch.setattr(credentials, "USAGE_TRACKING_BASE_URL", "http://usage.local")

    result = asyncio.run(credentials.get_owner_credential_async("owner-1", "openai"))
    assert result is None


def test_get_owner_credential_async_returns_none_when_base_url_unset(monkeypatch):
    monkeypatch.setattr(credentials, "USAGE_TRACKING_BASE_URL", None)
    result = asyncio.run(credentials.get_owner_credential_async("owner-1", "openai"))
    assert result is None


def test_get_owner_credential_async_returns_none_without_owner_id(monkeypatch):
    monkeypatch.setattr(credentials, "USAGE_TRACKING_BASE_URL", "http://usage.local")
    result = asyncio.run(credentials.get_owner_credential_async("", "openai"))
    assert result is None


def test_get_api_key_async_falls_back_to_default_when_no_owner_key(monkeypatch):
    async def _fake(owner_id, provider):
        return None
    monkeypatch.setattr(credentials, "get_owner_credential_async", _fake)

    api_key, key_type = asyncio.run(credentials.get_api_key_async("owner-1", "openai", "default-key"))
    assert (api_key, key_type) == ("default-key", "platform-key")


def test_get_api_key_async_uses_owner_key_when_present(monkeypatch):
    async def _fake(owner_id, provider):
        return "owner-key-value"
    monkeypatch.setattr(credentials, "get_owner_credential_async", _fake)

    api_key, key_type = asyncio.run(credentials.get_api_key_async("owner-1", "openai", "default-key"))
    assert (api_key, key_type) == ("owner-key-value", "owner-key")


def test_get_api_key_async_defaults_when_owner_id_missing(monkeypatch):
    api_key, key_type = asyncio.run(credentials.get_api_key_async(None, "openai", "default-key"))
    assert (api_key, key_type) == ("default-key", "platform-key")


# ---------------------------------------------------------------------------
# knowledge_id-preferred resolution (in-process, no external HTTP call)
# ---------------------------------------------------------------------------

def test_resolve_owner_credential_async_prefers_knowledge_id_over_http(monkeypatch):
    """When knowledge_id is supplied, resolution must go through
    get_credential_for_knowledge — never touch the owner_id HTTP path, even
    if owner_id is also present."""
    async def _fail_if_called(*args, **kwargs):
        raise AssertionError("should not call the HTTP-based owner_id path when knowledge_id is given")
    monkeypatch.setattr(credentials, "get_owner_credential_async", _fail_if_called)

    async def _fake_get_credential_for_knowledge(knowledge_id, provider):
        assert knowledge_id == "kb-1"
        return "sk-kb-key", "owner-key"
    monkeypatch.setattr(credentials, "get_credential_for_knowledge", _fake_get_credential_for_knowledge)

    api_key, key_type = asyncio.run(
        credentials.resolve_owner_credential_async("owner-1", "openai", knowledge_id="kb-1")
    )
    assert (api_key, key_type) == ("sk-kb-key", "owner-key")


def test_get_api_key_async_passes_knowledge_id_through(monkeypatch):
    async def _fake_get_credential_for_knowledge(knowledge_id, provider):
        return (None, "platform-key") if not knowledge_id else ("sk-kb-key", "owner-key")
    monkeypatch.setattr(credentials, "get_credential_for_knowledge", _fake_get_credential_for_knowledge)

    api_key, key_type = asyncio.run(
        credentials.get_api_key_async("owner-1", "openai", "default-key", knowledge_id="kb-1")
    )
    assert (api_key, key_type) == ("sk-kb-key", "owner-key")


def test_resolve_owner_credential_async_falls_back_to_http_without_knowledge_id(monkeypatch):
    """No knowledge_id supplied — the old owner_id HTTP path is still the
    fallback, so a caller not yet updated keeps working."""
    async def _fake_get_owner_credential_async(owner_id, provider):
        assert owner_id == "owner-1"
        return "sk-owner-key"
    monkeypatch.setattr(credentials, "get_owner_credential_async", _fake_get_owner_credential_async)

    api_key, key_type = asyncio.run(
        credentials.resolve_owner_credential_async("owner-1", "openai")
    )
    assert (api_key, key_type) == ("sk-owner-key", "owner-key")


def test_get_llm_threads_knowledge_id_to_get_api_key_async(monkeypatch):
    captured = {}

    async def _fake(owner_id, provider, default_key, knowledge_id=None, force_platform_key=False):
        captured["knowledge_id"] = knowledge_id
        return default_key, "platform-key"
    monkeypatch.setattr(factory, "get_api_key_async", _fake)

    asyncio.run(factory.get_llm(owner_id="owner-1", knowledge_id="kb-1"))
    assert captured["knowledge_id"] == "kb-1"
