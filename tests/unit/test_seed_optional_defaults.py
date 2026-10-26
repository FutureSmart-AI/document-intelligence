"""Unit tests for initialize_field_configurations_by_template's
seed_optional_defaults gate (utils/db/knowledge_setting_ops.py).

Added 2026-09-10: PUT /v1/{knowledge_id}/config used to force-create all 9
document-level DEFAULT_FIELD_CONFIGURATIONS (title, summary, document_type +
6 optional ones) on every call, template or not — live-verified to leave a
resume-template collection with 22 fields when the UI only showed 13. The
fix is seed_optional_defaults=False: only the still-protected document-level
defaults (summary, document_type) get force-created; everything else is left
for Step 4 to create explicitly, from the user's own Step 1 answers.

No real DB: async_session is replaced with a fake whose execute() reports no
existing rows and whose session.add() is captured, so the assertions are
about which field names would be created.
"""

import pytest

import utils.db.knowledge_setting_ops as setting_ops
from utils.config.enrichment_config import PROTECTED_SYSTEM_FIELDS


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)


class _FakeSession:
    def __init__(self):
        self.added = []

    async def execute(self, _statement):
        return _FakeResult([])  # nothing pre-exists

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fake_session(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(setting_ops, "async_session", lambda: _FakeSessionCtx(session))
    return session


@pytest.mark.asyncio
async def test_seed_optional_defaults_false_creates_only_protected_document_fields(fake_session):
    await setting_ops.initialize_field_configurations_by_template(
        "kb-1", template=None, document_level_metadata=True,
        chunk_level_metadata=False, seed_optional_defaults=False,
    )
    created_names = {f.field_name for f in fake_session.added}
    assert created_names == {"summary", "document_type"}
    assert "title" not in created_names
    assert "topics" not in created_names


@pytest.mark.asyncio
async def test_seed_optional_defaults_true_creates_every_document_default(fake_session):
    await setting_ops.initialize_field_configurations_by_template(
        "kb-1", template=None, document_level_metadata=True,
        chunk_level_metadata=False, seed_optional_defaults=True,
    )
    created_names = {f.field_name for f in fake_session.added}
    # 9 document-level DEFAULT_FIELD_CONFIGURATIONS entries, protected or not
    assert created_names == {
        "title", "summary", "document_type", "topics", "keywords",
        "entities", "organizations", "temporal_context", "document_outline",
    }


@pytest.mark.asyncio
async def test_seed_optional_defaults_false_still_seeds_a_picked_template(fake_session):
    """A field_template is an explicit user choice — seed_optional_defaults
    must never suppress it, only the universal (non-template) defaults."""
    await setting_ops.initialize_field_configurations_by_template(
        "kb-1", template="resume", document_level_metadata=True,
        chunk_level_metadata=False, seed_optional_defaults=False,
    )
    created_names = {f.field_name for f in fake_session.added}
    assert {"summary", "document_type"} <= created_names
    # candidate_name is one of the resume template's own fields
    assert "candidate_name" in created_names
    assert "title" not in created_names
    assert "topics" not in created_names


@pytest.mark.asyncio
async def test_seed_optional_defaults_false_chunk_level_untouched(fake_session):
    """Scope decision 2026-09-10: this gate only affects document-level
    defaults. Chunk-level optional defaults stay governed solely by
    chunk_level_metadata, unchanged by seed_optional_defaults."""
    await setting_ops.initialize_field_configurations_by_template(
        "kb-1", template=None, document_level_metadata=True,
        chunk_level_metadata=True, seed_optional_defaults=False,
    )
    created_names = {f.field_name for f in fake_session.added}
    assert {"chunk_idx", "chunk_text", "chunk_keywords", "chunk_topic", "chunk_entities"} <= created_names


def test_protected_system_fields_is_exactly_four():
    assert PROTECTED_SYSTEM_FIELDS == {"summary", "document_type", "chunk_idx", "chunk_text"}
