"""
Filter-value resolution tool.

Thin wrapper that returns the full canonical value list for a single
metadata field (or leaf path). The agent calls it BEFORE constructing a
`metadata_filter` so it has the complete set of stored values to choose
from — no guessing, no LLM-based fuzzy match, no scoring.

Behavior:
- If the prefetched schema cache has the field's complete value list
  (not budget-trimmed, not inherited from a high-cardinality parent),
  return that list directly. Sub-millisecond, no API call.
- Otherwise fall back to `knowledge_get_metadata_values(field_name, limit=1000)`
  to pull the canonical list from the backend. The limit is high on purpose:
  the backend enrichment endpoint silently caps its result and can report a
  capped slice as complete, so a real value can be absent purely because the
  cap was hit. A high limit makes the common case exhaustive; if the field
  still exceeds it, the result is flagged as a partial sample (`is_complete`).

Output: a flat list of strings under `values`. The agent drops the ones
matching its intent into a `$in` clause.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from utils.logger import logger
from chat.knowledge_adapter import (
    get_metadata_values as knowledge_get_metadata_values,
)


class ResolveFilterValueInput(BaseModel):
    """Input schema for resolve_filter_value tool."""

    knowledge_id: str = Field(description="The knowledge base ID")
    field_name: str = Field(
        description=(
            "The metadata field whose canonical value list should be returned. "
            "Leaf paths are accepted (e.g. '<dict_field>.<key>', "
            "'<list_of_dict_field>[].<key>')."
        )
    )
    limit: int = Field(
        default=1000,
        description=(
            "Maximum number of canonical values to return (default 1000). "
            "The default is high on purpose: the backend enrichment endpoint "
            "silently caps its result and can report a capped slice as if it "
            "were complete, so a value you are looking for can be absent purely "
            "because the cap was hit. A high limit makes the common case "
            "exhaustive; if the field still exceeds it, the result is flagged "
            "as a partial sample."
        ),
    )


# ---------------------------------------------------------------------------
# Schema-cache accessor — reuses the in-memory cache populated by rag_node.
# ---------------------------------------------------------------------------

def _cached_field_values(knowledge_id: str, field_name: str) -> Optional[List[str]]:
    """Read enumerable values for a field from the rag_node schema cache.

    Returns None when the cache:
    - is empty, or
    - the schema doesn't have the field, or
    - the field is flagged as `distinct_count_is_lower_bound` (fetch hit
      cap, parent was high-cardinality, or iterative budget trimmed it), or
    - the cached list is shorter than the observed `distinct_count`, or
    - a leaf path's parent is high-cardinality.

    Falls through to the API fetch in those cases.

    Note: the rag_node schema cache (agents.nodes.rag._SCHEMA_MEM_CACHE) is
    part of a later porting phase (rag.py's node logic, per the plan's
    Phase 3 step 16) and does not exist yet in this repo — the import below
    always raises ImportError until that lands, which the except clause
    already treats as "no cache" (falls through to the API fetch below).
    """
    try:
        from agents.nodes.rag import _SCHEMA_MEM_CACHE  # local import to avoid cycle
    except Exception:
        return None

    entry = _SCHEMA_MEM_CACHE.get(knowledge_id) if isinstance(_SCHEMA_MEM_CACHE, dict) else None
    if not entry:
        return None
    schema, _ts = entry
    enumerable = (schema or {}).get("enumerable_values", {}) or {}
    fields_def = (schema or {}).get("fields", {}) or {}

    finfo = fields_def.get(field_name) or {}
    is_lower_bound = bool(finfo.get("distinct_count_is_lower_bound"))
    distinct_count = finfo.get("distinct_count") or 0
    vals = enumerable.get(field_name)
    if not vals:
        return None
    if is_lower_bound:
        return None
    if distinct_count and len(vals) < distinct_count:
        return None
    parent = finfo.get("parent_field")
    if parent:
        parent_finfo = fields_def.get(parent) or {}
        if parent_finfo.get("distinct_count_is_lower_bound"):
            return None
    return list(vals)


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

@tool("resolve_filter_value", args_schema=ResolveFilterValueInput, response_format="content_and_artifact")
async def resolve_filter_value_tool(
    knowledge_id: str,
    field_name: str,
    limit: int = 1000,
):
    """Return the full canonical value list for a single metadata field.

    USE THIS BEFORE CONSTRUCTING A `metadata_filter` whenever you need the
    exact stored values for a field (including leaf paths). Returns a flat
    list of strings — pick the ones matching your intent and drop them into
    a `$in` clause.

    Returns:
        Tuple of (markdown content, artifact dict with `values` list).
    """
    cached = _cached_field_values(knowledge_id, field_name)
    source = "cache" if cached is not None else "api"
    values: List[str] = cached or []
    # The cache accessor only returns a field when its complete value list is
    # cached (it rejects lower-bound / sample / parent-high-cardinality fields),
    # so a cache hit is always complete. The API path sets this from the
    # backend's values-vs-sample_values signal below.
    is_complete = cached is not None

    if not values:
        try:
            res = await knowledge_get_metadata_values(
                knowledge_id=knowledge_id,
                field_name=field_name,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"resolve_filter_value: API fetch for '{field_name}' failed: {e}")
            artifact = {
                "values": [],
                "field_name": field_name,
                "error": f"could not fetch values for '{field_name}': {e}",
                "source": source,
            }
            return f"_error fetching values for `{field_name}`: {e}_", artifact

        from chat.schema import _normalize_metadata_values
        values = _normalize_metadata_values(res.get("values", []))
        # Backend tells us whether this is the full distinct set or a sample
        # (high-cardinality fields return sample_values). Default to True only
        # if the key is absent (older backends) — but prefer the explicit flag.
        is_complete = bool(res.get("is_complete", True))

    if limit and len(values) > limit:
        values = values[:limit]
        is_complete = False  # we truncated — definitely not the full set

    artifact: Dict[str, Any] = {
        "values": values,
        "field_name": field_name,
        "source": source,
        "count": len(values),
        "is_complete": is_complete,
    }

    if not values:
        content = (
            f"No values found for `{field_name}` (source: {source}). "
            f"Either the field has no values or the field name is wrong."
        )
    else:
        completeness = "complete list" if is_complete else "PARTIAL SAMPLE — not exhaustive"
        lines = [
            f"**resolve_filter_value** — `{field_name}` "
            f"(source: {source}, {len(values)} values, {completeness}):",
            "",
        ]
        for v in values:
            lines.append(f"- `{v}`")
        if not is_complete:
            lines.append("")
            lines.append(
                "⚠️ **This is a SAMPLE of a high-cardinality field, NOT the complete value list.** "
                "A value you are looking for may exist but not appear above. Do NOT conclude a "
                "value is absent from this list. To find a specific entity in this field, use "
                "semantic search instead: `retrieve_documents(query=\"<entity name>\")` — let "
                "retrieval match the doc directly rather than scanning this partial enumeration."
            )
        content = "\n".join(lines)

    return content, artifact
