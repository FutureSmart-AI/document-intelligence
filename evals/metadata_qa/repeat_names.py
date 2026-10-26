"""Ask one set question N times on a hosted collection; report each run's
named set against the expected set computed from the extracted metadata.

The names an answer lists are read by an LLM with structured output; the
comparison with the expected set is set equality. Also records how the
population was retrieved (shape, filter, count) for every run.

  DI_API_KEY_FILE=... OPENAI_KEY_FILE=... python repeat_names.py --runs 6 --out runs.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

import httpx
from openai import OpenAI
from pydantic import BaseModel

BASE = "https://app.futuresmart.ai/api/knowledge"
KID = "7c37eb7d-7f1f-448c-9322-1a24203bd18f"
QUESTION = "Which candidates are based in or open to Bengaluru?"


class Names(BaseModel):
    names: List[str]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kid", default=KID)
    ap.add_argument("--question", default=QUESTION)
    ap.add_argument("--field", default="locations")
    ap.add_argument("--value", default="bengaluru")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = Path(os.environ["DI_API_KEY_FILE"]).read_text().strip()
    llm = OpenAI(api_key=Path(os.environ["OPENAI_KEY_FILE"]).read_text().strip())
    out = []
    with httpx.Client(base_url=BASE, headers={"x-api-key": key}, timeout=400) as c:
        docs = (c.get(f"/v2/{a.kid}/documents", params={"page": 1, "page_size": 100}).json().get("document") or [])
        expected = set()
        for d in docs:
            m = c.get(f"/v1/{a.kid}/documents/{d['object_id']}/metadata").json().get("metadata") or {}
            vals = m.get(a.field) or []
            if a.value.lower() in [str(v).strip().lower() for v in (vals if isinstance(vals, list) else [vals])]:
                expected.add(m.get("candidate_name"))
        for i in range(a.runs):
            r = c.post(f"/v1/{a.kid}/chat", json={"query": a.question, "owner_id": os.environ["DI_OWNER_ID"], "stream": False})
            body = r.json()
            answer = body.get("response") or ""
            got = llm.responses.parse(model="gpt-5.4-mini", text_format=Names, input=[
                {"role": "system", "content": "List every person the answer names as satisfying the question. Full names as written."},
                {"role": "user", "content": json.dumps({"question": a.question, "answer": answer})}]).output_parsed.names
            rc = ((body.get("data") or {}).get("knowledge") or {}).get("rag_context") or {}
            calls = [{"shape": (t.get("result") or {}).get("question_shape"), "filter": (t.get("result") or {}).get("applied_filter"),
                      "query": (t.get("result") or {}).get("query"), "count": (t.get("result") or {}).get("count"),
                      "complete": (t.get("result") or {}).get("population_complete")}
                     for t in rc.get("tool_responses") or [] if t.get("tool_name") == "retrieve_documents" and isinstance(t.get("result"), dict)]
            missed, extra = sorted(expected - set(got)), sorted(set(got) - expected)
            row = {"run": i + 1, "exact": not missed and not extra, "missed": missed, "extra": extra,
                   "answer": answer, "calls": calls, "chat_id": body.get("chat_id")}
            out.append(row)
            print(f"run {i + 1}: {'EXACT' if row['exact'] else 'WRONG'} missed={missed} extra={extra} calls={json.dumps(calls)}", flush=True)
            Path(a.out).write_text(json.dumps({"expected": sorted(expected), "runs": out}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"exact {sum(r['exact'] for r in out)}/{len(out)}; expected {sorted(expected)}")


if __name__ == "__main__":
    main()
