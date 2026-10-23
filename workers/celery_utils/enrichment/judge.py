"""
LLM-as-judge for enrichment quality.

Scores each extracted metadata field against the source document on three
dimensions — grounding, completeness, relevance — using a judge model from a
DIFFERENT provider than the extractor, so judge and extractor don't share
correlated failure modes.

Design reference: claude-logs/plan/enrichment-llm-judge.md

This module is evaluation-only: it never blocks, rejects, or re-runs enrichment.
It is consumed by testing/eval_enrichment_langsmith.py (offline LangSmith
experiments). The production fire-and-forget Celery path described in the plan
is not wired up here.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from celery_utils.enrichment.llm_factory import get_enrichment_llm
from celery_utils.enrichment.reference_match import invoke_with_backoff
from services.celery_logger import logger


# Judge runs on Anthropic while extraction runs on OpenAI/Groq — a different
# provider makes agreement-by-shared-bias much less likely.
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_JUDGE_PROVIDER = "anthropic"

# Source text sent to the judge. Resumes are 1-3 pages so this is rarely hit,
# but it bounds cost on long documents.
_MAX_SOURCE_CHARS = 16_000

# Values the extractor emits when it found nothing. Treated as "field is empty"
# rather than as a real extracted value — mirrors the strategies' own checks.
_EMPTY_SENTINELS = {"", "not_found", "none", "n/a", "null"}


# --- Output schemas ---

class FieldJudgment(BaseModel):
    """One judge verdict per extracted field."""

    grounding: Literal["SUPPORTED", "PARTIAL", "NOT_SUPPORTED", "NOT_APPLICABLE"] = Field(
        description="Is the extracted value supported by the source text?"
    )
    completeness: Literal["COMPLETE", "PARTIAL", "MISSING", "NOT_APPLICABLE"] = Field(
        description="Did the extractor capture what the source actually offered?"
    )
    relevance: Literal["HIGH", "MEDIUM", "LOW", "NOT_APPLICABLE"] = Field(
        description="Does the value answer what the field description asks for?"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Judge's self-reported confidence across all three verdicts.",
    )
    reason: str = Field(
        default="", description="One line covering all three dimensions."
    )


class _JudgeResponse(BaseModel):
    """Raw structured output from the judge LLM."""

    field_judgments: Dict[str, FieldJudgment] = Field(
        description="One entry per field, keyed by the exact field name given in the prompt."
    )


class JudgmentResult(BaseModel):
    """Scored, aggregated judge output for one document."""

    field_judgments: Dict[str, FieldJudgment] = {}
    flagged_fields: List[str] = []
    overall_grounding_score: float = 0.0
    overall_completeness_score: float = 0.0
    overall_relevance_score: float = 0.0
    judge_model: str = DEFAULT_JUDGE_MODEL
    judge_provider: str = DEFAULT_JUDGE_PROVIDER
    fields_judged: int = 0
    duration_ms: int = 0
    error: Optional[str] = None


# --- Scoring ---

# NOT_APPLICABLE is deliberately absent: those fields are excluded from the mean
# rather than scored as 0, so a resume with no "certifications" section doesn't
# drag the score down for correctly leaving the field empty.
_VERDICT_SCORES: Dict[str, float] = {
    "SUPPORTED": 1.0, "PARTIAL": 0.5, "NOT_SUPPORTED": 0.0,
    "COMPLETE": 1.0, "MISSING": 0.0,
    "HIGH": 1.0, "MEDIUM": 0.5, "LOW": 0.0,
}


def _mean_score(verdicts: List[str]) -> float:
    """Mean of scoreable verdicts. Returns 1.0 when every field was NOT_APPLICABLE."""
    scores = [_VERDICT_SCORES[v] for v in verdicts if v in _VERDICT_SCORES]
    if not scores:
        # Nothing to judge (all fields legitimately empty) — not a quality failure.
        return 1.0
    return sum(scores) / len(scores)


def _is_flagged(j: FieldJudgment) -> bool:
    """A field is flagged if any dimension shows a real quality failure."""
    return (
        j.grounding == "NOT_SUPPORTED"      # hallucination
        or j.completeness == "MISSING"      # extraction miss
        or j.relevance == "LOW"             # off-target value
    )


def _is_empty(value: Any) -> bool:
    """True when the extractor produced no usable value for this field."""
    if value is None:
        return False if isinstance(value, bool) else True
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_SENTINELS
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


# --- Prompt construction ---

_SYSTEM_PROMPT = """You are an extraction quality evaluator. A separate LLM extracted \
metadata fields from a source document. Your job is to judge that extraction — not to \
redo it.

For EVERY field listed, return three verdicts:

1. GROUNDING — is the extracted value actually supported by the source text?
   SUPPORTED      = explicitly stated, or a direct and safe inference
   PARTIAL        = partly present, or requires a significant leap
   NOT_SUPPORTED  = absent from, or contradicted by, the source (a hallucination)
   NOT_APPLICABLE = the field is empty/null (nothing to ground)

2. COMPLETENESS — did the extractor capture what the source actually offered?
   COMPLETE       = captures the available content for this field
   PARTIAL        = correct as far as it goes, but the source had more
   MISSING        = field is empty BUT the source clearly contained extractable content
   NOT_APPLICABLE = field is empty AND the source genuinely has no such content

3. RELEVANCE — does the value answer what the field's description asks for?
   HIGH           = directly answers the field description
   MEDIUM         = partially answers it
   LOW            = tangential, wrong category, or off-topic
   NOT_APPLICABLE = the field is empty

CRITICAL DISTINCTIONS:
- NOT_SUPPORTED applies only to NON-EMPTY fields — it means the value was invented.
- MISSING applies only to EMPTY fields — it means the extractor failed to find content
  that was there. If the field is empty and the source truly has nothing relevant,
  that is NOT_APPLICABLE on all three dimensions and is CORRECT behaviour, not a failure.
- Do not penalise a field for being empty when the document has no such information.
- Judge against the field's stated description and constraints, not your own idea of
  what the field should contain.

Return one entry per field, keyed by the EXACT field name given."""


def _format_value(value: Any) -> str:
    """Render an extracted value compactly for the prompt."""
    if _is_empty(value):
        return "<EMPTY>"
    if isinstance(value, str):
        return value if len(value) <= 600 else value[:600] + "…"
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = str(value)
    return rendered if len(rendered) <= 600 else rendered[:600] + "…"


def _format_field_block(
    field_name: str,
    value: Any,
    config: Optional[Any] = None,
) -> str:
    """
    One prompt block per field: name, description, constraints, extracted value.

    Passing the real FieldConfiguration constraints (type, allowed values, caps)
    lets the judge evaluate against the field's actual contract rather than
    guessing intent from the field name.
    """
    lines = [f"### {field_name}"]

    if config is not None:
        description = getattr(config, "description", None)
        field_type = getattr(config, "field_type", None)
        sample_values = getattr(config, "sample_values", None)
        restrict = getattr(config, "restrict_to_choices", False)
        max_items = getattr(config, "max_items", None)

        if description:
            lines.append(f"description: {description}")
        if field_type:
            lines.append(f"type: {field_type}")
        if sample_values:
            label = "allowed values (value MUST be one of these)" if restrict else "example values"
            lines.append(f"{label}: {', '.join(str(v) for v in sample_values)}")
        if max_items is not None:
            lines.append(f"max items: {max_items}")

    lines.append(f"extracted value: {_format_value(value)}")
    return "\n".join(lines)


def build_judge_prompt(
    source_text: str,
    extracted: Dict[str, Any],
    field_configs: Optional[List[Any]] = None,
) -> str:
    """
    Build the judge user message.

    Source text comes FIRST so the judge reads the evidence before seeing the
    claims — this reduces the tendency to rubber-stamp whatever was extracted.
    """
    configs_by_name = {
        getattr(c, "field_name", None): c
        for c in (field_configs or [])
        if getattr(c, "field_name", None)
    }

    truncated = (source_text or "")[:_MAX_SOURCE_CHARS]
    truncation_note = (
        "\n\n[NOTE: source truncated for length — do not mark fields MISSING solely "
        "because content appears absent near the end]"
        if len(source_text or "") > _MAX_SOURCE_CHARS
        else ""
    )

    field_blocks = "\n\n".join(
        _format_field_block(name, value, configs_by_name.get(name))
        for name, value in extracted.items()
    )

    return (
        "## SOURCE DOCUMENT\n"
        f"{truncated}{truncation_note}\n\n"
        "## FIELDS TO EVALUATE\n"
        f"{field_blocks}\n\n"
        "Evaluate every field above and return your verdicts."
    )


# --- Field selection ---

# chunk_text is the source itself, not an extraction — judging it is meaningless.
# chunks carry their own nested structure and are judged separately (or not at all).
_SKIP_FIELDS = {"chunks", "chunk_text", "chunks_metadata"}


def select_judgeable_fields(extracted: Dict[str, Any]) -> Dict[str, Any]:
    """Drop chunk plumbing; keep every real metadata field, including empty ones.

    Empty fields are kept deliberately: MISSING (the extractor overlooked content
    that was present) is the single most useful signal this judge produces, and it
    can only be detected on fields that came back empty.
    """
    return {k: v for k, v in extracted.items() if k not in _SKIP_FIELDS}


# --- Entry point ---

def judge_enrichment(
    source_text: str,
    extracted: Dict[str, Any],
    field_configs: Optional[List[Any]] = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    judge_provider: str = DEFAULT_JUDGE_PROVIDER,
    owner_id: Optional[str] = None,
) -> JudgmentResult:
    """
    Judge one document's enrichment output.

    Never raises — on any failure returns a JudgmentResult with `error` set and
    zeroed scores, so a judge outage can't take down an eval run or a pipeline.

    Args:
        source_text: The document text the extractor saw.
        extracted: Serialized enrichment output (field_name -> value).
        field_configs: FieldConfiguration rows, so the judge sees each field's
                       description and constraints. Strongly recommended.
        judge_model / judge_provider: Judge LLM. Keep on a different provider
                       than the extractor to avoid correlated errors.
        owner_id: Owner ID for owner-scoped API keys.

    Returns:
        JudgmentResult with per-field verdicts and aggregate 0.0-1.0 scores.
    """
    started = time.time()

    fields = select_judgeable_fields(extracted or {})
    if not fields:
        logger.warning("[judge] No judgeable fields in extraction output — skipping judge call")
        return JudgmentResult(
            judge_model=judge_model,
            judge_provider=judge_provider,
            duration_ms=int((time.time() - started) * 1000),
            error="no_judgeable_fields",
        )

    try:
        llm, _key_type = get_enrichment_llm(owner_id, judge_provider, judge_model, temperature=0.0)
        structured_judge = llm.with_structured_output(_JudgeResponse)

        logger.info(f"[judge] Judging {len(fields)} fields with {judge_provider}/{judge_model}")
        # Anthropic caps concurrent connections; under eval concurrency a bare invoke
        # would 429 and silently turn into a zero score.
        response = invoke_with_backoff(
            lambda: structured_judge.invoke([
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": build_judge_prompt(source_text, fields, field_configs)},
            ]),
            what="enrichment judge",
        )

        judgments = response.field_judgments or {}

        # The judge occasionally returns fields we didn't ask about, or omits some.
        # Keep only what we asked for so scores stay comparable across runs.
        judgments = {k: v for k, v in judgments.items() if k in fields}
        if len(judgments) < len(fields):
            missing = sorted(set(fields) - set(judgments))
            logger.warning(f"[judge] Judge omitted {len(missing)} field(s): {missing}")

        result = JudgmentResult(
            field_judgments=judgments,
            flagged_fields=sorted(name for name, j in judgments.items() if _is_flagged(j)),
            overall_grounding_score=_mean_score([j.grounding for j in judgments.values()]),
            overall_completeness_score=_mean_score([j.completeness for j in judgments.values()]),
            overall_relevance_score=_mean_score([j.relevance for j in judgments.values()]),
            judge_model=judge_model,
            judge_provider=judge_provider,
            fields_judged=len(judgments),
            duration_ms=int((time.time() - started) * 1000),
        )

        logger.info(
            f"[judge] grounding={result.overall_grounding_score:.2f} "
            f"completeness={result.overall_completeness_score:.2f} "
            f"relevance={result.overall_relevance_score:.2f} "
            f"flagged={result.flagged_fields or 'none'} "
            f"({result.duration_ms}ms)"
        )
        return result

    except Exception as e:
        logger.error(f"[judge] Judge call failed: {e}", exc_info=True)
        return JudgmentResult(
            judge_model=judge_model,
            judge_provider=judge_provider,
            duration_ms=int((time.time() - started) * 1000),
            error=str(e),
        )
