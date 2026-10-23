"""One place that says "no" — thin adapter over ``check_operation_allowed``.

Every quota gate in the codebase should reach for ``enforce_operation``, not for
``check_operation_allowed`` directly. This is what keeps behaviour consistent across
call sites: one HTTP status (429), one shape of error detail, one policy for what
happens when the usage DB is unreachable, and one flag that flips the whole system
from observe-only to enforcing.

Design points, each with a paper trail in
``docs/plan/usage-and-limits/decisions.md``:

- **429, not 403.** The request is well-formed and authorised — it is the quantity
  that is refused, and (for the month-scoped limits) the condition clears on its own.
  A bare "quota exceeded" cannot be acted on, so the body includes the specific
  limit, the current value, the ceiling, and how much this request would add.
- **Fail open when the usage DB is unreachable.** A metering dependency must not be
  able to cause a total service outage. The error is logged loudly; the request
  proceeds.
- **Observe-only mode** — controlled by ``USAGE_ENFORCE_LIMITS`` (default ``false``).
  When off, a would-exceed logs at WARNING and the request proceeds. When on, the
  same finding raises ``HTTPException(429)``. This is how a quota that has never been
  measured against real traffic gets switched on without refusing a paying customer
  on day one.
- **``None`` ceilings mean unlimited** — handled inside ``check_operation_allowed``
  (``limit_checking.py``: ``if limit_val is None: continue``). No code here.
"""

from __future__ import annotations

import os
from typing import List, Optional, Set

from fastapi import HTTPException

from utils.db.session import async_session
from utils.logger import logger
from utils.pydantic_models_usage_limits import EstimatedMetric, Operation, UsageCheckResponse
from utils.usage.limit_checking import check_operation_allowed


def _enforcement_enabled() -> bool:
    """Read the flag fresh each call — cheap, and lets ops toggle without a restart."""
    return os.getenv("USAGE_ENFORCE_LIMITS", "false").strip().lower() in {"1", "true", "yes", "on"}


async def enforce_operation(
    owner_id: Optional[str],
    operation: Operation,
    estimated_metrics: Optional[List[EstimatedMetric]] = None,
    *,
    source: str = "",
    byok_exempt_if_all_of: Optional[Set[str]] = None,
) -> Optional[UsageCheckResponse]:
    """Refuse (429) or allow one operation for this owner.

    Returns the ``UsageCheckResponse`` when allowed (or in observe-only mode, even if
    over), so callers may surface remaining quota to the client. Returns ``None``
    when the check itself could not run — see fail-open above.

    ``source`` is a short free-text tag for logs (``chat``, ``ingest:file``,
    ``ingest:crawl-child``, ``create_agent``). It never influences the decision;
    it just lets the observe-only WARNING lines be searchable.

    ``byok_exempt_if_all_of`` is the full-BYOK exemption. Pass the set of providers
    this operation will pay for (see ``utils/usage/pipeline_providers.py``). If the
    owner holds a valid key for **every** one of them, the operation is exempt and no
    limit is checked at all — there is no platform spend to ration. If any are
    missing, the limit applies as normal *and* the refusal names the missing providers,
    so the client can tell the user exactly which key to add to lift the restriction.

    "All or nothing" is the point: a partial BYOK set still leaves us paying for the
    stages the owner did not cover, so a half-funded pipeline gets no discount. An
    empty set means "no paid providers at all" and therefore counts as fully exempt —
    that is correct, not a loophole: a plain-text upload embedded with a local model
    genuinely costs no provider money.
    """
    # No owner resolvable → nothing to check against. Allow, but log — most call
    # sites now do resolve one, so hitting this path means something upstream lost
    # it and the request is running unmetered.
    if not owner_id:
        logger.warning("[enforce_operation:%s] no owner_id resolved; allowing without a check", source or operation)
        return None

    metrics = estimated_metrics or []

    # --- full-BYOK exemption, before any counting ---
    # Checked first because when it applies there is no reason to run the aggregate
    # queries at all: the answer is "allowed" regardless of what the counts say.
    missing_providers: list = []
    if byok_exempt_if_all_of is not None:
        from utils.usage.credentials import get_byok_providers
        from utils.usage.pipeline_providers import missing_byok_providers

        try:
            owner_keys = await get_byok_providers(owner_id)
            missing_providers = sorted(missing_byok_providers(byok_exempt_if_all_of, owner_keys))
        except Exception as e:
            # Fail *closed* on the exemption specifically (i.e. no exemption), which is
            # the opposite of the fail-open policy for the limit check itself — and
            # deliberately so. Failing open here would hand out free unlimited ingest
            # on a transient DB error; failing closed only means the owner keeps the
            # limit they already had.
            logger.error(
                "[enforce_operation:%s] BYOK provider lookup failed for owner_id=%s: %s — not exempting",
                source or operation, owner_id, e, exc_info=True,
            )
            missing_providers = sorted(byok_exempt_if_all_of)

        if not missing_providers:
            logger.info(
                "[enforce_operation:%s] exempt: owner_id=%s funds every provider in this pipeline (%s)",
                source or operation, owner_id, sorted(byok_exempt_if_all_of) or ["none"],
            )
            return None

    # Fail open on DB error. `check_operation_allowed` does not log the exception on
    # its own — if we drop this try/except, a transient RDS blip becomes a 500 for
    # every request, which is exactly the failure mode this is designed to avoid.
    try:
        async with async_session() as session:
            result = await check_operation_allowed(session, owner_id, operation, metrics)
    except Exception as e:
        logger.error(
            "[enforce_operation:%s] limit check failed for owner_id=%s op=%s: %s — failing open",
            source or operation, owner_id, operation, e, exc_info=True,
        )
        return None

    if result.allowed:
        return result

    # `would_exceed` is the actionable part: it names the limit that tripped, the
    # current value, the ceiling, and the requested increment. Kept as-is in the
    # detail so the client does not have to re-derive it.
    would_exceed = [ex.dict() if hasattr(ex, "dict") else ex for ex in result.would_exceed]

    if not _enforcement_enabled():
        # Observe-only. This log is deliberately loud (WARNING, not INFO) so it stands
        # out in a real traffic pattern: it is the whole point of the shadow phase.
        logger.warning(
            "[enforce_operation:%s] WOULD refuse owner_id=%s op=%s: %s missing_byok=%s "
            "(observe-only; USAGE_ENFORCE_LIMITS=false)",
            source or operation, owner_id, operation, would_exceed, missing_providers or None,
        )
        return result

    logger.info(
        "[enforce_operation:%s] refusing owner_id=%s op=%s: %s missing_byok=%s",
        source or operation, owner_id, operation, would_exceed, missing_providers or None,
    )

    detail = {
        "error": "quota_exceeded",
        "message": "You have reached one of your account's usage limits. Delete unused items or upgrade your plan.",
        "would_exceed": would_exceed,
        "remaining_quota": result.remaining_quota,
    }

    # When the operation *could* have been exempt, say what would earn the exemption.
    # This is the difference between "you hit your limit" and "you hit your limit, and
    # here is the specific thing to change" — the client can render an actionable
    # prompt (add a LlamaParse key) instead of a dead end.
    if missing_providers:
        detail["error"] = "quota_exceeded_add_provider_keys"
        detail["missing_provider_keys"] = missing_providers
        detail["message"] = (
            "You have reached your account's limit for this action. "
            "Add your own API key for "
            + ", ".join(missing_providers)
            + " to remove this limit — an account that funds every provider in the pipeline is not restricted. "
            "Otherwise, delete unused items or upgrade your plan."
        )

    raise HTTPException(status_code=429, detail=detail)
