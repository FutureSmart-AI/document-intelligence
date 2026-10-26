"""RAG cases for the model evaluation, with expected answers COMPUTED from a collection's
extracted metadata (evals/metadata_qa/questions.py's method: the answering layer can only see
what was extracted, so that is what an answer is graded against). Where the ground truth gives
a different answer, the case carries `truth_differs`: the difference is an extraction error,
reported in the extraction suite, not a QA failure.

Sources reused, not rewritten:
  evals/metadata_qa/questions.py       STRUCTURED (filters, ranges, counts, aggregates, ranking)
                                       and TEXT (passage, multi-document synthesis, hybrid)
  evals/metadata_qa/chart_regression.py the chart questions and their aggregations
  backend/samples/invoices/questions.json the invoice questions (expected recomputed per collection
                                       by the same rules as backend/samples/invoices/build.py)
Added here: two multi-turn follow-ups, one of them the "chart their years" regression.
"""
from __future__ import annotations

import collections
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, List

from grade import REPO, load_module

_mods: Dict[str, Any] = {}


def questions_module():
    if "q" not in _mods:
        _mods["q"] = load_module("di_metadata_qa_questions", REPO / "evals/metadata_qa/questions.py")
    return _mods["q"]


Doc = Dict[str, Any]


def _lower(values) -> set:
    return {str(v).strip().lower() for v in (values or []) if v is not None}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def _str(v) -> str:
    return str(v).strip() if v is not None else ""


# ── invoices (same rules as backend/samples/invoices/build.py::build_questions) ─

def inv_eur(D: List[Doc]):
    rows = [{"invoice_number": d.get("invoice_number"), "vendor": d.get("vendor_name"), "total": d.get("total_amount")}
            for d in D if _str(d.get("currency")).upper() == "EUR"]
    return {"invoices": rows, "count": len(rows)}


def inv_cloud(D: List[Doc]):
    rows = [{"invoice_number": d.get("invoice_number"), "vendor": d.get("vendor_name")}
            for d in D if "cloud infrastructure" in _lower(d.get("line_item_categories"))]
    return {"invoices": rows, "count": len(rows)}


def inv_due_q4(D: List[Doc]):
    rows = [{"invoice_number": d.get("invoice_number"), "vendor": d.get("vendor_name"), "due_date": d.get("due_date")}
            for d in D if _str(d.get("due_date"))[:10] >= "2026-10-01"]
    return {"invoices": rows, "count": len(rows)}


def inv_receipt(D: List[Doc]):
    rows = [d.get("invoice_number") for d in D if _str(d.get("payment_terms")).lower() == "due on receipt"]
    return {"invoices": rows, "count": len(rows)}


def inv_usd_by_vendor(D: List[Doc]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for d in D:
        if _str(d.get("currency")).upper() == "USD" and d.get("vendor_name"):
            out[d["vendor_name"]] = round(out.get(d["vendor_name"], 0.0) + _num(d.get("total_amount")), 2)
    return out


def inv_eur_total(D: List[Doc]) -> float:
    return round(sum(_num(d.get("total_amount")) for d in D if _str(d.get("currency")).upper() == "EUR"), 2)


# ── resumes ──────────────────────────────────────────────────────────────────

SKILLS = ["python", "aws", "sql", "docker", "rag"]


def res_by_field(field: str) -> Callable[[List[Doc]], Dict[str, float]]:
    return lambda D: dict(collections.Counter(str(d.get(field)) for d in D if d.get(field) is not None))


def res_by_skill(D: List[Doc]) -> Dict[str, float]:
    return {s: sum(s in _lower(d.get("skills")) for d in D) for s in SKILLS}


def python_aws_5plus(D: List[Doc]) -> Dict[str, float]:
    """{name: years} for candidates with python AND aws and >= 5 years (the follow-up population)."""
    return {d["candidate_name"]: _num(d.get("years_of_experience")) for d in D
            if {"python", "aws"} <= _lower(d.get("skills")) and _num(d.get("years_of_experience")) >= 5}


CHARTS = [
    {"id": "ch_seniority", "collection": "resumes",
     "question": "How are candidates distributed by seniority level? Show it as a chart.", "expected": res_by_field("seniority_level")},
    {"id": "ch_years", "collection": "resumes",
     "question": "What is the distribution of years of experience across candidates? Show a chart.",
     "expected": res_by_field("years_of_experience")},
    {"id": "ch_education", "collection": "resumes",
     "question": "Chart the number of candidates by education level", "expected": res_by_field("education_level")},
    {"id": "ch_skills", "collection": "resumes",
     "question": "How many candidates list each of these skills: python, aws, sql, docker, rag? Show a chart.",
     "expected": res_by_skill},
    {"id": "ch_usd_vendors", "collection": "invoices",
     "question": "What is the total billed by each US-dollar vendor? Show it as a chart.", "expected": inv_usd_by_vendor},
]

INVOICE_QUESTIONS: Dict[str, Callable[[List[Doc]], Any]] = {
    "q01": inv_eur, "q02": inv_cloud, "q03": inv_due_q4, "q04": inv_receipt,
}

FOLLOW_UP_YEARS = {
    "id": "fu_python_aws_years_chart", "collection": "resumes", "category": "follow_up",
    "turns": ["Which candidates know Python and AWS and have 5+ years of experience?", "chart their years"],
}
FOLLOW_UP_EUR = {
    "id": "fu_eur_total", "collection": "invoices", "category": "follow_up",
    "turns": ["Which invoices are billed in euros?", "What is the combined total of those invoices?"],
}


def _files_of(records: List[Dict[str, Any]], key: str, names: List[str]) -> List[str]:
    want = {n.lower() for n in names}
    return sorted(Path(r["file"]).name for r in records if str(r["fields"].get(key, "")).lower() in want)


def build_cases(tables: Dict[str, Dict[str, List[Doc]]], records: Dict[str, List[Dict[str, Any]]],
                invoice_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every RAG case with its expected answer.

    tables[collection] = {"extracted": [...], "truth": [...]} — one flat dict of field values per document.
    records[collection] = the ground-truth records (for expected source files of text questions)."""
    q = questions_module()
    R, I = tables["resumes"], tables["invoices"]
    cases: List[Dict[str, Any]] = []

    for s in q.STRUCTURED:
        exp, tru = s["expected"](R["extracted"]), s["expected"](R["truth"])
        case = {k: v for k, v in s.items() if k != "expected"}
        case.update(collection="resumes", expected=exp, kind=s["kind"])
        if tru != exp:
            case["truth_differs"] = tru
        cases.append(case)

    sources_for = {"p01": ["Daniel Okafor"], "p02": ["Daniel Okafor"], "p03": ["Fatima Sheikh"]}
    for t in q.TEXT:
        case = dict(t, collection="resumes")
        if t["id"] in sources_for:
            case["expected_sources"] = _files_of(records["resumes"], "candidate_name", sources_for[t["id"]])
        cases.append(case)

    by_id = {x["id"]: x for x in invoice_questions}
    for qid, fn in INVOICE_QUESTIONS.items():
        exp, tru = fn(I["extracted"]), fn(I["truth"])
        case = {"id": f"inv_{qid}", "category": "invoice_structured", "collection": "invoices",
                "question": by_id[qid]["question"], "kind": "expected_object", "expected": exp}
        if tru != exp:
            case["truth_differs"] = tru
        cases.append(case)
    lumen = by_id["q06"]
    cases.append({"id": "inv_q06", "category": "invoice_text", "collection": "invoices", "question": lumen["question"],
                  "kind": "expected_object", "expected": lumen["expected"],
                  "expected_sources": _files_of(records["invoices"], "vendor_name", ["Lumen Ridge Analytics LLC"])})

    for c in CHARTS:
        table = R if c["collection"] == "resumes" else I
        exp, tru = c["expected"](table["extracted"]), c["expected"](table["truth"])
        case = {"id": c["id"], "category": "chart", "collection": c["collection"], "question": c["question"],
                "kind": "chart", "expected_chart": exp}
        if tru != exp:
            case["truth_differs"] = tru
        cases.append(case)

    people, people_truth = python_aws_5plus(R["extracted"]), python_aws_5plus(R["truth"])
    fu = dict(FOLLOW_UP_YEARS, kind="follow_up_chart", question=FOLLOW_UP_YEARS["turns"][0],
              expected=sorted(people), expected_population=people)
    if people_truth != people:
        fu["truth_differs"] = {"names": sorted(people_truth), "population": people_truth}
    cases.append(fu)

    eur, eur_truth = inv_eur_total(I["extracted"]), inv_eur_total(I["truth"])
    fe = dict(FOLLOW_UP_EUR, kind="follow_up_number", question=FOLLOW_UP_EUR["turns"][0],
              expected_turn1=inv_eur(I["extracted"]), expected=eur)
    if eur_truth != eur:
        fe["truth_differs"] = eur_truth
    cases.append(fe)
    return cases


def flat_truth(records: List[Dict[str, Any]]) -> List[Doc]:
    return [dict(r["fields"], file=Path(r["file"]).name) for r in records]


def flat_extracted(records: List[Dict[str, Any]], extracted: Dict[str, Dict[str, Any]], name_field: str = None) -> List[Doc]:
    """Extracted values per ground-truth document (by file name). A missing name falls back to the
    ground truth's name only so a person stays identifiable in expected lists (questions.py's rule)."""
    out = []
    for r in records:
        f = Path(r["file"]).name
        meta = dict(extracted.get(f) or {})
        if name_field and not meta.get(name_field):
            meta[name_field] = r["fields"].get(name_field)
        out.append(dict(meta, file=f))
    return out


def load_invoice_questions() -> List[Dict[str, Any]]:
    return json.loads((REPO / "backend/samples/invoices/questions.json").read_text(encoding="utf-8"))["questions"]
