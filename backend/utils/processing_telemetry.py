"""Price a document's processing telemetry on read (Contract 2).

The worker records what it measured while processing a document — parse step,
extraction call(s) with the model that actually served them, embeddings, total
time — on ``document.processing_telemetry`` (migration 056), deliberately
WITHOUT prices: the worker image cannot import this service's price table, and
two price tables would drift. This module turns that raw record into the
``processing_telemetry`` field the document endpoints return, priced with
``chat/pricing.py`` — the same table the chat ``usage`` block uses.

A model with no price (e.g. ``google/gemini-3.1-flash-lite``) prices to ``None``
and makes ``cost_complete`` false; it is never counted as ``$0``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from chat.pricing import PRICE_TABLE_VERSION, estimate_cost

_INT_FIELDS = ("calls", "failed_calls", "input_tokens", "cached_input_tokens",
               "output_tokens", "latency_ms")


def price_processing_telemetry(raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The API view of one document's processing telemetry, or None when the
    document has none (processed before it was recorded, or still queued)."""
    if not isinstance(raw, dict) or not raw:
        return None
    operations: List[Dict[str, Any]] = []
    total = 0.0
    priced_any = False
    unpriced: List[str] = []
    for op in raw.get("operations") or []:
        if not isinstance(op, dict):
            continue
        row = dict(op)
        for f in _INT_FIELDS:
            if f in row and row[f] is not None:
                row[f] = int(row[f])
        row.setdefault("requested_model", row.get("model"))
        row["fallback"] = bool(row.get("fallback"))
        inp = int(row.get("input_tokens") or 0)
        out = int(row.get("output_tokens") or 0)
        cost = None
        if inp or out:
            cost = estimate_cost(row.get("provider"), row.get("model"), inp, out,
                                 int(row.get("cached_input_tokens") or 0), 0)
            if cost is None:
                label = f"{row.get('provider')}/{row.get('model')}"
                if label not in unpriced:
                    unpriced.append(label)
            else:
                total += cost
                priced_any = True
        row["estimated_cost_usd"] = cost
        operations.append(row)
    parse = raw.get("parse") if isinstance(raw.get("parse"), dict) else None
    return {
        "total_ms": int(raw["total_ms"]) if raw.get("total_ms") is not None else None,
        "parse": parse,
        "operations": operations,
        # None when nothing could be priced -- an all-unpriced document must not
        # read as free. Otherwise the priced part (a lower bound if incomplete).
        "estimated_cost_usd": round(total, 9) if priced_any else None,
        "cost_complete": not unpriced,
        "unpriced_models": unpriced,
        "price_table": PRICE_TABLE_VERSION,
        **({"recorded_at": raw["recorded_at"]} if raw.get("recorded_at") else {}),
        **({"status": raw["status"]} if raw.get("status") else {}),
    }
