"""Unit tests for utils/usage/credential_health.py's pure classification logic
(BYOK invalid-key fallback).

Pure unit tests: classify_provider_error and is_terminal take a plain exception
and a string respectively — no DB, no network, no monkeypatching needed. The
DB-touching half (mark_credential_failed, platform_fallback_allowed,
schedule_revalidation_if_stale) is exercised indirectly through
tests/unit/test_byok_fallback.py's fakes instead of here, since testing it
directly would mean faking a whole AsyncSession for very little extra signal.
"""

from utils.usage.credential_health import classify_provider_error, is_terminal


class _FakeProviderError(Exception):
    """Stands in for an SDK exception carrying an HTTP status, without pulling
    in openai/anthropic/groq/google as test dependencies."""

    def __init__(self, message: str, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# classify_provider_error — status-code path
# ---------------------------------------------------------------------------

def test_401_classifies_as_auth_error():
    assert classify_provider_error(_FakeProviderError("nope", status_code=401)) == "auth_error"


def test_403_classifies_as_auth_error():
    assert classify_provider_error(_FakeProviderError("forbidden", status_code=403)) == "auth_error"


def test_402_classifies_as_low_balance():
    assert classify_provider_error(_FakeProviderError("go pay", status_code=402)) == "low_balance"


def test_429_without_quota_text_classifies_as_rate_limit():
    assert classify_provider_error(_FakeProviderError("slow down", status_code=429)) == "rate_limit"


def test_429_with_insufficient_quota_text_classifies_as_low_balance():
    """OpenAI's actual shape: 429 + code=insufficient_quota. Status alone is
    ambiguous between rate_limit and low_balance — text must win."""
    exc = _FakeProviderError("Error code: 429 - insufficient_quota exceeded", status_code=429)
    assert classify_provider_error(exc) == "low_balance"


# ---------------------------------------------------------------------------
# classify_provider_error — text-only path (no status_code attribute)
# ---------------------------------------------------------------------------

def test_invalid_api_key_text_classifies_as_auth_error():
    assert classify_provider_error(Exception("Invalid API Key provided: sk-***")) == "auth_error"


def test_insufficient_quota_text_classifies_as_low_balance():
    assert classify_provider_error(Exception("You exceeded your current quota")) == "low_balance"


def test_timeout_classifies_as_none():
    """Not a credential problem — must not demote a key over a network blip."""
    assert classify_provider_error(TimeoutError("Request timed out")) is None


def test_model_not_found_classifies_as_none():
    assert classify_provider_error(Exception("The model 'gpt-9' does not exist")) is None


def test_provider_500_classifies_as_none():
    assert classify_provider_error(_FakeProviderError("internal error", status_code=500)) is None


# ---------------------------------------------------------------------------
# is_terminal — only auth_error/low_balance demote a key
# ---------------------------------------------------------------------------

def test_auth_error_is_terminal():
    assert is_terminal("auth_error") is True


def test_low_balance_is_terminal():
    assert is_terminal("low_balance") is True


def test_rate_limit_is_not_terminal():
    """A rate limit clears on its own — demoting on it would hand a merely-busy
    owner's traffic to the platform key permanently."""
    assert is_terminal("rate_limit") is False


def test_none_is_not_terminal():
    assert is_terminal(None) is False


def test_unrecognised_kind_is_not_terminal():
    assert is_terminal("provider_error") is False
