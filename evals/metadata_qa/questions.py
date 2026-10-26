"""Ground-truth question set for metadata-aware question answering.

Every expected answer is COMPUTED from a table of per-document metadata, never
typed by hand, so the set stays correct if the sample changes. Two tables are
used:

* ``truth``     - the sample's ground truth (what the resume really says);
* ``extracted`` - the metadata the hosted collection actually extracted.

Question answering is graded against ``extracted``: it is the population the
answering layer can see, so a mismatch there is a QA defect. Where ``truth``
gives a different answer the difference is an EXTRACTION defect, recorded on
the question as ``truth_differs`` so a failure is never blamed on the wrong
layer.

Questions whose answer lives only in document text (``passage``,
``synthesis``, ``hybrid``) carry hand-written ``facts`` taken from the
ground-truth work history; they are graded by the LLM judge.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

Doc = Dict[str, Any]


def _lower_set(values) -> set:
    return {str(v).strip().lower() for v in (values or [])}


def _has_all(doc: Doc, field: str, *values: str) -> bool:
    have = _lower_set(doc.get(field))
    return all(v.lower() in have for v in values)


def _yoe(doc: Doc) -> float:
    v = doc.get("years_of_experience")
    return float(v) if isinstance(v, (int, float)) else float("nan")


def _names(docs: List[Doc], pred: Callable[[Doc], bool]) -> List[str]:
    return sorted(d["candidate_name"] for d in docs if pred(d))


def _count_by(docs: List[Doc], field: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for d in docs:
        key = str(d.get(field))
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _top_by_yoe(docs: List[Doc], n: int, reverse: bool = True) -> List[str]:
    ranked = sorted(docs, key=lambda d: (_yoe(d), d["candidate_name"]), reverse=reverse)
    return [f"{d['candidate_name']} ({d.get('years_of_experience')})" for d in ranked[:n]]


# id, category, question, kind, expected(docs) -> answer
STRUCTURED: List[Dict[str, Any]] = [
    # exact and multi-field filters
    {"id": "f01", "category": "exact_filter", "kind": "names",
     "question": "Which candidates have a PhD?",
     "expected": lambda D: _names(D, lambda d: d.get("education_level") == "phd")},
    {"id": "f02", "category": "exact_filter", "kind": "names",
     "question": "Which candidates are at lead seniority level?",
     "expected": lambda D: _names(D, lambda d: d.get("seniority_level") == "lead")},
    {"id": "f03", "category": "multi_field_filter", "kind": "names",
     "question": "Which candidates know both Kubernetes and Terraform?",
     "expected": lambda D: _names(D, lambda d: _has_all(d, "skills", "kubernetes", "terraform"))},
    {"id": "f04", "category": "multi_field_filter", "kind": "names",
     "question": "Which candidates with a master's degree speak Hindi?",
     "expected": lambda D: _names(D, lambda d: d.get("education_level") == "masters" and _has_all(d, "languages", "hindi"))},
    {"id": "f05", "category": "multi_field_filter", "kind": "names",
     "question": "Which candidates have worked in fintech or healthcare?",
     "expected": lambda D: _names(D, lambda d: bool(_lower_set(d.get("industries")) & {"fintech", "healthcare"}))},
    {"id": "f06", "category": "negation_filter", "kind": "names",
     "question": "Which candidates do not list Python as a skill?",
     "expected": lambda D: _names(D, lambda d: not _has_all(d, "skills", "python"))},
    # numeric / range
    {"id": "n01", "category": "numeric_range", "kind": "names",
     "question": "Which candidates have more than 10 years of experience?",
     "expected": lambda D: _names(D, lambda d: _yoe(d) > 10)},
    {"id": "n02", "category": "numeric_range", "kind": "names",
     "question": "Which candidates have between 3 and 6 years of experience, inclusive?",
     "expected": lambda D: _names(D, lambda d: 3 <= _yoe(d) <= 6)},
    {"id": "n03", "category": "numeric_range", "kind": "names",
     "question": "Which candidates have fewer than 3 years of experience?",
     "expected": lambda D: _names(D, lambda d: _yoe(d) < 3)},
    {"id": "n04", "category": "numeric_range", "kind": "names",
     "question": "Which senior-level candidates have at least 8 years of experience?",
     "expected": lambda D: _names(D, lambda d: d.get("seniority_level") == "senior" and _yoe(d) >= 8)},
    {"id": "n05", "category": "numeric_range", "kind": "names",
     "question": "Which candidates know AWS and have at most 5 years of experience?",
     "expected": lambda D: _names(D, lambda d: _has_all(d, "skills", "aws") and _yoe(d) <= 5)},
    # complete counts / aggregations
    {"id": "c01", "category": "count", "kind": "number",
     "question": "How many candidates are in this collection?",
     "expected": lambda D: len(D)},
    {"id": "c02", "category": "count", "kind": "number",
     "question": "How many candidates list Python as a skill?",
     "expected": lambda D: sum(1 for d in D if _has_all(d, "skills", "python"))},
    {"id": "c03", "category": "aggregation", "kind": "number",
     "question": "What is the average years of experience across all candidates? Give one decimal place.",
     "expected": lambda D: round(sum(_yoe(d) for d in D) / len(D), 1)},
    {"id": "c04", "category": "aggregation", "kind": "mapping",
     "question": "How many candidates are there at each education level?",
     "expected": lambda D: _count_by(D, "education_level")},
    {"id": "c05", "category": "aggregation", "kind": "number",
     "question": "What is the total years of experience of all candidates who know Kubernetes?",
     "expected": lambda D: int(sum(_yoe(d) for d in D if _has_all(d, "skills", "kubernetes")))},
    # ranking / comparison
    {"id": "r01", "category": "ranking", "kind": "ordered",
     "question": "Who are the three most experienced candidates by years of experience?",
     "expected": lambda D: _top_by_yoe(D, 3)},
    {"id": "r02", "category": "ranking", "kind": "ordered",
     "question": "Which candidate has the fewest years of experience?",
     "expected": lambda D: _top_by_yoe(D, 1, reverse=False)},
    {"id": "r03", "category": "comparison", "kind": "mapping",
     "question": "Compare Aarav Mehta and Oliver Schmidt: years of experience, seniority level and education level.",
     "expected": lambda D: {d["candidate_name"]: {k: d.get(k) for k in ("years_of_experience", "seniority_level", "education_level")}
                            for d in D if d["candidate_name"] in ("Aarav Mehta", "Oliver Schmidt")}},
    {"id": "r04", "category": "ranking", "kind": "ordered",
     "question": "Among candidates who know Python, who has the most years of experience?",
     "expected": lambda D: _top_by_yoe([d for d in D if _has_all(d, "skills", "python")], 1)},
]

TEXT: List[Dict[str, Any]] = [
    {"id": "p01", "category": "passage", "kind": "facts",
     "question": "What did Daniel Okafor build at Ridgeway Telecom?",
     "facts": ["Billing and provisioning services", "written in Java", "for a mobile network"]},
    {"id": "p02", "category": "passage", "kind": "facts",
     "question": "What scale does the card-authorisation platform Daniel Okafor architects handle?",
     "facts": ["4,000 transactions per second"]},
    {"id": "p03", "category": "passage", "kind": "facts",
     "question": "What did Fatima Sheikh do before moving into data work?",
     "facts": ["Chemistry teacher at Crescent Valley High School", "taught grades 9-12", "ran the science olympiad programme"]},
    {"id": "s01", "category": "multi_doc_synthesis", "kind": "facts",
     "question": "Which candidates have built RAG or LLM applications, and what did each build?",
     "facts": ["Aarav Mehta: LLM-based retrieval and RAG assistants at Lumora Commerce",
               "Kenji Watanabe: trains/evaluates LLMs (RLHF, long-context retrieval); RAG QA prototype",
               "Oliver Schmidt: ML platform deploying LLM support models",
               "Arjun Malhotra: RAG and agent workflows over legal contracts",
               "Siddharth Rao: LLM support-assistant program; advises on RAG architecture",
               "Tanvi Joshi: RAG chatbots over help-centre articles"]},
    {"id": "s02", "category": "multi_doc_synthesis", "kind": "facts",
     "question": "Which candidates have changed careers from a non-technical role, and what were they before?",
     "facts": ["Fatima Sheikh: chemistry teacher before data analyst / junior data scientist"]},
    {"id": "h01", "category": "hybrid", "kind": "facts",
     "question": "Among candidates with at least 7 years of experience, who explicitly leads or runs a team in their current role, and which team?",
     "facts": ["Aarav Mehta (8): leads the team building LLM retrieval/RAG assistants",
               "Priya Raghunathan (12): leads a data science team (patient-flow/readmission models)",
               "Oliver Schmidt (10): leads the ML platform team",
               "Michael Brennan (18): runs a 120-person engineering organisation"],
     "acceptable_extras": ["Nadia Haddad (leads reliability / incident-response rotation)",
                           "Siddharth Rao (leads the LLM support-assistant program)",
                           "Lucas Fernandez (leads the Next.js rebuild)",
                           "Zoya Ahmed (leads pricing and promotions analytics)"],
     "must_not_include": ["anyone with fewer than 7 years of experience, e.g. Tanvi Joshi, Kenji Watanabe, Grace Liu"]},
    {"id": "h02", "category": "hybrid", "kind": "facts",
     "question": "Which candidates who know Kubernetes moved services or infrastructure onto Kubernetes, according to their work history?",
     "facts": ["Aarav Mehta: moved model training to Kubernetes with MLflow tracking",
               "Daniel Okafor: led the migration from a monolith to Go/Java services on Kubernetes with Kafka",
               "Michael Brennan: led the platform consolidation onto AWS and Kubernetes (cost down 31%)",
               "Nadia Haddad: moved 40 services to Kubernetes with Helm charts and Terraform"],
     "acceptable_extras": ["Rahul Verma: runs the Kubernetes platform (operates it; not stated as a migration)"]},
]


def load_tables(gt_path: Path, extracted_path: Path) -> Dict[str, List[Doc]]:
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    ex = json.loads(extracted_path.read_text(encoding="utf-8"))
    truth, extracted = [], []
    for r in gt["resumes"]:
        truth.append(dict(r["fields"], file=r["file"].split("/")[-1]))
        meta = (ex.get(r["file"].split("/")[-1]) or {}).get("metadata") or {}
        extracted.append(dict(meta, candidate_name=meta.get("candidate_name") or r["fields"]["candidate_name"],
                              file=r["file"].split("/")[-1]))
    return {"truth": truth, "extracted": extracted}


def build_question_set(gt_path: Path, extracted_path: Path) -> List[Dict[str, Any]]:
    tables = load_tables(gt_path, extracted_path)
    out = []
    for q in STRUCTURED:
        exp = q["expected"](tables["extracted"])
        tru = q["expected"](tables["truth"])
        row = {k: v for k, v in q.items() if k != "expected"}
        row["expected"] = exp
        if tru != exp:
            row["truth_differs"] = tru
        out.append(row)
    out.extend(dict(q) for q in TEXT)
    return out


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--extracted", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    qs = build_question_set(Path(a.ground_truth), Path(a.extracted))
    Path(a.out).write_text(json.dumps({"questions": qs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {len(qs)} questions -> {a.out}\n")
