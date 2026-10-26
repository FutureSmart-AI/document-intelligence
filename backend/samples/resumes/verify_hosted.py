"""Run the synthetic resume sample against a hosted Document Intelligence service.

Steps (each idempotent, state kept in hosted_run/state.json):
  create    new collection for OWNER, seeded with the `resume` field template
  upload    the 24 PDFs (skips files already uploaded)
  wait      until every document is completed (or failed)
  extract   pull each document's extracted metadata -> hosted_run/extracted.json
  ask       ask every question in questions.json -> hosted_run/answers.json
  all       the above in order

Auth is the service key (x-api-key), read from DI_API_KEY or the file named by
DI_API_KEY_FILE; it is never printed. The collection is created with owner_id
OWNER, so it appears in that person's workspace.

  DI_API_KEY_FILE=... python verify_hosted.py all --owner <owner email>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
RUN = HERE / "hosted_run"
STATE = RUN / "state.json"
COLLECTION_NAME = "Sample: Candidate Resumes (synthetic)"
DESCRIPTION = ("24 fictional resumes for trying Document Intelligence. Every person, employer and school "
               "is invented. Source: fs-agent-knowledge backend/samples/resumes.")


def api_key() -> str:
    if os.environ.get("DI_API_KEY"):
        return os.environ["DI_API_KEY"]
    path = os.environ.get("DI_API_KEY_FILE")
    if path:
        return Path(path).read_text().strip()
    sys.exit("set DI_API_KEY or DI_API_KEY_FILE")


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    RUN.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n")


def client(base: str) -> httpx.Client:
    return httpx.Client(base_url=f"{base.rstrip('/')}/api/knowledge", headers={"x-api-key": api_key()},
                        timeout=httpx.Timeout(300.0, connect=30.0))


def check(r: httpx.Response) -> httpx.Response:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {r.text[:500]}")
    return r


def create(c: httpx.Client, state: dict, owner: str) -> None:
    if state.get("knowledge_id"):
        print("collection exists:", state["knowledge_id"])
        return
    kid = check(c.post("/v1", json={"owner_id": owner, "collection_name": COLLECTION_NAME})).json()["knowledge_id"]
    state.update(knowledge_id=kid, owner=owner, collection_name=COLLECTION_NAME, created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    save_state(state)
    # field_template seeds the 13 resume fields with their full config (allowed values, list caps);
    # seed_optional_defaults=False keeps the generic document defaults to the protected two.
    check(c.put(f"/v1/{kid}/config", json={"collection_name": COLLECTION_NAME, "service_description": DESCRIPTION,
                                          "field_template": "resume", "seed_optional_defaults": False}))
    fields = check(c.get(f"/v1/{kid}/enrichment")).json()
    state["fields"] = sorted(f["field_name"] for f in fields.get("fields", fields if isinstance(fields, list) else []))
    save_state(state)
    print("created", kid, "fields:", state["fields"])


def upload(c: httpx.Client, state: dict) -> None:
    kid = state["knowledge_id"]
    done = state.setdefault("uploads", {})
    for pdf in sorted((HERE / "pdf").glob("*.pdf")):
        if pdf.name in done:
            continue
        with pdf.open("rb") as fh:
            r = check(c.post(f"/v1/{kid}/documents", files={"file": (pdf.name, fh, "application/pdf")}))
        body = r.json()
        done[pdf.name] = body.get("object_id") or body.get("data", {}).get("object_id") or body
        save_state(state)
        print("uploaded", pdf.name)


def list_docs(c: httpx.Client, kid: str) -> list[dict]:
    body = check(c.get(f"/v2/{kid}/documents", params={"page": 1, "page_size": 100})).json()
    return body.get("document") or body.get("documents") or []


def wait(c: httpx.Client, state: dict, timeout_s: int = 3600) -> None:
    kid, t0 = state["knowledge_id"], time.time()
    while True:
        docs = list_docs(c, kid)
        counts: dict[str, int] = {}
        for d in docs:
            counts[d.get("status", "?")] = counts.get(d.get("status", "?"), 0) + 1
        print(f"{int(time.time() - t0)}s", counts, flush=True)
        if docs and all(d.get("status") in ("completed", "failed") for d in docs) and len(docs) >= len(state.get("uploads", {})):
            state["documents"] = {d.get("file_name") or d.get("name"): {"object_id": d.get("object_id"),
                                  "status": d.get("status"), "error": d.get("error_detail")} for d in docs}
            state["indexed_seconds"] = int(time.time() - t0)
            save_state(state)
            return
        if time.time() - t0 > timeout_s:
            raise SystemExit("timed out waiting for indexing")
        time.sleep(20)


def extract(c: httpx.Client, state: dict) -> None:
    kid, out = state["knowledge_id"], {}
    for name, d in sorted(state["documents"].items()):
        r = c.get(f"/v1/{kid}/documents/{d['object_id']}/metadata")
        out[name] = r.json() if r.status_code < 400 else {"error": r.status_code, "body": r.text[:300]}
    (RUN / "extracted.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("extracted", len(out))


def parse_sse(text: str) -> dict:
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                pass
    final = next((e for e in reversed(events) if isinstance(e, dict) and e.get("session_id")), None)
    return final or {"error": "no final event", "tail": text[-500:]}


def find_charts(obj, path="") -> list[dict]:
    """Every value under a key that mentions 'chart', wherever the final event puts it."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}" if path else k
            if "chart" in k.lower() and v:
                found.append({"path": here, "value": v})
            else:
                found.extend(find_charts(v, here))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(find_charts(v, f"{path}[{i}]"))
    return found


def ask(c: httpx.Client, state: dict, only: list[str] | None) -> None:
    kid = state["knowledge_id"]
    qs = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))["questions"]
    path = RUN / "answers.json"
    answers = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for q in qs:
        if only and q["id"] not in only:
            continue
        t0 = time.time()
        r = check(c.post(f"/v1/{kid}/chat", json={"query": q["question"], "owner_id": state["owner"], "stream": True}))
        final = parse_sse(r.text)
        data = final.get("data") or {}
        sources = (((data.get("knowledge") or {}).get("relevant_sources") or {}).get("data")) or []
        answers[q["id"]] = {
            "question": q["question"], "seconds": round(time.time() - t0, 1),
            "response": final.get("response"),
            "sources": [{k: s.get(k) for k in ("file_name", "title", "object_id", "source", "name") if s.get(k)} for s in sources],
            "charts": find_charts(final),
            "final_keys": sorted(final.keys()),
            "data_keys": sorted(data.keys()),
            "session_id": final.get("session_id"),
        }
        path.write_text(json.dumps(answers, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(q["id"], f"{answers[q['id']]['seconds']}s", (final.get("response") or "")[:120].replace("\n", " "))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["create", "upload", "wait", "extract", "ask", "all"])
    ap.add_argument("--base", default="https://app.futuresmart.ai")
    ap.add_argument("--owner", default=os.environ.get("DI_OWNER_ID"), required="DI_OWNER_ID" not in os.environ,
                    help="owner_id for the collection/chat calls (or set DI_OWNER_ID)")
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    state = load_state()
    with client(a.base) as c:
        steps = ["create", "upload", "wait", "extract", "ask"] if a.step == "all" else [a.step]
        for s in steps:
            if s == "create":
                create(c, state, a.owner)
            elif s == "upload":
                upload(c, state)
            elif s == "wait":
                wait(c, state)
            elif s == "extract":
                extract(c, state)
            elif s == "ask":
                ask(c, state, a.only)


if __name__ == "__main__":
    main()
