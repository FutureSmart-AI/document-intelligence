"""Latency, tokens, cost and served models for one answer or one extracted document.

Preferred source is the API itself:
  chat      response `usage` (Contract 1: latency_ms, estimated_cost_usd, cost_complete,
            operations[{operation, provider, model, requested_model, fallback, ...}])
  document  `processing_telemetry` on GET /v1/{kid}/documents/{id} (Contract 2)
When the running API does not return those yet, the record falls back — and says so in
`source` — to what the product already exposes:
  chat      the turn's own LangSmith trace (project document-intelligence-model-eval-server,
            found by the chat_id the API returns), every LLM run's model and token counts
  document  the enrichment-health attempt rows (GET /v1/{kid}/health/enrichment/documents):
            served model, fallback flag, tokens, duration
Fallback costs are tokens x FALLBACK_PRICES (dated list prices); embeddings are not visible on
that path, so a fallback document cost is never `cost_complete`.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional

# USD per 1M tokens: input, cached input, output. developers.openai.com/api/docs/pricing,
# standard tier, read 2026-10-26. Used ONLY when the API returns no estimate of its own.
FALLBACK_PRICE_TABLE = "openai-standard-2026-10-26 (evals/model_eval fallback)"
FALLBACK_PRICES = {
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}


def bare_model(name: Optional[str]) -> Optional[str]:
    """'openai/gpt-5.4-nano' -> 'gpt-5.4-nano'; dated snapshots keep their family prefix match."""
    if not name:
        return None
    return str(name).split("/", 1)[-1].strip()


def price(model: Optional[str], input_tokens: int, output_tokens: int, cached: int = 0) -> Optional[float]:
    m = bare_model(model)
    rates = FALLBACK_PRICES.get(m) or next((v for k, v in FALLBACK_PRICES.items() if m and m.startswith(k + "-")), None)
    if rates is None:
        return None
    fresh = max(0, int(input_tokens or 0) - int(cached or 0))
    return (fresh * rates[0] + int(cached or 0) * rates[1] + int(output_tokens or 0) * rates[2]) / 1e6


# ── chat ─────────────────────────────────────────────────────────────────────

def from_chat_usage(usage: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Contract 1 `usage`, or None when the API predates it (no `operations`)."""
    ops = (usage or {}).get("operations")
    if not ops:
        return None
    return {
        "source": "api",
        "server_latency_ms": usage.get("latency_ms"),
        "input_tokens": usage.get("input_tokens"), "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "estimated_cost_usd": usage.get("estimated_cost_usd"),
        "cost_complete": bool(usage.get("cost_complete")),
        "unpriced_models": usage.get("unpriced_models") or [],
        "price_table": usage.get("price_table"),
        "operations": [{k: o.get(k) for k in ("operation", "provider", "model", "requested_model", "fallback", "calls",
                                              "input_tokens", "cached_input_tokens", "output_tokens", "latency_ms",
                                              "estimated_cost_usd")} for o in ops],
    }


def from_llm_runs(runs: List[Dict[str, Any]], service_calls: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Aggregate a turn's LangSmith LLM runs into the same shape (source 'langsmith')."""
    ops: Dict[tuple, Dict[str, Any]] = {}
    unpriced = set()
    total_cost = 0.0
    for r in runs:
        model = bare_model(r.get("model"))
        key = (r.get("name") or "llm", model)
        op = ops.setdefault(key, {"operation": r.get("name") or "llm", "provider": r.get("provider"), "model": model,
                                  "requested_model": None, "fallback": None, "calls": 0, "input_tokens": 0,
                                  "cached_input_tokens": 0, "output_tokens": 0, "latency_ms": 0, "estimated_cost_usd": 0.0})
        op["calls"] += 1
        for k in ("input_tokens", "cached_input_tokens", "output_tokens", "latency_ms"):
            op[k] += int(r.get(k) or 0)
        cost = price(model, r.get("input_tokens") or 0, r.get("output_tokens") or 0, r.get("cached_input_tokens") or 0)
        if cost is None:
            unpriced.add(model)
            op["estimated_cost_usd"] = None
        elif op["estimated_cost_usd"] is not None:
            op["estimated_cost_usd"] += cost
            total_cost += cost
    if not runs:
        # No trace visible: models from the API's own service_calls, no cost claim.
        for c in service_calls:
            model = bare_model(c.get("model"))
            if model:
                ops.setdefault((c.get("service"), model), {"operation": c.get("service"), "provider": c.get("provider"),
                                                           "model": model, "calls": 1})
    return {
        "source": "langsmith" if runs else "service_calls",
        "server_latency_ms": None,
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in runs),
        "cached_input_tokens": sum(int(r.get("cached_input_tokens") or 0) for r in runs),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in runs),
        "estimated_cost_usd": round(total_cost, 8) if runs else None,
        "cost_complete": bool(runs) and not unpriced,
        "unpriced_models": sorted(m for m in unpriced if m),
        "price_table": FALLBACK_PRICE_TABLE if runs else None,
        "operations": list(ops.values()),
    }


class LangSmithTraces:
    """Reads the server's own traces of one turn (raw REST, like evals/metadata_qa/run_hosted.py)."""

    def __init__(self, api_key: Optional[str], project: str, base: str = "https://api.smith.langchain.com"):
        self.enabled = bool(api_key)
        self.project = project
        self.session_id = None
        if not self.enabled:
            return
        import httpx
        self.c = httpx.Client(base_url=base, headers={"x-api-key": api_key}, timeout=60)

    def _request(self, method: str, path: str, **kw):
        """One LangSmith call with backoff (3 tries; read timeout 60 s, then 120 s, then 120 s).
        Raises after the last try; turn() turns that into a recorded lookup error."""
        import httpx
        last = None
        for attempt, timeout in enumerate((60, 120, 120)):
            try:
                return self.c.request(method, path, timeout=timeout, **kw)
            except httpx.HTTPError as e:  # timeouts and connection errors
                last = e
                time.sleep(5 * (attempt + 1))
        raise last

    def _session(self) -> Optional[str]:
        if self.session_id is None and self.enabled:
            r = self._request("GET", "/api/v1/sessions", params={"name": self.project})
            rows = r.json() if r.status_code < 400 else []
            self.session_id = rows[0]["id"] if rows else None
        return self.session_id

    def turn(self, chat_id: str, attempts: int = 12, wait_s: float = 5.0) -> Dict[str, Any]:
        """{"trace_id", "runs": [llm runs], "error"} for the turn whose root run carries metadata chat_id.
        Never raises: a telemetry lookup must not drop the answer it describes."""
        if not self.enabled or not chat_id:
            return {"trace_id": None, "runs": [], "error": None if self.enabled else "no LangSmith key"}
        try:
            return self._turn(chat_id, attempts, wait_s)
        except Exception as e:
            return {"trace_id": None, "runs": [], "error": f"LangSmith lookup failed: {type(e).__name__}: {str(e)[:200]}"}

    def _turn(self, chat_id: str, attempts: int, wait_s: float) -> Dict[str, Any]:
        flt = f'and(eq(metadata_key, "chat_id"), eq(metadata_value, "{chat_id}"))'
        root = None
        for _ in range(attempts):  # ingestion lags the response by a few seconds
            session = self._session()
            if session:
                r = self._request("POST", "/api/v1/runs/query", json={"session": [session], "filter": flt, "is_root": True,
                                                             "limit": 1, "select": ["id", "trace_id"]})
                rows = r.json().get("runs") if r.status_code < 400 else None
                if rows:
                    root = rows[0]
                    break
            time.sleep(wait_s)
        if not root:
            return {"trace_id": None, "runs": [], "error": "no root run found for this chat_id"}
        runs: List[Dict[str, Any]] = []
        seen = -1
        for _ in range(18):  # child runs land after the root; wait until the count stops growing
            r = self._request("POST", "/api/v1/runs/query", json={"session": [self.session_id], "trace": root["trace_id"],
                                                         "run_type": "llm", "limit": 100,
                                                         "select": ["name", "extra", "prompt_tokens", "completion_tokens",
                                                                    "start_time", "end_time", "parent_run_ids",
                                                                    "prompt_token_details"]})
            runs = r.json().get("runs") or [] if r.status_code < 400 else []
            if runs and len(runs) == seen:
                break
            seen = len(runs)
            time.sleep(wait_s)
        return {"trace_id": root["trace_id"], "runs": [llm_run(x) for x in runs], "error": None}


def _ms(start: Optional[str], end: Optional[str]) -> int:
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return int((b - a).total_seconds() * 1000)
    except Exception:
        return 0


def llm_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """One LangSmith LLM run -> {name, provider, model, tokens, latency}."""
    extra = run.get("extra") or {}
    meta = extra.get("metadata") or {}
    params = extra.get("invocation_params") or {}
    model = meta.get("ls_model_name") or params.get("model") or params.get("model_name")
    cached = (run.get("prompt_token_details") or {}).get("cache_read") or 0
    return {"name": run.get("name"), "provider": meta.get("ls_provider"), "model": model,
            "input_tokens": run.get("prompt_tokens") or 0, "cached_input_tokens": cached,
            "output_tokens": run.get("completion_tokens") or 0,
            "latency_ms": _ms(run.get("start_time"), run.get("end_time"))}


# ── documents ────────────────────────────────────────────────────────────────

def from_processing_telemetry(t: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not t or not t.get("operations"):
        return None
    ext = [o for o in t["operations"] if o.get("operation") == "extraction"]
    return {
        "source": "api",
        "total_ms": t.get("total_ms"),
        "parse": t.get("parse"),
        "extraction_models": sorted({bare_model(o.get("model")) for o in ext if o.get("model")}),
        # The registry names that ran: a variant ("gpt-5.6-luna-low") is recorded as its wire
        # model plus configured_model and reasoning_effort, and the config names the variant.
        "extraction_configs": sorted({bare_model(o.get("configured_model") or o.get("model"))
                                      for o in ext if o.get("model")}),
        "reasoning_efforts": sorted({str(o["reasoning_effort"]) for o in ext if o.get("reasoning_effort")}),
        "fallback": any(o.get("fallback") for o in ext),
        "fallback_reason": next((o.get("fallback_reason") for o in ext if o.get("fallback")), None),
        "extraction_ms": sum(int(o.get("latency_ms") or 0) for o in ext) or None,
        "input_tokens": sum(int(o.get("input_tokens") or 0) for o in ext),
        "output_tokens": sum(int(o.get("output_tokens") or 0) for o in ext),
        "estimated_cost_usd": t.get("estimated_cost_usd"),
        "cost_complete": bool(t.get("cost_complete")),
        "unpriced_models": t.get("unpriced_models") or [],
        "price_table": t.get("price_table"),
        "operations": t["operations"],
    }


def from_enrichment_health(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The document's enrichment attempts (newest last). The successful attempt is the one that
    produced the metadata; earlier failed attempts still cost time and tokens."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: r.get("created_at") or "")
    final = next((r for r in reversed(rows) if r.get("status") in ("success", "succeeded", "completed", "partial")), rows[-1])
    cost = 0.0
    unpriced = set()
    for r in rows:
        c = r.get("cost_usd")
        if c is None:
            c = price(r.get("model"), r.get("input_tokens") or 0, r.get("output_tokens") or 0)
        if c is None:
            unpriced.add(bare_model(r.get("model")))
        else:
            cost += c
    return {
        "source": "enrichment_health",
        "total_ms": None,
        "parse": {"mode": final.get("document_processing_mode")},
        "extraction_models": sorted({bare_model(r.get("model")) for r in rows if r.get("model")}),
        # A health row carries no configured_model: a registry variant shows as the wire model
        # with the variant as requested_model and no fallback.
        "extraction_configs": sorted({bare_model(r.get("requested_model") if (
            not r.get("used_fallback") and r.get("requested_model")) else r.get("model"))
            for r in rows if r.get("model")}),
        "served_model": bare_model(final.get("model")),
        "requested_model": bare_model(final.get("requested_model")),
        "fallback": any(r.get("used_fallback") for r in rows),
        "fallback_reason": next((r.get("fallback_reason") for r in rows if r.get("used_fallback")), None),
        "attempts": len(rows),
        "extraction_ms": sum(int(r.get("duration_ms") or 0) for r in rows),
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "estimated_cost_usd": round(cost, 8),
        # Embeddings and parsing are not on this path, so this is never the complete document cost.
        "cost_complete": False,
        "cost_incomplete_reason": "enrichment-health rows carry extraction tokens only (no embeddings)",
        "unpriced_models": sorted(m for m in unpriced if m),
        "price_table": FALLBACK_PRICE_TABLE,
    }


# ── config verification ──────────────────────────────────────────────────────

def chat_config_verified(telemetry: Dict[str, Any], models: Dict[str, Any]) -> Dict[str, Any]:
    """Served models must belong to the configuration; the answer and retrieval-agent operations,
    when the API names them, must be the configured ones; no operation may report a fallback."""
    served = {bare_model(o.get("model")) for o in telemetry.get("operations") or [] if o.get("model")}
    outside = sorted(m for m in served if m not in models["any"])
    problems = []
    if not served:
        problems.append("no served model visible (no usage.operations and no trace)")
    if outside:
        problems.append(f"served models outside the config: {outside}")
    for op in telemetry.get("operations") or []:
        name = op.get("operation")
        want = models["by_operation"].get(name)
        if want and bare_model(op.get("model")) != want:
            problems.append(f"{name} served {op.get('model')} (config {want})")
        if op.get("fallback"):
            problems.append(f"{name} fell back to {op.get('model')}")
    if telemetry.get("source") != "api" and served and models["answer"] not in served:
        problems.append(f"answer model {models['answer']} not among served models {sorted(served)}")
    return {"verified": not problems, "served": sorted(served), "problems": problems}


def extraction_config_verified(telemetry: Optional[Dict[str, Any]], expected_model: str) -> Dict[str, Any]:
    if not telemetry:
        return {"verified": False, "served": [], "problems": ["no extraction telemetry for this document"]}
    served = telemetry.get("extraction_configs") or telemetry.get("extraction_models") or []
    problems = []
    if telemetry.get("fallback"):
        problems.append(f"fell back: {telemetry.get('fallback_reason')}")
    if served != [bare_model(expected_model)]:
        problems.append(f"served {served}, config {expected_model}")
    return {"verified": not problems, "served": served, "problems": problems}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
