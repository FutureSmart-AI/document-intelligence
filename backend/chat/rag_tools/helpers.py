"""
Helper functions for RAG tools.
"""
import ast
import logging
from typing import Dict, Any, Optional, List
from utils.logger import logger


# Backend-supported per-field operators, in both wire shapes the model may emit:
#   - shorthand `$`-op wrapper:  {"field": {"$in": [...]}}, {"field": {"$nin": [...]}},
#                                {"field": {"$ne": x}}
#   - verbose op/value wrapper:  {"field": {"op": "ne"|"nin"|"not_in"|"in",
#                                            "value": x}}
# Both are PRESERVED verbatim through normalization and passed to the backend,
# which evaluates them. The verbose negatives the search endpoint supports are
# ne / nin / not_in (positive `in`/`eq` may also appear).
# Single-key shorthand operator names, with and without the `$` prefix — the
# model emits both ({"$nin": [...]} and {"not_in": [...]}). Bare keys that
# collide with real metadata field names are avoided here: only operator-looking
# names (nin/not_in/ne/in) are treated as operators.
_BARE_RANGE_OPS = frozenset({"gte", "lte", "gt", "lt"})
_LOGICAL_OPS = ("$and", "$or", "$nor", "$not")

# Single-key `$` operators that have a backend equivalent in the verbose op
# syntax (retrieval_ops._build_simple_field_condition). Anything else is
# refused by name rather than unwrapped: `{"title": {"$contains": "engineer"}}`
# used to become the EQUALITY filter `{"title": "engineer"}` — a different
# question, answered without a word.
_OP_TO_VERBOSE = {"$contains": "contains", "$not_contains": "not_contains", "$eq": "eq"}


class UnsupportedMetadataFilter(ValueError):
    """A metadata_filter clause the retrieval layer cannot evaluate. Raised,
    never dropped: the tool returns it to the agent as an error so it can
    rewrite the filter, instead of answering over a silently wider set."""

_SUPPORTED_SHORTHAND_OPS = frozenset(
    {"$in", "$nin", "$not_in", "$ne",
     "nin", "not_in", "ne"}
)
_SUPPORTED_VERBOSE_OPS = frozenset(
    {"eq", "ne", "in", "nin", "not_in", "contains"}
)


def _is_supported_op_wrapper(value: Any) -> bool:
    """True when ``value`` is a backend operator clause to pass through untouched.

    Recognizes both the shorthand ``{"$op": ...}`` form and the verbose
    ``{"op": ..., "value": ...}`` form. Any other dict is a real nested-field
    filter and must go through the flattening logic instead.
    """
    if not isinstance(value, dict) or not value:
        return False
    # Shorthand: single `$`-prefixed key from the supported set.
    if len(value) == 1:
        only_key = next(iter(value))
        if isinstance(only_key, str) and only_key in _SUPPORTED_SHORTHAND_OPS:
            return True
    # Verbose: {"op": <supported>, "value": ...}.
    if "op" in value and "value" in value and isinstance(value.get("op"), str):
        return value["op"] in _SUPPORTED_VERBOSE_OPS
    return False


def _extract_range_clause(key: str, value: Any) -> Optional[Dict[str, Any]]:
    """If ``value`` is a range wrapper, return it in the bare `gte`/`lte` form.

    Accepts both `{"$gte": a, "$lte": b}` and the already-bare `{"gte": a}`.
    Returns None for anything that is not a range, so the caller can pass it on
    to the normal normalization path.

    The `$` is stripped because the retrieval layer's range branch
    (utils/db/retrieval_ops.py::_build_range_condition) matches the bare form.
    """
    if not isinstance(value, dict) or not value:
        return None

    bounds = {
        k.lstrip("$"): v for k, v in value.items()
        if isinstance(k, str) and k.lstrip("$") in _BARE_RANGE_OPS and v is not None
    }
    if not bounds:
        return None

    others = [k for k in value.keys() if not (isinstance(k, str) and k.lstrip("$") in _BARE_RANGE_OPS)]
    if others:
        # A range combined with $in/$ne on the same field has no single
        # representation. Keep the interval — it is the narrower, safer half —
        # and name what was dropped rather than dropping it quietly.
        logger.warning(
            f"normalize_metadata_filter: kept range bounds {sorted(bounds)} on '{key}', "
            f"dropped operators that cannot combine with a range: {others}"
        )
    return bounds


def normalize_metadata_filter(
    metadata_filter: Optional[Dict[str, Any]],
    field_types: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, Any]]:
    """Normalize a metadata filter, handling range clauses separately.

    Range clauses are lifted out BEFORE the rest of normalization runs and
    merged back into the result afterwards. That separation is not cosmetic: a
    bare `{"gte": ..., "lte": ...}` left in the dict is indistinguishable from
    a nested-field filter, and the nested-dict handling below rewrites it to
    `field.gte` / `field[].gte` — a payload key that does not exist, which
    matches nothing and reports no error. Held aside, an interval survives
    intact with all of its bounds.

    Everything else is delegated to _normalize_metadata_filter_inner, unchanged.
    """
    if not isinstance(metadata_filter, dict) or not metadata_filter:
        return _normalize_metadata_filter_inner(metadata_filter, field_types)

    range_clauses: Dict[str, Any] = {}
    remainder: Dict[str, Any] = {}
    for key, value in metadata_filter.items():
        clause = _extract_range_clause(key, value)
        if clause is None:
            remainder[key] = value
        else:
            range_clauses[key] = clause
            logger.info(f"normalize_metadata_filter: range on '{key}' → {clause}")

    if not range_clauses:
        return _normalize_metadata_filter_inner(metadata_filter, field_types)

    normalized = dict(_normalize_metadata_filter_inner(remainder, field_types) or {}) if remainder else {}
    normalized.update(range_clauses)
    return normalized or None


def _normalize_metadata_filter_inner(
    metadata_filter: Optional[Dict[str, Any]],
    field_types: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Normalize metadata_filter coming from agents, LangSmith, or vector stores.

    Handles:
    1. ReAct logical filters:
       {"all": [{"key": "organizations", "value": "TechNova"}]}
    2. $operator wrappers:
       {"keywords": {"$contains": ["Trendora Mart"]}}
    3. Already-flat metadata:
       {"organizations": "TechNova"}
    4. Dot-notation keys for List[dict] fields:
       {"skills.related_tools": "Redux"}
       Converts to: {"skills[].related_tools": ["Redux"]} (if skills is List[dict])
    5. Actual nested dictionaries (dict fields):
       {"address": {"city": "Pune", "state": "Maharashtra"}}
       Converts to: {"address.city": "Pune", "address.state": "Maharashtra"}
    6. Actual nested dictionaries (List[dict] fields):
       {"skills": {"name": "React"}}
       Converts to: {"skills[].name": ["React"]}
    7. String representations of nested dictionaries:
       {"address": "{'city': 'Pune', 'state': 'Maharashtra'}"}
       Converts to: {"address.city": "Pune", "address.state": "Maharashtra"}
    8. String representations of lists of dictionaries:
       {"address": "[{'city': 'Pune'}, {'city': 'Mumbai'}]"}
       Converts to: {"address[].city": ["Pune", "Mumbai"]}
    """

    if not metadata_filter:
        return metadata_filter

    # ---- Case 0: Normalize $-prefixed operators ----
    # Most operators are unwrapped to their raw values before the filter leaves
    # this function. Two kinds survive, because the retrieval layer really does
    # implement them (utils/db/retrieval_ops.py::_build_simple_field_condition):
    # the set operators ($in/$nin/$ne), kept in their `$` form, and RANGE
    # operators ($gte/$lte/$gt/$lt), unprefixed to the bare `gte`/`lte` form
    # that layer matches on.
    #
    # Ranges used to be dropped here on the belief that "the backend does not
    # support range operators". It does — and dropping them is what made a
    # `date` field unusable for the only question worth asking of one ("expiring
    # in Q2"), since an interval silently became an unbounded query.
    #
    # Semantics differ by operator:
    #
    # Top-level logical:
    #   - $and  : merge all branch dicts into one flat filter (semantically correct).
    #   - $or   : reduce to FIRST branch only (LOSSY — narrows the set;
    #             logged as WARNING so callers can see the lossy fallback).
    #   - $nor / $not : dropped entirely (negation not representable).
    #
    # Per-field operator wrappers:
    #   - {field: {$gte: a, $lte: b}} → {field: {gte: a, lte: b}}  (range, bounds KEPT)
    #   - {field: {$in: [...]}}       → unchanged (retrieval understands $in/$nin/$ne)
    #   - {field: {$op: v}}           → {field: v}    (single op, drop wrapper)
    #   - {field: {$op1: v1, ...}}    → {field: [v1, ...]}  (multi-op, collect values)
    if isinstance(metadata_filter, dict):
        logical_keys = [k for k in metadata_filter.keys() if isinstance(k, str) and k in _LOGICAL_OPS]
        if logical_keys:
            # The retrieval layer evaluates $and / $or / $nor / $not natively
            # (retrieval_ops._build_logical_condition). Each branch is normalized
            # on its own and the operator is KEPT. This used to cut $or down to
            # its first branch and drop $nor/$not — the answer was then computed
            # over a different population than the user asked about, with only
            # a log line to show for it.
            out = dict(normalize_metadata_filter(
                {k: v for k, v in metadata_filter.items() if k not in logical_keys} or None,
                field_types=field_types,
            ) or {})
            for op in logical_keys:
                branches = metadata_filter[op]
                if isinstance(branches, dict):
                    branches = [branches]
                if not isinstance(branches, list) or not branches or not all(isinstance(b, dict) and b for b in branches):
                    raise UnsupportedMetadataFilter(
                        f"'{op}' needs a non-empty list of filter objects, got {branches!r}"
                    )
                normalized_branches = [normalize_metadata_filter(b, field_types=field_types) or {} for b in branches]
                if not all(normalized_branches):
                    raise UnsupportedMetadataFilter(f"'{op}' has a branch with no usable condition: {branches!r}")
                if op == "$and" and not any(set(b) & set(out) for b in normalized_branches) and \
                        len({k for b in normalized_branches for k in b}) == sum(len(b) for b in normalized_branches):
                    # Disjoint keys: a flat dict already means AND.
                    for b in normalized_branches:
                        out.update(b)
                else:
                    out[op] = normalized_branches
            return out or None

    # ---- Case 0b: Strip per-field $-operators (except supported ones) ----
    # Walks the top-level dict and unwraps any value of the form {"$op": v} or
    # {"$op1": v1, "$op2": v2}. The backend natively supports a small set of
    # per-field operators — `$in` (positive membership) plus the negatives
    # `$nin` / `$not_in` (list exclusion) and `$ne` (single-value exclusion).
    # Those wrappers are PRESERVED untouched. All other `$`-prefixed operators
    # are unwrapped to their raw values. This runs BEFORE the rest of the cases
    # so downstream logic always sees raw values (or a supported operator
    # wrapper), never an unsupported one.
    #
    # Note: the verbose negative form {"op": "ne"|"nin"|"not_in",
    # "value": x} has NON-`$` keys, so it does not enter this branch — it is
    # passed through verbatim by the final `normalized[key] = value` fallthrough.
    SUPPORTED_PER_FIELD_OPS = _SUPPORTED_SHORTHAND_OPS
    if isinstance(metadata_filter, dict):
        had_op_wrappers = False
        sanitized: Dict[str, Any] = {}
        for key, value in metadata_filter.items():
            if isinstance(value, dict) and value and all(isinstance(k, str) and k.startswith("$") for k in value.keys()):
                # If the wrapper is a single supported operator (e.g. $in, $nin), keep it as-is.
                if len(value) == 1 and next(iter(value.keys())) in SUPPORTED_PER_FIELD_OPS:
                    sanitized[key] = value
                    continue

                had_op_wrappers = True
                if len(value) == 1 and next(iter(value)) in _OP_TO_VERBOSE:
                    op, op_value = next(iter(value.items()))
                    sanitized[key] = {"op": _OP_TO_VERBOSE[op], "value": op_value}
                    continue
                # Anything else changes the question if it is unwrapped or
                # partially kept, so refuse it by name.
                raise UnsupportedMetadataFilter(
                    f"operator(s) {sorted(value)} on '{key}' are not supported. Supported per-field "
                    f"operators: $in, $nin, $ne, $eq, $contains, $not_contains, $gte, $gt, $lte, $lt; "
                    f"logical: $and, $or, $nor, $not."
                )
            else:
                sanitized[key] = value
        if had_op_wrappers or any(isinstance(v, dict) for v in sanitized.values()):
            metadata_filter = sanitized

    # ---- Case 1: ReAct logical filter AST (any/all/not) ----
    if isinstance(metadata_filter, dict) and (
        "all" in metadata_filter or
        "any" in metadata_filter or
        "not" in metadata_filter
    ):
        # Translated to the $-operators above instead of keeping only `all`
        # (which used to drop `any`/`not` with a log line).
        def _clauses(name: str) -> List[Dict[str, Any]]:
            out = []
            for clause in metadata_filter.get(name) or []:
                if not (isinstance(clause, dict) and "key" in clause and "value" in clause):
                    raise UnsupportedMetadataFilter(f"'{name}' clause must be {{key, value}}, got {clause!r}")
                out.append({clause["key"]: clause["value"]})
            return out

        translated: Dict[str, Any] = {}
        if _clauses("all"):
            translated["$and"] = _clauses("all")
        if _clauses("any"):
            translated["$or"] = _clauses("any")
        if _clauses("not"):
            translated["$nor"] = _clauses("not")
        return normalize_metadata_filter(translated or None, field_types=field_types)

    # ---- Case 2: $operator wrappers and nested dict string parsing ----
    normalized: Dict[str, Any] = {}
    for key, value in metadata_filter.items():
        # Supported backend operator clauses (shorthand $op or verbose op/value)
        # are passed through verbatim — they are NOT nested-field filters and
        # must not be unwrapped or flattened by the logic below.
        if _is_supported_op_wrapper(value):
            normalized[key] = value
            continue

        if "." in key and field_types and len(field_types) > 0:
            base_field = key.split(".", 1)[0]
            field_type = field_types.get(base_field)

            logger.debug(
                f"Processing dot-notation key '{key}': base_field='{base_field}', "
                f"field_type={field_type}, field_types_keys={list(field_types.keys())[:5]}"
            )

            is_list_dict_field = (
                field_type and
                isinstance(field_type, str) and
                ("list" in field_type.lower() and "dict" in field_type.lower())
            )

            if is_list_dict_field:
                nested_key = key.split(".", 1)[1]
                array_key = f"{base_field}[].{nested_key}"
                if isinstance(value, list):
                    normalized[array_key] = value
                else:
                    normalized[array_key] = [value]
                logger.info(
                    f"Converted dot-notation key '{key}' (base field '{base_field}' is List[dict]) "
                    f"to array notation: '{array_key}'"
                )
                continue
            else:
                logger.debug(
                    f"Key '{key}' with base_field '{base_field}' is NOT List[dict] "
                    f"(field_type={field_type}), keeping as dot notation"
                )

        if isinstance(value, dict) and len(value) == 1:
            operator, inner_value = next(iter(value.items()))
            if operator.startswith("$"):
                normalized[key] = inner_value
                continue

        if isinstance(value, dict) and len(value) > 0:
            field_type = field_types.get(key) if field_types else None

            is_list_dict_field = (
                field_type and
                isinstance(field_type, str) and
                ("list" in field_type.lower() and "dict" in field_type.lower())
            )

            has_complex_nested_values = any(
                isinstance(nested_value, (dict, list))
                for nested_value in value.values()
            )

            is_single_simple_filter = (
                len(value) == 1 and
                not has_complex_nested_values
            )

            should_use_array_notation = is_list_dict_field or (
                not field_type and is_single_simple_filter and not has_complex_nested_values
            )

            if should_use_array_notation:
                for nested_key, nested_value in value.items():
                    array_key = f"{key}[].{nested_key}"
                    if isinstance(nested_value, list):
                        normalized[array_key] = nested_value
                    else:
                        normalized[array_key] = [nested_value]
                logger.info(
                    f"Converted List[dict] filter '{key}' (type: {field_type}) to array notation keys: "
                    f"{[f'{key}[].{k}' for k in value.keys()]}"
                )
            else:
                for nested_key, nested_value in value.items():
                    dot_key = f"{key}.{nested_key}"
                    normalized[dot_key] = nested_value
                logger.info(
                    f"Converted nested dict '{key}' (type: {field_type}) to dot-notation keys: "
                    f"{[f'{key}.{k}' for k in value.keys()]}"
                )
            continue

        if isinstance(value, str) and value.strip().startswith("[") and value.strip().endswith("]"):
            try:
                parsed_list = ast.literal_eval(value)
                if isinstance(parsed_list, list) and len(parsed_list) > 0:
                    if all(isinstance(item, dict) for item in parsed_list):
                        all_nested_keys = set()
                        for item in parsed_list:
                            all_nested_keys.update(item.keys())

                        for nested_key in all_nested_keys:
                            array_key = f"{key}[].{nested_key}"
                            nested_values = [
                                item[nested_key]
                                for item in parsed_list
                                if nested_key in item
                            ]
                            normalized[array_key] = nested_values

                        logger.info(
                            f"Converted list of dicts string '{key}' to array notation keys: "
                            f"{[f'{key}[].{k}' for k in all_nested_keys]}"
                        )
                        continue
            except (ValueError, SyntaxError) as e:
                logger.warning(
                    f"Failed to parse list string for key '{key}': {e}. "
                    f"Treating as regular string value."
                )

        if isinstance(value, str) and value.strip().startswith("{") and value.strip().endswith("}"):
            try:
                parsed_dict = ast.literal_eval(value)
                if isinstance(parsed_dict, dict):
                    for nested_key, nested_value in parsed_dict.items():
                        dot_key = f"{key}.{nested_key}"
                        normalized[dot_key] = nested_value
                    logger.info(
                        f"Converted nested dict string '{key}' to dot-notation keys: "
                        f"{[f'{key}.{k}' for k in parsed_dict.keys()]}"
                    )
                    continue
            except (ValueError, SyntaxError) as e:
                logger.warning(
                    f"Failed to parse nested dict string for key '{key}': {e}. "
                    f"Treating as regular string value."
                )

        normalized[key] = value

    return normalized


# ---------------------------------------------------------------------------
# In-memory metadata-filter matcher (PARKED — not wired into the live flow).
#
# `document_matches_metadata_filter` and its helpers below evaluate a normalized
# metadata_filter against a document's metadata in Python. This is only needed
# for semantic-scope mode, where the filter would be applied locally after the
# backend ranks by `query`. That path is NOT currently called anywhere — the
# active retrieval flow sends every filter (positive and negative) to the
# backend, which evaluates it. The block is kept (commented out) so it can be
# restored quickly if in-memory filtering is ever wired in; it already handles
# the negative/substring operators in lockstep with the backend.
# ---------------------------------------------------------------------------
# def document_matches_metadata_filter(
#     metadata: Optional[Dict[str, Any]],
#     metadata_filter: Optional[Dict[str, Any]],
# ) -> bool:
#     """Return True when ``metadata`` satisfies a normalized flat metadata filter.
#
#     Supports the subset of filter syntax the tool emits after normalization:
#     plain equality, list membership, ``$in`` lowered into list values, and the
#     backend negative/substring operators (both wire shapes):
#       - shorthand ``{"field": {"$nin": [...]}}`` / ``{"$ne": x}`` / ``{"$not_contains": x}``
#       - verbose   ``{"field": {"op": "nin"|"ne"|"not_contains"|..., "value": x}}``
#     Keys may use dot notation (``address.city``) or array-dot notation
#     (``skills[].name``). Kept in lockstep with the backend so semantic-scope
#     mode (which applies the filter in memory) matches filter-mode behavior.
#     """
#     if not metadata_filter:
#         return True
#     if not isinstance(metadata, dict):
#         return False
#
#     for key, expected in metadata_filter.items():
#         if not _matches_filter_key(metadata, key, expected):
#             return False
#     return True
#
#
# def _matches_filter_key(metadata: Dict[str, Any], key: str, expected: Any) -> bool:
#     if "[]." in key:
#         base, nested = key.split("[].", 1)
#         container = metadata.get(base)
#         if not isinstance(container, list):
#             return False
#         nested_values = [
#             _extract_nested_value(item, nested)
#             for item in container
#             if isinstance(item, dict)
#         ]
#         flattened = _flatten_values(nested_values)
#         return _value_matches(flattened, expected)
#
#     actual = _extract_nested_value(metadata, key)
#     return _value_matches(actual, expected)
#
#
# def _extract_nested_value(value: Any, path: str) -> Any:
#     current = value
#     for part in path.split("."):
#         if not isinstance(current, dict):
#             return None
#         current = current.get(part)
#     return current
#
#
# def _flatten_values(values: List[Any]) -> List[Any]:
#     flattened: List[Any] = []
#     for value in values:
#         if isinstance(value, list):
#             flattened.extend(_flatten_values(value))
#         elif value is not None:
#             flattened.append(value)
#     return flattened
#
#
# def _parse_operator_clause(expected: Any) -> Optional[tuple]:
#     """Return (op, value) when ``expected`` is an operator clause, else None.
#
#     Normalizes both wire shapes to a bare op name:
#       - shorthand: {"$nin": [...]} -> ("nin", [...]); {"$ne": x} -> ("ne", x)
#       - verbose:   {"op": "nin", "value": [...]} -> ("nin", [...])
#     Only recognizes the supported backend ops; anything else returns None so it
#     falls through to plain equality/membership matching.
#     """
#     if not isinstance(expected, dict) or not expected:
#         return None
#     if len(expected) == 1:
#         only_key = next(iter(expected))
#         if isinstance(only_key, str) and only_key in _SUPPORTED_SHORTHAND_OPS:
#             return only_key.lstrip("$"), expected[only_key]
#     if "op" in expected and "value" in expected and isinstance(expected.get("op"), str):
#         if expected["op"] in _SUPPORTED_VERBOSE_OPS:
#             return expected["op"], expected["value"]
#     return None
#
#
# def _actual_strings(actual: Any) -> set:
#     """Collapse an actual metadata value to a set of comparable strings."""
#     if isinstance(actual, list):
#         return {str(v) for v in _flatten_values(actual) if v is not None}
#     if actual is None:
#         return set()
#     return {str(actual)}
#
#
# def _value_matches(actual: Any, expected: Any) -> bool:
#     clause = _parse_operator_clause(expected)
#     if clause is not None:
#         op, value = clause
#         actual_strings = _actual_strings(actual)
#         value_list = value if isinstance(value, list) else [value]
#         value_strings = {str(v) for v in value_list if v is not None}
#
#         if op in ("eq", "in"):
#             return bool(actual_strings & value_strings)
#         if op in ("ne",):
#             # Excluded unless the actual value is present.
#             return not (actual_strings & value_strings)
#         if op in ("nin", "not_in"):
#             return not (actual_strings & value_strings)
#         if op == "contains":
#             return any(any(v in a for v in value_strings) for a in actual_strings)
#         if op == "not_contains":
#             # A missing/empty field trivially does not contain the substring.
#             return not any(any(v in a for v in value_strings) for a in actual_strings)
#         # Unknown op — be permissive rather than silently dropping the doc.
#         return True
#
#     # Plain equality / list-membership (positive) match.
#     expected_values = expected if isinstance(expected, list) else [expected]
#     expected_strings = {str(v) for v in expected_values if v is not None}
#
#     if isinstance(actual, list):
#         actual_values = _flatten_values(actual)
#         actual_strings = {str(v) for v in actual_values if v is not None}
#         return bool(actual_strings & expected_strings)
#
#     if isinstance(actual, dict):
#         return str(actual) in expected_strings
#
#     if actual is None:
#         return False
#
#     return str(actual) in expected_strings
