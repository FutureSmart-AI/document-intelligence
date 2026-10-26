"""Unit tests for utils/reports/schedule_utils.py — pure schedule math,
ported from fs-agent-dashboard (docs/plan/batch-query-reports/schema.md).
"""
from datetime import datetime, timedelta

from utils.reports.schedule_utils import compute_next_run_at


def test_no_schedule_returns_none():
    assert compute_next_run_at(None) is None


def test_daily():
    base = datetime(2026, 8, 19, 10, 0, 0)
    result = compute_next_run_at({"frequency": "daily"}, base=base)
    assert result == base + timedelta(days=1)


def test_weekly():
    base = datetime(2026, 8, 19, 10, 0, 0)
    result = compute_next_run_at({"frequency": "weekly"}, base=base)
    assert result == base + timedelta(weeks=1)


def test_monthly():
    base = datetime(2026, 1, 31, 10, 0, 0)
    result = compute_next_run_at({"frequency": "monthly"}, base=base)
    # relativedelta(months=1) rolls Jan 31 -> Feb 28 (2026 not a leap year)
    assert result.month == 2
    assert result.year == 2026


def test_cron_without_expression_returns_none():
    assert compute_next_run_at({"frequency": "cron"}) is None


def test_cron_next_occurrence():
    base = datetime(2026, 8, 19, 10, 0, 0)
    # "every 5 minutes"
    result = compute_next_run_at({"frequency": "cron", "cron": "*/5 * * * *", "timezone": "UTC"}, base=base)
    assert result is not None
    assert result > base
    assert (result - base) <= timedelta(minutes=5)


def test_unknown_frequency_returns_none():
    assert compute_next_run_at({"frequency": "yearly"}) is None
