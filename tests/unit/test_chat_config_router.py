"""Unit tests for the chat config endpoints in routers/chat.py
(GET/PUT/DELETE /v1/{knowledge_id}/chat/config).

Pure unit tests: require_permission, resolve_chat_config and all three
chat/db/config_ops.py functions are monkeypatched — no real DB call in any
test. Separate file from test_chat_router.py because these routes take
"read"/"write" permissions rather than the chat turn's "use", so they need
their own dependency overrides (each require_permission(...) call builds a
fresh closure, and FastAPI keys dependency_overrides by the exact callable
object baked into the route at import time).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import chat.config as chat_config_module
import routers.chat as chat_router_module


def _make_app():
    """Mount the chat router with EVERY require_permission dependency
    overridden — the config routes use "read"/"write" while the chat turn
    uses "use", and all three are distinct closure objects."""
    app = FastAPI()
    app.include_router(chat_router_module.router)
    for route in chat_router_module.router.routes:
        for dep in route.dependant.dependencies:
            if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                app.dependency_overrides[dep.call] = lambda: None
    return app


def _resolved(**overrides):
    defaults = dict(
        llm_provider="openai", llm_model="gpt-5.4-mini", temperature=0.0,
        system_prompt=None, show_sources=True, chat_enabled=True, follow_up=True,
        suggested_questions=[], voice_input_enabled=False, voice_output_enabled=False,
    )
    defaults.update(overrides)
    return chat_config_module.ResolvedChatConfig(**defaults)


def _patch_resolve(monkeypatch, resolved=None):
    async def _fake_resolve_chat_config(knowledge_id, **kwargs):
        return resolved or _resolved()
    monkeypatch.setattr(chat_router_module, "resolve_chat_config", _fake_resolve_chat_config)


def _patch_get_row(monkeypatch, row):
    async def _fake_get_config_row(knowledge_id):
        return row
    monkeypatch.setattr(chat_router_module, "get_config_row", _fake_get_config_row)


class _FakeNodeRow:
    def __init__(self, node_name, provider=None, model=None, temperature=None,
                 max_tokens=None, prompt=None, enabled=True):
        self.node_name = node_name
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prompt = prompt
        self.enabled = enabled


@pytest.fixture(autouse=True)
def _patch_node_rows(monkeypatch):
    """The GET and DELETE handlers both read node rows. Without this the
    tests reach a real DB — they'd still pass (list_node_config_rows degrades
    to []), but on a live connection rather than a fake."""
    async def _fake_list(knowledge_id):
        return []
    monkeypatch.setattr(chat_router_module, "list_node_config_rows", _fake_list)

    async def _fake_delete_node(knowledge_id, node_name):
        return False
    monkeypatch.setattr(chat_router_module, "delete_node_config_row", _fake_delete_node)


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def test_get_config_with_no_row_returns_defaults_and_configured_false(monkeypatch):
    """A missing row is the normal case, NOT a 404 — chat works without one,
    so the resolved env/hardcoded values come back with configured=false."""
    _patch_resolve(monkeypatch)
    _patch_get_row(monkeypatch, None)

    response = TestClient(_make_app()).get("/v1/kb-1/chat/config")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert body["knowledge_id"] == "kb-1"
    assert body["llm_provider"] == "openai"
    assert body["llm_model"] == "gpt-5.4-mini"
    assert body["temperature"] == 0.0
    assert body["show_sources"] is True
    assert body["chat_enabled"] is True
    assert body["follow_up"] is True


def test_get_config_with_row_returns_configured_true_and_resolved_values(monkeypatch):
    from utils.models.db_tables import KnowledgeChatConfig
    row = KnowledgeChatConfig(knowledge_id="kb-1", llm_provider="anthropic")
    _patch_get_row(monkeypatch, row)
    _patch_resolve(monkeypatch, _resolved(
        llm_provider="anthropic", llm_model="claude-haiku-4-5", temperature=0.3,
        system_prompt="Be terse.", show_sources=False, follow_up=False,
    ))

    response = TestClient(_make_app()).get("/v1/kb-1/chat/config")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["llm_provider"] == "anthropic"
    assert body["llm_model"] == "claude-haiku-4-5"
    assert body["temperature"] == 0.3
    assert body["system_prompt"] == "Be terse."
    assert body["show_sources"] is False
    assert body["follow_up"] is False


def test_get_config_db_failure_returns_500(monkeypatch):
    _patch_resolve(monkeypatch)

    async def _boom(knowledge_id):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(chat_router_module, "get_config_row", _boom)

    response = TestClient(_make_app()).get("/v1/kb-1/chat/config")

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to retrieve chat configuration"


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------

def test_put_config_forwards_only_supplied_fields(monkeypatch):
    """Partial update: omitted fields must NOT reach upsert_config_row, so a
    caller setting llm_provider alone doesn't blank out temperature."""
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured["knowledge_id"] = knowledge_id
        captured["updates"] = updates
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config",
        json={"llm_provider": "anthropic", "llm_model": "claude-haiku-4-5"},
    )

    assert response.status_code == 200
    assert response.json()["knowledge_id"] == "kb-1"
    assert captured["knowledge_id"] == "kb-1"
    assert captured["updates"] == {"llm_provider": "anthropic", "llm_model": "claude-haiku-4-5"}


def test_put_config_preserves_explicit_falsy_values(monkeypatch):
    """temperature=0.0 and show_sources=False are legitimate explicit values,
    not "unset" — exclude_unset (not exclude_none/truthiness) must keep them."""
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured["updates"] = updates
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config",
        json={"temperature": 0.0, "show_sources": False, "chat_enabled": False, "follow_up": False},
    )

    assert response.status_code == 200
    assert captured["updates"] == {
        "temperature": 0.0, "show_sources": False, "chat_enabled": False, "follow_up": False,
    }


def test_put_config_explicit_null_clears_system_prompt(monkeypatch):
    """An explicitly-sent null is a real intent to clear, distinct from
    omitting the field — exclude_unset keeps it, exclude_none would drop it."""
    captured = {}

    async def _fake_upsert(knowledge_id, updates):
        captured["updates"] = updates
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config", json={"system_prompt": None},
    )

    assert response.status_code == 200
    assert captured["updates"] == {"system_prompt": None}


def test_put_config_empty_body_returns_400(monkeypatch):
    """A write that changes nothing must not report 200 success."""
    called = {"n": 0}

    async def _fake_upsert(knowledge_id, updates):
        called["n"] += 1
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_app()).put("/v1/kb-1/chat/config", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "No config fields supplied"
    assert called["n"] == 0


def test_put_config_db_failure_returns_500(monkeypatch):
    """Unlike the session/chat_log writes, a failed config write must surface
    as an error rather than degrade silently."""
    async def _boom(knowledge_id, updates):
        raise RuntimeError("unique violation")
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _boom)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config", json={"llm_provider": "openai"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to save chat configuration"


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def test_delete_config_removes_row(monkeypatch):
    captured = {}

    async def _fake_delete(knowledge_id):
        captured["knowledge_id"] = knowledge_id
        return True
    monkeypatch.setattr(chat_router_module, "delete_config_row", _fake_delete)

    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config")

    assert response.status_code == 200
    assert response.json()["message"] == "Chat configuration reset to defaults"
    assert captured["knowledge_id"] == "kb-1"


def test_delete_config_with_no_row_returns_404(monkeypatch):
    """404 lets a caller distinguish an actual reset from a no-op."""
    async def _fake_delete(knowledge_id):
        return False
    monkeypatch.setattr(chat_router_module, "delete_config_row", _fake_delete)

    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config")

    assert response.status_code == 404
    assert response.json()["detail"] == "No chat configuration exists for this knowledge_id"


def test_delete_config_db_failure_returns_500(monkeypatch):
    async def _boom(knowledge_id):
        raise RuntimeError("deadlock detected")
    monkeypatch.setattr(chat_router_module, "delete_config_row", _boom)

    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config")

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to delete chat configuration"


def test_delete_config_also_clears_node_rows(monkeypatch):
    """Reset-to-defaults must remove node overrides too — leaving them would
    keep overriding the very defaults the caller asked to fall back to."""
    deleted = []

    async def _fake_list(knowledge_id):
        return [_FakeNodeRow("rag_agent"), _FakeNodeRow("generate_answer")]
    monkeypatch.setattr(chat_router_module, "list_node_config_rows", _fake_list)

    async def _fake_delete_node(knowledge_id, node_name):
        deleted.append(node_name)
        return True
    monkeypatch.setattr(chat_router_module, "delete_node_config_row", _fake_delete_node)

    async def _fake_delete(knowledge_id):
        return True
    monkeypatch.setattr(chat_router_module, "delete_config_row", _fake_delete)

    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config")

    assert response.status_code == 200
    assert sorted(deleted) == ["generate_answer", "rag_agent"]


def test_delete_config_succeeds_when_only_node_rows_exist(monkeypatch):
    """404 means "nothing at all was configured" — a knowledge base with node
    rows but no knowledge-level row still has something to reset."""
    async def _fake_list(knowledge_id):
        return [_FakeNodeRow("rag_agent")]
    monkeypatch.setattr(chat_router_module, "list_node_config_rows", _fake_list)

    async def _fake_delete_node(knowledge_id, node_name):
        return True
    monkeypatch.setattr(chat_router_module, "delete_node_config_row", _fake_delete_node)

    async def _fake_delete(knowledge_id):
        return False
    monkeypatch.setattr(chat_router_module, "delete_config_row", _fake_delete)

    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Per-node config — nested in GET/PUT, plus the per-node DELETE
# ---------------------------------------------------------------------------

def test_get_returns_every_supported_node_even_when_unconfigured(monkeypatch):
    """The UI renders the form from this response, so all nodes must appear
    whether or not they have a row."""
    _patch_resolve(monkeypatch)
    _patch_get_row(monkeypatch, None)

    response = TestClient(_make_app()).get("/v1/kb-1/chat/config")

    assert response.status_code == 200
    nodes = response.json()["node_configs"]
    assert set(nodes) == set(chat_router_module.SUPPORTED_NODES)
    for state in nodes.values():
        assert state["configured"] is False
        assert state["provider"] is None
        assert state["model"] is None
        assert state["enabled"] is True
        # Effective values still resolve, even with nothing stored.
        assert state["effective_provider"] == "openai"


def test_get_reports_stored_override_and_effective_value(monkeypatch):
    _patch_resolve(monkeypatch)
    _patch_get_row(monkeypatch, None)

    async def _fake_list(knowledge_id):
        return [_FakeNodeRow("rag_agent", provider="anthropic",
                             model="claude-opus-4-6", prompt="Dig deep.")]
    monkeypatch.setattr(chat_router_module, "list_node_config_rows", _fake_list)

    response = TestClient(_make_app()).get("/v1/kb-1/chat/config")

    assert response.status_code == 200
    nodes = response.json()["node_configs"]
    assert nodes["rag_agent"]["configured"] is True
    assert nodes["rag_agent"]["provider"] == "anthropic"
    assert nodes["rag_agent"]["prompt"] == "Dig deep."
    assert nodes["generate_answer"]["configured"] is False


def test_put_writes_node_rows(monkeypatch):
    captured = []

    async def _fake_upsert_node(knowledge_id, node_name, updates):
        captured.append((node_name, updates))
        return None
    monkeypatch.setattr(chat_router_module, "upsert_node_config_row", _fake_upsert_node)

    async def _fake_upsert(knowledge_id, updates):
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config",
        json={"node_configs": {
            "rag_agent": {"provider": "anthropic", "model": "claude-opus-4-6"},
            "refined_query_node": {"prompt": "Rewrite tersely."},
        }},
    )

    assert response.status_code == 200
    by_node = dict(captured)
    assert by_node["rag_agent"] == {"provider": "anthropic", "model": "claude-opus-4-6"}
    assert by_node["refined_query_node"] == {"prompt": "Rewrite tersely."}


def test_put_with_only_node_configs_does_not_create_knowledge_row(monkeypatch):
    """A phantom knowledge-level row would make `configured` report true when
    only a node was configured."""
    called = {"flat": 0}

    async def _fake_upsert(knowledge_id, updates):
        called["flat"] += 1
        return None
    monkeypatch.setattr(chat_router_module, "upsert_config_row", _fake_upsert)

    async def _fake_upsert_node(knowledge_id, node_name, updates):
        return None
    monkeypatch.setattr(chat_router_module, "upsert_node_config_row", _fake_upsert_node)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config",
        json={"node_configs": {"rag_agent": {"model": "claude-opus-4-6", "provider": "anthropic"}}},
    )

    assert response.status_code == 200
    assert called["flat"] == 0


def test_put_node_explicit_null_clears_override(monkeypatch):
    """Explicit null clears an override so the field inherits again; an
    omitted field leaves the stored value alone."""
    captured = []

    async def _fake_upsert_node(knowledge_id, node_name, updates):
        captured.append((node_name, updates))
        return None
    monkeypatch.setattr(chat_router_module, "upsert_node_config_row", _fake_upsert_node)

    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config",
        json={"node_configs": {"rag_agent": {"prompt": None}}},
    )

    assert response.status_code == 200
    assert captured == [("rag_agent", {"prompt": None})]


def test_put_rejects_unknown_node_name(monkeypatch):
    response = TestClient(_make_app()).put(
        "/v1/kb-1/chat/config",
        json={"node_configs": {"supervisor": {"model": "gpt-5.4-mini"}}},
    )

    assert response.status_code == 422


def test_delete_node_config_removes_one_node(monkeypatch):
    captured = {}

    async def _fake_delete_node(knowledge_id, node_name):
        captured["args"] = (knowledge_id, node_name)
        return True
    monkeypatch.setattr(chat_router_module, "delete_node_config_row", _fake_delete_node)

    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config/nodes/rag_agent")

    assert response.status_code == 200
    assert captured["args"] == ("kb-1", "rag_agent")


def test_delete_node_config_404_when_absent(monkeypatch):
    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config/nodes/rag_agent")

    assert response.status_code == 404


def test_delete_node_config_422_on_unknown_node(monkeypatch):
    response = TestClient(_make_app()).delete("/v1/kb-1/chat/config/nodes/supervisor")

    assert response.status_code == 422
