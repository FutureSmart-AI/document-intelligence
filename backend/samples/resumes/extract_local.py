"""Re-run the worker's document-level extraction on the 24 sample resumes, locally, and score it.

This is the fast loop for extraction accuracy: it calls the worker's own code, so a change to
the extraction prompt or to a field definition can be measured before it is deployed.

  - text:    celery_utils.processing.file_utils.read_pdf_to_plain_text (the text_based reader)
  - prompt:  celery_utils.enrichment.prompts.create_enrichment_prompt, as strategy 6 calls it
             (doc-level fields only, the collection's enrichment instructions appended)
  - schema:  celery_utils.enrichment.schema_builder.create_dynamic_pydantic_model
  - model:   the platform-key model the worker would build (get_enrichment_llm)

Two inputs come from the production database because they live there, not in git:
  --admin-prompt-file  the active `enrichment_without_chunks` admin prompt (plain text)
  --fields-jsonl       the collection's document-level field_configurations, one JSON object per line
                       (field_name, description, field_type, sample_values, restrict_to_choices,
                       max_items, schema, ...). With --template-descriptions, each field's
                       description is replaced by the current `resume` template's, which is what a
                       re-provisioned sample would store.

Mechanical fields are scored exactly against ground_truth.json (alternatives honoured). Every run
of every resume is written to --out with the extracted values, so a miss can be read, not guessed.

  python extract_local.py --workers-root ../../../workers --env-file .wenv \
      --admin-prompt-file prompt.txt --fields-jsonl fields.jsonl --runs 3 --out run.json
"""
from __future__ import annotations

import argparse
import inspect
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Per sample: its directory, the ground-truth list key, its field template, and the fields compared exactly.
SAMPLES = {
    "resumes": (HERE, "resumes", "resume",
                ["candidate_email", "seniority_level", "education_level", "years_of_experience"]),
    "invoices": (HERE.parent / "invoices", "invoices", "invoice",
                 ["invoice_number", "total_amount", "currency", "issue_date", "due_date", "payment_terms"]),
}


def load_env(path: str) -> None:
    for line in Path(path).read_text(encoding="utf8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, json.loads(v))


def exact_ok(field: str, got, gt: dict) -> bool:
    accepted = [gt["fields"][field]] + list(gt.get("alternatives", {}).get(field, []))
    if field == "candidate_email" and isinstance(got, str):
        got = got.lower()
    return got in accepted


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", choices=sorted(SAMPLES), default="resumes")
    ap.add_argument("--workers-root", required=True)
    ap.add_argument("--env-file", required=True, help="KEY=json-string lines; needs OPENAI_API_KEY")
    ap.add_argument("--admin-prompt-file", required=True)
    ap.add_argument("--fields-jsonl", required=True)
    ap.add_argument("--template-descriptions", action="store_true")
    ap.add_argument("--knowledge-prompt", default=None, help="default: the collection default instructions")
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--model", default="gpt-5.4-nano")
    ap.add_argument("--reasoning-effort", default=None, help="override the registry effort for --model")
    ap.add_argument("--reference-date", default=None, help="YYYY-MM-DD; only used if the worker supports it")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", nargs="*", help="resume ids, e.g. r03 r08")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    load_env(args.env_file)
    os.environ.setdefault("MODE", "LOCAL")
    sys.path.insert(0, str(Path(args.workers_root).resolve()))
    sys.path.insert(0, str(HERE.parent.parent))  # backend/, for the template definitions
    sample_dir, gt_key, template_name, EXACT = SAMPLES[args.sample]

    from celery_utils.enrichment import prompts as worker_prompts
    from celery_utils.enrichment.llm_factory import get_enrichment_llm
    from celery_utils.enrichment.schema_builder import create_dynamic_pydantic_model
    from celery_utils.processing.file_utils import read_pdf_to_plain_text
    from services.pydantic_models_db import FieldConfiguration
    from utils.config.enrichment_config import DEFAULT_ENRICHMENT_PROMPT, DOMAIN_FIELD_TEMPLATES

    admin_prompt = Path(args.admin_prompt_file).read_text(encoding="utf8")
    worker_prompts.get_admin_prompts_sync = lambda prompt_type=None: [{"prompt_content": admin_prompt}]

    template = {f["field_name"]: f for f in DOMAIN_FIELD_TEMPLATES[template_name]}
    configs = []
    for line in Path(args.fields_jsonl).read_text(encoding="utf8").splitlines():
        if not line.startswith("{"):
            continue
        row = json.loads(line)
        if args.template_descriptions and row["field_name"] in template:
            row["description"] = template[row["field_name"]]["description"]
        configs.append(FieldConfiguration(knowledge_id="local", **{k: v for k, v in row.items()
                                                                    if k in FieldConfiguration.model_fields}))
    Model = create_dynamic_pydantic_model("DynamicOutputModel", {"enable_chunking": True, "chunk_size": 2000}, configs)

    kw = {"existing_metadata": None, "include_chunks": False,
          "knowledge_prompt": args.knowledge_prompt or DEFAULT_ENRICHMENT_PROMPT}
    supports_date = "reference_date" in inspect.signature(worker_prompts.create_enrichment_prompt).parameters
    if supports_date and args.reference_date:
        kw["reference_date"] = date.fromisoformat(args.reference_date)
    system_prompt = worker_prompts.create_enrichment_prompt(**kw)

    if args.reasoning_effort:  # measure a different effort for the same registry model
        from celery_utils.enrichment import llm_factory
        llm_factory.ENRICHMENT_MODELS[args.model] = {**llm_factory.ENRICHMENT_MODELS[args.model],
                                                      "reasoning_effort": args.reasoning_effort}
    llm, _ = get_enrichment_llm(None, args.provider, args.model, 0.0, force_platform_key=True)
    structured = llm.with_structured_output(Model, include_raw=True)

    gt = json.loads((sample_dir / "ground_truth.json").read_text(encoding="utf8"))
    resumes = [r for r in gt[gt_key] if not args.only or r["id"] in args.only]
    texts = {}
    for r in resumes:  # the same text process_and_index_file hands to enrich_document
        read = read_pdf_to_plain_text(io.BytesIO((sample_dir / r["file"]).read_bytes()))
        parts = [f"File Name: {Path(r['file']).name}\n\n"]
        if read.get("extra_info"):
            parts.append(f"Extra Info: {read['extra_info']}\n\n")
        parts.append(f"Extracted Text: {read['content']}\n")
        texts[r["id"]] = "".join(parts)

    def one(job):
        r, run = job
        try:
            out = structured.invoke([{"role": "system", "content": system_prompt},
                                     {"role": "user", "content": texts[r["id"]]}])
            values = out["parsed"].model_dump(mode="json")
            usage = dict(getattr(out["raw"], "usage_metadata", None) or {})
        except Exception as e:  # recorded, never swallowed
            return {"id": r["id"], "run": run, "error": f"{type(e).__name__}: {e}"}
        return {"id": r["id"], "run": run, "name": r["fields"].get("candidate_name") or r["fields"].get("invoice_number"),
                "exact": {f: {"expected": r["fields"][f], "got": values.get(f), "ok": exact_ok(f, values.get(f), r)}
                          for f in EXACT},
                "usage": usage, "values": values}

    jobs = [(r, i + 1) for i in range(args.runs) for r in resumes]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, jobs))

    summary = {f: {"ok": sum(1 for x in results if "exact" in x and x["exact"][f]["ok"]),
                   "of": sum(1 for x in results if "exact" in x)} for f in EXACT}
    misses = [{"id": x["id"], "run": x["run"], "field": f, "expected": x["exact"][f]["expected"],
               "got": x["exact"][f]["got"]}
              for x in results if "exact" in x for f in EXACT if not x["exact"][f]["ok"]]
    errors = [x for x in results if "error" in x]
    report = {"sample": args.sample, "model": f"{args.provider}/{args.model}", "runs": args.runs, "reference_date_in_prompt": supports_date,
              "template_descriptions": args.template_descriptions, "summary": summary, "misses": misses,
              "errors": errors, "system_prompt": system_prompt, "results": results}
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf8")
    tok = [x["usage"] for x in results if x.get("usage")]
    mean = lambda k: round(sum(u.get(k, 0) for u in tok) / max(len(tok), 1))
    print(json.dumps({"summary": summary, "errors": len(errors), "reference_date_in_prompt": supports_date,
                      "mean_input_tokens": mean("input_tokens"), "mean_output_tokens": mean("output_tokens")}))
    for m in misses:
        print(f"  MISS {m['id']} run{m['run']} {m['field']}: expected {m['expected']!r} got {m['got']!r}")


if __name__ == "__main__":
    main()
