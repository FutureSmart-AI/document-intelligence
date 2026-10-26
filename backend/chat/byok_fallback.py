"""Same-request platform-key fallback when an owner's BYOK key is rejected.

## What this fixes

A BYOK key is resolved once, before the provider is ever called
(chat/llm_factory.py::get_llm). Until this module existed, a key that had been
revoked or had expired since it was stored was still handed to the provider, the
call raised, and whichever ``except`` happened to be nearest turned it into a
failed message. The owner was never told why, and every later request repeated the
same doomed call.

The fix has two halves, and both matter:

- **This request still succeeds.** The rejected call is retried once on the
  platform key, so the user gets their answer instead of an error.
- **Later requests skip the dead key.** ``mark_credential_failed`` demotes it, and
  ``get_credential_for_owner`` already refuses to hand out a demoted key — so the
  retry cost is paid once, not on every subsequent message.

## Why the fallback is gated on the owner's usage limit

While an owner funds their own provider calls they are exempt from platform quotas
(``utils/usage/enforcement.py``) — there is no platform spend to ration. The moment
we start paying, that exemption stops applying. So an owner already over their
limit is *not* covered: they get ``ByokKeyRejected``, which carries a message
telling them to fix the key. Covering them regardless would make "let your key
expire" the cheapest route to unlimited usage.

## What does NOT trigger a fallback

Only a *terminal* credential failure — bad credentials, or a spent provider balance.
A rate limit, a timeout, a provider 5xx, a model-not-found: all re-raised
untouched. Falling back on those would mask real bugs behind a silent switch to our
own key, and would move a merely-busy owner onto our bill. See
``utils/usage/credential_health.classify_provider_error``.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from chat.llm_factory import get_llm
from utils.logger import logger
from utils.usage.credential_health import (
    classify_provider_error,
    is_terminal,
    mark_credential_failed,
    platform_fallback_allowed,
)

# The limit that chat spend counts against — one llm_generation event is one
# message (see docs/modules/usage-event-catalog.md).
CHAT_OPERATION = "send_message"


class ByokKeyRejected(Exception):
    """The owner's key was rejected and the platform key may not cover for it.

    Raised only when the owner is over their usage limit (or fallback is switched
    off). Carries a message written for the end user, since this is the one case
    where they must act before anything works again.
    """

    def __init__(self, provider: str, error_type: str, reason: str):
        self.provider = provider
        self.error_type = error_type
        self.reason = reason
        if reason == "over_limit":
            message = (
                f"Your {provider} API key was rejected by {provider} ({error_type}), and your "
                f"account has already reached its usage limit — so we cannot fall back to the "
                f"platform key. Update your {provider} key, or upgrade your plan."
            )
        else:
            message = (
                f"Your {provider} API key was rejected by {provider} ({error_type}). "
                f"Update or remove the key to continue."
            )
        self.user_message = message
        super().__init__(message)


@dataclass
class ByokOutcome:
    """Result of a call that may have been retried on the platform key.

    ``provider`` and ``key_type`` describe the attempt that actually produced
    ``value`` — so a caller logging a usage event reports the key that really paid,
    which is what keeps the BYOK exemption in ``limit_checking`` honest.
    """

    value: Any
    provider: str
    key_type: str
    warning: Optional[Dict[str, str]] = None

    @property
    def fell_back(self) -> bool:
        return self.warning is not None


# ── Request-scoped warning collection ────────────────────────────────────────
#
# A chat turn runs several LLM calls (refiner, retrieval agent, verifier, answer,
# follow-up), all on the SAME owner key. Whichever one runs first is the one that
# discovers the key is dead, and it is not necessarily the one whose result reaches
# the client. Threading a warning back through all five call chains would touch
# every signature between here and the router for a field most of them do not
# otherwise care about.
#
# A contextvar is the natural fit: asyncio copies the context per task, so this is
# per-request without any plumbing, and a stray value cannot leak between requests
# the way a module-level list would. The router resets it at the start of a turn and
# drains it when building the response.
_warnings: contextvars.ContextVar[Optional[List[Dict[str, str]]]] = contextvars.ContextVar(
    "byok_warnings", default=None
)


def reset_warnings() -> None:
    """Start collecting BYOK warnings for a new chat turn."""
    _warnings.set([])


def collect_warning(warning: Dict[str, str]) -> None:
    """Record a fallback notice for this turn. No-op if nothing is collecting."""
    bucket = _warnings.get()
    if bucket is None:
        return
    # One notice per provider is enough — five nodes hitting the same dead key
    # should not produce five identical warnings in the response.
    if any(w.get("provider") == warning.get("provider") for w in bucket):
        return
    bucket.append(warning)


def drain_warnings() -> List[Dict[str, str]]:
    """Take the warnings collected during this turn and stop collecting."""
    bucket = _warnings.get() or []
    _warnings.set(None)
    return list(bucket)


def build_warning(provider: str, error_type: str) -> Dict[str, str]:
    """Client-facing notice that the owner's key is dead and we covered for it.

    Deliberately says the usage now counts against their plan: silently absorbing
    a customer's provider spend onto our own key without telling them is how a
    surprise appears on someone's quota later.
    """
    detail = "has no credit left" if error_type == "low_balance" else "was rejected"
    return {
        "code": "byok_key_invalid",
        "provider": provider,
        "error_type": error_type,
        "message": (
            f"Your {provider} API key {detail}, so this request was served using the "
            f"platform key and counts against your plan's usage limits. Please update "
            f"or remove the key in your settings."
        ),
    }


async def _resolve_owner(owner_id: Optional[str], knowledge_id: Optional[str]) -> Optional[str]:
    """The owner the credential belongs to.

    Most chat call sites carry ``owner_id`` already. The ones that only hold
    ``knowledge_id`` still need it, because the credential to demote is keyed by
    owner — demoting nothing would leave the dead key in service and make every
    later request pay for the same failed provider call.
    """
    if owner_id:
        return owner_id
    if not knowledge_id:
        return None
    from utils.usage.credentials import resolve_owner_id_for_knowledge

    return await resolve_owner_id_for_knowledge(knowledge_id)


async def run_with_key_fallback(
    call: Callable[[Any], Awaitable[Any]],
    *,
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    source: str = "chat",
    operation: str = CHAT_OPERATION,
    **get_llm_kwargs: Any,
) -> ByokOutcome:
    """Build a chat model, run ``call`` against it, and retry once on the platform key
    if the owner's BYOK key turns out to be dead.

    ``call`` receives the built LLM and must do all of its own wrapping —
    ``.with_structured_output(...)``, ``prompt | llm``, ``create_agent(model=llm)``.
    That is why the wrapping happens inside the callable rather than being passed in
    pre-built: the retry needs to rebuild the *whole* chain on the new key, and a
    chain built once outside would still hold the rejected one.

    Any remaining kwargs go to ``get_llm`` unchanged (provider, model, temperature,
    max_tokens, streaming, for_function_calling).
    """
    llm, provider, key_type = await get_llm(
        owner_id=owner_id, knowledge_id=knowledge_id, **get_llm_kwargs
    )

    try:
        return ByokOutcome(await call(llm), provider, key_type)
    except Exception as exc:
        # Nothing to fall back FROM — the platform key was already in use, so this
        # is a real failure and belongs to the caller's own error handling.
        if key_type != "owner-key":
            raise

        error_type = classify_provider_error(exc)
        if not is_terminal(error_type):
            # Transient, or not a credential problem at all. Re-raise untouched so
            # a rate limit or a provider outage is not misread as a dead key.
            raise

        resolved_owner = await _resolve_owner(owner_id, knowledge_id)
        logger.warning(
            "[byok_fallback:%s] %s key rejected (%s) for owner_id=%s — demoting and retrying "
            "on the platform key",
            source, provider, error_type, resolved_owner,
        )
        if resolved_owner:
            await mark_credential_failed(resolved_owner, provider, error_type, str(exc))

        allowed, reason = await platform_fallback_allowed(resolved_owner, operation)
        if not allowed:
            raise ByokKeyRejected(provider, error_type, reason) from exc

        retry_llm, retry_provider, retry_key_type = await get_llm(
            owner_id=owner_id, knowledge_id=knowledge_id,
            force_platform_key=True, **get_llm_kwargs
        )
        value = await call(retry_llm)
        logger.info(
            "[byok_fallback:%s] retry on the platform key succeeded for owner_id=%s provider=%s",
            source, resolved_owner, retry_provider,
        )
        warning = build_warning(provider, error_type)
        collect_warning(warning)
        return ByokOutcome(value, retry_provider, retry_key_type, warning)


async def astream_with_key_fallback(
    stream: Callable[[Any], Any],
    *,
    owner_id: Optional[str],
    knowledge_id: Optional[str] = None,
    source: str = "chat",
    operation: str = CHAT_OPERATION,
    **get_llm_kwargs: Any,
):
    """Streaming twin of ``run_with_key_fallback``, as an async generator.

    Yields ``("chunk", chunk)`` pairs, then exactly one ``("outcome", ByokOutcome)``
    as its final item — the outcome carries the provider/key_type that actually
    produced the stream, plus any fallback warning, since a caller cannot learn
    those from the chunks themselves.

    **The retry only happens before the first chunk is yielded.** Once tokens have
    reached the client, re-running the call would duplicate the answer, so a
    failure mid-stream is re-raised. In practice an auth rejection always arrives on
    the opening request — the provider refuses before generating anything — so this
    limitation costs nothing real, but it is the reason this cannot simply reuse the
    function above.
    """
    llm, provider, key_type = await get_llm(
        owner_id=owner_id, knowledge_id=knowledge_id, **get_llm_kwargs
    )

    emitted = False
    try:
        async for chunk in stream(llm):
            emitted = True
            yield "chunk", chunk
        yield "outcome", ByokOutcome(None, provider, key_type)
        return
    except Exception as exc:
        if emitted or key_type != "owner-key":
            raise
        error_type = classify_provider_error(exc)
        if not is_terminal(error_type):
            raise

        resolved_owner = await _resolve_owner(owner_id, knowledge_id)
        logger.warning(
            "[byok_fallback:%s] %s key rejected (%s) before any token for owner_id=%s — "
            "demoting and restarting the stream on the platform key",
            source, provider, error_type, resolved_owner,
        )
        if resolved_owner:
            await mark_credential_failed(resolved_owner, provider, error_type, str(exc))

        allowed, reason = await platform_fallback_allowed(resolved_owner, operation)
        if not allowed:
            raise ByokKeyRejected(provider, error_type, reason) from exc

    # Retry outside the except block so a failure here reports on its own terms
    # rather than as "during handling of the above exception".
    retry_llm, retry_provider, retry_key_type = await get_llm(
        owner_id=owner_id, knowledge_id=knowledge_id,
        force_platform_key=True, **get_llm_kwargs
    )
    async for chunk in stream(retry_llm):
        yield "chunk", chunk
    warning = build_warning(provider, error_type)
    collect_warning(warning)
    yield "outcome", ByokOutcome(None, retry_provider, retry_key_type, warning)
