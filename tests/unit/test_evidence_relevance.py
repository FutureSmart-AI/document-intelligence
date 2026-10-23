"""Unit tests for chat/evidence_relevance.py (Phase 3c — see
claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests: OpenAIEmbeddings is monkeypatched with a fake that returns
deterministic vectors, no real network/API-key required.
"""

import asyncio

import chat.evidence_relevance as evidence_relevance


class _FakeEmbeddings:
    """Maps each input string to a hand-assigned vector via `vectors`."""

    def __init__(self, vectors: dict):
        self._vectors = vectors

    async def aembed_documents(self, texts):
        return [self._vectors[t] for t in texts]


def _patch_embeddings(monkeypatch, vectors):
    # evidence_relevance imports OpenAIEmbeddings locally inside the function
    # (`from langchain_openai import OpenAIEmbeddings`), so patching the
    # module-level attribute is a no-op — patch the source module instead.
    import langchain_openai
    def _factory(*args, **kwargs):
        return _FakeEmbeddings(vectors)
    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _factory)


def _patch_config(monkeypatch, enabled=True, threshold=0.30, embed_model="text-embedding-3-small"):
    monkeypatch.setattr(evidence_relevance, "_config", lambda: (enabled, threshold, embed_model))


def _patch_api_key(monkeypatch, key="sk-fake"):
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "OPENAI_API_KEY", key, raising=False)


# ---------------------------------------------------------------------------
# Gate disabled / no-op fast paths
# ---------------------------------------------------------------------------

def test_returns_groups_unchanged_when_disabled(monkeypatch):
    _patch_config(monkeypatch, enabled=False)
    groups = [{"query": "anything"}]

    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("new query", groups))

    assert result is groups


def test_returns_groups_unchanged_when_query_empty(monkeypatch):
    _patch_config(monkeypatch, enabled=True)
    groups = [{"query": "anything"}]

    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("   ", groups))

    assert result is groups


def test_returns_groups_unchanged_when_no_groups(monkeypatch):
    _patch_config(monkeypatch, enabled=True)

    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("query", []))

    assert result == []


# ---------------------------------------------------------------------------
# Threshold gating
# ---------------------------------------------------------------------------

def test_keeps_group_above_threshold_drops_group_below(monkeypatch):
    _patch_config(monkeypatch, enabled=True, threshold=0.5)
    _patch_api_key(monkeypatch)

    relevant_group = {"query": "tell me about invoices"}
    unrelated_group = {"query": "unrelated topic"}
    groups = [relevant_group, unrelated_group]

    vectors = {
        "current query": [1.0, 0.0],
        "tell me about invoices": [1.0, 0.0],      # cosine 1.0 with query -> kept
        "unrelated topic": [0.0, 1.0],              # cosine 0.0 with query -> dropped
    }
    _patch_embeddings(monkeypatch, vectors)

    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("current query", groups))

    assert result == [relevant_group]


def test_group_with_no_usable_text_is_kept_unconditionally(monkeypatch):
    _patch_config(monkeypatch, enabled=True, threshold=0.9)
    _patch_api_key(monkeypatch)
    # No "query"/"question_shape"/"metadata_filter"/"documents" fields at all.
    empty_group = {"source": "global_chunk_scan", "documents": []}
    _patch_embeddings(monkeypatch, {})

    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("query", [empty_group]))

    assert result == [empty_group]


# ---------------------------------------------------------------------------
# Fail-open behavior
# ---------------------------------------------------------------------------

def test_fails_open_when_no_api_key(monkeypatch):
    _patch_config(monkeypatch, enabled=True)
    _patch_api_key(monkeypatch, key=None)
    groups = [{"query": "anything"}]

    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("query", groups))

    assert result == groups


def test_fails_open_on_embedding_error(monkeypatch):
    _patch_config(monkeypatch, enabled=True)
    _patch_api_key(monkeypatch)

    def _broken_factory(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr(evidence_relevance, "OpenAIEmbeddings", _broken_factory, raising=False)

    groups = [{"query": "anything"}]
    result = asyncio.run(evidence_relevance.filter_evidence_by_relevance("query", groups))

    assert result == groups


# ---------------------------------------------------------------------------
# _cosine / _group_texts helpers
# ---------------------------------------------------------------------------

def test_cosine_orthogonal_vectors_is_zero():
    assert evidence_relevance._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_zero_vector_returns_zero():
    assert evidence_relevance._cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_group_texts_includes_query_label_and_doc_summaries():
    group = {
        "query": "invoices from March",
        "documents": [{"file_name": "march.pdf", "summary": "Invoice details"}],
    }
    texts = evidence_relevance._group_texts(group)
    assert texts[0] == "invoices from March"
    assert "march.pdf" in texts[1]
