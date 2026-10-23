"""Unit tests for gap G2 (docs/plan/usage-and-limits/state.md): the analytics
query endpoint used to reject `knowledge_id` in both `group_by` and `filters`, even
though the underlying column and query-building code already supported it — the
Pydantic allowlist was the only thing standing in the way. These tests cover the
schema layer only (no DB) — see the same task's state.md for why a live check is
still needed to confirm the SQL grouping behaves as expected against real data.
"""

import pytest
from pydantic import ValidationError

from utils.pydantic_models_usage_analytics import AnalyticsFilters, AnalyticsQueryRequest, DateRange


def _request(**kwargs):
    return AnalyticsQueryRequest(date_range=DateRange(start="2026-08-01T00:00:00Z", end="2026-08-26T00:00:00Z"), **kwargs)


def test_group_by_knowledge_id_is_accepted():
    req = _request(group_by=["knowledge_id"])
    assert req.group_by == ["knowledge_id"]


def test_group_by_knowledge_id_combines_with_other_fields():
    req = _request(group_by=["knowledge_id", "operation"])
    assert req.group_by == ["knowledge_id", "operation"]


def test_group_by_still_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        _request(group_by=["not_a_real_field"])


def test_filters_accept_knowledge_id():
    filters = AnalyticsFilters(knowledge_id="kb_123")
    assert filters.knowledge_id == "kb_123"


def test_filters_knowledge_id_defaults_to_none():
    assert AnalyticsFilters().knowledge_id is None
