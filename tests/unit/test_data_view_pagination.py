"""Regression test for the v2 data-view export pagination bug.

get_data_view_tabular_v2's "no sort" branch used to call Qdrant's scroll() with
offset=<page * page_size> directly. Qdrant's scroll `offset` is a point-ID cursor,
not a skip-count, so passing an arbitrary computed integer never advanced past
page 1 in practice — every page returned the same leading batch, which is why a
page-walking export ended up with duplicated rows (e.g. 800 rows for 764 docs).

The fix routes every call (sorted or not) through _fetch_documents_with_offset_pagination,
which paginates via Qdrant's own opaque cursor and slices the result in Python with
real offset/limit semantics. This test locks that in: get_data_view_tabular_v2 must
never call indexer.client_async.scroll() directly to satisfy pagination.
"""

import asyncio

import utils.files.retrieval_utils as retrieval_utils


class _FakeCountResult:
    def __init__(self, count):
        self.count = count


class _FakeIndexer:
    """Records any direct scroll() calls so we can assert they never happen for
    pagination purposes (count() is fine, it's a separate code path)."""

    def __init__(self):
        self.collection_name = "fake_collection"
        self.direct_scroll_calls = []
        self.client_async = self

    async def count(self, **kwargs):
        return _FakeCountResult(count=4)

    async def scroll(self, **kwargs):
        # get_data_view_tabular_v2 also scrolls directly to sample extra_metadata keys
        # (no offset involved) — that's fine. What must never happen is a direct scroll
        # call carrying a non-None `offset`, since that's the point-ID-cursor-abused-as-
        # skip-count bug this test guards against.
        if kwargs.get("offset") is not None:
            self.direct_scroll_calls.append(kwargs)
        return [], None


def _install_fakes(monkeypatch, fake_indexer, pagination_calls):
    async def _fake_metadata_enabled(knowledge_id):
        return True

    async def _fake_hybrid_search_value(knowledge_id):
        return False, "fake-embedding-model"

    async def _fake_get_document_indexer(embedding_model):
        return fake_indexer

    async def _fake_field_configs(knowledge_id):
        return []

    async def _fake_offset_pagination(indexer, filter_condition, sort_configs, limit, page_offset):
        pagination_calls.append({
            "sort_configs": sort_configs,
            "limit": limit,
            "page_offset": page_offset,
        })
        return [], False, None

    monkeypatch.setattr(retrieval_utils, "get_document_metadata_enabled", _fake_metadata_enabled)
    monkeypatch.setattr(retrieval_utils, "get_hybrid_search_value", _fake_hybrid_search_value)
    monkeypatch.setattr(retrieval_utils, "_get_document_indexer", _fake_get_document_indexer)
    monkeypatch.setattr(retrieval_utils, "_fetch_documents_with_offset_pagination", _fake_offset_pagination)

    import utils.db.enrichment_db_utils as enrichment_db_utils
    monkeypatch.setattr(enrichment_db_utils, "get_field_configurations_sqlmodel_v2", _fake_field_configs)


def test_no_sort_page_2_routes_through_offset_pagination_not_raw_scroll(monkeypatch):
    fake_indexer = _FakeIndexer()
    pagination_calls = []
    _install_fakes(monkeypatch, fake_indexer, pagination_calls)

    asyncio.run(retrieval_utils.get_data_view_tabular_v2(
        knowledge_id="kb-1",
        page=2,
        page_size=2,
        sort=None,
    ))

    assert fake_indexer.direct_scroll_calls == [], (
        "get_data_view_tabular_v2 must not scroll() directly with a computed "
        "page offset — Qdrant's offset is a point-ID cursor, not a skip-count."
    )
    assert len(pagination_calls) == 1
    call = pagination_calls[0]
    assert call["sort_configs"] == [{"field": "created_at", "order": "desc"}]
    assert call["limit"] == 2
    assert call["page_offset"] == '{"offset": 2}'


def test_page_1_has_no_offset_cursor(monkeypatch):
    fake_indexer = _FakeIndexer()
    pagination_calls = []
    _install_fakes(monkeypatch, fake_indexer, pagination_calls)

    asyncio.run(retrieval_utils.get_data_view_tabular_v2(
        knowledge_id="kb-1",
        page=1,
        page_size=2,
        sort=None,
    ))

    assert fake_indexer.direct_scroll_calls == []
    assert pagination_calls[0]["page_offset"] is None
