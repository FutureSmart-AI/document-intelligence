"""Grading for the Document Intelligence model evaluation.

Extraction: per document, per field. Exact fields (identifiers, enums, dates, amounts,
integer years) are compared exactly, honouring the fixture's `alternatives`. Fields whose
correctness turns on meaning (names, roles, lists, work history, line items, payment terms)
are judged by an LLM with structured output, twice; a value counts as correct only when BOTH
passes say "correct" (backend/samples/resumes/score.py's rubric for resumes, INVOICE_RUBRIC
below for invoices).

RAG: every answer is judged twice with evals/metadata_qa/grade.py's rubric against an
expected answer computed from the collection's own extracted metadata (cases.py); correct
only when both passes say "pass". Charts are graded mechanically: the chart's rows must equal
the expected aggregation of the extracted metadata, on the chart's own groups.

The pure functions here need no network and are covered by tests/unit/test_model_eval.py.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
JUDGE_MODEL = os.getenv("MODEL_EVAL_JUDGE_MODEL", "gpt-5.6-terra")
# The judge is gpt-5.6-terra reasoning medium. gpt-5.4-mini without reasoning graded 'Milestone-based' vs
# 'milestone-based' as wrong and called lists 'wrong' whose reason said every expected item matched
# (first workflow run, 2026-10-26), and at reasoning medium it still called lists "wrong" whose own
# reason said the extras were true and not errors; terra applied the rubric consistently on the same
# disputed fields. The resume baseline 306/312 was scored by gpt-5.4-mini without reasoning.
JUDGE_EFFORT = os.getenv("MODEL_EVAL_JUDGE_EFFORT", "medium")


def load_module(name: str, path: Path):
    """Import a sibling script (score.py, metadata_qa/grade.py) by path, without packaging it."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: pydantic resolves `from __future__ import annotations` forward
    # references (score.py's ResumeJudgement -> FieldVerdict) through sys.modules[name].
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_cache: Dict[str, Any] = {}


def resume_score():
    """backend/samples/resumes/score.py: its rubric, judgement schema and exact comparison."""
    if "score" not in _cache:
        _cache["score"] = load_module("di_resume_score", REPO / "backend/samples/resumes/score.py")
    return _cache["score"]


def qa_grade():
    """evals/metadata_qa/grade.py: the answer rubric used for every RAG verdict."""
    if "qa" not in _cache:
        _cache["qa"] = load_module("di_metadata_qa_grade", REPO / "evals/metadata_qa/grade.py")
    return _cache["qa"]


INVOICE_RUBRIC = """You grade structured extraction from one supplier invoice against its ground truth.
For each listed field return one verdict. Items are list elements (a category, a line item); a scalar field counts as one item.
- Match names on MEANING, not punctuation or legal-suffix spelling: 'Bramblecloud Hosting Ltd' = 'Bramblecloud Hosting Ltd.',
  'Cedar Lane Retail' = 'Cedar Lane Retail Inc.'. A different company is wrong.
- payment_terms: the same terms in other words are correct ('Net 30', 'net 30 days', 'Payment within 30 days' = 'net 30';
  'Due upon receipt' = 'due on receipt'). Different terms are wrong.
- line_item_categories: a category matches when it names the same kind of goods/services ('cloud hosting' = 'cloud infrastructure',
  'licenses' = 'software licenses'). A true extra category is not an error; a false one is.
- line_items: an item matches when its description refers to the same billed line AND quantity, unit_price and amount are
  numerically equal (1 = 1.0; 2400 = 2400.00). A wrong number makes that item wrong. Missing lines are missed items.
- verdict: correct = every expected item matched and nothing false; partial = some matched; wrong = none matched or something
  false dominates; missing = field absent/null/empty while the ground truth is non-empty. Empty expected + empty extracted = correct.
"""

# Exact comparison per field kind. Everything else in a collection's `judged` list goes to the judge.
NUMERIC_TOLERANCE = 0.005


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def find_fields(meta: Any, fields: Iterable[str]) -> Dict[str, Any]:
    """The metadata route nests the values; return the innermost dict holding any template field."""
    wanted = set(fields)
    stack = [meta]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if wanted & set(cur):
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return {}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def exact_ok(kind: str, expected: Any, got: Any, alternatives: Iterable[Any] = ()) -> bool:
    """Mechanical equality for identifier/enum/date/number fields (no semantic judgement here).

    kind: "email" (case-insensitive), "integer" (whole numbers), "amount" (float, half-cent
    tolerance), "date" (ISO YYYY-MM-DD string), "code" (trimmed, case-insensitive: invoice
    numbers, ISO currency codes), "enum" (exact)."""
    allowed = [expected, *alternatives]
    if kind == "email":
        return isinstance(got, str) and got.strip().lower() in {str(a).lower() for a in allowed if a}
    if kind == "integer":
        n = _number(got)
        return n is not None and float(n).is_integer() and int(n) in {int(a) for a in allowed if a is not None}
    if kind == "amount":
        n = _number(got)
        return n is not None and any(abs(n - float(a)) <= NUMERIC_TOLERANCE for a in allowed if a is not None)
    if kind == "date":
        return isinstance(got, str) and got.strip()[:10] in {str(a) for a in allowed if a}
    if kind == "code":
        return isinstance(got, str) and got.strip().lower() in {str(a).strip().lower() for a in allowed if a}
    return got in allowed


def field_status(expected: Any, got: Any, correct: bool) -> str:
    """correct | wrong | missing (expected a value, got none) | unexpected (expected none, got one)."""
    if correct:
        return "correct"
    if is_empty(got) and not is_empty(expected):
        return "missing"
    if is_empty(expected) and not is_empty(got):
        return "unexpected"
    return "wrong"


def judge_fields(client, rubric: str, judged: List[str], expected: Dict[str, Any], got: Dict[str, Any],
                 notes: Any = None) -> List[Any]:
    """Two independent structured judgements of the meaning fields (score.py's schema)."""
    score = resume_score()
    payload = {"fields": judged, "ground_truth": {f: expected.get(f) for f in judged},
               "extracted": {f: got.get(f) for f in judged}, "notes": notes}

    def once():
        r = client.responses.parse(model=JUDGE_MODEL, reasoning={"effort": JUDGE_EFFORT},
                                   input=[{"role": "system", "content": rubric},
                                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
                                   text_format=score.ResumeJudgement)
        return r.output_parsed

    return [once(), once()]


def grade_document(spec: Dict[str, Any], record: Dict[str, Any], got: Dict[str, Any],
                   passes: Optional[List[Any]]) -> Dict[str, Any]:
    """Every field of one extracted document against its ground-truth record.

    spec: the collection's entry in dataset.json (exact {field: kind}, judged [fields]).
    passes: the two judge passes for spec["judged"] (None only in offline tests of exact fields)."""
    expected = record["fields"]
    alternatives = record.get("alternatives") or {}
    fields: Dict[str, Dict[str, Any]] = {}
    for field, kind in spec["exact"].items():
        ok = exact_ok(kind, expected.get(field), got.get(field), alternatives.get(field, []))
        fields[field] = {"method": "exact", "kind": kind, "expected": expected.get(field), "extracted": got.get(field),
                         "correct": ok, "status": field_status(expected.get(field), got.get(field), ok)}
    for field in spec["judged"]:
        verdicts = []
        reason = None
        for p in passes or []:
            v = next((x for x in p.fields if x.field == field), None)
            verdicts.append(v.verdict if v else "absent")
            reason = reason or (v.reason if v else None)
        ok = bool(verdicts) and all(v == "correct" for v in verdicts)
        fields[field] = {"method": "judge", "expected": expected.get(field), "extracted": got.get(field),
                         "verdicts": verdicts, "agree": len(set(verdicts)) == 1, "reason": reason,
                         "correct": ok, "status": field_status(expected.get(field), got.get(field), ok)}
    required = [f for f in fields if not is_empty(expected.get(f))]
    filled = [f for f in required if not is_empty(got.get(f))]
    return {
        "doc_id": record["id"], "file": Path(record["file"]).name, "layout": record.get("layout"),
        "fields": fields,
        "fields_correct": sum(1 for f in fields.values() if f["correct"]),
        "fields_total": len(fields),
        "required_fields": len(required), "required_filled": len(filled),
        "complete": len(filled) == len(required),
        "all_correct": all(f["correct"] for f in fields.values()),
    }


# ── RAG ──────────────────────────────────────────────────────────────────────

def judge_answer(client, case: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """Two passes of metadata_qa's answer judge; correct only when both say pass."""
    qa = qa_grade()
    payload = {k: case[k] for k in ("question", "kind", "expected", "facts", "acceptable_extras", "must_not_include",
                                    "near_misses") if k in case}
    payload["answer"] = answer or ""

    def once():
        # metadata_qa/grade.py's rubric and verdict schema, with the evaluation's judge settings
        r = client.responses.parse(model=JUDGE_MODEL, reasoning={"effort": JUDGE_EFFORT},
                                   input=[{"role": "system", "content": qa.JUDGE_SYSTEM},
                                          {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
                                   text_format=qa.Verdict)
        return r.output_parsed

    passes = [once(), once()]
    verdicts = [p.verdict for p in passes]
    return {"verdicts": verdicts, "agree": len(set(verdicts)) == 1, "pass": all(v == "pass" for v in verdicts),
            "reason": passes[0].reason, "missing": passes[0].missing, "wrong": passes[0].wrong}


def judge_either(client, case: Dict[str, Any], answer: str, truth: Any = None) -> Dict[str, Any]:
    """Judge against the expectation computed from the EXTRACTED metadata; when the ground truth
    gives a different answer (an extraction error) and the answer fails that, judge it against the
    ground-truth expectation too. An answer that is right about the documents is never a QA failure;
    `graded_against` records which expectation it met."""
    v = judge_answer(client, case, answer)
    v["graded_against"] = "extracted"
    if v["pass"] or truth is None or truth == case.get("expected"):
        return v
    t = judge_answer(client, {**case, "expected": truth}, answer)
    if t["pass"]:
        t["graded_against"] = "truth"
        t["extracted_verdict"] = {k: v[k] for k in ("verdicts", "reason")}
        return t
    v["truth_verdict"] = {k: t[k] for k in ("verdicts", "reason")}
    return v


def chart_rows(body: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """{category: value} from the response's structured chart, or None when no chart was returned."""
    co = (((body.get("data") or {}).get("knowledge") or {}).get("chart_output")) or {}
    rows = (co.get("chart_data") or {}).get("data")
    if not rows:
        return None
    out: Dict[str, float] = {}
    for r in rows:
        try:
            out[str(r.get("category")).strip()] = float(r.get("value"))
        except (TypeError, ValueError):
            out[str(r.get("category")).strip()] = math.nan
    return out


def _range(label: str):
    s = str(label).strip()
    try:
        if s.endswith("+"):
            return float(s[:-1]), float("inf")
        if "-" in s[1:]:
            lo, hi = s.split("-", 1)
            return float(lo), float(hi)
        return float(s), float(s)
    except ValueError:
        return None


def regroup(expected: Dict[str, float], groups) -> Dict[str, float]:
    """Per-value counts onto the chart's numeric range groups (evals/metadata_qa/samples.py's rule)."""
    ranges = {g: _range(g) for g in groups}
    values = {k: _range(k) for k in expected}
    if not ranges or None in ranges.values() or None in values.values() or any(lo != hi for lo, hi in values.values()):
        return expected
    out = {g: 0.0 for g in ranges}
    for k, n in expected.items():
        hit = [g for g, (lo, hi) in ranges.items() if lo <= values[k][0] <= hi]
        if len(hit) != 1:
            return expected
        out[hit[0]] += n
    return out


def _norm_key(k: str) -> str:
    s = str(k).strip().lower()
    n = _number(s)
    return str(int(n)) if n is not None and float(n).is_integer() else s


def compare_chart(expected: Dict[str, float], chart: Optional[Dict[str, float]]) -> Dict[str, Any]:
    """Exact when every expected group has its value (±0.01) and the chart has no other non-zero group."""
    if chart is None:
        return {"exact": False, "chart": None, "expected": expected, "wrong": {}, "extra": [], "reason": "no chart returned"}
    exp = regroup({str(k): float(v) for k, v in expected.items()}, list(chart))
    got = {_norm_key(k): v for k, v in chart.items()}
    wrong = {k: {"expected": v, "chart": got.get(_norm_key(k))} for k, v in exp.items()
             if got.get(_norm_key(k)) is None or abs(got[_norm_key(k)] - v) > 0.01}
    extra = sorted(k for k, v in got.items() if k not in {_norm_key(x) for x in exp} and v)
    return {"exact": not wrong and not extra, "chart": chart, "expected": exp, "wrong": wrong, "extra": extra,
            "reason": None if not wrong and not extra else "chart rows differ from the expected aggregation"}


def compare_population_chart(people: Dict[str, float], chart: Optional[Dict[str, float]]) -> Dict[str, Any]:
    """A follow-up chart of a population ('chart their years'). The chart may draw one bar per
    person (category = name) or a distribution (category = value, value = count); either way it
    must cover exactly the people in `people` ({name: value}). Which reading applies is decided
    mechanically by whether the chart's categories are the population's names."""
    if chart is None:
        return {"exact": False, "chart": None, "reading": None, "reason": "no chart returned",
                "expected_population": len(people)}
    names = {n.lower() for n in people}
    by_name = sum(1 for k in chart if str(k).strip().lower() in names)
    if by_name and by_name >= len(chart) / 2:
        result = compare_chart({n: v for n, v in people.items()}, chart)
        result["reading"] = "per_person"
    else:
        dist: Dict[str, float] = {}
        for v in people.values():
            key = _norm_key(str(v))
            dist[key] = dist.get(key, 0) + 1
        result = compare_chart(dist, chart)
        result["reading"] = "distribution"
        result["chart_population"] = sum(v for v in chart.values() if not math.isnan(v))
    result["expected_population"] = len(people)
    return result


def sources_ok(expected_files: Optional[List[str]], sources: List[str]) -> Optional[bool]:
    """Every expected source document is among the returned sources (None when the case names none)."""
    if not expected_files:
        return None
    have = {Path(str(s)).name.lower() for s in sources if s}
    return all(Path(f).name.lower() in have for f in expected_files)
