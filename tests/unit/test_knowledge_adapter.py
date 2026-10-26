"""Unit tests for chat/knowledge_adapter.py — the in-process replacement for
the Orchestrator's HTTP Knowledge API client (see claude-logs/plan/
knowledge-owned-chat.md, Phase 1).

Pure unit tests: the Qdrant/DB layer is monkeypatched with fakes, matching
the approach in tests/unit/test_document_search.py. Focus is on the logic
the adapter itself adds on top of pass-through calls — the cross-tenant
guard on chunk-by-id retrieval, search_chunks' mode dispatch, and
get_metadata_values' is_complete heuristic — not on re-testing
retrieval_utils/retrieval_ops, which have their own coverage.
"""

import asyncio
from types import SimpleNamespace

import chat.knowledge_adapter as adapter


def _point(point_id, payload):
    return SimpleNamespace(id=point_id, payload=payload)


class _FakeClientAsync:
    def __init__(self, points_by_id):
        self._points_by_id = points_by_id

    async def retrieve(self, collection_name, ids, with_payload=True):
        return [self._points_by_id[i] for i in ids if i in self._points_by_id]


class _FakeChunkIndexer:
    def __init__(self, points_by_id=None, hybrid_result=None, semantic_result=None, all_chunks=None):
        self.client_async = _FakeClientAsync(points_by_id or {})
        self.collection_name = "fake-chunks"
        self._hybrid_result = hybrid_result if hybrid_result is not None else []
        self._semantic_result = semantic_result if semantic_result is not None else []
        self._all_chunks = all_chunks if all_chunks is not None else []
        self.hybrid_calls = []
        self.semantic_calls = []

    async def hybrid_chunk_search(self, object_ids, query, knowledge_id, user_id=None, limit=3, *, allow_scope_expansion=False):
        self.hybrid_calls.append({"object_ids": object_ids, "query": query, "knowledge_id": knowledge_id, "limit": limit})
        return self._hybrid_result

    async def search_chunks_semantic_only(self, query, knowledge_id, user_id=None, object_ids=None, limit=3, *, allow_scope_expansion=False):
        self.semantic_calls.append({"object_ids": object_ids, "query": query, "knowledge_id": knowledge_id, "limit": limit})
        return self._semantic_result

    async def get_all_chunks_for_document(self, object_id, knowledge_id=None):
        return self._all_chunks


def _install_fake_indexer(monkeypatch, indexer):
    async def _fake_get_hybrid_search_value(knowledge_id):
        return (True, "fake-model")

    async def _fake_get_chunk_indexer(embedding_model):
        return indexer

    monkeypatch.setattr(adapter, "get_hybrid_search_value", _fake_get_hybrid_search_value)
    monkeypatch.setattr(adapter, "_get_chunk_indexer", _fake_get_chunk_indexer)


# ---------------------------------------------------------------------------
# search_chunks — mode dispatch
# ---------------------------------------------------------------------------

def test_search_chunks_defaults_to_hybrid(monkeypatch):
    indexer = _FakeChunkIndexer(hybrid_result=[{"id": "c1"}])
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.search_chunks("kb-1", query="hello", num_chunks=5))

    assert result == [{"id": "c1"}]
    assert len(indexer.hybrid_calls) == 1
    assert indexer.hybrid_calls[0]["knowledge_id"] == "kb-1"
    assert not indexer.semantic_calls


def test_search_chunks_non_hybrid_mode_uses_semantic_only(monkeypatch):
    indexer = _FakeChunkIndexer(semantic_result=[{"id": "c2"}])
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.search_chunks("kb-1", query="hello", mode="non_hybrid"))

    assert result == [{"id": "c2"}]
    assert len(indexer.semantic_calls) == 1
    assert not indexer.hybrid_calls


def test_search_chunks_without_query_requires_object_ids(monkeypatch):
    indexer = _FakeChunkIndexer()
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.search_chunks("kb-1", query=None, object_ids=None))

    assert result == []


def test_search_chunks_without_query_returns_document_chunks(monkeypatch):
    indexer = _FakeChunkIndexer(all_chunks=[{"id": "c1"}, {"id": "c2"}, {"id": "c3"}])
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.search_chunks("kb-1", query=None, object_ids=["doc-1"], num_chunks=2))

    assert result == [{"id": "c1"}, {"id": "c2"}]


def test_search_chunks_swallows_indexer_init_failure(monkeypatch):
    async def _boom(knowledge_id):
        raise RuntimeError("no embedding model")

    monkeypatch.setattr(adapter, "get_hybrid_search_value", _boom)

    result = asyncio.run(adapter.search_chunks("kb-1", query="hello"))

    assert result == []


# ---------------------------------------------------------------------------
# Cross-tenant guard — get_chunk_by_id / get_chunks_batch / get_chunk_metadata_by_id
# ---------------------------------------------------------------------------

def test_get_chunk_by_id_returns_chunk_for_matching_tenant(monkeypatch):
    indexer = _FakeChunkIndexer(points_by_id={
        "c1": _point("c1", {"object_id": "doc-1", "knowledge_id": "kb-1", "chunk_text": "hello"}),
    })
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.get_chunk_by_id("kb-1", "c1"))

    assert result["id"] == "c1"
    assert result["text"] == "hello"


def test_get_chunk_by_id_rejects_foreign_tenant_chunk(monkeypatch):
    indexer = _FakeChunkIndexer(points_by_id={
        "c1": _point("c1", {"object_id": "doc-1", "knowledge_id": "kb-1", "chunk_text": "hello"}),
    })
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.get_chunk_by_id("kb-OTHER", "c1"))

    assert result == {}


def test_get_chunk_by_id_missing_chunk_returns_empty(monkeypatch):
    indexer = _FakeChunkIndexer(points_by_id={})
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.get_chunk_by_id("kb-1", "does-not-exist"))

    assert result == {}


def test_get_chunks_batch_filters_out_foreign_tenant_chunks(monkeypatch):
    async def _fake_get_chunks_by_ids_wrapped(chunk_ids, knowledge_id):
        return [
            {"id": "c1", "object_id": "doc-1", "text": "mine", "metadata": {"knowledge_id": "kb-1"}},
            {"id": "c2", "object_id": "doc-2", "text": "foreign", "metadata": {"knowledge_id": "kb-OTHER"}},
        ]

    monkeypatch.setattr(adapter, "get_chunks_by_ids_wrapped", _fake_get_chunks_by_ids_wrapped)

    result = asyncio.run(adapter.get_chunks_batch("kb-1", ["c1", "c2"]))

    assert [c["id"] for c in result] == ["c1"]


def test_get_chunk_metadata_by_id_rejects_foreign_tenant_chunk(monkeypatch):
    indexer = _FakeChunkIndexer(points_by_id={
        "c1": _point("c1", {"object_id": "doc-1", "knowledge_id": "kb-1", "chunk_keywords": ["a"]}),
    })
    _install_fake_indexer(monkeypatch, indexer)

    result = asyncio.run(adapter.get_chunk_metadata_by_id("kb-OTHER", "c1"))

    assert result == {}


def test_get_chunk_metadata_by_id_filters_to_chunk_level_active_fields(monkeypatch):
    indexer = _FakeChunkIndexer(points_by_id={
        "c1": _point("c1", {
            "object_id": "doc-1",
            "knowledge_id": "kb-1",
            "chunk_keywords": ["a", "b"],
            "chunk_topic": "topic",
            "chunk_text": "should be excluded",
        }),
    })
    _install_fake_indexer(monkeypatch, indexer)

    async def _fake_field_configs(knowledge_id):
        return [
            {"field_name": "chunk_keywords", "level": "chunk", "is_active": True},
            {"field_name": "chunk_topic", "level": "chunk", "is_active": False},
            {"field_name": "summary", "level": "document", "is_active": True},
        ]

    monkeypatch.setattr(adapter, "get_field_configurations_sqlmodel_v2", _fake_field_configs)

    result = asyncio.run(adapter.get_chunk_metadata_by_id("kb-1", "c1"))

    assert result["metadata"] == {"chunk_keywords": ["a", "b"]}
    assert "chunk_text" not in result["metadata"]


# ---------------------------------------------------------------------------
# get_metadata_values — is_complete heuristic
# ---------------------------------------------------------------------------

def test_get_metadata_values_is_complete_when_under_limit(monkeypatch):
    async def _fake_get_values(knowledge_id, field_name, filters=None, limit=100):
        return SimpleNamespace(values=["a", "b"], field_type="str")

    monkeypatch.setattr(adapter, "get_metadata_values_for_field", _fake_get_values)

    result = asyncio.run(adapter.get_metadata_values("kb-1", "document_type", limit=5))

    assert result == {"values": ["a", "b"], "field_type": "str", "is_complete": True}


def test_get_metadata_values_is_incomplete_when_at_limit(monkeypatch):
    async def _fake_get_values(knowledge_id, field_name, filters=None, limit=100):
        return SimpleNamespace(values=["a", "b", "c"], field_type="str")

    monkeypatch.setattr(adapter, "get_metadata_values_for_field", _fake_get_values)

    result = asyncio.run(adapter.get_metadata_values("kb-1", "document_type", limit=3))

    assert result["is_complete"] is False


def test_get_metadata_values_falls_back_to_user_defined_on_value_error(monkeypatch):
    async def _fake_get_values(knowledge_id, field_name, filters=None, limit=100):
        raise ValueError("not a configured field")

    async def _fake_user_defined(knowledge_id, limit, filters=None, field_name=None):
        return SimpleNamespace(fields=[
            SimpleNamespace(field_name="custom_field", values=["x"], field_type="str"),
        ])

    monkeypatch.setattr(adapter, "get_metadata_values_for_field", _fake_get_values)
    monkeypatch.setattr(adapter, "get_user_defined_metadata_from_qdrant_object_ids", _fake_user_defined)

    result = asyncio.run(adapter.get_metadata_values("kb-1", "custom_field", limit=10))

    assert result == {"values": ["x"], "field_type": "str", "is_complete": True}


def test_get_metadata_values_unknown_field_returns_empty(monkeypatch):
    async def _fake_get_values(knowledge_id, field_name, filters=None, limit=100):
        raise ValueError("not found")

    async def _fake_user_defined(knowledge_id, limit, filters=None, field_name=None):
        return SimpleNamespace(fields=[])

    monkeypatch.setattr(adapter, "get_metadata_values_for_field", _fake_get_values)
    monkeypatch.setattr(adapter, "get_user_defined_metadata_from_qdrant_object_ids", _fake_user_defined)

    result = asyncio.run(adapter.get_metadata_values("kb-1", "nonexistent", limit=10))

    assert result == {"values": [], "field_type": None, "is_complete": True}
