"""Document Intelligence model evaluation: real HTTP API, real models, graded against ground truth.

    env --config NAME                  write the config's model settings as an env file for api + worker
                                       (host side; the compose overlay loads it; see README.md)
    prepare                            check the dataset (fixtures, computed expectations), the API and auth
    extract --config NAME --repeats N  N fresh resume + invoice collections through the API (full template
                                       config, PDF upload, wait), then grade every field of every document
    ask --config NAME --repeats N      every RAG case N times on the config's own collections
    report                             compare every config in the results directory

The API and worker must already run with the config under test (`env`, then restart them).
Each answer/document records the served models; a run whose models differ from the config,
or that fell back, fails (exit 1) after its records are written.
Secrets come from the environment / the stack's .env and are never printed or written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cases as casebook  # noqa: E402
import grade  # noqa: E402
import usage as telemetry  # noqa: E402

REPO = grade.REPO
API = os.getenv("MODEL_EVAL_API", "http://api:8000/api/knowledge")
SERVER_PROJECT = "document-intelligence-model-eval-server"
PROFILES = REPO / "deploy/model-profiles.json"
EXTRACTION_KEYS = ("ENRICHMENT_", "EXTRACTION_", "TRANSCRIPT_", "LLAMA_PARSE")
# Cases/documents graded in parallel. The same for every config, so latency stays comparable.
CONCURRENCY = int(os.getenv("MODEL_EVAL_CONCURRENCY", "4"))
WRITE_LOCK = threading.Lock()


# ── dataset / config identity ────────────────────────────────────────────────

def _bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    return raw if path.suffix.lower() == ".pdf" else raw.replace(b"\r\n", b"\n")


def sha256(paths: List[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(REPO).as_posix().encode())
        digest.update(_bytes(path))
    return digest.hexdigest()


def load_dataset() -> Dict[str, Any]:
    spec = json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))
    files = [REPO / f for f in spec["hashed_files"]]
    collections = {}
    for name, c in spec["collections"].items():
        records = json.loads((REPO / c["ground_truth"]).read_text(encoding="utf-8"))[c["records_key"]]
        pdfs = sorted((REPO / c["pdf_dir"]).glob("*.pdf"))
        files += pdfs
        collections[name] = dict(c, name=name, records=records, pdfs=pdfs)
    files = sorted(set(files), key=lambda p: p.relative_to(REPO).as_posix())
    digest = sha256(files)
    return {"spec": spec, "collections": collections, "sha256": digest,
            "files": [p.relative_to(REPO).as_posix() for p in files]}


def load_config(name: str) -> Dict[str, Any]:
    path = HERE / "configs" / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"unknown config {name}: {path.relative_to(REPO)} does not exist")
    config = json.loads(path.read_text(encoding="utf-8"))
    settings = {**profile_settings(config["profile"]), **config["settings"]}
    return {"name": name, "path": path, "sha256": sha256([path]), "profile": config["profile"],
            "settings": settings, "about": config.get("about"), "suites": config.get("suites", ["extraction", "rag"])}


def profile_settings(name: str) -> Dict[str, str]:
    """The profile's env settings exactly as deploy/configure.py resolves them (incl. `extends`)."""
    configure = grade.load_module("di_deploy_configure", REPO / "deploy/configure.py")
    catalog = json.loads(PROFILES.read_text(encoding="utf-8"))
    if hasattr(configure, "resolve_profile"):
        return dict(configure.resolve_profile(catalog, name).get("settings", {}))
    return dict(catalog["profiles"][name]["settings"])


def extraction_identity(config: Dict[str, Any]) -> str:
    """Configs with equal extraction settings produce equivalent collections (RAG may share them)."""
    keys = {k: v for k, v in sorted(config["settings"].items()) if k.startswith(EXTRACTION_KEYS)}
    return hashlib.sha256(json.dumps(keys, sort_keys=True).encode()).hexdigest()[:16]


def expected_models(config: Dict[str, Any]) -> Dict[str, Any]:
    s = config["settings"]
    by_op = {
        "query_refinement": s.get("RAG_CHAT_REFINED_QUERY_LLM_MODEL") or "gpt-5.4-nano",
        "retrieval_agent": s.get("RAG_CHAT_AGENT_LLM_MODEL") or "gpt-5.6-luna",
        "answer": s.get("RAG_CHAT_ANSWER_LLM_MODEL") or "gpt-5.6-luna",
        "retrieval_verifier": s.get("RAG_RETRIEVAL_VERIFIER_MODEL") or "gpt-5.4-nano",
        "follow_up": s.get("FOLLOW_UP_LLM_MODEL") or "gpt-5.4-nano",
        "embeddings": s.get("RAG_EVIDENCE_EMBED_MODEL") or "text-embedding-3-small",
        "chart_spec": s.get("CHART_EXTRACTION_MODEL") or "gpt-4o-mini",
        "chart_render": s.get("CHART_RENDER_MODEL") or "gpt-5.4-mini",
    }
    chart = by_op["chart_spec"]
    fallback = s.get("RAG_FALLBACK_MODEL") or "gpt-4.1-mini"
    return {"by_operation": by_op, "answer": by_op["answer"], "chart": chart, "rag_fallback": fallback,
            "any": set(by_op.values()) | {fallback}, "extraction": s.get("ENRICHMENT_MODEL"),
            "reasoning_effort": s.get("ENRICHMENT_REASONING_EFFORT") or "registry default"}


def git_sha() -> str:
    if os.getenv("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def read_env_file(path: Path) -> Dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def stack_env() -> Dict[str, str]:
    return {**read_env_file(Path(os.getenv("ENV_FILE", str(REPO / ".env")))), **os.environ}


# ── API client ───────────────────────────────────────────────────────────────

class Api:
    def __init__(self, base: str, key: str):
        import httpx
        self.c = httpx.Client(base_url=base.rstrip("/"), headers={"x-api-key": key},
                              timeout=httpx.Timeout(600.0, connect=30.0))

    def __call__(self, method: str, path: str, **kw):
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else None


def api_client() -> Api:
    env = stack_env()
    key = env.get("FASTAPI_SECRET_KEY")
    if not key:
        raise SystemExit("FASTAPI_SECRET_KEY not found (run deploy/configure.py; mount .env as ENV_FILE)")
    return Api(API, key)


def owner() -> str:
    return (stack_env().get("STANDALONE_OWNER_EMAIL") or "owner@example.test").strip().lower()


# ── env (host side) ──────────────────────────────────────────────────────────

def write_env(out: Path, name: str) -> None:
    config = load_config(name)
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# evals/model_eval config {name} (sha256 {config['sha256'][:12]}); profile {config['profile']}"]
    for k, v in config["settings"].items():
        if "\n" in str(v):
            raise SystemExit(f"{k}: multi-line value")
        lines.append(f"{k}={v}")
    lines.append(f"MODEL_EVAL_CONFIG={name}")
    (out / "model-config.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out / 'model-config.env'} for {name}: extraction {config['settings'].get('ENRICHMENT_MODEL')} "
          f"effort {config['settings'].get('ENRICHMENT_REASONING_EFFORT') or 'registry'}; "
          f"agent {config['settings'].get('RAG_CHAT_AGENT_LLM_MODEL')}; answer {config['settings'].get('RAG_CHAT_ANSWER_LLM_MODEL')}")


# ── prepare ──────────────────────────────────────────────────────────────────

def truth_tables(ds) -> Dict[str, Dict[str, list]]:
    return {n: {"truth": casebook.flat_truth(c["records"]), "extracted": casebook.flat_truth(c["records"])}
            for n, c in ds["collections"].items()}


def check_dataset(ds) -> List[str]:
    """Fixture integrity: every ground-truth record has its PDF, every case id in dataset.json is
    built by cases.py, and the invoice expectations recomputed from ground truth equal the
    committed questions.json (the same rules, so a drift in either shows here)."""
    problems = []
    for name, c in ds["collections"].items():
        have = {p.name for p in c["pdfs"]}
        want = {Path(r["file"]).name for r in c["records"]}
        if have != want:
            problems.append(f"{name}: PDFs {sorted(have ^ want)} do not match ground truth")
        for r in c["records"]:
            missing = [f for f in [*c["exact"], *c["judged"]] if f not in r["fields"]]
            if missing:
                problems.append(f"{name} {r['id']}: ground truth lacks {missing}")
    built = casebook.build_cases(truth_tables(ds), {n: c["records"] for n, c in ds["collections"].items()},
                                 casebook.load_invoice_questions())
    spec_ids = [c["id"] for c in ds["spec"]["suites"]["rag"]["cases"]]
    if sorted(spec_ids) != sorted(c["id"] for c in built):
        problems.append(f"rag case ids differ: spec-only {sorted(set(spec_ids) - {c['id'] for c in built})}, "
                        f"code-only {sorted({c['id'] for c in built} - set(spec_ids))}")
    committed = {q["id"]: q["expected"] for q in casebook.load_invoice_questions()}
    by_id = {c["id"]: c for c in built}
    for qid in casebook.INVOICE_QUESTIONS:
        if by_id[f"inv_{qid}"]["expected"] != committed[qid]:
            problems.append(f"invoices {qid}: recomputed expectation differs from questions.json")
    if by_id["ch_usd_vendors"]["expected_chart"] != committed["q05"]["totals_usd"]:
        problems.append("invoices q05: recomputed USD totals differ from questions.json")
    return problems


def prepare(out: Path) -> None:
    ds = load_dataset()
    problems = check_dataset(ds)
    if problems:
        raise SystemExit("dataset check failed:\n  " + "\n  ".join(problems))
    n_docs = sum(len(c["records"]) for c in ds["collections"].values())
    print(f"PASS dataset {ds['spec']['name']} {ds['spec']['version']} sha256 {ds['sha256'][:12]}: "
          f"{n_docs} documents, {len(ds['spec']['suites']['rag']['cases'])} RAG cases; invoice expectations "
          "recomputed from ground truth equal questions.json")
    api = api_client()
    api("GET", "/v1/validate", params={"owner_id": owner(), "knowledge_id": "model-eval-auth-check"})
    print(f"PASS api reachable at {API} and the stack's service key is accepted")
    out.mkdir(parents=True, exist_ok=True)
    (out / "state.json").write_text(json.dumps({"dataset_sha256": ds["sha256"], "prepared_at": now()}, indent=2) + "\n",
                                    encoding="utf-8")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── extract ──────────────────────────────────────────────────────────────────

def create_collection(api: Api, c: Dict[str, Any], label: str) -> str:
    name = f"model-eval {c['name']} {label}"
    kid = api("POST", "/v1", json={"owner_id": owner(), "collection_name": name})["knowledge_id"]
    # field_template seeds the template's fields with their full config (allowed values, list caps, definitions);
    # seed_optional_defaults=False keeps the generic defaults to the protected two (verify_hosted.py's create).
    api("PUT", f"/v1/{kid}/config", json={"collection_name": name, "field_template": c["template"],
                                          "seed_optional_defaults": False,
                                          "service_description": f"Document Intelligence model evaluation: synthetic {c['name']}."})
    return kid


def upload_all(api: Api, kid: str, pdfs: List[Path]) -> Dict[str, str]:
    ids = {}
    for pdf in pdfs:
        with pdf.open("rb") as fh:
            body = api("POST", f"/v1/{kid}/documents", files={"file": (pdf.name, fh, "application/pdf")})
        ids[pdf.name] = body.get("object_id") or (body.get("data") or {}).get("object_id")
    return ids


def list_docs(api: Api, kid: str) -> List[Dict[str, Any]]:
    body = api("GET", f"/v2/{kid}/documents", params={"page": 1, "page_size": 100})
    return body.get("document") or body.get("documents") or []


def wait_all(api: Api, kids: Dict[str, int], timeout_s: int) -> Dict[str, List[Dict[str, Any]]]:
    t0 = time.time()
    while True:
        docs = {kid: list_docs(api, kid) for kid in kids}
        pending = {kid: sum(1 for d in ds if d.get("status") not in ("completed", "failed")) + max(0, kids[kid] - len(ds))
                   for kid, ds in docs.items()}
        print(f"  {int(time.time() - t0)}s pending {sum(pending.values())} of {sum(kids.values())}", flush=True)
        if not any(pending.values()):
            return docs
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"timed out after {timeout_s}s waiting for indexing: {pending}")
        time.sleep(15)


def document_telemetry(api: Api, kid: str, object_id: str) -> Dict[str, Any]:
    detail = {}
    try:
        detail = api("GET", f"/v1/{kid}/documents/{object_id}") or {}
    except RuntimeError as e:
        detail = {"error": str(e)[:200]}
    t = telemetry.from_processing_telemetry(grade.find_fields(detail, ["processing_telemetry"]).get("processing_telemetry"))
    if t:
        return t
    rows = api("GET", f"/v1/{kid}/health/enrichment/documents",
               params={"object_id": object_id, "limit": 50}).get("documents") or []
    t = telemetry.from_enrichment_health(rows)
    if t:
        t["langsmith_run_ids"] = [r.get("langsmith_run_id") for r in rows if r.get("langsmith_run_id")]
    return t or {}


def extract(out: Path, name: str, repeats: int, upload: bool, timeout_s: int) -> None:
    ds = load_dataset()
    config = load_config(name)
    models = expected_models(config)
    api = api_client()
    label = f"{name} {datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    runs: List[Dict[str, Any]] = []
    for rep in range(repeats):
        rec = {"repeat": rep + 1, "collections": {}}
        for cname, c in ds["collections"].items():
            kid = create_collection(api, c, f"{label} r{rep + 1}")
            t0 = time.time()
            ids = upload_all(api, kid, c["pdfs"])
            rec["collections"][cname] = {"knowledge_id": kid, "uploads": ids, "upload_started": t0}
            print(f"{name} r{rep + 1} {cname}: {kid} uploaded {len(ids)}", flush=True)
        runs.append(rec)
    expected_counts = {v["knowledge_id"]: len(ds["collections"][k]["pdfs"]) for r in runs for k, v in r["collections"].items()}
    t_wait = time.time()
    listed = wait_all(api, expected_counts, timeout_s)
    print(f"{name}: all {sum(expected_counts.values())} documents settled in {int(time.time() - t_wait)}s", flush=True)

    docs: List[Dict[str, Any]] = []  # one entry per (repeat, document): values + telemetry, graded later
    for r in runs:
        for cname, info in r["collections"].items():
            c = ds["collections"][cname]
            by_name = {d.get("file_name") or d.get("name"): d for d in listed[info["knowledge_id"]]}
            fields = [*c["exact"], *c["judged"]]
            for rec in c["records"]:
                fname = Path(rec["file"]).name
                d = by_name.get(fname) or {}
                oid = d.get("object_id") or info["uploads"].get(fname)
                meta = {}
                if d.get("status") == "completed":
                    meta = api("GET", f"/v1/{info['knowledge_id']}/documents/{oid}/metadata")
                tel = document_telemetry(api, info["knowledge_id"], oid) if oid else {}
                docs.append({"repeat": r["repeat"], "collection": cname, "knowledge_id": info["knowledge_id"],
                             "doc_id": rec["id"], "file": fname, "object_id": oid, "status": d.get("status"),
                             "error": d.get("error_detail"), "values": grade.find_fields(meta, fields),
                             "telemetry": tel, "config_check": telemetry.extraction_config_verified(tel, models["extraction"])})
    state_path = out / f"collections-{name}.json"
    out.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"config": name, "extraction_identity": extraction_identity(config),
                                      "repeats": [{"repeat": r["repeat"], "collections": {k: v["knowledge_id"] for k, v in
                                                                                          r["collections"].items()}}
                                                  for r in runs]}, indent=2) + "\n", encoding="utf-8")

    from openai import OpenAI
    judge = OpenAI()
    records_path = out / f"extraction-{name}.jsonl"
    records_path.unlink(missing_ok=True)
    queue = defaultdict(list)
    for d in docs:
        queue[(d["collection"], d["doc_id"])].append(d)
    gt = {(n, r["id"]): r for n, c in ds["collections"].items() for r in c["records"]}
    rubric = {"resume": grade.resume_score().FIELD_RUBRIC, "invoice": grade.INVOICE_RUBRIC}

    def grade_one(d: Dict[str, Any]) -> Dict[str, Any]:
        c = ds["collections"][d["collection"]]
        rec = gt[(d["collection"], d["doc_id"])]
        passes = grade.judge_fields(judge, rubric[c["rubric"]], c["judged"], rec["fields"], d["values"], rec.get("notes"))
        g = grade.grade_document(c, rec, d["values"], passes)
        tel = d["telemetry"] or {}
        row = {"run_id": str(uuid.uuid4()), "suite": "extraction", "config": name, **{k: d[k] for k in (
            "repeat", "collection", "knowledge_id", "doc_id", "file", "object_id", "status", "error")},
               **{k: g[k] for k in ("fields_correct", "fields_total", "required_fields", "required_filled", "complete",
                                    "all_correct")},
               "fields": g["fields"], "telemetry": tel, "config_verified": d["config_check"]["verified"],
               "config_problems": d["config_check"]["problems"], "served_models": d["config_check"]["served"],
               "latency_ms": tel.get("total_ms") or tel.get("extraction_ms"),
               "estimated_cost_usd": tel.get("estimated_cost_usd"), "cost_complete": tel.get("cost_complete"),
               "cost_source": tel.get("source")}
        with WRITE_LOCK, records_path.open("a", encoding="utf-8") as fh:
            fh.write(telemetry.dumps(row) + "\n")
        print(f"{'OK  ' if row['all_correct'] else 'MISS'} {name} r{row['repeat']} {row['collection']} {row['doc_id']} "
              f"{row['fields_correct']}/{row['fields_total']} served={row['served_models']} verified={row['config_verified']}",
              flush=True)
        return row

    run_meta = run_metadata(ds, config, "extraction", repeats)
    run_meta["concurrency"] = CONCURRENCY
    run_meta["collections"] = [{"repeat": r["repeat"], **{k: v["knowledge_id"] for k, v in r["collections"].items()}}
                               for r in runs]
    if upload:
        from langsmith import Client, evaluate
        client = Client()
        dname = langsmith_dataset(client, ds, "extraction",
                                  [{"inputs": {"collection": c, "doc_id": r["id"], "file": Path(r["file"]).name},
                                    "outputs": {"fields": r["fields"], "alternatives": r.get("alternatives") or {}},
                                    "metadata": {"collection": c, "layout": r.get("layout")}}
                                   for c, coll in ds["collections"].items() for r in coll["records"]])

        def target(inputs: Dict[str, Any]) -> Dict[str, Any]:
            row = grade_one(queue[(inputs["collection"], inputs["doc_id"])].pop(0))
            return {"fields_correct": row["fields_correct"], "fields_total": row["fields_total"],
                    "field_accuracy": row["fields_correct"] / row["fields_total"], "complete": row["complete"],
                    "config_verified": row["config_verified"], "latency_ms": row["latency_ms"],
                    "estimated_cost_usd": row["estimated_cost_usd"], "repeat": row["repeat"],
                    "misses": {f: v.get("reason") or v.get("extracted") for f, v in row["fields"].items() if not v["correct"]}}

        results = evaluate(target, data=dname, evaluators=[metric(k) for k in (
            "field_accuracy", "complete", "config_verified", "latency_ms", "estimated_cost_usd")],
            experiment_prefix=f"{name}-extraction-{run_meta['git_sha'][:7]}", metadata=run_meta,
            max_concurrency=CONCURRENCY, num_repetitions=repeats, client=client)
        run_meta["langsmith_experiment"] = results.experiment_name
        run_meta["langsmith_dataset"] = dname
    else:
        with ThreadPoolExecutor(CONCURRENCY) as pool:
            list(pool.map(grade_one, docs))
    finish(out, records_path, run_meta, name, "extraction", len(docs))


# ── ask ──────────────────────────────────────────────────────────────────────

def collections_for(out: Path, config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The config's own collections (repeat 1), else those of a config with identical extraction settings."""
    own = out / f"collections-{config['name']}.json"
    candidates = [own] + sorted(p for p in out.glob("collections-*.json") if p != own)
    ident = extraction_identity(config)
    for p in candidates:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data["extraction_identity"] == ident:
                return {**data["repeats"][0]["collections"], "_from": data["config"]}
    return None


def extracted_table(api: Api, kid: str, c: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    fields = [*c["exact"], *c["judged"]]
    out = {}
    for d in list_docs(api, kid):
        if d.get("status") == "completed":
            meta = api("GET", f"/v1/{kid}/documents/{d['object_id']}/metadata")
            out[d.get("file_name") or d.get("name")] = grade.find_fields(meta, fields)
    return out


def chat(api: Api, kid: str, query: str, session_id: Optional[str]) -> Dict[str, Any]:
    body = {"query": query, "owner_id": owner(), "stream": False}
    if session_id:
        body["session_id"] = session_id
    import httpx
    t0 = time.perf_counter()
    try:
        r = api.c.post(f"/v1/{kid}/chat", json=body)
    except httpx.TimeoutException as e:
        # A hung turn is a FAILED answer, recorded as such and never retried away.
        ms = int((time.perf_counter() - t0) * 1000)
        return {"http_status": None, "client_latency_ms": ms, "timeout": True,
                "error": f"chat request timed out after {ms} ms ({type(e).__name__})", "body": {}}
    ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code >= 400:
        return {"http_status": r.status_code, "client_latency_ms": ms, "error": r.text[:300], "body": {}}
    return {"http_status": r.status_code, "client_latency_ms": ms, "body": r.json()}


def sources_of(body: Dict[str, Any]) -> List[str]:
    rs = ((((body.get("data") or {}).get("knowledge") or {}).get("relevant_sources") or {}).get("data")) or []
    return [s.get("file_name") or s.get("title") or s.get("object_id") for s in rs if isinstance(s, dict)]


def answer_draws_chart(text: str) -> bool:
    return any(t in (text or "") for t in ("█", "▇", "▆", "```mermaid", "xychart", "■■"))


def ask(out: Path, name: str, repeats: int, upload: bool, timeout_s: int) -> None:
    ds = load_dataset()
    config = load_config(name)
    models = expected_models(config)
    api = api_client()
    kids = collections_for(out, config)
    if kids is None:
        print(f"{name}: no extracted collections with these extraction settings in {out}; extracting once (repeat 1) "
              "so the RAG suite runs on collections this stack built with this config", flush=True)
        extract(out, name, 1, upload, timeout_s)
        kids = collections_for(out, config)
    tables = {}
    for cname, c in ds["collections"].items():
        ext = extracted_table(api, kids[cname], c)
        tables[cname] = {"truth": casebook.flat_truth(c["records"]),
                         "extracted": casebook.flat_extracted(c["records"], ext, c.get("name_field"))}
    built = casebook.build_cases(tables, {n: c["records"] for n, c in ds["collections"].items()},
                                 casebook.load_invoice_questions())
    by_id = {c["id"]: c for c in built}
    (out / f"rag-cases-{name}.json").write_text(json.dumps(built, indent=2, ensure_ascii=False, default=str) + "\n",
                                                encoding="utf-8")
    from openai import OpenAI
    judge = OpenAI()
    ls_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    traces = telemetry.LangSmithTraces(ls_key, SERVER_PROJECT)
    records_path = out / f"rag-{name}.jsonl"
    records_path.unlink(missing_ok=True)

    def turn_usage(resp: Dict[str, Any]) -> Dict[str, Any]:
        body = resp.get("body") or {}
        u = telemetry.from_chat_usage(body.get("usage") or {})
        trace = (traces.turn(str(body.get("chat_id"))) if body.get("chat_id")
                 else {"trace_id": None, "runs": [], "error": "no chat_id in the response"})
        if not u:
            u = telemetry.from_llm_runs(trace["runs"], (body.get("usage") or {}).get("service_calls") or [])
            if not trace["runs"] and trace.get("error"):
                u.update(source="unavailable", cost_complete=False, telemetry_error=trace["error"])
        u["server_trace_id"] = trace["trace_id"]
        u["trace_lookup_error"] = trace.get("error")
        u["client_latency_ms"] = resp["client_latency_ms"]
        return u

    def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
        kid = kids[case["collection"]]
        turns = case.get("turns") or [case["question"]]
        responses, usages, session = [], [], None
        for text in turns:
            resp = chat(api, kid, text, session)
            responses.append(resp)
            usages.append(turn_usage(resp))
            session = (resp.get("body") or {}).get("session_id")
            if resp["http_status"] != 200:
                break
        bodies = [r.get("body") or {} for r in responses]
        last = bodies[-1]
        row: Dict[str, Any] = {"run_id": str(uuid.uuid4()), "suite": "rag", "config": name, "case_id": case["id"],
                               "category": case["category"], "collection": case["collection"],
                               "knowledge_id": kid, "collections_from": kids.get("_from"),
                               "truth_differs": case.get("truth_differs"),
                               "http_status": [r["http_status"] for r in responses],
                               "answers": [b.get("response") for b in bodies], "sources": [sources_of(b) for b in bodies],
                               "chat_ids": [b.get("chat_id") for b in bodies], "session_id": session}
        if any(r["http_status"] != 200 for r in responses) or len(responses) < len(turns):
            row.update(correct=False, failure="timeout" if any(r.get("timeout") for r in responses) else "http_error", reason="; ".join(str(r.get("error")) for r in responses if r.get("error")))
        elif case["kind"] == "chart":
            cmp = grade.compare_chart(case["expected_chart"], grade.chart_rows(last))
            cmp["graded_against"] = "extracted"
            if not cmp["exact"] and case.get("truth_differs") is not None and cmp["chart"] is not None:
                alt = grade.compare_chart(case["truth_differs"], cmp["chart"])
                if alt["exact"]:
                    cmp = {**alt, "graded_against": "truth"}
            row.update(correct=cmp["exact"], chart=cmp, answer_draws_chart=answer_draws_chart(last.get("response")),
                       failure=None if cmp["exact"] else "chart", reason=cmp["reason"])
        elif case["kind"] == "follow_up_chart":
            truth = case.get("truth_differs") or {}
            v1 = grade.judge_either(judge, {"question": turns[0], "kind": "names", "expected": case["expected"]},
                                    bodies[0].get("response"), truth.get("names"))
            cmp = grade.compare_population_chart(case["expected_population"], grade.chart_rows(last))
            cmp["graded_against"] = "extracted"
            if not cmp["exact"] and truth.get("population") and cmp["chart"] is not None:
                alt = grade.compare_population_chart(truth["population"], cmp["chart"])
                if alt["exact"]:
                    cmp = {**alt, "graded_against": "truth"}
            row.update(turn1=v1, chart=cmp, correct=v1["pass"] and cmp["exact"],
                       failure=None if v1["pass"] and cmp["exact"] else ("turn1" if not v1["pass"] else "follow_up_chart"),
                       reason=None if v1["pass"] and cmp["exact"] else (v1["reason"] if not v1["pass"] else
                              f"chart covered {cmp.get('chart_population', len(cmp.get('chart') or {}))} "
                              f"({cmp.get('reading')}), expected the {cmp['expected_population']} from turn 1: "
                              f"wrong={cmp.get('wrong')} extra={cmp.get('extra')}"))
        elif case["kind"] == "follow_up_number":
            truth_eur = casebook.inv_eur(tables["invoices"]["truth"])
            v1 = grade.judge_either(judge, {"question": turns[0], "kind": "expected_object", "expected": case["expected_turn1"]},
                                    bodies[0].get("response"), truth_eur)
            v2 = grade.judge_either(judge, {"question": f"{turns[1]} (follow-up to: {turns[0]})", "kind": "number",
                                            "expected": case["expected"]}, last.get("response"), case.get("truth_differs"))
            row.update(turn1=v1, verdict=v2, correct=v1["pass"] and v2["pass"],
                       failure=None if v1["pass"] and v2["pass"] else ("turn1" if not v1["pass"] else "follow_up"),
                       reason=None if v1["pass"] and v2["pass"] else (v1["reason"] if not v1["pass"] else v2["reason"]))
        else:
            v = grade.judge_either(judge, case, last.get("response"), case.get("truth_differs"))
            src = grade.sources_ok(case.get("expected_sources"), sources_of(last))
            ok = v["pass"] and src is not False
            row.update(verdict=v, sources_ok=src, correct=ok,
                       failure=None if ok else ("answer" if not v["pass"] else "sources"),
                       reason=None if ok else (v["reason"] if not v["pass"] else
                                               f"expected sources {case['expected_sources']} not all returned"))
        # usage summed over the case's turns
        row["usage"] = usages
        row["client_latency_ms"] = sum(u["client_latency_ms"] for u in usages)
        server = [u.get("server_latency_ms") for u in usages]
        row["server_latency_ms"] = sum(server) if all(s is not None for s in server) else None
        costs = [u.get("estimated_cost_usd") for u in usages]
        row["estimated_cost_usd"] = round(sum(costs), 8) if costs and all(c is not None for c in costs) else None
        row["cost_complete"] = all(u.get("cost_complete") for u in usages)
        row["cost_source"] = usages[0].get("source") if usages else None
        row["server_trace_ids"] = [u.get("server_trace_id") for u in usages]
        checks = [telemetry.chat_config_verified(u, models) for u in usages]
        row["config_verified"] = all(c["verified"] for c in checks)
        row["config_problems"] = [p for c in checks for p in c["problems"]]
        row["served_models"] = sorted({m for c in checks for m in c["served"]})
        with WRITE_LOCK, records_path.open("a", encoding="utf-8") as fh:
            fh.write(telemetry.dumps(row) + "\n")
        print(f"{'OK  ' if row['correct'] else 'FAIL'} {name} {case['id']:26} {row['client_latency_ms']:>6} ms "
              f"${row['estimated_cost_usd']} verified={row['config_verified']}"
              + ("" if row["correct"] else f" | {str(row.get('reason'))[:140]}"), flush=True)
        return row

    run_meta = run_metadata(ds, config, "rag", repeats)
    run_meta["concurrency"] = CONCURRENCY
    run_meta["collections"] = kids
    if upload:
        from langsmith import Client, evaluate
        client = Client()
        dname = langsmith_dataset(client, ds, "rag", [
            {"inputs": {"case_id": c["id"], "question": c["question"], "turns": c.get("turns"), "collection": c["collection"]},
             "outputs": {"category": c["category"], "truth_expected": c.get("truth_differs")},
             "metadata": {"category": c["category"], "collection": c["collection"]}}
            for c in casebook.build_cases(truth_tables(ds), {n: c["records"] for n, c in ds["collections"].items()},
                                          casebook.load_invoice_questions())])

        def target(inputs: Dict[str, Any]) -> Dict[str, Any]:
            row = run_case(by_id[inputs["case_id"]])
            return {k: row.get(k) for k in ("correct", "config_verified", "client_latency_ms", "server_latency_ms",
                                            "estimated_cost_usd", "answers", "reason", "server_trace_ids")}

        results = evaluate(target, data=dname, evaluators=[metric(k) for k in (
            "correct", "config_verified", "client_latency_ms", "server_latency_ms", "estimated_cost_usd")],
            experiment_prefix=f"{name}-rag-{run_meta['git_sha'][:7]}", metadata=run_meta,
            max_concurrency=CONCURRENCY, num_repetitions=repeats, client=client)
        run_meta["langsmith_experiment"] = results.experiment_name
        run_meta["langsmith_dataset"] = dname
    else:
        with ThreadPoolExecutor(CONCURRENCY) as pool:
            list(pool.map(run_case, [c for _ in range(repeats) for c in built]))
    finish(out, records_path, run_meta, name, "rag", len(built) * repeats)


# ── shared ───────────────────────────────────────────────────────────────────

def run_metadata(ds, config, suite: str, repeats: int) -> Dict[str, Any]:
    models = expected_models(config)
    return {"config": config["name"], "config_sha256": config["sha256"], "profile": config["profile"],
            "settings": config["settings"], "about": config["about"], "suite": suite, "repeats": repeats,
            "dataset": ds["spec"]["name"], "dataset_version": ds["spec"]["version"], "dataset_sha256": ds["sha256"],
            "git_sha": git_sha(), "judge_model": grade.JUDGE_MODEL, "judge_reasoning_effort": grade.JUDGE_EFFORT, "server_trace_project": SERVER_PROJECT,
            "extraction_model": models["extraction"], "extraction_reasoning_effort": models["reasoning_effort"],
            "chat_models": models["by_operation"], "chart_model": models["chart"], "rag_fallback_model": models["rag_fallback"],
            "github_run_id": os.getenv("GITHUB_RUN_ID"), "started_at": now()}


def langsmith_dataset(client, ds, suite: str, examples: List[Dict[str, Any]]) -> str:
    name = f"{ds['spec']['name']}-{suite}-{ds['spec']['version']}-{ds['sha256'][:8]}"
    if client.has_dataset(dataset_name=name):
        return name
    try:
        dataset = client.create_dataset(name, description=f"{ds['spec']['about']} Suite: {suite}. sha256 {ds['sha256']}",
                                        metadata={"dataset_sha256": ds["sha256"], "version": ds["spec"]["version"],
                                                  "suite": suite})
    except Exception:
        # Parallel workflow jobs race to create the same content-addressed dataset; the winner fills it.
        for _ in range(12):
            if client.has_dataset(dataset_name=name) and len(list(client.list_examples(dataset_name=name))) == len(examples):
                return name
            time.sleep(5)
        raise
    client.create_examples(dataset_id=dataset.id, inputs=[e["inputs"] for e in examples],
                           outputs=[e["outputs"] for e in examples], metadata=[e["metadata"] for e in examples])
    return name


def metric(key: str):
    def evaluator(outputs: dict) -> dict:
        value = outputs.get(key)
        return {"key": key, "score": float(value) if isinstance(value, (int, float, bool)) else None}
    evaluator.__name__ = key
    return evaluator


def finish(out: Path, records_path: Path, run_meta: Dict[str, Any], name: str, suite: str, expected: int) -> None:
    run_meta["finished_at"] = now()
    (out / f"run-{suite}-{name}.json").write_text(json.dumps(run_meta, indent=2, default=str) + "\n", encoding="utf-8")
    records = ([json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
               if records_path.exists() else [])
    if len(records) < expected:
        raise SystemExit(f"{name} {suite}: {len(records)} of {expected} records written; the rest errored (see the log above)")
    # Only answers/documents the stack actually produced are checked against the config: a timeout,
    # an HTTP error or a failed document is already recorded as a failure and has no served model.
    produced = [r for r in records if (r.get("status") == "completed" if suite == "extraction"
                                       else all(s == 200 for s in r.get("http_status") or [None]))]
    bad = [r for r in produced if not r.get("config_verified")]
    if bad:
        first = bad[0].get("config_problems")
        raise SystemExit(f"{name} {suite}: {len(bad)}/{len(records)} records were not produced by this configuration "
                         f"(first: {first})")


# ── report ───────────────────────────────────────────────────────────────────

def percentile(values, q):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo))


def cost_summary(records):
    costs = [r.get("estimated_cost_usd") for r in records if r.get("estimated_cost_usd") is not None]
    return {"estimated_cost_usd_mean": round(statistics.mean(costs), 6) if costs else None,
            "estimated_cost_usd_total": round(sum(costs), 6) if costs else None,
            "cost_measured": len(costs), "cost_complete": bool(records) and all(r.get("cost_complete") for r in records),
            "cost_sources": sorted({str(r.get("cost_source")) for r in records})}


def summarize_extraction(records: List[Dict[str, Any]], numeric: List[str]) -> Dict[str, Any]:
    if not records:
        return {}
    fields_total = sum(r["fields_total"] for r in records)
    fields_correct = sum(r["fields_correct"] for r in records)
    by_field: Dict[str, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "of": 0, "missing": 0, "unexpected": 0,
                                                                 "wrong": 0, "judge_disagreed": 0})
    for r in records:
        for f, v in r["fields"].items():
            cell = by_field[f"{r['collection']}.{f}"]
            cell["of"] += 1
            cell["correct"] += bool(v["correct"])
            if v["status"] in ("missing", "unexpected", "wrong"):
                cell[v["status"]] += 1
            if v.get("verdicts") and not v.get("agree", True):
                cell["judge_disagreed"] += 1
    by_collection = {}
    for c in sorted({r["collection"] for r in records}):
        rs = [r for r in records if r["collection"] == c]
        tot, ok = sum(r["fields_total"] for r in rs), sum(r["fields_correct"] for r in rs)
        reps = defaultdict(lambda: [0, 0])
        for r in rs:
            reps[r["repeat"]][0] += r["fields_correct"]
            reps[r["repeat"]][1] += r["fields_total"]
        by_collection[c] = {"fields_correct": ok, "fields_total": tot, "accuracy": round(ok / tot, 4),
                            "documents": len(rs), "complete_documents": sum(1 for r in rs if r["complete"]),
                            "all_correct_documents": sum(1 for r in rs if r["all_correct"]),
                            "per_repeat": {str(k): f"{v[0]}/{v[1]}" for k, v in sorted(reps.items())}}
    num = [v for r in records for f, v in r["fields"].items() if f in numeric]
    return {
        "documents": len(records), "fields_correct": fields_correct, "fields_total": fields_total,
        "accuracy": round(fields_correct / fields_total, 4),
        "numeric_exact": f"{sum(1 for v in num if v['correct'])}/{len(num)}",
        "missing_values": sum(1 for r in records for v in r["fields"].values() if v["status"] == "missing"),
        "unexpected_values": sum(1 for r in records for v in r["fields"].values() if v["status"] == "unexpected"),
        "complete_documents": f"{sum(1 for r in records if r['complete'])}/{len(records)}",
        "failed_documents": sum(1 for r in records if r.get("status") != "completed"),
        "latency_ms_p50": percentile([r.get("latency_ms") for r in records], 0.5),
        "latency_ms_p95": percentile([r.get("latency_ms") for r in records], 0.95),
        **cost_summary(records),
        "config_verified": f"{sum(1 for r in records if r.get('config_verified'))}/{len(records)}",
        "served_models": sorted({m for r in records for m in r.get("served_models") or []}),
        "by_collection": by_collection,
        "by_field": {k: dict(v) for k, v in sorted(by_field.items())},
    }


def summarize_rag(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        return {}
    n = len(records)
    lat = [r.get("server_latency_ms") if r.get("server_latency_ms") is not None else r.get("client_latency_ms")
           for r in records]
    by_cat = {}
    for c in sorted({r["category"] for r in records}):
        rs = [r for r in records if r["category"] == c]
        by_cat[c] = {"correct": sum(1 for r in rs if r["correct"]), "answers": len(rs)}
    src = [r["sources_ok"] for r in records if r.get("sources_ok") is not None]
    return {"answers": n, "correct": sum(1 for r in records if r["correct"]),
            "accuracy": round(sum(1 for r in records if r["correct"]) / n, 4),
            "latency_source": "server" if all(r.get("server_latency_ms") is not None for r in records) else "client",
            "latency_ms_p50": percentile(lat, 0.5), "latency_ms_p95": percentile(lat, 0.95),
            "client_latency_ms_p50": percentile([r.get("client_latency_ms") for r in records], 0.5),
            **cost_summary(records),
            "sources_ok": f"{sum(src)}/{len(src)}",
            "config_verified": f"{sum(1 for r in records if r.get('config_verified'))}/{n}",
            "served_models": sorted({m for r in records for m in r.get("served_models") or []}),
            "http_errors": sum(1 for r in records if r.get("failure") == "http_error"),
            "timeouts": sum(1 for r in records if r.get("failure") == "timeout"),
            "telemetry_unavailable": sum(1 for r in records if r.get("cost_source") == "unavailable"),
            "graded_against_truth": sum(1 for r in records if any((r.get(k) or {}).get("graded_against") == "truth"
                                                                  for k in ("verdict", "chart", "turn1"))),
            "by_category": by_cat}


def report(out: Path) -> Dict[str, Any]:
    ds = load_dataset()
    numeric = ds["spec"]["numeric_fields"]
    summary: Dict[str, Any] = {"generated_at": now(), "dataset": ds["spec"]["name"], "dataset_version": ds["spec"]["version"],
                               "dataset_sha256": ds["sha256"], "configs": {}, "failures": []}
    for path in sorted(out.glob("extraction-*.jsonl")) + sorted(out.glob("rag-*.jsonl")):
        suite, name = path.stem.split("-", 1)
        records = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        meta_path = out / f"run-{suite}-{name}.json"
        entry = summary["configs"].setdefault(name, {})
        entry[suite] = {"run": json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None,
                        "summary": summarize_extraction(records, numeric) if suite == "extraction" else summarize_rag(records)}
        if suite == "extraction":
            for r in records:
                for f, v in r["fields"].items():
                    if not v["correct"]:
                        summary["failures"].append({"config": name, "suite": suite, "repeat": r["repeat"],
                                                    "item": f"{r['doc_id']}.{f}", "status": v["status"],
                                                    "expected": v["expected"], "extracted": v["extracted"],
                                                    "reason": v.get("reason")})
        else:
            for r in records:
                if not r["correct"]:
                    summary["failures"].append({"config": name, "suite": suite, "item": r["case_id"],
                                                "status": r.get("failure"), "reason": r.get("reason"),
                                                "truth_differs": r.get("truth_differs") is not None})
            fu = [r for r in records if r["case_id"] == "fu_python_aws_years_chart"]
            if fu:
                entry[suite]["follow_up_regression"] = {
                    "passed": f"{sum(1 for r in fu if r['correct'])}/{len(fu)}",
                    "chart_population": [(r.get("chart") or {}).get("chart_population") or len((r.get("chart") or {}).get("chart") or {})
                                         for r in fu],
                    "expected_population": [(r.get("chart") or {}).get("expected_population") for r in fu]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(markdown(summary), encoding="utf-8")
    return summary


def _s(ms) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f} s"


def _usd(v) -> str:
    return "-" if v is None else f"${v:.5f}"


def markdown(summary: Dict[str, Any]) -> str:
    lines = [f"# Document Intelligence model evaluation", "",
             f"Dataset `{summary['dataset']}` {summary['dataset_version']} sha256 `{summary['dataset_sha256'][:12]}`", "",
             "## Extraction", "",
             "| Config | Model / effort | Fields correct | Resumes | Invoices | Numeric exact | Complete docs | p50 / p95 per doc | Mean cost / doc | Cost complete | Verified |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, e in summary["configs"].items():
        if "extraction" not in e:
            continue
        s, run = e["extraction"]["summary"], e["extraction"]["run"] or {}
        bc = s.get("by_collection", {})
        res, inv = bc.get("resumes", {}), bc.get("invoices", {})
        lines.append(f"| {name} | {run.get('extraction_model')} / {run.get('extraction_reasoning_effort')} | "
                     f"{s['fields_correct']}/{s['fields_total']} ({s['accuracy']:.1%}) | "
                     f"{res.get('fields_correct')}/{res.get('fields_total')} {res.get('per_repeat')} | "
                     f"{inv.get('fields_correct')}/{inv.get('fields_total')} {inv.get('per_repeat')} | {s['numeric_exact']} | "
                     f"{s['complete_documents']} | {_s(s['latency_ms_p50'])} / {_s(s['latency_ms_p95'])} | "
                     f"{_usd(s['estimated_cost_usd_mean'])} | {s['cost_complete']} ({', '.join(s['cost_sources'])}) | {s['config_verified']} |")
    lines += ["", "## RAG", "",
              "| Config | Agent / answer | Correct | p50 / p95 | Mean cost / answer | Cost complete | Sources ok | Verified | Follow-up regression |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name, e in summary["configs"].items():
        if "rag" not in e:
            continue
        s, run = e["rag"]["summary"], e["rag"]["run"] or {}
        cm = run.get("chat_models") or {}
        fu = e["rag"].get("follow_up_regression") or {}
        lines.append(f"| {name} | {cm.get('retrieval_agent')} / {cm.get('answer')} | {s['correct']}/{s['answers']} ({s['accuracy']:.1%}) | "
                     f"{_s(s['latency_ms_p50'])} / {_s(s['latency_ms_p95'])} ({s['latency_source']}) | "
                     f"{_usd(s['estimated_cost_usd_mean'])} | {s['cost_complete']} ({', '.join(s['cost_sources'])}) | "
                     f"{s['sources_ok']} | {s['config_verified']} | {fu.get('passed', '-')} |")
    cats = sorted({c for e in summary["configs"].values() if "rag" in e for c in e["rag"]["summary"]["by_category"]})
    rag_names = [n for n, e in summary["configs"].items() if "rag" in e]
    if cats:
        lines += ["", "| Category | " + " | ".join(rag_names) + " |", "|---|" + "---|" * len(rag_names)]
        for c in cats:
            row = []
            for n in rag_names:
                v = summary["configs"][n]["rag"]["summary"]["by_category"].get(c)
                row.append(f"{v['correct']}/{v['answers']}" if v else "-")
            lines.append(f"| {c} | " + " | ".join(row) + " |")
    lines += ["", "## Experiments", ""]
    for name, e in summary["configs"].items():
        for suite in ("extraction", "rag"):
            run = (e.get(suite) or {}).get("run") or {}
            if run:
                lines.append(f"- {name} {suite}: LangSmith `{run.get('langsmith_experiment')}` (dataset `{run.get('langsmith_dataset')}`), "
                             f"config sha256 `{run.get('config_sha256', '')[:12]}`, git `{run.get('git_sha', '')[:7]}`, "
                             f"workflow run {run.get('github_run_id')}")
    lines += ["", f"## Failures ({len(summary['failures'])})", "", "| Config | Suite | Item | Status | Reason |", "|---|---|---|---|---|"]
    for f in summary["failures"][:200]:
        reason = str(f.get("reason") or f.get("extracted") or "").replace("|", "/").replace("\n", " ")[:220]
        lines.append(f"| {f['config']} | {f['suite']} | {f['item']} | {f.get('status')} | {reason} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["env", "prepare", "extract", "ask", "report"])
    parser.add_argument("--out", type=Path, default=Path(os.getenv("MODEL_EVAL_OUT", str(REPO / "model-eval-results"))))
    parser.add_argument("--config")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=5400, help="seconds to wait for indexing (extract)")
    parser.add_argument("--no-upload", action="store_true", help="do not create LangSmith datasets/experiments")
    o = parser.parse_args()
    if o.command in ("env", "extract", "ask") and not o.config:
        raise SystemExit("--config is required")
    if o.command == "env":
        write_env(o.out, o.config)
    elif o.command == "prepare":
        prepare(o.out)
    elif o.command == "extract":
        extract(o.out, o.config, o.repeats, not o.no_upload, o.timeout)
    elif o.command == "ask":
        ask(o.out, o.config, o.repeats, not o.no_upload, o.timeout)
    else:
        print(markdown(report(o.out)))


if __name__ == "__main__":
    main()
