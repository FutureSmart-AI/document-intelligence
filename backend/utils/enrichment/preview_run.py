"""The single-document, nothing-saved extraction preview (CC6) + field discovery (CC9).

Design: docs/plan/collection-creation-flow/preview-run.md
Decisions: docs/plan/collection-creation-flow/decisions.md ("Decided 2026-09-08")

## What this does, in one paragraph

Step 3 of the create-collection wizard ("Try a sample") shows the user what their
field list would actually pull out of one document, before any collection exists
and before anything is stored. This module is that run: it takes the document's
text and the fields the caller typed, builds the *same* extraction model the real
ingest would build (utils/enrichment/preview_model_builder.py — a pinned port of
the worker's builder), asks the LLM once, and hands back the values plus a list of
fields the document appears to contain that nobody asked for.

Nothing here writes to S3, Qdrant or Postgres — except one usage row, which is the
point of Q1 below.

## Two decisions worth knowing before editing

**CC9 is folded into ONE LLM call, not two.** The endpoint has two jobs — extract
the requested fields, and propose fields the user missed — and preview-run.md left
open whether that is one structured-output call or two. It is one. Three reasons,
in order of weight:

  1. **Cost and latency, on a button designed to be clicked repeatedly.** "Run
     again" is the whole point of step 3. Two calls double the spend and roughly
     double the wait for every click, against a monthly token ceiling the user is
     now charged under (Q1).
  2. **The suggestion job needs the requested-field list anyway.** "What else is
     in this document" is only answerable relative to what was already asked for —
     a second call would have to be given the same field list to avoid proposing
     duplicates, so it re-sends most of the same prompt for no new information.
  3. **One structured schema expresses it cleanly.** The dynamic extraction model
     nests unchanged inside a wrapper with a ``suggested_fields`` list beside it,
     so folding costs nothing in schema fidelity — the inner model's JSON schema
     is byte-identical to the one the parity test checks.

The cost of folding, stated honestly: one model doing two jobs can do the second
one lazily, and a suggestion failure now fails the extraction too. If suggestion
quality turns out poor in practice, splitting is a contained change — build the
second model, make a second ``run_with_key_fallback`` call, and record two usage
events instead of one.

**A preview is metered (Q1).** It costs about one real extraction, so it is
checked against ``max_llm_tokens_per_month`` *before* the call and recorded after
it. This is the **first** pre-flight ``enforce_operation(Operation.llm_call, ...)``
call site in this repo — see the comment at utils/usage/limit_checking.py:376-392,
which states that ``llm_call`` is emitted only by the worker, async and post-hoc,
so that ceiling has never been able to refuse anything. An interactive request is
the first place where refusing it before it spends is even possible.

## The gap this deliberately does NOT close

There is **no per-owner "Run again" rate limit** — no sliding-window limiter
exists anywhere in this repo to build on (``chat/llm_limiter.py`` is a
*process-wide* token bucket, not per-owner). What bounds a preview loop today is
the monthly token ceiling above plus that process-wide throttle. Recorded as a
named, deferred gap in preview-run.md rather than quietly skipped.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

import pydantic
from fastapi import HTTPException
from pydantic import BaseModel, Field

from utils.enrichment.preview_model_builder import (
    PreviewFieldSpec,
    create_dynamic_pydantic_model,
    specs_from_request_fields,
)
from utils.enrichment import model_registry
from utils.logger import logger
from utils.models.enums import CALLER_FIELD_TYPES, label_for_field_type

# The judge path already truncates source text at 24,000 characters, for exactly
# this reason: one click on a 300-page PDF must not turn into an unbounded call.
# Reported back as `document.text_truncated` so the user knows they are looking at
# a partial read rather than a document that answered nothing.
MAX_PREVIEW_TEXT_CHARS = 24_000

# Bounds on the request itself, so the prompt size stays predictable regardless of
# what the wizard sends.
MAX_PREVIEW_FIELDS = 30
MAX_SUGGESTED_FIELDS = 8

# Output budget. The chat default (llm_factory.get_llm -> max_tokens=1000) is sized
# for a chat reply and is far too small here: one response carries a value for every
# requested field (including List[dict] tables) PLUS up to MAX_SUGGESTED_FIELDS
# proposals. Hitting the cap stops the model mid-JSON and surfaces as an unparseable
# response — the same failure that hit POST /enrichment/generate in prod on
# 2026-09-04 (see utils/config/field_generation.py's MAX_GENERATION_OUTPUT_TOKENS).
MAX_PREVIEW_OUTPUT_TOKENS = 4000

# Rough pre-call token estimate. ~4 characters per token is the usual English
# approximation; the fixed part covers the system prompt, and the per-field part
# covers each field's name/description/type in the generated JSON schema. It only
# has to be the right order of magnitude — the real count is recorded after the call.
_CHARS_PER_TOKEN = 4
_FIXED_PROMPT_TOKENS = 600
_TOKENS_PER_FIELD = 60

# Usage accounting. `operation` must be "llm_call" so preview spend counts against
# the same max_llm_tokens_per_month ceiling as real ingest enrichment; `label` is
# deliberately NOT the worker's "document_enrichment" so preview spend stays
# separately visible in analytics — see docs/modules/usage-event-catalog.md's
# (operation, label) discipline.
USAGE_OPERATION = "llm_call"
USAGE_LABEL = "enrichment_preview"
USAGE_SERVICE_TYPE = "knowledge"


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def bad_preview_request(detail: str) -> HTTPException:
    """Build the 400 AND log it, so the reason survives outside the response body.

    A rejected preview leaves nothing in CloudWatch but a bare
    ``"POST ...preview HTTP/1.1" 400`` line: the `detail` goes to the browser
    only. That turned one real 400 into log archaeology (timing the gap between
    the auth line and the completion line to work out how far the handler got),
    so every branch here says out loud what it refused.
    """
    logger.warning(f"[enrichment_preview] rejected request: {detail}")
    return HTTPException(status_code=400, detail=detail)


def parse_fields_json(raw: str) -> List[Dict[str, Any]]:
    """Parse and validate the ``fields`` form value.

    ``fields`` is REQUIRED on this endpoint (unlike the superseded
    knowledge-scoped design): no collection exists at step 3, so there is no
    stored schema to fall back to.

    ``field_type`` inside each entry is NOT required, and this function must not
    reinstate that requirement. The user does not pick a Python type anywhere in
    this product — they describe the field and utils/config/field_type_resolver
    resolves it, which is why `field_type` is optional on both write endpoints
    (routers/enrichment_configuration_new.py::_resolve_missing_field_types).
    Demanding one here made step 3 reject any field the user added by hand,
    while step 2's generated fields (which always carry a type) passed — a 400
    that looked like a caller bug and was ours. An explicitly sent type is still
    validated: a typo is a real error, absence is not.

    Raises HTTPException(400) with a message naming the actual problem — a wizard
    showing "invalid request" for a typo'd type helps nobody. Every rejection is
    also logged, because the detail travels only in the response body: diagnosing
    one of these from CloudWatch alone otherwise means inferring the cause from
    request timing.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise bad_preview_request(f"'fields' is not valid JSON: {exc}")

    if not isinstance(parsed, list) or not parsed:
        raise bad_preview_request("'fields' must be a non-empty JSON array of field objects")
    if len(parsed) > MAX_PREVIEW_FIELDS:
        raise bad_preview_request(
            f"'fields' has {len(parsed)} entries; at most {MAX_PREVIEW_FIELDS} may be previewed in one run"
        )

    seen: set = set()
    out: List[Dict[str, Any]] = []
    for i, field in enumerate(parsed):
        if not isinstance(field, dict):
            raise bad_preview_request(f"'fields[{i}]' must be an object")

        name = str(field.get("field_name") or "").strip()
        if not name:
            raise bad_preview_request(f"'fields[{i}]' is missing 'field_name'")
        if name in seen:
            # Two fields with one name would silently shadow each other in
            # `extracted` — the caller would see one value and never learn why.
            raise bad_preview_request(f"duplicate field_name '{name}' in 'fields'")
        seen.add(name)

        normalized = dict(field)
        normalized["field_name"] = name

        field_type = str(field.get("field_type") or "").strip()
        if not field_type:
            # Left absent on purpose, not defaulted to `str`. The caller resolves
            # it before the run; defaulting here would extract a differently
            # shaped value than the real collection will, which is precisely the
            # lie a preview must not tell.
            normalized.pop("field_type", None)
        elif field_type not in CALLER_FIELD_TYPES:
            raise bad_preview_request(
                f"'fields[{i}].field_type' is '{field_type}', which is not a supported type. "
                f"Supported: {', '.join(sorted(CALLER_FIELD_TYPES))}"
            )
        else:
            normalized["field_type"] = field_type

        out.append(normalized)

    return out


# ---------------------------------------------------------------------------
# The LLM's output shape
# ---------------------------------------------------------------------------

class SuggestedField(BaseModel):
    """One field the document appears to contain that the caller did not ask for (CC9).

    Accepting one is a normal ``POST /v1/{knowledge_id}/enrichment`` call with this
    name/description/type once the collection exists at step 4 — no new write
    endpoint is needed, only the discovery that produces the candidate.
    """

    field_name: str = Field(..., description="snake_case, lowercase, alphanumeric and underscores only")
    description: str = Field(..., description="What this field captures, written for the extraction model")
    field_type: str = Field(..., description="One of: str, List[str], int, float, bool, dict, List[dict], date")
    sample_value: Optional[str] = Field(
        default=None,
        description="The value actually present in THIS document, as a short string — the evidence for the suggestion",
    )
    reason: str = Field(..., description="One short sentence: why this is worth capturing")


def build_preview_output_model(specs: List[PreviewFieldSpec]) -> Tuple[Any, Any]:
    """Build the combined (extraction + suggestions) structured-output model.

    Returns ``(CombinedModel, ExtractionModel)``. The extraction model is returned
    separately because it — not the wrapper — is what the parity test compares
    against the worker's builder. Nesting it changes nothing about its own schema.
    """
    extraction_model = create_dynamic_pydantic_model("PreviewExtractedFields", specs)
    combined = pydantic.create_model(
        "PreviewRunResult",
        extracted=(extraction_model, Field(description="The value of each requested field, read from this document.")),
        suggested_fields=(
            List[SuggestedField],
            Field(default_factory=list, description="Fields present in the document that were NOT requested."),
        ),
    )
    return combined, extraction_model


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You read one document and do two separate jobs in a single answer.

JOB 1 — `extracted`: fill in the requested fields.
- Use only what the document actually states. Never infer, calculate or invent a value.
- If the document does not answer a field, return null for it. A null is a useful, \
correct answer here — it tells the user their field or their document is the wrong one. \
A guessed value is worse than no value.
- Follow each field's own description and type exactly. Dates are ISO-8601 (YYYY-MM-DD).

JOB 2 — `suggested_fields`: propose fields the user did NOT ask for.
- Look at what this document clearly contains and is not already covered by the \
requested fields, and propose at most {max_suggestions} of them.
- Propose only fields you can point at a real value for in THIS document, and put that \
value in `sample_value`. No speculative fields.
- Never propose a field that duplicates or merely renames a requested field. \
The requested field names are: {requested_names}.
- `field_name` is snake_case, lowercase, letters/digits/underscores only.
- `field_type` is exactly one of: {allowed_types}.
- Prefer a flat type (str, List[str], int, float, bool, date) for anything used to filter \
or group. Use List[dict] only for genuinely repeating rows (line items, work history).
- If the document contains nothing worth adding, return an empty list. An empty list is \
a valid, respectable answer.

The document may have been truncated — judge only from the text you are given."""

_HUMAN_TEMPLATE = """Document: {file_name}

--- BEGIN DOCUMENT ---
{document_text}
--- END DOCUMENT ---

Extract the requested fields from the document above, and suggest what else it contains."""


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------

def estimate_prompt_tokens(document_text: str, field_count: int) -> int:
    """Order-of-magnitude pre-call estimate, for the quota pre-flight check."""
    return (
        len(document_text) // _CHARS_PER_TOKEN
        + _FIXED_PROMPT_TOKENS
        + _TOKENS_PER_FIELD * max(field_count, 0)
    )


async def _enforce_llm_quota(owner_id: str, estimated_tokens: int) -> None:
    """Refuse (429) if this preview would push the owner past their monthly LLM budget.

    Imported lazily so this module has no import-time dependency on the usage
    package — same pattern as field_generation.py's lazy chat imports.
    """
    from utils.pydantic_models_usage_limits import EstimatedMetric, Operation
    from utils.usage.enforcement import enforce_operation

    # Raises HTTPException(429) on its own when over quota and enforcement is on;
    # fails open (returns None) if the usage DB is unreachable. Nothing to catch.
    await enforce_operation(
        owner_id,
        Operation.llm_call,
        estimated_metrics=[EstimatedMetric(name="input_tokens", value=float(estimated_tokens))],
        source="enrichment_preview",
    )


async def _record_preview_usage(
    *,
    owner_id: str,
    provider: str,
    model: str,
    key_type: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
    reasoning_effort: Optional[str] = None,
) -> None:
    """Write the one usage row this endpoint produces. Never raises.

    Metering must not be able to fail a preview the user already paid for — a
    dropped row costs an analytics line; a raised exception costs the answer.
    """
    from utils.pydantic_models_usage_events import UsageEventRequest, UsageMetric
    from utils.usage.event_recording import record_usage_events

    try:
        event = UsageEventRequest(
            knowledge_id=None,  # deferred creation (Q2) — there is no collection yet
            key_type=key_type,
            service_type=USAGE_SERVICE_TYPE,
            operation=USAGE_OPERATION,
            label=USAGE_LABEL,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            metadata={"provider": provider, "model": model, "persisted": False,
                      **({"reasoning_effort": reasoning_effort} if reasoning_effort else {})},
            metrics=[
                UsageMetric(name="input_tokens", value=float(input_tokens)),
                UsageMetric(name="output_tokens", value=float(output_tokens)),
                UsageMetric(name="total_tokens", value=float(input_tokens + output_tokens)),
                UsageMetric(name="duration_ms", value=float(duration_ms)),
            ],
        )
        await record_usage_events(owner_id, [event])
    except Exception as exc:
        logger.error(f"[enrichment_preview] failed to record usage for owner_id={owner_id}: {exc}")


def _wire_call(model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``(wire model, reasoning effort)`` for an enrichment model name.

    A registry model (utils/enrichment/model_registry.py, the API's mirror of the
    worker's ENRICHMENT_MODELS) is called as the worker calls it: its ``api_model`` at
    its registry effort. Any other name passes through with no effort pinned, and the
    chat factory's defaults apply as before.
    """
    entry = model_registry.get(model)
    if entry is None:
        return model, None
    effort = entry.reasoning_effort if entry.provider == "openai" else None
    return model_registry.wire_model(model), effort


class PreviewModels(NamedTuple):
    """The primary and fallback model a brand-new collection would enrich with."""
    provider: Optional[str]
    model: Optional[str]
    temperature: float
    fallback_provider: Optional[str]
    fallback_model: Optional[str]
    source: str


async def _resolve_preview_model() -> PreviewModels:
    """The models a brand-new collection would enrich with — the SAME chain the
    worker's extraction follows (workers/celery_utils/enrichment/llm_factory.py
    ``get_enrichment_config`` levels 3-5 and ``get_fallback_enrichment_llm``):

      primary:  enrichment_default_config row -> ENRICHMENT_MODEL env -> chat default
      fallback: row's enrichment_fallback_model -> ENRICHMENT_FALLBACK_MODEL env

    With no knowledge base there is no per-collection override to read. Until
    2026-10-26 this read only the row and had no fallback at all: a standalone
    install (no row) previewed on the chat default instead of the profile's
    extraction model, and a primary outage (Gemini 402 since 2026-10-23) failed the
    preview while real extraction quietly succeeded on the fallback. A preview must
    not fail because an admin table is unreachable either — env applies then.
    """
    row: Dict[str, Any] = {}
    try:
        from utils.db.knowledge_setting_ops import get_enrichment_default_config

        row = await get_enrichment_default_config() or {}
    except Exception as exc:
        logger.warning(f"[enrichment_preview] could not read enrichment_default_config, using env defaults: {exc}")

    env_model = (os.getenv("ENRICHMENT_MODEL") or "").strip() or None
    env_fallback = (os.getenv("ENRICHMENT_FALLBACK_MODEL") or "").strip() or None
    if row.get("enrichment_model"):
        provider, model, source = row.get("enrichment_provider"), row["enrichment_model"], "global"
        temperature = float(row.get("enrichment_temperature") or 0.0)
    else:
        provider, model, source, temperature = None, env_model, "env" if env_model else "default", 0.0
    if row.get("enrichment_fallback_model"):
        fb_provider, fb_model = row.get("enrichment_fallback_provider"), row["enrichment_fallback_model"]
    else:
        fb_provider, fb_model = None, env_fallback
    if fb_model and fb_model == model:
        fb_provider = fb_model = None
    return PreviewModels(provider, model, temperature, fb_provider, fb_model, source)


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------

def _is_missing(value: Any) -> bool:
    """Did the document fail to answer this field?

    ``status: "not_found"`` must be distinguishable from a failed extraction (which
    is a 502, not an empty value) — it is the single most useful signal in step 3,
    because it is how a user learns their field is wrong or their document is the
    wrong kind. An empty list/dict/string counts as not-found for the same reason a
    null does: the document said nothing.
    """
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def build_fields_run(specs: List[PreviewFieldSpec], extracted: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-field status rows for the response, in the order the caller sent them."""
    rows = []
    for spec in specs:
        value = extracted.get(spec.field_name)
        rows.append({
            "field_name": spec.field_name,
            "field_type": spec.field_type,
            # Echo the caller's own label when they sent one — replacing it with the
            # canonical one would silently undo their edit (same rule the write path
            # follows in utils/config/field_type_resolver.py).
            "field_type_label": spec.field_type_label or label_for_field_type(spec.field_type),
            "status": "not_found" if _is_missing(value) else "extracted",
        })
    return rows


def filter_suggested_fields(
    suggestions: List[SuggestedField],
    requested_names: set,
) -> List[Dict[str, Any]]:
    """Drop suggestions that are unusable, and shape the rest for the response.

    Independently validated rather than trusted, exactly as
    field_generation._validate_and_filter validates its own LLM output: a
    hallucinated ``field_type`` is dropped, never silently coerced to ``str``.
    """
    out: List[Dict[str, Any]] = []
    seen = set(requested_names)
    for s in suggestions:
        name = (s.field_name or "").strip().lower().replace(" ", "_")
        if not name or name in seen:
            continue
        if s.field_type not in CALLER_FIELD_TYPES:
            logger.info(f"[enrichment_preview] dropping suggested field '{name}': unsupported field_type '{s.field_type}'")
            continue
        seen.add(name)
        out.append({
            "field_name": name,
            "description": s.description,
            "field_type": s.field_type,
            "field_type_label": label_for_field_type(s.field_type),
            "sample_value": s.sample_value,
            "reason": s.reason or "Present in the document but not among the requested fields",
        })
        if len(out) >= MAX_SUGGESTED_FIELDS:
            break
    return out


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

async def run_preview(
    *,
    owner_id: str,
    document_text: str,
    file_name: str,
    fields: List[Dict[str, Any]],
    document_processing_mode: str,
    text_truncated: bool,
) -> Dict[str, Any]:
    """Run one preview: quota check, one LLM call, usage row, response payload.

    Persists nothing but the usage row. Raises HTTPException(429) via
    ``enforce_operation`` when the owner is over budget, and HTTPException(502)
    when the provider fails — never an empty ``extracted`` on failure, because a
    failed run and a document that answers nothing demand opposite user reactions.
    """
    specs = specs_from_request_fields(fields)
    combined_model, _extraction_model = build_preview_output_model(specs)

    estimated_tokens = estimate_prompt_tokens(document_text, len(specs))
    await _enforce_llm_quota(owner_id, estimated_tokens)

    models = await _resolve_preview_model()
    provider, model, temperature = models.provider, models.model, models.temperature

    # Lazily imported, mirroring field_generation.py: this module is reached from
    # the enrichment path, not from chat, and must not pull chat's LLM plumbing in
    # at import time.
    from chat.byok_fallback import run_with_key_fallback
    from chat.llm_factory import HARDCODED_DEFAULT_MODEL
    from langchain_core.prompts import ChatPromptTemplate

    system = _SYSTEM_PROMPT.format(
        max_suggestions=MAX_SUGGESTED_FIELDS,
        requested_names=", ".join(s.field_name for s in specs),
        allowed_types=", ".join(sorted(CALLER_FIELD_TYPES)),
    )
    prompt = ChatPromptTemplate.from_messages([("system", system), ("human", _HUMAN_TEMPLATE)])
    invoke_payload = {"file_name": file_name, "document_text": document_text}

    # include_raw gives us the raw AIMessage alongside the parsed model, which is
    # the only place usage_metadata (the real token counts) survives structured
    # output. Some provider integrations reject the kwarg — fall back to the plain
    # call and record zero tokens rather than failing the preview over metering.
    raw_holder: Dict[str, Any] = {"raw": None}

    async def _invoke(llm):
        try:
            structured_llm = llm.with_structured_output(combined_model, include_raw=True)
            result = await (prompt | structured_llm).ainvoke(invoke_payload)
            if isinstance(result, dict) and "parsed" in result:
                raw_holder["raw"] = result.get("raw")
                return result.get("parsed")
            return result
        except TypeError:
            structured_llm = llm.with_structured_output(combined_model)
            return await (prompt | structured_llm).ainvoke(invoke_payload)

    async def _run(run_provider, run_model, run_temperature):
        # A registry name runs exactly as extraction runs it: its wire model at its
        # reasoning effort ("gpt-5.6-luna-low" -> gpt-5.6-luna at "low"). Sending the
        # registry name itself would 404 at the provider.
        wire, effort = _wire_call(run_model)
        return await run_with_key_fallback(
            _invoke,
            owner_id=owner_id,
            knowledge_id=None,  # deferred creation (Q2): the owner-only credential path
            source="enrichment_preview",
            provider=run_provider,
            model=wire,
            temperature=run_temperature,
            max_tokens=MAX_PREVIEW_OUTPUT_TOKENS,
            for_function_calling=True,
            **({"reasoning_effort": effort} if effort else {}),
        )

    started = time.perf_counter()
    requested_model = model or HARDCODED_DEFAULT_MODEL
    fallback_reason: Optional[str] = None
    try:
        try:
            outcome = await _run(provider, model, temperature)
            parsed = outcome.value
            if parsed is None and models.fallback_model:
                raise ValueError("primary model returned no parseable result")
        except HTTPException:
            raise
        except Exception as primary_exc:
            if not models.fallback_model:
                raise
            # Same rule as extraction (llm_retry.invoke_with_retry -> fallback LLM):
            # a primary that cannot answer is retried once on the configured fallback.
            fallback_reason = f"{type(primary_exc).__name__}: {primary_exc}"[:300]
            logger.warning(
                f"[enrichment_preview] primary {requested_model} failed ({fallback_reason}); "
                f"retrying on fallback {models.fallback_model}"
            )
            raw_holder["raw"] = None
            outcome = await _run(models.fallback_provider, models.fallback_model, 0.0)
            model = models.fallback_model
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"[enrichment_preview] extraction failed for owner_id={owner_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="The extraction provider failed to read this document. Nothing was saved — try again.",
        )
    duration_ms = int((time.perf_counter() - started) * 1000)

    parsed = outcome.value
    if parsed is None:
        # A structured-output call that came back unparseable. Same rule as above:
        # this is a failure, not a document that answered nothing.
        logger.error(f"[enrichment_preview] provider returned no parseable result for owner_id={owner_id}")
        raise HTTPException(status_code=502, detail="The extraction provider returned an unreadable result. Nothing was saved — try again.")

    # mode="json" so a `date` field comes back as "2026-03-12", not a date object
    # the response serializer would have to guess at.
    extracted = parsed.extracted.model_dump(mode="json")
    suggested = filter_suggested_fields(
        list(getattr(parsed, "suggested_fields", []) or []),
        requested_names={s.field_name for s in specs},
    )

    usage = getattr(raw_holder["raw"], "usage_metadata", None) or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    resolved_model = model or HARDCODED_DEFAULT_MODEL
    # Billed and priced as the wire model; the registry name stays as requested_model.
    served_model, served_effort = _wire_call(resolved_model)

    await _record_preview_usage(
        owner_id=owner_id,
        provider=outcome.provider,
        model=served_model,
        reasoning_effort=served_effort,
        key_type=outcome.key_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
    )

    return {
        "extracted": extracted,
        "fields_run": build_fields_run(specs, extracted),
        "suggested_fields": suggested,
        "document": {
            "file_name": file_name,
            "document_processing_mode": document_processing_mode,
            "text_chars": len(document_text),
            "text_truncated": text_truncated,
        },
        "run": {
            "provider": outcome.provider,
            "model": served_model,
            "reasoning_effort": served_effort,
            "key_type": outcome.key_type,
            "requested_model": requested_model,
            "fallback": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # Stated in the payload, not merely implied: the whole product promise
            # of step 3 is that nothing was saved, and the UI should be able to
            # render that from the response rather than assert it from nothing.
            "persisted": False,
        },
    }
