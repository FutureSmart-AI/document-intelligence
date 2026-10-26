"""Ask the metadata QA question set on a hosted collection and record the trace.

For every question this records what the answer was built from, not just the
answer: each RAG tool call with its RAW arguments (question_shape, the
metadata_filter the model wrote, query, object_ids) taken from the turn's
LangSmith trace, the filter the executor actually APPLIED, document counts,
total_match_count / truncation, chunk counts, and the sources returned to the
user. Raw-vs-applied is how a silently dropped condition becomes visible.

Secrets are read from files and never printed:
  DI_API_KEY_FILE   x-api-key for the knowledge service
  LANGSMITH_KEY_FILE  LangSmith API key (optional; without it raw args are absent)

  python run_hosted.py --questions qset.json --knowledge-id <id> --out run.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

LS_BASE = "https://api.smith.langchain.com"


def _read_key(env: str) -> Optional[str]:
    path = os.environ.get(env)
    return Path(path).read_text().strip() if path else None


def parse_sse(text: str) -> dict:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass
    final = next((e for e in reversed(events) if isinstance(e, dict) and e.get("session_id")), None)
    return final or {"error": "no final event", "tail": text[-500:]}


def summarize_tool_response(tr: Dict[str, Any]) -> Dict[str, Any]:
    res = tr.get("result")
    out: Dict[str, Any] = {"tool": tr.get("tool_name"), "tool_call_id": tr.get("tool_call_id")}
    if not isinstance(res, dict):
        out["result_type"] = type(res).__name__
        return out
    for k in ("question_shape", "applied_filter", "retrieval_mode", "query", "count", "total_match_count",
              "truncated", "total_is_lower_bound", "semantic_candidate_count", "error", "warning",
              "filter_warnings", "population_complete"):
        if k in res:
            out[k] = res[k]
    if isinstance(res.get("documents"), list):
        out["documents"] = [d.get("file_name") or (d.get("metadata") or {}).get("file_name") or d.get("object_id")
                            for d in res["documents"] if isinstance(d, dict)]
    chunks = res.get("chunks") or res.get("results")
    if isinstance(chunks, list):
        out["chunk_count"] = len(chunks)
        out["chunk_docs"] = sorted({str(c.get("file_name") or c.get("object_id")) for c in chunks if isinstance(c, dict)})
    if "fields" in res and isinstance(res["fields"], list):
        out["catalog_fields"] = len(res["fields"])
    return out


class LangSmith:
    def __init__(self, key: str, project: str):
        self.c = httpx.Client(base_url=LS_BASE, headers={"x-api-key": key}, timeout=60)
        r = self.c.get("/api/v1/sessions", params={"name": project})
        r.raise_for_status()
        rows = r.json()
        self.session_id = rows[0]["id"] if rows else None

    def tool_calls(self, chat_id: str, attempts: int = 12) -> List[Dict[str, Any]]:
        if not self.session_id:
            return []
        flt = f'and(eq(metadata_key, "chat_id"), eq(metadata_value, "{chat_id}"))'
        root = None
        for _ in range(attempts):  # ingestion lags the response by a few seconds
            r = self.c.post("/api/v1/runs/query", json={"session": [self.session_id], "filter": flt,
                                                         "is_root": True, "limit": 1, "select": ["id", "trace_id"]})
            runs = r.json().get("runs") if r.status_code < 400 else None
            if runs:
                root = runs[0]
                break
            time.sleep(5)
        if not root:
            return []
        r = self.c.post("/api/v1/runs/query", json={"session": [self.session_id], "trace": root["trace_id"],
                                                     "run_type": "tool", "limit": 100,
                                                     "select": ["name", "inputs", "start_time", "error"]})
        runs = sorted(r.json().get("runs") or [], key=lambda x: x.get("start_time") or "")
        out = []
        for run in runs:
            inputs = run.get("inputs") or {}
            args = inputs.get("input") if isinstance(inputs.get("input"), (dict, str)) else inputs
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    pass
            out.append({"tool": run.get("name"), "args": args, "error": run.get("error")})
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--knowledge-id", required=True)
    ap.add_argument("--owner", default=os.environ.get("DI_OWNER_ID"), required="DI_OWNER_ID" not in os.environ,
                    help="owner_id for the collection/chat calls (or set DI_OWNER_ID)")
    ap.add_argument("--base", default="https://app.futuresmart.ai")
    ap.add_argument("--project", default="fs-knowledge-service-prod")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    key = _read_key("DI_API_KEY_FILE")
    if not key:
        sys.exit("set DI_API_KEY_FILE")
    ls_key = _read_key("LANGSMITH_KEY_FILE")
    ls = LangSmith(ls_key, a.project) if ls_key else None

    qs = json.loads(Path(a.questions).read_text(encoding="utf-8"))["questions"]
    out_path = Path(a.out)
    results = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    with httpx.Client(base_url=f"{a.base.rstrip('/')}/api/knowledge", headers={"x-api-key": key},
                      timeout=httpx.Timeout(400.0, connect=30.0)) as c:
        for q in qs:
            if a.only and q["id"] not in a.only:
                continue
            t0 = time.time()
            # A fresh session per question: no history may leak between questions.
            r = c.post(f"/v1/{a.knowledge_id}/chat", json={"query": q["question"], "owner_id": a.owner, "stream": True})
            final = parse_sse(r.text) if r.status_code < 400 else {"error": f"HTTP {r.status_code}: {r.text[:300]}"}
            kn = ((final.get("data") or {}).get("knowledge") or {})
            rag_ctx = kn.get("rag_context") or {}
            sources = ((kn.get("relevant_sources") or {}).get("data")) or []
            row = {
                "id": q["id"], "category": q["category"], "question": q["question"],
                "seconds": round(time.time() - t0, 1),
                "chat_id": final.get("chat_id"), "refined_query": final.get("refined_query"),
                "response": final.get("response"), "error": final.get("error"),
                "debug_info": final.get("debug_info"),
                "sources": [s.get("file_name") or s.get("title") or s.get("object_id") for s in sources if isinstance(s, dict)],
                "tool_responses": [summarize_tool_response(t) for t in rag_ctx.get("tool_responses") or []],
                "retrieval_attempts": rag_ctx.get("retrieval_attempts"),
                "verifier_verdicts": rag_ctx.get("verifier_verdicts"),
                "agent_output": {k: (rag_ctx.get("agent_output") or {}).get(k)
                                 for k in ("stop_reason", "confidence", "granularity", "question_shape")},
                "chart": bool(kn.get("chart_output")),
            }
            if ls and final.get("chat_id"):
                try:
                    row["tool_calls_raw"] = ls.tool_calls(str(final["chat_id"]))
                except Exception as e:  # trace capture must never lose the answer
                    row["tool_calls_raw_error"] = str(e)[:300]
            results[q["id"]] = row
            out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(q["id"], f"{row['seconds']}s", (row["response"] or row["error"] or "")[:100].replace("\n", " "), flush=True)


if __name__ == "__main__":
    main()
