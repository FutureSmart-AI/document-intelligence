"""RAG / chat evaluation — the pure half: the owner gate, the free structural
metrics, and the judge's prompt and response parsing.

No IO here. `rag_eval_runner.py` fetches turns and persists scores; this module
holds everything testable without a database, a network or an LLM — the same
split as extraction_validator.py against validation_runner.py.

WHY TWO TIERS, AND WHY ONLY ONE OF THEM IS INLINE
-------------------------------------------------
Tier 0 costs nothing. Every value it reports was ALREADY computed during the
turn and then thrown away (docs/plan/quality-baselines/coverage.md audited four
such signals). Writing them is a dict lookup, so it happens inline.

Tier 1 calls a judge. A judge call inline in a chat request would make the user
wait for a number only the analytics screen reads — the same objection that put
extraction validation in its own pass — so it runs on demand over stored turns.

WHAT TIER 0 CAN AND CANNOT TELL YOU
-----------------------------------
Nothing in Tier 0 knows whether an answer is CORRECT. There is no arithmetic that
reconciles an answer the way a bank statement's balance column reconciles an
extraction, so the free tier here is structural rather than deterministic-correct:
counts, ratios and the verifier's own verdict. Its job is to say WHERE to point
the paid judge, not to replace it.

`context_utilization` is the sharpest of them, and its limitation is specific: it
is the agent's own report of which evidence it used
(`RagAgentOutput.relevant_docs`), so it is self-assessment, not an independent
measurement. A turn that retrieved 25 chunks and cited 2 is still the strongest
free signal of noisy retrieval we can get.

THE OWNER GATE
--------------
Both tiers are scoped to an allowlist of owner_ids (RAG_EVAL_OWNER_IDS), empty by
default, so deploying this module changes nothing until an owner is named. See
`rag_eval_enabled_for`.

Two ways this gate deliberately differs from the extraction one
(EXTRACTION_VALIDATION_ALLOWED_OWNERS), both worth knowing before assuming they
behave alike:

  * empty list — there it means "not configured, judge ALLOWED"; here it means
    OFF. That tier already ran in production, so an empty env var must not switch
    a live feature off; this tier is new, so an empty env var must not switch paid
    spend on. Retrofit and greenfield want opposite fail-safe directions.
  * NULL owner — there it is ALLOWED (master-key collections have no owner and
    are exactly their test collections); here it is refused unless the list is
    "*", because attributing spend to an unidentified tenant is the failure this
    gate exists to prevent.

Both pairs of choices are intentional. Neither should be copied to the other
without deciding which property to break.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from utils.logger import logger
from utils.setup_env import (
    RAG_EVAL_JUDGE_ENABLED,
    RAG_EVAL_MODEL,
    RAG_EVAL_OWNER_IDS,
    RAG_EVAL_PROVIDER,
)

# Sentinel meaning "no verifier verdict exists for this turn", kept distinct from
# sufficient/insufficient. The verifier is deliberately NOT run on the final
# attempt (chat/rag_agent/agent_execution.py: its only job is deciding whether to
# retry, and on the last attempt that is already decided), so a single-attempt
# turn legitimately has no verdict. Storing NULL would conflate that with "the
# column predates this migration".
VERDICT_NOT_RUN = "not_run"
VERDICT_SUFFICIENT = "sufficient"
VERDICT_INSUFFICIENT = "insufficient"


# ── the owner gate ────────────────────────────────────────────────────────────
def _parse_allowlist(raw: str) -> List[str]:
    """Split and normalise the configured allowlist. Blank entries dropped, so a
    trailing comma or a stray space cannot enable an empty owner_id."""
    return [part.strip().lower() for part in (raw or "").split(",") if part.strip()]


def rag_eval_enabled_for(owner_id: Optional[str], *, allowlist: Optional[str] = None) -> bool:
    """Is RAG evaluation enabled for this owner?

    ``allowlist`` is injectable for tests; production reads the env value.

    Rules, in order:
      * empty allowlist  → False for everyone. This is the default, so shipping
        the feature is inert until someone is named.
      * "*"              → True for everyone, including a turn with no owner.
      * otherwise        → True only on an exact, case-insensitive match.

    A turn with no ``owner_id`` (a credential-less public caller) matches only
    "*". An allowlist cannot match an absent identity, and substituting a default
    owner would attribute the spend to whoever happened to be first in the list.
    """
    entries = _parse_allowlist(RAG_EVAL_OWNER_IDS if allowlist is None else allowlist)
    if not entries:
        return False
    if "*" in entries:
        return True
    if not owner_id:
        return False
    return owner_id.strip().lower() in entries


def rag_judge_enabled_for(owner_id: Optional[str], *, allowlist: Optional[str] = None,
                          judge_enabled: Optional[bool] = None) -> bool:
    """Is the PAID tier enabled for this owner? Requires both the owner gate and
    the judge switch — an owner can be given the free structural metrics with no
    judge spend at all."""
    enabled = RAG_EVAL_JUDGE_ENABLED if judge_enabled is None else judge_enabled
    return bool(enabled) and rag_eval_enabled_for(owner_id, allowlist=allowlist)


# ── Tier 0: the free structural metrics ───────────────────────────────────────
def _cited_ids(agent_output: Dict[str, Any]) -> tuple[set, set]:
    """(doc_ids, chunk_ids) the agent reported as grounding its answer.

    Tolerant of shape: relevant_docs entries are dicts from a parsed
    RagAgentOutput, but a malformed model response can put a bare string there,
    and a metric must not raise on the path that only observes.
    """
    docs, chunks = set(), set()
    for entry in (agent_output or {}).get("relevant_docs") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("doc_id"):
            docs.add(entry["doc_id"])
        for cid in entry.get("chunk_ids") or []:
            if cid:
                chunks.add(cid)
    return docs, chunks


def compute_tier0(rag_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The free metrics for one turn, from the RAG context the graph already built.

    Every value is a count or a verdict already present in ``rag_context``; this
    function only reads and shapes. Returns all keys on every path (None where
    the turn genuinely has no value) so a caller never has to guess whether a
    missing key means zero.

    `context_utilization` is deliberately NOT computed here — it is
    cited/retrieved, which the read side can divide. Storing a ratio alongside
    its own numerator and denominator invites the three drifting apart.
    """
    ctx = rag_context or {}
    agent_output = ctx.get("agent_output") or {}

    retrieved_chunks = ctx.get("chunks")
    retrieved_summaries = ctx.get("summaries")
    cited_docs, cited_chunks = _cited_ids(agent_output)

    verdicts = ctx.get("verifier_verdicts") or []
    if verdicts:
        last = verdicts[-1]
        sufficient = last.get("sufficient") if isinstance(last, dict) else getattr(last, "sufficient", None)
        missing = last.get("missing") if isinstance(last, dict) else getattr(last, "missing", None)
        verdict = VERDICT_SUFFICIENT if sufficient else VERDICT_INSUFFICIENT
    else:
        verdict, missing = VERDICT_NOT_RUN, None

    return {
        # Retrieved vs cited, at both granularities. Kept as raw counts, not a
        # ratio — see the docstring.
        "retrieved_chunk_count": len(retrieved_chunks) if retrieved_chunks is not None else None,
        "retrieved_doc_count": len(retrieved_summaries) if retrieved_summaries is not None else None,
        "cited_chunk_count": len(cited_chunks) if agent_output else None,
        "cited_doc_count": len(cited_docs) if agent_output else None,
        # How many times the retrieval loop ran. 1 on the common path; >1 means
        # the verifier sent it back. Absent on the error path.
        "retrieval_attempts": ctx.get("retrieval_attempts"),
        # The verifier's own judgement, previously used for a branch and dropped.
        "verifier_verdict": verdict,
        "verifier_missing": (str(missing)[:2000] or None) if missing else None,
        # Cross-turn evidence reuse: how much recency-loaded evidence survived
        # the relevance gate (chat/evidence_relevance.py). None when the turn
        # loaded no prior evidence at all, which is different from "all dropped".
        "evidence_groups_loaded": ctx.get("evidence_groups_loaded"),
        "evidence_groups_kept": ctx.get("evidence_groups_kept"),
    }


def context_utilization(cited: Optional[int], retrieved: Optional[int]) -> Optional[float]:
    """cited / retrieved, or None when it cannot be read.

    None — not 0.0 — when nothing was retrieved: a turn that retrieved no chunks
    did not use 0% of its evidence, it had no evidence to use. Reporting 0.0
    there would drag a fleet average down for turns that never retrieved.
    """
    if not retrieved or cited is None:
        return None
    return round(min(cited, retrieved) / retrieved, 4)


# ── Tier 1: the judge ─────────────────────────────────────────────────────────
# One prompt, imported by both the on-demand runner and the offline harness
# (evals/scripts/chat_baseline_eval.py). Two copies of a judge rubric drift, and
# a drifted rubric silently breaks comparability between the two.
JUDGE_SYSTEM_PROMPT = (
    "You score one answer from a retrieval-augmented assistant on three "
    "INDEPENDENT dimensions. Score each on its own evidence — a low score on one "
    "must not drag the others down.\n"
    "\n"
    "1. faithfulness (0.0-1.0) — of the factual claims in the ANSWER, what "
    "fraction is supported by the CONTEXT? Judge only support, not whether the "
    "claim is true in the world and not whether it answers the question. "
    "1.0 = every claim traceable to the context. 0.0 = the central claim appears "
    "nowhere in it. General phrasing, hedging and reasonable paraphrase are not "
    "unsupported claims; a specific unsupported number, name or date is.\n"
    "\n"
    "2. answer_relevance (0.0-1.0) — does the ANSWER address the QUESTION that "
    "was asked? Ignore whether it is correct or grounded. A fluent answer to a "
    "different question scores 0.0. A partial but on-target answer scores in "
    "between. An honest 'the documents do not cover this' is RELEVANT (it "
    "responds to the question) — score it high here and report refused=true.\n"
    "\n"
    "3. coherence (0.0-1.0) — is the ANSWER internally consistent and readable: "
    "no self-contradiction, sensible order, no truncation mid-thought, no "
    "repeated boilerplate. Judge the prose alone, not its accuracy.\n"
    "\n"
    "Also report:\n"
    "  unsupported_claims — up to 3 claims you could not find in the context, "
    "quoted briefly. Empty if none.\n"
    "  refused — true if the answer declines to answer or says the documents do "
    "not cover it.\n"
    "\n"
    'Return strict JSON only: {"faithfulness": <float>, "answer_relevance": '
    '<float>, "coherence": <float>, "refused": <bool>, "unsupported_claims": '
    '["..."], "reason": "<one sentence>"}'
)

JUDGE_SCORE_KEYS = ("faithfulness", "answer_relevance", "coherence")


class RagJudgeResult(BaseModel):
    """Structured-output schema for the judge.

    The in-service runner binds this via ``with_structured_output`` (the house
    pattern — see extraction_validator.py), while the offline harness talks to
    the provider SDK directly and parses JSON with ``parse_judge_response``. Two
    transports, but ONE rubric: both import JUDGE_SYSTEM_PROMPT above. The
    mechanism may differ; the definition of the metric may not.
    """
    faithfulness: float = Field(description="0.0-1.0, fraction of the answer's claims supported by the cited context")
    answer_relevance: float = Field(description="0.0-1.0, does the answer address the question asked")
    coherence: float = Field(description="0.0-1.0, internally consistent and readable prose")
    refused: bool = Field(default=False, description="True if the answer declines or says the docs don't cover it")
    unsupported_claims: List[str] = Field(default_factory=list, description="Up to 3 claims absent from the context")
    reason: str = Field(default="", description="One sentence explaining the scores")

# Per-item cap on context handed to the judge. Per ITEM, not global: one global
# budget lets a single long chunk crowd every other piece of evidence out of the
# judge's view, which reads as "unsupported" for claims the other chunks support.
MAX_CONTEXT_CHARS_PER_ITEM = 1500


def build_judge_prompt(query: str, answer: str, context: str) -> str:
    """The user half of the judge call. Shared, for the same reason as the system
    prompt above."""
    return (
        f"QUESTION\n{query}\n\n"
        f"CONTEXT (evidence the assistant cited)\n{context or '(none)'}\n\n"
        f"ANSWER\n{answer}\n"
    )


def _clamp(value: Any) -> Optional[float]:
    """A judge score as a float in [0, 1], or None if it is not a number.

    Clamped rather than trusted: an out-of-range score from a model that ignored
    the rubric would otherwise pull an average outside the metric's own scale,
    which is harder to notice than a value pinned at the boundary.
    """
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def parse_judge_response(raw: str, *, has_context: bool) -> Dict[str, Any]:
    """Parse the judge's JSON into a score dict.

    ``has_context`` False withholds faithfulness: an answer with no cited
    evidence cannot be checked for grounding, and scoring it 0.0 would read as
    "hallucinated" when the truthful answer is "there was nothing to check
    against". The other two dimensions never read the context, so they stand.

    Raises ValueError on unparseable output, so the caller records a judge
    failure rather than a score.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in judge response")
    parsed = json.loads(text[start:end + 1])

    scores = {key: _clamp(parsed.get(key)) for key in JUDGE_SCORE_KEYS}
    if not has_context:
        scores["faithfulness"] = None
    return {
        **scores,
        "refused": bool(parsed.get("refused", False)),
        "unsupported_claims": [str(c)[:300] for c in (parsed.get("unsupported_claims") or [])][:3],
        "reason": str(parsed.get("reason", ""))[:400],
    }


def scores_from_judge_result(result: "RagJudgeResult", *, has_context: bool) -> Dict[str, Any]:
    """Normalise a structured-output judge result into the same dict shape
    `parse_judge_response` returns, so the runner and the harness agree.

    Applies the same two rules: clamp to [0, 1], and withhold faithfulness when
    the turn cited no context (an answer with nothing to check against is not a
    hallucination).
    """
    scores = {
        "faithfulness": _clamp(result.faithfulness) if has_context else None,
        "answer_relevance": _clamp(result.answer_relevance),
        "coherence": _clamp(result.coherence),
    }
    return {
        **scores,
        "refused": bool(result.refused),
        "unsupported_claims": [str(c)[:300] for c in (result.unsupported_claims or [])][:3],
        "reason": (result.reason or "")[:400],
    }


# Document-level metadata keys that are structural noise for a judge: they
# describe the record, not the document's content, and padding the context with
# them buys nothing but tokens.
_METADATA_SKIP = {"summary", "document_outline", "embedding", "chunk_count"}
MAX_METADATA_FIELDS_PER_DOC = 25


def _metadata_lines(doc_id: str, metadata: Optional[Dict[str, Any]]) -> List[str]:
    """The document's extracted fields, as judge-readable lines.

    WHY THIS EXISTS
    ---------------
    A large share of answers are grounded in document-level METADATA, not in
    chunk text: ask "which state's law governs this agreement" and the agent
    reads `governing_law` off the enrichment fields through
    `retrieve_metadata_catalog`, citing zero chunks. That value is stored on the
    turn (`summaries[doc_id]["metadata"]`, normalised in
    chat/rag_node.py:946) — it was simply never passed to the judge.

    The consequence was not a small bias. A correct answer scored faithfulness
    **0.0**, with the judge stating plainly that "the context contains no
    mention of governing law" — because it did not. Every metadata-grounded
    answer was being marked unsupported for using evidence the scorer withheld
    from itself, and the resulting mean read as a grounding problem in the
    product (chat_id 672eb7e4, 2026-09-01).
    """
    if not isinstance(metadata, dict):
        return []
    lines: List[str] = []
    for key, value in metadata.items():
        if key in _METADATA_SKIP or value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            rendered = ", ".join(str(v) for v in value if v not in (None, ""))
        elif isinstance(value, dict):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        if not rendered:
            continue
        lines.append(f"{key}: {rendered[:MAX_CONTEXT_CHARS_PER_ITEM]}")
        if len(lines) >= MAX_METADATA_FIELDS_PER_DOC:
            break
    return ([f"[metadata {doc_id}] " + " | ".join(lines)] if lines else [])


def flatten_cited_context(summaries: Optional[Dict[str, Any]],
                          detailed_chunks: Optional[Dict[str, Any]]) -> str:
    """One text block of the cited evidence, for the judge.

    Three kinds of evidence, because the agent answers from three:
    document-level **metadata** (enrichment fields, read via the metadata
    catalog), document **summaries** (a turn can legitimately be answered from a
    summary alone — the agent reports that as ``granularity: doc``), and chunk
    **text**. Leaving any of them out marks answers grounded in that kind as
    unsupported, which is a defect in the measurement, not in the answer.
    """
    parts: List[str] = []
    for doc_id, summary in (summaries or {}).items():
        if isinstance(summary, dict):
            text = summary.get("summary", "")
            parts.extend(_metadata_lines(doc_id, summary.get("metadata")))
        else:
            text = str(summary)
        if text:
            parts.append(f"[summary {doc_id}] {text[:MAX_CONTEXT_CHARS_PER_ITEM]}")
    for doc_id, chunks in (detailed_chunks or {}).items():
        for chunk in (chunks or []):
            if isinstance(chunk, dict):
                text = chunk.get("chunk_text") or chunk.get("text") or ""
            else:
                text = str(chunk)
            if text:
                parts.append(f"[chunk {doc_id}] {text[:MAX_CONTEXT_CHARS_PER_ITEM]}")
    return "\n\n".join(parts)


def score_rows_for_turn(*, knowledge_id: str, chat_id: str, scores: Dict[str, Any],
                        judge_model: Optional[str] = None) -> List[Dict[str, Any]]:
    """Shape one judged turn into rows for `knowledge_quality_scores`.

    One row per dimension rather than one row per turn with three columns — the
    design in docs/plan/quality-baselines/storage.md. A withheld dimension
    produces NO ROW at all, which is what keeps "not measured" and "measured as
    zero" structurally impossible to confuse.
    """
    rows = []
    for key in JUDGE_SCORE_KEYS:
        value = scores.get(key)
        if value is None:
            continue
        rows.append({
            "knowledge_id": knowledge_id,
            "subject_type": "chat_turn",
            "subject_id": str(chat_id),
            "name": key,
            "value": float(value),
            "data_type": "NUMERIC",
            "source": "judge",
            "grader_model": judge_model or RAG_EVAL_MODEL,
            "comment": scores.get("reason") or None,
            # The judge scores one answer against its own cited context, so the
            # denominator is 1 turn. Present for uniformity with the extraction
            # scores, where it is the observation count that makes a score
            # readable at all.
            "observations": 1,
        })
    if not rows:
        logger.info(
            f"[rag_eval] chat_id={chat_id} produced no score rows — every "
            f"dimension was withheld"
        )
    return rows


def judge_provider_model() -> tuple[Optional[str], Optional[str]]:
    """The pinned judge, as (provider, model). Read through a function so the
    runner and the harness cannot disagree about the default."""
    return RAG_EVAL_PROVIDER, RAG_EVAL_MODEL
