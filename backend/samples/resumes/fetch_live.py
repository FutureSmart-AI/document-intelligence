"""Read every document's extracted metadata from a hosted collection, in extract_local.py's format,
so score_local.py scores the live sample exactly as it scores a local run.

  KNOWLEDGE_MASTER_KEY=... python fetch_live.py <knowledge_id> --out live.json \
      [--api https://app.futuresmart.ai/api/knowledge]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

import score

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("knowledge_id")
    ap.add_argument("--api", default="https://app.futuresmart.ai/api/knowledge")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    by_file = {Path(r["file"]).name: r for r in json.loads((HERE / "ground_truth.json").read_text(encoding="utf8"))["resumes"]}
    with httpx.Client(base_url=args.api, headers={"x-api-key": os.environ["KNOWLEDGE_MASTER_KEY"]}, timeout=60) as c:
        body = c.get(f"/v2/{args.knowledge_id}/documents", params={"page": 1, "page_size": 100}).raise_for_status().json()
        docs = body.get("document") or body.get("documents") or []
        results = []
        for d in docs:
            name = d.get("file_name") or d.get("name")
            meta = c.get(f"/v1/{args.knowledge_id}/documents/{d['object_id']}/metadata").raise_for_status().json()
            values = score.extracted_fields(meta)
            gt = by_file[name]
            results.append({"id": gt["id"], "run": 1, "name": gt["fields"]["candidate_name"], "object_id": d["object_id"],
                            "status": d.get("status"), "values": values})
    results.sort(key=lambda x: x["id"])
    Path(args.out).write_text(json.dumps({"source": f"{args.api} {args.knowledge_id}", "results": results},
                                         indent=2, ensure_ascii=False), encoding="utf8")
    print("documents", len(results), "statuses", sorted({x["status"] for x in results}))
    for x in results:
        print(f"  {x['id']} {x['name']:20} years {x['values'].get('years_of_experience')}")


if __name__ == "__main__":
    main()
