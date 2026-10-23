"""Unit tests for get_usage_limits_screen (utils/usage/limit_checking.py) — the
proposed screen-shaped Limits tab from docs/product/usage-and-limits-screen.md §7.

No DB, no network: get_owner_limits, get_current_usage, get_owner_config and
get_byok_providers are all monkeypatched, same style as test_usage_limits_defaults.py.
"""

import pytest

from utils.usage import limit_checking
from utils.usage.limit_checking import get_usage_limits_screen, GATED_LIMIT_KEYS
from utils.pydantic_models_usage_limits import LimitsInfo, CurrentUsageInfo


LIMITS = LimitsInfo(
    max_agents=10, max_indexed_characters=1_000_000, max_knowledge_items=500,
    max_conversations_per_month=100, max_messages_per_month=500, max_llm_tokens_per_month=1_000_000,
    max_documents=400, max_urls=100, max_messages_per_day=50, max_cost_usd_per_month=50.0,
)
USAGE = CurrentUsageInfo(
    indexed_characters=400_000, knowledge_items=3, agents=3,
    documents=2, urls=1, messages_today=7, cost_usd_this_month=1.25,
    conversations_this_month=10, messages_this_month=50, llm_tokens_this_month=250_000,
)


def _patch_common(monkeypatch, *, config=None, own_keys=None):
    async def fake_limits(session, owner_id):
        return LIMITS

    async def fake_usage(session, owner_id, month_start=None, month_end=None, cost_bearing_only=False, include_cost=False):
        # Pin the new contract: the screen endpoint must ask for the cost figure,
        # since its cost row would otherwise silently render $0.00 forever.
        assert include_cost is True
        return USAGE

    async def fake_config(session, owner_id):
        return config

    async def fake_byok(owner_id):
        return own_keys or set()

    monkeypatch.setattr(limit_checking, "get_owner_limits", fake_limits)
    monkeypatch.setattr(limit_checking, "get_current_usage", fake_usage)
    monkeypatch.setattr("utils.usage.owner_config.get_owner_config", fake_config)
    monkeypatch.setattr("utils.usage.credentials.get_byok_providers", fake_byok)


@pytest.mark.asyncio
async def test_unprovisioned_account_reads_as_trial_default(monkeypatch):
    _patch_common(monkeypatch, config=None)
    screen = await get_usage_limits_screen(session=None, owner_id="new@futuresmart.ai")
    assert screen.tier.tier_type == "trial"
    assert screen.tier.is_default is True


@pytest.mark.asyncio
async def test_every_row_reflects_the_shared_seed_exactly(monkeypatch):
    """Two screens disagreeing about how much has been used would be worse than not
    shipping the second one — every used figure here must be the CurrentUsageInfo
    the mocked get_current_usage returned, not recomputed."""
    _patch_common(monkeypatch, config=None)
    screen = await get_usage_limits_screen(session=None, owner_id="me")
    by_key = {it.key: it for g in screen.groups for it in g.items}
    assert by_key["max_agents"].used == USAGE.agents
    assert by_key["max_knowledge_items"].used == USAGE.knowledge_items
    assert by_key["max_conversations_per_month"].used == USAGE.conversations_this_month
    assert by_key["max_messages_per_month"].used == USAGE.messages_this_month
    assert by_key["max_llm_tokens_per_month"].used == USAGE.llm_tokens_this_month
    assert by_key["max_documents"].used == USAGE.documents
    assert by_key["max_urls"].used == USAGE.urls
    assert by_key["max_messages_per_day"].used == USAGE.messages_today
    assert by_key["max_cost_usd_per_month"].used == USAGE.cost_usd_this_month


@pytest.mark.asyncio
async def test_only_gated_keys_can_report_enforced(monkeypatch):
    monkeypatch.setenv("USAGE_ENFORCE_LIMITS", "true")
    _patch_common(monkeypatch, config=None)
    screen = await get_usage_limits_screen(session=None, owner_id="me")
    for g in screen.groups:
        for it in g.items:
            if it.enforced:
                assert it.key in GATED_LIMIT_KEYS
    # max_indexed_characters dropped from this screen entirely (2026-09-03, a
    # reporting-only row that never enforced anything — see limit_checking.py's
    # get_usage_limits_screen comment). max_cost_usd_per_month is still reporting-only —
    # nothing calls enforce_operation for it. max_llm_tokens_per_month changed 2026-09-08:
    # POST /v1/enrichment/preview (docs/plan/collection-creation-flow) is a real
    # enforce_operation(Operation.llm_call, ...) call site now, so this key genuinely
    # gates something and belongs in GATED_LIMIT_KEYS — see limit_checking.py's comment
    # on that set. Worker-side document enrichment still emits llm_call events async
    # with no pre-flight check; only the preview endpoint enforces ahead of spend.
    by_key = {it.key: it for g in screen.groups for it in g.items}
    assert "max_indexed_characters" not in by_key
    assert by_key["max_llm_tokens_per_month"].enforced is True
    assert by_key["max_cost_usd_per_month"].enforced is False
    assert by_key["max_messages_per_month"].enforced is True
    assert by_key["max_messages_per_day"].enforced is True
    assert by_key["max_documents"].enforced is True
    assert by_key["max_urls"].enforced is True


@pytest.mark.asyncio
async def test_enforcement_off_means_nothing_is_enforced(monkeypatch):
    monkeypatch.delenv("USAGE_ENFORCE_LIMITS", raising=False)
    _patch_common(monkeypatch, config=None)
    screen = await get_usage_limits_screen(session=None, owner_id="me")
    assert all(not it.enforced for g in screen.groups for it in g.items)


@pytest.mark.asyncio
async def test_team_members_row_draws_no_bar_and_no_enforcement(monkeypatch):
    _patch_common(monkeypatch, config=None)
    screen = await get_usage_limits_screen(session=None, owner_id="me")
    by_key = {it.key: it for g in screen.groups for it in g.items}
    tm = by_key["max_team_members"]
    assert tm.percent is None
    assert tm.enforced is False


@pytest.mark.asyncio
async def test_own_credentials_sorted_and_used_for_lifted(monkeypatch):
    _patch_common(monkeypatch, config=None, own_keys={"groq", "anthropic"})
    screen = await get_usage_limits_screen(session=None, owner_id="me")
    assert screen.own_credentials == ["anthropic", "groq"]
