"""
Reference-based comparison for enrichment eval.

Compares a model's enrichment output against a frozen reference produced by a
stronger model (Opus). Each field is routed to a comparator chosen for what the
field actually *is*, not merely its Python type:

  - normalized exact  → low-cardinality labels (document_type)
  - set F1            → order-independent tag lists (topics, keywords, entities…)
  - semantic (LLM)    → generated prose where paraphrase is valid (title, summary…)

Terminology note: the reference is a SILVER standard, not ground truth. It is one
strong model's output, not a human label. A low score means "diverges from Opus",
which is evidence of a quality gap but not proof of error — read it alongside the
grounding scores from judge.py, which check the resume itself.

Plan: claude-logs/plan/enrichment-langsmith-eval.md
"""

from __future__ import annotations

import random
import re
import time
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from celery_utils.enrichment.llm_factory import get_enrichment_llm
from services.celery_logger import logger


def invoke_with_backoff(
    fn: Callable[[], Any],
    what: str,
    max_attempts: int = 5,
    base_delay: float = 2.0,
) -> Any:
    """
    Retry an LLM call through rate limits with exponential backoff and jitter.

    Anthropic caps *concurrent connections*, and one eval document fans out to
    several comparator calls at once, so 429s are expected under any real
    concurrency. Without this, rate limits silently become neutral scores and quietly
    corrupt the experiment.

    Jitter matters: without it, parallel workers all back off in lockstep and collide
    again on every retry.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            # Retry only throttling/transient errors; a bad request will never succeed.
            transient = any(
                marker in str(e).lower()
                for marker in ("rate_limit", "429", "overloaded", "529", "timeout", "connection")
            )
            if not transient or attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(
                f"[backoff] {what} attempt {attempt}/{max_attempts} hit a limit; "
                f"retrying in {delay:.1f}s"
            )
            time.sleep(delay)
    raise last_error  # unreachable; satisfies type checkers


# Semantic comparison runs on Anthropic; enrichment runs on OpenAI/Groq. Different
# provider keeps the comparator from sharing the extractor's blind spots.
DEFAULT_COMPARATOR_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_COMPARATOR_PROVIDER = "anthropic"


# --- Field routing ---

Comparator = Literal["exact", "set_f1", "semantic", "semantic_set"]

# Chosen per field's real character. `title`/`summary` are GENERATED prose — exact
# match would punish valid paraphrase. `document_outline` is a list, but ordered
# structural text rather than tags, so F1 on raw strings would score near zero even
# for good output.
#
# Tag lists use `semantic_set`, NOT `set_f1`. Measured on real output, plain string F1
# scored 0.00 on topics where the two models produced the SAME topics in different
# words ("Machine Learning" vs "Machine Learning Projects"), and penalised
# "IIT KGP" vs "IIT Kharagpur" as a total miss. That measures vocabulary agreement,
# not extraction quality. `set_f1` remains available for cheap deterministic runs.
FIELD_COMPARATORS: Dict[str, Comparator] = {
    "document_type": "exact",
    "title": "semantic",
    "summary": "semantic",
    "temporal_context": "semantic",
    "topics": "semantic_set",
    "keywords": "semantic_set",
    "entities": "semantic_set",
    "organizations": "semantic_set",
    "document_outline": "semantic",
}

# Fields not in the map fall back by Python type.
_LIST_FALLBACK: Comparator = "set_f1"
_STR_FALLBACK: Comparator = "semantic"

_SKIP_FIELDS = {"chunks", "chunk_text", "chunks_metadata"}

_EMPTY_SENTINELS = {"", "not_found", "none", "n/a", "null"}


def comparator_for(field_name: str, value: Any, reference: Any) -> Comparator:
    """Pick a comparator for a field, preferring the explicit map."""
    if field_name in FIELD_COMPARATORS:
        return FIELD_COMPARATORS[field_name]
    probe = reference if reference is not None else value
    return _LIST_FALLBACK if isinstance(probe, list) else _STR_FALLBACK


# --- Normalization ---

def _normalize(text: Any) -> str:
    """Lowercase, strip punctuation and collapse whitespace for tolerant matching."""
    s = str(text).lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)   # punctuation → space, so "co-founder" ~ "co founder"
    return re.sub(r"\s+", " ", s).strip()


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _EMPTY_SENTINELS
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _as_list(value: Any) -> List[str]:
    """Coerce a field value to a list of strings, dropping empties."""
    if _is_empty(value):
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        # Extraction sometimes collapses a list into a comma-joined string.
        items = value.split(",")
    else:
        items = [value]
    return [str(i).strip() for i in items if str(i).strip()]


# --- Deterministic comparators ---

def compare_exact(value: Any, reference: Any) -> Tuple[float, str]:
    """Normalized string equality. For low-cardinality labels only."""
    if _is_empty(value) and _is_empty(reference):
        return 1.0, "both empty"
    if _is_empty(value):
        return 0.0, f"empty; reference={reference!r}"
    if _is_empty(reference):
        return 1.0, "no reference value to match (not penalised)"

    a, b = _normalize(value), _normalize(reference)
    if a == b:
        return 1.0, f"exact: {value!r}"
    return 0.0, f"got {value!r}, reference {reference!r}"


def compare_set_f1(value: Any, reference: Any) -> Tuple[float, str]:
    """
    Order-independent F1 over normalized items.

    F1 rather than raw overlap so that padding a list with extra tags is penalised
    (precision) alongside missing the reference's tags (recall).
    """
    got_raw, ref_raw = _as_list(value), _as_list(reference)

    if not got_raw and not ref_raw:
        return 1.0, "both empty"
    if not ref_raw:
        return 1.0, "no reference items to match (not penalised)"
    if not got_raw:
        return 0.0, f"empty; reference had {len(ref_raw)} item(s)"

    got = {_normalize(v) for v in got_raw}
    ref = {_normalize(v) for v in ref_raw}

    overlap = got & ref
    precision = len(overlap) / len(got)
    recall = len(overlap) / len(ref)
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    missed = sorted(r for r in ref if r not in got)
    extra = sorted(g for g in got if g not in ref)
    detail = f"P={precision:.2f} R={recall:.2f}"
    if missed:
        detail += f" | missed: {', '.join(missed[:4])}"
    if extra:
        detail += f" | extra: {', '.join(extra[:4])}"

    return f1, detail


# --- Semantic comparator ---

class _SemanticVerdict(BaseModel):
    """LLM verdict on whether two prose values convey the same thing."""

    equivalent: Literal["EQUIVALENT", "PARTIAL", "DIFFERENT"] = Field(
        description="EQUIVALENT = same meaning; PARTIAL = overlapping but one omits or adds "
                    "something material; DIFFERENT = conveys something else."
    )
    reason: str = Field(default="", description="One short line.")


_SEMANTIC_SCORES = {"EQUIVALENT": 1.0, "PARTIAL": 0.5, "DIFFERENT": 0.0}

_SEMANTIC_SYSTEM = """You compare two values extracted from the same document for the same \
metadata field. One is a reference produced by a stronger model; the other is a candidate.

Judge whether the CANDIDATE conveys the same information as the REFERENCE for this field's purpose.

  EQUIVALENT = same substance; wording, order, and level of detail may differ freely
  PARTIAL    = overlapping, but one omits or adds something material
  DIFFERENT  = conveys something substantively different

Rules:
- Judge MEANING, not phrasing. Paraphrase, different word order, and different length are
  all EQUIVALENT so long as the substance matches.
- The reference is not infallible. If the candidate is clearly a reasonable value for this
  field and captures the same substance, call it EQUIVALENT even if worded quite differently.
- Do not reward padding. Extra unsupported specifics make it PARTIAL, not EQUIVALENT."""


def compare_semantic(
    field_name: str,
    field_description: str,
    value: Any,
    reference: Any,
    model: str = DEFAULT_COMPARATOR_MODEL,
    provider: str = DEFAULT_COMPARATOR_PROVIDER,
    owner_id: Optional[str] = None,
) -> Tuple[float, str]:
    """
    LLM equivalence check for generated prose.

    Empty-vs-empty and no-reference cases short-circuit without an LLM call, so the
    common cheap paths stay free.
    """
    if _is_empty(value) and _is_empty(reference):
        return 1.0, "both empty"
    if _is_empty(reference):
        return 1.0, "no reference value to match (not penalised)"
    if _is_empty(value):
        return 0.0, f"empty; reference={str(reference)[:80]!r}"

    def _render(v: Any) -> str:
        return "\n".join(f"- {i}" for i in v) if isinstance(v, list) else str(v)

    try:
        llm, _ = get_enrichment_llm(owner_id, provider, model, temperature=0.0)
        structured = llm.with_structured_output(_SemanticVerdict)
        verdict = invoke_with_backoff(
            lambda: structured.invoke([
                {"role": "system", "content": _SEMANTIC_SYSTEM},
                {"role": "user", "content": (
                    f"FIELD: {field_name}\n"
                    f"FIELD PURPOSE: {field_description or '(none given)'}\n\n"
                    f"REFERENCE:\n{_render(reference)}\n\n"
                    f"CANDIDATE:\n{_render(value)}"
                )},
            ]),
            what=f"semantic compare ({field_name})",
        )
        return _SEMANTIC_SCORES[verdict.equivalent], f"{verdict.equivalent}: {verdict.reason}"
    except Exception as e:
        logger.error(f"[reference_match] Semantic compare failed for {field_name}: {e}")
        # Neutral 0.5 rather than 0.0 — a comparator outage is not evidence of a bad
        # extraction, and scoring it 0 would silently depress the experiment. `error`
        # on the aggregate result is what flags the run as untrustworthy.
        return 0.5, f"comparator error (scored neutral): {e}"


# --- Semantic set comparator ---

class _SetAlignment(BaseModel):
    """LLM alignment between two tag lists."""

    matched_pairs: List[str] = Field(
        default_factory=list,
        description="One 'candidate <=> reference' string per semantically equivalent pair.",
    )
    unmatched_reference: List[str] = Field(
        default_factory=list, description="Reference tags with no equivalent in the candidate."
    )
    unmatched_candidate: List[str] = Field(
        default_factory=list, description="Candidate tags with no equivalent in the reference."
    )


_SET_SYSTEM = """You align two lists of metadata tags extracted from the same document for the \
same field. One list is a REFERENCE from a stronger model; the other is a CANDIDATE.

Pair up tags that refer to THE SAME THING, even when worded differently. Count as equivalent:
  - narrower/broader wording of the same concept ("Machine Learning" <=> "Machine Learning Projects")
  - abbreviations and expansions ("IIT KGP" <=> "IIT Kharagpur", "NL2SQL" <=> "NL2SQL chatbot")
  - singular/plural, casing, punctuation, and word-order differences
  - the same entity named slightly differently ("FutureSmart AI" <=> "FutureSmart")

Do NOT pair genuinely different things: two distinct organizations, two unrelated topics, or a
technology paired with a company.

Every tag must appear exactly once — either in a matched pair or in one unmatched list."""


def compare_semantic_set(
    field_name: str,
    field_description: str,
    value: Any,
    reference: Any,
    model: str = DEFAULT_COMPARATOR_MODEL,
    provider: str = DEFAULT_COMPARATOR_PROVIDER,
    owner_id: Optional[str] = None,
) -> Tuple[float, str]:
    """
    F1 over a MEANING-based alignment of two tag lists.

    Exists because plain `set_f1` conflates "picked different words" with "got it
    wrong": on real output it scored 0.00 for topic lists that were substantively
    identical. Falls back to `compare_set_f1` if the aligner call fails, so a
    comparator outage degrades to a stricter score rather than no score.
    """
    got_raw, ref_raw = _as_list(value), _as_list(reference)

    if not got_raw and not ref_raw:
        return 1.0, "both empty"
    if not ref_raw:
        return 1.0, "no reference items to match (not penalised)"
    if not got_raw:
        return 0.0, f"empty; reference had {len(ref_raw)} item(s)"

    # Skip the LLM when strings already agree exactly — same answer, no cost.
    exact_score, exact_detail = compare_set_f1(value, reference)
    if exact_score == 1.0:
        return 1.0, f"exact set match ({exact_detail})"

    try:
        llm, _ = get_enrichment_llm(owner_id, provider, model, temperature=0.0)
        structured = llm.with_structured_output(_SetAlignment)
        alignment = invoke_with_backoff(
            lambda: structured.invoke([
                {"role": "system", "content": _SET_SYSTEM},
                {"role": "user", "content": (
                    f"FIELD: {field_name}\n"
                    f"FIELD PURPOSE: {field_description or '(none given)'}\n\n"
                    f"REFERENCE:\n" + "\n".join(f"- {t}" for t in ref_raw) + "\n\n"
                    f"CANDIDATE:\n" + "\n".join(f"- {t}" for t in got_raw)
                )},
            ]),
            what=f"semantic set align ({field_name})",
        )

        matched = len(alignment.matched_pairs)
        # Trust the counts we asked for, but never let a miscount exceed the real
        # list sizes — the LLM occasionally double-lists a tag.
        matched = min(matched, len(got_raw), len(ref_raw))

        precision = matched / len(got_raw)
        recall = matched / len(ref_raw)
        f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

        detail = f"P={precision:.2f} R={recall:.2f} ({matched} semantic match(es))"
        if alignment.unmatched_reference:
            detail += f" | missed: {', '.join(alignment.unmatched_reference[:4])}"
        if alignment.unmatched_candidate:
            detail += f" | extra: {', '.join(alignment.unmatched_candidate[:4])}"

        return f1, detail

    except Exception as e:
        logger.error(f"[reference_match] Semantic set align failed for {field_name}: {e}")
        return exact_score, f"aligner failed, fell back to strict F1: {exact_detail}"


# --- Batched comparator ---
#
# One LLM call per DOCUMENT instead of one per field. Measured on the first runs:
# 8 comparable fields meant 8 serial Anthropic calls per document (~15s, $0.0135 per
# row), because MAX_CONCURRENCY=1 leaves no overlap to hide the latency. The fields
# are independent, so a single call carrying all of them returns the same verdicts.
#
# The per-field functions above are kept: they are the fallback when the batch call
# fails, and `set_f1`/`exact` still run locally with no LLM at all.

class _BatchFieldVerdict(BaseModel):
    """One field's verdict inside a batched comparison."""

    field_name: str = Field(description="Echo the field name exactly as given.")
    kind: Literal["prose", "tags"] = Field(
        description="Echo the field's KIND exactly as given."
    )
    # Prose fields only; ignored for tag fields.
    equivalent: Optional[Literal["EQUIVALENT", "PARTIAL", "DIFFERENT"]] = Field(
        default=None, description="Prose fields only. Omit for tag fields."
    )
    # Tag fields only; ignored for prose fields.
    matched_pairs: List[str] = Field(
        default_factory=list,
        description="Tag fields only. One 'candidate <=> reference' string per "
                    "semantically equivalent pair.",
    )
    unmatched_reference: List[str] = Field(
        default_factory=list, description="Tag fields only. Reference tags with no match."
    )
    unmatched_candidate: List[str] = Field(
        default_factory=list, description="Tag fields only. Candidate tags with no match."
    )
    reason: str = Field(default="", description="One short line.")


class _BatchComparison(BaseModel):
    verdicts: List[_BatchFieldVerdict] = Field(default_factory=list)


_BATCH_SYSTEM = """You compare metadata extracted from a single document by two models. \
One set of values is a REFERENCE from a stronger model; the other is the CANDIDATE under test.

You are given SEVERAL fields at once. Judge each field INDEPENDENTLY and return exactly one
verdict per field given, echoing its `field_name` and `kind`.

Each field is one of two kinds, and the kind decides which verdict you fill in:

=== kind = "prose" (generated text: titles, summaries, outlines) ===
Set `equivalent` and leave the tag lists empty. Judge whether the CANDIDATE conveys the same
information as the REFERENCE for this field's purpose.

  EQUIVALENT = same substance; wording, order, and level of detail may differ freely
  PARTIAL    = overlapping, but one omits or adds something material
  DIFFERENT  = conveys something substantively different

Rules:
- Judge MEANING, not phrasing. Paraphrase, different word order, and different length are
  all EQUIVALENT so long as the substance matches.
- The reference is not infallible. If the candidate is clearly a reasonable value for this
  field and captures the same substance, call it EQUIVALENT even if worded quite differently.
- Do not reward padding. Extra unsupported specifics make it PARTIAL, not EQUIVALENT.

=== kind = "tags" (lists of metadata tags) ===
Fill `matched_pairs`, `unmatched_reference`, `unmatched_candidate`; leave `equivalent` null.

Pair up tags that refer to THE SAME THING, even when worded differently. Count as equivalent:
  - narrower/broader wording of the same concept ("Machine Learning" <=> "Machine Learning Projects")
  - abbreviations and expansions ("IIT KGP" <=> "IIT Kharagpur", "NL2SQL" <=> "NL2SQL chatbot")
  - singular/plural, casing, punctuation, and word-order differences
  - the same entity named slightly differently ("FutureSmart AI" <=> "FutureSmart")

Do NOT pair genuinely different things: two distinct organizations, two unrelated topics, or a
technology paired with a company.

Every tag must appear exactly once - either in a matched pair or in one unmatched list."""


def compare_batched(
    items: List[Tuple[str, str, str, Any, Any]],
    model: str = DEFAULT_COMPARATOR_MODEL,
    provider: str = DEFAULT_COMPARATOR_PROVIDER,
    owner_id: Optional[str] = None,
) -> Dict[str, Tuple[float, str]]:
    """
    Score every LLM-backed field of one document in a single call.

    `items` is (field_name, kind, description, value, reference) where kind is
    "semantic" or "semantic_set". Returns {field_name: (score, detail)}.

    On any failure the caller falls back to the per-field comparators, so a batch
    outage costs latency, not scores.
    """
    if not items:
        return {}

    kind_of = {name: kind for name, kind, _, _, _ in items}

    def _render(v: Any) -> str:
        return "\n".join(f"- {i}" for i in v) if isinstance(v, list) else str(v)

    blocks = []
    for field_name, kind, description, value, reference in items:
        blocks.append(
            f"### FIELD: {field_name}\n"
            f"KIND: {'tags' if kind == 'semantic_set' else 'prose'}\n"
            f"FIELD PURPOSE: {description or '(none given)'}\n"
            f"REFERENCE:\n{_render(reference)}\n"
            f"CANDIDATE:\n{_render(value)}"
        )
    user_content = (
        f"Compare {len(items)} field(s) from the same document. "
        f"Return exactly {len(items)} verdict(s).\n\n" + "\n\n".join(blocks)
    )

    llm, _ = get_enrichment_llm(owner_id, provider, model, temperature=0.0)
    structured = llm.with_structured_output(_BatchComparison)
    batch = invoke_with_backoff(
        lambda: structured.invoke([
            {"role": "system", "content": _BATCH_SYSTEM},
            {"role": "user", "content": user_content},
        ]),
        what=f"batched compare ({len(items)} fields)",
    )

    scores: Dict[str, Tuple[float, str]] = {}
    for v in batch.verdicts:
        kind = kind_of.get(v.field_name)
        if kind is None:
            continue  # hallucinated field name — ignore rather than trust

        if kind == "semantic":
            if v.equivalent not in _SEMANTIC_SCORES:
                continue  # missing verdict → leave to the per-field fallback
            scores[v.field_name] = (
                _SEMANTIC_SCORES[v.equivalent], f"{v.equivalent}: {v.reason}"
            )
        else:
            # Recompute F1 locally from the alignment; never trust an LLM's arithmetic.
            got_n = len(_as_list(next(i[3] for i in items if i[0] == v.field_name)))
            ref_n = len(_as_list(next(i[4] for i in items if i[0] == v.field_name)))
            if not got_n or not ref_n:
                continue
            matched = min(len(v.matched_pairs), got_n, ref_n)
            precision, recall = matched / got_n, matched / ref_n
            f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
            detail = f"P={precision:.2f} R={recall:.2f} ({matched} semantic match(es))"
            if v.unmatched_reference:
                detail += f" | missed: {', '.join(v.unmatched_reference[:4])}"
            if v.unmatched_candidate:
                detail += f" | extra: {', '.join(v.unmatched_candidate[:4])}"
            scores[v.field_name] = (f1, detail)

    return scores


# --- Aggregate ---

class FieldComparison(BaseModel):
    field_name: str
    comparator: str
    score: float
    detail: str


class ReferenceMatchResult(BaseModel):
    """Per-field and aggregate agreement with the reference."""

    field_comparisons: List[FieldComparison] = []
    overall_score: float = 0.0
    exact_score: float = 0.0
    list_f1_score: float = 0.0       # tag lists (semantic alignment by default)
    semantic_score: float = 0.0      # prose fields
    mismatched_fields: List[str] = []
    fields_compared: int = 0
    duration_ms: int = 0
    error: Optional[str] = None


def _mean(scores: List[float]) -> float:
    return sum(scores) / len(scores) if scores else 1.0


def compare_to_reference(
    extracted: Dict[str, Any],
    reference: Dict[str, Any],
    field_configs: Optional[List[Any]] = None,
    comparator_model: str = DEFAULT_COMPARATOR_MODEL,
    comparator_provider: str = DEFAULT_COMPARATOR_PROVIDER,
    owner_id: Optional[str] = None,
) -> ReferenceMatchResult:
    """
    Compare an enrichment result against the frozen reference.

    Iterates the REFERENCE's fields — a field the candidate dropped entirely must
    still be scored, and iterating the candidate would silently skip it.

    Never raises; returns `error` set on unexpected failure.
    """
    started = time.time()

    descriptions = {
        getattr(c, "field_name", None): (getattr(c, "description", "") or "")
        for c in (field_configs or [])
    }

    try:
        # Pass 1: settle everything that needs no LLM — local comparators, plus the
        # empty/no-reference short-circuits that would otherwise pay for a call.
        # Whatever is left is batched into ONE request in pass 2.
        scored: Dict[str, Tuple[str, float, str]] = {}   # field -> (kind, score, detail)
        pending: List[Tuple[str, str, str, Any, Any]] = []

        for field_name, ref_value in (reference or {}).items():
            if field_name in _SKIP_FIELDS:
                continue

            got_value = (extracted or {}).get(field_name)
            kind = comparator_for(field_name, got_value, ref_value)

            if kind == "exact":
                scored[field_name] = (kind, *compare_exact(got_value, ref_value))
                continue
            if kind == "set_f1":
                scored[field_name] = (kind, *compare_set_f1(got_value, ref_value))
                continue

            # LLM-backed kinds. Trivial cases resolve without a call.
            if _is_empty(got_value) and _is_empty(ref_value):
                scored[field_name] = (kind, 1.0, "both empty")
                continue
            if _is_empty(ref_value):
                scored[field_name] = (kind, 1.0, "no reference value to match (not penalised)")
                continue
            if _is_empty(got_value):
                scored[field_name] = (
                    kind, 0.0, f"empty; reference={str(ref_value)[:80]!r}"
                )
                continue
            if kind == "semantic_set":
                # Identical strings need no aligner.
                exact_score, exact_detail = compare_set_f1(got_value, ref_value)
                if exact_score == 1.0:
                    scored[field_name] = (kind, 1.0, f"exact set match ({exact_detail})")
                    continue

            pending.append(
                (field_name, kind, descriptions.get(field_name, ""), got_value, ref_value)
            )

        # Pass 2: one call for every remaining field.
        batched: Dict[str, Tuple[float, str]] = {}
        if pending:
            try:
                batched = compare_batched(
                    pending, model=comparator_model,
                    provider=comparator_provider, owner_id=owner_id,
                )
            except Exception as e:
                logger.error(f"[reference_match] Batched compare failed: {e}")

        # Any field the batch skipped or dropped falls back to its own call, so a
        # partial batch response degrades one field rather than the document.
        for field_name, kind, description, got_value, ref_value in pending:
            if field_name in batched:
                scored[field_name] = (kind, *batched[field_name])
                continue
            logger.warning(
                f"[reference_match] Batch returned no verdict for {field_name}; "
                "falling back to a single-field call"
            )
            fn = compare_semantic_set if kind == "semantic_set" else compare_semantic
            scored[field_name] = (kind, *fn(
                field_name, description, got_value, ref_value,
                model=comparator_model, provider=comparator_provider, owner_id=owner_id,
            ))

        # Rebuild in the reference's field order so output stays stable across runs.
        comparisons: List[FieldComparison] = []
        by_kind: Dict[str, List[float]] = {
            "exact": [], "set_f1": [], "semantic": [], "semantic_set": []
        }
        for field_name in (reference or {}):
            if field_name not in scored:
                continue
            kind, score, detail = scored[field_name]
            comparisons.append(FieldComparison(
                field_name=field_name, comparator=kind, score=score, detail=detail
            ))
            by_kind[kind].append(score)

        all_scores = [c.score for c in comparisons]

        # A comparator that errored produced a placeholder score, not a real verdict.
        # Surfacing it keeps a degraded run from reading as a clean one.
        degraded = [c.field_name for c in comparisons if "comparator error" in c.detail]

        result = ReferenceMatchResult(
            field_comparisons=comparisons,
            overall_score=_mean(all_scores),
            exact_score=_mean(by_kind["exact"]),
            # Tag-list fields, however they were aligned (semantic by default).
            list_f1_score=_mean(by_kind["semantic_set"] + by_kind["set_f1"]),
            semantic_score=_mean(by_kind["semantic"]),
            # 0.7 keeps PARTIAL semantic verdicts (0.5) and weak F1 out of "matched".
            mismatched_fields=sorted(c.field_name for c in comparisons if c.score < 0.7),
            fields_compared=len(comparisons),
            duration_ms=int((time.time() - started) * 1000),
            error=(
                f"comparator failed on {len(degraded)} field(s): {', '.join(degraded)}"
                if degraded else None
            ),
        )

        logger.info(
            f"[reference_match] overall={result.overall_score:.2f} "
            f"exact={result.exact_score:.2f} f1={result.list_f1_score:.2f} "
            f"semantic={result.semantic_score:.2f} "
            f"mismatched={result.mismatched_fields or 'none'} ({result.duration_ms}ms)"
        )
        return result

    except Exception as e:
        logger.error(f"[reference_match] Comparison failed: {e}", exc_info=True)
        return ReferenceMatchResult(
            duration_ms=int((time.time() - started) * 1000), error=str(e)
        )
