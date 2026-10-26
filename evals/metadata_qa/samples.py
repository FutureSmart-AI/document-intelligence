"""The live sample collections, resolved by sample key, so a re-provisioned sample is checked
without editing ids. Resume expectations come from backend/samples/resumes/ground_truth.json."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
RESUME_TRUTH = [r["fields"] for r in json.loads(
    (ROOT / "backend/samples/resumes/ground_truth.json").read_text(encoding="utf8"))["resumes"]]


def sample_ids(c: httpx.Client) -> dict[str, str]:
    """{sample_key: knowledge_id} for every live sample."""
    return {s["sample_key"]: s["knowledge_id"] for s in c.get("/v1/samples").raise_for_status().json().get("samples", [])}


def _range(label: str):
    """'2-3' -> (2, 3); '15+' -> (15, inf); '7' -> (7, 7); anything else -> None."""
    s = str(label).strip()
    try:
        if s.endswith("+"):
            return float(s[:-1]), float("inf")
        if "-" in s[1:]:
            lo, hi = s.split("-", 1)
            return float(lo), float(hi)
        return float(s), float(s)
    except ValueError:
        return None


def regroup(expected: dict[str, float], groups) -> dict[str, float]:
    """Re-aggregate per-value counts ({'2': 2, '3': 1}) onto numeric range groups ('2-3'), so a
    chart or answer that buckets a numeric field is compared on its own buckets. Returned unchanged
    when either side is not numeric or the groups miss a value."""
    ranges = {g: _range(g) for g in groups}
    values = {k: _range(k) for k in expected}
    if not ranges or None in ranges.values() or None in values.values() or any(lo != hi for lo, hi in values.values()):
        return expected
    out = {g: 0.0 for g in ranges}
    for k, n in expected.items():
        v = values[k][0]
        hit = [g for g, (lo, hi) in ranges.items() if lo <= v <= hi]
        if len(hit) != 1:
            return expected
        out[hit[0]] += n
    return out
