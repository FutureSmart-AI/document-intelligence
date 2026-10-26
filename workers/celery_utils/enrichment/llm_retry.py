"""
LLM retry logic and token tracking for enrichment.

Provides:
  - extract_token_usage   — pull input/output tokens from any LangChain response shape
  - accumulate_token_usage — sum token counts across retry attempts
  - invoke_with_retry      — retries with self-correction on validation errors,
                             exponential backoff on infra errors, and optional fallback model
"""

import time
import os
from pydantic import ValidationError
from typing import Any, Callable, Optional, Tuple

from services.celery_logger import logger
from celery_utils.enrichment.llm_factory import FallbackLLM, served_model_identity
from celery_utils.shared.credentials_utils import (
    classify_provider_error,
    is_terminal,
    platform_fallback_allowed,
    report_credential_failure,
)
from celery_utils.shared.tracing import track_time


def extract_token_usage(response_or_exception) -> dict:
    """
    Extract token usage from a LangChain response or exception.

    Handles two response shapes:
      - dict with "raw" key  (with_structured_output include_raw=True)
      - direct response object (include_raw=False / legacy)

    Returns:
        {"input_tokens": int, "output_tokens": int}  — defaults to 0 if not found.
    """
    token_usage = {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}

    # Shape 1: dict {"raw": AIMessage, "parsed": ..., "parsing_error": ...}
    if isinstance(response_or_exception, dict) and "raw" in response_or_exception:
        raw = response_or_exception["raw"]
        if hasattr(raw, "usage_metadata") and raw.usage_metadata:
            m = raw.usage_metadata
            token_usage["input_tokens"] = m.get("input_tokens", 0) or m.get("prompt_tokens", 0)
            token_usage["output_tokens"] = m.get("output_tokens", 0) or m.get("completion_tokens", 0)
            # Cache-read subset of input_tokens (prompt caching), for pricing.
            token_usage["cached_input_tokens"] = int(
                ((m.get("input_token_details") or {}).get("cache_read")) or 0
            )
        elif hasattr(raw, "response_metadata") and raw.response_metadata:
            m = raw.response_metadata
            if "token_usage" in m:
                u = m["token_usage"]
                token_usage["input_tokens"] = u.get("prompt_tokens", 0) or u.get("input_tokens", 0)
                token_usage["output_tokens"] = u.get("completion_tokens", 0) or u.get("output_tokens", 0)
            elif "input_tokens" in m:
                token_usage["input_tokens"] = m.get("input_tokens", 0)
                token_usage["output_tokens"] = m.get("output_tokens", 0)

    # Shape 2: direct AIMessage / response object
    elif hasattr(response_or_exception, "response_metadata"):
        m = response_or_exception.response_metadata
        if m:
            if "token_usage" in m:
                u = m["token_usage"]
                token_usage["input_tokens"] = u.get("prompt_tokens", 0) or u.get("input_tokens", 0)
                token_usage["output_tokens"] = u.get("completion_tokens", 0) or u.get("output_tokens", 0)
            elif "input_tokens" in m:
                token_usage["input_tokens"] = m.get("input_tokens", 0)
                token_usage["output_tokens"] = m.get("output_tokens", 0)

    return token_usage


def accumulate_token_usage(
    total: dict,
    attempt: dict,
    attempt_number: int,
    is_failed: bool = False,
):
    """
    Add attempt token counts into total (in-place).

    Skips silently when both counters are 0 (no billing info available).
    """
    if attempt["input_tokens"] > 0 or attempt["output_tokens"] > 0:
        total["input_tokens"] += attempt["input_tokens"]
        total["output_tokens"] += attempt["output_tokens"]
        total["cached_input_tokens"] = total.get("cached_input_tokens", 0) + attempt.get("cached_input_tokens", 0)
        label = "failed attempt" if is_failed else "attempt"
        logger.info(
            f"Token usage from {label} {attempt_number}: "
            f"input={attempt['input_tokens']}, output={attempt['output_tokens']}. "
            f"Total accumulated: {total}"
        )


def _exec_meta(
    model: Optional[str],
    provider: Optional[str],
    used_fallback: bool,
    attempts: int,
    failure_category: Optional[str] = None,
    failure_detail: Optional[str] = None,
    fallback_reason: Optional[str] = None,
) -> dict:
    """Build the execution-metadata dict returned alongside every result.

    `model`/`provider`/`used_fallback` are the original three keys — the model
    that ACTUALLY served the call, which is what cost and latency must be
    attributed to when the fallback ran.

    `attempts`, `failure_category` and `failure_detail` were added for the
    enrichment analytics event (fs-agent-knowledge
    docs/plan/enrichment-analytics/plan.md). They exist because the caller
    otherwise cannot tell WHY a `None` came back: a schema/validation rejection
    and an infrastructure error both surfaced as the same empty result, and they
    need different fixes — the first is a prompt or field-description problem,
    the second is a retry or a provider problem. Recording them as one category
    would make the analytics screen's failure breakdown useless.

    `failure_category` is `validation_failed` | `error` | None (success), matching
    the `status` values the enrichment event accepts.
    """
    meta = {
        "model": model,
        "provider": provider,
        "used_fallback": used_fallback,
        "attempts": int(attempts or 0),
    }
    # `model` is the WIRE model the provider billed ("gpt-5.6-luna" for the
    # registry name "gpt-5.6-luna-low"), so every record built from this dict
    # prices; `configured_model` and `reasoning_effort` keep which configuration ran.
    meta.update(served_model_identity(model))
    # Only meaningful when a substitution actually happened; emitting it on a
    # clean call would put a stale reason on a row that never fell back.
    if used_fallback and fallback_reason:
        meta["fallback_reason"] = fallback_reason[:500]
    if failure_category:
        meta["failure_category"] = failure_category
        # Truncated: this lands in a DB column and a rendered UI cell, and a
        # Pydantic ValidationError over a wide schema can run to thousands of
        # characters.
        meta["failure_detail"] = (failure_detail or "")[:2000]
    return meta


@track_time("LLM: invoke_with_retry")
def invoke_with_retry(
    llm: Any,
    messages: list[dict],
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    fallback: Optional[FallbackLLM] = None,
    response_validator: Optional[Callable[[Any], Optional[str]]] = None,
    primary_model: Optional[str] = None,
    primary_provider: Optional[str] = None,
    owner_id: Optional[str] = None,
    key_type: Optional[str] = None,
    platform_key_fallback: Optional[FallbackLLM] = None,
) -> Tuple[Optional[Any], dict, dict]:
    """
    Invoke an LLM with retry logic and optional fallback model.

    Retry behaviour by error type:
    - ValidationError / ValueError (including custom response_validator failures):
        Append the error to messages so the LLM can self-correct.
        On exhaustion, switch to fallback (full fresh cycle) or return None.
    - Groq "tool_use_failed" / "tool call validation failed":
        Same self-correction loop as ValidationError.
    - A BYOK key rejection (auth_error / low_balance), when the primary ran on the
      owner's key: reported to fs-agent-knowledge so it demotes the stored
      credential (celery_utils/shared/credentials_utils.report_credential_failure),
      then retried ONCE on ``platform_key_fallback`` — same provider and model, just
      paid by the platform key — gated on the owner's usage limit
      (platform_fallback_allowed). This is deliberately not the same path as the
      configured ``fallback`` model below: a dead key says nothing about whether the
      owner's chosen model was a bad choice, so this keeps the model and only
      changes who is paying.
    - Other exceptions (rate limits, network, etc.), or a BYOK rejection the
      platform key may not cover for:
        Switch to ``fallback`` immediately; only retry locally if no fallback.

    Args:
        llm: LLM instance with structured output (e.g. from with_structured_output).
        messages: Message list for LLM input.
        max_retries: Max attempts per model (default 3 = first try + 2 retries).
        initial_delay: Initial backoff delay in seconds.
        backoff_factor: Multiplier for exponential backoff.
        fallback: FallbackLLM(llm, model_name, provider) used after primary exhausts.
        response_validator: Optional callable(parsed) -> error_str | None.
            Return an error string when the response is logically invalid (e.g. empty
            chunks). Treated as ValueError — self-correction is applied.
        primary_model: Model name for attribution in exec_meta.
        primary_provider: Provider name for attribution in exec_meta.
        owner_id: Needed to report a rejected key and to check the owner's usage
            limit before falling back to the platform key. Without it (or without
            ``platform_key_fallback``), a BYOK rejection just falls straight through
            to ``fallback`` as before this parameter existed.
        key_type: "owner-key" or "platform-key" — the key the PRIMARY call is using.
            Only "owner-key" can be demoted/covered; a platform-key failure is a
            platform problem, not a credential one.
        platform_key_fallback: The same provider/model as the primary, built on the
            platform key (celery_utils.enrichment.llm_factory.get_platform_key_llm),
            with structured output already bound to the same schema. Pass ``None``
            when key_type is already "platform-key" — there is nothing to fall back
            FROM.

    Returns:
        Tuple of:
          - parsed response or None on total failure
          - token_usage dict {"input_tokens": int, "output_tokens": int}
          - exec_meta dict — see :func:`_exec_meta`. Always carries "model",
            "provider", "used_fallback" and "attempts"; on failure it also
            carries "failure_category" ("validation_failed" | "error") and a
            truncated "failure_detail".
    """
    current_messages = messages.copy()
    token_usage = {"input_tokens": 0, "output_tokens": 0}
    delay = initial_delay
    attempt = 0

    actual_model: Optional[str] = primary_model
    actual_provider: Optional[str] = primary_provider
    used_fallback: bool = False
    # WHY the fallback fired. `used_fallback` alone cannot distinguish a caller
    # misconfiguration from a real model failure, which made a 2026-08-27 matrix
    # unreadable: two documents requested openai/gpt-oss-120b, were served by
    # gpt-5.4-nano, and nothing recorded that the cause was a wrong provider on the
    # caller side rather than any weakness in the requested model.
    fallback_reason: Optional[str] = None
    # Tracks whether the CURRENT llm is running on the owner's key — starts as
    # whatever the primary call used, and flips to False the moment either
    # fallback kicks in (both fallbacks below are always platform-paid).
    _current_key_type: Optional[str] = key_type

    def _switch_to_platform_key(exc: BaseException) -> bool:
        """One-shot: if this failure is a terminal BYOK rejection on the primary's
        owner key, report it and retry on the SAME model paid by the platform key.

        Returns False (does nothing) for any failure that isn't a credential
        problem, for a primary already on the platform key, or once this has
        already fired once for this call — callers fall through to the normal
        ``_switch_to_fallback`` path in all of those cases.
        """
        nonlocal llm, platform_key_fallback, attempt, delay, current_messages
        nonlocal actual_model, actual_provider, used_fallback, _current_key_type
        nonlocal fallback_reason

        if _current_key_type != "owner-key":
            return False

        error_type = classify_provider_error(exc)
        if not is_terminal(error_type):
            return False

        if os.getenv("BYOK_PLATFORM_FALLBACK", "true").lower() in {"false", "0", "no", "off"}:
            raise exc

        if platform_key_fallback is None:
            return False

        if owner_id:
            report_credential_failure(owner_id, actual_provider or primary_provider or "", error_type, str(exc))

        if not platform_fallback_allowed(owner_id):
            logger.warning(
                f"BYOK key rejected ({error_type}) and owner_id={owner_id} is over "
                f"their usage limit — not covering with the platform key, falling "
                f"through to the configured fallback model instead"
            )
            # Consumed either way: retrying this same check on a later attempt
            # would just repeat the same "over limit" verdict. Let the ordinary
            # fallback (different model) be the safety net instead.
            platform_key_fallback = None
            return False

        logger.warning(
            f"BYOK key rejected ({error_type}) — retrying {platform_key_fallback.provider}/"
            f"{platform_key_fallback.model_name} on the platform key"
        )
        llm = platform_key_fallback.llm
        actual_model = platform_key_fallback.model_name
        actual_provider = platform_key_fallback.provider
        used_fallback = True
        fallback_reason = f"byok_key_rejected: {error_type}"
        _current_key_type = "platform-key"
        platform_key_fallback = None  # one shot — no key left to demote a second time
        attempt = 0
        delay = initial_delay
        current_messages = messages.copy()
        return True

    def _switch_to_fallback(reason: str) -> bool:
        nonlocal llm, fallback, attempt, delay, current_messages, actual_model, actual_provider, used_fallback
        nonlocal fallback_reason
        if fallback is not None:
            logger.warning(f"{reason} — switching to fallback model: {fallback.model_name}")
            llm = fallback.llm
            actual_model = fallback.model_name
            actual_provider = fallback.provider
            used_fallback = True
            fallback_reason = reason
            fallback = None
            attempt = 0
            delay = initial_delay
            current_messages = messages.copy()
            return True
        return False

    while attempt < max_retries:
        try:
            response = llm.invoke(current_messages)
            attempt_tokens = extract_token_usage(response)
            accumulate_token_usage(token_usage, attempt_tokens, attempt + 1, is_failed=False)

            if isinstance(response, dict) and "parsed" in response and response["parsed"] is not None:
                parsed = response["parsed"]
                if response_validator:
                    err = response_validator(parsed)
                    if err:
                        raise ValueError(f"Response validation failed: {err}")
                exec_meta = _exec_meta(actual_model, actual_provider, used_fallback, attempt + 1, fallback_reason=fallback_reason)
                return parsed, token_usage, exec_meta

            if isinstance(response, dict) and "parsing_error" in response and response["parsing_error"] is not None:
                parsing_error = response["parsing_error"]
                if isinstance(parsing_error, ValidationError):
                    raise parsing_error
                raise ValueError(f"Parsing error: {parsing_error}")

            # include_raw=False path (legacy callers)
            exec_meta = _exec_meta(actual_model, actual_provider, used_fallback, attempt + 1, fallback_reason=fallback_reason)
            return response, token_usage, exec_meta

        except (ValidationError, ValueError) as e:
            attempt_tokens = extract_token_usage(e)
            accumulate_token_usage(token_usage, attempt_tokens, attempt + 1, is_failed=True)

            if attempt == max_retries - 1:
                if _switch_to_fallback(f"Max retries ({max_retries}) reached — validation failed: {e}"):
                    continue
                logger.warning(f"Max retries ({max_retries}) reached — validation failed: {e}. token_usage={token_usage}")
                exec_meta = _exec_meta(actual_model, actual_provider, used_fallback, attempt + 1, "validation_failed", f"schema_validation_failed: {e}", fallback_reason=fallback_reason)
                return None, token_usage, exec_meta

            current_messages.append({
                "role": "system",
                "content": f"Previous output failed validation: {e}. Please correct the output to match the expected format.",
            })
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} validation error, retrying in {delay}s: "
                f"{e.json(indent=2) if isinstance(e, ValidationError) else e}"
            )
            time.sleep(delay)
            delay *= backoff_factor
            attempt += 1

        except Exception as e:
            attempt_tokens = extract_token_usage(e)
            accumulate_token_usage(token_usage, attempt_tokens, attempt + 1, is_failed=True)

            error_str = str(e)
            is_tool_validation_failure = (
                "tool call validation failed" in error_str.lower()
                or "tool_use_failed" in error_str.lower()
            )

            if is_tool_validation_failure:
                if attempt == max_retries - 1:
                    if _switch_to_fallback(f"Max retries ({max_retries}) reached — tool validation failed: {e}"):
                        continue
                    logger.warning(f"Max retries ({max_retries}) reached — tool validation failed: {e}. token_usage={token_usage}")
                    exec_meta = _exec_meta(actual_model, actual_provider, used_fallback, attempt + 1, "validation_failed", f"tool_validation_failed: {e}", fallback_reason=fallback_reason)
                    return None, token_usage, exec_meta

                current_messages.append({
                    "role": "system",
                    "content": (
                        f"Previous output was rejected by schema validation: {e}. "
                        "Ensure every field listed in the error above is present and correctly typed."
                    ),
                })
                logger.warning(f"Attempt {attempt + 1}/{max_retries} tool validation error, retrying in {delay}s: {e}")
                time.sleep(delay)
                delay *= backoff_factor
                attempt += 1
                continue

            # A rejected BYOK key gets first crack at recovery — same model, just
            # billed to the platform key — before falling through to a differently
            # configured fallback model. See _switch_to_platform_key's docstring for
            # why this order matters (a dead key says nothing about model choice).
            if _switch_to_platform_key(e):
                continue

            # Infrastructure error (rate limit, network, etc.) — switch fallback immediately
            if _switch_to_fallback(f"Primary LLM error ({type(e).__name__}: {e})"):
                continue

            if attempt == max_retries - 1:
                logger.error(f"Max retries ({max_retries}) reached — LLM error ({type(e).__name__}): {e}. token_usage={token_usage}")
                exec_meta = _exec_meta(actual_model, actual_provider, used_fallback, attempt + 1, "error", f"llm_error: {type(e).__name__}: {e}", fallback_reason=fallback_reason)
                return None, token_usage, exec_meta

            logger.warning(f"Attempt {attempt + 1}/{max_retries} LLM error, retrying in {delay}s: {type(e).__name__}: {e}")
            time.sleep(delay)
            delay *= backoff_factor
            attempt += 1

    exec_meta = _exec_meta(actual_model, actual_provider, used_fallback, attempt, "error", "retries_exhausted", fallback_reason=fallback_reason)
    return None, token_usage, exec_meta
