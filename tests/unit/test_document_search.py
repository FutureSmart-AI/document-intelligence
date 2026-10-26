"""Unit tests for file_name / object_id search on retrieve_chatbot_knowledge_v2
(utils/db/knowledge_ops.py), backing the Upload Status window's search feature
(product backlog).

Pure unit tests: async_session is replaced with a fake session that captures
the compiled SQL statement instead of hitting a real database, matching the
approach in tests/unit/test_audit_logging.py.
"""

import asyncio

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
    def __init__(self, captured):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, statement):
        self._captured.append(statement)
        return _FakeResult([])


def _install_fake_async_session(monkeypatch):
    """Replace knowledge_ops.async_session with a factory returning a fake
    session, and return the list that captured statements are appended to."""
    captured = []
    monkeypatch.setattr(knowledge_ops, "async_session", lambda: _FakeSession(captured))
    return captured


def _compiled_where(statement) -> str:
    # Compile against the postgresql dialect (what the app actually runs on via
    # asyncpg) so ilike() renders as a real ILIKE instead of the generic
    # dialect's lower(x) LIKE lower(y) fallback.
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_no_filters_omits_ilike_clauses(monkeypatch):
    captured = _install_fake_async_session(monkeypatch)

    asyncio.run(knowledge_ops.retrieve_chatbot_knowledge_v2(
        user_id=None,
        knowledge_id="kb-1",
    ))

    assert len(captured) == 1
    assert "ILIKE" not in _compiled_where(captured[0])


def test_file_name_filter_adds_case_insensitive_partial_match(monkeypatch):
    captured = _install_fake_async_session(monkeypatch)

    asyncio.run(knowledge_ops.retrieve_chatbot_knowledge_v2(
        user_id=None,
        knowledge_id="kb-1",
        file_name="invoice",
    ))

    compiled = _compiled_where(captured[0])
    assert "document.file_name ILIKE '%%invoice%%'" in compiled
    assert "object_id ILIKE" not in compiled


def test_object_id_filter_adds_case_insensitive_partial_match(monkeypatch):
    captured = _install_fake_async_session(monkeypatch)

    asyncio.run(knowledge_ops.retrieve_chatbot_knowledge_v2(
        user_id=None,
        knowledge_id="kb-1",
        object_id="obj-123",
    ))

    compiled = _compiled_where(captured[0])
    assert "document.object_id ILIKE '%%obj-123%%'" in compiled
    assert "file_name ILIKE" not in compiled


def test_file_name_and_object_id_filters_combine(monkeypatch):
    captured = _install_fake_async_session(monkeypatch)

    asyncio.run(knowledge_ops.retrieve_chatbot_knowledge_v2(
        user_id=None,
        knowledge_id="kb-1",
        content_type="file",
        file_name="invoice",
        object_id="obj-123",
    ))

    compiled = _compiled_where(captured[0])
    assert "document.file_name ILIKE '%%invoice%%'" in compiled
    assert "document.object_id ILIKE '%%obj-123%%'" in compiled
    assert "document.content_type = 'file'" in compiled


def test_blank_filters_are_ignored(monkeypatch):
    """Empty-string query params (e.g. `?file_name=&object_id=`) must not
    turn into a `ILIKE '%%'` clause that matches every document."""
    captured = _install_fake_async_session(monkeypatch)

    asyncio.run(knowledge_ops.retrieve_chatbot_knowledge_v2(
        user_id=None,
        knowledge_id="kb-1",
        file_name="",
        object_id="",
    ))

    assert "ILIKE" not in _compiled_where(captured[0])
