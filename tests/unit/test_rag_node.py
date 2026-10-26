"""Unit tests for chat/rag_node.py (Phase 3c — see
claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests. rag_node.py imports its collaborators (run_rag_agent,
get_recent_evidence, filter_evidence_by_relevance, get_lightweight_schema,
get_docs, search_chunks, get_document_details) LOCALLY inside each function
body, so monkeypatches target the source modules (chat.rag_agent.
agent_execution, chat.db.chat_logs, chat.evidence_relevance, chat.schema,
chat.knowledge_adapter) rather than chat.rag_node's own namespace.
"""

import asyncio

import pytest

import chat.rag_node as rag_node


@pytest.fixture(autouse=True)
def _default_chat_config(monkeypatch):
    """Autouse: chat/config.py::resolve_chat_config (Phase 3d) does a real DB
    lookup — patch it to the hardcoded-default shape for every test in this
    file so tests that don't care about config resolution aren't forced to
    hit a DB. Tests that DO care call _patch_resolve_chat_config explicitly
    to override with different values."""
    _patch_resolve_chat_config(monkeypatch)


def _patch_schema(monkeypatch, schema=None, doc_ids=None):
    schema = schema if schema is not None else {"fields": {}, "enumerable_values": {}}
    doc_ids = doc_ids if doc_ids is not None else ["doc-1", "doc-2"]

    async def _fake_get_lightweight_schema(knowledge_id):
        return dict(schema)

    async def _fake_get_docs(knowledge_id):
        return doc_ids

    import chat.schema as chat_schema
    import chat.knowledge_adapter as knowledge_adapter
    monkeypatch.setattr(chat_schema, "get_lightweight_schema", _fake_get_lightweight_schema)
    monkeypatch.setattr(knowledge_adapter, "get_docs", _fake_get_docs)
    # Force a fresh schema build every test (avoid cross-test cache pollution).
    rag_node._SCHEMA_MEM_CACHE.clear()


def _patch_run_rag_agent(monkeypatch, context):
    async def _fake_run_rag_agent(**kwargs):
        return context

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)


def _disable_small_kb_direct(monkeypatch):
    monkeypatch.setattr(rag_node, "os", rag_node.os)  # no-op, keep import intact
    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "SMALL_KB_DIRECT_MODE", False)


def _patch_resolve_chat_config(monkeypatch, provider="openai", model="gpt-4.1-mini", system_prompt=None,
                                temperature=0.0, show_sources=True, chat_enabled=True):
    import chat.config as chat_config_module

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return chat_config_module.ResolvedChatConfig(
            llm_provider=provider, llm_model=model, temperature=temperature,
            system_prompt=system_prompt, show_sources=show_sources, chat_enabled=chat_enabled,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)


def _default_context(**overrides):
    context = {
        "raw_output": "The answer is 42.",
        "summaries": {"doc-1": {"summary": "Doc 1 summary", "file_name": "doc1.pdf"}},
        "chunks": [{"document_id": "doc-1", "text": "chunk text", "chunk_id": "c1"}],
        "usage_metrics": {"input_tokens": 100, "output_tokens": 50},
        "agent_output": {},
        "is_answered": True,
    }
    context.update(overrides)
    return context


# ---------------------------------------------------------------------------
# Hardcoded-config paths
# ---------------------------------------------------------------------------

def test_rag_node_uses_hardcoded_provider_and_model_when_no_config_row(monkeypatch):
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)
    _patch_resolve_chat_config(monkeypatch, provider="openai", model="gpt-4.1-mini")

    captured = {}

    async def _fake_run_rag_agent(**kwargs):
        captured.update(kwargs)
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert captured["llm_provider"] == "openai"
    assert captured["llm_model"] == "gpt-4.1-mini"
    assert captured["user_instructions"] is None
    assert result["error"] is None


def test_rag_node_uses_resolved_provider_and_model_from_config(monkeypatch):
    """chat/config.py::resolve_chat_config (Phase 3d) — rag_node must pass
    whatever it resolves through to run_rag_agent, not a hardcoded literal."""
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)
    _patch_resolve_chat_config(
        monkeypatch, provider="anthropic", model="claude-haiku-4-5",
        system_prompt="Always answer in French.",
    )

    captured = {}

    async def _fake_run_rag_agent(**kwargs):
        captured.update(kwargs)
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert captured["llm_provider"] == "anthropic"
    assert captured["llm_model"] == "claude-haiku-4-5"
    assert captured["user_instructions"] == "Always answer in French."
    assert result["error"] is None


def test_rag_node_forwards_request_chat_config_to_resolve_chat_config(monkeypatch):
    """Level-1 config passthrough: rag_node's per-node request_chat_config map
    must reach resolve_chat_config as request_provider/request_model/
    request_temperature/request_system_prompt kwargs — once for the rag_agent
    slice, once for the generate_answer slice."""
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)

    import chat.config as chat_config_module
    # One entry per call: rag_node resolves TWICE (retrieval loop, then answer
    # writer), so a single overwritten dict would hide whichever ran first.
    calls = []

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        calls.append(kwargs)
        return chat_config_module.ResolvedChatConfig(
            llm_provider=kwargs.get("request_provider") or "openai",
            llm_model=kwargs.get("request_model") or "gpt-4.1-mini",
            temperature=kwargs.get("request_temperature") if kwargs.get("request_temperature") is not None else 0.0,
            system_prompt=kwargs.get("request_system_prompt"),
            show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    agent_kwargs = {}

    async def _fake_run_rag_agent(**kwargs):
        agent_kwargs.update(kwargs)
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    asyncio.run(rag_node.rag_node(
        query="hello", knowledge_id="kb-1",
        request_chat_config={
            "rag_agent": {
                "provider": "anthropic", "model": "claude-haiku-4-5",
                "temperature": 0.7, "system_prompt": "Be terse.",
            },
            "generate_answer": {
                "provider": "google", "model": "gemini-2.5-flash",
                "system_prompt": "Include ClickUp URLs.",
            },
        },
    ))

    assert len(calls) == 2, "expected one resolution per role"
    rag_call, answer_call = calls
    assert rag_call["request_provider"] == "anthropic"
    assert rag_call["request_model"] == "claude-haiku-4-5"
    assert rag_call["request_temperature"] == 0.7
    assert rag_call["request_system_prompt"] == "Be terse."

    assert answer_call["request_provider"] == "google"
    assert answer_call["request_model"] == "gemini-2.5-flash"
    assert answer_call["request_system_prompt"] == "Include ClickUp URLs."

    # The resolved values must actually reach run_rag_agent as DISTINCT
    # provider/model pairs — this is the regression that motivated the change
    # (answer_llm_* used to be hardcoded `= llm_provider, llm_model`).
    assert agent_kwargs["llm_provider"] == "anthropic"
    assert agent_kwargs["llm_model"] == "claude-haiku-4-5"
    assert agent_kwargs["answer_llm_provider"] == "google"
    assert agent_kwargs["answer_llm_model"] == "gemini-2.5-flash"
    assert agent_kwargs["user_instructions"] == "Be terse."
    # include_charts defaults on, so the chart directive follows the operator's text.
    assert agent_kwargs["generate_answer_instructions"] == (
        "Include ClickUp URLs.\n\n" + rag_node.CHART_RENDERED_SEPARATELY_DIRECTIVE
    )


def test_rag_node_request_chat_config_none_forwards_no_overrides(monkeypatch):
    """request_chat_config=None (default) must not accidentally pass through
    truthy override kwargs — every request_* kwarg should resolve to None."""
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)

    import chat.config as chat_config_module
    captured_kwargs = {}

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        captured_kwargs.update(kwargs)
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    async def _fake_run_rag_agent(**kwargs):
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert captured_kwargs["request_provider"] is None
    assert captured_kwargs["request_model"] is None
    assert captured_kwargs["request_temperature"] is None
    assert captured_kwargs["request_system_prompt"] is None


def test_rag_node_unconfigured_answer_role_matches_rag_role(monkeypatch):
    """Backwards compatibility: with no per-node overrides, both roles resolve
    through the same DB-row/env/hardcoded chain and therefore land on the same
    provider/model — exactly the old `answer_llm_* = llm_*` behaviour. The
    ~426 production agents with no node_configs rows must be unaffected."""
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)

    import chat.config as chat_config_module

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        # Simulates a knowledge_chat_config row: identical for every caller,
        # since neither role supplied a request-level override.
        return chat_config_module.ResolvedChatConfig(
            llm_provider="groq", llm_model="llama-3.3-70b-versatile",
            temperature=0.0, system_prompt="From the DB row.",
            show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_config_module, "resolve_chat_config", _fake_resolve_chat_config)

    agent_kwargs = {}

    async def _fake_run_rag_agent(**kwargs):
        agent_kwargs.update(kwargs)
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert agent_kwargs["llm_provider"] == agent_kwargs["answer_llm_provider"] == "groq"
    assert agent_kwargs["llm_model"] == agent_kwargs["answer_llm_model"] == "llama-3.3-70b-versatile"
    # The DB row's system_prompt stays on the retrieval role only — feeding it
    # to the answer role as well would append the same suffix twice.
    assert agent_kwargs["user_instructions"] == "From the DB row."
    # No operator instructions; include_charts defaults on, so only the chart directive.
    assert agent_kwargs["generate_answer_instructions"] == rag_node.CHART_RENDERED_SEPARATELY_DIRECTIVE


def test_rag_node_requires_knowledge_id_positional(monkeypatch):
    import inspect
    sig = inspect.signature(rag_node.rag_node)
    params = list(sig.parameters.values())
    knowledge_id_param = next(p for p in params if p.name == "knowledge_id")
    assert knowledge_id_param.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# Answer node always runs (no RAG_DIRECT_ANSWER_MODE gate — removed 2026-08-11)
# ---------------------------------------------------------------------------

def test_answer_node_output_emits_final_response(monkeypatch):
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)
    _patch_run_rag_agent(monkeypatch, _default_context(raw_output="Final answer text"))

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert result.get("is_direct_response") is True
    assert result.get("response") == "Final answer text"


def test_empty_answer_is_reported_never_replaced_by_retrieval_context(monkeypatch):
    """The router used to fall back to relevant_context ("DOCUMENT SUMMARIES /
    RELEVANT CHUNKS ...") when no answer was produced, and that prompt dump
    reached users as the answer on the hosted resume sample."""
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)
    _patch_run_rag_agent(monkeypatch, _default_context(raw_output=""))

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert result["response"] == rag_node.NO_ANSWER_RESPONSE
    assert "RELEVANT CHUNKS" not in result["response"]
    assert result["is_answered"] == "no"
    assert result["error"] == "answer_generation_empty"


# ---------------------------------------------------------------------------
# Small-KB-direct branch
# ---------------------------------------------------------------------------

def _patch_small_kb_answer_node(monkeypatch, answer_text="Small KB answer", is_answered=True):
    async def _fake_generate_rag_answer(**kwargs):
        if kwargs.get("token_queue") is not None:
            kwargs["token_queue"].put_nowait(answer_text)
        return {
            "answer": answer_text,
            "is_answered": is_answered,
            "usage_metrics": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    import chat.rag_agent.answer_node as answer_node
    monkeypatch.setattr(answer_node, "generate_rag_answer", _fake_generate_rag_answer)
    # rag_node imports generate_rag_answer LOCALLY inside _try_small_kb_direct,
    # so patching the source module above is sufficient — no re-import needed.


def test_small_kb_direct_mode_bypasses_agentic_loop_but_still_generates_answer(monkeypatch):
    schema = {"fields": {}, "enumerable_values": {}, "document_count": 3}
    _patch_schema(monkeypatch, schema=schema, doc_ids=["d1", "d2", "d3"])

    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "SMALL_KB_DIRECT_MODE", True)
    monkeypatch.setattr(setup_env, "SMALL_KB_DOC_THRESHOLD", 15)

    chunks = [{"document_id": "d1", "object_id": "d1", "text": "chunk 1"}]

    async def _fake_search_chunks(**kwargs):
        return chunks

    async def _fake_get_document_details(**kwargs):
        return [{"object_id": "d1", "summary": "Doc 1 summary", "file_name": "d1.pdf", "metadata": {}}]

    import chat.knowledge_adapter as knowledge_adapter
    monkeypatch.setattr(knowledge_adapter, "search_chunks", _fake_search_chunks)
    monkeypatch.setattr(knowledge_adapter, "get_document_details", _fake_get_document_details)
    _patch_small_kb_answer_node(monkeypatch, answer_text="Small KB answer")

    # If the agentic path were reached instead, run_rag_agent would need to be
    # patched — leave it unpatched so a real call (network) would error loudly.
    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert result["error"] is None
    assert result["summaries"]["d1"]["file_name"] == "d1.pdf"
    assert result["relevant_sources"]["tool_name"] == "rag"
    # The gap this closes: small-KB-direct used to return with NO generated
    # answer at all. It must now carry the same response/is_direct_response
    # shape as the normal agentic path.
    assert result["response"] == "Small KB answer"
    assert result["is_direct_response"] is True
    assert result["is_answered"] == "yes"


def test_small_kb_direct_mode_streams_answer_tokens(monkeypatch):
    schema = {"fields": {}, "enumerable_values": {}, "document_count": 1}
    _patch_schema(monkeypatch, schema=schema, doc_ids=["d1"])

    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "SMALL_KB_DIRECT_MODE", True)
    monkeypatch.setattr(setup_env, "SMALL_KB_DOC_THRESHOLD", 15)

    async def _fake_search_chunks(**kwargs):
        return [{"document_id": "d1", "object_id": "d1", "text": "chunk 1"}]

    async def _fake_get_document_details(**kwargs):
        return []

    import chat.knowledge_adapter as knowledge_adapter
    monkeypatch.setattr(knowledge_adapter, "search_chunks", _fake_search_chunks)
    monkeypatch.setattr(knowledge_adapter, "get_document_details", _fake_get_document_details)
    _patch_small_kb_answer_node(monkeypatch, answer_text="Streamed small KB answer")

    sentinel_queue = asyncio.Queue()
    result = asyncio.run(
        rag_node.rag_node(query="hello", knowledge_id="kb-1", token_queue=sentinel_queue)
    )

    assert result["response"] == "Streamed small KB answer"
    assert sentinel_queue.get_nowait() == "Streamed small KB answer"


def test_small_kb_direct_mode_falls_through_when_doc_count_exceeds_threshold(monkeypatch):
    schema = {"fields": {}, "enumerable_values": {}, "document_count": 100}
    _patch_schema(monkeypatch, schema=schema, doc_ids=[f"d{i}" for i in range(100)])

    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "SMALL_KB_DIRECT_MODE", True)
    monkeypatch.setattr(setup_env, "SMALL_KB_DOC_THRESHOLD", 15)

    _patch_run_rag_agent(monkeypatch, _default_context())

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert result["error"] is None
    # Went through the agentic path's normalized summaries, not the direct path's raw dict.
    assert result["summaries"]["doc-1"]["metadata"] == {}


def test_small_kb_direct_mode_disabled_falls_through_to_agentic(monkeypatch):
    schema = {"fields": {}, "enumerable_values": {}, "document_count": 1}
    _patch_schema(monkeypatch, schema=schema, doc_ids=["d1"])

    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "SMALL_KB_DIRECT_MODE", False)

    _patch_run_rag_agent(monkeypatch, _default_context())

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert result["error"] is None
    assert result["summaries"]["doc-1"]["file_name"] == "doc1.pdf"


# ---------------------------------------------------------------------------
# Streaming gate
# ---------------------------------------------------------------------------

def test_streaming_gate_passes_token_queue_when_supplied(monkeypatch):
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)

    captured = {}

    async def _fake_run_rag_agent(**kwargs):
        captured.update(kwargs)
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    sentinel_queue = object()
    asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1", token_queue=sentinel_queue))

    assert captured["answer_token_queue"] is sentinel_queue


def test_streaming_gate_omits_token_queue_when_none_supplied(monkeypatch):
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)

    captured = {}

    async def _fake_run_rag_agent(**kwargs):
        captured.update(kwargs)
        return _default_context()

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1", token_queue=None))

    assert captured["answer_token_queue"] is None


# ---------------------------------------------------------------------------
# _filter_to_cited fallback behavior
# ---------------------------------------------------------------------------

def test_filter_to_cited_keeps_everything_when_no_citations():
    summaries = {"doc-1": {"summary": "s1"}}
    chunks = [{"document_id": "doc-1", "text": "c1"}]

    result_summaries, result_chunks = rag_node._filter_to_cited(summaries, chunks, agent_output={})

    assert result_summaries == summaries
    assert result_chunks == chunks


def test_filter_to_cited_falls_back_when_helper_import_fails(monkeypatch):
    # Simulate the helper raising (e.g. answer_node import breaks) — should
    # degrade to "keep everything", never raise.
    import chat.rag_agent.answer_node as answer_node

    def _broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(answer_node, "_cited_ids_from_agent_output", _broken)

    summaries = {"doc-1": {"summary": "s1"}}
    chunks = [{"document_id": "doc-1", "text": "c1"}]

    result_summaries, result_chunks = rag_node._filter_to_cited(
        summaries, chunks, agent_output={"relevant_docs": ["doc-1"]}
    )

    assert result_summaries == summaries
    assert result_chunks == chunks


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_rag_node_returns_error_dict_when_agent_raises(monkeypatch):
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)

    async def _fake_run_rag_agent(**kwargs):
        raise RuntimeError("agent exploded")

    import chat.rag_agent.agent_execution as agent_execution
    monkeypatch.setattr(agent_execution, "run_rag_agent", _fake_run_rag_agent)

    result = asyncio.run(rag_node.rag_node(query="hello", knowledge_id="kb-1"))

    assert "agent exploded" in result["error"]
    assert result["relevant_context"] == ""


# ---------------------------------------------------------------------------
# _log_usage's key_type gap (documents current, known-gap behavior)
# ---------------------------------------------------------------------------

def test_log_usage_defaults_key_type_to_platform_key_when_not_in_context(monkeypatch):
    captured = {}

    async def _fake_send_llm_usage_event(**kwargs):
        captured.update(kwargs)

    import utils.qdrant.usage_tracking as usage_tracking
    monkeypatch.setattr(usage_tracking, "send_llm_usage_event", _fake_send_llm_usage_event)

    async def _run():
        context = {"usage_metrics": {"input_tokens": 10, "output_tokens": 5}}
        rag_node._log_usage(
            context, "openai", "gpt-4.1-mini", owner_id="owner-1", knowledge_id="kb-1",
            session_id="sess-1", key_type=context.get("key_type"),
        )
        # _log_usage schedules a fire-and-forget task via ensure_future, which
        # requires a running loop; yield once so it actually executes.
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert captured.get("key_type") == "platform-key"


def test_log_usage_reports_real_key_type_when_present(monkeypatch):
    """Once run_rag_agent surfaces key_type in its returned context (the
    key_type gap fix in chat/rag_agent/agent_execution.py), _log_usage must
    report that real value instead of always defaulting to platform-key."""
    captured = {}

    async def _fake_send_llm_usage_event(**kwargs):
        captured.update(kwargs)

    import utils.qdrant.usage_tracking as usage_tracking
    monkeypatch.setattr(usage_tracking, "send_llm_usage_event", _fake_send_llm_usage_event)

    async def _run():
        context = {"usage_metrics": {"input_tokens": 10, "output_tokens": 5}, "key_type": "owner-key"}
        rag_node._log_usage(
            context, "openai", "gpt-4.1-mini", owner_id="owner-1", knowledge_id="kb-1",
            session_id="sess-1", key_type=context.get("key_type"),
        )
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert captured.get("key_type") == "owner-key"


def test_rag_node_propagates_key_type_from_run_rag_agent_context(monkeypatch):
    """End-to-end: rag_node reads context["key_type"] (set by run_rag_agent,
    which now surfaces it from create_rag_agent_graph's get_llm resolution)
    and forwards the real value to the usage event, not a hardcoded default."""
    _patch_schema(monkeypatch)
    _disable_small_kb_direct(monkeypatch)
    _patch_run_rag_agent(monkeypatch, _default_context(key_type="owner-key"))

    captured = {}

    async def _fake_send_llm_usage_event(**kwargs):
        captured.update(kwargs)

    import utils.qdrant.usage_tracking as usage_tracking
    monkeypatch.setattr(usage_tracking, "send_llm_usage_event", _fake_send_llm_usage_event)

    async def _run():
        result = await rag_node.rag_node(query="hello", knowledge_id="kb-1")
        await asyncio.sleep(0)  # let _log_usage's fire-and-forget task run
        return result

    result = asyncio.run(_run())

    assert result["error"] is None
    assert captured.get("key_type") == "owner-key"
