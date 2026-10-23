"""Unit tests for the four limits added 2026-08-26 (docs/plan/usage-and-limits):
max_documents, max_urls (standing sub-caps of max_knowledge_items, by content_type),
max_messages_per_day (a second, tighter window on the same messages), and
max_cost_usd_per_month (reporting-only — never enforced).

No DB: get_owner_limits and get_current_usage are monkeypatched, same style as the
sibling test files.
"""

import pytest

from utils.usage import limit_checking
from utils.usage.limit_checking import (
    check_operation_allowed, calculate_remaining_quota,
    OPERATION_LIMIT_MAPPING, GATED_LIMIT_KEYS,
)
from utils.pydantic_models_usage_limits import LimitsInfo, CurrentUsageInfo, EstimatedMetric, Operation


LIMITS = LimitsInfo(
    max_agents=10, max_indexed_characters=1_000_000, max_knowledge_items=500,
    max_conversations_per_month=100, max_messages_per_month=500, max_llm_tokens_per_month=1_000_000,
    max_documents=400, max_urls=100, max_messages_per_day=50, max_cost_usd_per_month=50.0,
)


def _usage(**overrides):
    base = dict(
        indexed_characters=0, knowledge_items=0, agents=0, documents=0, urls=0,
        messages_today=0, cost_usd_this_month=0.0,
        conversations_this_month=0, messages_this_month=0, llm_tokens_this_month=0,
    )
    base.update(overrides)
    return CurrentUsageInfo(**base)


def _patch(monkeypatch, usage):
    async def fake_limits(session, owner_id):
        return LIMITS

    async def fake_usage(session, owner_id, month_start=None, month_end=None, cost_bearing_only=False, include_cost=False):
        return usage

    monkeypatch.setattr(limit_checking, "get_owner_limits", fake_limits)
    monkeypatch.setattr(limit_checking, "get_current_usage", fake_usage)


@pytest.mark.asyncio
async def test_document_cap_trips_at_the_ingest_gate(monkeypatch):
    _patch(monkeypatch, _usage(documents=400, knowledge_items=400))
    result = await check_operation_allowed(
        None, "me", Operation.create_knowledge_item,
        [EstimatedMetric(name="knowledge_items", value=1), EstimatedMetric(name="documents", value=1)],
    )
    assert result.allowed is False
    assert [e.limit_name for e in result.would_exceed] == ["max_documents"]


@pytest.mark.asyncio
async def test_url_add_does_not_consume_the_document_cap(monkeypatch):
    """A weblink add passes the urls metric only — a full document cap must not
    block it, and vice versa. This is the whole point of source-specific metrics."""
    _patch(monkeypatch, _usage(documents=400, urls=0, knowledge_items=400))
    result = await check_operation_allowed(
        None, "me", Operation.create_knowledge_item,
        [EstimatedMetric(name="knowledge_items", value=1), EstimatedMetric(name="urls", value=1)],
    )
    assert result.allowed is True


@pytest.mark.asyncio
async def test_combined_cap_still_applies_over_the_sub_caps(monkeypatch):
    """documents and urls are sub-caps, not replacements: an account under both
    sub-caps but at the combined knowledge_items ceiling is still refused."""
    _patch(monkeypatch, _usage(documents=300, urls=50, knowledge_items=500))
    result = await check_operation_allowed(
        None, "me", Operation.create_knowledge_item,
        [EstimatedMetric(name="knowledge_items", value=1), EstimatedMetric(name="documents", value=1)],
    )
    assert result.allowed is False
    assert [e.limit_name for e in result.would_exceed] == ["max_knowledge_items"]


@pytest.mark.asyncio
async def test_daily_message_cap_trips_before_the_monthly_one(monkeypatch):
    _patch(monkeypatch, _usage(messages_this_month=100, messages_today=50))
    result = await check_operation_allowed(
        None, "me", Operation.send_message,
        [EstimatedMetric(name="messages", value=1), EstimatedMetric(name="messages_today", value=1)],
    )
    assert result.allowed is False
    assert [e.limit_name for e in result.would_exceed] == ["max_messages_per_day"]


@pytest.mark.asyncio
async def test_monthly_message_cap_still_trips_on_its_own(monkeypatch):
    _patch(monkeypatch, _usage(messages_this_month=500, messages_today=10))
    result = await check_operation_allowed(
        None, "me", Operation.send_message,
        [EstimatedMetric(name="messages", value=1), EstimatedMetric(name="messages_today", value=1)],
    )
    assert result.allowed is False
    assert [e.limit_name for e in result.would_exceed] == ["max_messages_per_month"]


def test_cost_limit_is_never_gated():
    """max_cost_usd_per_month must not appear in any operation's gate list nor in
    GATED_LIMIT_KEYS — it reports, it never refuses (estimating a call's cost before
    the call runs would be a guess)."""
    assert "max_cost_usd_per_month" not in GATED_LIMIT_KEYS
    for keys in OPERATION_LIMIT_MAPPING.values():
        assert "max_cost_usd_per_month" not in keys


def test_remaining_quota_carries_the_new_dimensions():
    usage = _usage(documents=10, urls=5, messages_today=7, cost_usd_this_month=12.5)
    remaining = calculate_remaining_quota(LIMITS, usage)
    assert remaining.documents == 390
    assert remaining.urls == 95
    assert remaining.messages_today == 43
    assert remaining.cost_usd == 37.5


def test_cost_remaining_keeps_decimals():
    """int() would round $0.75 of headroom to $0 and read as exhausted."""
    usage = _usage(cost_usd_this_month=49.25)
    remaining = calculate_remaining_quota(LIMITS, usage)
    assert remaining.cost_usd == 0.75
