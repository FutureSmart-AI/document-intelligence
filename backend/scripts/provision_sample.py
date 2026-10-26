"""Provision a sample collection from a manifest (``backend/samples/<key>/sample.json``).

A sample is built once and read by every visitor (utils/auth/sample_access.py), so
this is the only time its documents are extracted and embedded. It goes through the
public API with the master key, the same calls a customer's collection is built
with, so the sample is exactly what a customer would get:

  1. the sample owner's account limits (a platform owner, no sign-in): high enough
     that the per-visitor allowance, not the owner's trial, bounds sample use;
  2. a new collection owned by that owner, with the manifest's name and
     description, and its fields — ``field_template`` seeds a template's full
     field config on ``PUT /config`` (allowed values, list caps), which the
     create screen's field list does not carry; or the manifest's own ``fields``;
  3. the documents, one ``POST /documents`` each, then waited for until every one
     is indexed — any failure stops here and the old sample stays live;
  4. the chat config: sources on, and the suggested questions — only questions
     that were verified against the hosted sample belong in the manifest;
  5. the swap: the old copy (if any) stops being the sample, the new one becomes
     it, and with ``--retire-old`` the old copy is deleted.

``--update-questions`` does step 4 alone on the live sample, for when more
questions have been verified.

Usage (from backend/):
    KNOWLEDGE_MASTER_KEY=... python -m scripts.provision_sample samples/resumes/sample.json \
        [--api https://app.futuresmart.ai/api/knowledge] [--retire-old | --update-questions] [--dry-run]

Manifest:
    {
      "sample_key": "resumes",
      "owner_id": "document-intelligence-samples@futuresmart.ai",
      "collection_name": "...",
      "service_description": "...",
      "field_template": "resume",            # or "fields": [{field_name, description, ...}]
      "documents": "pdf/*.pdf",              # glob, relative to the manifest
      "suggested_questions": ["..."]
    }
"""

import argparse
import glob
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml

TERMINAL_OK = {"indexed", "completed", "processed", "success", "succeeded", "ready"}
IN_PROGRESS = {"processing", "pending", "in_progress", "queued", "uploading", "parsing", "enriching", "indexing"}

# Room for the global daily ceiling (SAMPLE_QUESTIONS_GLOBAL_PER_DAY, 1000) with
# headroom; the allowance is the real bound, these only keep the owner's own
# account limits from refusing sample questions first.
SAMPLE_OWNER_LIMITS = {
    "max_agents": 20,
    "max_documents": 500,
    "max_knowledge_items": 600,
    "max_indexed_characters": 20_000_000,
    "max_messages_per_day": 5_000,
    "max_messages_per_month": 100_000,
    "max_conversations_per_month": 100_000,
    "max_llm_tokens_per_month": 500_000_000,
}


def log(message: str) -> None:
    print(message, flush=True)


class Api:
    def __init__(self, base: str, master_key: str):
        self.base = base.rstrip("/")
        self.client = httpx.Client(timeout=180, headers={"x-api-key": master_key})

    def call(self, method: str, path: str, *, ok=(200, 201), **kwargs) -> httpx.Response:
        response = self.client.request(method, f"{self.base}{path}", **kwargs)
        if response.status_code not in ok:
            raise SystemExit(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response


def load_manifest(path: Path) -> Dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for key in ("sample_key", "owner_id", "collection_name", "service_description", "documents"):
        if not manifest.get(key):
            raise SystemExit(f"manifest is missing {key!r}")
    # May be empty: a new sample goes live with no suggestions until its questions
    # have been verified against it, then --update-questions adds them.
    if not isinstance(manifest.get("suggested_questions"), list):
        raise SystemExit("manifest needs a suggested_questions list (it may be empty)")
    if not manifest.get("field_template") and not manifest.get("fields"):
        raise SystemExit("manifest needs field_template or fields")
    files = sorted(glob.glob(str(path.parent / manifest["documents"])))
    if not files:
        raise SystemExit(f"no documents match {manifest['documents']!r} next to {path}")
    manifest["_files"] = files
    return manifest


def ensure_owner_limits(api: Api, owner_id: str) -> None:
    path = f"/v1/owners/{owner_id}/usage/owner-config"
    response = api.call("PATCH", path, ok=(200, 404), json={"platform_limits": SAMPLE_OWNER_LIMITS})
    if response.status_code == 200:
        log(f"owner limits updated for {owner_id}")
        return
    defaults = yaml.safe_load(Path(__file__).resolve().parents[1].joinpath(
        "utils/config/usage/owner_defaults.yaml").read_text(encoding="utf-8"))
    api.call("PUT", path, json={
        "tier_type": "custom",
        "base_tier": "trial",
        "platform_limits": {**defaults["platform_limits"], **SAMPLE_OWNER_LIMITS},
        "default_models": defaults["default_models"],
    })
    log(f"owner config created for {owner_id}")


def as_enrichment_field(field: Dict[str, Any]) -> Dict[str, Any]:
    out = {"field_name": field["field_name"], "description": field.get("description") or field["field_name"], "is_custom": True}
    for key in ("field_type", "level", "schema"):
        if field.get(key):
            out[key] = field[key]
    return out


def create_collection(api: Api, manifest: Dict[str, Any]) -> str:
    knowledge_id = api.call("POST", "/v1", json={
        "owner_id": manifest["owner_id"], "collection_name": manifest["collection_name"],
    }).json()["knowledge_id"]
    log(f"created collection {knowledge_id}")
    config = {
        "collection_name": manifest["collection_name"],
        "service_description": manifest["service_description"],
        "seed_optional_defaults": False,
    }
    if manifest.get("field_template"):
        config["field_template"] = manifest["field_template"]
    api.call("PUT", f"/v1/{knowledge_id}/config", json=config)
    if manifest.get("fields"):
        api.call("POST", f"/v1/{knowledge_id}/enrichment",
                 json={"fields": [as_enrichment_field(f) for f in manifest["fields"]]})
    fields = api.call("GET", f"/v1/{knowledge_id}/enrichment").json()
    fields = fields.get("fields", []) if isinstance(fields, dict) else fields
    log(f"configured {len(fields)} extraction fields: {sorted(f.get('field_name') for f in fields)}")
    return knowledge_id


def upload_documents(api: Api, knowledge_id: str, files: List[str]) -> None:
    for path in files:
        name = Path(path).name
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            api.call("POST", f"/v1/{knowledge_id}/documents", files={"file": (name, fh, content_type)})
        log(f"  uploaded {name}")
    log(f"uploaded {len(files)} documents")


def list_documents(api: Api, knowledge_id: str) -> List[Dict[str, Any]]:
    payload = api.call("GET", f"/v2/{knowledge_id}/documents", params={"page": 1, "page_size": 200}).json()
    return payload.get("documents") or payload.get("document") or []


def wait_until_indexed(api: Api, knowledge_id: str, expected: int, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while True:
        docs = list_documents(api, knowledge_id)
        statuses = [str(d.get("status") or "").lower() for d in docs]
        failed = [d.get("file_name") or d.get("object_id") for d, s in zip(docs, statuses) if s == "failed"]
        busy = sum(1 for s in statuses if s in IN_PROGRESS or not s)
        log(f"  {len(docs)}/{expected} listed, {busy} in progress, {len(failed)} failed")
        if failed:
            raise SystemExit(f"documents failed: {failed}")
        if len(docs) >= expected and busy == 0:
            unknown = sorted({s for s in statuses if s not in TERMINAL_OK})
            if unknown:
                raise SystemExit(f"unexpected document statuses: {unknown}")
            return
        if time.time() > deadline:
            raise SystemExit("timed out waiting for indexing")
        time.sleep(15)


def configure_chat(api: Api, knowledge_id: str, manifest: Dict[str, Any]) -> None:
    body = {"chat_enabled": True, "show_sources": True, "suggested_questions": manifest["suggested_questions"]}
    api.call("PUT", f"/v1/{knowledge_id}/chat/config", json=body)
    log(f"chat config set ({len(manifest['suggested_questions'])} suggested questions)")


def current_sample(api: Api, sample_key: str) -> Optional[str]:
    for sample in api.call("GET", "/v1/samples").json().get("samples", []):
        if sample["sample_key"] == sample_key:
            return sample["knowledge_id"]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--api", default="https://app.futuresmart.ai/api/knowledge")
    parser.add_argument("--retire-old", action="store_true", help="delete the previous copy after the swap")
    parser.add_argument("--update-questions", action="store_true",
                        help="only replace the live sample's suggested questions")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds to wait for indexing")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    log(f"sample {manifest['sample_key']!r}: {len(manifest['_files'])} documents, "
        f"{len(manifest['suggested_questions'])} suggested questions")
    if args.dry_run:
        return

    master_key = os.environ.get("KNOWLEDGE_MASTER_KEY")
    if not master_key:
        raise SystemExit("set KNOWLEDGE_MASTER_KEY")
    api = Api(args.api, master_key)

    if args.update_questions:
        live = current_sample(api, manifest["sample_key"])
        if not live:
            raise SystemExit(f"no live {manifest['sample_key']!r} sample to update")
        configure_chat(api, live, manifest)
        return

    ensure_owner_limits(api, manifest["owner_id"])
    old = current_sample(api, manifest["sample_key"])
    knowledge_id = create_collection(api, manifest)
    upload_documents(api, knowledge_id, manifest["_files"])
    wait_until_indexed(api, knowledge_id, len(manifest["_files"]), args.timeout)
    configure_chat(api, knowledge_id, manifest)

    if old:
        api.call("PUT", f"/v1/{old}/sample", json={"sample_key": None})
    api.call("PUT", f"/v1/{knowledge_id}/sample", json={"sample_key": manifest["sample_key"]})
    log(f"{knowledge_id} is now the {manifest['sample_key']!r} sample" + (f" (was {old})" if old else ""))
    if old and args.retire_old:
        api.call("DELETE", f"/v1/{old}", ok=(200, 202, 204))
        log(f"retired {old}")


if __name__ == "__main__":
    sys.exit(main())
