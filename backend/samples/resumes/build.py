"""Build the synthetic resume sample collection from source/candidates.py.

Outputs (all committed):
  html/<id>-<slug>.html     the rendered resume source, one of six layouts
  pdf/<id>-<slug>.pdf       printed with headless Chrome
  ground_truth.json         per resume, the values the `resume` template should extract
  questions.json            suggested questions with answers computed from ground_truth.json

Usage:
  python build.py                       # everything
  python build.py --no-pdf              # skip Chrome (html + json only)
  CHROME=/path/to/chrome python build.py
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "source"))
from candidates import CANDIDATES  # noqa: E402

REFERENCE_YEAR = 2026
TEMPLATE_FIELDS = [
    "candidate_name", "candidate_email", "current_role", "seniority_level", "years_of_experience",
    "skills", "education_level", "certifications", "locations", "industries", "languages",
    "work_experience", "education",
]

# ---------------------------------------------------------------- rendering

BASE_CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; font-size: 10.5pt; line-height: 1.38; color: #1d1d1f; }
h1 { margin: 0; line-height: 1.1; }
h2 { font-size: 10.5pt; letter-spacing: .08em; text-transform: uppercase; margin: 14pt 0 5pt; }
ul { margin: 3pt 0 0 0; padding-left: 14pt; }
li { margin: 1.5pt 0; }
.role { margin-bottom: 8pt; break-inside: avoid; }
.role-head { display: flex; justify-content: space-between; gap: 8pt; }
.title { font-weight: 700; }
.dates { white-space: nowrap; color: #555; }
.org { color: #444; }
.about { color: #777; font-style: italic; }
aside .contact-list li { white-space: nowrap; font-size: 8.4pt; }
.chips span { display: inline-block; margin: 0 3pt 3pt 0; padding: 1pt 5pt; border-radius: 3pt; }
"""

LAYOUT_CSS = {
    "classic": """
body { font-family: Georgia, 'Times New Roman', serif; padding: 18mm 20mm; }
header { text-align: center; border-bottom: 1.5pt solid #222; padding-bottom: 8pt; }
h1 { font-size: 22pt; font-weight: 400; letter-spacing: .04em; }
.headline { font-size: 12pt; margin-top: 3pt; }
.contact { color: #444; margin-top: 3pt; }
h2 { border-bottom: .6pt solid #999; padding-bottom: 2pt; }
""",
    "compact": """
body { font-family: 'Segoe UI', Arial, sans-serif; padding: 12mm 14mm; font-size: 9.8pt; }
header { display: flex; justify-content: space-between; align-items: flex-end; }
h1 { font-size: 20pt; }
.headline { color: #0b6b5d; font-weight: 600; }
.contact { text-align: right; color: #444; }
h2 { color: #0b6b5d; margin-top: 10pt; border-top: 1pt solid #0b6b5d; padding-top: 4pt; }
.chips span { background: #e5f2ef; }
""",
    "header_band": """
body { font-family: Calibri, 'Segoe UI', Arial, sans-serif; }
header { background: #1f3a5f; color: #fff; padding: 14mm 18mm 9mm; }
header .contact { color: #d7e2f0; }
h1 { font-size: 26pt; }
.headline { font-size: 13pt; color: #f2c14e; margin-top: 3pt; }
main { padding: 4mm 18mm 14mm; }
h2 { color: #1f3a5f; }
.chips span { background: #e8eef6; }
""",
    "academic": """
body { font-family: 'Palatino Linotype', Palatino, Georgia, serif; padding: 20mm 22mm; }
h1 { font-size: 20pt; font-variant: small-caps; }
.headline { font-style: italic; }
.contact { color: #444; }
h2 { font-variant: small-caps; text-transform: none; letter-spacing: .03em; font-size: 13pt; }
""",
    "sidebar_left": """
body { font-family: 'Segoe UI', Arial, sans-serif; display: grid; grid-template-columns: 64mm 1fr; min-height: 297mm; }
aside { background: #f1ede6; padding: 16mm 7mm 12mm 9mm; }
main { padding: 16mm 12mm 12mm 9mm; }
h1 { font-size: 21pt; }
.headline { color: #8a4b22; font-weight: 600; margin: 3pt 0 6pt; }
aside h2 { color: #8a4b22; }
.chips span { background: #fff; border: .6pt solid #d4c7b4; }
""",
    "sidebar_right": """
body { font-family: Verdana, Arial, sans-serif; font-size: 9.6pt; display: grid; grid-template-columns: 1fr 66mm; min-height: 297mm; }
main { padding: 15mm 9mm 12mm 14mm; }
aside { background: #23282f; color: #e8e8e8; padding: 15mm 8mm 12mm 7mm; }
aside .about, aside .org { color: #bbb; }
h1 { font-size: 21pt; color: #23282f; }
.headline { color: #c2410c; font-weight: 700; margin: 3pt 0 4pt; }
aside h2 { color: #fb923c; }
main h2 { color: #c2410c; }
.chips span { background: #3a414b; }
""",
}


def esc(s) -> str:
    return html.escape(str(s))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def years(r) -> str:
    return f"{r['start']} - {'Present' if r['end'] is None else r['end']}"


def contact_lines(c) -> list[str]:
    out = [c["email"]]
    if c.get("phone"):
        out.append(c["phone"])
    loc = c["locations"][0]
    extra = c["locations"][1:]
    out.append(loc + (f" (open to {', '.join(extra)})" if extra else ""))
    return out


def experience_html(c) -> str:
    parts = []
    for r in c["experience"]:
        bullets = "".join(f"<li>{esc(b)}</li>" for b in r["bullets"])
        parts.append(
            f"<div class='role'><div class='role-head'><span class='title'>{esc(r['title'])}</span>"
            f"<span class='dates'>{esc(years(r))}</span></div>"
            f"<div class='org'>{esc(r['company'])}, {esc(r['location'])} <span class='about'>- {esc(r['about'])}</span></div>"
            f"<ul>{bullets}</ul></div>")
    return "<h2>Experience</h2>" + "".join(parts)


def education_html(c) -> str:
    rows = "".join(
        f"<div class='role'><div class='title'>{esc(e['degree'])}, {esc(e['field'])}</div>"
        f"<div class='org'>{esc(e['institution'])}, {e['year']}</div></div>" for e in c["education"])
    return "<h2>Education</h2>" + rows


def list_section(title, items, chips=False) -> str:
    if not items:
        return ""
    if chips:
        return f"<h2>{esc(title)}</h2><div class='chips'>" + "".join(f"<span>{esc(i)}</span>" for i in items) + "</div>"
    return f"<h2>{esc(title)}</h2><ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"


def extra_html(c) -> str:
    return "".join(list_section(t, items) for t, items in c.get("extra_sections", []))


def languages_display(c) -> list[str]:
    return [l.capitalize() for l in c["languages"]]


def render(c) -> str:
    layout = c["layout"]
    head = (f"<h1>{esc(c['name'])}</h1><div class='headline'>{esc(c['headline'])}</div>")
    contact = "<div class='contact'>" + " &middot; ".join(esc(x) for x in contact_lines(c)) + "</div>"
    summary = f"<h2>Profile</h2><p>{esc(c['summary'])}</p>"
    skills = list_section("Skills", c["skills"], chips=True)
    certs = list_section("Certifications", c["certifications"])
    langs = list_section("Languages", languages_display(c))
    exp, edu, extra = experience_html(c), education_html(c), extra_html(c)

    if layout in ("sidebar_left", "sidebar_right"):
        side_contact = "<h2>Contact</h2><ul class='contact-list'>" + "".join(f"<li>{esc(x)}</li>" for x in contact_lines(c)) + "</ul>"
        aside = f"<aside>{'' if layout == 'sidebar_right' else head}{side_contact}{skills}{certs}{langs}</aside>"
        main = f"<main>{head if layout == 'sidebar_right' else ''}{summary}{exp}{extra}{edu}</main>"
        body = aside + main if layout == "sidebar_left" else main + aside
    elif layout == "header_band":
        body = f"<header>{head}{contact}</header><main>{summary}{exp}{extra}{edu}{skills}{certs}{langs}</main>"
    elif layout == "academic":
        body = f"<header>{head}{contact}</header>{summary}{edu}{exp}{extra}{skills}{certs}{langs}"
    elif layout == "compact":
        body = (f"<header><div>{head}</div>{contact}</header>{summary}{skills}{exp}{extra}{edu}{certs}{langs}")
    else:  # classic
        body = f"<header>{head}{contact}</header>{summary}{exp}{extra}{edu}{skills}{certs}{langs}"

    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{esc(c['name'])} - Resume</title>"
            f"<style>{BASE_CSS}{LAYOUT_CSS[layout]}</style></head><body>{body}</body></html>")


def find_chrome() -> str | None:
    for cand in [os.environ.get("CHROME"),
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chrome")]:
        if cand and Path(cand).exists():
            return cand
    return None


def print_pdf(chrome: str, src: Path, dst: Path) -> None:
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={dst}", src.resolve().as_uri()],
                   check=True, capture_output=True, timeout=120)


# ------------------------------------------------------------ ground truth

def ground_truth(c, filename: str) -> dict:
    current = next(r for r in c["experience"] if r["end"] is None) if any(r["end"] is None for r in c["experience"]) \
        else c["experience"][0]
    fields = {
        "candidate_name": c["name"],
        "candidate_email": c["email"],
        "current_role": current["title"],
        "seniority_level": c["seniority_level"],
        "years_of_experience": c["years_of_experience"],
        "skills": c["skills"],
        "education_level": c["education_level"],
        "certifications": c["certifications"],
        "locations": c["locations"],
        "industries": c["industries"],
        "languages": c["languages"],
        "work_experience": [
            {"company": r["company"], "title": r["title"], "start_year": r["start"], "end_year": r["end"],
             "summary": r["summary"]} for r in c["experience"]],
        "education": [
            {"institution": e["institution"], "degree": e["degree"], "field_of_study": e["field"],
             "graduation_year": e["year"]} for e in c["education"]],
    }
    assert list(fields) == TEMPLATE_FIELDS
    assert len(c["skills"]) <= 10 and len(c["locations"]) <= 3 and len(c["industries"]) <= 3
    return {"id": c["id"], "file": filename, "layout": c["layout"], "fields": fields,
            "alternatives": c.get("alternatives", {}), "notes": c.get("notes")}


# --------------------------------------------------------------- questions

def build_questions(gt: list[dict]) -> list[dict]:
    F = {g["id"]: g["fields"] for g in gt}
    name = lambda i: F[i]["candidate_name"]  # noqa: E731
    has = lambda i, s: s in F[i]["skills"]   # noqa: E731

    q = []

    py_aws = sorted((i for i in F if has(i, "python") and has(i, "aws") and F[i]["years_of_experience"] >= 5),
                    key=lambda i: -F[i]["years_of_experience"])
    q.append({
        "id": "q01", "question": "Which candidates know Python and AWS and have at least 5 years of experience?",
        "answer_source": "structured_fields",
        "why": "Exact filter on skills (contains python AND aws) plus years_of_experience >= 5.",
        "expected": {"candidates": [{"name": name(i), "years_of_experience": F[i]["years_of_experience"]} for i in py_aws],
                     "count": len(py_aws)},
        "near_misses": [{"name": name(i), "reason": r} for i, r in [
            ("r03", "AWS but no Python skill listed"), ("r12", "AWS but no Python skill listed"),
            ("r02", "Python but Azure, not AWS"), ("r24", "Python, no AWS, 2 years")]],
    })

    rag = [i for i in F if any(s in F[i]["skills"] for s in ("rag", "langchain", "llamaindex", "llms", "llmops", "rlhf", "llm fine-tuning", "llm evaluation", "prompt engineering"))]
    q.append({
        "id": "q02", "question": "Who has hands-on experience building RAG or LLM applications? Cite what they built.",
        "answer_source": "document_text",
        "why": "The evidence (what was built) lives in role bullets and projects; skills tags alone miss Oliver's LLM support models and over-include nobody.",
        "expected": {"candidates": [name(i) for i in rag], "count": len(rag),
                     "evidence_hints": {
                         name("r01"): "RAG assistants for 2,000+ support agents; hallucination 11% -> 3%",
                         name("r07"): "retrieval-augmented QA prototype; long-context retrieval eval suite; RLHF",
                         name("r10"): "CampusQA RAG chatbot over college notes (student project)",
                         name("r14"): "deploys LLM support models; llmops",
                         name("r16"): "RAG over 1.2M legal contracts with clause citations; accuracy 71% -> 88%",
                         name("r22"): "RAG over 50k policy documents deflecting 35% of tickets; RAG consulting",
                         name("r24"): "RAG chatbots over help-centre articles with LangChain + FastAPI",
                     }},
    })

    q.append({
        "id": "q03",
        "question": "Compare the strongest candidates for a Senior AI Engineer role (production LLM/RAG systems, 5+ years). Who would you shortlist and why?",
        "answer_source": "hybrid",
        "why": "Filter on fields (years, rag/llm skills), then judge from role bullets (production scale, evaluation, leadership).",
        "expected": {
            "shortlist": [name("r16"), name("r22"), name("r01")],
            "rationale": {
                name("r16"): "Current Senior AI Engineer; RAG + agents over 1.2M contracts; built LLM eval pipeline; 7 yrs.",
                name("r22"): "13 yrs; leads LLM support assistant (RAG over 50k docs); RAG consulting; possibly over-senior (staff).",
                name("r01"): "8 yrs; leads RAG assistants team; hallucination eval harness; LoRA fine-tuning.",
            },
            "should_not_shortlist": {
                name("r07"): "strong LLM research but 4 years and research-focused (acceptable as an honourable mention)",
                name("r24"): "RAG work but only 2 years",
                name("r10"): "fresher; RAG only as a student project",
            },
        },
    })

    sen_order = ["intern", "junior", "mid_level", "senior", "lead", "principal", "executive"]
    sen = Counter(F[i]["seniority_level"] for i in F)
    q.append({
        "id": "q04", "question": "How are candidates distributed by seniority level? Show it as a chart.",
        "answer_source": "structured_fields", "chart": "bar",
        "why": "Aggregate count over the seniority_level facet across all 24 documents.",
        "expected": {"distribution": {k: sen.get(k, 0) for k in sen_order if sen.get(k)}, "total": len(F)},
    })

    buckets = [("0-2", 0, 2), ("3-5", 3, 5), ("6-9", 6, 9), ("10-14", 10, 14), ("15+", 15, 99)]
    dist = {b: sum(1 for i in F if lo <= F[i]["years_of_experience"] <= hi) for b, lo, hi in buckets}
    yoe = [F[i]["years_of_experience"] for i in F]
    q.append({
        "id": "q05", "question": "What is the distribution of years of experience across candidates? Show a chart.",
        "answer_source": "structured_fields", "chart": "histogram",
        "why": "Aggregate over the numeric years_of_experience field.",
        "expected": {"buckets": dist, "min": min(yoe), "max": max(yoe),
                     "mean": round(sum(yoe) / len(yoe), 1), "median": statistics.median(yoe)},
        "known_ambiguity": "Fatima Sheikh counts as 10 (total professional) or 3 (tech only); the 6-9 / 10-14 / 0-2 buckets shift by one if 3 is used.",
    })

    skills = Counter(s for i in F for s in F[i]["skills"])
    top = [(s, n) for s, n in sorted(skills.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 3]
    q.append({
        "id": "q06", "question": "What are the most common skills across all candidates? Chart the top 10.",
        "answer_source": "structured_fields", "chart": "bar",
        "why": "Aggregate value counts over the skills List[str] facet.",
        "expected": {"top_skills": [{"skill": s, "count": n} for s, n in top[:10]],
                     "all_skills_with_3_plus": dict(top)},
    })

    blr = [name(i) for i in F if "Bengaluru" in F[i]["locations"]]
    q.append({
        "id": "q07", "question": "Which candidates are based in or open to Bengaluru?",
        "answer_source": "structured_fields",
        "why": "Filter on the locations facet.",
        "expected": {"candidates": blr, "count": len(blr)},
    })

    adv = [(name(i), F[i]["education_level"]) for i in F if F[i]["education_level"] in ("masters", "phd")]
    q.append({
        "id": "q08", "question": "Which candidates hold a master's degree or a PhD?",
        "answer_source": "structured_fields",
        "why": "Filter on education_level in {masters, phd}. MBAs count as masters.",
        "expected": {"candidates": [{"name": n, "education_level": l} for n, l in adv], "count": len(adv)},
    })

    aws_cert = [(name(i), [c for c in F[i]["certifications"] if c.startswith("AWS")]) for i in F
                if any(c.startswith("AWS") for c in F[i]["certifications"])]
    q.append({
        "id": "q09", "question": "Who holds an AWS certification, and which one?",
        "answer_source": "structured_fields",
        "why": "Filter on the certifications facet.",
        "expected": {"candidates": [{"name": n, "certifications": c} for n, c in aws_cert], "count": len(aws_cert)},
    })

    q.append({
        "id": "q10", "question": "Has anyone switched careers into tech or taken a career break? Explain each case.",
        "answer_source": "document_text",
        "why": "Career changes and gaps are narrative facts in summaries and extra sections, not extracted fields.",
        "expected": {"cases": {
            name("r08"): "career switcher: chemistry teacher 2015-2022, data science bootcamp 2022-2023, then analytics/data science",
            name("r13"): "career break Jul 2021 - Jun 2022 to care for a family member",
            name("r23"): "gap year 2021-2022 volunteering in Peru after graduation",
            name("r06"): "moved from software engineering to product management via a full-time MBA (2015-2017) - acceptable, not required",
        }, "required": [name("r08"), name("r13"), name("r23")]},
    })

    hc = [name(i) for i in F if "healthcare" in F[i]["industries"]]
    q.append({
        "id": "q11", "question": "Which candidates have worked in healthcare?",
        "answer_source": "structured_fields",
        "why": "Filter on the industries facet.",
        "expected": {"candidates": hc, "count": len(hc)},
    })

    k8s = sorted((i for i in F if has(i, "kubernetes")), key=lambda i: -F[i]["years_of_experience"])
    q.append({
        "id": "q12", "question": "List everyone with Kubernetes skills, with their current role and years of experience.",
        "answer_source": "structured_fields",
        "why": "Filter on skills, project current_role and years_of_experience into a table.",
        "expected": {"candidates": [{"name": name(i), "current_role": F[i]["current_role"],
                                     "years_of_experience": F[i]["years_of_experience"]} for i in k8s],
                     "count": len(k8s)},
    })
    return q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    html_dir, pdf_dir = HERE / "html", HERE / "pdf"
    html_dir.mkdir(exist_ok=True)
    pdf_dir.mkdir(exist_ok=True)
    chrome = None if args.no_pdf else find_chrome()
    if not args.no_pdf and not chrome:
        sys.exit("Chrome not found; set CHROME or pass --no-pdf")

    gt = []
    for c in CANDIDATES:
        base = f"{c['id']}-{slug(c['name'])}"
        src = html_dir / f"{base}.html"
        src.write_text(render(c), encoding="utf-8")
        if chrome:
            print_pdf(chrome, src, pdf_dir / f"{base}.pdf")
        gt.append(ground_truth(c, f"pdf/{base}.pdf"))

    meta = {"reference_date": f"{REFERENCE_YEAR}-10", "template": "resume",
            "fields": TEMPLATE_FIELDS, "count": len(gt),
            "synthetic": "All people, contacts, employers and schools are invented."}
    (HERE / "ground_truth.json").write_text(json.dumps({"meta": meta, "resumes": gt}, indent=2, ensure_ascii=False) + "\n",
                                            encoding="utf-8")
    qs = build_questions(gt)
    (HERE / "questions.json").write_text(json.dumps({"meta": {"computed_from": "ground_truth.json"}, "questions": qs},
                                                    indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"built {len(gt)} resumes, {len(qs)} questions, pdf={'yes' if chrome else 'no'}")


if __name__ == "__main__":
    main()
