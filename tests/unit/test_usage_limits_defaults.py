"""Unit tests for the tier-defaults fallback in
`utils/usage/limit_checking.get_owner_limits`.

Most accounts have no `owner_configurations` row — the row is only written on an
explicit owner-config write, never on signup. Before the fallback existed, those
accounts got an empty `LimitsInfo` where every ceiling is `None`, and `None` means
UNLIMITED downstream: `/limits/status` reported no ceilings (so a usage meter has
no denominator) and `check_operation_allowed` answered `allowed=True` no matter
how much had been consumed.

No DB and no network: `get_owner_config` is patched, and the defaults come from
`utils/config/usage/owner_defaults.yaml` on disk.
"""

import pytest

from utils.usage import limit_checking
from utils.usage.limit_checking import get_owner_limits
from utils.usage.owner_defaults import get_defaults_manager


@pytest.fixture
def yaml_limits():
    return get_defaults_manager().defaults["platform_limits"]


@pytest.mark.asyncio
async def test_missing_owner_row_falls_back_to_tier_defaults(monkeypatch, yaml_limits):
    """No config row -> the YAML tier ceilings, not an empty (unlimited) LimitsInfo."""
    async def no_row(session, owner_id):
        return None

    monkeypatch.setattr("utils.usage.owner_config.get_owner_config", no_row)

    limits = await get_owner_limits(session=None, owner_id="nobody@futuresmart.ai")

    assert limits.max_agents == yaml_limits["max_agents"]
    assert limits.max_messages_per_month == yaml_limits["max_messages_per_month"]
    assert limits.max_conversations_per_month == yaml_limits["max_conversations_per_month"]
    assert limits.max_knowledge_items == yaml_limits["max_knowledge_items"]
    assert limits.max_indexed_characters == yaml_limits["max_indexed_characters"]
    assert limits.max_llm_tokens_per_month == yaml_limits["max_llm_tokens_per_month"]


@pytest.mark.asyncio
async def test_no_ceiling_is_left_null_for_a_missing_row(monkeypatch):
    """The regression itself: a missing row must not read as unlimited on any metric.

    `None` is a legitimate value meaning "unlimited", so it is only correct here if
    the YAML actually declares it — and today the YAML sets all six.
    """
    async def no_row(session, owner_id):
        return None

    monkeypatch.setattr("utils.usage.owner_config.get_owner_config", no_row)

    limits = await get_owner_limits(session=None, owner_id="nobody@futuresmart.ai")

    assert not [name for name, value in limits.model_dump().items() if value is None]


@pytest.mark.asyncio
async def test_existing_owner_row_wins_over_defaults(monkeypatch, yaml_limits):
    """A real row is still authoritative — the fallback must not override it."""
    from utils.pydantic_models_usage_owner_config import (
        OwnerConfigResponse, PlatformLimits, DefaultModels,
    )

    overridden = dict(yaml_limits)
    overridden["max_agents"] = 99
    overridden["max_messages_per_month"] = 12345

    async def with_row(session, owner_id):
        return OwnerConfigResponse(
            owner_id=owner_id, tier_type="custom", base_tier=None,
            platform_limits=PlatformLimits(**overridden),
            default_models=DefaultModels(**get_defaults_manager().defaults["default_models"]),
            enabled_services={},
        )

    monkeypatch.setattr("utils.usage.owner_config.get_owner_config", with_row)

    limits = await get_owner_limits(session=None, owner_id="someone@futuresmart.ai")

    assert limits.max_agents == 99
    assert limits.max_messages_per_month == 12345


@pytest.mark.asyncio
async def test_no_owner_id_still_returns_empty_limits(monkeypatch):
    """Unchanged behaviour: with no owner to look up there is nothing to default to.

    Deliberately not routed through the tier defaults — a caller with no owner_id is
    a bug to be surfaced by the existing warning log, not an account to be quota'd.
    """
    limits = await get_owner_limits(session=None, owner_id="")

    assert all(v is None for v in limits.model_dump().values())
