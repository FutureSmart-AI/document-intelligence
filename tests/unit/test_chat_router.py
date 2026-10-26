"""Unit tests for routers/chat.py (Phase 5 — see
claude-logs/plan/knowledge-owned-chat-progress.md).

Pure unit tests: require_permission, resolve_chat_config,
run_knowledge_chat_graph, get_past_messages, insert_chat_log, and
build_grouped_evidence are all monkeypatched with fakes — no real DB/LLM/
Qdrant call in any test. Uses FastAPI's TestClient against a minimal app
that mounts only routers.chat's router, so require_permission's real
implementation (which does its own DB/session lookups) is overridden via
FastAPI's dependency_overrides rather than left to run for real.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

import routers.chat as chat_router_module


def _find_require_permission_dependencies():
    """Each Depends(require_permission("use"/"read"/"write"/"delete")) call
    site in routers/chat.py bakes in its OWN closure (require_permission
    builds a fresh one per call), so overriding just one route's closure
    (e.g. the chat POST endpoint's "use") leaves every other route's check
    (sessions' "read"/"write"/"delete") still enforcing the real RBAC
    lookup — collect every distinct closure across every route, not just
    the first match, so dependency_overrides covers all of them."""
    calls = []
    for route in chat_router_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency") and dep.call not in calls:
                calls.append(dep.call)
    if not calls:
        raise RuntimeError("Could not locate any require_permission dependency on the chat router")
    return calls


def _make_app():
    app = FastAPI()
    app.include_router(chat_router_module.router)
    for call in _find_require_permission_dependencies():
        app.dependency_overrides[call] = lambda: None
    return app


@pytest.fixture(autouse=True)
def _patch_collaborators(monkeypatch):
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        import chat.config as chat_config_module
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve_chat_config)

    async def _fake_get_past_messages(session_id, limit=10):
        return []
    monkeypatch.setattr(chat_router_module, "get_past_messages", _fake_get_past_messages)

    async def _fake_insert_chat_log(**kwargs):
        return SimpleNamespace(chat_id=uuid.UUID(int=0))
    monkeypatch.setattr(chat_router_module, "insert_chat_log", _fake_insert_chat_log)

    # A supplied session_id is looked up to check it belongs to the collection;
    # these tests' sessions are all new.
    async def _fake_get_session_identity(session_id):
        return None
    monkeypatch.setattr(chat_router_module.sample_access, "get_session_identity", _fake_get_session_identity)


def _default_final_state(**overrides):
    state = {
        "response": "The ECR link is 123456789.dkr.ecr.us-east-1.amazonaws.com/knowledge-service.",
        "refined_query": "What is the ECR link for the knowledge service?",
        "relevant_sources": {"data": [{"id": "doc-1", "title": "runbook.md"}], "tool_name": "rag"},
        "summaries": {"doc-1": {"summary": "Deployment runbook"}},
        "detailed_chunks": {"doc-1": [{"text": "chunk"}]},
        "rag_context": {"tool_responses": []},
        "is_answered": "yes",
        "error": None,
        "usage_by_model": [{"provider": "openai", "model": "gpt-4.1-mini", "input_tokens": 10, "output_tokens": 5}],
        "service_calls": [{
            "service": "rag", "latency_ms": 850, "provider": "openai", "model": "gpt-4.1-mini",
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "cost_usd": 0.0001,
        }],
    }
    state.update(overrides)
    return state


def _patch_graph_run(monkeypatch, final_state):
    async def _fake_run(**kwargs):
        return final_state
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)


def _patch_evidence_builder(monkeypatch, groups=None):
    async def _fake_build_grouped_evidence(rag_context, knowledge_id=None):
        return groups or []
    import chat.evidence_builder as evidence_builder
    monkeypatch.setattr(evidence_builder, "build_grouped_evidence", _fake_build_grouped_evidence)


# ---------------------------------------------------------------------------
# Non-streaming path
# ---------------------------------------------------------------------------

def test_non_streaming_chat_returns_full_response(monkeypatch):
    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "What is the ECR link for the knowledge service?", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "What is the ECR link for the knowledge service?"
    assert "ecr" in body["response"].lower()
    assert body["session_id"]  # a session_id was minted
    assert body["chat_id"]  # minted by insert_chat_log
    assert body["is_answered"] == "yes"
    assert body["error"] is None
    assert body["data"]["knowledge"]["summaries"] == {"doc-1": {"summary": "Deployment runbook"}}
    assert body["usage"]["total_tokens"] == 15
    assert body["debug_info"]["type"] == "vanilla_rag"  # no agent_output in rag_context fixture


def test_non_streaming_chat_uses_supplied_session_id(monkeypatch):
    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "session_id": "my-session", "stream": False},
    )

    assert response.status_code == 200
    assert response.json()["session_id"] == "my-session"


def test_non_streaming_chat_500s_on_graph_failure(monkeypatch):
    async def _fake_run(**kwargs):
        raise RuntimeError("graph exploded")
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 500


def test_chat_enabled_false_returns_404(monkeypatch):
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        import chat.config as chat_config_module
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=False,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve_chat_config)
    _patch_graph_run(monkeypatch, _default_final_state())

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 404


def test_show_sources_false_strips_sources_from_response(monkeypatch):
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        import chat.config as chat_config_module
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=False, chat_enabled=True,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve_chat_config)
    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    body = response.json()
    knowledge_data = body["data"]["knowledge"]
    assert knowledge_data["relevant_sources"] is None
    assert knowledge_data["summaries"] is None
    assert knowledge_data["detailed_chunks"] is None
    # response text itself is untouched by show_sources
    assert body["response"]


def test_non_streaming_chat_forwards_config_overrides_to_graph(monkeypatch):
    """Level-1 config passthrough: the flat ChatRequest.provider/model/
    temperature/system_prompt fields apply to EVERY node, so they must reach
    run_knowledge_chat_graph fanned out under each supported node key with
    only the caller-supplied fields present."""
    captured = {}

    async def _fake_run(**kwargs):
        captured.update(kwargs)
        return _default_final_state()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "hello", "owner_id": "owner-1", "stream": False,
            "provider": "anthropic", "model": "claude-haiku-4-5", "temperature": 0.5,
        },
    )

    assert response.status_code == 200
    expected = {"provider": "anthropic", "model": "claude-haiku-4-5", "temperature": 0.5}
    assert captured["request_chat_config"] == {
        node: expected for node in chat_router_module.SUPPORTED_NODES
    }


def test_non_streaming_chat_omits_overrides_when_not_supplied(monkeypatch):
    """No overrides sent -> every node's entry is empty, NOT missing. An empty
    dict per node still resolves through the DB row / env / hardcoded levels;
    what must not happen is a stray key carrying a None that would look like a
    deliberate override downstream."""
    captured = {}

    async def _fake_run(**kwargs):
        captured.update(kwargs)
        return _default_final_state()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    assert captured["request_chat_config"] == {
        node: {} for node in chat_router_module.SUPPORTED_NODES
    }


def test_node_configs_override_flat_fields_per_node(monkeypatch):
    """The whole point of node_configs: rag_agent and generate_answer keep
    DIFFERENT models and prompts instead of collapsing onto one. 27 of the 52
    production agents holding node_configs rows differ this way."""
    captured = {}

    async def _fake_run(**kwargs):
        captured.update(kwargs)
        return _default_final_state()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    client = TestClient(_make_app())
    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "hello", "owner_id": "owner-1", "stream": False,
            # Turn-wide default...
            "provider": "openai", "model": "gpt-4o",
            # ...specialised per node.
            "node_configs": {
                "rag_agent": {"provider": "openai", "model": "gpt-5-mini",
                              "prompt": "Retrieve thoroughly."},
                "generate_answer": {"provider": "google", "model": "gemini-2.5-flash",
                                    "prompt": "Include ClickUp URLs."},
            },
        },
    )

    assert response.status_code == 200
    rcc = captured["request_chat_config"]
    assert rcc["rag_agent"] == {
        "provider": "openai", "model": "gpt-5-mini", "system_prompt": "Retrieve thoroughly."}
    assert rcc["generate_answer"] == {
        "provider": "google", "model": "gemini-2.5-flash", "system_prompt": "Include ClickUp URLs."}
    # Unnamed node falls back to the flat turn-wide values.
    assert rcc["refined_query_node"] == {"provider": "openai", "model": "gpt-4o"}


def test_node_configs_rejects_unsupported_node_name():
    """A node this service cannot run must 422, not be silently discarded —
    otherwise the caller gets a 200 and never learns its config was dropped.
    'supervisor' and 'response_node' both exist in the Orchestrator registry
    but have no counterpart in this graph."""
    client = TestClient(_make_app())
    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "hello", "owner_id": "owner-1", "stream": False,
            "node_configs": {"supervisor": {"provider": "openai", "model": "gpt-4o"}},
        },
    )
    assert response.status_code == 422
    assert "supervisor" in response.text


def test_node_config_rejects_provider_without_model():
    """provider and model resolve as a pair; half an override would silently
    mix a request-level provider with a DB-level model."""
    client = TestClient(_make_app())
    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "hello", "owner_id": "owner-1", "stream": False,
            "node_configs": {"rag_agent": {"provider": "openai"}},
        },
    )
    assert response.status_code == 422


def test_non_streaming_chat_persists_attachment_fields(monkeypatch):
    """Vision option 1 (see the plan's "Vision input" section): has_attachment/
    attachment_url are transcript-completeness fields only — must reach
    insert_chat_log unchanged, never fed into the graph/retrieval path."""
    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    captured = {}

    async def _fake_insert_chat_log(**kwargs):
        captured.update(kwargs)
        return None
    monkeypatch.setattr(chat_router_module, "insert_chat_log", _fake_insert_chat_log)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "what's wrong in this screenshot?", "owner_id": "owner-1", "stream": False,
            "has_attachment": True, "attachment_url": "https://example.com/screenshot.png",
        },
    )

    assert response.status_code == 200
    assert captured["has_attachment"] is True
    assert captured["attachment_url"] == "https://example.com/screenshot.png"


def test_non_streaming_chat_defaults_no_attachment(monkeypatch):
    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    captured = {}

    async def _fake_insert_chat_log(**kwargs):
        captured.update(kwargs)
        return None
    monkeypatch.setattr(chat_router_module, "insert_chat_log", _fake_insert_chat_log)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    assert captured["has_attachment"] is False
    assert captured["attachment_url"] is None


def test_streaming_chat_persists_attachment_fields(monkeypatch):
    async def _fake_run(**kwargs):
        token_queue = kwargs.get("token_queue")
        if token_queue is not None:
            token_queue.put_nowait("hi")
        return _default_final_state()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    captured = {}

    async def _fake_insert_chat_log(**kwargs):
        captured.update(kwargs)
        return None
    monkeypatch.setattr(chat_router_module, "insert_chat_log", _fake_insert_chat_log)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "hello", "owner_id": "owner-1", "stream": True,
            "has_attachment": True, "attachment_url": "https://example.com/img.png",
        },
    )

    assert response.status_code == 200
    assert captured["has_attachment"] is True
    assert captured["attachment_url"] == "https://example.com/img.png"


def test_non_streaming_chat_surfaces_follow_up_from_graph_state(monkeypatch):
    """Follow-ups are produced by the graph's own follow_up node (the last node
    in chat/graph.py) and land on final_state["follow_up"]; the router only
    copies them onto the response. The gating itself — flag off, turn
    unanswered, generation failed — belongs to the node and is covered in
    tests/unit/test_follow_up.py, not here."""
    _patch_graph_run(
        monkeypatch,
        _default_final_state(follow_up=["Follow-up A?", "Follow-up B?"]),
    )
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "What is the ECR link?", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    assert response.json()["follow_up"] == ["Follow-up A?", "Follow-up B?"]


def test_non_streaming_chat_omits_follow_up_when_disabled(monkeypatch):
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        import chat.config as chat_config_module
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=True, chat_enabled=True, follow_up=False,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve_chat_config)

    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    called = {"count": 0}

    async def _fake_generate_follow_up_questions(**kwargs):
        called["count"] += 1
        return ["should not be called"]
    import chat.follow_up as follow_up_module
    monkeypatch.setattr(follow_up_module, "generate_follow_up_questions", _fake_generate_follow_up_questions)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "What is the ECR link?", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    assert response.json()["follow_up"] is None
    assert called["count"] == 0


def test_non_streaming_chat_omits_follow_up_when_not_answered(monkeypatch):
    """No point generating follow-ups from an empty/unanswered turn — see
    _maybe_generate_follow_up's short-circuit, mirroring the donor's own
    `if not answered: return None` (fs-agent-chat/agents/run.py)."""
    _patch_graph_run(monkeypatch, _default_final_state(response="", relevant_context="", is_answered="no"))
    _patch_evidence_builder(monkeypatch)

    called = {"count": 0}

    async def _fake_generate_follow_up_questions(**kwargs):
        called["count"] += 1
        return ["should not be called"]
    import chat.follow_up as follow_up_module
    monkeypatch.setattr(follow_up_module, "generate_follow_up_questions", _fake_generate_follow_up_questions)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "unanswerable query", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    assert response.json()["follow_up"] is None
    assert called["count"] == 0


def test_non_streaming_chat_omits_follow_up_on_timeout(monkeypatch):
    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch)

    async def _fake_generate_follow_up_questions(**kwargs):
        await asyncio.sleep(10)
        return ["too slow"]
    import chat.follow_up as follow_up_module
    monkeypatch.setattr(follow_up_module, "generate_follow_up_questions", _fake_generate_follow_up_questions)

    import utils.setup_env as setup_env
    monkeypatch.setattr(setup_env, "RAG_FOLLOW_UP_TIMEOUT_SEC", 0.05)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "What is the ECR link?", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    assert response.json()["follow_up"] is None


def test_streaming_chat_includes_follow_up_in_final_frame(monkeypatch):
    async def _fake_run(**kwargs):
        token_queue = kwargs.get("token_queue")
        if token_queue is not None:
            token_queue.put_nowait("hi")
        return _default_final_state(follow_up=["Streamed follow-up?"])
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": True},
    )

    assert response.status_code == 200
    frames = [line for line in response.text.split("\n\n") if line.strip()]
    final_frame = frames[-1]
    assert '"Streamed follow-up?"' in final_frame


def test_missing_owner_id_resolves_from_the_knowledge_config(monkeypatch):
    """owner_id is optional on the chat body: when omitted the router resolves
    the KB's actual owner from knowledge_config (see ChatRequest.owner_id and
    routers/chat.py's `request.owner_id or await resolve_kb_owner_id(...)`),
    rather than rejecting the request."""
    captured = {}

    async def _fake_resolve_kb_owner_id(knowledge_id):
        captured["knowledge_id"] = knowledge_id
        return "owner-from-config"
    monkeypatch.setattr(chat_router_module, "resolve_kb_owner_id", _fake_resolve_kb_owner_id)

    async def _fake_run(**kwargs):
        captured["owner_id"] = kwargs.get("owner_id")
        return _default_final_state()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post("/v1/kb-1/chat", json={"query": "hello", "stream": False})

    assert response.status_code == 200
    assert captured["knowledge_id"] == "kb-1"
    assert captured["owner_id"] == "owner-from-config"


# ---------------------------------------------------------------------------
# Streaming path (SSE)
# ---------------------------------------------------------------------------

def test_streaming_chat_emits_chunks_then_final_frame(monkeypatch):
    async def _fake_run(**kwargs):
        token_queue = kwargs.get("token_queue")
        if token_queue is not None:
            token_queue.put_nowait("Hello")
            token_queue.put_nowait(" world")
        return _default_final_state()

    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": True},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = [line for line in response.text.split("\n\n") if line.strip()]
    assert any('"chunk": "Hello"' in f for f in frames)
    assert any('"chunk": " world"' in f for f in frames)
    # Final frame is the full response payload, not a {"chunk": ...} frame.
    final_frame = frames[-1]
    assert '"response"' in final_frame
    assert '"session_id"' in final_frame


def test_streaming_chat_forwards_config_overrides_to_graph(monkeypatch):
    captured = {}

    async def _fake_run(**kwargs):
        captured.update(kwargs)
        token_queue = kwargs.get("token_queue")
        if token_queue is not None:
            token_queue.put_nowait("hi")
        return _default_final_state()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={
            "query": "hello", "owner_id": "owner-1", "stream": True,
            "system_prompt": "Be terse.",
        },
    )

    assert response.status_code == 200
    assert captured["request_chat_config"] == {
        node: {"system_prompt": "Be terse."} for node in chat_router_module.SUPPORTED_NODES
    }


def test_streaming_chat_emits_error_frame_on_graph_failure(monkeypatch):
    async def _fake_run(**kwargs):
        raise RuntimeError("stream exploded")
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": True},
    )

    assert response.status_code == 200  # SSE stream itself started fine
    assert '"error"' in response.text
    assert "stream exploded" in response.text


# ---------------------------------------------------------------------------
# Regression: rag_context["messages"] holds raw LangChain message objects
# (chat/rag_agent/agent_execution.py::_extract_retrieved_context_from_messages
# stores the ReAct loop's message list verbatim) — found via a live smoke
# test: json.dumps on the final SSE frame raised "Object of type HumanMessage
# is not JSON serializable" because rag_context was included in the payload
# unfiltered. Fixed by _json_safe_rag_context stripping the "messages" key.
# ---------------------------------------------------------------------------

def _final_state_with_raw_messages(**overrides):
    return _default_final_state(
        rag_context={
            "tool_responses": [],
            "messages": [HumanMessage(content="hi"), AIMessage(content="hello")],
        },
        **overrides,
    )


def test_non_streaming_chat_serializes_despite_raw_messages_in_rag_context(monkeypatch):
    _patch_graph_run(monkeypatch, _final_state_with_raw_messages())
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    body = response.json()
    rag_context = body["data"]["knowledge"]["rag_context"]
    assert "messages" not in (rag_context or {})
    assert rag_context["tool_responses"] == []


def test_streaming_chat_final_frame_serializes_despite_raw_messages_in_rag_context(monkeypatch):
    async def _fake_run(**kwargs):
        return _final_state_with_raw_messages()
    monkeypatch.setattr(chat_router_module, "run_knowledge_chat_graph", _fake_run)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": True},
    )

    assert response.status_code == 200
    # The bug crashed json.dumps inside the generator, which surfaces as no
    # final payload frame (only chunk/error frames, or a truncated stream) —
    # assert the final frame is present, well-formed JSON, and messages-free.
    frames = [line for line in response.text.split("\n\n") if line.strip()]
    final_frame = frames[-1]
    assert final_frame.startswith("data: ")
    import json as _json
    final_payload = _json.loads(final_frame[len("data: "):])
    rag_context = (final_payload.get("data") or {}).get("knowledge", {}).get("rag_context")
    assert "messages" not in (rag_context or {})


# ---------------------------------------------------------------------------
# debug_info / usage — pipeline diagnostics distinct from data.knowledge
# (see chat/rag_agent/agent_output.py's RagAgentOutput.previous_context_used
# and answer_node.py's _cited_ids_from_agent_output).
# ---------------------------------------------------------------------------

def test_debug_info_reports_agentic_path_with_citations(monkeypatch):
    """When rag_context carries an agent_output (the agentic ReAct path, as
    opposed to small-KB direct mode), debug_info.type is "agentic_rag" and
    document_ids/previous_context_used are read straight off agent_output —
    no extra LLM call."""
    final_state = _default_final_state(
        rag_context={
            "tool_responses": [],
            "agent_output": {
                "previous_context_used": True,
                "relevant_docs": [{"doc_id": "doc-1", "chunk_ids": ["c1"]}],
            },
        },
        service_calls=[{
            "service": "rag", "latency_ms": 1234, "provider": "openai", "model": "gpt-4.1-mini",
            "input_tokens": 20, "output_tokens": 10, "total_tokens": 30, "cost_usd": 0.0002,
        }],
    )
    _patch_graph_run(monkeypatch, final_state)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    debug_info = response.json()["debug_info"]
    assert debug_info["type"] == "agentic_rag"
    assert debug_info["latency_ms"] == 1234
    assert debug_info["previous_context_used"] is True
    assert debug_info["document_ids"] == ["doc-1"]
    assert debug_info["is_answered"] == "yes"


def test_usage_sums_service_calls(monkeypatch):
    final_state = _default_final_state(
        service_calls=[
            {"service": "query_refiner", "latency_ms": 100, "provider": "openai", "model": "gpt-4.1-mini",
             "input_tokens": 5, "output_tokens": 2, "total_tokens": 7, "cost_usd": 0.00001},
            {"service": "rag", "latency_ms": 900, "provider": "openai", "model": "gpt-4.1-mini",
             "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "cost_usd": 0.0001},
        ],
    )
    _patch_graph_run(monkeypatch, final_state)
    _patch_evidence_builder(monkeypatch)

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "hello", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["input_tokens"] == 15
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 22
    assert len(usage["service_calls"]) == 2


# ---------------------------------------------------------------------------
# G14 — _persist_turn stores relevant_sources/summaries/detailed_chunks
# alongside knowledge_evidence, so a reopened session's sources panel has
# real data behind it (previously only the lossy knowledge_evidence was
# persisted).
# ---------------------------------------------------------------------------

def test_persist_turn_stores_display_evidence_alongside_knowledge_evidence(monkeypatch):
    captured = {}

    async def _fake_insert_chat_log(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(chat_id=uuid.UUID(int=0))
    monkeypatch.setattr(chat_router_module, "insert_chat_log", _fake_insert_chat_log)

    _patch_graph_run(monkeypatch, _default_final_state())
    _patch_evidence_builder(monkeypatch, groups=[{"tool": "retrieve_documents", "documents": [{"object_id": "doc-1", "summary": "…"}]}])

    app = _make_app()
    client = TestClient(app)

    response = client.post(
        "/v1/kb-1/chat",
        json={"query": "What is the ECR link for the knowledge service?", "owner_id": "owner-1", "stream": False},
    )

    assert response.status_code == 200
    service_data = captured["service_data"]
    assert service_data["relevant_sources"] == {"data": [{"id": "doc-1", "title": "runbook.md"}], "tool_name": "rag"}
    assert service_data["summaries"] == {"doc-1": {"summary": "Deployment runbook"}}
    assert service_data["detailed_chunks"] == {"doc-1": [{"text": "chunk"}]}
    assert service_data["knowledge_evidence"] == [{"tool": "retrieve_documents", "documents": [{"object_id": "doc-1", "summary": "…"}]}]


# ---------------------------------------------------------------------------
# G14 — GET /v1/{knowledge_id}/sessions/{session_id} returns a flat
# messages[] list and applies show_sources at read time, same as the live
# /chat response.
# ---------------------------------------------------------------------------

def _fake_transcript():
    return {
        "session_id": "sess-1",
        "knowledge_id": "kb-1",
        "user_id": "owner-1",
        "title": "hello",
        "created_at": None,
        "updated_at": None,
        "exchange_count": 1,
        "messages": [
            {
                "n": 1, "chat_id": "chat-1", "role": "user", "message": "hello",
                "created_at": None, "has_attachment": False, "attachment_url": None,
            },
            {
                "n": 1, "chat_id": "chat-1", "role": "assistant", "message": "hi there",
                "created_at": None, "is_answered": True, "refined_query": None,
                "llm_provider": "openai", "model": "gpt-4.1-mini",
                "data": {"knowledge": {
                    "relevant_sources": {"data": [{"id": "doc-1"}], "tool_name": "rag"},
                    "summaries": {"doc-1": {"summary": "…"}},
                    "detailed_chunks": {"doc-1": [{"text": "chunk"}]},
                    "knowledge_evidence": [{"tool": "retrieve_documents", "documents": []}],
                }},
            },
        ],
    }


def test_get_session_returns_message_list_with_sources_when_show_sources_true(monkeypatch):
    async def _fake_get_session_transcript(knowledge_id, session_id):
        return _fake_transcript()
    monkeypatch.setattr(chat_router_module, "get_session_transcript", _fake_get_session_transcript)

    app = _make_app()
    client = TestClient(app)

    response = client.get("/v1/kb-1/sessions/sess-1")

    assert response.status_code == 200
    body = response.json()
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant["data"]["knowledge"]["relevant_sources"] == {"data": [{"id": "doc-1"}], "tool_name": "rag"}
    assert assistant["data"]["knowledge"]["detailed_chunks"] == {"doc-1": [{"text": "chunk"}]}
    assert assistant["data"]["knowledge"]["knowledge_evidence"] == [{"tool": "retrieve_documents", "documents": []}]


def test_get_session_strips_sources_when_show_sources_false(monkeypatch):
    async def _fake_get_session_transcript(knowledge_id, session_id):
        return _fake_transcript()
    monkeypatch.setattr(chat_router_module, "get_session_transcript", _fake_get_session_transcript)

    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        import chat.config as chat_config_module
        return chat_config_module.ResolvedChatConfig(
            llm_provider="openai", llm_model="gpt-4.1-mini", temperature=0.0,
            system_prompt=None, show_sources=False, chat_enabled=True,
        )
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve_chat_config)

    app = _make_app()
    client = TestClient(app)

    response = client.get("/v1/kb-1/sessions/sess-1")

    assert response.status_code == 200
    assistant = response.json()["messages"][1]
    knowledge_data = assistant["data"]["knowledge"]
    assert knowledge_data["relevant_sources"] is None
    assert knowledge_data["summaries"] is None
    assert knowledge_data["detailed_chunks"] is None
    # knowledge_evidence is cross-turn memory, not a sources-panel field —
    # show_sources must not strip it.
    assert knowledge_data["knowledge_evidence"] == [{"tool": "retrieve_documents", "documents": []}]


def test_get_session_404s_when_transcript_missing(monkeypatch):
    async def _fake_get_session_transcript(knowledge_id, session_id):
        return None
    monkeypatch.setattr(chat_router_module, "get_session_transcript", _fake_get_session_transcript)

    app = _make_app()
    client = TestClient(app)

    response = client.get("/v1/kb-1/sessions/missing-session")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# G13 — PATCH .../sessions/{session_id}/messages/{chat_id}/feedback
# ---------------------------------------------------------------------------

def test_set_message_feedback_positive(monkeypatch):
    captured = {}

    async def _fake_set_message_feedback(knowledge_id, session_id, chat_id, feedback):
        captured.update(knowledge_id=knowledge_id, session_id=session_id, chat_id=chat_id, feedback=feedback)
        return True
    monkeypatch.setattr(chat_router_module, "set_message_feedback", _fake_set_message_feedback)

    app = _make_app()
    client = TestClient(app)

    response = client.patch(
        "/v1/kb-1/sessions/sess-1/messages/chat-1/feedback",
        json={"feedback": "positive"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"chat_id": "chat-1", "feedback": "positive", "message": "Feedback saved"}
    assert captured == {"knowledge_id": "kb-1", "session_id": "sess-1", "chat_id": "chat-1", "feedback": "positive"}


def test_set_message_feedback_clears_with_null(monkeypatch):
    async def _fake_set_message_feedback(knowledge_id, session_id, chat_id, feedback):
        assert feedback is None
        return True
    monkeypatch.setattr(chat_router_module, "set_message_feedback", _fake_set_message_feedback)

    app = _make_app()
    client = TestClient(app)

    response = client.patch(
        "/v1/kb-1/sessions/sess-1/messages/chat-1/feedback",
        json={"feedback": None},
    )

    assert response.status_code == 200
    assert response.json()["feedback"] is None


def test_set_message_feedback_rejects_invalid_value(monkeypatch):
    app = _make_app()
    client = TestClient(app)

    response = client.patch(
        "/v1/kb-1/sessions/sess-1/messages/chat-1/feedback",
        json={"feedback": "meh"},
    )

    assert response.status_code == 422


def test_set_message_feedback_404s_when_chat_id_not_in_session(monkeypatch):
    async def _fake_set_message_feedback(knowledge_id, session_id, chat_id, feedback):
        return False
    monkeypatch.setattr(chat_router_module, "set_message_feedback", _fake_set_message_feedback)

    app = _make_app()
    client = TestClient(app)

    response = client.patch(
        "/v1/kb-1/sessions/sess-1/messages/wrong-chat-id/feedback",
        json={"feedback": "negative"},
    )

    assert response.status_code == 404
