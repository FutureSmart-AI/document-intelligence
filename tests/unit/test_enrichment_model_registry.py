"""Enrichment model registry variants on every API path (gpt-5.6-luna-low).

Production selects the extraction model through a config row (PATCH
/v1/admin/enrichment-config) or a collection's enrichment_model, never through the
worker env. ``gpt-5.6-luna-low`` is a registry *variant*: the wire model
``gpt-5.6-luna`` at reasoning effort "low". These tests pin that the API

- mirrors the worker registry exactly (the API image cannot import worker code),
- accepts the variant and refuses unknown names / wrong providers on both write paths,
- previews with the wire model at the variant's effort (never sends "gpt-5.6-luna-low"),
- prices the variant as its wire model in both price tables (never "no price").

Runs in the API image with workers/ mounted (CI "Enrichment model registry checks").
"""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from utils.enrichment import model_registry

ROOT = Path(__file__).resolve().parents[2]
WORKER_FACTORY = ROOT / "workers" / "celery_utils" / "enrichment" / "llm_factory.py"


def _worker_registry() -> dict:
    """ENRICHMENT_MODELS read from the worker source (a pure literal), without importing
    the worker package and its provider SDKs."""
    tree = ast.parse(WORKER_FACTORY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ENRICHMENT_MODELS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("ENRICHMENT_MODELS not found in the worker llm_factory.py")


# ── the mirror ───────────────────────────────────────────────────────────────

def test_api_registry_mirrors_the_worker_registry():
    worker = _worker_registry()
    assert set(model_registry.ENRICHMENT_MODELS) == set(worker)
    for name, cfg in worker.items():
        expected = model_registry.EnrichmentModel(
            cfg["provider"],
            cfg.get("api_model", name),
            cfg.get("reasoning_effort") if cfg.get("supports_reasoning_effort") else None,
        )
        assert model_registry.ENRICHMENT_MODELS[name] == expected, name


def test_luna_low_is_a_variant_and_plain_luna_is_unchanged():
    assert model_registry.get("gpt-5.6-luna-low") == ("openai", "gpt-5.6-luna", "low")
    # Plain luna is also the rich-mode extraction model; it must stay at "none".
    assert model_registry.get("gpt-5.6-luna") == ("openai", "gpt-5.6-luna", "none")
    assert model_registry.wire_model("gpt-5.6-luna-low") == "gpt-5.6-luna"
    assert model_registry.wire_model("gpt-5.6-luna") == "gpt-5.6-luna"
    assert model_registry.wire_model("google/gemini-3.1-flash-lite") == "google/gemini-3.1-flash-lite"
    assert model_registry.wire_model("not-a-model") == "not-a-model"


# ── validation ───────────────────────────────────────────────────────────────

def test_validate_accepts_known_names_and_fills_the_provider():
    assert model_registry.validate("gpt-5.6-luna-low") == ("gpt-5.6-luna-low", "openai")
    assert model_registry.validate("gpt-5.6-luna-low", "openai") == ("gpt-5.6-luna-low", "openai")
    # The spelling the live row has carried stays acceptable, and is stored as sent.
    assert model_registry.validate("google/gemini-3.1-flash-lite", "gemini") == (
        "google/gemini-3.1-flash-lite", "google")
    assert model_registry.validate("openai/gpt-oss-120b") == ("openai/gpt-oss-120b", "groq")


@pytest.mark.parametrize("model,provider", [
    ("gpt-5.6-luna-lowest", None),
    ("gpt-9", "openai"),
    ("gpt-5.6-luna-low", "groq"),
    ("openai/gemini-3.1-flash-lite", None),
    ("", None),
])
def test_validate_refuses_unknown_names_and_wrong_providers(model, provider):
    with pytest.raises(ValueError):
        model_registry.validate(model, provider)


def _patch_admin(monkeypatch, current=None):
    import routers.admin_config as admin

    stored = {}

    async def _update(**kwargs):
        stored.update(kwargs)
        row = dict(current or {})
        row.update({k: v for k, v in kwargs.items() if v is not None})
        return {"enrichment_temperature": 0.0, **row}

    async def _current():
        return dict(current or {})

    monkeypatch.setattr(admin, "update_enrichment_default_config", _update)
    monkeypatch.setattr(admin, "get_enrichment_default_config", _current)
    return admin, stored


CURRENT_ROW = {
    "enrichment_model": "google/gemini-3.1-flash-lite", "enrichment_provider": "google",
    "enrichment_temperature": 0.0,
    "enrichment_fallback_model": "gpt-5.4-nano", "enrichment_fallback_provider": "openai",
}


def test_patch_enrichment_config_accepts_luna_low_with_the_registry_provider(monkeypatch):
    admin, stored = _patch_admin(monkeypatch, CURRENT_ROW)
    body = admin.PatchEnrichmentDefaultConfigBody(
        enrichment_model="gpt-5.6-luna-low", enrichment_fallback_model="gpt-5.6-luna-low")
    out = asyncio.run(admin.patch_enrichment_default(body))
    # The previous row's provider (google) must not be inherited by an OpenAI model.
    assert (stored["enrichment_model"], stored["enrichment_provider"]) == ("gpt-5.6-luna-low", "openai")
    assert (stored["enrichment_fallback_model"], stored["enrichment_fallback_provider"]) == (
        "gpt-5.6-luna-low", "openai")
    assert out.enrichment_model == "gpt-5.6-luna-low" and out.enrichment_provider == "openai"


@pytest.mark.parametrize("body", [
    {"enrichment_model": "gpt-5.6-luna-lowest"},
    {"enrichment_fallback_model": "gpt-9"},
    {"enrichment_model": "gpt-5.6-luna-low", "enrichment_provider": "google"},
    # A provider sent alone is checked against the model it will pair with.
    {"enrichment_provider": "openai"},
])
def test_patch_enrichment_config_refuses_what_the_worker_cannot_run(monkeypatch, body):
    admin, stored = _patch_admin(monkeypatch, CURRENT_ROW)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.patch_enrichment_default(admin.PatchEnrichmentDefaultConfigBody(**body)))
    assert exc.value.status_code == 422
    assert stored == {}  # nothing written


def _patch_collection(monkeypatch, existing=None):
    import routers.knowledge_config_new as kc

    saved = {}

    async def _get(_kid):
        return dict(existing or {})

    async def _save(**kwargs):
        saved.update(kwargs)

    async def _init(*args, **kwargs):
        return None

    monkeypatch.setattr(kc, "get_knowledge_config", _get)
    monkeypatch.setattr(kc, "save_knowledge_config", _save)
    monkeypatch.setattr(kc, "initialize_field_configurations_by_template", _init)
    return kc, saved


def test_collection_config_accepts_luna_low(monkeypatch):
    kc, saved = _patch_collection(monkeypatch, {"embedding_model": "openai-text-embedding-3-small"})
    body = kc.UpdateKnowledgeConfigBody(enrichment_model="gpt-5.6-luna-low")
    asyncio.run(kc.update_knowledge_configuration_v2("kid-1", body, None))
    assert (saved["enrichment_model"], saved["enrichment_provider"]) == ("gpt-5.6-luna-low", "openai")


@pytest.mark.parametrize("body,existing", [
    ({"enrichment_model": "gpt-5.6-luna-lowest"}, {}),
    ({"enrichment_model": "gpt-5.6-luna-low", "enrichment_provider": "anthropic"}, {}),
    ({"enrichment_provider": "groq"}, {"enrichment_model": "gpt-5.6-luna-low"}),
])
def test_collection_config_refuses_unknown_models(monkeypatch, body, existing):
    kc, saved = _patch_collection(monkeypatch, existing)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(kc.update_knowledge_configuration_v2("kid-1", kc.UpdateKnowledgeConfigBody(**body), None))
    assert exc.value.status_code == 422
    assert saved == {}


# ── collection preview ───────────────────────────────────────────────────────

def test_preview_calls_the_wire_model_at_the_variant_effort(monkeypatch):
    import chat.byok_fallback as byok_fallback
    import utils.enrichment.preview_run as preview_run

    async def _models():
        return preview_run.PreviewModels("openai", "gpt-5.6-luna-low", 0.0, "openai", "gpt-5.4-nano", "global")

    async def _noop(*args, **kwargs):
        return None

    recorded = {}

    async def _record(**kwargs):
        recorded.update(kwargs)

    calls = []

    async def _fake_run(call, **kwargs):
        calls.append(kwargs)

        class _LLM:
            def with_structured_output(self, schema, include_raw=False):
                from langchain_core.runnables import RunnableLambda

                def _respond(_prompt):
                    extraction = schema.model_fields["extracted"].annotation
                    parsed = schema(extracted=extraction(full_name="Ada"), suggested_fields=[])
                    raw = SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 3})
                    return {"raw": raw, "parsed": parsed} if include_raw else parsed

                return RunnableLambda(_respond)

        return byok_fallback.ByokOutcome(await call(_LLM()), "openai", "platform-key")

    monkeypatch.setattr(preview_run, "_resolve_preview_model", _models)
    monkeypatch.setattr(preview_run, "_enforce_llm_quota", _noop)
    monkeypatch.setattr(preview_run, "_record_preview_usage", _record)
    monkeypatch.setattr(byok_fallback, "run_with_key_fallback", _fake_run)

    body = asyncio.run(preview_run.run_preview(
        owner_id="owner@example.test", document_text="Ada Lovelace, engineer.", file_name="cv.txt",
        fields=[{"field_name": "full_name", "field_type": "str", "description": "Full name"}],
        document_processing_mode="text_based", text_truncated=False))
    assert len(calls) == 1
    assert (calls[0]["model"], calls[0]["reasoning_effort"]) == ("gpt-5.6-luna", "low")
    run = body["run"]
    assert (run["model"], run["reasoning_effort"], run["requested_model"], run["fallback"]) == (
        "gpt-5.6-luna", "low", "gpt-5.6-luna-low", False)
    assert (recorded["model"], recorded["reasoning_effort"]) == ("gpt-5.6-luna", "low")


def test_preview_wire_call_for_non_variants():
    import utils.enrichment.preview_run as preview_run

    assert preview_run._wire_call("gpt-5.4-nano") == ("gpt-5.4-nano", "medium")
    assert preview_run._wire_call("google/gemini-3.1-flash-lite") == ("google/gemini-3.1-flash-lite", None)
    assert preview_run._wire_call("gpt-4.1-mini") == ("gpt-4.1-mini", None)


def test_chat_factory_sends_a_pinned_effort_on_the_responses_api():
    import chat.llm_factory as factory

    llm = factory._create_llm("openai", "gpt-5.6-luna", 0.0, 4000, "sk-offline-test", False,
                              for_function_calling=True, reasoning_effort="low")
    assert llm.model_name == "gpt-5.6-luna"
    assert llm.reasoning == {"effort": "low"}
    assert llm.reasoning_effort is None
    # Unpinned callers (chat) keep the chat default.
    llm = factory._create_llm("openai", "gpt-5.6-luna", 0.0, 4000, "sk-offline-test", False,
                              for_function_calling=True)
    assert llm.reasoning is None and llm.reasoning_effort == "none"


# ── pricing ──────────────────────────────────────────────────────────────────

def test_variant_prices_as_its_wire_model():
    from chat import pricing

    luna = pricing.estimate_cost("openai", "gpt-5.6-luna", 10000, 800, 2000)
    assert luna is not None
    assert pricing.estimate_cost("openai", "gpt-5.6-luna-low", 10000, 800, 2000) == luna


def test_processing_telemetry_with_a_variant_is_cost_complete():
    from utils.processing_telemetry import price_processing_telemetry

    # What the worker records: the wire model, the configured name and the effort.
    raw = {"operations": [{
        "operation": "extraction", "provider": "openai", "model": "gpt-5.6-luna",
        "requested_model": "gpt-5.6-luna-low", "configured_model": "gpt-5.6-luna-low",
        "reasoning_effort": "low", "fallback": False, "calls": 1,
        "input_tokens": 10000, "cached_input_tokens": 0, "output_tokens": 800, "latency_ms": 5700}]}
    view = price_processing_telemetry(raw)
    op = view["operations"][0]
    assert view["cost_complete"] is True and view["unpriced_models"] == []
    assert op["estimated_cost_usd"] is not None
    assert (op["model"], op["requested_model"], op["reasoning_effort"]) == (
        "gpt-5.6-luna", "gpt-5.6-luna-low", "low")
    # A record that names the variant itself still prices.
    raw["operations"][0]["model"] = "gpt-5.6-luna-low"
    assert price_processing_telemetry(raw)["cost_complete"] is True


def test_usage_price_lookup_resolves_the_variant():
    from utils.usage.cost_calculator import CostCalculator

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _Session:
        async def execute(self, _query):
            return _Result()

    calc = CostCalculator(_Session())
    asyncio.run(calc.get_pricing("gpt-5.6-luna-low", "openai", "llm"))
    assert any(k.startswith("openai:gpt-5.6-luna:llm:") for k in calc.pricing_cache), calc.pricing_cache
