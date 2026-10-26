"""Run the optional report-level analysis prompt.

Spec §5.4: after every query in a run completes, if the report had an
`analysis_prompt`, the worker generates a qualitative summary over the
collected Q/As and posts it back via `/internal/runs/{run_id}/analysis`.

Runs on the enrichment model through the enrichment LLM factory, so the provider
follows the model id (an ENRICHMENT_MODEL on Groq or Gemini is called on Groq or
Gemini). Until 2026-10-26 this built a raw OpenAI client for whatever
ENRICHMENT_MODEL named — a Groq model id sent to OpenAI always failed — and its
fallback default was gpt-5, which the platform OpenAI key cannot reach.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services import celery_setup_env
from services.celery_logger import logger


_MODEL = celery_setup_env.ENRICHMENT_MODEL or "gpt-5.4-mini"
_FALLBACK = celery_setup_env.ENRICHMENT_FALLBACK_MODEL or "gpt-5.4-nano"
_MAX_QA_CHARS = 60_000  # cap the transcript we send to the model


def _build_transcript(group_results: List[Dict[str, Any]]) -> str:
    """Flatten a list of {group_id, type, results:[{question, answer, error}]}
    blocks into a single readable transcript for the LLM.
    """
    out: List[str] = []
    for g in group_results:
        gtype = g.get("type", "independent")
        out.append(f"### Group ({gtype})")
        for r in g.get("results", []):
            q = (r.get("question") or "").strip()
            err = r.get("error")
            if err:
                out.append(f"Q: {q}\nERROR: {err}\n")
            else:
                a = (r.get("answer") or "").strip()
                out.append(f"Q: {q}\nA: {a}\n")
    text = "\n".join(out)
    if len(text) > _MAX_QA_CHARS:
        text = text[:_MAX_QA_CHARS] + "\n…[truncated]"
    return text


def _call_model(model: str, prompt: str, transcript: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from celery_utils.enrichment.llm_factory import get_enrichment_llm, get_provider_from_model

    llm, _key_type = get_enrichment_llm(None, get_provider_from_model(model), model, 0.0)
    response = llm.invoke(
        [
            SystemMessage(content=(
                "You are summarizing the output of an agent evaluation report. "
                "Follow the user's instructions and produce a clear, qualitative "
                "summary. Do not invent facts that are not in the transcript."
            )),
            HumanMessage(content=f"INSTRUCTIONS:\n{prompt}\n\nREPORT TRANSCRIPT:\n{transcript}"),
        ],
        config={"run_name": "report_analysis"},
    )
    content = response.content
    if isinstance(content, list):  # some providers return content blocks
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return (content or "").strip()


def run_analysis(
    analysis_prompt: str,
    group_results: List[Dict[str, Any]],
) -> Optional[str]:
    """Return the analysis summary, or None if generation failed entirely."""
    transcript = _build_transcript(group_results)
    try:
        return _call_model(_MODEL, analysis_prompt, transcript)
    except Exception as primary:
        logger.warning(
            f"[reports.analysis] primary model {_MODEL} failed ({primary}); "
            f"falling back to {_FALLBACK}"
        )
        try:
            return _call_model(_FALLBACK, analysis_prompt, transcript)
        except Exception as fallback:
            logger.error(f"[reports.analysis] fallback {_FALLBACK} also failed: {fallback}")
            return None
