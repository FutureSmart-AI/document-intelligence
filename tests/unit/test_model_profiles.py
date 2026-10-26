"""deploy/model-profiles.json: every profile names models the product can run."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "deploy" / "model-profiles.json").read_text(encoding="utf-8"))
ENRICHMENT_FACTORY = (ROOT / "workers" / "celery_utils" / "enrichment" / "llm_factory.py").read_text(encoding="utf-8")


def test_default_profile_needs_only_openai():
    default = CATALOG["profiles"][CATALOG["default"]]
    assert default["required_keys"] == ["OPENAI_API_KEY"]
    assert default["settings"]["EXTRACTION_VALIDATION_LLM_ENABLED"] == "false"


def test_profile_enrichment_models_are_registered():
    for name, profile in CATALOG["profiles"].items():
        for key in ("ENRICHMENT_MODEL", "ENRICHMENT_FALLBACK_MODEL"):
            model = profile["settings"][key]
            assert f'"{model}": {{' in ENRICHMENT_FACTORY, f"{name}.{key}={model} missing from ENRICHMENT_MODELS"


def _configure(tmp_path, env, *args):
    (tmp_path / "deploy").mkdir()
    for name in ("configure.py", "model-profiles.json"):
        (tmp_path / "deploy" / name).write_bytes((ROOT / "deploy" / name).read_bytes())
    clean = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    return subprocess.run([sys.executable, str(tmp_path / "deploy" / "configure.py"), *args],
                          env={**clean, **env}, capture_output=True, text=True)


def test_configure_writes_default_profile_with_one_key(tmp_path):
    result = _configure(tmp_path, {"OPENAI_API_KEY": "sk-test-placeholder"})
    assert result.returncode == 0, result.stderr
    env = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines())
    assert env["MODEL_PROFILE"] == "openai-v1"
    assert env["ENRICHMENT_MODEL"] == "gpt-5.4-mini"
    assert env["GROQ_API_KEY"] == ""


def test_configure_refuses_profile_without_its_keys(tmp_path):
    result = _configure(tmp_path, {"OPENAI_API_KEY": "sk-test-placeholder"}, "--profile", "openai-groq-v1")
    assert result.returncode != 0
    assert "GROQ_API_KEY" in result.stderr
    assert not (tmp_path / ".env").exists()


# ── 2026-10-26: per-operation documentation and profile inheritance ──────────

REACHABLE_OPENAI = {"gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.6-luna", "gpt-5.6-terra",
                    "text-embedding-3-small", "text-embedding-3-large"}
CORE_OPERATIONS = {"extraction", "collection_preview", "embeddings", "query_refinement", "retrieval_agent",
                   "retrieval_verifier", "answer", "follow_up", "chart_spec", "chart_render"}


def _load_configure():
    import importlib.util
    spec = importlib.util.spec_from_file_location("di_configure", ROOT / "deploy" / "configure.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_default_operation_states_provider_model_and_fallback():
    profile = CATALOG["profiles"][CATALOG["default"]]
    operations = profile["operations"]
    assert CORE_OPERATIONS <= set(operations), CORE_OPERATIONS - set(operations)
    for name, op in operations.items():
        for key in ("service", "provider", "model", "fallback", "settings"):
            assert key in op, f"{name} lacks {key}"
        for setting in op["settings"]:
            assert setting in profile["settings"], f"{name} names {setting}, which the profile does not set"


def test_core_path_runs_on_openai_models_the_key_reaches():
    profile = CATALOG["profiles"][CATALOG["default"]]
    for name in CORE_OPERATIONS:
        op = profile["operations"][name]
        assert op["provider"] == "openai" and op["model"] in REACHABLE_OPENAI, (name, op)
        fallback = op["fallback"]
        if fallback and fallback.get("provider") == "openai":
            assert fallback["model"] in REACHABLE_OPENAI, (name, fallback)


def test_profile_settings_agree_with_the_operations_they_document():
    s = CATALOG["profiles"]["openai-v1"]["settings"]
    ops = CATALOG["profiles"]["openai-v1"]["operations"]
    assert s["ENRICHMENT_MODEL"] == ops["extraction"]["model"] == ops["collection_preview"]["model"]
    assert s["ENRICHMENT_FALLBACK_MODEL"] == ops["extraction"]["fallback"]["model"]
    assert s["RAG_CHAT_REFINED_QUERY_LLM_MODEL"] == ops["query_refinement"]["model"]
    assert s["RAG_CHAT_AGENT_LLM_MODEL"] == ops["retrieval_agent"]["model"]
    assert s["RAG_FALLBACK_MODEL"] == ops["retrieval_agent"]["fallback"]["model"]
    assert s["RAG_RETRIEVAL_VERIFIER_MODEL"] == ops["retrieval_verifier"]["model"]
    assert s["RAG_CHAT_ANSWER_LLM_MODEL"] == ops["answer"]["model"]
    assert s["FOLLOW_UP_LLM_MODEL"] == ops["follow_up"]["model"]
    assert s["CHART_EXTRACTION_MODEL"] == ops["chart_spec"]["model"]
    assert s["CHART_RENDER_MODEL"] == ops["chart_render"]["model"]


def test_extends_inherits_settings_and_operations():
    configure = _load_configure()
    name, groq = configure.load_profile("openai-groq-v1")
    base = CATALOG["profiles"]["openai-v1"]
    assert groq["settings"]["ENRICHMENT_MODEL"] == "openai/gpt-oss-120b"
    assert groq["settings"]["RAG_CHAT_ANSWER_LLM_MODEL"] == base["settings"]["RAG_CHAT_ANSWER_LLM_MODEL"]
    assert groq["operations"]["extraction"]["provider"] == "groq"
    assert groq["operations"]["answer"] == base["operations"]["answer"]
    assert groq["required_keys"] == ["OPENAI_API_KEY", "GROQ_API_KEY"]


def test_configure_writes_every_profile_setting_and_no_operations(tmp_path):
    result = _configure(tmp_path, {"OPENAI_API_KEY": "sk-test-placeholder"})
    assert result.returncode == 0, result.stderr
    env = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines())
    for key, value in CATALOG["profiles"]["openai-v1"]["settings"].items():
        assert env[key] == value, key
    assert "operations" not in env
    # Optional provider keys are present only as empty placeholders.
    for key in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "LLAMA_PARSE_API_KEY", "FIRECRAWL_API_KEY"):
        assert env[key] == "", key
