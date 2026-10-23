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
