"""Quota/limit checking, ported from fs-agent-usage-limits' utils/limit_checking.py.

Everything here is keyed by owner_id: both the limit CEILINGS (account-wide
OwnerPlatformLimits) and the current-usage aggregation they're compared against.
Callers pass owner_id directly — the routes are `/v1/owners/{owner_id}/usage/limits/...`.

Originally (before migration 039) current usage was summed per knowledge_id while
the ceiling it was compared against was already owner-wide, so an owner with several
knowledge bases could exceed a shared monthly limit by spreading usage across them,
each individually reading as under the limit. Scoping both sides by owner_id fixes that.

Dropped from the source service: agents-count limit checking (max_agents /
create_agent) — that relied on an external Agent Registry HTTP client this
service has no equivalent for, and "agents" is not a concept this service
manages. See docs/plan/usage-and-limits/state.md for this gap.
"""

import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from sqlalchemy import select, func, and_
from sqlmodel.ext.asyncio.session import AsyncSession

from utils.logger import logger
from utils.pydantic_models_usage_limits import (
    LimitsInfo, CurrentUsageInfo, RemainingQuotaInfo, UsageStatusResponse,
    UsageCheckResponse, EstimatedMetric, ExceededLimitInfo, Operation, PeriodInfo,
    TierInfo, LimitsScreenItem, LimitsScreenGroup, LimitsScreenResponse,
)
from utils.models.usage_events import UsageMetric, UsageEvent
from utils.models.db_tables import Document, KnowledgeConfig

OPERATION_LIMIT_MAPPING = {
    "index_content": ["max_indexed_characters"],
    # documents/urls are additional, source-specific limit keys on the SAME operation —
    # a request only trips one of them if its estimated_metrics actually names it (see
    # the "documents"/"urls" entries below and the call sites in document_management.py
    # / batch_document_management.py, which pass that metric only for the source type
    # the request actually is).
    "create_knowledge_item": ["max_knowledge_items", "max_documents", "max_urls"],
    "create_agent": ["max_agents"],
    "start_conversation": ["max_conversations_per_month"],
    "send_message": ["max_messages_per_month", "max_messages_per_day"],
    "llm_call": ["max_llm_tokens_per_month"],
}

METRIC_TO_LIMIT_KEY = {
    "indexed_characters": "max_indexed_characters",
    "knowledge_items": "max_knowledge_items",
    "agents": "max_agents",
    "conversations": "max_conversations_per_month",
    "messages": "max_messages_per_month",
    "llm_tokens": "max_llm_tokens_per_month",
    "documents": "max_documents",
    "urls": "max_urls",
    # Deliberately a different metric NAME from "messages" even though both count the
    # same event (one chat message) — check_operation_allowed sums requested_by_limit
    # per LIMIT KEY, keyed off metric name, so one physical message needs two metric
    # entries in estimated_metrics (name="messages" and name="messages_today") to feed
    # both max_messages_per_month and max_messages_per_day from a single request.
    "messages_today": "max_messages_per_day",
}


def _month_bounds(now: Optional[datetime] = None):
    now = now or datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    month_end = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc) if now.month == 12 else datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return month_start, month_end


def _day_bounds(now: Optional[datetime] = None):
    """00:00 UTC today through 00:00 UTC tomorrow — the daily message window resets
    here, not on the calendar-month boundary max_messages_per_month uses."""
    now = now or datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


async def get_owner_limits(session: AsyncSession, owner_id: str) -> LimitsInfo:
    """Resolve the account-wide platform limits for this owner.

    An owner with no `owner_configurations` row falls back to the tier defaults in
    `utils/config/usage/owner_defaults.yaml` — the same fallback
    `GET /v1/owners/{owner_id}/usage/owner-config` already applies
    (routers/usage_owner_config.py). Without it this returned an empty LimitsInfo,
    where every ceiling is None and None means UNLIMITED downstream:
    `/limits/status` reported no ceilings at all (so a usage UI has no denominator
    to draw a meter against), and `check_operation_allowed` answered `allowed=true`
    unconditionally, so enforcement was a silent no-op even with
    USAGE_ENFORCE_LIMITS=true. Most accounts have no row — the row is only written
    on an explicit owner-config write, never on signup — so this was the common
    case, not an edge case. Confirmed live 2026-08-25 against a real account:
    /owner-config returned the trial limits while /limits/status returned all null
    for that same account in the same minute.
    """
    from utils.usage.owner_config import get_owner_config
    from utils.usage.owner_defaults import get_defaults_manager

    if not owner_id:
        logger.warning("get_owner_limits called with no owner_id; returning empty limits")
        return LimitsInfo()

    # A verified FutureSmart / AI Demos account has no trial ceilings — every limit
    # None, which every gate reads as unlimited. Usage is still recorded as usual.
    # See utils/usage/internal_accounts.py for how an account qualifies.
    from utils.usage.internal_accounts import is_internal_owner
    if await is_internal_owner(owner_id):
        return LimitsInfo()

    config = await get_owner_config(session, owner_id)
    if not config:
        logger.info(f"No owner configuration row for owner_id={owner_id}; falling back to tier defaults")
        config = get_defaults_manager().get_default_config(owner_id)
    pl = config.platform_limits
    return LimitsInfo(
        max_agents=pl.max_agents, max_indexed_characters=pl.max_indexed_characters,
        max_knowledge_items=getattr(pl, "max_knowledge_items", None),
        max_conversations_per_month=pl.max_conversations_per_month, max_messages_per_month=pl.max_messages_per_month,
        max_llm_tokens_per_month=pl.max_llm_tokens_per_month,
        max_documents=getattr(pl, "max_documents", None), max_urls=getattr(pl, "max_urls", None),
        max_messages_per_day=getattr(pl, "max_messages_per_day", None),
        max_cost_usd_per_month=getattr(pl, "max_cost_usd_per_month", None),
    )


async def get_current_usage(session: AsyncSession, owner_id: str, month_start: Optional[datetime] = None, month_end: Optional[datetime] = None, cost_bearing_only: bool = False, include_cost: bool = False) -> CurrentUsageInfo:
    """Current usage for an owner, across every knowledge base.

    ``cost_bearing_only`` is the BYOK exemption. When True, usage that a customer's
    own provider key paid for (``key_type == 'owner-key'``) is excluded from the
    *provider-spend* aggregates — today that's the message count, since a message is
    one LLM call. Storage and infrastructure counts (knowledge_items, agents,
    conversations, indexed_characters) are never exempted: they cost us regardless of
    whose key answered. Enforcement passes True here; analytics/reporting pass the
    default False so an owner always sees their full consumption.

    ``include_cost`` opts in to computing ``cost_usd_this_month`` — a per-event
    pricing walk over the month's events (the same calculation the analytics cost
    strip runs), too heavy to pay on every enforcement pre-flight. Only the display
    paths (``get_usage_status``, ``get_usage_limits_screen``) pass True; the field
    stays 0.0 otherwise, which is safe because the cost limit is reporting-only and
    never enforced (see ``max_cost_usd_per_month`` on ``OwnerPlatformLimits``).
    """
    if month_start is None or month_end is None:
        month_start, month_end = _month_bounds()

    if not owner_id:
        logger.warning("get_current_usage called with no owner_id; returning zero usage")
        return CurrentUsageInfo(indexed_characters=0, knowledge_items=0, agents=0, conversations_this_month=0, messages_this_month=0, llm_tokens_this_month=0)

    # Indexed characters: only `data_ingestion` events, which is where the characters
    # actually entering the corpus are reported (file / weblink / raw-text
    # processing). The operation filter is load-bearing: `char_count` is emitted
    # under the same metric name by THREE different operations, so summing the metric
    # alone triple-counts —
    #   * data_ingestion/file_processing  -> the document's characters (what we want)
    #   * embedding_generation/content_embedding -> the SAME characters again, at
    #     embed time
    #   * embedding_generation/query_embedding  -> the characters of a *search query*,
    #     which are not indexed content at all
    #
    # Measured live 2026-08-20 on a real account: the un-filtered sum reported 9315
    # where the true ingested figure was 4008 (+5206 re-counted at embedding, +101
    # of search-query text) — 2.3x over, and since this aggregate is lifetime-scoped
    # rather than month-bounded, the drift only ever grows.
    #
    # General rule this is an instance of: metric_name alone is not a unique key;
    # (operation, metric_name) is. See docs/modules/usage-event-catalog.md.
    stmt_idx = (
        select(func.coalesce(func.sum(UsageMetric.metric_value), 0))
        .select_from(UsageMetric)
        .join(UsageEvent, UsageMetric.event_id == UsageEvent.event_id)
        .where(
            UsageEvent.owner_id == owner_id,
            UsageMetric.metric_name.in_(["indexed_characters", "char_count"]),
            UsageEvent.operation == "data_ingestion",
        )
    )
    indexed_characters = int((await session.execute(stmt_idx)).scalar_one() or 0)

    # Standing count: knowledge items the account holds right now, across all its
    # knowledge bases. Read from the `document` table (one row per ingested unit,
    # whatever the source), NOT from usage_events — a knowledge item is a resource
    # that currently exists and can be deleted to free a slot, unlike an append-only
    # usage event. `deleted=false` on both sides so a soft-deleted item or KB frees
    # its slot. This is the single place all ingest sources are counted, so it holds
    # whether the row was written at the API, by a crawl fan-out, or by a sync run.
    stmt_knowledge_items = (
        select(func.count())
        .select_from(Document)
        .join(KnowledgeConfig, Document.knowledge_id == KnowledgeConfig.knowledge_id)
        .where(KnowledgeConfig.owner_id == owner_id, Document.deleted == False, KnowledgeConfig.deleted == False)  # noqa: E712
    )
    knowledge_items = int((await session.execute(stmt_knowledge_items)).scalar_one() or 0)

    # documents/urls: the same standing count, split by Document.content_type. Only
    # 'file' and 'url' are counted — pasted text ('plain_text') has no cap and is
    # deliberately left out of both. documents + urls + (uncounted text rows) sum to
    # knowledge_items above; these are sub-counts, not a second total.
    stmt_by_type = (
        select(Document.content_type, func.count())
        .select_from(Document)
        .join(KnowledgeConfig, Document.knowledge_id == KnowledgeConfig.knowledge_id)
        .where(
            KnowledgeConfig.owner_id == owner_id, Document.deleted == False,  # noqa: E712
            KnowledgeConfig.deleted == False,  # noqa: E712
            Document.content_type.in_(["file", "url"]),
        )
        .group_by(Document.content_type)
    )
    by_type = dict((await session.execute(stmt_by_type)).all())
    documents = int(by_type.get("file", 0) or 0)
    urls = int(by_type.get("url", 0) or 0)

    # Standing count: knowledge bases ("agents") the account has. One indexed query
    # on the local table — no Agent Registry, which is why the source service dropped
    # this and this service can keep it (see docs/plan/usage-and-limits).
    stmt_agents = (
        select(func.count())
        .select_from(KnowledgeConfig)
        .where(KnowledgeConfig.owner_id == owner_id, KnowledgeConfig.deleted == False)  # noqa: E712
    )
    agents = int((await session.execute(stmt_agents)).scalar_one() or 0)

    stmt_conversations = (
        select(func.count(func.distinct(UsageEvent.session_id)))
        .where(
            UsageEvent.owner_id == owner_id, UsageEvent.session_id.is_not(None),
            UsageEvent.timestamp >= month_start, UsageEvent.timestamp < month_end,
        )
    )
    conversations_this_month = int((await session.execute(stmt_conversations)).scalar_one() or 0)

    # Messages == one *chat* LLM generation. Counts `llm_generation` ONLY, and
    # deliberately NOT `llm_call`: the worker emits `operation="llm_call"` for
    # document enrichment (fs-agent-workers
    # celery_utils/shared/user_usage_tracking.py:355, label="document_enrichment").
    # Counting that as a "message" made ingest consume the chat quota — indexing a
    # few hundred documents would exhaust max_messages_per_month without anyone
    # sending a chat message. Enrichment spend is still limited, via
    # max_llm_tokens_per_month, which is what `llm_call` maps to in
    # OPERATION_LIMIT_MAPPING. See docs/modules/usage-event-catalog.md.
    #
    # This is also the one aggregate where BYOK matters: under `cost_bearing_only`,
    # exclude events paid by an owner-supplied provider key. `key_type` is set by
    # chat/rag_node.py:766 on every LLM usage event. NULL is treated as
    # platform-key (older rows written before the column existed).
    stmt_messages = (
        select(func.count())
        .select_from(UsageEvent)
        .where(
            UsageEvent.owner_id == owner_id, UsageEvent.operation == "llm_generation",
            UsageEvent.timestamp >= month_start, UsageEvent.timestamp < month_end,
        )
    )
    if cost_bearing_only:
        stmt_messages = stmt_messages.where(
            (UsageEvent.key_type != "owner-key") | (UsageEvent.key_type.is_(None))
        )
    messages_this_month = int((await session.execute(stmt_messages)).scalar_one() or 0)

    # Same count over today's window (00:00 UTC → now), for max_messages_per_day.
    # Same operation filter and the same BYOK exemption as the monthly count — the
    # daily cap is a tighter window on the identical activity, so the two must agree
    # on what a "message" is or a request could pass one gate and fail the other for
    # reasons no user could see.
    day_start, day_end = _day_bounds()
    stmt_messages_today = (
        select(func.count())
        .select_from(UsageEvent)
        .where(
            UsageEvent.owner_id == owner_id, UsageEvent.operation == "llm_generation",
            UsageEvent.timestamp >= day_start, UsageEvent.timestamp < day_end,
        )
    )
    if cost_bearing_only:
        stmt_messages_today = stmt_messages_today.where(
            (UsageEvent.key_type != "owner-key") | (UsageEvent.key_type.is_(None))
        )
    messages_today = int((await session.execute(stmt_messages_today)).scalar_one() or 0)

    # LLM tokens: restricted to operations that are actually LLM generation —
    # `llm_generation` (chat) and `llm_call` (worker document enrichment). The
    # operation filter is load-bearing, not decoration: `send_embedding_event`
    # reports its embedding tokens under the SAME metric name `input_tokens`
    # (fs-agent-workers user_usage_tracking.py, and utils/qdrant/usage_tracking.py
    # for search-time query embeddings). Without the filter, embedding tokens count
    # against max_llm_tokens_per_month — two different cost units, roughly two
    # orders of magnitude apart in price, summed into one ceiling.
    #
    # Confirmed live 2026-08-20 on a real account: 5776 "llm_tokens_this_month"
    # was 5074 enrichment + 702 embedding, with zero chat tokens.
    # See docs/modules/usage-event-catalog.md.
    stmt_llm = (
        select(func.coalesce(func.sum(UsageMetric.metric_value), 0))
        .select_from(UsageMetric)
        .join(UsageEvent, UsageMetric.event_id == UsageEvent.event_id)
        .where(
            UsageEvent.owner_id == owner_id,
            UsageMetric.metric_name.in_(["input_tokens", "output_tokens"]),
            UsageEvent.operation.in_(["llm_generation", "llm_call"]),
            UsageEvent.timestamp >= month_start, UsageEvent.timestamp < month_end,
        )
    )
    llm_tokens_this_month = int((await session.execute(stmt_llm)).scalar_one() or 0)

    # Estimated spend this month — the same figure the analytics cost strip reports,
    # via the same per-event calculation, so the Limits tab and the Overview tab can
    # never disagree about what the month cost. Skipped (0.0) unless asked for; see
    # the include_cost note in the docstring.
    cost_usd_this_month = 0.0
    if include_cost:
        from utils.usage.analytics import _calculate_total_costs

        cost_filters = [
            UsageEvent.owner_id == owner_id,
            UsageEvent.timestamp >= month_start, UsageEvent.timestamp < month_end,
        ]
        try:
            breakdown = await _calculate_total_costs(session, cost_filters)
            cost_usd_this_month = float(breakdown.total_cost_usd or 0.0)
        except Exception as e:
            # Reporting-only figure: a pricing failure must not take down
            # /limits/status with it. 0.0 with a loud log, same fail-open posture
            # as enforcement.
            logger.error(f"cost_usd_this_month calculation failed for owner_id={owner_id}: {e}", exc_info=True)

    return CurrentUsageInfo(
        indexed_characters=indexed_characters, knowledge_items=knowledge_items, agents=agents,
        documents=documents, urls=urls,
        messages_today=messages_today, cost_usd_this_month=cost_usd_this_month,
        conversations_this_month=conversations_this_month,
        messages_this_month=messages_this_month, llm_tokens_this_month=llm_tokens_this_month,
    )


def calculate_remaining_quota(limits: LimitsInfo, usage: CurrentUsageInfo) -> RemainingQuotaInfo:
    def calc(limit_val, used):
        if limit_val is None:
            return None
        try:
            return int(limit_val) - int(used)
        except Exception:
            return None

    # Cost remaining keeps its decimals — int() would round $0.75 of headroom to $0
    # and read as exhausted.
    cost_remaining = None
    if limits.max_cost_usd_per_month is not None:
        try:
            cost_remaining = round(float(limits.max_cost_usd_per_month) - float(usage.cost_usd_this_month), 6)
        except Exception:
            cost_remaining = None

    return RemainingQuotaInfo(
        indexed_characters=calc(limits.max_indexed_characters, usage.indexed_characters),
        knowledge_items=calc(limits.max_knowledge_items, usage.knowledge_items),
        agents=calc(limits.max_agents, usage.agents),
        conversations=calc(limits.max_conversations_per_month, usage.conversations_this_month),
        messages=calc(limits.max_messages_per_month, usage.messages_this_month),
        llm_tokens=calc(limits.max_llm_tokens_per_month, usage.llm_tokens_this_month),
        documents=calc(limits.max_documents, usage.documents),
        urls=calc(limits.max_urls, usage.urls),
        messages_today=calc(limits.max_messages_per_day, usage.messages_today),
        cost_usd=cost_remaining,
    )


async def get_usage_status(session: AsyncSession, owner_id: str) -> UsageStatusResponse:
    month_start, month_end = _month_bounds()
    limits = await get_owner_limits(session, owner_id)
    usage = await get_current_usage(session, owner_id, month_start, month_end, include_cost=True)
    remaining = calculate_remaining_quota(limits, usage)
    return UsageStatusResponse(
        owner_id=owner_id, period=PeriodInfo(start=month_start.isoformat(), end=month_end.isoformat()),
        limits=limits, current_usage=usage, remaining=remaining,
    )


# Which OPERATION_LIMIT_MAPPING keys actually have an enforce_operation(...) call
# site wired up today. Kept separate from OPERATION_LIMIT_MAPPING itself because
# that dict describes what an operation WOULD gate if called, not what is actually
# called from a router. Confirmed by grep 2026-08-26 (docs/plan/usage-and-limits):
# create_knowledge_item (document_management.py, batch_document_management.py),
# start_conversation and send_message (chat.py), create_agent (knowledge_management.py).
# index_content is reported by /limits/status but nothing calls
# enforce_operation(Operation.index_content) anywhere in this repo, so that ceiling
# can never trip a 429 regardless of USAGE_ENFORCE_LIMITS.
# llm_call GAINED a real call site 2026-09-08: routers/enrichment_configuration_new.py's
# POST /v1/enrichment/preview (docs/plan/collection-creation-flow) is the first pre-flight
# enforce_operation(Operation.llm_call, ...) in this repo — worker-side enrichment still
# emits llm_call events async with no pre-flight check, but the preview endpoint's
# max_llm_tokens_per_month gate is real. Update this set if a new call site is added.
GATED_LIMIT_KEYS = {
    "max_knowledge_items", "max_conversations_per_month", "max_messages_per_month", "max_agents",
    "max_llm_tokens_per_month",
    # Gated by the same create_knowledge_item / send_message call sites as their
    # parent keys — the router passes the source-specific metric (documents / urls /
    # messages_today) alongside the general one, so these trip at the same gate.
    "max_documents", "max_urls", "max_messages_per_day",
}


def _is_enforcement_enabled() -> bool:
    """Mirrors utils.usage.enforcement._enforcement_enabled() exactly (same env var,
    same parsing). Not imported from there — enforcement.py imports
    check_operation_allowed from this module, so importing back would be circular."""
    return os.getenv("USAGE_ENFORCE_LIMITS", "false").strip().lower() in {"1", "true", "yes", "on"}


# Tiers this service actually has, per OwnerConfigResponse.tier_type's Literal.
# Not a marketing catalog — just names for the three values the DB can hold.
_TIER_DISPLAY_NAMES = {"trial": "Trial", "payg": "Pay as you go", "custom": "Custom", "internal": "Internal"}


def _percent(used, limit_val) -> Optional[float]:
    if limit_val is None or limit_val == 0:
        return None
    try:
        return round(min(100.0, (float(used) / float(limit_val)) * 100.0), 1)
    except (TypeError, ValueError):
        return None


async def get_usage_limits_screen(session: AsyncSession, owner_id: str) -> LimitsScreenResponse:
    """Screen-shaped Limits tab — proposed in docs/product/usage-and-limits-screen.md
    §7, modelled on NL2SQL's own `GET /v1/owners/{owner_id}/limits`
    Repackages exactly the numbers `/limits/status` and
    `/usage/owner-config` already return, one row per limit, with the display
    wording and whether the row can actually refuse anything today — so a UI
    renders it with no arithmetic and no need to know this service's field names.
    Adds no ceiling and changes no configured value.
    """
    from utils.usage.owner_config import get_owner_config
    from utils.usage.credentials import get_byok_providers

    month_start, month_end = _month_bounds()
    limits = await get_owner_limits(session, owner_id)
    usage = await get_current_usage(session, owner_id, month_start, month_end, include_cost=True)

    config = await get_owner_config(session, owner_id)
    tier_type = config.tier_type if config else "trial"
    tier = TierInfo(
        name=_TIER_DISPLAY_NAMES.get(tier_type, tier_type.title()),
        tier_type=tier_type,
        is_default=config is None,
    )
    # get_owner_limits has already lifted every ceiling for a verified internal
    # account; name it so each row reads "Unlimited on Internal", not "on Trial".
    from utils.usage.internal_accounts import is_internal_owner
    if await is_internal_owner(owner_id):
        tier = TierInfo(name=_TIER_DISPLAY_NAMES["internal"], tier_type="internal", is_default=False)
    # max_team_members lives on OwnerConfigResponse.platform_limits, not on LimitsInfo
    # (limit_checking's own schema never carried it — see docs/plan/usage-and-limits
    # gap G7). Fall back to the same YAML default get_owner_limits itself falls back to.
    if config:
        max_team_members = config.platform_limits.max_team_members
    else:
        from utils.usage.owner_defaults import get_defaults_manager
        max_team_members = get_defaults_manager().defaults["platform_limits"].get("max_team_members")

    own_credentials = sorted(await get_byok_providers(owner_id))
    enforcement_on = _is_enforcement_enabled()

    def item(key, label, used, limit_val, unit, note, window, plan_word, has_usage_counter=True, money=False) -> LimitsScreenItem:
        used_v = float(used)
        lifted = limit_val is None and key in GATED_LIMIT_KEYS and bool(own_credentials)
        if limit_val is None:
            maximum = f"Unlimited on {tier.name}"
        elif money:
            maximum = f"${limit_val:,.2f} {plan_word} on {tier.name}"
        else:
            maximum = f"{limit_val:,} {plan_word} on {tier.name}"
        # No bar when there's nothing real to fill it with — a 0% bar over an
        # uncounted metric reads as "you have used none of this", which is a claim
        # this service cannot back up.
        percent = None if (lifted or not has_usage_counter) else _percent(used_v, limit_val)
        return LimitsScreenItem(
            key=key, label=label, used=used_v, limit=limit_val, unit=unit, maximum=maximum, note=note,
            window=window, lifted=lifted, percent=percent,
            enforced=enforcement_on and key in GATED_LIMIT_KEYS and limit_val is not None,
        )

    groups = [
        LimitsScreenGroup(group="Workspace", items=[
            item("max_agents", "Collections you can create", usage.agents, limits.max_agents, "collections",
                 "Every collection in this workspace counts, active or paused.", "current", "collections"),
            item("max_knowledge_items", "Everything added, across the whole workspace", usage.knowledge_items,
                 limits.max_knowledge_items, "items",
                 "Every ingested unit — files, links and pasted text together — added up across "
                 "every collection right now. The two rows below split this by source and apply "
                 "alongside it, whichever trips first.",
                 "current", "items"),
            item("max_documents", "Documents you can hold", usage.documents,
                 limits.max_documents, "documents",
                 "Uploaded files held right now, across every collection — a synced connector "
                 "page counts as a document too. Delete one and the slot comes back.",
                 "current", "documents"),
            item("max_urls", "Links you can hold", usage.urls,
                 limits.max_urls, "links",
                 "Website links held right now, across every collection. Counted per link you "
                 "add, not per page a crawl reads underneath it.",
                 "current", "links"),
        ]),
        LimitsScreenGroup(group="Requests, tokens and cost", items=[
            item("max_conversations_per_month", "Conversations this month", usage.conversations_this_month,
                 limits.max_conversations_per_month, "conversations",
                 "Distinct chat sessions started this calendar month. Continuing an existing one is free.",
                 "calendar_month", "conversations"),
            item("max_messages_per_month", "Questions this month", usage.messages_this_month,
                 limits.max_messages_per_month, "questions",
                 "Every question asked across every collection this calendar month.",
                 "calendar_month", "questions"),
            item("max_messages_per_day", "Questions today", usage.messages_today,
                 limits.max_messages_per_day, "questions",
                 "The same questions, counted over today only. Resets at 00:00 UTC. Both this "
                 "and the monthly row apply — whichever fills first stops the next question.",
                 "calendar_day", "questions"),
            # "Characters indexed" (max_indexed_characters) dropped from this tab on
            # 2026-09-03 — reporting-only (never blocks: see GATED_LIMIT_KEYS)
            # and one more bar on an already dense screen for a ceiling nothing enforces.
            # usage.indexed_characters itself is untouched and still powers the Overview
            # tab's character card.
            #
            # "Tokens this month" (max_llm_tokens_per_month) stays, despite the same
            # request and the same reasoning applying — removing it here would silently
            # break the Document Intelligence home tile's allowance meter, which reads
            # *this exact row* out of this endpoint's response
            # (docs/product/usage-and-limits-screen.md §1: "the max_llm_tokens_per_month
            # row of GET .../usage/limits/screen"). Dropping it from the Limits tab
            # without breaking the home tile needs the home tile's meter split onto its
            # own response field first — flagged, not done, since that's a second
            # endpoint/frontend change beyond "hide a row."
            item("max_llm_tokens_per_month", "Tokens this month", usage.llm_tokens_this_month,
                 limits.max_llm_tokens_per_month, "tokens",
                 "Read and written together, across every collection, this calendar month.",
                 "calendar_month", "tokens"),
            item("max_cost_usd_per_month", "Estimated spend this month", usage.cost_usd_this_month,
                 limits.max_cost_usd_per_month, "of estimated spend",
                 "The same estimate the Usage tab's cost strip shows, against this plan's monthly "
                 "allowance. An estimate priced per model, not a bill — and this row reports, it "
                 "never blocks a request.", "calendar_month", "of estimated spend", money=True),
        ]),
        LimitsScreenGroup(group="Team", items=[
            item("max_team_members", "Team members", 0, max_team_members, "members",
                 "This account has no running count of team members yet — the ceiling is real, "
                 "the usage figure is not.", "current", "members", has_usage_counter=False),
        ]),
    ]

    exceeded = [
        it.key for g in groups for it in g.items
        if it.limit is not None and not it.lifted and it.used > it.limit
    ]

    return LimitsScreenResponse(
        owner_id=owner_id, tier=tier,
        window=PeriodInfo(start=month_start.isoformat(), end=month_end.isoformat()),
        own_credentials=own_credentials, groups=groups, exceeded=exceeded,
    )


async def check_operation_allowed(session: AsyncSession, owner_id: str, operation, metrics: List[EstimatedMetric]) -> UsageCheckResponse:
    op = operation.value if isinstance(operation, Operation) else str(operation)
    if op not in OPERATION_LIMIT_MAPPING:
        raise ValueError(f"Unknown operation: {op}")

    limits = await get_owner_limits(session, owner_id)
    month_start, month_end = _month_bounds()
    # send_message is the one op whose count is provider-spend, so it's the one that
    # gets the BYOK exemption at the gate. Everything else counts a resource we own.
    cost_bearing_only = (op == "send_message")
    usage = await get_current_usage(session, owner_id, month_start, month_end, cost_bearing_only=cost_bearing_only)

    requested_by_limit: Dict[str, int] = {}
    for m in metrics:
        name = m.name if isinstance(m, EstimatedMetric) else m.get("name")
        val = m.value if isinstance(m, EstimatedMetric) else m.get("value", 0)
        limit_key = METRIC_TO_LIMIT_KEY.get(name)
        if limit_key:
            requested_by_limit[limit_key] = requested_by_limit.get(limit_key, 0) + int(val)

    current_map = {
        "max_indexed_characters": usage.indexed_characters,
        "max_knowledge_items": usage.knowledge_items, "max_agents": usage.agents,
        "max_conversations_per_month": usage.conversations_this_month,
        "max_messages_per_month": usage.messages_this_month, "max_llm_tokens_per_month": usage.llm_tokens_this_month,
        "max_documents": usage.documents, "max_urls": usage.urls,
        "max_messages_per_day": usage.messages_today,
        # max_cost_usd_per_month deliberately absent: reporting-only, never checked
        # here — see the field's docstring on OwnerPlatformLimits.
    }

    would_exceed: List[ExceededLimitInfo] = []
    for limit_key in OPERATION_LIMIT_MAPPING[op]:
        current = current_map.get(limit_key, 0)
        requested = requested_by_limit.get(limit_key, 0)
        limit_val = getattr(limits, limit_key)
        if limit_val is None:
            continue
        if current + requested > int(limit_val):
            would_exceed.append(ExceededLimitInfo(limit_name=limit_key, current=current, limit=int(limit_val), requested=requested))

    allowed = len(would_exceed) == 0

    temp_usage = CurrentUsageInfo(
        indexed_characters=usage.indexed_characters + requested_by_limit.get("max_indexed_characters", 0),
        knowledge_items=usage.knowledge_items + requested_by_limit.get("max_knowledge_items", 0),
        agents=usage.agents + requested_by_limit.get("max_agents", 0),
        documents=usage.documents + requested_by_limit.get("max_documents", 0),
        urls=usage.urls + requested_by_limit.get("max_urls", 0),
        messages_today=usage.messages_today + requested_by_limit.get("max_messages_per_day", 0),
        cost_usd_this_month=usage.cost_usd_this_month,
        conversations_this_month=usage.conversations_this_month + requested_by_limit.get("max_conversations_per_month", 0),
        messages_this_month=usage.messages_this_month + requested_by_limit.get("max_messages_per_month", 0),
        llm_tokens_this_month=usage.llm_tokens_this_month + requested_by_limit.get("max_llm_tokens_per_month", 0),
    )
    remaining_after = calculate_remaining_quota(limits, temp_usage)

    return UsageCheckResponse(allowed=allowed, remaining_quota=remaining_after.dict(), would_exceed=would_exceed)
