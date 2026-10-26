"""The extraction model builder for the preview run — PORTED CODE, read this first.

## What this is

Turning a field's ``field_type`` string ("str", "date", "List[dict]", ...) into a
real Pydantic field is what decides what the extraction LLM is actually asked
for. That logic already exists — but in the **other** service, the one that does
real ingest:

    Source repo : fs-agent-workers
    Source file : celery_utils/enrichment/schema_builder.py
    Functions   : list_to_string_validator        (lines  19- 42)
                  create_nested_model_from_schema (lines 106-159)
                  create_dynamic_pydantic_model   (lines 163-374)
    Read from that repo's working tree on **2026-09-08** and copied
    field-type by field-type, not from any description of it.

## Why a copy exists at all

The preview endpoint's whole promise is *"this is what you'll get."* If the
preview built its extraction schema even slightly differently from the real
ingest, the product would show the user one thing and then produce another —
worse than having no preview. Three options were weighed in
docs/plan/collection-creation-flow/preview-run.md ("The one real risk: a second
extraction implementation"); the user chose **port, pinned to a live diff**
(decisions.md, "CC6 sub-decision", 2026-09-08): copy it, verify it against the
running code, and keep a test that fails the day the two drift apart.

That test is tests/unit/test_enrichment_preview.py's parity block. It imports
the worker's **real** function off disk and asserts the two builders emit an
identical JSON schema for identical input. It is the thing that keeps this file
honest; do not delete it to make a change land.

## Deliberate deviations from the source — every one is a removal

1. **No ``chunking_params``, no ``chunk_type``, no ``'chunks'`` field type.** A
   preview runs one document and returns values; it never produces chunks, and
   the caller cannot ask for a chunk-level field (the request carries document
   fields only). The source's ``'chunks': list[chunk_type]`` entry and the
   ``min_items``/``max_items`` branch that only fires for it are therefore gone.
2. **No ``chunk_text`` special case.** In the source, a field literally named
   ``chunk_text`` gets the target-chunk-length sentence appended. Same reason as
   (1): there are no chunk fields here, and ``chunk_text`` is a protected system
   field a caller cannot send.
3. **``configs`` are ``PreviewFieldSpec`` dataclasses, not ``FieldConfiguration``
   SQLModel rows.** Nothing is stored at preview time — the fields arrive inline
   in the request — so there is no database row to pass. The dataclass exposes
   exactly the attributes the source reads, and nothing else, so the copied body
   below is unchanged.

Anything else that differs is a **bug**, not a design choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import date
from typing import Any, Dict, List, Literal, Optional, Type

import pydantic
from pydantic.functional_validators import BeforeValidator
from typing_extensions import Annotated

from utils.logger import logger


# ---------------------------------------------------------------------------
# The DB-row stand-in
# ---------------------------------------------------------------------------

@dataclass
class PreviewFieldSpec:
    """One requested field, shaped like the ``FieldConfiguration`` row the ported
    builder was written against.

    Attribute names and defaults mirror fs-agent-workers'
    ``services/pydantic_models_db.py::FieldConfiguration`` for exactly the columns
    ``create_dynamic_pydantic_model`` reads. A dataclass rather than a Pydantic
    model on purpose: the builder reads ``config.schema``, and ``schema`` is a
    reserved (deprecated) method name on a Pydantic ``BaseModel``.
    """

    field_name: str
    field_type: str
    description: Optional[str] = None
    sample_values: Optional[List[str]] = None
    schema: Optional[Dict[str, str]] = None
    is_active: bool = True
    is_required: bool = False
    # Every field a preview caller sends is a user-defined ("custom") field —
    # there is no collection yet, so none of them can be a seeded system field.
    is_custom: bool = True
    restrict_to_choices: bool = False
    max_items: Optional[int] = None
    # Not read by the builder; carried through so the response can echo the label
    # the caller saw in the wizard's step-2 table.
    field_type_label: Optional[str] = dataclass_field(default=None)


# ---------------------------------------------------------------------------
# Ported verbatim: list_to_string_validator (source lines 19-42)
# ---------------------------------------------------------------------------

def list_to_string_validator(value: Any) -> str:
    """Convert a list the LLM returned into the single string a ``str`` field wants.

    Verbatim from the source. Models routinely answer a "one value" question with
    a list; coercing here is what stops that becoming a validation error the user
    sees as a failed extraction.
    """
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return ""
        return ", ".join(str(item) for item in value)
    return str(value) if value is not None else None


# ---------------------------------------------------------------------------
# Ported: create_nested_model_from_schema (source lines 106-159)
# ---------------------------------------------------------------------------

def create_nested_model_from_schema(schema: Dict[str, str], model_name: str) -> Type[pydantic.BaseModel]:
    """Build the row model for a ``dict`` / ``List[dict]`` field from its flat schema.

    Supports str, int, float, bool, List[str], date. Deliberately NOT nested
    dicts — one level deep only, same as the source.
    """
    logger.debug(f"[preview_model_builder] Creating nested model {model_name} from schema: {schema}")
    fields_definitions = {}
    basic_type_map = {
        'str': str,
        'int': int,
        'float': float,
        'bool': bool,
        'List[str]': list[str],
        # A real date, not a string: pydantic puts `format: date` in the JSON
        # schema handed to the extraction model, which is what forces ONE format
        # out of it. A bank statement's transactions table printing "16 Jun 19"
        # per row is the case this fixes.
        'date': date,
    }

    for field_name, field_type_str in schema.items():
        field_type: Any = None

        if field_type_str == "List[str]":
            field_type = basic_type_map[field_type_str]
            fields_definitions[field_name] = (Optional[field_type], pydantic.Field(default=None, description="A list of strings like '[value1, value2, value3]'"))

        elif field_type_str == "str":
            # BeforeValidator coerces LLM list responses into a single string
            field_type = Annotated[str, BeforeValidator(list_to_string_validator)]
            fields_definitions[field_name] = (Optional[field_type], pydantic.Field(default=None, description="A single string like 'value1'"))

        elif field_type_str in basic_type_map:
            field_type = basic_type_map[field_type_str]
            fields_definitions[field_name] = (Optional[field_type], pydantic.Field(default=None))

        else:
            logger.info(f"[preview_model_builder] Unsupported nested field type '{field_type_str}' for '{field_name}'. Skipping.")
            continue

    NestedModel = pydantic.create_model(model_name, **fields_definitions)
    return NestedModel


# ---------------------------------------------------------------------------
# Ported: create_dynamic_pydantic_model (source lines 163-374), minus chunks
# ---------------------------------------------------------------------------

def create_dynamic_pydantic_model(
    model_name: str,
    configs: List[PreviewFieldSpec],
) -> Type[pydantic.BaseModel]:
    """Build the extraction output model for one preview run.

    Same body as the worker's ``create_dynamic_pydantic_model`` with the three
    chunk-related removals listed in this module's docstring. ``chunking_params``
    and ``chunk_type`` are gone from the signature because neither has any input
    left to read.
    """
    try:
        fields_definitions = {}
        type_map = {
            'str': str,
            'int': int,
            'float': float,
            'bool': bool,
            'List[str]': list[str],
            'date': date,  # see basic_type_map above
            'dict': dict[str, Any],  # Fallback if no schema provided
            'List[dict]': list[dict[str, Any]],  # Fallback if no schema provided
        }
        for config in configs:
            # We only add active fields to the model
            if not config.is_active:
                continue

            field_type_str = config.field_type
            field_name = config.field_name
            description = config.description or ""  # guard all downstream += concatenations
            example_values = list(config.sample_values) if config.sample_values else None  # copy — never mutate the caller's list
            logger.debug(f"[preview_model_builder] Processing field: {field_name} with type: {field_type_str}")

            # Handle dict type
            if config.field_type in ['dict', 'List[dict]']:
                if config.schema:
                    # Create nested model from schema
                    nested_model_name = f"{field_name}_Nested"
                    nested_model = create_nested_model_from_schema(config.schema, nested_model_name)
                    if config.field_type == 'List[dict]':
                        field_type = list[nested_model]
                    else:
                        field_type = nested_model
                else:
                    # Use generic Dict[str, Any] if no schema provided
                    logger.debug(f"[preview_model_builder] Using generic Dict[str, Any] for field {field_name}")
                    field_type = type_map[field_type_str]

            elif config.field_type == 'List[str]':
                item_type: Any = str

                # For List[str] fields, describe the format and give sample values
                description += "| Format: List of strings like '[value1, value2, value3]'"
                if not example_values:
                    example_values = ["value1", "value2", "value3"]

                # Check if the list items themselves should be restricted
                if config.restrict_to_choices and config.sample_values:
                    item_type = Literal[tuple(config.sample_values)]

                # If max_items is specified, create a constrained list type that truncates
                if config.max_items is not None:
                    def truncator_factory(max_len: int):
                        def truncate(v) -> list:
                            if isinstance(v, list):
                                return v[:max_len]
                            return v
                        return truncate

                    field_type = Annotated[list[item_type], BeforeValidator(truncator_factory(config.max_items))]
                else:
                    # Otherwise, it's a regular list of the item type
                    field_type = list[item_type]

            # Apply validator to string fields to handle lists returned instead of strings
            elif config.field_type == 'str':
                if config.max_items:
                    description += f"| Max items: {config.max_items}"

                # If restrict_to_choices is set, apply it after the validator
                if config.restrict_to_choices and config.sample_values:
                    literal_values = tuple(config.sample_values)
                    if literal_values:
                        # The validator converts lists to strings, then Literal validates the string value
                        field_type = Annotated[Literal[literal_values], BeforeValidator(list_to_string_validator)]
                    else:
                        field_type = Annotated[str, BeforeValidator(list_to_string_validator)]
                else:
                    field_type = Annotated[str, BeforeValidator(list_to_string_validator)]

            elif config.field_type == 'date':
                # Tell the model what to do with what documents actually print —
                # "16 Jun 19", "May 2024", "05/24" — rather than leaving it to
                # guess. pydantic enforces the shape; this decides the two cases
                # pydantic cannot: an absent date and a partial one.
                description += (
                    ". Return a single calendar date in ISO-8601 format (YYYY-MM-DD). "
                    "Convert whatever format the document uses. If the document gives "
                    "only a month and year, use the first day of that month. If the "
                    "document does not state this date, return null — never infer or "
                    "calculate it from other dates."
                )
                if not example_values:
                    example_values = ["2024-05-01"]
                # restrict_to_choices is deliberately NOT applied: sample_values are
                # strings, and a Literal of strings against a `date` type rejects
                # every value the model returns.
                if config.restrict_to_choices:
                    logger.info(
                        f"[preview_model_builder] Ignoring restrict_to_choices on date field '{field_name}' — "
                        f"not applicable to dates; use a range filter at query time."
                    )
                field_type = date

            # Determine the Python type for other field types
            else:
                field_type: Any = type_map.get(field_type_str)
                if field_type is None:
                    logger.info(f"[preview_model_builder] Unsupported field type '{field_type_str}' for field '{field_name}'. Skipping.")
                    continue

                # Handle constraints for other non-list types (bool, int, float)
                if config.restrict_to_choices and config.sample_values:
                    if config.field_type == 'bool':
                        literal_values = tuple(s.lower() == 'true' for s in config.sample_values)
                    else:
                        literal_values = tuple(config.sample_values)
                    if literal_values:
                        field_type = Literal[literal_values]

            # Make fields optional or required based on is_required
            field_dict = {}
            field_dict["description"] = description
            if config.is_required:
                if config.is_custom:
                    # Nullable defaults — missing LLM output stays detectably None,
                    # not a fake placeholder. The prompt still marks fields as required;
                    # the schema accepts None so a "not_found" is distinguishable.
                    field_dict["default"] = None
                    field_type = Optional[field_type]

                    if example_values:
                        field_dict["examples"] = example_values

                    pydantic_field = pydantic.Field(**field_dict)
                else:
                    # System fields are required by default. (The source's
                    # chunks-specific min_items/max_items branch is dropped here —
                    # see deviation 1 in the module docstring.)
                    pydantic_field = pydantic.Field(**field_dict)

                logger.debug(f"[preview_model_builder] Required field type: {field_type}")
            else:
                # Optional fields with appropriate defaults
                if config.field_type == 'List[str]':
                    field_dict["default"] = []
                elif config.field_type == 'List[dict]':
                    field_dict["default"] = []
                elif config.field_type == 'dict':
                    field_dict["default"] = None  # Use None for optional dict
                elif config.field_type == 'bool':
                    field_dict["default"] = None  # None, not False — False is a meaningful LLM output
                elif config.field_type in ['int', 'float']:
                    field_dict["default"] = None
                else:  # str and other types
                    field_dict["default"] = None
                # Make the field type optional
                field_type = Optional[field_type]
                pydantic_field = pydantic.Field(**field_dict)
                logger.debug(f"[preview_model_builder] Optional field type: {field_type}")

            fields_definitions[field_name] = (field_type, pydantic_field)

        DynamicModel = pydantic.create_model(model_name, **fields_definitions)
        return DynamicModel

    except Exception as e:
        logger.error(f"[preview_model_builder] Error creating dynamic Pydantic model: {str(e)}")
        raise


def specs_from_request_fields(fields: List[Dict[str, Any]]) -> List[PreviewFieldSpec]:
    """Map the request's ``fields`` JSON onto the DB-row stand-in.

    Accepts both ``schema`` and ``field_schema`` for the nested-row shape, since
    the write path (utils/config/field_generation.py) names it ``field_schema``
    on the wire and ``schema`` in storage.
    """
    specs: List[PreviewFieldSpec] = []
    for f in fields:
        specs.append(PreviewFieldSpec(
            field_name=f["field_name"],
            field_type=f["field_type"],
            description=f.get("description"),
            sample_values=f.get("sample_values"),
            schema=f.get("schema") or f.get("field_schema"),
            is_active=bool(f.get("is_active", True)),
            is_required=bool(f.get("is_required", False)),
            is_custom=bool(f.get("is_custom", True)),
            restrict_to_choices=bool(f.get("restrict_to_choices", False)),
            max_items=f.get("max_items"),
            field_type_label=f.get("field_type_label"),
        ))
    return specs
