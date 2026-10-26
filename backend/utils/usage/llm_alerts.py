"""LLM provider status/quota checking, ported from fs-agent-usage-limits'
utils/llm_provider_checker.py. Email alerting (services/email_alerts.py) is NOT
ported — fs-agent-knowledge has no SMTP wiring and none is being added here; a
low-balance/rate-limit condition is logged at WARNING/ERROR instead (see
check_provider_status's callers in routers/usage_llm_alerts.py). If email
alerting is wanted later, hook it in there rather than reintroducing a parallel
SMTP stack.
"""

from typing import Dict, Any, Optional

import httpx

from utils.logger import logger


class LLMProviderStatus:
    def __init__(
        self, is_valid: bool, has_low_balance: bool, has_rate_limit: bool,
        error_type: Optional[str] = None, error_message: Optional[str] = None,
        http_status: Optional[int] = None, error_code: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None,
    ):
        self.is_valid = is_valid
        self.has_low_balance = has_low_balance
        self.has_rate_limit = has_rate_limit
        self.error_type = error_type
        self.error_message = error_message
        self.http_status = http_status
        self.error_code = error_code
        self.metadata = metadata or {}


def check_openai_status(api_key: str) -> LLMProviderStatus:
    url = "https://api.openai.com/v1/chat/completions"
    response = httpx.post(url, headers={"Authorization": f"Bearer {api_key}"}, json={"model": "gpt-5.4-mini"}, timeout=10.0)
    if response.status_code == 400:
        return LLMProviderStatus(is_valid=True, has_low_balance=False, has_rate_limit=False)
    if response.status_code == 429:
        error = response.json().get("error", {})
        code = error.get("code", "")
        if code == "insufficient_quota":
            return LLMProviderStatus(is_valid=False, has_low_balance=True, has_rate_limit=False, error_type="low_balance")
        if "rate_limit" in code:
            return LLMProviderStatus(is_valid=True, has_low_balance=False, has_rate_limit=True, error_type="rate_limit")
    if response.status_code == 401:
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error")
    return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="other")


def check_anthropic_status(api_key: str) -> LLMProviderStatus:
    try:
        payload = {"model": "claude-3-haiku-20240307", "max_tokens": 1, "messages": [{"role": "user", "content": "test"}]}
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code == 200:
            return LLMProviderStatus(is_valid=True, has_low_balance=False, has_rate_limit=False)

        error_data = response.json() if response.content else {}
        error_info = error_data.get("error", {})
        error_type_str, error_message = error_info.get("type", ""), error_info.get("message", "")
        low_balance_indicators = ["insufficient_quota", "billing_limit", "payment_required", "balance is too low", "low balance", "insufficient balance", "billing", "payment"]
        rate_limit_indicators = ["rate_limit_error", "too_many_requests", "rate limit"]
        text = f"{error_type_str} {error_message}".lower()
        has_low_balance = any(i in text for i in low_balance_indicators) or response.status_code == 402
        has_rate_limit = any(i in text for i in rate_limit_indicators)

        error_type = "low_balance" if has_low_balance else "rate_limit" if has_rate_limit else "auth_error" if response.status_code == 401 else "other"
        return LLMProviderStatus(
            is_valid=False, has_low_balance=has_low_balance, has_rate_limit=has_rate_limit, error_type=error_type,
            error_message=error_message, http_status=response.status_code, error_code=error_type_str,
        )
    except httpx.TimeoutException:
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="network_error", error_message="Request timeout")
    except Exception as exc:
        logger.error(f"Unexpected error checking Anthropic status: {exc}", exc_info=True)
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="other", error_message=str(exc))


def check_groq_status(api_key: str) -> LLMProviderStatus:
    url = "https://api.groq.com/openai/v1/chat/completions"
    response = httpx.post(url, headers={"Authorization": f"Bearer {api_key}"}, json={"model": "llama3-8b-8192", "messages": []}, timeout=10.0)
    if response.status_code == 400:
        return LLMProviderStatus(is_valid=True, has_low_balance=False, has_rate_limit=False)
    if response.status_code == 429:
        error = response.json().get("error", {})
        if error.get("code") == "rate_limit_exceeded":
            return LLMProviderStatus(is_valid=True, has_low_balance=False, has_rate_limit=True, error_type="rate_limit")
    if response.status_code == 401:
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error")
    return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="other")


def check_google_status(api_key: str) -> LLMProviderStatus:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    response = httpx.post(url, json={"contents": []}, timeout=10.0)
    if response.status_code == 400:
        message = response.json().get("error", {}).get("message", "").lower()
        if "empty" in message or "contents" in message:
            return LLMProviderStatus(is_valid=True, has_low_balance=False, has_rate_limit=False)
    if response.status_code == 429:
        if response.json().get("error", {}).get("status") == "RESOURCE_EXHAUSTED":
            return LLMProviderStatus(is_valid=False, has_low_balance=True, has_rate_limit=False, error_type="low_balance")
    if response.status_code in (401, 403):
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error")
    return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="other")


def check_llamaparse_status(api_key: str) -> LLMProviderStatus:
    if not api_key:
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error", error_message="empty key")
    try:
        payload = {"tier": "fast", "version": "latest", "source_url": "https://www.ietf.org/rfc/rfc2606.txt", "page_ranges": {"max_pages": 1}}
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "https://api.cloud.llamaindex.ai/api/v2/parse",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload,
            )
        if response.status_code in (401, 403):
            return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error", http_status=response.status_code)
        if response.status_code >= 500:
            return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="provider_error", http_status=response.status_code)
        data = response.json() if response.content else {}
        if response.status_code != 200:
            return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="request_error", http_status=response.status_code, metadata={"raw_response": data})

        usage = data.get("usage") or {}
        remaining, used, total = usage.get("remaining_credits"), usage.get("credits_used"), usage.get("total_credits") or usage.get("total")
        has_low_balance = False
        if isinstance(remaining, (int, float)):
            has_low_balance = remaining <= 0
        elif isinstance(total, (int, float)) and isinstance(used, (int, float)):
            has_low_balance = (total - used) <= 0
        return LLMProviderStatus(is_valid=True, has_low_balance=has_low_balance, has_rate_limit=False, error_type="low_balance" if has_low_balance else None, metadata={"raw_response": data})
    except Exception as exc:
        logger.error(f"Unexpected error checking LlamaParse status: {exc}", exc_info=True)
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="other", error_message=str(exc))


def check_firecrawl_status(api_key: str) -> LLMProviderStatus:
    if not api_key:
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error", error_message="empty key")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get("https://api.firecrawl.dev/v2/team/credit-usage", headers={"Authorization": f"Bearer {api_key}"})
        if response.status_code in (401, 403):
            return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="auth_error", http_status=response.status_code)
        if response.status_code >= 500:
            return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="provider_error", http_status=response.status_code)
        data = response.json() if response.content else {}
        remaining = data.get("remaining_credits")
        has_low_balance = isinstance(remaining, (int, float)) and remaining <= 0
        return LLMProviderStatus(is_valid=True, has_low_balance=has_low_balance, has_rate_limit=False, error_type="low_balance" if has_low_balance else None, metadata={"raw_response": data})
    except Exception as exc:
        logger.error(f"Unexpected error checking Firecrawl status: {exc}", exc_info=True)
        return LLMProviderStatus(is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="other", error_message=str(exc))


def check_provider_status(provider: str, api_key: str) -> LLMProviderStatus:
    provider_lower = provider.lower()
    checkers = {
        "openai": check_openai_status, "anthropic": check_anthropic_status, "groq": check_groq_status,
        "google": check_google_status, "llamaparse": check_llamaparse_status, "firecrawl": check_firecrawl_status,
    }
    checker = checkers.get(provider_lower)
    if not checker:
        return LLMProviderStatus(
            is_valid=False, has_low_balance=False, has_rate_limit=False, error_type="unsupported_provider",
            error_message=f"Unsupported provider: {provider}. Supported: {', '.join(checkers)}",
        )
    result = checker(api_key)
    logger.info(f"Provider status check for {provider_lower}: valid={result.is_valid}, low_balance={result.has_low_balance}, rate_limit={result.has_rate_limit}")
    if result.has_low_balance:
        logger.error(f"LOW BALANCE detected for provider={provider_lower}: {result.error_message}")
    elif result.has_rate_limit:
        logger.warning(f"RATE LIMIT detected for provider={provider_lower}: {result.error_message}")
    return result
