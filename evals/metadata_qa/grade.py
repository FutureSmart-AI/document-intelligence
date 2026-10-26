"""Grade a hosted run of the metadata QA set.

The verdict on whether an answer is right is an LLM judgement (structured
output, two independent passes; disagreement is reported, not hidden). The
trace checks next to it are mechanical facts read off the recorded tool
calls - which filter keys the model wrote vs. which the executor applied,
whether an exhaustive shape was used, whether the population was truncated,
whether a tool errored - and never decide pass/fail on their own.

  OPENAI_KEY_FILE=... python grade.py --questions qset.json --run run.json --out graded.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal

from openai import OpenAI
from pydantic import BaseModel, Field

JUDGE_MODEL = "gpt-5.4-mini"
EXHAUSTIVE_CATEGORIES = {"exact_filter", "multi_field_filter", "negation_filter", "numeric_range",
                         "count", "aggregation", "ranking"}


class Verdict(BaseModel):
    verdict: Literal["pass", "fail"] = Field(description="pass only if the answer is fully correct for the expected answer")
    reason: str = Field(description="one or two sentences: why")
    missing: List[str] = Field(default_factory=list, description="expected items absent from the answer")
    wrong: List[str] = Field(default_factory=list, description="items/values in the answer that are not expected")
    confidence: float = Field(ge=0, le=1)


JUDGE_SYSTEM = """You grade a document-QA system's answer against a computed expected answer.
Rules:
- kind=names: the answer must list exactly the expected people. Missing or extra people = fail.
- kind=number: the number must equal the expected value (one-decimal rounding tolerance for averages).
- kind=mapping: every key/value must match; extra keys with zero counts are fine.
- kind=ordered: the first items must be the expected people in the expected order; ties may be listed in either order.
- kind=facts: every item in `facts` must be stated (meaning, not wording). Items in `acceptable_extras` may appear or not.
  Anything in `must_not_include`, or any additional PERSON / list member not in facts or acceptable_extras,
  is wrong -> fail. Extra descriptive detail about an expected item (a number, a scope, a technology) is
  NOT an error — the source document has more detail than the facts list — unless it contradicts a fact.
- kind=expected_object: `expected` is a computed answer (people with values, counts, distributions, or
  required facts). Every expected person / number / bucket must be present and correct; anyone listed in
  `near_misses` or otherwise not expected is wrong. Charts are not graded.
- Ignore formatting, ordering for unordered kinds, and extra explanation that does not contradict the expected answer.
- An answer that refuses, says it lacks information, or hedges instead of answering = fail."""


def judge(client: OpenAI, q: Dict[str, Any], answer: str) -> Verdict:
    payload = {k: q[k] for k in ("question", "kind", "expected", "facts", "acceptable_extras", "must_not_include",
                                 "near_misses") if k in q}
    payload["answer"] = answer
    resp = client.responses.parse(
        model=JUDGE_MODEL,
        input=[{"role": "system", "content": JUDGE_SYSTEM},
               {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        text_format=Verdict,
    )
    return resp.output_parsed


def trace_checks(q: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    tr = run.get("tool_responses") or []
    raw = run.get("tool_calls_raw") or []
    docs_calls = [t for t in tr if t.get("tool") == "retrieve_documents"]
    raw_docs = [c for c in raw if c.get("tool") == "retrieve_documents"]
    dropped = []
    for raw_call, applied in zip(raw_docs, docs_calls):
        rf = (raw_call.get("args") or {}).get("metadata_filter") or {}
        af = applied.get("applied_filter") or {}
        if isinstance(rf, dict) and isinstance(af, dict):
            lost = sorted(set(rf) - set(af) - {"$and"})
            if lost or (json.dumps(rf, sort_keys=True) != json.dumps(af, sort_keys=True)):
                dropped.append({"raw": rf, "applied": af, "keys_lost": lost})
    shapes = [t.get("question_shape") for t in docs_calls]
    exhaustive = [t for t in docs_calls if t.get("question_shape") in {"predicate", "selection", "aggregate"}]
    return {
        "calls": [f"{c.get('tool')}({(c.get('args') or {}).get('question_shape', '')})" for c in raw],
        "shapes": shapes,
        "raw_filters": [(c.get("args") or {}).get("metadata_filter") for c in raw_docs],
        "applied_filters": [t.get("applied_filter") for t in docs_calls],
        "doc_counts": [t.get("count") for t in docs_calls],
        "total_match_counts": [t.get("total_match_count") for t in docs_calls],
        "chunk_calls": sum(1 for c in raw if c.get("tool") == "retrieve_chunks"),
        "filter_rewritten": dropped,
        "needs_population": q["category"] in EXHAUSTIVE_CATEGORIES,
        "used_exhaustive_shape": bool(exhaustive),
        "population_truncated": any(t.get("truncated") for t in exhaustive),
        "tool_errors": [t.get("error") for t in tr if t.get("error")],
        "sources": run.get("sources") or [],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = OpenAI(api_key=Path(os.environ["OPENAI_KEY_FILE"]).read_text().strip())
    qs = {q["id"]: q for q in json.loads(Path(a.questions).read_text(encoding="utf-8"))["questions"]}
    runs = json.loads(Path(a.run).read_text(encoding="utf-8"))
    out = {}
    for qid, run in runs.items():
        q = qs[qid]
        answer = run.get("response") or run.get("error") or ""
        v1, v2 = judge(client, q, answer), judge(client, q, answer)
        verdict = v1.verdict if v1.verdict == v2.verdict else "disputed"
        out[qid] = {"id": qid, "category": q["category"], "question": q["question"], "expected": q.get("expected", q.get("facts")),
                    "truth_differs": q.get("truth_differs"), "verdict": verdict,
                    "judge": [v1.model_dump(), v2.model_dump()], "trace": trace_checks(q, run),
                    "response": answer, "seconds": run.get("seconds")}
        print(f"{qid:4} {q['category']:20} {verdict:8} {v1.reason[:110]}")
    Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    passed = sum(1 for r in out.values() if r["verdict"] == "pass")
    print(f"pass {passed}/{len(out)}")


if __name__ == "__main__":
    main()
