"""Unit tests for case/whitespace-insensitive owner matching in
`utils/db/knowledge_ops.py::get_knowledge_services_by_owner`.

Why: every writer stores `owner_id` normalized (`create_knowledge` does
`.strip().lower()`), but rows written before that normalization existed kept the
caller's original casing. The listing query used an exact `==`, so those older
collections never came back from `GET /v1?owner_id=…` — while `/v1/validate`,
which compares `.strip().lower()` in Python, still said the same account owned
them. Reproduced in production 2026-08-29: the mixed-case spelling of one
account returned 1 collection where its lower-case spelling returned 14.

No DB: `async_session` is replaced with a fake whose `execute` captures the
statement, so the assertions are about the SQL that would be sent.
"""

import pytest
from sqlalchemy.dialects import postgresql

import utils.db.knowledge_ops as knowledge_ops


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Captures the statement instead of executing it."""

    def __init__(self, sink, rows):
        self._sink = sink
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement):
        self._sink.append(statement)
        return _FakeResult(self._rows)


@pytest.fixture
def captured(monkeypatch):
    statements = []
    monkeypatch.setattr(
        knowledge_ops, "async_session", lambda: _FakeSession(statements, ["k-1", "k-2"])
    )
    return statements


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.mark.asyncio
async def test_owner_column_and_input_are_both_normalized(captured):
    ids = await knowledge_ops.get_knowledge_services_by_owner("  Mixed.Case.Owner@Example.TEST ")
    assert ids == ["k-1", "k-2"]

    sql = _sql(captured[0])
    # The stored value is normalized in SQL (older rows may carry any casing)...
    assert "lower(trim(knowledge_config.owner_id))" in sql.lower()
    # ...and the caller's input is normalized before it is bound.
    assert "mixed.case.owner@example.test" in sql
    assert "Mixed" not in sql
    # Soft-deleted collections still stay out.
    assert "deleted" in sql.lower()


@pytest.mark.asyncio
async def test_case_variants_produce_the_same_query(captured):
    for variant in (
        "owner@example.com",
        "Owner@Example.com",
        "OWNER@EXAMPLE.COM",
        "  owner@example.com  ",
    ):
        await knowledge_ops.get_knowledge_services_by_owner(variant)

    rendered = {_sql(s) for s in captured}
    assert len(rendered) == 1, "case/whitespace variants must hit one identical query"


@pytest.mark.asyncio
async def test_empty_owner_id_is_passed_through_unchanged(captured):
    """No owner means no rows — don't raise on the `.strip()` of a falsy value."""
    assert await knowledge_ops.get_knowledge_services_by_owner("") == ["k-1", "k-2"]
    assert captured, "the query is still built; filtering is the DB's job"
