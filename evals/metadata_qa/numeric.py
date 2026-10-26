"""Numeric consistency: do the numbers in the answer TEXT equal the chart's and the data's?

For each question the expected numbers are computed from the collection's
EXTRACTED metadata (fetched from the hosted API). The hosted chat answers;
the numbers the answer states are read out of the prose by an LLM with
structured output (reading prose is a judgement); the chart's own data rows
are read from `data.knowledge.chart_output`. Comparing the three is plain
numeric equality.

  DI_API_KEY_FILE=... OPENAI_KEY_FILE=... python numeric.py --out numeric.json [--only R1 I2]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from openai import OpenAI
from pydantic import BaseModel, Field

from samples import regroup, sample_ids

BASE = "https://app.futuresmart.ai/api/knowledge"


def _count_by(docs, key, pred=lambda m: True):
    out = collections.Counter()
    for m in docs:
        if pred(m):
            out[str(m.get(key))] += 1
    return dict(out)


QUESTIONS: List[Dict[str, Any]] = [
    {"id": "R1", "sample": "resumes", "kind": "grouped count",
     "question": "What is the distribution of years of experience across candidates? Show a chart.",
     "expected": lambda D: _count_by(D, "years_of_experience")},
    {"id": "R2", "sample": "resumes", "kind": "grouped count",
     "question": "How many candidates are there at each education level? Show a chart.",
     "expected": lambda D: _count_by(D, "education_level")},
    {"id": "R3", "sample": "resumes", "kind": "filtered grouped count",
     "question": "Among candidates with at least 5 years of experience, how many are at each seniority level?",
     "expected": lambda D: _count_by(D, "seniority_level", lambda m: (m.get("years_of_experience") or 0) >= 5)},
    {"id": "R4", "sample": "resumes", "kind": "count",
     "question": "How many candidates list Python as a skill?",
     "expected": lambda D: {"total": sum(1 for m in D if "python" in [s.lower() for s in m.get("skills") or []])}},
    {"id": "I1", "sample": "invoices", "kind": "grouped count",
     "question": "How many invoices does each vendor have? Show a chart.",
     "expected": lambda D: _count_by(D, "vendor_name")},
    {"id": "I2", "sample": "invoices", "kind": "filtered grouped sum",
     "question": "What is the total billed by each US-dollar vendor? Show it as a chart.",
     "expected": lambda D: {v: round(sum(m["total_amount"] for m in D if m.get("currency") == "USD" and m.get("vendor_name") == v), 2)
                            for v in {m.get("vendor_name") for m in D if m.get("currency") == "USD"}}},
    {"id": "I3", "sample": "invoices", "kind": "count",
     "question": "How many invoices are billed in euros?",
     "expected": lambda D: {"total": sum(1 for m in D if m.get("currency") == "EUR")}},
]


class StatedNumber(BaseModel):
    group: str = Field(description="The group / category / label the number is stated for, as written; 'total' for a single overall number.")
    value: float


class StatedNumbers(BaseModel):
    numbers: List[StatedNumber] = Field(description="Every count, sum or total the answer states for a group or overall. Exclude percentages, years used as labels, invoice numbers and dates.")


class Pair(BaseModel):
    expected: str
    stated: Optional[str] = Field(description="The stated group naming the same group, or null.")


class Matching(BaseModel):
    pairs: List[Pair] = Field(description="One entry per expected key.")


def _llm_parse(client: OpenAI, system: str, payload: Dict[str, Any], schema):
    r = client.responses.parse(model="gpt-5.4-mini", input=[{"role": "system", "content": system},
                               {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], text_format=schema)
    return r.output_parsed


def stated_numbers(client: OpenAI, question: str, answer: str) -> List[StatedNumber]:
    return _llm_parse(client, "Read the numbers this answer states. Copy each value exactly as stated.",
                      {"question": question, "answer": answer}, StatedNumbers).numbers


def match_groups(client: OpenAI, expected_keys: List[str], stated_groups: List[str]) -> Dict[str, Optional[str]]:
    """Which stated label means which expected group ('Masters' ~ 'masters', '5 years' ~ '5')."""
    if not stated_groups:
        return {k: None for k in expected_keys}
    m = _llm_parse(client, "Match each expected group key to the stated group label that names the same group. "
                           "Use null when none does. Never match two different groups.",
                   {"expected_keys": expected_keys, "stated_groups": stated_groups}, Matching)
    return {p.expected: p.stated for p in m.pairs if p.expected in expected_keys}


def chart_numbers(chart_output: Any) -> Dict[str, float]:
    """{category: value} from the Chart API's chart_data.data rows."""
    rows = (((chart_output or {}).get("chart_data") or {}).get("data")) or []
    out: Dict[str, float] = {}
    for r in rows:
        if isinstance(r, dict) and "category" in r and isinstance(r.get("value"), (int, float)):
            cat = r["category"]
            out[str(int(cat)) if isinstance(cat, float) and cat.is_integer() else str(cat)] = float(r["value"])
    return out


def _bucket_total(label: str, expected: Dict[str, float]) -> Optional[float]:
    """Expected value of a chart bucket. Bucket labels are the chart's own
    format (chart_generator._bucket_label): '3-5' = [3, 5], '10+' and '<3'."""
    def num(x):
        try:
            return float(x)
        except ValueError:
            return None
    lo = hi = None
    if label.endswith("+") and num(label[:-1]) is not None:
        lo = num(label[:-1])
    elif label.startswith("<") and num(label[1:]) is not None:
        hi = num(label[1:]) - 1e-9
    elif "-" in label and all(num(p) is not None for p in label.split("-", 1)):
        lo, hi = (num(p) for p in label.split("-", 1))
    else:
        return None
    keys = [(num(k), v) for k, v in expected.items() if num(k) is not None]
    return sum(v for k, v in keys if (lo is None or k >= lo) and (hi is None or k <= hi))


def chart_vs_data(expected: Dict[str, float], chart: Dict[str, float]) -> Dict[str, Any]:
    want = {}
    for cat in chart:
        want[cat] = expected.get(cat) if cat in expected else _bucket_total(cat, expected)
    bad = {c: {"expected": want[c], "chart": v} for c, v in chart.items()
           if want[c] is None or abs(want[c] - v) > 0.01}
    # every expected group must be in the chart, exactly or inside a bucket
    covered = sum(chart.values())
    return {"match": not bad and abs(covered - sum(expected.values())) <= 0.01, "wrong": bad,
            "chart_total": covered, "expected_total": sum(expected.values())}


def compare(expected: Dict[str, float], stated: Dict[str, float]) -> Dict[str, Any]:
    wrong = {k: {"expected": v, "stated": stated.get(k)} for k, v in expected.items()
             if stated.get(k) is None or abs(float(stated[k]) - float(v)) > 0.01}
    return {"match": not wrong, "wrong": wrong}


def ask(c: httpx.Client, kid: str, owner: str, question: str) -> dict:
    r = c.post(f"/v1/{kid}/chat", json={"query": question, "owner_id": owner, "stream": True})
    events = []
    for line in r.text.splitlines():
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass
    return next((e for e in reversed(events) if isinstance(e, dict) and e.get("session_id")), {"error": r.text[:300]})


def extracted(c: httpx.Client, kid: str) -> List[Dict[str, Any]]:
    body = c.get(f"/v2/{kid}/documents", params={"page": 1, "page_size": 100}).json()
    out = []
    for d in body.get("document") or body.get("documents") or []:
        m = c.get(f"/v1/{kid}/documents/{d['object_id']}/metadata").json().get("metadata")
        if m:
            out.append(m)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--owner", default=os.environ.get("DI_OWNER_ID"), required="DI_OWNER_ID" not in os.environ,
                    help="owner_id for the collection/chat calls (or set DI_OWNER_ID)")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = Path(os.environ["DI_API_KEY_FILE"]).read_text().strip()
    llm = OpenAI(api_key=Path(os.environ["OPENAI_KEY_FILE"]).read_text().strip())
    results: Dict[str, Any] = {}
    with httpx.Client(base_url=BASE, headers={"x-api-key": key}, timeout=httpx.Timeout(400.0, connect=30.0)) as c:
        ids = sample_ids(c)  # the live samples, whichever collections they are now
        for q in QUESTIONS:
            q["kid"] = ids[q["sample"]]
        data = {kid: extracted(c, kid) for kid in {q["kid"] for q in QUESTIONS}}
        for q in QUESTIONS:
            if a.only and q["id"] not in a.only:
                continue
            per_value = {str(k): float(v) for k, v in q["expected"](data[q["kid"]]).items()}
            for run in range(a.repeat):
                t0 = time.time()
                final = ask(c, q["kid"], a.owner, q["question"])
                answer = final.get("response") or ""
                chart = ((final.get("data") or {}).get("knowledge") or {}).get("chart_output")
                cnums = chart_numbers(chart)
                # a numeric field may be charted per value or in buckets (the chart spec's choice):
                # the data is compared on whichever groups the chart drew
                exp = regroup(per_value, list(cnums)) if cnums else per_value
                stated_list = stated_numbers(llm, q["question"], answer)
                stated_groups = [s.group for s in stated_list]
                mapping = match_groups(llm, list(exp), stated_groups)
                by_group = {s.group: s.value for s in stated_list}
                stated = {k: by_group.get(g) for k, g in mapping.items() if g is not None}
                # the text compared to the chart on the CHART's own groups
                tmap = match_groups(llm, list(cnums), stated_groups) if cnums else {}
                text_on_chart_groups = {c: by_group.get(g) for c, g in tmap.items() if g is not None}
                text_vs_data = compare(exp, stated)
                row = {"id": q["id"], "kind": q["kind"], "question": q["question"], "run": run + 1,
                       "seconds": round(time.time() - t0, 1), "expected": exp, "answer": answer,
                       "text_numbers": stated, "chart_numbers": cnums or None,
                       "text_vs_data": text_vs_data,
                       "chart_vs_data": chart_vs_data(exp, cnums) if cnums else None,
                       "text_vs_chart": (compare(cnums, text_on_chart_groups) if cnums else None),
                       "chart_present": bool(chart),
                       "population": [{k: (t.get("result") or {}).get(k) for k in ("question_shape", "applied_filter", "total_match_count")}
                                      for t in (((final.get("data") or {}).get("knowledge") or {}).get("rag_context") or {}).get("tool_responses") or []
                                      if t.get("tool_name") == "retrieve_documents"],
                       "aggregation": ((((final.get("data") or {}).get("knowledge") or {}).get("rag_context") or {}).get("aggregation") or {}).get("rows")}
                results[f"{q['id']}#{run + 1}"] = row
                print(f"{q['id']}#{run + 1} text={'OK' if text_vs_data['match'] else 'WRONG ' + json.dumps(text_vs_data['wrong'])[:160]} "
                      f"chart={'none' if not cnums else ('OK' if row['chart_vs_data']['match'] else 'WRONG')} "
                      f"text_vs_chart={'n/a' if not cnums else ('AGREE' if row['text_vs_chart']['match'] else 'DISAGREE')}", flush=True)
                Path(a.out).write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
