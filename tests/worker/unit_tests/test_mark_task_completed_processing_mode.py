"""mark_task_completed grew an `effective_mode` parameter so the mode that actually ran
a document (post any auto-shift — see test_pdf_auto_shift_modes.py) gets written back
to the shared `document` table's `document_processing_mode` column. That column
already exists in the real DB (fs-agent-knowledge migration
042_add_document_processing_mode.py); this repo's own SQLModel definition
(services/pydantic_models_db.py) was just missing the field.

The rule under test:
- Given an effective_mode, it is written onto the document row alongside the existing
  completed-task fields.
- Omitting effective_mode (the default) leaves the column untouched — a caller that
  doesn't have this value yet must never blank out a value written by a previous run.

Pure unit tests: get_session and _get_document are monkeypatched to a fake in-memory
session/document, so no real database connection is made.
"""

import time

import celery_utils.db.db_utils as db_utils


class _FakeDocument:
    """Stands in for the SQLModel Document row mark_task_completed mutates."""

    def __init__(self, document_processing_mode=None):
        self.processing_status = None
        self.task_duration_ms = None
        self.completed_at = None
        self.document_processing_mode = document_processing_mode


class _FakeSession:
    """A no-op context-manager session — commit() just records that it happened."""

    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _patch_session_and_document(monkeypatch, document):
    fake_session = _FakeSession()
    monkeypatch.setattr(db_utils, "get_session", lambda: fake_session)
    monkeypatch.setattr(db_utils, "_get_document", lambda session, knowledge_id, object_id: document)
    return fake_session


def test_writes_effective_mode_onto_the_document_row(monkeypatch):
    document = _FakeDocument()
    fake_session = _patch_session_and_document(monkeypatch, document)

    duration_ms = db_utils.mark_task_completed(
        "kid", "oid", time.time(), effective_mode="layout_aware"
    )

    assert document.document_processing_mode == "layout_aware"
    assert document.processing_status == "completed"
    assert fake_session.committed
    assert isinstance(duration_ms, int)


def test_omitting_effective_mode_leaves_the_column_untouched(monkeypatch):
    # A previous run already recorded a mode; a caller that doesn't pass
    # effective_mode (e.g. an older call site) must not blank it out.
    document = _FakeDocument(document_processing_mode="scanned")
    _patch_session_and_document(monkeypatch, document)

    db_utils.mark_task_completed("kid", "oid", time.time())

    assert document.document_processing_mode == "scanned"
    assert document.processing_status == "completed"


def test_omitting_effective_mode_on_a_fresh_row_leaves_it_none(monkeypatch):
    document = _FakeDocument()  # None, as create_document_record leaves it by default
    _patch_session_and_document(monkeypatch, document)

    db_utils.mark_task_completed("kid", "oid", time.time())

    assert document.document_processing_mode is None


def test_document_not_found_returns_zero_without_touching_effective_mode(monkeypatch):
    monkeypatch.setattr(db_utils, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(db_utils, "_get_document", lambda session, knowledge_id, object_id: None)

    result = db_utils.mark_task_completed("kid", "oid", time.time(), effective_mode="visual")

    assert result == 0
