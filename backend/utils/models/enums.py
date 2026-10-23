"""Shared enumerations used across knowledge, enrichment, and retrieval models."""

from enum import Enum
from typing import Dict, List, Optional


class ScrapeUsing(str, Enum):
    option1 = "Newspaper"
    option2 = "FireCrawl"


class ScrapingMethod(str, Enum):
    option1 = "scrape"
    option2 = "crawl"


class TranscribeUsing(str, Enum):
    option1 = "openai"
    option2 = "groq"


class EmbeddingModel(str, Enum):
    option1 = "paraphrase-multilingual-mpnet-base-v2"
    option2 = "openai-text-embedding-3-large"
    option3 = "openai-text-embedding-3-small"


class DocumentProcessingMode(str, Enum):
    """What a document's content is like — the caller's answer, not a parser name.

    The point of this enum is that it asks a question the person uploading the file can
    actually answer. "Is this a scan?" is answerable by anyone; "should this use OCR with
    high-resolution preprocessing?" is not. Choosing the machinery is our job, and it
    happens in the worker (fs-agent-workers `celery_utils/processing/processing_modes.py`),
    which is the only place that knows which parser serves which kind of content.

    Sent to the worker as a plain string over SQS, so these *values* are the cross-service
    contract — renaming one is a breaking change even though it looks local.
    """

    AUTO = "auto"                  # caller sent no mode at all — let the worker detect it
    TEXT_BASED = "text_based"      # the text is already in the file; just read it
    SCANNED = "scanned"            # pictures of text — needs OCR
    LAYOUT_AWARE = "layout_aware"  # tables/columns: position carries meaning
    VISUAL = "visual"              # charts/diagrams: the image carries meaning
    MIXED = "mixed"                # more than one of the above in one collection


class FieldTemplate(str, Enum):
    """Field template options for knowledge configuration.

    Each non-default value keys into DOMAIN_FIELD_TEMPLATES
    (utils/config/enrichment_config.py): the template's fields are seeded
    on top of DEFAULT_FIELD_CONFIGURATIONS, never instead of them.
    """
    DEFAULT = "default"
    RESUME = "resume"
    INVOICE_CONTRACT = "invoice_contract"  # kept for backward compatibility — existing
    # callers may still send this. New callers should send INVOICE or CONTRACT
    # instead; see docs/plan/collection-creation-flow/gap-analysis.md (CC2, Q4).
    INVOICE = "invoice"
    CONTRACT = "contract"
    SUPPORT_TICKET = "support_ticket"
    SOFTWARE_DOCS = "software_docs"


class DeletionStatus(str, Enum):
    DELETING = "deleting"
    COMPLETED = "completed"
    ERROR = "error"


class FieldType(str, Enum):
    """Field types for enrichment configuration.

    These are the *code-level* types the enrichment worker's type maps are
    keyed by (fs-agent-workers celery_utils/enrichment/schema_builder.py) —
    not display labels. The user does not pick from this list: they describe
    the field in prose and utils/config/field_type_resolver.py resolves it to
    one of these. See docs/plan/field-and-retrieval-precision/terminology.md.
    """
    STR = "str"
    LIST_STR = "List[str]"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    DICT = "dict"
    LIST_DICT = "List[dict]"
    # A calendar date, stored canonically as an ISO-8601 "YYYY-MM-DD" string
    # and indexed in Qdrant as DATETIME so range filters work. Extracted as a
    # real datetime.date by the worker, which is what forces one format out of
    # the model — see docs/plan/field-and-retrieval-precision/terminology.md
    # rows 8-9 and the "unspecified format" defect in field-recommendation.md.
    DATE = "date"

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return self.value

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.str_schema()


# The types a caller may send. Single source of truth — previously duplicated as
# inline literals in utils/models/enrichment.py's validator, its model_dump
# enum_mapping, utils/db/enrichment_db_utils.py and utils/config/field_generation.py.
CALLER_FIELD_TYPES = frozenset(ft.value for ft in FieldType)

# One internal type that is NOT in FieldType, is not documented, and must
# never be rejected: the enrichment worker writes it directly.
#   "chunks" — selects the dynamic chunk model
#             (fs-agent-workers celery_utils/enrichment/enrichment_strategies.py:94,
#              guarded at celery_utils/shared/enrichment_health.py:115)
# Rejecting it is the single most likely way a "simple validation fix" takes
# down enrichment — see docs/plan/field-and-retrieval-precision/type-migration.md.
INTERNAL_FIELD_TYPES = frozenset({"chunks"})

ALL_FIELD_TYPES = CALLER_FIELD_TYPES | INTERNAL_FIELD_TYPES


# What a person sees where the stored value is a Python type annotation.
#
# `FieldType`'s values are the keys of the enrichment worker's type map — an
# implementation detail. Nobody outside a developer tool asks an HR or
# procurement user to tell `List[dict]` from `dict`, so every surface that
# shows a field's type shows one of these instead: the field modal, the field
# list, the tabular data view header, and any validation error text.
#
# This is the DEFAULT only. The label a user actually sees is persisted per
# field on `field_configurations.field_type_label` (migration 052) and wins
# over this map — see FieldConfigurationResponseV2. Editing this map therefore
# changes what NEW fields are labelled, and leaves existing fields showing the
# label they were created with, which is the intended behaviour: a label a user
# has already seen (or edited) should not silently change under them.
FIELD_TYPE_LABELS = {
    "str": "Text",
    "List[str]": "Text (multiple values)",
    "int": "Number",
    "float": "Number",       # int/float deliberately share one label — see below
    "bool": "Yes / No",
    "date": "Date",
    "dict": "Group of fields",
    "List[dict]": "Table",
    # Internal, never shown in a picker, but a stored row can carry it and a
    # response must still render something rather than blank.
    "chunks": "Chunks",
}

# The map is deliberately NOT one-to-one: `int` and `float` both read "Number",
# because no business-tier product splits them and the distinction is ours, not
# the user's (Tier 1 type_conformance enforces it, and the worker hands the
# extraction model a real int or float). A consequence worth knowing before
# relying on this: **you cannot invert this map.** "Number" does not identify a
# stored type, so a label never round-trips back to a type by lookup — that is
# what the LLM resolver is for.

DEFAULT_FIELD_TYPE_LABEL = "Text"

# The two field types that take a caller-defined `field_schema` (a flat
# {subfield_name: subfield_type} map describing the group's/table's columns).
# This is not an arbitrary label — it names exactly the types the write path
# already special-cases as schema-bearing:
#   - utils/config/field_type_resolver.py:354 treats a resolved
#     `field_type in ("dict", "List[dict]")` with no `schema` attached as a
#     problem worth a warning, because extraction has no shape to align its
#     output to without one — the other six types never trigger this check.
#   - utils/config/field_generation.py:412 rejects a `field_schema` on any
#     `field.field_type not in ("dict", "List[dict]")` — i.e. a schema is only
#     ever meaningful for these two; sending one for any other type is an
#     error, not a no-op.
# Every other FieldType (str, List[str], int, float, bool, date) is a scalar
# with no sub-shape, so it never takes a schema. Kept as a small local
# constant rather than re-deriving it from FieldType members, since "does this
# type carry a nested shape" isn't something the enum itself encodes.
SCHEMA_BEARING_FIELD_TYPES = frozenset({"dict", "List[dict]"})


def label_for_field_type(field_type: Optional[str]) -> str:
    """The default display label for a stored field_type.

    Falls back to "Text" for an unrecognised or missing value rather than
    raising or returning the raw annotation: this runs on the response path,
    where a row that is already in the database cannot be un-stored, and
    showing a user `List[Dict]` in a type column helps nobody.
    """
    if not field_type:
        return DEFAULT_FIELD_TYPE_LABEL
    return FIELD_TYPE_LABELS.get(field_type, DEFAULT_FIELD_TYPE_LABEL)


def get_field_type_catalog() -> List[Dict[str, object]]:
    """The type picker's option list — the wizard's Step 2 type dropdown, per the
    mock. Every caller-choosable `FieldType`, in enum declaration order, each with
    its default display label.

    This does NOT reintroduce the type picker the resolver replaced
    (docs/plan/field-and-retrieval-precision/field-type-resolution.md — "there is
    no picker left to label"). That decision is about what happens when a caller
    sends NO field_type: an LLM resolves one from the description, and nothing
    here changes that. This catalog exists for the caller who WANTS to pick one
    explicitly — "deterministic first" already means an explicit field_type is
    never sent to the LLM, so a UI that lets a user choose from this list and
    sends that choice back is exercising a path that already existed, just
    without anywhere to read the options from before now. Leaving the field on
    "auto"/unset still resolves as before.

    `int` and `float` share the label "Number" (deliberately — see
    FIELD_TYPE_LABELS above), so this list contains two entries with the same
    label and different `field_type` values. Merging them into one dropdown row
    (and picking which stored type a "Number" selection sends) is a UI decision,
    not something this catalog resolves — same as `GET /v1/field-templates`
    leaves rendering choices to the caller.

    "chunks" (INTERNAL_FIELD_TYPES) is excluded — never offered as a choice.

    Each entry also carries `requires_schema`: true for `dict`/`List[dict]`
    (see SCHEMA_BEARING_FIELD_TYPES above), false for the other six. Without
    this, the only way for a caller to know which two types need a
    `field_schema` would be to hardcode that pair on the frontend — exactly
    the second-source-of-truth pattern
    docs/plan/field-and-retrieval-precision/field-type-resolution.md flags as
    a real bug when the backend adds a type and the frontend copy goes stale.
    """
    return [
        {
            "field_type": ft.value,
            "field_type_label": FIELD_TYPE_LABELS.get(ft.value, DEFAULT_FIELD_TYPE_LABEL),
            "requires_schema": ft.value in SCHEMA_BEARING_FIELD_TYPES,
        }
        for ft in FieldType
    ]
