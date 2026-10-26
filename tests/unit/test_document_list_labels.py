"""Unit tests for the two labels the v2 document listing now carries:
`source` (where a document came from) and `document_processing_mode` (how it was
asked to be read).

The gap these close: a ClickUp-synced page is uploaded through the ordinary
file_url path, so by `content_type` alone it is a "file" and looks exactly like a
manually uploaded PDF; and the processing mode was resolved in the router, sent to
the worker, and never stored, so nothing could report it afterwards.

Pure unit tests — `async_session` is replaced by a fake session that yields
hand-built rows instead of hitting a database, the same approach as
tests/unit/test_document_search.py.
"""

import asyncio
from datetime import datetime

import utils.db.knowledge_ops as knowledge_ops
from utils.models.enums import DocumentProcessingMode


class _Row:
    """The subset of a Document row that the v2 listing reads."""

    def __init__(self, object_id="obj-1", content_type="file", extra_metadata=None,
                 document_processing_mode=None, source_object_id=None,
                 processing_telemetry=None):
        self.id = 1
        self.object_id = object_id
        self.file_name = "report.pdf"
        self.processing_status = "completed"
        self.error_detail = None
        self.url = None
        self.source_url = None
        self.source_presigned_url = None
        self.source_presigned_url_expires_at = None
        self.created_at = datetime(2026, 8, 26, 9, 0, 0)
        self.updated_at = datetime(2026, 8, 26, 9, 0, 0)
        self.content_type = content_type
        self.extra_metadata = extra_metadata
        self.document_processing_mode = document_processing_mode
        self.source_object_id = source_object_id
        self.processing_telemetry = processing_telemetry
        self.task_duration_ms = None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, statement):
        return _FakeResult(self._rows)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None


def _list_documents(monkeypatch, rows):
    monkeypatch.setattr(knowledge_ops, "async_session", lambda: _FakeSession(rows))
    return asyncio.run(knowledge_ops.retrieve_chatbot_knowledge_v2(
        user_id=None, knowledge_id="kb-1",
    ))


# --------------------------------------------------------------- source label

def test_clickup_document_is_labelled_clickup_not_file(monkeypatch):
    """The bug this fixes: a ClickUp page is posted as a file_url, so its row's
    content_type is "file" and the listing was unable to say it came from ClickUp."""
    rows = [_Row(extra_metadata={"source": "clickup", "clickup_page_id": "p-1"})]

    documents = _list_documents(monkeypatch, rows)

    assert documents[0]["source"] == "clickup"
    # content_type is untouched — it is a filter parameter callers depend on.
    assert documents[0]["content_type"] == "file"


def test_everything_else_keeps_the_content_type_vocabulary(monkeypatch):
    """`clickup` is the only value this field adds. A document that did not come
    from a connector reports exactly what content_type already says — no renaming."""
    rows = [
        _Row(object_id="a", content_type="file"),
        _Row(object_id="b", content_type="url"),
        _Row(object_id="c", content_type="plain_text"),
    ]

    by_id = {d["object_id"]: d["source"] for d in _list_documents(monkeypatch, rows)}

    assert by_id == {"a": "file", "b": "url", "c": "plain_text"}


def test_missing_content_type_and_metadata_yields_null(monkeypatch):
    """No content_type and no connector stamp means nothing is known about the
    origin. Report that, rather than inventing a default."""
    rows = [_Row(content_type=None, extra_metadata=None)]

    assert _list_documents(monkeypatch, rows)[0]["source"] is None


def test_crawled_sub_pages_inherit_the_parent_source(monkeypatch):
    parent = _Row(object_id="parent", content_type="url")
    child = _Row(object_id="child", content_type="url", source_object_id="parent")

    documents = _list_documents(monkeypatch, [parent, child])

    assert documents[0]["source"] == "url"
    assert documents[0]["sub_urls"][0]["source"] == "url"


# ------------------------------------------------------- processing mode label

def test_processing_mode_is_returned(monkeypatch):
    rows = [_Row(document_processing_mode="scanned")]

    assert _list_documents(monkeypatch, rows)[0]["document_processing_mode"] == "scanned"


def test_processing_mode_is_null_for_rows_predating_the_column(monkeypatch):
    """Migration 042 backfills every existing row, so a null here means an insert
    path forgot to set it — worth being visible rather than defaulted away."""
    rows = [_Row(document_processing_mode=None)]

    assert _list_documents(monkeypatch, rows)[0]["document_processing_mode"] is None


# ------------------------------------------------------------ what gets stored

def test_insert_stores_the_enum_value_not_its_repr(monkeypatch):
    """str(DocumentProcessingMode.VISUAL) is "DocumentProcessingMode.VISUAL" — the
    column must get "visual"."""
    session = _FakeSession([])
    monkeypatch.setattr(knowledge_ops, "async_session", lambda: session)

    asyncio.run(knowledge_ops.insert_extracted_text(
        user_id="u", knowledge_id="kb-1", object_id="obj-1",
        content_type="file",
        document_processing_mode=DocumentProcessingMode.VISUAL,
    ))

    assert session.added[0].document_processing_mode == "visual"


def test_insert_accepts_a_plain_string_mode(monkeypatch):
    """Handlers annotate the mode as str and pass it straight through."""
    session = _FakeSession([])
    monkeypatch.setattr(knowledge_ops, "async_session", lambda: session)

    asyncio.run(knowledge_ops.insert_extracted_text(
        user_id="u", knowledge_id="kb-1", object_id="obj-1",
        content_type="file",
        document_processing_mode="layout_aware",
    ))

    assert session.added[0].document_processing_mode == "layout_aware"


def test_reindex_refreshes_the_stored_mode(monkeypatch):
    """Re-uploading a file as `scanned` genuinely changes how it is read, so the
    row must not stay at the original ingest's mode."""
    row = _Row(document_processing_mode="text_based")
    session = _FakeSession([row])

    class _SingleResult:
        def scalar_one_or_none(self_inner):
            return row

    async def _execute(statement):
        return _SingleResult()

    session.execute = _execute
    monkeypatch.setattr(knowledge_ops, "async_session", lambda: session)

    asyncio.run(knowledge_ops.mark_document_processing(
        knowledge_id="kb-1", object_id="obj-1",
        document_processing_mode=DocumentProcessingMode.SCANNED,
    ))

    assert row.document_processing_mode == "scanned"


def test_reindex_without_a_mode_leaves_the_stored_one_alone(monkeypatch):
    row = _Row(document_processing_mode="visual")
    session = _FakeSession([row])

    class _SingleResult:
        def scalar_one_or_none(self_inner):
            return row

    async def _execute(statement):
        return _SingleResult()

    session.execute = _execute
    monkeypatch.setattr(knowledge_ops, "async_session", lambda: session)

    asyncio.run(knowledge_ops.mark_document_processing(
        knowledge_id="kb-1", object_id="obj-1",
    ))

    assert row.document_processing_mode == "visual"


# --------------------------------------------------- processing telemetry

def test_processing_telemetry_is_priced_on_read(monkeypatch):
    """The worker stores raw measurements; the listing returns them priced with
    the backend price table (utils/processing_telemetry.py)."""
    raw = {"total_ms": 900, "parse": {"parser": "local_text", "mode": "text_based", "ms": 50},
           "operations": [{"operation": "extraction", "provider": "openai", "model": "gpt-5.4-nano",
                           "calls": 1, "input_tokens": 1_000_000, "output_tokens": 0, "latency_ms": 800}]}
    doc = _list_documents(monkeypatch, [_Row(processing_telemetry=raw)])[0]

    telemetry = doc["processing_telemetry"]
    assert telemetry["operations"][0]["estimated_cost_usd"] == 0.2
    assert telemetry["cost_complete"] is True
    assert telemetry["price_table"]


def test_processing_telemetry_is_null_for_unrecorded_documents(monkeypatch):
    assert _list_documents(monkeypatch, [_Row()])[0]["processing_telemetry"] is None
