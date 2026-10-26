"""di-charts' hosted chart regression set, run against whichever collections are the live samples.

A chart passes when chart_output.chart_data equals the collection's own extracted metadata,
aggregated on the groups the chart drew (a numeric field may be charted per value or in buckets),
and the answer text does not draw a chart. For resumes the chart is also compared with
ground_truth.json on the same groups (`vs_truth`): since the 2026-10-25 extraction fix
(backend/samples/resumes/EXTRACTION-YEARS-2026-10-25.md) the two differ only where the fixture
lists an accepted alternative (r10 junior/intern, r22 lead/principal/senior).

  DI_API_KEY_FILE=... python chart_regression.py --out charts.json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

import httpx

from samples import RESUME_TRUTH, regroup, sample_ids

BASE = "https://app.futuresmart.ai/api/knowledge"
SKILLS = ["python", "aws", "sql", "docker", "rag"]


def by_field(field):
    return lambda docs: dict(collections.Counter(str(d.get(field)) for d in docs if d.get(field) is not None))


def by_skill(docs):
    return {s: sum(s in [x.lower() for x in d.get("skills") or []] for d in docs) for s in SKILLS}


def usd_totals(docs):
    usd = [d for d in docs if d.get("currency") == "USD"]
    return {v: round(sum(d["total_amount"] for d in usd if d.get("vendor_name") == v), 2) for v in {d.get("vendor_name") for d in usd}}


CASES = [
    ("resumes", "How are candidates distributed by seniority level? Show it as a chart.", by_field("seniority_level")),
    ("resumes", "What is the distribution of years of experience across candidates? Show a chart.", by_field("years_of_experience")),
    ("resumes", "Chart the number of candidates by education level", by_field("education_level")),
    ("resumes", "How many candidates list each of these skills: python, aws, sql, docker, rag? Show a chart.", by_skill),
    ("invoices", "What is the total billed by each US-dollar vendor? Show it as a chart.", usd_totals),
]
DRAWN = ("█", "▇", "▆", "```mermaid", "xychart", "■■")


def extracted(c: httpx.Client, kid: str) -> list[dict]:
    body = c.get(f"/v2/{kid}/documents", params={"page": 1, "page_size": 100}).raise_for_status().json()
    return [c.get(f"/v1/{kid}/documents/{d['object_id']}/metadata").raise_for_status().json().get("metadata") or {}
            for d in body.get("document") or body.get("documents") or []]


def diff(expected: dict, chart: dict) -> tuple[dict, list]:
    norm = {k.lower(): v for k, v in chart.items()}
    wrong = {k: {"expected": v, "chart": norm.get(k.lower())} for k, v in expected.items()
             if norm.get(k.lower()) is None or abs(norm[k.lower()] - v) > 0.01}
    extra = sorted(set(norm) - {k.lower() for k in expected})
    return wrong, extra


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = Path(os.environ["DI_API_KEY_FILE"]).read_text().strip()
    results = []
    with httpx.Client(base_url=BASE, headers={"x-api-key": key}, timeout=400) as c:
        ids = sample_ids(c)
        docs = {s: extracted(c, ids[s]) for s in {s for s, _, _ in CASES}}
        for sample, q, expect in CASES:
            body = c.post(f"/v1/{ids[sample]}/chat", json={"query": q, "owner_id": os.environ["DI_OWNER_ID"], "stream": False}).json()
            co = ((body.get("data") or {}).get("knowledge") or {}).get("chart_output") or {}
            data = {str(r.get("category")).strip(): float(r.get("value")) for r in ((co.get("chart_data") or {}).get("data") or [])}
            expected = regroup({str(k): float(v) for k, v in expect(docs[sample]).items()}, list(data))
            wrong, extra = diff(expected, data)
            vs_truth = None
            if sample == "resumes":
                truth = regroup({str(k): float(v) for k, v in expect(RESUME_TRUTH).items()}, list(data))
                t_wrong, t_extra = diff(truth, data)
                vs_truth = {"exact": not t_wrong and not t_extra, "wrong": t_wrong, "extra": t_extra}
            answer = body.get("response") or ""
            drawn = [t for t in DRAWN if t in answer]
            row = {"sample": sample, "knowledge_id": ids[sample], "question": q, "chart": data, "expected": expected,
                   "exact": not wrong and not extra, "wrong": wrong, "extra": extra, "vs_truth": vs_truth,
                   "answer_draws_chart": bool(drawn),
                   "aggregation_status": (((body.get("data") or {}).get("knowledge") or {}).get("rag_context") or {}).get("aggregation", {}).get("status"),
                   "answer": answer}
            results.append(row)
            truth_note = "" if vs_truth is None else f" truth={'EXACT' if vs_truth['exact'] else json.dumps({**vs_truth['wrong'], 'extra': vs_truth['extra']})}"
            print(f"{'EXACT' if row['exact'] else 'DIFF '} drawn={bool(drawn)} agg={row['aggregation_status']} | {q[:60]} | "
                  f"chart={json.dumps(data)} wrong={json.dumps(wrong)} extra={extra}{truth_note}", flush=True)
            Path(a.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
