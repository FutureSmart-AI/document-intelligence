"""Availability is evidence from successful attempts, not current schema inference."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import time
import unittest
import uuid

from sqlalchemy.dialects import postgresql

from utils.knowledge_health import extraction_context as context
from utils.models.retrieval import DataViewResponseV2


def event(**overrides):
    value = {
        "object_id": "old-A", "event_key": uuid.uuid4(),
        "created_at": datetime(2026, 10, 20, tzinfo=timezone.utc),
        "fields_configured": 3,
        "field_data": [
            {"field_name": "absent", "level": "document", "populated": False, "value_count": 0},
            {"field_name": "empty_array", "level": "document", "populated": False, "value_count": 0},
            {"field_name": "line_items", "level": "document", "populated": True, "value_count": 3},
        ],
    }
    return value | overrides


def test_records_asked_empty_fields_without_claiming_current_definition():
    result = context.context_from_event(event())
    assert result["status"] == "recorded"
    assert result["processed_fields"] == ["absent", "empty_array", "line_items"]
    assert "added_later" not in result["processed_fields"]
    assert result["definition_history"] == "unknown"
    # Reusing a name for a changed description/type is not historical evidence.
    assert context.context_from_event(event())["definition_history"] == "unknown"


def test_empty_partial_malformed_and_legacy_context_remain_unknown():
    for overrides in [
        {"field_data": None}, {"field_data": []}, {"fields_configured": 4},
        {"field_data": [{}], "fields_configured": 1},
        {"field_data": [{"field_name": "x", "level": "invalid"}], "fields_configured": 1},
        {"created_at": None}, {"event_key": None},
    ]:
        assert context.context_from_event(event(**overrides)) == context.unknown_context()


def test_query_selects_only_latest_success_with_collection_and_page_scope():
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value = [event()]
    session.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch.object(context, "async_session", factory):
        contexts = asyncio.run(context.get_extraction_context("kb-A", ["old-A", "failed-only", "legacy"]))
    query = session.execute.call_args.args[0]
    compiled = query.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "DISTINCT ON (knowledge_enrichment_events.object_id)" in sql
    assert "knowledge_enrichment_events.knowledge_id =" in sql
    assert "knowledge_enrichment_events.object_id IN" in sql
    assert "knowledge_enrichment_events.created_at DESC" in sql
    assert "knowledge_enrichment_events.id DESC" in sql
    assert "success" in compiled.params.values()
    assert "kb-A" in compiled.params.values()
    assert contexts["old-A"]["status"] == "recorded"
    assert contexts["failed-only"] == contexts["legacy"] == context.unknown_context()


def test_newer_success_without_context_does_not_resurrect_older_snapshot():
    session = AsyncMock()
    result = MagicMock()
    # DISTINCT ON supplies the latest success; its missing field_data wins.
    result.mappings.return_value = [event(field_data=[])]
    session.execute.return_value = result
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch.object(context, "async_session", factory):
        assert asyncio.run(context.get_extraction_context("kb", ["old-A"]))["old-A"]["status"] == "unknown"


def test_telemetry_failure_and_no_rows_do_not_fail_metadata():
    factory = MagicMock(side_effect=RuntimeError("unavailable"))
    with patch.object(context, "async_session", factory):
        assert asyncio.run(context.get_extraction_context("kb", ["old-A"])) == {"old-A": context.unknown_context()}
        assert asyncio.run(context.get_extraction_context("kb", [])) == {}


def test_slow_history_read_times_out_to_unknown():
    async def slow_read(_query):
        await asyncio.sleep(10)
    session = AsyncMock()
    session.execute.side_effect = slow_read
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    start = time.monotonic()
    with patch.object(context, "async_session", factory):
        assert asyncio.run(context.get_extraction_context("kb", ["old-A"]))["old-A"]["status"] == "unknown"
    assert 1.9 <= time.monotonic() - start < 4


def test_additive_response_keeps_null_empty_arrays_and_numeric_nested_cells():
    rows = [{"object_id": "old-A", "absent": None, "empty_array": [], "quantity": 2,
             "total_due": 1246.8, "zero": 0, "false": False, "remaining_quantity": [1]}]
    response = DataViewResponseV2(columns=[], rows=rows, total_count=1, current_count=1,
                                 current_page=1, total_pages=1,
                                 extraction_context={"old-A": context.context_from_event(event())})
    assert response.model_dump()["rows"] == rows


def load_tests(_loader, _tests, _pattern):
    """Run these function checks with either pytest or the runtime's unittest."""
    return unittest.TestSuite(
        unittest.FunctionTestCase(check)
        for name, check in globals().items() if name.startswith("test_")
    )
