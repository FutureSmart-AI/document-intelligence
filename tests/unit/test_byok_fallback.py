"""Unit tests for chat/byok_fallback.py — the same-request platform-key retry
(BYOK invalid-key fallback).

Pure unit tests: get_llm, mark_credential_failed, and platform_fallback_allowed
are monkeypatched with fakes; no real LLM call, DB session, or network I/O.
classify_provider_error/is_terminal are the REAL functions (already covered by
tests/unit/test_credential_health.py) — using the real ones here checks the
integration, not just the policy in isolation.
"""

import asyncio

import pytest

import chat.byok_fallback as byok_fallback
from chat.byok_fallback import ByokKeyRejected, run_with_key_fallback


class _AuthError(Exception):
    def __init__(self, message="Invalid API Key provided"):
        super().__init__(message)


class _RateLimitError(Exception):
    def __init__(self, message="rate_limit_exceeded"):
        super().__init__(message)


def _patch_get_llm(monkeypatch, responses):
    """responses: list of (llm_marker, provider, key_type) tuples, consumed in
    order across successive get_llm calls (primary, then retry if any)."""
    calls = {"n": 0}

    async def _fake(*args, **kwargs):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[i]

    monkeypatch.setattr(byok_fallback, "get_llm", _fake)
    return calls


def _patch_demotion(monkeypatch, *, allowed: bool):
    demoted = {"called_with": None}

    async def _fake_mark_failed(owner_id, provider, error_type, message=""):
        demoted["called_with"] = (owner_id, provider, error_type)
        return True

    async def _fake_allowed(owner_id, operation):
        return allowed, ("within_limit" if allowed else "over_limit")

    monkeypatch.setattr(byok_fallback, "mark_credential_failed", _fake_mark_failed)
    monkeypatch.setattr(byok_fallback, "platform_fallback_allowed", _fake_allowed)
    return demoted


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Happy path — no failure at all
# ---------------------------------------------------------------------------

def test_no_retry_when_call_succeeds(monkeypatch):
    calls = _patch_get_llm(monkeypatch, [("primary-llm", "openai", "owner-key")])

    async def _call(llm):
        assert llm == "primary-llm"
        return "the answer"

    outcome = _run(run_with_key_fallback(_call, owner_id="owner-1"))

    assert outcome.value == "the answer"
    assert outcome.provider == "openai"
    assert outcome.key_type == "owner-key"
    assert outcome.warning is None
    assert not outcome.fell_back
    assert calls["n"] == 1  # get_llm called once — no retry needed


# ---------------------------------------------------------------------------
# Terminal failure, owner-key, within limit -> retries and succeeds
# ---------------------------------------------------------------------------

def test_retries_on_platform_key_after_auth_error(monkeypatch):
    calls = _patch_get_llm(monkeypatch, [
        ("primary-llm", "openai", "owner-key"),
        ("platform-llm", "openai", "platform-key"),
    ])
    demoted = _patch_demotion(monkeypatch, allowed=True)

    attempts = {"n": 0}

    async def _call(llm):
        attempts["n"] += 1
        if llm == "primary-llm":
            raise _AuthError()
        return f"answer from {llm}"

    outcome = _run(run_with_key_fallback(_call, owner_id="owner-1"))

    assert outcome.value == "answer from platform-llm"
    assert outcome.key_type == "platform-key"
    assert outcome.fell_back
    assert outcome.warning["code"] == "byok_key_invalid"
    assert outcome.warning["provider"] == "openai"
    assert calls["n"] == 2  # primary + retry
    assert attempts["n"] == 2
    assert demoted["called_with"] == ("owner-1", "openai", "auth_error")


# ---------------------------------------------------------------------------
# Terminal failure, owner-key, OVER limit -> no coverage, raises
# ---------------------------------------------------------------------------

def test_raises_byok_key_rejected_when_over_limit(monkeypatch):
    calls = _patch_get_llm(monkeypatch, [("primary-llm", "anthropic", "owner-key")])
    demoted = _patch_demotion(monkeypatch, allowed=False)

    async def _call(llm):
        raise _AuthError("authentication_error")

    with pytest.raises(ByokKeyRejected) as exc_info:
        _run(run_with_key_fallback(_call, owner_id="owner-1"))

    err = exc_info.value
    assert err.provider == "anthropic"
    assert err.error_type == "auth_error"
    assert err.reason == "over_limit"
    assert "usage limit" in err.user_message
    assert calls["n"] == 1  # never built a retry model
    assert demoted["called_with"] == ("owner-1", "anthropic", "auth_error")


# ---------------------------------------------------------------------------
# Non-terminal failure — must NOT retry or demote
# ---------------------------------------------------------------------------

def test_rate_limit_reraises_without_retry_or_demotion(monkeypatch):
    calls = _patch_get_llm(monkeypatch, [("primary-llm", "groq", "owner-key")])
    demoted = _patch_demotion(monkeypatch, allowed=True)

    async def _call(llm):
        raise _RateLimitError()

    with pytest.raises(_RateLimitError):
        _run(run_with_key_fallback(_call, owner_id="owner-1"))

    assert calls["n"] == 1  # no retry attempted
    assert demoted["called_with"] is None  # key must survive a transient rate limit


# ---------------------------------------------------------------------------
# Primary already on the platform key — nothing to fall back FROM
# ---------------------------------------------------------------------------

def test_platform_key_failure_reraises_without_retry(monkeypatch):
    calls = _patch_get_llm(monkeypatch, [("primary-llm", "openai", "platform-key")])
    demoted = _patch_demotion(monkeypatch, allowed=True)

    async def _call(llm):
        raise _AuthError()

    with pytest.raises(_AuthError):
        _run(run_with_key_fallback(_call, owner_id="owner-1"))

    assert calls["n"] == 1
    assert demoted["called_with"] is None


# ---------------------------------------------------------------------------
# Warning collection (contextvar) — drained once per turn, deduped by provider
# ---------------------------------------------------------------------------

def test_collect_and_drain_warnings_dedupes_by_provider():
    byok_fallback.reset_warnings()
    byok_fallback.collect_warning({"provider": "openai", "code": "byok_key_invalid"})
    byok_fallback.collect_warning({"provider": "openai", "code": "byok_key_invalid"})
    byok_fallback.collect_warning({"provider": "anthropic", "code": "byok_key_invalid"})

    warnings = byok_fallback.drain_warnings()

    assert len(warnings) == 2
    assert {w["provider"] for w in warnings} == {"openai", "anthropic"}
    # Draining stops collection — a stray late call after the turn is a no-op
    # rather than leaking into the NEXT turn's warnings.
    byok_fallback.collect_warning({"provider": "google", "code": "byok_key_invalid"})
    assert byok_fallback.drain_warnings() == []
