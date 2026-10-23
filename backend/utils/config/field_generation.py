"""LLM-driven enrichment field generation for collection creation.

Called from ``POST /v1/{knowledge_id}/enrichment/generate``
(routers/enrichment_configuration_new.py) — the Document Intelligence "Create
document collection" modal's flow: the user names the collection, writes what
it's about, optionally writes extraction instructions, and attaches files.
Instead of asking the user to hand-configure fields (the "imagination task"
problem — see docs/plan/field-and-retrieval-precision/active-plan.md, Part 1)
or picking a fixed preset (``field_template``), an LLM proposes the schema
from everything the user already gave the form.

Design, per 2026-09-01 sign-off:
  - Inputs: collection_name, service_description, extraction_instructions
    (new — migration 051), and a cheap local text peek of the attached files
    (no LlamaParse spend; see peek_document_text below).
  - The four PROTECTED_SYSTEM_FIELDS (summary, document_type, chunk_idx,
    chunk_text — utils/config/enrichment_config.py) are the floor: they
    always exist and the LLM is never asked about them.
  - The optional default fields (title, topics, keywords, ...) are NOT part
    of this call's input or output (2026-09-22). The wizard no longer seeds
    them (PUT /config seed_optional_defaults=false), so asking the LLM for a
    keep/drop verdict on fields that don't exist only cost output tokens.
    The LLM just proposes the fields this corpus needs; a proposal is
    rejected only if it clashes with a protected field or a field the
    collection already has.
  - New fields follow the same facet/structure split
    documented in fs-agent-workers/docs/plan/document-processing/enrichment-fields.md: flat
    str/List[str] fields carry filterable facets, List[dict] fields carry
    structure. Every generated field is re-validated against
    validate_field_configuration_sqlmodel_v2 before being written — a
    hallucinated field_type is dropped, not silently coerced to str the way
    utils/models/enrichment.py's FieldType coercion does today.
  - Auto-applied, not a review step (2026-09-01 decision) — the create modal
    has no room for one. Generated fields are editable afterwards like any
    other field.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from utils.logger import logger
from utils.config.enrichment_config import PROTECTED_SYSTEM_FIELDS
from utils.db.enrichment_db_utils import validate_field_configuration_sqlmodel_v2
from utils.models.enums import CALLER_FIELD_TYPES, label_for_field_type
from utils.config.field_type_resolver import (
    MONETARY_DESCRIPTION_SUFFIX,
    TYPE_RULES_BLOCK,
    ensure_currency_field,
)
from utils.config.field_type_resolver import (
    _ALLOWED_SCHEMA_VALUE_TYPES as _ALLOWED_SCHEMA_VALUE_TYPES,
)

# The types a caller may send, straight from the enum — NOT a local literal.
# This list previously duplicated the seven values inline, which is how `date`
# came to be missing from it: adding the type in one place would have left this
# flow dropping every date field it proposed, reported as a "hallucinated type".
_ALLOWED_FIELD_TYPES = CALLER_FIELD_TYPES
_ALLOWED_LEVELS = {"document", "chunk"}

# Bounds that keep the generation call — and the enrichment cost of whatever
# it produces — predictable regardless of how verbose the user's prompt or
# how many files they attached.
# Enforced in code only, never stated in the prompt — the model proposes what
# the corpus needs, and every valid field past this many is returned in
# rejected_fields with a reason instead of being silently sliced off (the
# 12-field cap used to drop a 13th proposal with no trace in the response).
MAX_GENERATED_FIELDS = 20
MAX_PEEKED_FILES = 5
MAX_CHARS_PER_DOC_PEEK = 3000
# Output budget for the generation call. A live run (01a0ca42…, 2026-09-22)
# spent 1 203 output tokens on 13 fields plus default verdicts, so this leaves
# room for well over 20 fields — far above the chat default of 1000.
MAX_GENERATION_OUTPUT_TOKENS = 4000


class GeneratedFieldSpec(BaseModel):
    """One LLM-proposed enrichment field, in the shape
    FieldConfigurationRequestV2 (utils/models/enrichment.py) accepts."""
    field_name: str = Field(..., description="snake_case, alphanumeric + underscore only")
    field_type: str = Field(..., description="One of: str, List[str], int, float, bool, dict, List[dict], date")
    is_monetary: bool = Field(
        default=False,
        description="True if this field (or any numeric sub-field of it) holds a sum of money — "
                    "an amount, balance, price, salary, total, fee or tax.",
    )
    description: str = Field(..., description="What to extract and how to normalize it — shown to the enrichment LLM verbatim")
    level: str = Field(default="document", description="'document' or 'chunk'")
    is_required: bool = Field(
        default=False,
        description="True only if near-certain every document in this corpus carries this fact. "
                    "A required field on an absent fact forces the extraction LLM to fabricate a value.",
    )
    restrict_to_choices: bool = Field(default=False)
    sample_values: Optional[List[str]] = Field(
        default=None, description="2-6 example values; required if restrict_to_choices is true"
    )
    max_items: Optional[int] = Field(default=None, description="Cap for List[str] fields only")
    field_schema: Optional[Dict[str, str]] = Field(
        default=None,
        description="Flat {subfield: type} map, one level deep, only for dict/List[dict] field_type. "
                    "Subfield types: str, int, float, bool, List[str], date.",
    )

    @field_validator("field_name")
    @classmethod
    def _normalize_field_name(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")


class FieldGenerationResponse(BaseModel):
    """The LLM's raw structured output."""
    new_fields: List[GeneratedFieldSpec]


class FieldGenerationResult(BaseModel):
    """Post-validation result: what actually gets written, plus everything
    that was dropped and why — returned to the caller for the response body
    and logged, never silently discarded."""
    accepted_fields: List[Dict[str, Any]]
    rejected_fields: List[Dict[str, str]]  # {"field_name": ..., "reason": ...}
    provider: str
    model: str
    key_type: str
    # Real token counts when the provider exposes usage_metadata (via the
    # include_raw=True call below), 0 otherwise. The knowledge-scoped caller
    # ignores these; the owner-scoped Step 1 orchestration (run_generate_preview,
    # below) needs them to write an honest usage row, the same way
    # preview_run.py's run_preview does for CC6/CC9.
    input_tokens: int = 0
    output_tokens: int = 0


_SYSTEM_PROMPT = """You design metadata extraction schemas for a document knowledge base (RAG system).

Given a collection's name, description, the user's extraction intent, and a peek at a few of \
their actual documents, propose the metadata fields this corpus would actually benefit from, \
driven by what the user said they want to filter/search by (extraction instructions) and what the \
document previews actually show. Rules, all load-bearing:
  - Never propose a field named the same as (or a close variant of) one of these always-present \
system fields: {protected_names}.
  - Choose each field's field_type by the rules below, which are the same rules the single-field \
type resolver uses — a field described the same way must get the same type whichever flow \
created it.
  - A field meant to FILTER or FACET the corpus (a category, a status, a low-cardinality tag) must \
be flat — "str" or "List[str]". Give it a short, reusable value shape in the \
description (e.g. "short, lowercase, reusable label"), and 2-6 sample_values. Facets and \
choice restriction only work on flat str/List[str] fields.
  - A field meant to capture STRUCTURE (line items, work history, parties to a contract) should be \
field_type "List[dict]" with a field_schema — a FLAT one-level map of subfield name to type \
(str/int/float/bool/List[str]/date only — no nested dicts).
  - Set is_required=true only when you are confident nearly every document in this corpus carries \
that fact — a required field on an absent fact makes the extraction LLM invent a value, which is \
worse than not having the field.
  - Set restrict_to_choices=true only when you can also give a closed, exhaustive sample_values \
list (e.g. a fixed status enum) — never for open-ended text.
  - Fewer, well-targeted fields beat many speculative ones.
  - level is "document" unless the field only makes sense per-chunk (rare — prefer document-level).

If the document previews are empty or unhelpful, rely on collection_name, service_description and \
extraction_instructions alone — do not invent fields with no basis in any of the given signal.

Never propose a field whose job is to hold a currency code or symbol, and never put a currency \
sub-field inside a table. Mark any field holding money with is_monetary instead — the currency is \
captured once, at document level, and is added for you.

{type_rules_block}
"""

_HUMAN_TEMPLATE = """Collection name: {collection_name}
Description (what these documents are about): {service_description}
Extraction instructions (what the user wants to filter/search by): {extraction_instructions}

Document previews (first ~{max_chars} characters of up to {max_files} attached files):
{doc_previews_block}
"""


def _build_doc_previews_block(doc_previews: List[Tuple[str, str]]) -> str:
    if not doc_previews:
        return "(none attached / none could be read)"
    parts = []
    for filename, text in doc_previews[:MAX_PEEKED_FILES]:
        snippet = (text or "")[:MAX_CHARS_PER_DOC_PEEK]
        parts.append(f"--- {filename} ---\n{snippet}")
    return "\n\n".join(parts)


@traceable(name="EnrichmentFieldGeneration")
async def generate_field_schema(
    *,
    owner_id: Optional[str],
    knowledge_id: Optional[str],
    collection_name: Optional[str],
    service_description: Optional[str],
    extraction_instructions: Optional[str],
    doc_previews: List[Tuple[str, str]],
    existing_field_names: Iterable[str] = (),
) -> FieldGenerationResult:
    """Call the LLM to propose a field schema, then validate and filter its
    output. ``existing_field_names`` is whatever the collection already has
    (empty for the pre-creation wizard call) — a proposal reusing one of those
    names is rejected, since writing it would overwrite that field.

    Raises on a genuine LLM/provider failure — the caller (the
    /enrichment/generate endpoint) decides how to surface that; this function
    does not silently fall back to "no fields", because that would look
    identical to a legitimate "corpus supports nothing new" result.

    ``@traceable`` gives this call a named root LangSmith run — same
    convention as chat/graph.py's ``KnowledgeChat`` root span. Everything the
    LLM does (the structured-output chat-model call) auto-traces as a child of
    it. Best-effort: with tracing off or the SDK unhappy,
    ``get_current_run_tree()`` returns None and generation proceeds untraced —
    this must never block a collection from being created.
    """
    try:
        rt = get_current_run_tree()
        if rt is not None:
            if collection_name:
                rt.name = f"EnrichmentFieldGeneration - {collection_name[:80]}"
            rt.tags = list(rt.tags or []) + ["enrichment", "field_generation"]
            rt.metadata.update({
                "knowledge_id": knowledge_id,
                "owner_id": owner_id,
                "has_extraction_instructions": bool(extraction_instructions),
                "doc_previews_count": len(doc_previews),
            })
    except Exception as e:  # tracing must never break collection creation
        logger.debug(f"[field_generation] could not annotate LangSmith run tree: {e}")

    # Imported lazily (mirrors chat/follow_up.py's own pattern) so this module
    # has no import-time dependency on the chat package's LLM plumbing —
    # field generation runs from the collection-creation path, not chat.
    from chat.byok_fallback import run_with_key_fallback
    from chat.llm_factory import HARDCODED_DEFAULT_MODEL
    from langchain_core.prompts import ChatPromptTemplate

    system = _SYSTEM_PROMPT.format(
        protected_names=", ".join(sorted(PROTECTED_SYSTEM_FIELDS)),
        type_rules_block=TYPE_RULES_BLOCK,
    )
    human = _HUMAN_TEMPLATE.format(
        collection_name=collection_name or "(untitled)",
        service_description=service_description or "(not provided)",
        extraction_instructions=extraction_instructions or "(not provided)",
        doc_previews_block=_build_doc_previews_block(doc_previews),
        max_chars=MAX_CHARS_PER_DOC_PEEK,
        max_files=MAX_PEEKED_FILES,
    )
    prompt = ChatPromptTemplate.from_messages([("system", system), ("human", human)])

    # include_raw=True surfaces the raw AIMessage alongside the parsed model —
    # the only place usage_metadata (real token counts) survives structured
    # output. Same resilience pattern as preview_run.py's run_preview: some
    # provider integrations reject the kwarg, so fall back to the plain call
    # and report 0 tokens rather than failing generation over metering.
    raw_holder: Dict[str, Any] = {"raw": None}

    async def _invoke(llm):
        try:
            structured_llm = llm.with_structured_output(FieldGenerationResponse, include_raw=True)
            result = await (prompt | structured_llm).ainvoke({})
            if isinstance(result, dict) and "parsed" in result:
                raw_holder["raw"] = result.get("raw")
                return result.get("parsed")
            return result
        except TypeError:
            structured_llm = llm.with_structured_output(FieldGenerationResponse)
            return await (prompt | structured_llm).ainvoke({})

    outcome = await run_with_key_fallback(
        _invoke,
        owner_id=owner_id,
        knowledge_id=knowledge_id,
        source="field_generation",
        temperature=0.0,
        # The chat default (llm_factory.get_llm -> max_tokens=1000) is sized for a
        # chat reply and is far too small here: one response carries every
        # proposed field definition (20+ is possible). Hitting the cap stops the
        # model mid-JSON, which surfaces as "Could not parse response content as the
        # length limit was reached" and a 500 on POST /enrichment/generate
        # (seen in prod 2026-09-04, completion_tokens=1000 exactly).
        max_tokens=MAX_GENERATION_OUTPUT_TOKENS,
        for_function_calling=True,
    )
    response: FieldGenerationResponse = outcome.value

    existing_names = set(existing_field_names)
    accepted, rejected = _validate_and_filter(response.new_fields, existing_names)
    logger.info(
        f"[field_generation] knowledge_id={knowledge_id}: model proposed "
        f"{len(response.new_fields)} field(s) — accepted {len(accepted)}, rejected {len(rejected)}"
    )

    # Same money rule as the single-field resolver: an amount is a bare number,
    # and the currency it is denominated in is one document-level field. Applied
    # here too so a generated schema is never a set of amounts with nothing
    # saying what they are denominated in — a failure that is invisible, since
    # 1200 looks equally valid whether it meant INR or USD. Added after the
    # MAX_GENERATED_FIELDS cap on purpose: it is system-added, not a model
    # proposal, and dropping it would leave the amounts uninterpretable.
    accepted.extend(ensure_currency_field(
        any_monetary=any(f.get("_is_monetary") for f in accepted),
        submitted=accepted,
        existing_field_names=sorted(existing_names),
    ))
    for field in accepted:
        field.pop("_is_monetary", None)

    usage = getattr(raw_holder["raw"], "usage_metadata", None) or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)

    return FieldGenerationResult(
        accepted_fields=accepted,
        rejected_fields=rejected,
        provider=outcome.provider,
        model=HARDCODED_DEFAULT_MODEL,
        key_type=outcome.key_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _validate_and_filter(
    fields: List[GeneratedFieldSpec],
    existing_field_names: Iterable[str] = (),
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Re-validate every generated field the same way a human-submitted POST
    /enrichment field would be validated, plus the extra guards a
    free-generating LLM specifically needs (name collisions, a hard type
    allow-list instead of the silent str-coercion FieldType.field_type
    normally falls back to). Never raises — a bad field is dropped with a
    reason, not written or coerced.

    The MAX_GENERATED_FIELDS cap counts ACCEPTED fields, checked last — so an
    invalid proposal early in the list never costs a valid one its slot, and
    every valid field past the cap is reported in rejected, not discarded."""
    existing_names = set(existing_field_names)
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    seen_names = set()

    for field in fields:
        name = field.field_name

        if not _NAME_RE.match(name or ""):
            rejected.append({"field_name": name, "reason": "field_name must be lowercase alphanumeric/underscore"})
            continue
        if name in PROTECTED_SYSTEM_FIELDS:
            rejected.append({"field_name": name, "reason": "collides with a protected system field"})
            continue
        if name in existing_names:
            rejected.append({"field_name": name, "reason": "collides with a field this collection already has"})
            continue
        if name in seen_names:
            rejected.append({"field_name": name, "reason": "duplicate field_name in generated output"})
            continue
        if field.field_type not in _ALLOWED_FIELD_TYPES:
            rejected.append({"field_name": name, "reason": f"unrecognized field_type '{field.field_type}' — refusing to silently coerce to str"})
            continue
        if field.level not in _ALLOWED_LEVELS:
            rejected.append({"field_name": name, "reason": f"invalid level '{field.level}'"})
            continue
        if field.field_schema:
            if field.field_type not in ("dict", "List[dict]"):
                rejected.append({"field_name": name, "reason": "field_schema only allowed for dict/List[dict]"})
                continue
            bad_subfields = [k for k, v in field.field_schema.items() if v not in _ALLOWED_SCHEMA_VALUE_TYPES]
            if bad_subfields:
                rejected.append({"field_name": name, "reason": f"field_schema has unsupported subfield type(s): {bad_subfields}"})
                continue
        if field.restrict_to_choices and not field.sample_values:
            # Recoverable — the field is still useful, just not choice-restricted.
            field.restrict_to_choices = False

        description = field.description
        if field.is_monetary:
            # Tell the extraction model the number carries no symbol or
            # separators, and where the currency lives. Same wording the
            # resolver uses, so the two flows produce identical field text.
            description = (description or "").rstrip() + MONETARY_DESCRIPTION_SUFFIX

        field_dict: Dict[str, Any] = {
            "field_name": name,
            "field_type": field.field_type,
            # Persisted with the field, not derived on read. /generate is the
            # main way fields come into existence, so if it did not write a
            # label, most rows in a new collection would have none.
            "field_type_label": label_for_field_type(field.field_type),
            "description": description,
            "level": field.level,
            "is_custom": True,
            "is_required": field.is_required,
            "restrict_to_choices": field.restrict_to_choices,
            "sample_values": field.sample_values,
            "max_items": field.max_items,
        }
        if field.field_schema:
            field_dict["schema"] = field.field_schema

        errors = validate_field_configuration_sqlmodel_v2(field_dict, is_update=False)
        if errors:
            rejected.append({"field_name": name, "reason": "; ".join(errors)})
            continue

        seen_names.add(name)
        if len(accepted) >= MAX_GENERATED_FIELDS:
            # Valid, but over the cap. seen_names is still updated above so a
            # later repeat of this name is reported as a duplicate, not a
            # second over-cap entry.
            rejected.append({"field_name": name, "reason": f"over the {MAX_GENERATED_FIELDS}-field limit"})
            continue
        # Private marker, stripped in generate_field_schema once the currency
        # decision is made — it is not a field_configurations column.
        field_dict["_is_monetary"] = field.is_monetary
        accepted.append(field_dict)

    return accepted, rejected


def deactivate_chunk_fields_if_disabled(
    existing_fields: List[Dict[str, Any]],
    verdict_updates: List[Dict[str, Any]],
    chunk_level_metadata_enabled: bool,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Force every active chunk-level field off when this knowledge base has
    chunk-level metadata disabled.

    Called from the /enrichment/generate router (with an empty
    verdict_updates list since LLM default verdicts were removed 2026-09-22;
    the parameter stays so any queued update is still corrected). Needed because the endpoint's own default-field seeding step
    (initialize_default_field_configurations_sqlmodel) creates/restores every
    DEFAULT_FIELD_CONFIGURATIONS row unconditionally, level included — unlike
    PUT /config's seeding (initialize_field_configurations_by_template), which
    only ever creates a chunk-level row when chunk_level_metadata is True. The
    LLM is never even asked about the two protected chunk fields
    (chunk_idx/chunk_text — PROTECTED_SYSTEM_FIELDS), so nothing upstream can
    turn them back off. Confirmed live 2026-09-01: a collection with
    chunk_level_metadata=False came back from this endpoint with all 5
    chunk-level fields active.

    This override wins over anything already queued — an already-queued
    update for a chunk field (e.g. a stray verdict that would have left it
    active) is corrected in place to ``is_active: False``, not left as-is.

    Returns the (possibly extended/corrected) verdict_updates list, plus the
    plain list of field names deactivated — reported back to the caller,
    never applied silently.
    """
    if chunk_level_metadata_enabled:
        return verdict_updates, []

    updates_by_id = {u["id"]: dict(u) for u in verdict_updates}
    order = [u["id"] for u in verdict_updates]
    deactivated: List[str] = []
    for f in existing_fields:
        if f.get("level") != "chunk" or not f.get("is_active", True):
            continue
        deactivated.append(f["field_name"])
        if f["id"] not in updates_by_id:
            order.append(f["id"])
        updates_by_id[f["id"]] = {"id": f["id"], "field_name": f["field_name"], "is_active": False}

    return [updates_by_id[i] for i in order], deactivated


# ---------------------------------------------------------------------------
# Owner-scoped, pre-creation generation (Step 1 of the create-collection
# wizard — docs/plan/collection-creation-flow/). Reuses generate_field_schema
# unchanged (it already accepts knowledge_id=None); the only new work here is
# the quota pre-flight and usage accounting, mirroring
# utils/enrichment/preview_run.py's run_preview for the Step 3 preview
# endpoint. Nothing is persisted — there is no knowledge_id to write to, so
# this is a propose-only call by construction, not by a flag.
# ---------------------------------------------------------------------------

# Same order-of-magnitude approach as preview_run.estimate_prompt_tokens. The
# fixed cost is higher here: the system prompt embeds TYPE_RULES_BLOCK, which
# preview's prompt does not carry. Kept at 900 after the default-field block
# was removed (2026-09-22) — an over-estimate only makes the quota check
# slightly stricter, never lets a call through that shouldn't be.
_GEN_CHARS_PER_TOKEN = 4
_GEN_FIXED_PROMPT_TOKENS = 900

GENERATE_PREVIEW_USAGE_OPERATION = "llm_call"
# Distinct from both the knowledge-scoped /generate (which records no usage
# event at all today) and preview's "enrichment_preview" label, so this call's
# spend stays separately visible in analytics.
GENERATE_PREVIEW_USAGE_LABEL = "enrichment_generate_preview"
GENERATE_PREVIEW_USAGE_SERVICE_TYPE = "knowledge"


def estimate_generation_prompt_tokens(
    service_description: Optional[str],
    extraction_instructions: Optional[str],
    doc_previews: List[Tuple[str, str]],
) -> int:
    """Order-of-magnitude pre-call estimate, for the quota pre-flight check."""
    text_chars = len(service_description or "") + len(extraction_instructions or "")
    text_chars += sum(len(text or "") for _, text in doc_previews[:MAX_PEEKED_FILES])
    return text_chars // _GEN_CHARS_PER_TOKEN + _GEN_FIXED_PROMPT_TOKENS


async def _enforce_generation_quota(owner_id: str, estimated_tokens: int) -> None:
    """Refuse (429) if this generation call would push the owner past their
    monthly LLM budget. Same call site preview_run.py uses; lazily imported so
    this module has no import-time dependency on the usage package."""
    from utils.pydantic_models_usage_limits import EstimatedMetric, Operation
    from utils.usage.enforcement import enforce_operation

    await enforce_operation(
        owner_id,
        Operation.llm_call,
        estimated_metrics=[EstimatedMetric(name="input_tokens", value=float(estimated_tokens))],
        source="enrichment_generate_preview",
    )


async def _record_generation_usage(
    *,
    owner_id: str,
    provider: str,
    model: str,
    key_type: str,
    input_tokens: int,
    output_tokens: int,
    duration_ms: int,
) -> None:
    """Write the one usage row this call produces. Never raises — metering
    must not be able to fail a call the user already paid for."""
    from utils.pydantic_models_usage_events import UsageEventRequest, UsageMetric
    from utils.usage.event_recording import record_usage_events

    try:
        event = UsageEventRequest(
            knowledge_id=None,  # deferred creation (Q2) — there is no collection yet
            key_type=key_type,
            service_type=GENERATE_PREVIEW_USAGE_SERVICE_TYPE,
            operation=GENERATE_PREVIEW_USAGE_OPERATION,
            label=GENERATE_PREVIEW_USAGE_LABEL,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            metadata={"provider": provider, "model": model, "persisted": False},
            metrics=[
                UsageMetric(name="input_tokens", value=float(input_tokens)),
                UsageMetric(name="output_tokens", value=float(output_tokens)),
                UsageMetric(name="total_tokens", value=float(input_tokens + output_tokens)),
                UsageMetric(name="duration_ms", value=float(duration_ms)),
            ],
        )
        await record_usage_events(owner_id, [event])
    except Exception as exc:
        logger.error(f"[field_generation] failed to record usage for owner_id={owner_id}: {exc}")


async def run_generate_preview(
    *,
    owner_id: str,
    collection_name: Optional[str],
    service_description: Optional[str],
    extraction_instructions: Optional[str],
    doc_previews: List[Tuple[str, str]],
) -> Dict[str, Any]:
    """Step 1 of the create-collection wizard: propose an enrichment field
    schema from free text, before any collection exists. Quota check, one LLM
    call (generate_field_schema, unchanged), usage row, response payload —
    same shape of orchestration as preview_run.run_preview.

    Raises HTTPException(429) via enforce_operation when the owner is over
    budget. A genuine LLM/provider failure propagates from
    generate_field_schema uncaught, same as the knowledge-scoped endpoint —
    the router decides how to surface that.
    """
    estimated_tokens = estimate_generation_prompt_tokens(
        service_description, extraction_instructions, doc_previews
    )
    await _enforce_generation_quota(owner_id, estimated_tokens)

    started = time.perf_counter()
    result = await generate_field_schema(
        owner_id=owner_id,
        knowledge_id=None,
        collection_name=collection_name,
        service_description=service_description,
        extraction_instructions=extraction_instructions,
        doc_previews=doc_previews,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    await _record_generation_usage(
        owner_id=owner_id,
        provider=result.provider,
        model=result.model,
        key_type=result.key_type,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        duration_ms=duration_ms,
    )

    return {
        "message": "Generated an enrichment field schema — nothing saved yet.",
        "proposed_fields": result.accepted_fields,
        "rejected_fields": result.rejected_fields,
        "protected_fields_untouched": sorted(PROTECTED_SYSTEM_FIELDS),
        "documents_used": [name for name, _ in doc_previews],
        "generation": {"provider": result.provider, "model": result.model, "key_type": result.key_type},
        "run": {
            "duration_ms": duration_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "persisted": False,
        },
    }
