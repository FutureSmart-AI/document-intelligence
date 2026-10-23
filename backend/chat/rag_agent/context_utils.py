"""
Context formatting utilities for RAG agent.

Migrated from agentic_rag.langgraph_utils.nodes.agent_node.context_builders
(only the format_metadata_schema_for_llm function which is still used).
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_metadata_catalog_tool_result(
    schema: Dict[str, Any],
) -> Optional[tuple[str, Dict[str, Any]]]:
    """Build the prefetched metadata catalog in the same content/artifact shape
    as the live ``retrieve_metadata_catalog`` tool.

    Returns:
        Tuple of ``(content, artifact)`` where ``content`` is the Markdown
        shown to the LLM / traces and ``artifact`` is the structured payload
        consumed by downstream state extraction.
    """
    artifact = build_metadata_catalog_tool_response(schema)
    if not artifact:
        return None

    from chat.rag_tools.renderers import render_metadata_catalog

    content = render_metadata_catalog(
        fields=artifact.get("fields", []),
        values_by_field=artifact.get("values_by_field"),
        truncation_note=artifact.get("truncation_note"),
    )
    return content, artifact


def build_metadata_catalog_tool_response(
    schema: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Convert a prefetched lightweight metadata schema to the exact response
    shape the ``retrieve_metadata_catalog`` tool would produce .

    This lets us inject the schema into the RAG agent's message history as a
    completed tool call, skipping the first LLM-triggered metadata exploration
    hop for every query.

    Input shape (from ``get_lightweight_schema``):
        {
          "fields": {field_name: {"field_type": ..., "description": ..., "level": ...}},
          "enumerable_values": {field_name: [values]},
          "high_cardinality_fields": [field_name],
          "document_count": int,
        }

    Output shape (matches retrieve_metadata_catalog v2):
        {
          "fields": [
            {"field_name": "...", "field_type": "...", "description": "...", "cardinality_hint": "low|high|unknown"}
          ],
          "values_by_field": {
            "<field_name>": {"values": [...], "field_type": "..."}
          }
        }
    """
    if not schema or not schema.get("fields"):
        return None

    raw_fields: Dict[str, Any] = schema.get("fields", {})
    enumerable: Dict[str, List[Any]] = schema.get("enumerable_values", {})
    high_card = set(schema.get("high_cardinality_fields", []))

    fields_array: List[Dict[str, Any]] = []
    values_by_field: Dict[str, Dict[str, Any]] = {}

    for fname, finfo in raw_fields.items():
        if not isinstance(finfo, dict):
            continue
        ftype = finfo.get("field_type", "unknown")
        fdesc = finfo.get("description", "")

        # distinct_count is the canonical signal; cardinality_hint kept for
        # backward compat. lower_bound=true means there are likely more
        # values than what's shown (fetch cap / budget trim / parent high-card).
        distinct_count = finfo.get("distinct_count")
        if distinct_count is None:
            distinct_count = len(enumerable.get(fname, []))
        is_lower_bound = bool(finfo.get("distinct_count_is_lower_bound", fname in high_card))

        if fname in enumerable and not is_lower_bound:
            cardinality = "low"
        elif fname in high_card or is_lower_bound:
            cardinality = "high"
        else:
            cardinality = "unknown"

        field_entry: Dict[str, Any] = {
            "field_name": fname,
            "field_type": ftype,
            "description": fdesc,
            "distinct_count": distinct_count,
            "distinct_count_is_lower_bound": is_lower_bound,
            "cardinality_hint": cardinality,
        }
        parent = finfo.get("parent_field")
        if parent:
            field_entry["parent_field"] = parent
            field_entry["leaf_key"] = finfo.get("leaf_key")
        fields_array.append(field_entry)

        if fname in enumerable:
            values_by_field[fname] = {
                "values": enumerable[fname],
                "field_type": ftype,
                "distinct_count": distinct_count,
                "is_complete": not is_lower_bound,
            }

    result: Dict[str, Any] = {
        "fields": fields_array,
        "values_by_field": values_by_field,
    }

    if schema.get("has_truncated_values"):
        # List exactly which fields had their values trimmed by the budget,
        # so the agent doesn't assume EVERY field is partial. Allowlist
        # fields (document_type, etc.) are never trimmed.
        from chat.schema import ALWAYS_INCLUDE_FIELDS
        trimmed_fields = sorted(
            f for f in enumerable
            if f not in ALWAYS_INCLUDE_FIELDS
        )
        if trimmed_fields:
            result["truncation_note"] = (
                f"Only these fields have a representative subset of values due to prompt size limits: "
                f"{trimmed_fields}. Their `values` lists are NOT exhaustive — "
                f"call retrieve_metadata_catalog(include_values=true, field_names=[...]) "
                f"to fetch the complete list for any of them."
            )

    return result


def format_metadata_schema_for_llm(
    schema: dict,
    char_budget: int = 0,
) -> str:
    """
    Turn the lightweight metadata schema into a compact prompt section headed
    by ``metadata fields`` that tells the LLM which fields exist, which have
    enumerable values it can use directly in metadata_filter, and which require
    on-demand lookup.

    If *char_budget* > 0 the output is capped: always-include fields are
    rendered first (never truncated), then remaining fields are added until
    the budget is reached.  Fields that don't fit are collapsed to a compact
    "field (type) — many values, lookup on demand" line.
    """
    if not schema or not schema.get("fields"):
        return ""

    from chat.schema import ALWAYS_INCLUDE_FIELDS

    enumerable = schema.get("enumerable_values", {})
    high_card = set(schema.get("high_cardinality_fields", []))

    def _field_line(fname: str, finfo: dict, force_compact: bool = False) -> str:
        ftype = finfo.get("field_type", "unknown")
        desc = finfo.get("description", "")
        desc_part = f" — {desc}" if desc else ""

        if not force_compact and fname in enumerable:
            vals = enumerable[fname]
            vals_str = ", ".join(str(v) for v in vals)
            return f"- {fname} ({ftype}){desc_part} — values: [{vals_str}]"
        elif fname in high_card or force_compact:
            if "date" in ftype.lower() or "date" in fname.lower() or "time" in fname.lower():
                return f"- {fname} ({ftype}){desc_part} — use range filters"
            return f"- {fname} ({ftype}){desc_part} — many values, use retrieve_metadata_catalog(include_values=true, field_names=[\"{fname}\"], limit=100) when this field is relevant to the query"
        return f"- {fname} ({ftype}){desc_part}"

    priority_lines: list[str] = []
    regular_lines: list[str] = []

    for fname, finfo in schema["fields"].items():
        line = _field_line(fname, finfo)
        if fname in ALWAYS_INCLUDE_FIELDS:
            priority_lines.append(line)
        else:
            regular_lines.append(line)

    header = [
        "**PREFETCHED METADATA FIELDS FOR THIS KNOWLEDGE BASE**",
        "(You already have this data — do NOT call retrieve_metadata_catalog to rediscover these fields.)",
        "(Use the field names and values below directly in metadata_filter when the query needs filtering.)",
        "",
    ]

    if priority_lines:
        header.append("Key document-level fields (always available for filtering):")
    else:
        header.append("Fields (use these exact names in metadata_filter):")

    lines = header + priority_lines
    if priority_lines and regular_lines:
        lines.append("")
        lines.append("Other fields:")

    if char_budget > 0:
        budget_used = sum(len(l) + 1 for l in lines)
        footer_cost = 200
        remaining_budget = char_budget - budget_used - footer_cost

        for fname_line in regular_lines:
            cost = len(fname_line) + 1
            if cost <= remaining_budget:
                lines.append(fname_line)
                remaining_budget -= cost
            else:
                fname = fname_line.split(" (")[0].lstrip("- ")
                finfo = schema["fields"].get(fname, {})
                compact = _field_line(fname, finfo, force_compact=True)
                lines.append(compact)
    else:
        lines.extend(regular_lines)

    lines.append("")
    lines.append("HOW TO USE: Construct metadata_filter directly from the enumerated values above.")
    lines.append("For fields with listed values (e.g. \"values: [...]\"), use those values directly — no tool call needed.")
    lines.append("If you need fuller metadata values for filtering, always call retrieve_metadata_catalog(include_values=true, field_names=[the query-relevant field name(s)], limit=100) and keep the request limited to only those fields.")

    if schema.get("has_truncated_values"):
        lines.append("")
        lines.append(
            "Note: The metadata values provided above are a representative subset due to prompt size limits. "
            "Additional values exist in the knowledge base. If needed, use retrieve_metadata_catalog "
            "to fetch more complete or specific values before applying filters."
        )

    return "\n".join(lines)
