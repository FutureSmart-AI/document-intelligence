"""Clean-install proof: the core path on an installation with ONLY OPENAI_API_KEY.

Run against a fresh `python deploy/configure.py` + `docker compose up -d --build`
(see .github/workflows/standalone-install-e2e.yml). Standard library only.

It exercises, through the REST API: collection create + configure (resume and
invoice templates), PDF upload, extraction, extracted-metadata read, a
metadata-filter question, an aggregate question whose chart must come from the
in-process renderer (`chart_output.renderer == "local"`), a follow-up in the same
session, the sources of an answer, and the explicit-parser-mode refusal (HTTP 422
naming LLAMA_PARSE_API_KEY). Every response is written to --out as JSON evidence;
nothing secret is written or printed.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUMES = ["r01-aarav-mehta.pdf", "r02-priya-raghunathan.pdf", "r03-daniel-okafor.pdf",
           "r04-sneha-kulkarni.pdf", "r05-rahul-verma.pdf"]
INVOICES = ["bramblecloud-bch-2026-0418.pdf", "copperleaf-co-55120.pdf"]


class Api:
    def __init__(self, base, key, out):
        self.base, self.key, self.out = base.rstrip("/"), key, out

    def call(self, method, path, body=None, content_type="application/json", expect=(200, 201, 202)):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        req = urllib.request.Request(f"{self.base}/{path.lstrip('/')}", method=method, data=data,
                                     headers={"x-api-key": self.key, "Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                status, payload = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        try:
            parsed = json.loads(payload or b"null")
        except ValueError:
            parsed = {"raw": payload[:500].decode("utf-8", "replace")}
        if status not in expect:
            raise RuntimeError(f"{method} {path} -> HTTP {status}: {json.dumps(parsed)[:800]}")
        return status, parsed

    def save(self, name, payload):
        (self.out / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def multipart(fields, file_path, content_type="application/pdf"):
    boundary = "di-e2e-" + uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
                  f"Content-Type: {content_type}\r\n\r\n").encode() + file_path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def create_collection(api, owner, name, template):
    _, created = api.call("POST", "v1", {"owner_id": owner, "collection_name": name})
    kid = created["knowledge_id"]
    _, config = api.call("PUT", f"v1/{kid}/config", {"collection_name": name, "field_template": template})
    api.save(f"{template}-config", config)
    return kid


def upload_all(api, kid, folder, names):
    ids = {}
    for name in names:
        body, ctype = multipart({}, ROOT / "backend" / "samples" / folder / "pdf" / name)
        _, uploaded = api.call("POST", f"v1/{kid}/documents", body, ctype)
        ids[name] = uploaded["object_id"]
    return ids


def wait_completed(api, kid, ids, timeout=1200):
    deadline, pending = time.monotonic() + timeout, dict(ids)
    statuses = {}
    while pending:
        for name, oid in list(pending.items()):
            _, status = api.call("GET", f"v1/{kid}/documents/{oid}/status")
            state = status.get("status")
            statuses[name] = status
            if state == "completed":
                pending.pop(name)
            elif state == "failed":
                raise RuntimeError(f"{name} failed: {status.get('error_detail')}")
        if pending and time.monotonic() > deadline:
            raise TimeoutError(f"still processing after {timeout}s: {sorted(pending)}")
        if pending:
            time.sleep(5)
    return statuses


def chat(api, kid, query, session_id=None):
    body = {"query": query, "stream": False}
    if session_id:
        body["session_id"] = session_id
    started = time.monotonic()
    _, answer = api.call("POST", f"v1/{kid}/chat", body)
    answer["_client_seconds"] = round(time.monotonic() - started, 1)
    return answer


def chart_of(answer):
    knowledge = (answer.get("data") or {}).get("knowledge") or {}
    return knowledge.get("chart_output") or answer.get("chart_output")


def sources_of(answer):
    knowledge = (answer.get("data") or {}).get("knowledge") or {}
    return knowledge.get("relevant_sources") or knowledge.get("summaries") or knowledge.get("sources")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    env = dict(line.split("=", 1) for line in args.env_file.read_text(encoding="utf-8").splitlines()
               if "=" in line and not line.startswith("#"))
    args.out.mkdir(parents=True, exist_ok=True)
    api = Api(args.base_url, env["FASTAPI_SECRET_KEY"], args.out)
    owner = env.get("STANDALONE_OWNER_EMAIL", "owner@example.test")
    summary = {"checks": {}}

    def check(name, ok, detail):
        summary["checks"][name] = {"ok": bool(ok), "detail": detail}
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)

    resumes_kid = create_collection(api, owner, "E2E resumes", "resume")
    invoices_kid = create_collection(api, owner, "E2E invoices", "invoice")
    summary["collections"] = {"resumes": resumes_kid, "invoices": invoices_kid}

    # Explicit parser mode with no LlamaParse key: refused up front, with the reason.
    body, ctype = multipart({"document_processing_mode": "scanned"},
                            ROOT / "backend" / "samples" / "resumes" / "pdf" / RESUMES[0])
    status, refused = api.call("POST", f"v1/{resumes_kid}/documents", body, ctype, expect=(422,))
    api.save("parser-mode-refused", refused)
    check("parser_mode_without_key_is_422", status == 422 and "LLAMA_PARSE_API_KEY" in json.dumps(refused),
          refused.get("detail", "")[:160])

    t0 = time.monotonic()
    resume_ids = upload_all(api, resumes_kid, "resumes", RESUMES)
    invoice_ids = upload_all(api, invoices_kid, "invoices", INVOICES)
    statuses = wait_completed(api, resumes_kid, resume_ids)
    statuses.update(wait_completed(api, invoices_kid, invoice_ids))
    api.save("document-statuses", statuses)
    check("all_documents_completed", True, f"{len(statuses)} documents in {round(time.monotonic() - t0)}s")

    metadata = {}
    for name, oid in {**resume_ids, **invoice_ids}.items():
        kid = resumes_kid if name in resume_ids else invoices_kid
        _, meta = api.call("GET", f"v1/{kid}/documents/{oid}/metadata")
        metadata[name] = meta
    api.save("extracted-metadata", metadata)
    text = json.dumps(metadata).lower()
    check("metadata_extracted", "years_of_experience" in text and ("invoice_number" in text or "total" in text),
          {n: sorted((m.get("metadata") or m).keys())[:12] if isinstance(m, dict) else str(m)[:80]
           for n, m in list(metadata.items())[:2]})

    q1 = chat(api, resumes_kid, "Which candidates have more than 5 years of experience?")
    api.save("chat-1-metadata-filter", q1)
    check("metadata_filter_answer", bool(q1.get("response")), q1.get("response", "")[:240])

    q2 = chat(api, resumes_kid, "How many candidates are there per seniority level? Show it as a chart.")
    api.save("chat-2-aggregate-chart", q2)
    chart = chart_of(q2) or {}
    check("chart_from_local_renderer",
          chart.get("chart_generated") is True and chart.get("renderer") == "local",
          {"chart_suggestion": chart.get("chart_suggestion"), "renderer": chart.get("renderer"),
           "data": (chart.get("chart_data") or {}).get("data"),
           "chart_type_source": ((chart.get("chart_data") or {}).get("metadata") or {}).get("chart_type_source")})

    session_id = q2.get("session_id")
    q3 = chat(api, resumes_kid, "Which of them know Python?", session_id=session_id)
    api.save("chat-3-follow-up", q3)
    check("follow_up_same_session", bool(q3.get("response")) and q3.get("session_id") == session_id,
          q3.get("response", "")[:240])

    sources = sources_of(q1)
    api.save("chat-1-sources", sources)
    check("answer_lists_sources", bool(sources), f"{len(sources) if hasattr(sources, '__len__') else sources} source entries")

    q4 = chat(api, invoices_kid, "What is the total amount on each invoice? Chart it.")
    api.save("chat-4-invoices-chart", q4)
    chart4 = chart_of(q4) or {}
    check("invoice_answer", bool(q4.get("response")), {"answer": q4.get("response", "")[:200],
                                                       "chart_renderer": chart4.get("renderer")})

    summary["latency_seconds"] = {k: v.get("_client_seconds") for k, v in
                                  {"q1": q1, "q2": q2, "q3": q3, "q4": q4}.items()}
    summary["ui_target"] = {"collection": resumes_kid, "question": "How many candidates are there per seniority level? Show it as a chart."}
    api.save("summary", summary)
    failed = [k for k, v in summary["checks"].items() if not v["ok"]]
    print(json.dumps({"failed": failed, "collections": summary["collections"]}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
