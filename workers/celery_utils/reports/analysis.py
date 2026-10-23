"""Run the optional report-level analysis prompt.

Spec §5.4: after every query in a run completes, if the report had an
`analysis_prompt`, the worker generates a qualitative summary over the
collected Q/As and posts it back via `/internal/runs/{run_id}/analysis`.

Uses the same OpenAI client the enrichment pipeline already depends on.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from openai import OpenAI

from services import celery_setup_env
from services.celery_logger import logger


_MODEL = celery_setup_env.ENRICHMENT_MODEL or "gpt-5.4-mini"
_FALLBACK = celery_setup_env.ENRICHMENT_FALLBACK_MODEL or "gpt-5"
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
    client = OpenAI(api_key=celery_setup_env.OPENAI_API_KEY)
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are summarizing the output of an agent evaluation report. "
                    "Follow the user's instructions and produce a clear, qualitative "
                    "summary. Do not invent facts that are not in the transcript."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"INSTRUCTIONS:\n{prompt}\n\n"
                    f"REPORT TRANSCRIPT:\n{transcript}"
                ),
            },
        ],
    )
    return (completion.choices[0].message.content or "").strip()


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
