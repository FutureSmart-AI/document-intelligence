"""Field-level accuracy of an extract_local.py run, with score.py's own rubric and judge.

Exact fields are compared exactly (alternatives honoured); every other field is judged on meaning,
twice, and counts as correct only when both passes say so — the same rule as VERIFICATION.md.

  OPENAI_API_KEY=... python score_local.py run.json [--runs 1 2 3] [--out scored.json]
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openai import OpenAI

import score

HERE = Path(__file__).resolve().parent


def field_table(rows: list[dict]) -> dict:
    table = {}
    for f in score.EXACT:
        table[f] = {"correct": sum(r["exact"][f]["ok"] for r in rows), "of": len(rows)}
    for f in score.JUDGED:
        js = [r["judged"].get(f) for r in rows if r["judged"].get(f)]
        table[f] = {"correct": sum(1 for j in js if set(j["verdicts"]) == {"correct"}), "of": len(rows),
                    "disagreed": sum(1 for j in js if not j["agree"])}
    return table


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--runs", nargs="*", type=int)
    ap.add_argument("--out")
    args = ap.parse_args()
    gt = {r["id"]: r for r in json.loads((HERE / "ground_truth.json").read_text(encoding="utf8"))["resumes"]}
    data = json.loads(Path(args.run).read_text(encoding="utf8"))
    results = [x for x in data["results"] if "values" in x and (not args.runs or x["run"] in args.runs)]
    client = OpenAI()
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda x: {**score.score_resume(client, gt[x["id"]], x["values"]), "run": x["run"]},
                             results))
    table = field_table(rows)
    total = sum(v["correct"] for v in table.values())
    possible = sum(v["of"] for v in table.values())
    misses = [{"id": r["id"], "run": r["run"], "field": f, **({"expected": r["exact"][f]["expected"],
                                                                  "got": r["exact"][f]["extracted"]})}
              for r in rows for f in score.EXACT if not r["exact"][f]["ok"]]
    misses += [{"id": r["id"], "run": r["run"], "field": f, "verdicts": j["verdicts"], "reason": j["reason"]}
               for r in rows for f, j in r["judged"].items() if set(j["verdicts"]) != {"correct"}]
    report = {"source": args.run, "documents": len(rows), "fields": table, "correct": total, "possible": possible,
              "misses": misses, "rows": rows}
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf8")
    print(f"{total}/{possible} field values correct over {len(rows)} extractions")
    for f, v in table.items():
        print(f"  {f:22} {v['correct']}/{v['of']}" + (f"  (judge passes disagreed {v['disagreed']})" if v.get("disagreed") else ""))
    for m in misses:
        print("  MISS", json.dumps(m, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
