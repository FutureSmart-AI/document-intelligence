"""Resolve a user-described enrichment field to a code-level ``field_type``.

WHY THIS EXISTS
---------------
Until now the caller had to send ``field_type`` themselves, chosen from the
seven Python type annotations in ``FieldType`` — ``str``, ``List[str]``,
``int``, ``float``, ``bool``, ``dict``, ``List[dict]``. Those are the keys of
the enrichment worker's type map (fs-agent-workers
``celery_utils/enrichment/schema_builder.py``), i.e. an implementation detail,
and they were being shown to an HR or procurement user trying to pull a salary
figure out of a contract. The market convention research
(docs/plan/field-and-retrieval-precision/terminology.md) is unambiguous that
nobody outside a developer tool asks a business user to distinguish
``List[dict]`` from ``dict``.

The fix is not to rename the types — that was costed at 40+ call sites across
two repos for the same visible result, and rejected. It is to stop asking:
the user describes the field in their own words, and this module resolves the
code-level type from that description. ``field_type`` becomes optional on the
write path.

WHAT THIS IS NOT
----------------
It is not a new type vocabulary. The resolved value is always one of the
existing ``FieldType`` members (plus ``date``), so nothing downstream — the
worker's type maps, Tier 1 ``type_conformance``, the tabular data view's four
type-literal blocks, the judge prompts — changes at all.

THE FAILURE MODE THIS GUARDS
----------------------------
An LLM-picked type that is *valid but wrong* cannot be caught by the write
path's allow-list check: ``List[str]`` where the user meant ``List[dict]`` is
a well-formed answer that silently extracts a flattened list instead of table
rows. So two rules apply throughout:

1. **Deterministic first.** An explicitly sent ``field_type`` always wins and
   is never sent to the LLM. Resolution only fills a gap.
2. **Never silent.** Every resolution is returned to the caller with the
   reason for it, and anything the guards had to correct is reported as a
   warning rather than quietly applied. The repo rule that a recommender must
   never *act* invisibly (docs/plan/field-and-retrieval-precision/
   field-recommendation.md, "What this must not become") applies here too.

MONEY
-----
There is deliberately no ``money`` type. An amount is a plain ``float``,
because amounts overwhelmingly appear as columns inside a table — a bank
statement's ``transactions[].amount`` — and a ``{amount, currency}`` object
per row would be both absurd and unfilterable (facets and range indexes only
work on flat values). The currency is captured ONCE, as a document-level
``currency`` field, which is exactly how documents themselves state it: a
statement prints "Currency: INR" in its header and bare numbers in its rows.

So when this module resolves any monetary field, it guarantees a document-level
``currency`` field exists and points every amount's description at it. That
pairing is what keeps arithmetic honest: an amount is a number (so it sums and
range-filters), and the currency it is denominated in is never lost.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from utils.logger import logger
from utils.models.enums import CALLER_FIELD_TYPES, label_for_field_type

# Subfield types the nested-model builder supports (mirrors basic_type_map in
# fs-agent-workers celery_utils/enrichment/schema_builder.py and the
# valid_schema_value_types list in utils/db/enrichment_db_utils.py).
_ALLOWED_SCHEMA_VALUE_TYPES = frozenset({"str", "int", "float", "bool", "List[str]", "date"})

# The type used when resolution produces something unusable. `str` is the safe
# floor because it is lossless — it stores whatever the model returns, which is
# what a caller sending no type gets today anyway. It is NOT a silent fallback:
# every use is reported as a warning on the resolution.
_FALLBACK_TYPE = "str"

# The document-level field that makes every monetary amount interpretable.
CURRENCY_FIELD_NAME = "currency"

CURRENCY_FIELD = {
    "field_name": CURRENCY_FIELD_NAME,
    "field_type": "str",
    "field_type_label": "Text",
    "description": (
        "The currency all monetary amounts in this document are denominated in, "
        "as a 3-letter ISO-4217 code (for example INR, USD, EUR). Read it from "
        "the document — a header line such as 'Currency: INR', an explicit "
        "symbol, or the account details. Do not guess from the language or "
        "country of the document; if it is genuinely not stated, return null."
    ),
    "level": "document",
    "is_custom": True,
    "is_required": False,
    "restrict_to_choices": False,
}

# Appended to a monetary field's description. States the two things that break
# arithmetic if left unsaid: symbols/separators inside the number, and where
# the currency lives.
MONETARY_DESCRIPTION_SUFFIX = (
    " Return the amount as a plain number — digits and an optional decimal point "
    "only, no currency symbol, no thousands separators, no trailing text. Use a "
    "negative number for debits, withdrawals and amounts the document shows in "
    f"parentheses or with a minus sign. Do not include the currency here; it is "
    f"captured once for the whole document in the '{CURRENCY_FIELD_NAME}' field."
)

_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class FieldTypeResolution(BaseModel):
    """One field's resolved type, as the LLM returns it."""
    field_name: str = Field(..., description="The field name being resolved, copied exactly from the input")
    field_type: str = Field(..., description="One of: str, List[str], int, float, bool, dict, List[dict], date")
    field_schema: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "REQUIRED when field_type is dict or List[dict]: the sub-field names "
            "mapped to their own types (str, int, float, bool, List[str], date). "
            "Omit for every other type."
        ),
    )
    is_monetary: bool = Field(
        False,
        description=(
            "True if this field (or any numeric sub-field of it) holds a sum of "
            "money — an amount, balance, price, salary, total, fee or tax."
        ),
    )
    reason: str = Field(..., description="One short sentence: why this type, in plain language")


class FieldTypeResolutionResponse(BaseModel):
    resolutions: List[FieldTypeResolution]


# The type-choice rules, shared verbatim with utils/config/field_generation.py.
#
# There are two flows that decide a field's type from prose — this resolver (a
# user describing one field) and schema generation at collection creation (an
# LLM proposing a whole schema). If each carried its own description of what
# `List[dict]` means, they would drift, and the same field described the same way
# would get different types depending on which door it came through. One block,
# imported by both.
TYPE_RULES_BLOCK = """Choose exactly one type per field:

- `str` — one piece of text per document. Names, IDs, statuses, a summary, an \
address written as one line. Also the right answer for a number that is really \
a label and is never used in arithmetic (an account number, an invoice number, \
a phone number, a PIN code) — those keep leading zeros and must not become \
numbers.
- `List[str]` — several independent text values, with no further detail \
attached to each one. Skills, tags, topics, keywords, party names, a list of \
languages.
- `int` — a whole-number count or quantity that is used arithmetically. Number \
of employees, page count, quantity ordered.
- `float` — any number that can have a fractional part, INCLUDING every \
monetary amount: balances, totals, prices, salaries, fees, taxes. Money is \
always `float`, never `str`.
- `bool` — a true/false or yes/no property. Has overdraft, is signed, \
auto-renews.
- `date` — a single calendar date. Effective date, date of birth, statement \
date, due date, expiry. Always prefer `date` over `str` for a date: it is \
stored in one canonical format so date ranges can actually be searched. Do NOT \
use `date` for a period or duration that is not one specific day ("Q3 2024", \
"30 days", "2019-07" as a statement *month*) — those are `str`.
- `dict` — ONE group of related values that belong together, appearing once per \
document. An address broken into street/city/state/postcode; a single \
account-holder block.
- `List[dict]` — a TABLE: the same set of columns repeating over many rows. \
Transactions, line items, employment history, education history. This is the \
type to choose whenever the user describes rows, a table, a statement of \
entries, or "a list of X with their Y and Z".

Rules that decide the hard cases:

1. `List[str]` vs `List[dict]`: if each entry carries more than one piece of \
information, it is `List[dict]`. "Languages" is `List[str]`; "languages with \
proficiency level" is `List[dict]`.
2. When you choose `dict` or `List[dict]` you MUST return a field_schema \
naming the sub-fields and their types. A table with no declared sub-fields \
cannot be extracted — the model has nothing to align its output to. Read the \
sub-fields out of the user's own description, and give each one the type it \
would get by these same rules (a per-row date is `date`, a per-row amount is \
`float`).
3. Mark is_monetary true for any field that holds money, including a table \
whose schema has a monetary sub-field. Never put the currency in the same \
field as the amount, and never propose a currency sub-field inside a table — \
currency is one document-level field, handled separately.
4. If the description is too vague to place, choose `str`. It is lossless, and \
a wrong specific type silently corrupts every document."""


_SYSTEM_PROMPT = """You map a described document field onto the data type a \
document-extraction system should store it as. You are not designing a schema; \
the fields already exist. You decide, for each one, the single type that makes \
its values usable.

""" + TYPE_RULES_BLOCK + """

Copy each field_name back exactly as given. Return one resolution per input \
field, and nothing else."""

_HUMAN_TEMPLATE = """Collection: {collection_name}
What these documents are: {service_description}

Resolve the type of each field below.

{fields_block}"""


def _build_fields_block(fields: List[Dict[str, Any]]) -> str:
    lines = []
    for f in fields:
        name = f.get("field_name")
        desc = (f.get("description") or "").strip() or "(no description given)"
        line = f"- {name}: {desc}"
        # A label the user typed is the strongest signal there is about what they
        # want — stronger than the description, which may not have been touched.
        # Someone renaming a field's type to "Table" is telling us the answer.
        label = (f.get("field_type_label") or "").strip()
        if label:
            line += f"\n  the user labelled this field's type: \"{label}\""
        samples = f.get("sample_values")
        if samples:
            line += f"\n  example values seen: {samples[:5]}"
        level = f.get("level") or "document"
        if level == "chunk":
            line += "\n  (extracted per chunk of the document, not once per document)"
        lines.append(line)
    return "\n".join(lines)


def needs_type_resolution(field: Dict[str, Any]) -> bool:
    """Does this field need its type resolved?

    Only when no usable ``field_type`` was sent. An explicit type is the
    caller's decision and is never second-guessed — that is what keeps this
    additive and keeps every existing integration byte-for-byte unaffected.
    """
    ft = field.get("field_type")
    return not (isinstance(ft, str) and ft.strip())


async def resolve_field_types(
    fields: List[Dict[str, Any]],
    *,
    existing_field_names: Optional[List[str]] = None,
    collection_name: Optional[str] = None,
    service_description: Optional[str] = None,
    owner_id: Optional[str] = None,
    knowledge_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fill in ``field_type`` for every field that was sent without one.

    Args:
        fields: field dicts as the write path builds them. Mutated copies are
            returned; the input list is not modified.
        existing_field_names: field names already configured on this knowledge
            base — used to decide whether a ``currency`` field must be added.
        collection_name / service_description: context from knowledge_config,
            so "amount" in a bank statement resolves differently from "amount"
            in a recipe.

    Returns:
        (resolved_fields, resolutions, extra_fields)

        ``resolutions`` is the audit trail — one entry per resolved field, with
        the type, the reason, and any guard warnings — and belongs in the API
        response. ``extra_fields`` holds fields this module added on its own
        (today: the document-level ``currency`` field), kept separate so the
        caller can report them as additions rather than as the user's own.

    Raises on a genuine LLM/provider failure. It deliberately does not fall
    back to "everything is str": that would look identical to a successful
    resolution and would be exactly the silent coercion this replaces.
    """
    out = [dict(f) for f in fields]
    pending = [(i, f) for i, f in enumerate(out) if needs_type_resolution(f)]
    if not pending:
        return out, [], []

    logger.info(
        f"Resolving field_type for {len(pending)} field(s) without an explicit type: "
        f"{[f.get('field_name') for _, f in pending]}"
    )

    # Imported lazily, mirroring utils/config/field_generation.py — this module
    # is reached from the enrichment write path, not from chat.
    from chat.byok_fallback import run_with_key_fallback
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("human", _HUMAN_TEMPLATE),
    ])

    async def _invoke(llm):
        structured_llm = llm.with_structured_output(FieldTypeResolutionResponse)
        return await (prompt | structured_llm).ainvoke({
            "collection_name": collection_name or "(untitled)",
            "service_description": service_description or "(not provided)",
            "fields_block": _build_fields_block([f for _, f in pending]),
        })

    outcome = await run_with_key_fallback(
        _invoke,
        owner_id=owner_id,
        knowledge_id=knowledge_id,
        source="field_type_resolution",
        temperature=0.0,
        for_function_calling=True,
    )
    response: FieldTypeResolutionResponse = outcome.value

    by_name = {r.field_name: r for r in response.resolutions}
    resolutions: List[Dict[str, Any]] = []
    any_monetary = False

    for idx, field in pending:
        name = field.get("field_name")
        resolution = by_name.get(name)
        warnings: List[str] = []

        if resolution is None:
            # The model skipped a field. Fall back, loudly — a missing type
            # would fail write-path validation with a message about a type the
            # caller never sent, which is a confusing way to learn about this.
            warnings.append(
                "the resolver returned no type for this field; defaulted to "
                f"'{_FALLBACK_TYPE}'"
            )
            field_type, field_schema, is_monetary, reason = (
                _FALLBACK_TYPE, None, False, "no resolution returned"
            )
        else:
            field_type, field_schema, warnings_from_guards = _guard(resolution)
            warnings.extend(warnings_from_guards)
            is_monetary = resolution.is_monetary
            reason = resolution.reason

        field["field_type"] = field_type
        # Persist the label alongside the type, so no read has to map anything.
        # A label the user sent is kept EXACTLY as they wrote it — replacing it
        # with the canonical one would silently undo their edit, which is the
        # whole reason this is stored rather than derived.
        if not (field.get("field_type_label") or "").strip():
            field["field_type_label"] = label_for_field_type(field_type)
        if field_schema:
            field["schema"] = field_schema
        elif field_type in ("dict", "List[dict]") and not field.get("schema"):
            # Defect 1 in field-recommendation.md: a dict/List[dict] with no
            # declared schema cannot succeed — the model has no shape to emit,
            # so it invents one and 0 rows align. The field is still created
            # (refusing it would lose the user's intent), but this must be
            # visible in the response, not merely skipped later by the
            # validator's "no declared schema".
            warnings.append(
                f"resolved to '{field_type}' but no sub-fields were declared — "
                "extraction cannot align its output to a shape it was never "
                "given. Add a schema naming the sub-fields."
            )

        if is_monetary:
            any_monetary = True
            description = (field.get("description") or "").rstrip()
            if MONETARY_DESCRIPTION_SUFFIX.strip() not in description:
                field["description"] = description + MONETARY_DESCRIPTION_SUFFIX

        out[idx] = field
        resolutions.append({
            "field_name": name,
            "field_type": field_type,
            "field_type_label": field["field_type_label"],
            "resolved_by": "llm" if resolution is not None else "fallback",
            "reason": reason,
            "is_monetary": is_monetary,
            "schema": field_schema,
            "warnings": warnings,
        })
        logger.info(
            f"Resolved field_type | name={name} type={field_type} "
            f"monetary={is_monetary} warnings={len(warnings)} reason={reason!r}"
        )

    extra_fields = ensure_currency_field(
        any_monetary=any_monetary,
        submitted=out,
        existing_field_names=existing_field_names or [],
    )

    return out, resolutions, extra_fields


def _guard(resolution: FieldTypeResolution) -> Tuple[str, Optional[Dict[str, str]], List[str]]:
    """Re-check the LLM's answer against the same allow-lists the write path
    enforces, plus the ones only a free-generating model needs.

    Deliberately does not trust the model's type discipline: a hallucinated
    type is corrected here rather than reaching the DB, and every correction is
    reported. Returns (field_type, field_schema, warnings).
    """
    warnings: List[str] = []
    field_type = (resolution.field_type or "").strip()

    if field_type not in CALLER_FIELD_TYPES:
        # Covers a hallucinated type ("Text", "Money", "datetime") and the
        # internal 'chunks' value, which a caller-facing path must never be
        # able to produce — it selects the chunk model. Also defensively
        # rejects 'text', a retired internal value nothing writes anymore, in
        # case a stale stored row's value were ever routed back through here.
        warnings.append(
            f"resolver returned unsupported field_type '{field_type}'; "
            f"defaulted to '{_FALLBACK_TYPE}'"
        )
        return _FALLBACK_TYPE, None, warnings

    field_schema = resolution.field_schema or None

    if field_schema and field_type not in ("dict", "List[dict]"):
        warnings.append(
            f"resolver returned a schema for a '{field_type}' field, which does "
            "not take one; the schema was dropped"
        )
        field_schema = None

    if field_schema:
        cleaned: Dict[str, str] = {}
        for key, value in field_schema.items():
            if not isinstance(key, str) or not _NAME_RE.match(key):
                warnings.append(f"dropped sub-field '{key}': name must be lowercase alphanumeric/underscore")
                continue
            if value not in _ALLOWED_SCHEMA_VALUE_TYPES:
                warnings.append(
                    f"sub-field '{key}' had unsupported type '{value}'; "
                    f"defaulted to '{_FALLBACK_TYPE}'"
                )
                cleaned[key] = _FALLBACK_TYPE
                continue
            cleaned[key] = value
        field_schema = cleaned or None
        if field_schema is None:
            warnings.append("no usable sub-fields survived validation")

    return field_type, field_schema, warnings


def ensure_currency_field(
    *,
    any_monetary: bool,
    submitted: List[Dict[str, Any]],
    existing_field_names: List[str],
) -> List[Dict[str, Any]]:
    """Add the document-level ``currency`` field when money is being extracted
    and nothing is capturing the currency yet.

    This is the half of "money" that a type could not deliver. An amount stored
    without its currency is not a smaller version of the truth, it is a number
    that cannot be compared, summed or reported — and the failure is invisible,
    because 1200 looks equally valid whether it meant INR or USD.

    Deliberately additive and idempotent: if the caller already has a currency
    field, by any of the obvious names, nothing is added.
    """
    if not any_monetary:
        return []

    have = {str(n).lower() for n in existing_field_names}
    have |= {str(f.get("field_name") or "").lower() for f in submitted}

    # Accept what a user would plausibly have called it already, so this never
    # creates a near-duplicate of a field that is doing the job.
    if have & {CURRENCY_FIELD_NAME, "currency_code", "iso_currency", "currency_type"}:
        logger.info("Monetary field(s) present; a currency field already exists — nothing added.")
        return []

    logger.info(
        "Monetary field(s) resolved and no currency field exists — adding the "
        "document-level 'currency' field so amounts stay interpretable."
    )
    return [dict(CURRENCY_FIELD)]
