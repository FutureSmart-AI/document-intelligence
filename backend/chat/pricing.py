"""
Best-effort USD cost estimation per query, from token counts.

Ported verbatim from fs-agent-chat's chat_utils/pricing.py (see
docs/plan/analytics-and-health/events.md) — this module has no agent_id/
knowledge_id dependency, so nothing needed re-keying. Filling this in also
fixes a live gap: chat/utils/service_calls.py already imports
``chat.pricing.estimate_cost`` inside a try/except, but the module never
existed, so every service_calls cost_usd has been silently None.

Prices are USD per 1,000 tokens, keyed by ``(provider, normalized_model)``.
Update as vendor pricing changes — ideally promote this table to config/DB.

Returns ``None`` for unknown models so callers can show "cost unavailable"
instead of a misleading ``$0.00`` (silent zeros corrupt cost trends).

Prompt caching
--------------
Providers bill prompt tokens served from a prefix cache at a discount, and some
(Anthropic, the ``gpt-5.6`` family) charge a one-time surcharge to *create* the
cache entry. Each model therefore carries four rates (see :class:`Price`):

    input        — fresh (uncached) prompt tokens
    cached_input — prompt tokens served from the cache (cache READ), discounted
    cache_write  — surcharge to CREATE a cache entry; ``None`` where the provider
                   does not bill cache creation separately (OpenAI 4.x, Google, …)
    output       — generated tokens

TOKEN CONTRACT: ``cached_input_tokens`` and ``cache_write_tokens`` are **subsets**
of ``input_tokens`` (this mirrors chat/utils/usage.py's normalized usage dict,
where ``input_tokens`` is the total and the cache split is reported alongside
it). :func:`estimate_cost` derives the fresh-token count as
``input − cached − write`` so total token accounting is unchanged. A rate of
``None`` falls back to the plain ``input`` rate, so an unknown cache tier is
priced conservatively (never under-counted to ``0``).
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Tuple


class Price(NamedTuple):
    """USD per 1,000 tokens for one model. ``cached_input``/``cache_write`` may be
    ``None`` when the provider publishes no separate cache tier (falls back to
    ``input`` at pricing time)."""
    input: float
    cached_input: Optional[float]
    cache_write: Optional[float]
    output: float


#: Version stamp every priced number travels with (``usage.price_table``,
#: ``processing_telemetry.price_table``). Bump it whenever a rate below changes,
#: so a stored estimate can always be traced to the table that produced it.
#: The OpenAI rows marked "verified" were read off
#: developers.openai.com/api/docs/pricing (standard tier) on 2026-10-26.
PRICE_TABLE_VERSION = "openai-standard-2026-10-26"

# Keys lowercase ``(provider, normalized_model)``. Keep in sync with vendors.
PRICE_PER_1K = {
    # ── OpenAI GPT-4.x — cache read discounted, no separate cache-write charge ──
    ("openai", "gpt-4o-mini"): Price(0.00015, 0.000075, None, 0.00060),
    ("openai", "gpt-4o"): Price(0.00250, 0.00125, None, 0.01000),
    ("openai", "gpt-4.1-mini"): Price(0.00040, 0.00010, None, 0.00160),
    ("openai", "gpt-4.1"): Price(0.00200, 0.00050, None, 0.00800),
    ("openai", "gpt-4.1-nano"): Price(0.00010, 0.000025, None, 0.00040),
    # ── OpenAI GPT-5.x (per vendor pricing table) ──
    # gpt-5.6 family bills cache WRITES separately (Anthropic-style prompt caching).
    ("openai", "gpt-5.6-sol"): Price(0.00500, 0.00050, 0.00625, 0.03000),
    # verified 2026-10-26: terra $2.00 / $0.20 cached / $2.50 cache write / $12.00 out per 1M
    ("openai", "gpt-5.6-terra"): Price(0.00200, 0.00020, 0.00250, 0.01200),
    # verified 2026-10-26: luna $0.20 / $0.02 cached / $0.25 cache write / $1.20 out per 1M
    ("openai", "gpt-5.6-luna"): Price(0.00020, 0.00002, 0.00025, 0.00120),
    # gpt-5.5 / 5.4 — cache read discounted, no separate cache-write charge.
    ("openai", "gpt-5.5"): Price(0.00500, 0.00050, None, 0.03000),
    ("openai", "gpt-5.5-pro"): Price(0.03000, None, None, 0.18000),
    ("openai", "gpt-5.4"): Price(0.00250, 0.00025, None, 0.01500),
    # verified 2026-10-26: mini $0.75 / $0.075 cached / $4.50 out per 1M
    ("openai", "gpt-5.4-mini"): Price(0.00075, 0.000075, None, 0.00450),
    # verified 2026-10-26: nano $0.20 / $0.02 cached / $1.25 out per 1M
    ("openai", "gpt-5.4-nano"): Price(0.00020, 0.00002, None, 0.00125),
    ("openai", "gpt-5.4-pro"): Price(0.03000, None, None, 0.18000),
    # ── OpenAI embeddings — input only (verified 2026-10-26: 3-small $0.02 per 1M) ──
    ("openai", "text-embedding-3-small"): Price(0.00002, None, None, 0.0),
    ("openai", "text-embedding-3-large"): Price(0.00013, None, None, 0.0),
    # ── Anthropic — cache read ≈ 0.1× input, cache write (5m TTL) ≈ 1.25× input ──
    ("anthropic", "claude-3-5-sonnet"): Price(0.00300, 0.00030, 0.00375, 0.01500),
    ("anthropic", "claude-3-5-haiku"): Price(0.00080, 0.00008, 0.00100, 0.00400),
    ("anthropic", "claude-3-opus"): Price(0.01500, 0.00150, 0.01875, 0.07500),
    # ── Google Gemini — implicit/explicit cache read ≈ 0.25× input, no per-token write ──
    ("google", "gemini-2.0-flash"): Price(0.00010, 0.000025, None, 0.00040),
    ("google", "gemini-1.5-pro"): Price(0.00125, 0.0003125, None, 0.00500),
    ("google", "gemini-1.5-flash"): Price(0.000075, 0.00001875, None, 0.00030),
    # ── DeepSeek — cache-hit read ≈ 0.1× input, no separate write charge ──
    ("deepseek", "deepseek-v4-flash"): Price(0.00014, 0.000014, None, 0.00028),
    ("deepseek", "deepseek-v4-pro"): Price(0.000435, 0.0000435, None, 0.00087),
    # ── MiniMax — no published cache tier; cached/write fall back to input rate ──
    ("minimax", "minimax-m3"): Price(0.00030, None, None, 0.00120),
}


def estimate_cost(
    provider: Optional[str],
    model: Optional[str],
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cached_input_tokens: Optional[int] = 0,
    cache_write_tokens: Optional[int] = 0,
) -> Optional[float]:
    """USD cost for a single call/turn, or ``None`` when the model price is unknown.

    ``cached_input_tokens`` and ``cache_write_tokens`` are the cache-read and
    cache-creation portions of ``input_tokens`` (subsets, not additions). Fresh
    input is derived as ``input − cached − write`` and priced at the full rate;
    the cached/write portions use their discounted/surcharge rates (falling back
    to the input rate when the model publishes none)."""
    if not model:
        return None
    # An enrichment registry variant ("gpt-5.6-luna-low") is billed as its wire
    # model; the effort changes the token count, not the rate.
    # Imported here, not at module top: importing the ``utils`` package runs
    # utils/__init__.py, which reaches utils/processing_telemetry.py, which imports
    # this module -- a top-level import is a cycle whenever chat.pricing loads first.
    from utils.enrichment.model_registry import wire_model

    norm = _norm_model(wire_model(model))
    key = ((provider or "").lower().strip(), norm)
    price = PRICE_PER_1K.get(key)
    if price is None:
        # Fall back to a provider-agnostic match on the normalized model name.
        price = next((v for (p, m), v in PRICE_PER_1K.items() if m == norm), None)
    if price is None:
        return None
    cached = max(0, int(cached_input_tokens or 0))
    write = max(0, int(cache_write_tokens or 0))
    # cached/write are subsets of input_tokens; the remainder is billed fresh.
    fresh = max(0, int(input_tokens or 0) - cached - write)
    cached_rate = price.cached_input if price.cached_input is not None else price.input
    write_rate = price.cache_write if price.cache_write is not None else price.input
    cost = (
        fresh / 1000 * price.input
        + cached / 1000 * cached_rate
        + write / 1000 * write_rate
        + (output_tokens or 0) / 1000 * price.output
    )
    # 9 places: an embedding of a short query costs ~1e-7 USD, and rounding that
    # to 0 would print a priced call as free.
    return round(cost, 9)


def price_breakdown(usage_by_model: Optional[list]) -> dict:
    """Price a per-(provider, model) usage breakdown and sum the priced subtotals.

    ``usage_by_model`` is a list of ``{"provider","model","input_tokens",
    "output_tokens"}`` entries (one per distinct model a query touched — see
    chat/graph.py's ``usage_by_model`` accumulator). Each is priced
    independently via :func:`estimate_cost` — never ``total_tokens × one
    model's rate`` (that collapsed-pricing bug is exactly what this function
    exists to avoid; see docs/plan/analytics-and-health/events.md's
    13-multi-model-cost note).

    Returns ``{"cost_breakdown": [...], "cost_usd": float, "cost_complete": bool}``:
    - ``cost_breakdown`` mirrors each input entry plus its ``total_tokens`` and
      per-line ``cost_usd`` (``None`` when that model has no known price).
    - ``cost_usd`` is the sum of the **priced** lines only — a documented LOWER
      BOUND when some models are unpriced (never inflates unknown to ``0``).
    - ``cost_complete`` is ``False`` iff any line is unpriced; callers should
      render a "partial" badge in that case.
    """
    cost_breakdown: list = []
    total = 0.0
    complete = True
    for e in usage_by_model or []:
        inp = int(e.get("input_tokens") or 0)
        out = int(e.get("output_tokens") or 0)
        # Cache-read / cache-creation portions of `inp` (subsets, not additions).
        cached_in = int(e.get("cached_input_tokens") or 0)
        cache_write = int(e.get("cache_write_tokens") or 0)
        # A line carrying its own cost_usd is already priced by an external source
        # (e.g. a sub-service call priced elsewhere). Use it verbatim; never
        # re-price it through the local map.
        pre = e.get("cost_usd") if "cost_usd" in e else None
        c = (
            float(pre) if pre is not None
            else estimate_cost(e.get("provider"), e.get("model"), inp, out, cached_in, cache_write)
        )
        line = {
            "provider": e.get("provider"),
            "model": e.get("model"),
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "cost_usd": c,
        }
        # Surface the cache split only when present, so a consumer can show
        # cache savings without cluttering non-cached lines.
        if cached_in:
            line["cached_input_tokens"] = cached_in
        if cache_write:
            line["cache_write_tokens"] = cache_write
        if e.get("cost_source"):
            line["cost_source"] = e["cost_source"]
        cost_breakdown.append(line)
        if c is None:
            complete = False
        else:
            total += c
    return {
        "cost_breakdown": cost_breakdown,
        "cost_usd": round(total, 6),
        "cost_complete": complete,
    }


def _norm_model(model: str) -> str:
    """Normalize a raw model id to a price-table key."""
    m = (model or "").lower().strip()
    if m.startswith("models/"):
        m = m[7:]
    # Collapse dated/version suffixes (e.g. claude-3-5-sonnet-20241022).
    for base in ("claude-3-5-sonnet", "claude-3-5-haiku", "claude-3-opus"):
        if m.startswith(base):
            return base
    return m


_Tuple = Tuple  # re-export for typing clarity in callers, if needed
