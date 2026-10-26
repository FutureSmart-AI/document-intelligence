"""Deterministic arithmetic over a complete retrieved population.

`retrieve_documents` with an exhaustive shape (predicate / selection /
aggregate) returns EVERY document that matches the filter. Counting, summing,
averaging, taking a max and ordering that set are arithmetic, not judgement —
so they are computed here, in code, once, and handed to the agent and the
answer node as numbers. Before this the answer model did them in its head over
18-24 metadata blobs and got them wrong on the hosted 24-resume sample
(“11 / 15 candidates know Python” for 18; the most experienced Python user
named as the 13-year one, not the 15-year one).

Nothing here decides what a value MEANS. Values are grouped by their exact
text, compared case-insensitively and with surrounding whitespace removed —
"Bengaluru" and "bengaluru" are the same stored value written two ways. Two
different labels for the same thing ("b2b saas" vs "saas") stay different;
merging those is a semantic call this module does not make.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

MAX_VALUE_COUNTS = 50


def _title(doc: Dict[str, Any]) -> str:
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    return str(doc.get("title") or doc.get("file_name") or meta.get("file_name") or doc.get("object_id") or "")


def field_value(doc: Dict[str, Any], field: str) -> Any:
    """A field from the document's metadata; dot paths walk nested dicts."""
    cur: Any = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    for part in field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and math.isnan(v))


def _key(v: Any) -> str:
    return str(v).strip().lower()


def field_stats(documents: List[Dict[str, Any]], field: str) -> Dict[str, Any]:
    values = [(d, field_value(d, field)) for d in documents]
    present = [(d, v) for d, v in values if v is not None and v != "" and v != []]
    out: Dict[str, Any] = {"field": field, "documents": len(documents),
                           "with_value": len(present), "missing": len(documents) - len(present)}
    if present and all(_is_number(v) for _, v in present):
        nums = [v for _, v in present]
        lo = min(nums)
        hi = max(nums)
        out.update(
            type="numeric",
            sum=round(sum(nums), 4),
            avg=round(sum(nums) / len(nums), 4),
            min=lo,
            max=hi,
            min_documents=sorted(_title(d) for d, v in present if v == lo),
            max_documents=sorted(_title(d) for d, v in present if v == hi),
        )
        return out
    counts: Dict[str, int] = {}
    shown: Dict[str, str] = {}
    for _, v in present:
        items = v if isinstance(v, list) else [v]
        seen_here = set()
        for item in items:
            if isinstance(item, (dict, list)):
                continue
            k = _key(item)
            if not k or k in seen_here:
                continue
            seen_here.add(k)  # a document counts once per value
            counts[k] = counts.get(k, 0) + 1
            shown.setdefault(k, str(item).strip())
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out.update(
        type="values",
        distinct_values=len(ranked),
        value_counts=[{"value": shown[k], "documents": c} for k, c in ranked[:MAX_VALUE_COUNTS]],
        value_counts_truncated=len(ranked) > MAX_VALUE_COUNTS,
    )
    return out


def order_documents(
    documents: List[Dict[str, Any]], field: str, direction: str = "desc"
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Sort by one field; documents without a value go last. Returns the
    sorted documents and a compact ranking [{rank, title, object_id, value}].
    Equal values share a rank (1, 2, 2, 4) so a tie is never presented as an
    order."""
    reverse = str(direction).lower() != "asc"
    with_value = [d for d in documents if field_value(d, field) is not None]
    without = [d for d in documents if field_value(d, field) is None]

    def sort_key(d):
        v = field_value(d, field)
        return (0, v) if _is_number(v) else (1, _key(v))

    with_value.sort(key=lambda d: _title(d))  # stable tie order
    with_value.sort(key=sort_key, reverse=reverse)
    ordered = with_value + without
    ranking: List[Dict[str, Any]] = []
    prev: Any = object()
    rank = 0
    for i, d in enumerate(ordered, start=1):
        v = field_value(d, field)
        if v != prev:
            rank = i
            prev = v
        ranking.append({"rank": rank if v is not None else None, "title": _title(d),
                        "object_id": d.get("object_id"), "value": v})
    return ordered, ranking


def compute_population(
    documents: List[Dict[str, Any]],
    *,
    stats_fields: Optional[List[str]] = None,
    order_by: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Apply `order_by` and compute `stats_fields` over `documents`.
    Returns (documents, extra_artifact_keys)."""
    extra: Dict[str, Any] = {}
    if stats_fields:
        extra["stats"] = [field_stats(documents, f) for f in dict.fromkeys(stats_fields) if f]
    if order_by and order_by.get("field"):
        documents, ranking = order_documents(documents, order_by["field"], order_by.get("direction", "desc"))
        extra["order_by"] = {"field": order_by["field"], "direction": order_by.get("direction", "desc")}
        extra["ranking"] = ranking
    return documents, extra


def render_population(extra: Dict[str, Any], total: Optional[int], complete: bool, max_rank_rows: int = 50) -> str:
    """Markdown block for the tool output and the answer node."""
    if not extra.get("stats") and not extra.get("ranking"):
        return ""
    scope = (f"all {total} matching documents" if complete and total is not None
             else "the documents returned (NOT the complete population — do not present these as totals)")
    lines = [f"**Computed over {scope}:**"]
    for s in extra.get("stats") or []:
        head = f"- `{s['field']}` ({s['with_value']} with a value, {s['missing']} missing)"
        if s.get("type") == "numeric":
            lines.append(f"{head}: sum={s['sum']}, avg={round(s['avg'], 2)}, min={s['min']} "
                         f"({', '.join(s['min_documents'])}), max={s['max']} ({', '.join(s['max_documents'])})")
        else:
            vc = ", ".join(f"{x['value']}={x['documents']}" for x in s.get("value_counts") or [])
            more = " (+more values)" if s.get("value_counts_truncated") else ""
            lines.append(f"{head}: documents per value — {vc}{more}")
    ranking = extra.get("ranking") or []
    if ranking:
        ob = extra.get("order_by") or {}
        lines.append(f"- ordered by `{ob.get('field')}` {ob.get('direction', 'desc')}:")
        for r in ranking[:max_rank_rows]:
            rank = r["rank"] if r["rank"] is not None else "-"
            lines.append(f"  {rank}. {r['title']} — {r['value']}")
        if len(ranking) > max_rank_rows:
            lines.append(f"  … (+{len(ranking) - max_rank_rows} more)")
    return "\n".join(lines)
