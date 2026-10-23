"""
Lightweight metadata schema builder for LLM prompt injection.

Ported from fs-agent-chat/chat_utils/knowledge/metadata.py:131-388. This logic
is pure Python (cardinality classification, character-budget trimming,
leaf-path expansion) with zero I/O beyond a single call to
knowledge_adapter.get_metadata_fields, so it ports unchanged onto the
in-process adapter — no HTTP client, no separate per-field fetches.
"""

import json
import re
from typing import Any, Dict, List, Optional

from utils.logger import logger
from chat.knowledge_adapter import get_metadata_fields

HIGH_CARDINALITY_HINTS = frozenset([
    "email", "id", "url", "date", "time", "phone",
    "address", "path", "uri", "timestamp", "created", "updated",
    "modified", "uuid", "filename", "file_name",
    "summary", "title", "description", "name", "content",
    "text", "body", "comment", "note", "outline", "temporal",
])

CARDINALITY_THRESHOLD = 100
METADATA_VALUE_CHAR_BUDGET = 2_000  # max chars for all enumerable value expansions in prefetch

ALWAYS_INCLUDE_FIELDS = frozenset(["document_type"])

PREFETCH_EXCLUDED_FIELDS = frozenset(["summary", "document_outline", "title"])

BULK_CAP_HINT = 10


def _looks_high_cardinality(field_name: str) -> bool:
    """Heuristic: True for field names likely unique-ish per document.

    Word-boundary matching (split on underscores/hyphens) avoids false
    positives like `temporal_context` matching `time`.
    """
    tokens = set(re.split(r"[_\-]", field_name.lower()))
    return bool(tokens & HIGH_CARDINALITY_HINTS)


def _normalize_metadata_values(values: List[Any]) -> List[str]:
    """Flatten enrichment values to display strings for prompts.

    Dict values: unwrap API-wrapper shapes (`{"value"/"label"/"name": ...}`);
    otherwise keep the JSON shape so nested keys remain visible.
    """
    out: List[str] = []
    for v in values or []:
        if v is None:
            continue
        if isinstance(v, str):
            s = v.strip()
            if s:
                out.append(s)
        elif isinstance(v, dict):
            wrapper_keys = ("value", "label", "name")
            if any(k in v for k in wrapper_keys) and len(v) <= 2:
                inner = v.get("value", v.get("label", v.get("name")))
                if inner is not None:
                    out.append(str(inner).strip())
            else:
                s = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                if s and s != "{}":
                    out.append(s)
        elif isinstance(v, list):
            s = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
            if s and s != "[]":
                out.append(s)
        else:
            out.append(str(v).strip())

    seen = set()
    unique: List[str] = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def _apply_iterative_budget(schema: Dict[str, Any], budget: int = METADATA_VALUE_CHAR_BUDGET) -> None:
    """Enforce a character budget using iterative per-field value trimming.

    1. ALWAYS_INCLUDE_FIELDS are never trimmed — reserved first.
    2. For all other fields, find the max N (values-per-field) such that
       showing N values for every field stays within the remaining budget.
    3. Trim every non-always field to N values.
    4. Set schema["has_truncated_values"] if anything was trimmed/dropped.
    """
    enumerable: Dict[str, List] = schema["enumerable_values"]
    high_card: List[str] = schema["high_cardinality_fields"]

    def _value_chars(vals: List, limit: Optional[int] = None) -> int:
        vs = vals[:limit] if limit is not None else vals
        return sum(len(str(v)) + 2 for v in vs)

    always_fields = [f for f in enumerable if f in ALWAYS_INCLUDE_FIELDS]
    other_fields = [f for f in enumerable if f not in ALWAYS_INCLUDE_FIELDS]

    always_chars = sum(_value_chars(enumerable[f]) for f in always_fields)

    if always_chars >= budget:
        logger.warning(
            f"[prefetch] iterative-budget: ALWAYS_INCLUDE_FIELDS use {always_chars} chars "
            f"(budget={budget}); dropping all other fields"
        )
        for fname in other_fields:
            del enumerable[fname]
            if fname not in high_card:
                high_card.append(fname)
        schema["has_truncated_values"] = bool(other_fields)
        return

    remaining = budget - always_chars

    if not other_fields:
        schema["has_truncated_values"] = False
        return

    total_all = sum(_value_chars(enumerable[f]) for f in other_fields)
    if total_all <= remaining:
        schema["has_truncated_values"] = False
        return

    # Highest N such that N values per field fits in remaining budget.
    # Floor at 1: every field keeps at least one sample so the agent sees
    # the field's value shape (important for nested dict fields).
    max_n = max(len(enumerable[f]) for f in other_fields)
    best_n = 1
    for n in range(2, max_n + 1):
        cost = sum(_value_chars(enumerable[f], n) for f in other_fields)
        if cost > remaining:
            break
        best_n = n

    truncated = False
    for fname in list(other_fields):
        vals = enumerable[fname]
        if len(vals) > best_n:
            enumerable[fname] = vals[:best_n]
            truncated = True

    final_chars = always_chars + sum(_value_chars(enumerable[f]) for f in other_fields)
    if final_chars > budget:
        logger.warning(
            f"[prefetch] iterative-budget ({budget} chars) exceeded — "
            f"using {final_chars} chars to keep 1 sample value per field "
            f"({len(other_fields)} non-always fields)"
        )

    schema["has_truncated_values"] = truncated
    logger.info(
        f"[prefetch] iterative-budget ({budget} chars): {best_n} values/field "
        f"across {len(other_fields)} fields; truncated={truncated}; "
        f"final_chars={final_chars}"
    )


def _expand_leaf_paths(schema: Dict[str, Any]) -> None:
    """Derive addressable leaf paths for dict / List[dict] fields.

    For a dict field `<f>` whose sample values look like `{"k1": v1, ...}`,
    adds `<f>.k1`, `<f>.k2`, ... to schema["fields"]/["enumerable_values"].
    For List[dict], the separator is `[].` instead of `.`.
    """
    fields_def: Dict[str, Dict[str, Any]] = schema["fields"]
    enumerable: Dict[str, List[str]] = schema["enumerable_values"]

    new_field_entries: Dict[str, Dict[str, Any]] = {}
    new_enumerable: Dict[str, List[str]] = {}

    for fname, finfo in list(fields_def.items()):
        ftype = str(finfo.get("field_type", "")).lower()
        is_dict = "dict" in ftype
        is_list_dict = ("list" in ftype) and is_dict
        if not is_dict and not is_list_dict:
            continue

        values = enumerable.get(fname, [])
        if not values:
            continue

        per_key_values: Dict[str, set] = {}

        def _ingest_dict(d: Dict[str, Any]) -> None:
            for k, v in d.items():
                if v is None:
                    continue
                if isinstance(v, (dict, list)):
                    serialized = json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                    per_key_values.setdefault(k, set()).add(serialized)
                else:
                    s = str(v).strip()
                    if s:
                        per_key_values.setdefault(k, set()).add(s)

        for raw_v in values:
            try:
                parsed = json.loads(raw_v) if isinstance(raw_v, str) else raw_v
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                _ingest_dict(parsed)
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        _ingest_dict(item)

        if not per_key_values:
            continue

        sep = "[]." if is_list_dict else "."
        for sub_key, vals_set in per_key_values.items():
            leaf_path = f"{fname}{sep}{sub_key}"
            new_field_entries[leaf_path] = {
                "field_type": "str",
                "description": f"Leaf path of `{fname}` (key `{sub_key}`)",
                "level": finfo.get("level", ""),
                "parent_field": fname,
                "leaf_key": sub_key,
            }
            new_enumerable[leaf_path] = sorted(vals_set)

        logger.info(
            f"[prefetch] Leaf-path expansion for '{fname}' ({ftype}): "
            f"{len(per_key_values)} sub-keys -> {list(per_key_values.keys())}"
        )

    fields_def.update(new_field_entries)
    enumerable.update(new_enumerable)


async def get_lightweight_schema(knowledge_id: str) -> Dict[str, Any]:
    """
    Build a compact metadata schema for LLM prompt injection.

    Strategy (tiered prefetch):
      1. Single call to get_metadata_fields(include_values=True) — field defs
         AND a capped sample of values in one pass (no per-field HTTP calls;
         there's no HTTP at all now, but the single-call shape is kept since
         resolve_filter_value handles exhaustive per-field lookups on demand).
      2. Classify fields as enumerable (low-cardinality) vs high-cardinality
         using the bulk value sample plus name heuristics.
      3. Expand dict/List[dict] fields into addressable leaf paths.
      4. Trim to a character budget so the schema is cheap to inject into
         every turn's prompt.

    Returns:
        {
            "fields": {"<field_name>": {"field_type", "description", "level", ...}},
            "enumerable_values": {"<field_name>": ["val1", "val2", ...]},
            "high_cardinality_fields": ["field_a", "field_b", ...],
            "has_truncated_values": bool,
        }
    """
    if not knowledge_id:
        logger.warning("get_lightweight_schema called without knowledge_id")
        return {"fields": {}, "enumerable_values": {}, "high_cardinality_fields": []}

    logger.info(f"[prefetch] Building lightweight schema | knowledge_id={knowledge_id}")

    fields_result = await get_metadata_fields(knowledge_id, include_values=True)
    raw_fields = fields_result.get("fields", [])
    logger.info(f"[prefetch] Adapter returned {len(raw_fields)} raw fields (with values)")

    schema: Dict[str, Any] = {
        "fields": {},
        "enumerable_values": {},
        "high_cardinality_fields": [],
    }

    fields_to_fetch: List[str] = []
    bulk_values_by_field: Dict[str, List] = {}
    excluded: List[str] = []
    chunk_skipped: List[str] = []

    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        fname = f.get("field_name")
        if not fname:
            continue
        ftype = f.get("field_type", "")
        schema["fields"][fname] = {
            "field_type": ftype,
            "description": f.get("description", ""),
            "level": f.get("level", ""),
        }

        if fname in PREFETCH_EXCLUDED_FIELDS:
            excluded.append(fname)
            schema["high_cardinality_fields"].append(fname)
            continue

        level = (f.get("level") or "").lower()
        if level == "chunk":
            chunk_skipped.append(fname)
            schema["high_cardinality_fields"].append(fname)
            continue

        # Real document-derived values only — never fall back to sample_values
        # (those are declared examples from field setup, not actual distinct
        # values, and would mislead filter construction).
        real_vals = f.get("values") or f.get("metadata") or []
        bulk_values_by_field[fname] = real_vals if isinstance(real_vals, list) else []
        fields_to_fetch.append(fname)

    if excluded:
        logger.info(f"[prefetch] Excluded fields (PREFETCH_EXCLUDED_FIELDS): {excluded}")
    if chunk_skipped:
        logger.info(f"[prefetch] Chunk-level fields skipped: {chunk_skipped}")
    logger.info(
        f"[prefetch] Classification — to_fetch={len(fields_to_fetch)} {fields_to_fetch} | "
        f"high_cardinality={len(schema['high_cardinality_fields'])}"
    )

    for fname in fields_to_fetch:
        values = bulk_values_by_field.get(fname, [])
        normalized = _normalize_metadata_values(values)
        if not normalized:
            schema["high_cardinality_fields"].append(fname)
            logger.info(f"[prefetch] '{fname}' — 0 values from bulk, marked high-cardinality")
            continue

        schema["enumerable_values"][fname] = normalized

        if len(normalized) >= BULK_CAP_HINT:
            schema["high_cardinality_fields"].append(fname)
            logger.info(
                f"[prefetch] '{fname}' — {len(normalized)} values "
                f"(hit bulk cap {BULK_CAP_HINT}), high-cardinality"
            )
        else:
            logger.info(f"[prefetch] '{fname}' — {len(normalized)} values")

    _expand_leaf_paths(schema)

    pretrim_counts: Dict[str, int] = {
        fname: len(vals) for fname, vals in schema["enumerable_values"].items()
    }

    high_card_set = set(schema["high_cardinality_fields"])
    for fname, finfo in list(schema["fields"].items()):
        parent = finfo.get("parent_field")
        if parent and parent in high_card_set and fname not in high_card_set:
            schema["high_cardinality_fields"].append(fname)
            high_card_set.add(fname)

    _apply_iterative_budget(schema)

    high_card_set = set(schema["high_cardinality_fields"])
    for fname, finfo in schema["fields"].items():
        vals = schema["enumerable_values"].get(fname, [])
        post_count = len(vals)
        pre_count = pretrim_counts.get(fname, post_count)
        finfo["distinct_count"] = max(pre_count, post_count)
        budget_truncated = post_count < pre_count
        is_lower_bound = (fname in high_card_set) or budget_truncated
        finfo["distinct_count_is_lower_bound"] = is_lower_bound
        finfo["enumerable"] = fname in schema["enumerable_values"] and not is_lower_bound

    logger.info(
        f"[prefetch] Schema ready — total={len(schema['fields'])} fields | "
        f"enumerable={len(schema['enumerable_values'])} {list(schema['enumerable_values'].keys())} | "
        f"high_cardinality={len(schema['high_cardinality_fields'])} {schema['high_cardinality_fields']}"
    )
    return schema
