"""Unit tests for chat/evidence_builder.py::build_grouped_evidence (Phase 5
— see claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests: get_document_details (the only I/O dependency, used for
global-chunk-scan topic enrichment) is monkeypatched; no real network/DB.
"""

import asyncio

import chat.evidence_builder as evidence_builder


def _tool_response(tool_name, result):
    return {"tool_name": tool_name, "tool_call_id": "call-1", "result": result}


# ---------------------------------------------------------------------------
# retrieve_documents groups
# ---------------------------------------------------------------------------

def test_builds_one_group_per_retrieve_documents_call():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "query": "invoices",
                "applied_filter": {"document_type": "invoice"},
                "retrieval_mode": "hybrid_query",
                "documents": [
                    {"object_id": "doc-1", "summary": "Invoice from March", "file_name": "march.pdf"},
                ],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert len(groups) == 1
    group = groups[0]
    assert group["tool"] == "retrieve_documents"
    assert group["query"] == "invoices"
    assert group["metadata_filter"] == {"document_type": "invoice"}
    assert group["retrieval_mode"] == "hybrid_query"
    assert group["documents"] == [{"object_id": "doc-1", "summary": "Invoice from March", "file_name": "march.pdf"}]


def test_skips_docs_missing_object_id():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [{"summary": "no object_id here"}],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert groups == []


def test_truncates_long_summary():
    long_summary = "x" * 500
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [{"object_id": "doc-1", "summary": long_summary}],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    stored_summary = groups[0]["documents"][0]["summary"]
    assert len(stored_summary) <= evidence_builder._EVIDENCE_SUMMARY_CHAR_CAP + 1
    assert stored_summary.endswith("…")


def test_strips_summary_key_out_of_persisted_metadata():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [{
                    "object_id": "doc-1",
                    "summary": "short summary",
                    "metadata": {"summary": "dup", "author": "Alice", "category": "finance"},
                }],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    metadata = groups[0]["documents"][0]["metadata"]
    assert metadata == {"author": "Alice", "category": "finance"}


def test_error_artifact_produces_no_group():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {"error": "retrieval failed", "documents": []}),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert groups == []


def test_empty_documents_list_produces_no_group():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {"documents": []}),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert groups == []


# ---------------------------------------------------------------------------
# Citation-curated persistence
# ---------------------------------------------------------------------------

def test_citation_filter_keeps_only_cited_docs():
    rag_context = {
        "agent_output": {"relevant_docs": [{"doc_id": "doc-1"}]},
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [
                    {"object_id": "doc-1", "summary": "cited"},
                    {"object_id": "doc-2", "summary": "not cited"},
                ],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert len(groups) == 1
    object_ids = [d["object_id"] for d in groups[0]["documents"]]
    assert object_ids == ["doc-1"]


def test_citation_filter_drops_group_entirely_when_no_docs_survive():
    rag_context = {
        "agent_output": {"relevant_docs": [{"doc_id": "doc-999"}]},
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [{"object_id": "doc-1", "summary": "not cited"}],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert groups == []


def test_citation_filter_skipped_for_exhaustive_groups():
    """total_match_count present -> the doc set IS the answer, completeness
    must survive even if the agent only explicitly cited a subset."""
    rag_context = {
        "agent_output": {"relevant_docs": [{"doc_id": "doc-1"}]},
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [
                    {"object_id": "doc-1", "summary": "cited"},
                    {"object_id": "doc-2", "summary": "not cited but part of exhaustive set"},
                ],
                "total_match_count": 2,
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    object_ids = {d["object_id"] for d in groups[0]["documents"]}
    assert object_ids == {"doc-1", "doc-2"}


def test_complete_population_marked_when_exhaustive_and_untruncated():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [{"object_id": "doc-1", "summary": "s"}],
                "total_match_count": 1,
                "total_is_lower_bound": False,
                "truncated": False,
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert groups[0]["complete_population"] == 1


def test_complete_population_absent_when_truncated():
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_documents", {
                "documents": [{"object_id": "doc-1", "summary": "s"}],
                "total_match_count": 5,
                "truncated": True,
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context))

    assert "complete_population" not in groups[0]


# ---------------------------------------------------------------------------
# global chunk scan groups
# ---------------------------------------------------------------------------

def test_global_chunk_scan_builds_group_without_summary_key(monkeypatch):
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_chunks", {
                "global_chunk_scan": True,
                "query": "deployment process",
                "chunks": [
                    {"object_id": "doc-1", "chunk_id": "c1", "relevance_score": 0.9, "chunk_topic": "ECR setup"},
                    {"object_id": "doc-1", "chunk_id": "c2", "relevance_score": 0.7},
                ],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context, knowledge_id="kb-1"))

    assert len(groups) == 1
    group = groups[0]
    assert group["source"] == "global_chunk_scan"
    assert "summary" not in group["documents"][0]
    doc = group["documents"][0]
    assert doc["object_id"] == "doc-1"
    assert set(doc["chunk_ids"]) == {"c1", "c2"}
    assert doc["score"] == 0.9  # best of 0.9/0.7
    assert doc["chunk_topic"] == "ECR setup"


def test_global_chunk_scan_enriches_missing_topic_via_get_document_details(monkeypatch):
    async def _fake_get_document_details(knowledge_id, object_ids):
        return [{"object_id": "doc-1", "title": "Deployment Runbook", "metadata": {"category": "infra"}}]

    import chat.knowledge_adapter as knowledge_adapter
    monkeypatch.setattr(knowledge_adapter, "get_document_details", _fake_get_document_details)

    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_chunks", {
                "global_chunk_scan": True,
                "chunks": [{"object_id": "doc-1", "chunk_id": "c1", "relevance_score": 0.5}],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context, knowledge_id="kb-1"))

    doc = groups[0]["documents"][0]
    assert doc["page_title"] == "Deployment Runbook"
    assert doc["category"] == "infra"


def test_global_chunk_scan_degrades_on_enrichment_timeout(monkeypatch):
    async def _slow_get_document_details(knowledge_id, object_ids):
        await asyncio.sleep(10)
        return []

    import chat.knowledge_adapter as knowledge_adapter
    monkeypatch.setattr(knowledge_adapter, "get_document_details", _slow_get_document_details)
    monkeypatch.setattr(evidence_builder, "_ENRICH_TIMEOUT_S", 0.01)

    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_chunks", {
                "global_chunk_scan": True,
                "chunks": [{"object_id": "doc-1", "chunk_id": "c1", "relevance_score": 0.5}],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context, knowledge_id="kb-1"))

    doc = groups[0]["documents"][0]
    assert doc["object_id"] == "doc-1"
    assert "page_title" not in doc  # degraded to bare entry


def test_scoped_chunk_call_not_persisted_as_global_scan():
    """object_ids present (a scoped call, not global_chunk_scan) must not
    produce a group — the agent already had those ids."""
    rag_context = {
        "tool_responses": [
            _tool_response("retrieve_chunks", {
                "global_chunk_scan": False,
                "chunks": [{"object_id": "doc-1", "chunk_id": "c1"}],
            }),
        ],
    }

    groups = asyncio.run(evidence_builder.build_grouped_evidence(rag_context, knowledge_id="kb-1"))

    assert groups == []


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def test_no_tool_responses_returns_empty_list():
    groups = asyncio.run(evidence_builder.build_grouped_evidence({}))
    assert groups == []


def test_non_list_tool_responses_returns_empty_list():
    groups = asyncio.run(evidence_builder.build_grouped_evidence({"tool_responses": "not-a-list"}))
    assert groups == []
