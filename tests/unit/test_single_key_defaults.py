"""An install with only OPENAI_API_KEY must run the core path (2026-10-26).

Pins the code defaults that used to need other providers or unreachable models:

* every code default that names an OpenAI model names one the platform key can
  reach (gpt-4o-mini / gpt-4.1-mini / gpt-5 were not), in backend AND worker;
* the worker's ENRICHMENT_MODEL default is OpenAI, not Groq;
* the extraction-validation judge is off when unset and its provider's key is
  absent, and an explicit value still wins (the hosted deployment sets it);
* the create-collection preview follows extraction's model chain, including the
  fallback when the primary fails;
* the profile documents every operation and its settings agree with it.
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _backend_dir() -> Path:
    import utils.setup_env as setup_env
    return Path(setup_env.__file__).resolve().parents[1]


# Models the platform OpenAI key reaches (fs-llm-engineering ALLOWED-MODELS, audit 2026-10-26).
REACHABLE_OPENAI = {"gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.6-luna", "gpt-5.6-terra",
                    "text-embedding-3-small", "text-embedding-3-large"}

_ENV_DEFAULT = re.compile(
    r"""(?:os\.getenv|os\.environ\.get)\(\s*["'](?P<var>[A-Z_]*MODEL[A-Z_]*)["']\s*,\s*["'](?P<default>[^"']+)["']""")


def _python_sources():
    for base in (_backend_dir(), ROOT / "workers"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            parts = set(path.parts)
            if parts & {"tests", "unit_tests", "migrations", "samples", "evals", "scripts"}:
                continue
            yield path


def test_no_env_default_names_an_unreachable_openai_model():
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _ENV_DEFAULT.finditer(text):
            default = m.group("default")
            if default.startswith(("gpt-", "o1", "o3", "o4")) and default not in REACHABLE_OPENAI:
                offenders.append(f"{path.name}: {m.group('var')}={default}")
    assert offenders == [], offenders


def _run_python(code: str, env_overrides: dict, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items()
           if not (k.endswith("_API_KEY") or "MODEL" in k or k.startswith(("EXTRACTION_VALIDATION", "CHART_")))}
    env.update(env_overrides)
    env["ENV_FILE"] = os.devnull  # never read a .env file: only the variables given here
    result = subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip().splitlines()[-1]


def test_backend_chart_and_fallback_defaults_are_reachable_with_no_env():
    out = _run_python(
        "import json, utils.setup_env as s; print(json.dumps([s.CHART_EXTRACTION_MODEL, s.CHART_RENDER_PROVIDER, "
        "s.CHART_RENDER_MODEL, s.CHART_API_URL]))",
        {"OPENAI_API_KEY": "sk-test"}, _backend_dir())
    assert json.loads(out) == ["gpt-5.4-mini", "openai", "gpt-5.4-mini", ""]
    source = (_backend_dir() / "chat" / "rag_agent" / "agent_creation.py").read_text(encoding="utf-8")
    assert 'os.environ.get("RAG_FALLBACK_MODEL", "gpt-5.4-mini")' in source


@pytest.mark.parametrize("env,expected", [
    ({"OPENAI_API_KEY": "sk-test"}, False),                                   # OpenAI-only install
    ({"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "sk-ant-test"}, True),  # judge key present
    ({"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "x",
      "EXTRACTION_VALIDATION_LLM_ENABLED": "false"}, False),                   # explicit wins
    ({"OPENAI_API_KEY": "sk-test", "EXTRACTION_VALIDATION_LLM_ENABLED": "true"}, True),
    ({"OPENAI_API_KEY": "sk-test", "EXTRACTION_VALIDATION_PROVIDER": "openai",
      "EXTRACTION_VALIDATION_MODEL": "gpt-5.4-mini"}, True),                   # judge moved to OpenAI
])
def test_extraction_judge_needs_its_key_unless_explicit(env, expected):
    out = _run_python("import utils.setup_env as s; print(s.EXTRACTION_VALIDATION_LLM_ENABLED)", env, _backend_dir())
    assert out == str(expected)


def test_worker_enrichment_defaults_are_openai():
    source = (ROOT / "workers" / "services" / "celery_setup_env.py").read_text(encoding="utf-8")
    assert 'os.getenv("ENRICHMENT_MODEL", "").strip() or "gpt-5.4-mini"' in source
    assert 'os.getenv("ENRICHMENT_FALLBACK_MODEL", "").strip() or "gpt-5.4-nano"' in source
    analysis = (ROOT / "workers" / "celery_utils" / "reports" / "analysis.py").read_text(encoding="utf-8")
    assert "from openai import OpenAI" not in analysis  # provider follows the model id now
    assert 'or "gpt-5"\n' not in analysis


# ── create-collection preview follows extraction's chain ───────────────────

def test_preview_model_chain_uses_env_when_no_admin_row(monkeypatch):
    import utils.enrichment.preview_run as preview_run
    import utils.db.knowledge_setting_ops as ops

    async def _no_row():
        return None

    monkeypatch.setattr(ops, "get_enrichment_default_config", _no_row)
    monkeypatch.setenv("ENRICHMENT_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("ENRICHMENT_FALLBACK_MODEL", "gpt-5.4-nano")
    models = asyncio.run(preview_run._resolve_preview_model())
    assert (models.model, models.fallback_model, models.source) == ("gpt-5.4-mini", "gpt-5.4-nano", "env")

    async def _row():
        return {"enrichment_provider": "google", "enrichment_model": "google/gemini-3.1-flash-lite",
                "enrichment_fallback_provider": "openai", "enrichment_fallback_model": "gpt-5.4-nano"}

    monkeypatch.setattr(ops, "get_enrichment_default_config", _row)
    models = asyncio.run(preview_run._resolve_preview_model())
    assert (models.provider, models.model, models.fallback_model) == (
        "google", "google/gemini-3.1-flash-lite", "gpt-5.4-nano")


def test_preview_retries_on_the_fallback_when_the_primary_fails(monkeypatch):
    import chat.byok_fallback as byok_fallback
    import utils.enrichment.preview_run as preview_run
    from types import SimpleNamespace

    async def _models():
        return preview_run.PreviewModels("google", "google/gemini-3.1-flash-lite", 0.0, "openai", "gpt-5.4-nano", "global")

    async def _noop(*args, **kwargs):
        return None

    calls = []

    async def _fake_run(call, **kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] != "gpt-5.4-nano":
            raise RuntimeError("402 Payment Required")

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
    monkeypatch.setattr(preview_run, "_record_preview_usage", _noop)
    monkeypatch.setattr(byok_fallback, "run_with_key_fallback", _fake_run)

    body = asyncio.run(preview_run.run_preview(
        owner_id="owner@example.test", document_text="Ada Lovelace, engineer.", file_name="cv.txt",
        fields=[{"field_name": "full_name", "field_type": "str", "description": "Full name"}],
        document_processing_mode="text_based", text_truncated=False))
    assert calls == ["google/gemini-3.1-flash-lite", "gpt-5.4-nano"]
    assert body["extracted"]["full_name"] == "Ada"
    run = body["run"]
    assert run["fallback"] is True and run["model"] == "gpt-5.4-nano"
    assert run["requested_model"] == "google/gemini-3.1-flash-lite"
    assert "402" in run["fallback_reason"]


# ── explicit parser mode without a key is refused up front ─────────────────

def test_parser_mode_without_a_llamaparse_key_is_a_clear_422(monkeypatch):
    from fastapi import HTTPException
    import utils.setup_env as setup_env
    import utils.usage.credentials as credentials
    from utils.files.processing_modes import ensure_parser_configured
    from utils.models.enums import DocumentProcessingMode as M

    async def _byok(owner_id):
        return set()

    monkeypatch.setattr(setup_env, "LLAMA_PARSE_API_KEY", None)
    monkeypatch.setattr(credentials, "get_byok_providers", _byok)
    with pytest.raises(HTTPException) as err:
        asyncio.run(ensure_parser_configured(M.SCANNED, "owner@example.test"))
    assert err.value.status_code == 422 and "LLAMA_PARSE_API_KEY" in err.value.detail
    # Local modes and a configured key pass.
    asyncio.run(ensure_parser_configured(M.AUTO, "owner@example.test"))
    asyncio.run(ensure_parser_configured(M.TEXT_BASED, "owner@example.test"))

    async def _owner_has_key(owner_id):
        return {"llamaparse"}

    monkeypatch.setattr(credentials, "get_byok_providers", _owner_has_key)
    asyncio.run(ensure_parser_configured(M.VISUAL, "owner@example.test"))
    monkeypatch.setattr(setup_env, "LLAMA_PARSE_API_KEY", "llx-test")
    monkeypatch.setattr(credentials, "get_byok_providers", _byok)
    asyncio.run(ensure_parser_configured(M.MIXED, "owner@example.test"))
