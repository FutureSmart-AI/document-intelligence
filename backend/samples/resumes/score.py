"""Score a hosted run (hosted_run/extracted.json, answers.json) against the ground truth.

Mechanical fields are compared exactly (email lowercased; the two enums; the
integer years), honouring `alternatives` in ground_truth.json. Everything whose
correctness turns on meaning - names, roles, skill/location/industry/language
lists, certifications, work history, education, and the chat answers - is
judged by an LLM with structured output, run twice; disagreements between the
two passes are reported rather than averaged away.

  OPENAI_API_KEY=... python score.py      -> hosted_run/scores.json + VERIFICATION.md tables
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
RUN = HERE / "hosted_run"
MODEL = os.environ.get("JUDGE_MODEL", "gpt-5.4-mini")
EXACT = ["candidate_email", "seniority_level", "education_level", "years_of_experience"]
JUDGED = ["candidate_name", "current_role", "skills", "certifications", "locations", "industries",
          "languages", "work_experience", "education"]


class FieldVerdict(BaseModel):
    field: str
    expected_items: int
    extracted_items: int
    matched_items: int
    verdict: Literal["correct", "partial", "wrong", "missing"]
    reason: str


class ResumeJudgement(BaseModel):
    fields: list[FieldVerdict]


class AnswerJudgement(BaseModel):
    verdict: Literal["correct", "partial", "wrong", "refused"]
    expected_named: int
    correctly_named: int
    wrongly_included: list[str]
    missed: list[str]
    numbers_correct: bool
    reason: str


FIELD_RUBRIC = """You grade structured extraction from one resume against its ground truth.
For each listed field return one verdict. Items are list elements (a skill, a location, a role, a degree);
a scalar field counts as one item.
- Match on MEANING, not spelling: 'k8s' = 'kubernetes', 'Amazon Web Services' = 'aws', 'B.Tech' = 'Bachelor of Technology',
  'Bangalore' = 'Bengaluru', 'Sr. ML Engineer' = 'Senior Machine Learning Engineer'.
- A work_experience item matches when company, title and years agree (summary wording may differ but must describe the same work).
  An education item matches when institution and degree agree and year agrees.
- An extracted item that is true for the resume but absent from the ground truth is NOT a match but is not an error either;
  mention it in reason. An item that is false for the resume is an error.
- verdict: correct = every expected item matched and nothing false; partial = some matched; wrong = none matched or something false
  dominates; missing = field absent/null/empty while the ground truth is non-empty. Empty expected + empty extracted = correct.
"""

ANSWER_RUBRIC = """You grade a chat answer over a collection of 24 resumes against the known correct answer.
- Count the candidates the correct answer requires (expected_named) and how many the response names correctly.
- wrongly_included: names the response includes that the correct answer excludes. missed: required names it leaves out.
- numbers_correct: every count/number/distribution the response states agrees with the correct answer (true if it states none).
- verdict: correct = all required names, no wrong inclusions, numbers right; partial = useful but incomplete or with
  one or two errors; wrong = mostly incorrect; refused = says it cannot answer.
If the response is not an answer at all - for example it is a dump of retrieved document text or chunks
("RELEVANT CHUNKS", "[Chunk 1 | Doc: ...]", "Extracted Text:") rather than a reply to the question - the verdict is
wrong and correctly_named is 0, whatever names the dumped text happens to contain.
For comparison/shortlist questions, judge whether the shortlist and reasoning agree with the expected shortlist and
rationale; honourable mentions listed as acceptable are not wrong inclusions.
"""


def judge(client: OpenAI, system: str, payload: dict, fmt):
    r = client.responses.parse(model=MODEL, input=[{"role": "system", "content": system},
                                                   {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                               text_format=fmt)
    return r.output_parsed


def extracted_fields(meta: dict) -> dict:
    """The metadata route nests the values; find the dict that holds the template fields."""
    stack = [meta]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "candidate_name" in cur or "skills" in cur:
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return {}


def exact_ok(field, expected, got, alternatives) -> bool:
    allowed = [expected] + alternatives.get(field, [])
    if field == "candidate_email":
        return isinstance(got, str) and got.strip().lower() in [a.lower() for a in allowed if a]
    if field == "years_of_experience":
        try:
            return int(got) in allowed
        except (TypeError, ValueError):
            return False
    return got in allowed


def score_resume(client, gt, got) -> dict:
    exp = gt["fields"]
    row = {"id": gt["id"], "name": exp["candidate_name"], "layout": gt["layout"], "exact": {}, "judged": {}}
    for f in EXACT:
        row["exact"][f] = {"expected": exp[f], "extracted": got.get(f), "ok": exact_ok(f, exp[f], got.get(f), gt["alternatives"])}
    payload = {"fields": JUDGED, "ground_truth": {f: exp[f] for f in JUDGED},
               "extracted": {f: got.get(f) for f in JUDGED}, "notes": gt.get("notes")}
    passes = [judge(client, FIELD_RUBRIC, payload, ResumeJudgement) for _ in range(2)]
    for f in JUDGED:
        vs = [next((v for v in p.fields if v.field == f), None) for p in passes]
        vs = [v for v in vs if v]
        if not vs:
            continue
        row["judged"][f] = {"verdicts": [v.verdict for v in vs], "agree": len({v.verdict for v in vs}) == 1,
                            "expected_items": vs[0].expected_items, "extracted_items": vs[0].extracted_items,
                            "matched_items": min(v.matched_items for v in vs), "reason": vs[0].reason,
                            "extracted": got.get(f)}
    return row


def score_answer(client, q, a) -> dict:
    payload = {"question": q["question"], "correct_answer": q["expected"], "response": a.get("response")}
    passes = [judge(client, ANSWER_RUBRIC, payload, AnswerJudgement) for _ in range(2)]
    p = passes[0]
    return {"id": q["id"], "answer_source": q["answer_source"], "verdicts": [x.verdict for x in passes],
            "agree": passes[0].verdict == passes[1].verdict, "expected_named": p.expected_named,
            "correctly_named": min(x.correctly_named for x in passes), "wrongly_included": p.wrongly_included,
            "missed": p.missed, "numbers_correct": p.numbers_correct, "reason": p.reason,
            "sources": len(a.get("sources") or []), "chart": any(isinstance(c.get("value"), dict) and c["value"].get("type") == "chart_response" for c in a.get("charts") or []), "seconds": a.get("seconds")}


def cell(v) -> str:
    text = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return text.replace("|", "/").replace("\n", " ")


def write_tables(scores: dict, questions: list[dict], answers: dict) -> str:
    """Markdown tables for VERIFICATION.md, derived only from scores.json / answers.json."""
    out = ["### Field-level extraction accuracy", "",
           "| Field | Correct (both judge passes) | Item recall | Judge passes disagreed |", "|---|---|---|---|"]
    rs = scores["resumes"]
    for f in EXACT:
        ok = sum(r["exact"][f]["ok"] for r in rs)
        out.append(f"| `{f}` | {ok}/{len(rs)} | exact match | - |")
    for f in JUDGED:
        js = [r["judged"][f] for r in rs if f in r["judged"]]
        ok = sum(1 for j in js if j["agree"] and j["verdicts"][0] == "correct")
        exp = sum(j["expected_items"] for j in js) or 1
        rec = sum(min(j["matched_items"], j["expected_items"]) for j in js) / exp
        dis = sum(1 for j in js if not j["agree"])
        out.append(f"| `{f}` | {ok}/{len(js)} | {rec:.0%} | {dis} |")
    out += ["", "### Every miss", "", "| Resume | Layout | Field | Expected | Extracted / judge reason |", "|---|---|---|---|---|"]
    for r in rs:
        for f, v in r["exact"].items():
            if not v["ok"]:
                out.append(f"| {r['id']} {r['name']} | {r['layout']} | `{f}` | {cell(v['expected'])} | {cell(v['extracted'])} |")
        for f, v in r["judged"].items():
            if not (v["agree"] and v["verdicts"][0] == "correct"):
                out.append(f"| {r['id']} {r['name']} | {r['layout']} | `{f}` | judge: {'/'.join(v['verdicts'])} | {cell(v['reason'])} |")
    out += ["", "### Suggested questions on the hosted collection", "",
            "| Q | Source label | Verdict (2 passes) | Named right | Wrong / missed | Numbers | Sources | Chart | Time |",
            "|---|---|---|---|---|---|---|---|---|"]
    by_id = {a["id"]: a for a in scores["answers"]}
    for q in questions:
        a = by_id.get(q["id"])
        if not a:
            continue
        wm = "; ".join(filter(None, [("+" + ", ".join(a["wrongly_included"])) if a["wrongly_included"] else "",
                                     ("-" + ", ".join(a["missed"])) if a["missed"] else ""])) or "none"
        out.append(f"| {q['id']} {cell(q['question'])} | {q['answer_source']} | {'/'.join(a['verdicts'])} | "
                   f"{a['correctly_named']}/{a['expected_named']} | {cell(wm)[:200]} | {'yes' if a['numbers_correct'] else 'no'} | "
                   f"{a['sources']} | {'yes' if a['chart'] else 'no'} | {a['seconds']}s |")
    out += ["", "### Answers as returned", ""]
    for q in questions:
        if q["id"] in answers:
            a = answers[q["id"]]
            out += [f"**{q['id']}. {q['question']}**", "", "> " + (a.get("response") or "(no response)").replace("\n", "\n> "), "",
                    "Sources: " + (", ".join(s.get("title") or s.get("file_name") or "?" for s in a.get("sources") or []) or "none"),
                    "Chart: " + (", ".join(c["path"] for c in a.get("charts") or []) or "none"), ""]
    return "\n".join(out) + "\n"


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY not set")
    client = OpenAI()
    gt = json.loads((HERE / "ground_truth.json").read_text(encoding="utf-8"))["resumes"]
    extracted = json.loads((RUN / "extracted.json").read_text(encoding="utf-8"))
    by_file = {Path(k).name: v for k, v in extracted.items()}
    with ThreadPoolExecutor(6) as ex:
        resumes = list(ex.map(lambda g: score_resume(client, g, extracted_fields(by_file.get(Path(g["file"]).name, {}))), gt))
    answers_path = RUN / "answers.json"
    answers, qs, got = [], [], {}
    if answers_path.exists():
        qs = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))["questions"]
        got = json.loads(answers_path.read_text(encoding="utf-8"))
        with ThreadPoolExecutor(6) as ex:
            answers = list(ex.map(lambda q: score_answer(client, q, got[q["id"]]), [q for q in qs if q["id"] in got]))
    (RUN / "scores.json").write_text(json.dumps({"judge_model": MODEL, "resumes": resumes, "answers": answers},
                                                indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if answers_path.exists():
        tables = write_tables({"resumes": resumes, "answers": answers}, qs, got)
        (RUN / "tables.md").write_text(tables, encoding="utf-8")
    print("scored", len(resumes), "resumes,", len(answers), "answers")


if __name__ == "__main__":
    main()
