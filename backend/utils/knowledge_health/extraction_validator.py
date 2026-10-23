"""Validate an enrichment result — a quality signal that can actually FAIL.

WHY THIS EXISTS
---------------
`fields_populated` / `field_data[].populated`, and the `avg_field_coverage`
built from them, answer exactly one question: is this field non-empty. That is
not a quality metric, and reading it as one hid a real 11x defect.

Measured against production on 2026-08-27
(docs/plan/enrichment-analytics/mode-model-experiment.md): one bank statement
ingested four times, graded against the control totals the statement prints on
itself. Row-level amount accuracy across the four cells was 8.3%, 95.9%, 11.1%
and 12.0%. Every one of the four reported `avg_field_coverage: 100.0` and
`status: success`, because every one returned all 8 fields non-empty.

Coverage cannot be repaired into a correctness signal — non-emptiness is all it
can observe. So correctness gets its own measurement, reported beside coverage
rather than folded into it:

    coverage   = did the extractor return something   (deterministic, free)
    validation = is what it returned right & complete (this module)

TIER 1 LEADS, AND THAT ORDER IS A FINDING
-----------------------------------------
Every defect in that experiment was caught by deterministic arithmetic over the
statement's own balance column: no LLM, no cost, exact answers. An LLM judge
asked "is this value grounded in the source?" would have PASSED the broken
cells, because the unsigned `1,500.00` really does appear in the document — the
error is an absent minus sign, which only arithmetic can see.

So Tier 1 is deterministic and always runs. Tier 2 spends money only on what
Tier 1 structurally cannot see: whether a value is invented, and whether
something present in the source was silently dropped.

WHAT EACH TIER CHECKS
---------------------
Tier 1 (deterministic, free, always):
  * type_conformance   — the value parses as its declared `field_type`
  * choice_conformance — `restrict_to_choices` fields hold a listed value
  * structural         — every `List[dict]` row carries the declared schema keys
  * numeric_series     — a per-row delta reconciles against a running total
                         (the balance check, generalised — see _numeric_series)

Tier 2 (LLM judge, sampled, costs money):
  * groundedness  — each scalar value is supported by the source text, with the
                    supporting span quoted back so a human can check the judge
  * completeness  — instances present in the source but missing from the output
                    (the dropped-transaction case, which Tier 1 finds only when
                    a reconcilable numeric series happens to exist)

CONFIGURATION
-------------
Two independent booleans in the environment, so the paid tier can be switched off
without losing the free one:

    EXTRACTION_VALIDATION_ENABLED       master switch; false makes the endpoint 503
    EXTRACTION_VALIDATION_LLM_ENABLED   Tier 2 only; false keeps Tier 1 running
                                        at zero cost
    EXTRACTION_VALIDATION_PROVIDER      default "anthropic"
    EXTRACTION_VALIDATION_MODEL         default "claude-sonnet-5"
    EXTRACTION_VALIDATION_MAX_DOCS      per-call cap on judged documents

The judge is pinned to Anthropic's latest Sonnet as ONE fixed yardstick. The
ENRICHING model already changes with the processing mode
(llm_factory.get_enrichment_config level 2), so a judge that also moved would
leave a mode comparison confounded in two places at once and neither factor
readable.

LANGSMITH
---------
Tier 2 runs under an `ExtractionValidation` traceable and stamps knowledge_id,
judge_model, enriching_model and self_graded onto the run. Without that the judge
appears in the same project as the chat traces as an unattributed LLM call --
no way to tell which document it graded, and no way to separate judge spend from
enrichment spend when reading LangSmith directly. Tracing never fails a
validation: a run-tree error is swallowed.

THE JUDGE IS NOT THE ENRICHER
-----------------------------
`validation_model` is stored separately from `model` on the same row, and the
resolver below refuses to silently reuse the enriching model. A model grading
its own output has a self-preference bias; because the enriching model varies
with the processing mode, without two distinct columns a "model X validates
well" row cannot be told apart from "X was asked to grade itself".

NULL IS NOT ZERO
----------------
An unvalidated row carries NULL scores and MUST render as "not validated".
0.0 is a real score meaning everything checked was wrong — the opposite claim.
Nothing in this module ever coalesces one into the other.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel, Field as PField

from utils.logger import logger

# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------

PASS = "pass"
FAIL = "fail"
SKIP = "skip"          # not checkable — NOT a pass, and never scored
UNKNOWN = "unknown"    # the judge declined or errored

# Statuses written to knowledge_enrichment_events.validation_status
ST_PASSED = "passed"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"
ST_ERROR = "error"

# A numeric series must reconcile for at least this share of its rows before we
# accept the pairing as the intended running-total relationship. Below it we
# conclude there is no such relationship and SKIP, rather than reporting every
# row as broken: a field that was never a ledger should not score 0.
_SERIES_ACCEPT_RATIO = 0.60

# Tolerance for float comparison of money-like values (two decimal places).
_EPS = 0.02


def _to_float(value: Any) -> Optional[float]:
    """Parse a money/number-ish value. Returns None when it is not numeric.

    Handles the shapes extractors actually emit: "1,500.00", "-1,500.00",
    "(1,500.00)" for negatives, "INR 1,500", and bare floats/ints. A returned
    None means "not a number", which callers treat as un-checkable rather than
    as zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    # Strip currency symbols/codes and thousands separators, keep sign+digits+dot
    s = re.sub(r"[^\d.\-+]", "", s)
    if s in ("", "-", "+", "."):
        return None
    try:
        out = float(s)
    except ValueError:
        return None
    return -out if negative else out


# ---------------------------------------------------------------------------
# Tier 1 — deterministic
# ---------------------------------------------------------------------------

def _is_iso_date(v: Any) -> bool:
    """Is the value a canonical ISO-8601 calendar date, 'YYYY-MM-DD'?

    A date field is stored as a string (Qdrant payloads are JSON — there is no
    datetime object in storage), so a plain isinstance check would pass on any
    text. What makes the type worth having is that the string is *parseable and
    canonical*: that is the property the Qdrant DATETIME index and every range
    filter depend on. "16 Jun 19" is a FAIL here by design — it is the measured
    "unspecified format" defect, and it silently drops out of range filters.
    """
    if not isinstance(v, str):
        return False
    try:
        return date.fromisoformat(v).isoformat() == v
    except (ValueError, TypeError):
        return False


_TYPE_CHECKS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "dict": lambda v: isinstance(v, dict),
    "List[str]": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "List[dict]": lambda v: isinstance(v, list) and all(isinstance(x, dict) for x in v),
    "date": _is_iso_date,
}


def _type_conformance(value: Any, field_type: Optional[str]) -> Tuple[str, Optional[str]]:
    """Does the value match its declared field_type?

    A str field holding "116149.46" is a PASS here — the declaration is what is
    being checked, not whether the declaration was wise. Numeric strings inside
    a List[dict] are the norm for extracted tables and are handled by
    _numeric_series, which parses rather than type-checks.
    """
    if field_type is None:
        return SKIP, "no declared field_type"
    check = _TYPE_CHECKS.get(field_type)
    if check is None:
        return SKIP, f"unknown field_type {field_type!r}"
    return (PASS, None) if check(value) else (FAIL, f"expected {field_type}, got {type(value).__name__}")


def _choice_conformance(value: Any, cfg: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """For a restrict_to_choices field, is the value one of the listed ones?

    This is the check that catches facet SPRAWL, which is not a cosmetic issue:
    the RAG agent builds `metadata_filter` from these values, and the retrieval
    backend matches them by equality or $in. Observed live — the same document
    yielded `bank_name` "Standard Chartered" on one run and "Standard Chartered
    Bank" on another, so a filter on either value silently misses the other
    document.

    Case- and whitespace-insensitive: "savings" and "Savings" are the same facet
    and flagging that as a violation would bury the real ones.
    """
    if not cfg.get("restrict_to_choices"):
        return SKIP, "not restricted"
    choices = cfg.get("sample_values") or []
    if not choices:
        return SKIP, "restrict_to_choices set but no sample_values to check against"
    allowed = {str(c).strip().lower() for c in choices}
    values = value if isinstance(value, list) else [value]
    offenders = [
        v for v in values
        if v is not None and str(v).strip().lower() not in allowed
    ]
    if offenders:
        return FAIL, f"value(s) not in sample_values: {offenders[:5]}"
    return PASS, None


def _structural(value: Any, cfg: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """For List[dict], do the rows carry the declared schema keys?

    Reports the share of rows missing at least one declared key. A row that is
    entirely empty is counted too — an extractor that pads its output with blank
    rows inflates row counts, which then reads as better completeness.
    """
    if cfg.get("field_type") != "List[dict]" or not isinstance(value, list) or not value:
        return SKIP, "not a populated List[dict]"
    declared = set((cfg.get("field_schema") or cfg.get("schema") or {}).keys())
    if not declared:
        return SKIP, "no declared schema"
    missing_rows = 0
    empty_rows = 0
    for row in value:
        if not isinstance(row, dict):
            missing_rows += 1
            continue
        if declared - set(row.keys()):
            missing_rows += 1
        if all(row.get(k) in (None, "") for k in declared):
            empty_rows += 1
    problems = []
    if missing_rows:
        problems.append(f"{missing_rows}/{len(value)} rows missing declared key(s)")
    if empty_rows:
        problems.append(f"{empty_rows}/{len(value)} rows entirely empty")
    return (FAIL, "; ".join(problems)) if problems else (PASS, None)


def _numeric_series(
    value: Any, cfg: Dict[str, Any]
) -> Tuple[str, Optional[str], Optional[float], int]:
    """Reconcile a per-row delta against a running total. The balance check,
    generalised so it needs no per-domain configuration.

    The idea: in any ledger-shaped table one numeric column is a RUNNING TOTAL
    and another is the PER-ROW CHANGE, so `total[n] - total[n-1]` must equal
    `delta[n]`. We do not know which columns those are, so every ordered pair of
    numeric-ish keys is tried and the best-reconciling pairing wins.

    Two deliberate properties:

    * The pairing is only ACCEPTED when it reconciles for at least
      _SERIES_ACCEPT_RATIO of rows. If no pairing clears that bar the field
      probably is not a ledger, and we SKIP — reporting 0% on a table that never
      had a running total would be a false alarm, and worse, an unfalsifiable
      one.
    * Sign is checked, not just magnitude. This is the whole point: the failure
      observed in production was 44 of 48 amounts carrying the RIGHT magnitude
      and the WRONG sign, because the parse lost which column (Deposit vs
      Withdrawal) the number came from. A magnitude-only check passes all 44 —
      an earlier version of this analysis did exactly that and reported a clean
      bill of health.

    Returns (verdict, detail, reconciled_ratio, checked_rows). `checked_rows` is
    the field's WEIGHT in the document score: a 48-row ledger is 48 observations
    about the extraction and a boolean is one, so averaging them flat lets one
    catastrophic table vanish into a handful of trivially-passing scalars.
    Measured live: a document whose ledger was 88% wrong scored 0.945 under a flat
    mean over 16 fields (15 x 1.0 + 0.12) / 16. See run_deterministic_checks.
    """
    if not isinstance(value, list) or len(value) < 3:
        return SKIP, "fewer than 3 rows", None, 0

    keys = [k for k in (value[0].keys() if isinstance(value[0], dict) else [])]
    numeric_keys = [
        k for k in keys
        if sum(1 for r in value if isinstance(r, dict) and _to_float(r.get(k)) is not None) >= len(value) * 0.5
    ]
    if len(numeric_keys) < 2:
        return SKIP, "fewer than 2 numeric columns", None, 0

    # Two ratios per candidate pairing, and the distinction between them is the
    # whole correctness of this check:
    #
    #   magnitude_ratio — |delta| reconciles against |total[n] - total[n-1]|.
    #                     Identifies WHETHER a ledger relationship exists, and is
    #                     blind to sign by design.
    #   signed_ratio    — the delta reconciles WITH its sign. The actual score.
    #
    # An earlier version picked the pairing by signed_ratio and skipped anything
    # below the acceptance bar. That inverted the result on real data: a table
    # whose amounts were all correct in magnitude and all wrong in sign
    # reconciled on 4 of 48 rows, fell under the bar, and was therefore dismissed
    # as "not a ledger" — scoring 100%. The one genuinely good extraction, at
    # 47/49, was the only cell reported as failing. Selecting on magnitude fixes
    # that: sign loss no longer hides the relationship it corrupts.
    best = None  # (magnitude_ratio, signed_ratio, total_key, delta_key, checked, m_matched, s_matched)
    for total_key in numeric_keys:
        for delta_key in numeric_keys:
            if total_key == delta_key:
                continue
            prev = None
            checked = s_matched = m_matched = 0
            for row in value:
                if not isinstance(row, dict):
                    continue
                t = _to_float(row.get(total_key))
                d = _to_float(row.get(delta_key))
                if t is None:
                    continue
                if prev is None or d is None:
                    prev = t if t is not None else prev
                    continue
                checked += 1
                actual = t - prev
                if abs(d - actual) < _EPS:
                    s_matched += 1
                if abs(abs(d) - abs(actual)) < _EPS:
                    m_matched += 1
                prev = t
            if not checked:
                continue
            m_ratio, s_ratio = m_matched / checked, s_matched / checked
            if best is None or m_ratio > best[0]:
                best = (m_ratio, s_ratio, total_key, delta_key, checked, m_matched, s_matched)

    if best is None:
        return SKIP, "no numeric pairing to reconcile", None, 0

    m_ratio, s_ratio, total_key, delta_key, checked, m_matched, s_matched = best

    if m_ratio < _SERIES_ACCEPT_RATIO:
        # Not even magnitudes line up, so there is genuinely no running-total
        # relationship here — the field is not ledger-shaped and scoring it would
        # be a false alarm. Report the best attempt so the skip is auditable.
        return (
            SKIP,
            f"not ledger-shaped: best pairing {delta_key}->{total_key} matches on "
            f"magnitude for only {m_matched}/{checked} rows "
            f"({m_ratio*100:.0f}%, bar is {_SERIES_ACCEPT_RATIO*100:.0f}%)",
            None, 0,
        )

    # A ledger relationship EXISTS. From here the signed ratio is the score, and
    # a low one is a real defect rather than a reason to skip.
    base = f"{delta_key} is a running-total delta of {total_key} ({m_matched}/{checked} rows by magnitude)"
    if s_matched == checked:
        return PASS, f"{base}; all signs correct", 1.0, checked

    sign_only = m_matched - s_matched
    if sign_only > 0 and s_ratio < 0.5:
        # The signature failure mode: right numbers, wrong direction. Worth
        # naming explicitly because the fix is upstream (a parse that preserves
        # which column the number came from), not a better prompt — and because
        # every value is individually present in the source, so an LLM
        # groundedness judge passes this.
        return (
            FAIL,
            f"{base}; SIGN LOST on {sign_only}/{checked} rows — magnitudes are "
            f"right but direction is not, so credits and debits are "
            f"indistinguishable. Only {s_matched}/{checked} rows carry the "
            f"correct sign.",
            s_ratio, checked,
        )
    return FAIL, f"{base}; {checked - s_matched}/{checked} row(s) disagree", s_ratio, checked


def run_deterministic_checks(
    extracted: Dict[str, Any], field_configs: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Tier 1 over one document. No LLM, no network, never raises.

    Returns a per-field verdict list plus a correctness score computed ONLY over
    fields that were actually checkable. A document whose every field skipped
    yields `correctness_score: None` — "nothing to check" is not "perfect", and
    scoring it 1.0 would let an unvalidatable schema report flawless quality.
    """
    per_field: List[Dict[str, Any]] = []
    scored: List[Tuple[float, int]] = []
    # Active fields that could not be scored at all, and content-bearing fields
    # that came back empty. Either makes a high aggregate a lie by omission.
    unscoreable: List[str] = []
    empty_content_fields: List[str] = []

    by_name = {c.get("field_name"): c for c in field_configs if c.get("field_name")}
    for name, cfg in by_name.items():
        if not cfg.get("is_active", True):
            continue
        value = extracted.get(name)
        checks: Dict[str, Any] = {}

        if value is None or value == [] or value == "":
            # Empty is coverage's business, not correctness'. Recording it as a
            # correctness failure would double-count the same defect in two
            # metrics and make them move together, defeating the point of
            # separating them.
            # Empty is coverage's business for REPORTING, but it must still
            # block a perfect correctness score: a document that extracted no
            # transactions at all scored 1.000 before this, off its scalars
            # alone. You cannot assess the correctness of something absent.
            if cfg.get("field_type") in ("List[dict]", "List[str]"):
                empty_content_fields.append(name)
            per_field.append({
                "field_name": name, "level": cfg.get("level"),
                "verdict": SKIP, "checks": {}, "produced_observation": False,
                "note": "empty — see coverage, not correctness",
            })
            continue

        v, d = _type_conformance(value, cfg.get("field_type"))
        checks["type_conformance"] = {"verdict": v, "detail": d}

        v, d = _choice_conformance(value, cfg)
        checks["choice_conformance"] = {"verdict": v, "detail": d}

        v, d = _structural(value, cfg)
        checks["structural"] = {"verdict": v, "detail": d}

        v, d, ratio, weight = _numeric_series(value, cfg)
        checks["numeric_series"] = {
            "verdict": v, "detail": d, "reconciled_ratio": ratio, "checked_rows": weight,
        }
        if ratio is not None:
            # Weighted by rows checked: a 48-row ledger is 48 observations about
            # this extraction, a boolean is one.
            scored.append((ratio, max(weight, 1)))

        verdicts = [c["verdict"] for c in checks.values()]
        if FAIL in verdicts:
            field_verdict = FAIL
        elif PASS in verdicts:
            field_verdict = PASS
        else:
            field_verdict = SKIP
        # Decide whether this field yielded real evidence BEFORE letting it score.
        produced_observation = ratio is not None or field_verdict in (PASS, FAIL)
        # A populated List[dict] whose numeric-series reconciliation SKIPPED has
        # NOT been meaningfully checked, however many other checks passed on it.
        # Measured live: a 22-row extraction with deposits off by 108,424 scored
        # 1.000 because its type check passed (it really is a list of dicts) and
        # contributed a free 1.0 while the arithmetic never ran. Type conformance
        # is not evidence of correctness, so the field counts as unscoreable and
        # the aggregate is withheld rather than published.
        if cfg.get("field_type") == "List[dict]" and ratio is None:
            produced_observation = False
        if not produced_observation:
            unscoreable.append(name)

        # A scalar field whose only checkable property is a pass/fail still
        # contributes 1.0 or 0.0 — otherwise a wrong facet value would be flagged
        # in `checks` and yet leave the score untouched.
        if ratio is None and field_verdict in (PASS, FAIL) and produced_observation:
            scored.append((1.0 if field_verdict == PASS else 0.0, 1))

        per_field.append({
            "field_name": name, "level": cfg.get("level"),
            "verdict": field_verdict, "checks": checks,
            "produced_observation": produced_observation,
        })

    # WEIGHTED, not a flat mean over fields. A flat mean lets one catastrophic
    # field disappear into however many trivially-passing scalars the schema
    # happens to have: measured live, a document whose 55-row ledger was 88%
    # wrong scored 0.945, because 15 of its 16 fields were type/boolean checks
    # that pass for free. That is the same false comfort as coverage, one layer
    # up, and it is what this module exists to stop.
    total_weight = sum(w for _, w in scored)
    correctness = (
        round(sum(r * w for r, w in scored) / total_weight, 4) if total_weight else None
    )
    # The unweighted mean is kept only so the two can be COMPARED — a wide gap
    # between them is the signal that one field is carrying the whole result.
    unweighted = round(sum(r for r, _ in scored) / len(scored), 4) if scored else None

    # The single actionable number. An aggregate can always be argued with; the
    # worst field names what to go and fix.
    worst_field, worst_score = None, None
    for f in per_field:
        ns = (f.get("checks") or {}).get("numeric_series") or {}
        r = ns.get("reconciled_ratio")
        if r is None:
            continue
        if worst_score is None or r < worst_score:
            worst_field, worst_score = f["field_name"], round(float(r), 4)

    # THE REFUSAL. Measured live: `1.000 over 5 observations` and
    # `0.964 over 56 observations` were both being reported as a correctness
    # score, and the first belonged to a document whose ledger was never
    # examined (22 of ~55 rows, deposits off by 108,424) or was empty outright.
    # A score computed only from checks that pass for free is not evidence of
    # correctness, so it is withheld rather than published: None means "no
    # opinion", which is the truthful answer. `not_scoreable` names why.
    not_scoreable = empty_content_fields + [
        n for n in unscoreable if n not in empty_content_fields
    ]
    if not_scoreable and correctness is not None:
        correctness = None

    any_fail = any(f["verdict"] == FAIL for f in per_field)
    return {
        "not_scoreable": not_scoreable,
        "empty_content_fields": empty_content_fields,
        "per_field": per_field,
        "correctness_score": correctness,
        "unweighted_correctness": unweighted,
        "worst_field": worst_field,
        "worst_field_score": worst_score,
        "checked_fields": len(scored),
        "checked_observations": total_weight,
        "status": ST_FAILED if any_fail else (ST_PASSED if scored else ST_SKIPPED),
    }


def _trim_tier1_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Summarise Tier 1's inputs instead of shipping them to LangSmith verbatim.

    The raw inputs are the whole extracted metadata — a fifty-five-row ledger for
    one bank statement — plus every field config, on every document. None of it
    is diagnostic at this level: the per-field verdicts are in the run's OUTPUT
    and the full payload is persisted in `validation_data`, so uploading the
    document's contents again is cost and exposure without a reader for it.
    """
    extracted = inputs.get("extracted") or {}
    configs = inputs.get("field_configs") or []
    return {
        "knowledge_id": inputs.get("knowledge_id"),
        "object_id": inputs.get("object_id"),
        "extracted_field_names": sorted(extracted.keys())[:50],
        "extracted_field_count": len(extracted),
        "configured_field_count": len(configs),
        "active_field_count": sum(1 for c in configs if c.get("is_active", True)),
    }


def _trim_judge_inputs(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Summarise the judge's inputs; the child LLM run already holds the prompt.

    `source_text` is up to _JUDGE_SOURCE_LIMIT characters of the document. It is
    interpolated into the prompt, so the nested ChatAnthropic run carries it in
    full and remains the place to audit a wrong verdict — recording it a second
    time on the parent duplicates every judged document into the trace store.
    Counts are kept because "how much source did the judge actually see" is the
    first question a suspicious completeness number raises.
    """
    source = inputs.get("source_text") or ""
    extracted = inputs.get("extracted") or {}
    return {
        "knowledge_id": inputs.get("knowledge_id"),
        "judge_model": inputs.get("judge_model"),
        "judge_provider": inputs.get("judge_provider"),
        "enriching_model": inputs.get("enriching_model"),
        "source_chars": len(source),
        "source_truncated": len(source) > _JUDGE_SOURCE_LIMIT,
        "source_chars_sent": min(len(source), _JUDGE_SOURCE_LIMIT),
        "extracted_field_names": sorted(extracted.keys())[:50],
        "field_config_count": len(inputs.get("field_configs") or []),
    }


@traceable(
    name="ExtractionValidation.Tier1Deterministic",
    run_type="tool",
    tags=["extraction-validation", "tier1-deterministic"],
    process_inputs=_trim_tier1_inputs,
)
def run_deterministic_checks_traced(
    extracted: Dict[str, Any],
    field_configs: List[Dict[str, Any]],
    *,
    knowledge_id: Optional[str] = None,
    object_id: Optional[str] = None,
) -> Dict[str, Any]:
    """`run_deterministic_checks` under its own LangSmith run.

    Tier 1 makes no LLM call, so nothing about it reached the trace and a reader
    comparing tiers in LangSmith could see only the judge — the free tier that
    actually caught the production defect was invisible next to the paid one.
    The wrapper exists so both tiers appear side by side under one parent run;
    the scoring function stays pure and callable without tracing.
    """
    result = run_deterministic_checks(extracted, field_configs)
    try:
        rt = get_current_run_tree()
        if rt is not None:
            rt.metadata.update({
                "tier": "tier1_deterministic",
                "knowledge_id": knowledge_id,
                "object_id": object_id,
                "correctness_deterministic": result.get("correctness_score"),
                "unweighted_correctness": result.get("unweighted_correctness"),
                "checked_fields": result.get("checked_fields"),
                "checked_observations": result.get("checked_observations"),
                "worst_field": result.get("worst_field"),
                "worst_field_score": result.get("worst_field_score"),
                "not_scoreable": result.get("not_scoreable"),
                "status": result.get("status"),
                # Stated rather than left as a bare NULL. Tier 1 is handed the
                # extraction and the field config and never the source document,
                # so it can count the rows it was GIVEN but not the rows that
                # exist — and completeness is precisely that comparison. This is
                # a structural limit of the tier, not a failed check.
                "completeness_deterministic": None,
                "completeness_deterministic_status": "unavailable_by_design",
                "completeness_deterministic_reason": (
                    "Tier 1 never receives the source text, so it has no source "
                    "instance count to compare the extraction against."
                ),
            })
    except Exception:
        # Tracing is observability, never a reason to fail a validation.
        pass
    return result


# ---------------------------------------------------------------------------
# Tier 2 — LLM judge
# ---------------------------------------------------------------------------

class JudgedField(BaseModel):
    """One field's groundedness verdict."""
    field_name: str = PField(description="The field being judged")
    supported: str = PField(description='Exactly one of: "yes", "no", "unknown"')
    evidence: Optional[str] = PField(
        default=None,
        description="Shortest span from the SOURCE that shows the value. Empty when unsupported.",
    )
    note: Optional[str] = PField(default=None, description="One short clause of reasoning")


class JudgedCompleteness(BaseModel):
    """Instance counts for ONE list-shaped field, reported whether or not they agree.

    Counts are requested for every repeated field, INCLUDING the ones that match,
    because "the judge reported no shortfall" and "the judge never counted" are
    otherwise the same empty list. When only shortfalls were reported, a perfect
    extraction produced no rows, `ratios` came out empty, and completeness was
    recorded as NULL — indistinguishable from the metric being switched off or
    the judge having failed. A matching pair now scores 1.0 explicitly.
    """
    field_name: str
    extracted_count: int = PField(description="Instances present in the extraction")
    source_count: int = PField(description="Instances visible in the source")
    note: Optional[str] = PField(default=None)


class JudgeResult(BaseModel):
    """The judge's whole reply.

    Passed to `llm.with_structured_output`, following the convention in
    chat/rag_agent/retrieval_verifier.py rather than parsing JSON out of free
    text: the provider enforces the shape, so "the judge replied but not in JSON"
    stops being a failure mode this module has to handle.
    """
    fields: List[JudgedField] = PField(default_factory=list)
    completeness: List[JudgedCompleteness] = PField(default_factory=list)


_JUDGE_PROMPT = """You are auditing a metadata extraction against its source document.

CONFIGURED FIELDS — judge exactly these, and no others:
---
{fields}
---

For each configured field above, decide whether the extracted value is SUPPORTED by the source text.

Rules:
- "supported" means the value is stated in, or unambiguously implied by, the source. Quote the shortest span that shows it.
- If the value is plausible but NOT actually present in the source, mark it unsupported. Plausibility is not evidence.
- If the source is truncated such that you cannot tell, mark it "unknown" rather than guessing.
- Judge only what is asked. Do not comment on formatting, units, or style.
- A configured field MISSING or empty in the extracted values is still judged, not skipped:
  mark it "no" if the source does state a value the extraction failed to capture, and
  "unknown" if the source does not state one either. A field the extraction dropped is a
  defect the audit must be able to see.
- Do not invent fields. If a key appears in the extracted values but not in the configured
  list above, ignore it.

Then judge COMPLETENESS.

- Consider every field that holds a LIST or a repeated structure — a table of transactions, a set of line items, an array of parties.
- For each one, report BOTH counts: how many instances the extraction contains, and how many are visible in the source. Report them EVEN WHEN THE TWO ARE EQUAL — a field that was extracted in full is a result, not a non-answer.
- Skip single-valued fields (a name, a date, one total). If the document has no list-shaped field at all, return an empty completeness list.
- Count what is actually visible. If the source is truncated mid-list, count only what you can see and say so in the note rather than estimating the full length.

SOURCE DOCUMENT (may be truncated):
---
{source}
---

EXTRACTED VALUES:
---
{extracted}
---
"""

# Source text sent to the judge. A judge that sees only the first page will
# report the rest of the document as "missing", so a truncated audit must be
# labelled as such rather than trusted — see `source_truncated` in the result.
_JUDGE_SOURCE_LIMIT = 24000

# The rendered field list. Small by design: it is a schema, not content, and a
# runaway description must not crowd out the source text it shares a budget with.
_JUDGE_FIELDS_LIMIT = 4000
_JUDGE_DESCRIPTION_LIMIT = 200


def _render_field_list(
    field_configs: List[Dict[str, Any]], extracted: Dict[str, Any]
) -> str:
    """Render the configured fields for the judge prompt.

    This exists because the prompt asked the judge to rule on "each field below"
    and no field list was ever supplied — `field_configs` reached this module,
    sat in the signature, and was never interpolated. The judge therefore
    inferred its own field list from whichever keys survived in `extracted`,
    with three consequences: the denominator moved between runs (the source of
    the observed variance), a field the extraction dropped entirely could not be
    judged because the judge never knew to look for it, and the 2x2 was scored
    against a set of fields nobody chose.

    Each line carries the declared type and description, so the judge rules
    against the schema the extractor was given rather than one it reconstructs.
    A field absent from `extracted` is marked explicitly — the judge cannot infer
    absence from a list it is reading for the first time.
    """
    lines: List[str] = []
    for config in field_configs:
        name = config.get("field_name")
        if not name:
            continue
        # `is_active` is the enabled flag; a retired field is not part of the
        # contract the extractor was working to and must not be judged against.
        if config.get("is_active") is False:
            continue
        parts = [f"- {name} ({config.get('field_type') or 'str'}"]
        if config.get("is_required"):
            parts.append(", required")
        parts.append(")")
        description = (config.get("description") or "").strip()
        if description:
            parts.append(f": {description[:_JUDGE_DESCRIPTION_LIMIT]}")
        if name not in extracted or extracted.get(name) in (None, "", [], {}):
            parts.append("  [NOT PRESENT in the extracted values]")
        lines.append("".join(parts))

    if not lines:
        # No configs reach the judge only if the caller passed none. Say so
        # plainly rather than sending an empty block that reads as "no fields
        # exist" — the runner already returns early when configs are missing.
        return "(no field configuration was supplied)"

    rendered = "\n".join(lines)
    if len(rendered) > _JUDGE_FIELDS_LIMIT:
        rendered = rendered[:_JUDGE_FIELDS_LIMIT] + "\n… field list truncated"
    return rendered


@traceable(
    name="ExtractionValidation.Tier2Judge",
    run_type="chain",
    tags=["extraction-validation", "tier2-llm"],
    process_inputs=_trim_judge_inputs,
)
async def run_llm_judge(
    *,
    source_text: str,
    extracted: Dict[str, Any],
    field_configs: List[Dict[str, Any]],
    owner_id: Optional[str],
    knowledge_id: str,
    judge_provider: Optional[str],
    judge_model: Optional[str],
    enriching_model: Optional[str] = None,
    judge_effort: Optional[str] = None,
) -> Dict[str, Any]:
    """Tier 2 over one document. Never raises — returns a result with
    `status: error` and a reason instead, because a judge failure must not fail
    an ingestion that already succeeded.

    `enriching_model` is accepted only so the self-grading case can be RECORDED.
    We do not refuse to run in that case — a same-model audit still catches
    outright fabrication — but the result carries `self_graded: true` so the
    analytics layer can mark those scores as biased rather than presenting them
    beside independent ones as if they were comparable.
    """
    from chat.byok_fallback import run_with_key_fallback

    started = time.time()

    # Stamp the run so a validation trace is findable in LangSmith by the same
    # keys the analytics row carries. Without this the judge shows up as an
    # unattributed LLM call: same project as the chat traces, no way to tell
    # which knowledge base or document it graded, and no way to separate judge
    # spend from enrichment spend when reading LangSmith directly.
    # Mirrors chat/graph.py's KnowledgeChat root run.
    try:
        rt = get_current_run_tree()
        if rt is not None:
            rt.metadata.update({
                "knowledge_id": knowledge_id,
                "judge_model": judge_model,
                "enriching_model": enriching_model,
                # Recorded rather than prevented: a same-model audit still catches
                # outright fabrication, but its scores carry a self-preference bias
                # and must not sit beside independent ones unlabelled.
                "self_graded": bool(
                    enriching_model and judge_model
                    and enriching_model.strip().lower() == judge_model.strip().lower()
                ),
                "tier": "tier2_llm_judge",
                "judge_effort": judge_effort,
            })
    except Exception:
        # Tracing is observability, never a reason to fail a validation.
        pass

    truncated = len(source_text) > _JUDGE_SOURCE_LIMIT
    prompt = (
        _JUDGE_PROMPT
        .replace("{fields}", _render_field_list(field_configs or [], extracted))
        .replace("{source}", source_text[:_JUDGE_SOURCE_LIMIT])
        .replace("{extracted}", json.dumps(extracted, ensure_ascii=False, default=str)[:12000])
    )

    self_graded = bool(
        enriching_model and judge_model and enriching_model.strip().lower() == judge_model.strip().lower()
    )
    if self_graded:
        logger.warning(
            f"[validation] judge model == enriching model ({judge_model!r}) for "
            f"knowledge_id={knowledge_id}: scores are self-graded and carry a "
            f"self-preference bias. Recording them as such."
        )

    # Structured output built inside the callable so a retry after a rejected
    # BYOK key rebuilds the wrapper on the platform-key model — same reason
    # retrieval_verifier.py constructs it inside its own `_invoke`.
    async def _invoke(llm):
        return await llm.with_structured_output(JudgeResult).ainvoke(prompt)

    try:
        outcome = await run_with_key_fallback(
            _invoke, owner_id=owner_id, knowledge_id=knowledge_id,
            source="extraction_validator",
            provider=judge_provider, model=judge_model,
            temperature=0, for_function_calling=False,
            # Ignored by the factory unless set. `temperature=0` above is also
            # dropped for the model generations that reject it, which is why a
            # judge asking for determinism may get neither knob and must be
            # measured over repeats rather than assumed stable.
            effort=judge_effort,
        )
        parsed: JudgeResult = outcome.value
        resolved_model = getattr(outcome, "model", None) or judge_model
        resolved_provider = getattr(outcome, "provider", None) or judge_provider
    except Exception as e:
        # Fail to NO OPINION, never to "passed". retrieval_verifier fails OPEN
        # (defaults to "sufficient") because its job is deciding whether to retry
        # and a wrong default costs one unverified answer. Here the opposite
        # applies: this module exists because a metric that always said "fine"
        # hid a real defect, so a judge failure that recorded `passed` would
        # reintroduce exactly that bug. NULL scores + status=error is the honest
        # result, and the analytics layer renders it as unvalidated.
        logger.error(
            f"[validation] judge failed for knowledge_id={knowledge_id}: "
            f"{type(e).__name__}: {e}", exc_info=True
        )
        return {
            "status": ST_ERROR, "reason": f"{type(e).__name__}: {e}",
            "validation_ms": int((time.time() - started) * 1000),
            "correctness_score": None, "completeness_score": None,
            "validation_model": judge_model, "self_graded": self_graded,
        }

    fields = [f.model_dump() for f in parsed.fields]
    # "unknown" is excluded from the denominator, not counted as a failure: a
    # judge that could not see the relevant part of a truncated document has no
    # opinion, and treating that as "unsupported" would penalise long documents.
    decided = [f for f in fields if str(f.get("supported", "")).lower() in ("yes", "no")]
    supported = [f for f in decided if str(f.get("supported")).lower() == "yes"]
    correctness = round(len(supported) / len(decided), 4) if decided else None

    comp = [c.model_dump() for c in parsed.completeness]
    # A matching pair contributes 1.0, so a fully-extracted document scores 1.0
    # instead of NULL. `ratios` is empty ONLY when the document has no
    # list-shaped field for the judge to count, which is why completeness can
    # still legitimately be NULL — but it now means "nothing countable here",
    # not "nobody looked".
    ratios = []
    over_extracted = []
    for c in comp:
        try:
            src = float(c.get("source_count") or 0)
            got = float(c.get("extracted_count") or 0)
        except (TypeError, ValueError):
            continue
        if src <= 0:
            continue
        # Capped at 1.0: this metric measures OMISSION, and an extraction with
        # more rows than the source is not 130% complete. Capping alone would
        # silently swallow it, so the field is named separately — extra rows are
        # a hallucination signal and belong in a precision metric, which this is
        # not. See the omission-vs-hallucination split in the eval-metrics notes.
        if got > src:
            over_extracted.append(c.get("field_name"))
        ratios.append(min(got / src, 1.0))
    completeness = round(sum(ratios) / len(ratios), 4) if ratios else None
    if over_extracted:
        logger.warning(
            f"[validation] extraction reported MORE instances than the source for "
            f"{over_extracted} on knowledge_id={knowledge_id}: completeness is "
            f"capped at 1.0 and cannot see this — treat as a hallucination signal"
        )

    # The judge's OUTCOME on its own run. The stamp near the top of this function
    # records what the judge was ASKED; without this one a reader sees the
    # request and the token cost but not the verdict, and has to join back to the
    # database to learn whether the call that cost money produced a score.
    try:
        rt = get_current_run_tree()
        if rt is not None:
            rt.metadata.update({
                "correctness_llm": correctness,
                "correctness_llm_decided_fields": len(decided),
                "correctness_llm_unknown_fields": len(fields) - len(decided),
                "completeness_llm": completeness,
                "completeness_llm_counted_fields": len(ratios),
                # Distinguishes the two ways completeness comes back NULL: no
                # list-shaped field existed to count, versus the judge having
                # failed. Only the first is a legitimate absence.
                "completeness_llm_status": (
                    "scored" if completeness is not None else "no_countable_field"
                ),
                "over_extracted_fields": over_extracted,
                "source_truncated": truncated,
                "self_graded": self_graded,
            })
    except Exception:
        pass

    return {
        # A judge that decided NOTHING is `skipped`, not `passed`. The refusal
        # itself is right — `correctness is None` means every field came back
        # "unknown", so there is no opinion to publish — but labelling that
        # absence a pass is the same bug this module exists to prevent, one
        # level up: a document nobody could score reading as a clean bill of
        # health. `skipped` is also the weakest status in merge_results'
        # precedence, so a Tier 1 verdict now decides the row instead of being
        # overruled by a judge that abstained.
        "status": (
            ST_SKIPPED if correctness is None
            else (ST_PASSED if correctness >= 1.0 else ST_FAILED)
        ),
        "correctness_score": correctness,
        "completeness_score": completeness,
        "validation_model": resolved_model,
        "validation_provider": resolved_provider,
        "self_graded": self_graded,
        "source_truncated": truncated,
        "fields": fields,
        "completeness_detail": comp,
        "over_extracted_fields": over_extracted,
        "validation_ms": int((time.time() - started) * 1000),
    }


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


async def run_llm_judge_repeated(
    *,
    repeats: int = 1,
    **judge_kwargs: Any,
) -> Dict[str, Any]:
    """Run the judge `repeats` times over one document and report the SPREAD.

    Measured on a 55-row bank statement, four consecutive judge calls on byte-
    identical input returned correctness 0.75, 0.75, 1.0, 0.7273 and completeness
    1.0, 1.0, NULL, NULL. A single call therefore reports a sample from a
    distribution while looking exactly like a measurement, and two runs of the
    same experiment can rank two models in opposite orders for no reason but
    sampling.

    So the aggregate carries `*_min`/`*_max`/`*_runs` beside the mean. A reader
    who sees `0.81 over 4 runs, min 0.73 max 1.0` knows not to trust a 0.05
    difference between models; a reader given `0.81` alone does not.

    `repeats=1` reproduces the old single-call behaviour exactly, spread fields
    included, so the honest-but-noisy case is still labelled as one sample rather
    than silently presented as a measurement.
    """
    runs: List[Dict[str, Any]] = []
    for _ in range(max(1, repeats)):
        runs.append(await run_llm_judge(**judge_kwargs))

    # A run that errored contributes nothing to the mean but is still COUNTED, so
    # "0.9 over 4 runs" cannot quietly mean "0.9 from the one run that worked".
    corr = [r["correctness_score"] for r in runs if r.get("correctness_score") is not None]
    comp = [r["completeness_score"] for r in runs if r.get("completeness_score") is not None]
    errored = [r for r in runs if r.get("status") == ST_ERROR]

    # The last successful run supplies the per-field detail: averaging field-level
    # verdicts across runs would invent a verdict no run actually gave, and the
    # detail exists to be read by a human, not aggregated.
    representative = next((r for r in reversed(runs) if r.get("status") != ST_ERROR), runs[-1])

    aggregate = dict(representative)
    aggregate.update({
        "correctness_score": _mean(corr),
        "correctness_min": min(corr) if corr else None,
        "correctness_max": max(corr) if corr else None,
        "correctness_scored_runs": len(corr),
        "completeness_score": _mean(comp),
        "completeness_min": min(comp) if comp else None,
        "completeness_max": max(comp) if comp else None,
        "completeness_scored_runs": len(comp),
        "judge_runs": len(runs),
        "judge_errored_runs": len(errored),
        "per_run": [
            {
                "status": r.get("status"),
                "correctness_score": r.get("correctness_score"),
                "completeness_score": r.get("completeness_score"),
                "reason": r.get("reason"),
                "validation_ms": r.get("validation_ms"),
            }
            for r in runs
        ],
    })
    # Status from the AGGREGATE, not from whichever run happened to be
    # representative: that inherited an arbitrary run's verdict, so a mean of
    # 0.875 could report `passed` while a single run at 0.875 reported `failed`.
    # Same rule as the single-run path — anything short of 1.0 is a fail.
    mean_corr = aggregate["correctness_score"]
    if corr:
        aggregate["status"] = ST_PASSED if mean_corr >= 1.0 else ST_FAILED
    elif not errored:
        # No run produced a number and none of them failed — every run abstained.
        # Without this the aggregate inherited the representative run's status,
        # which is exactly the `passed`-on-no-opinion bug the single-run path
        # above now avoids. The all-errored case is handled below and outranks
        # this, so a real failure is never downgraded to `skipped`.
        aggregate["status"] = ST_SKIPPED

    # Only every run failing is an error. One failure out of four is noise the
    # spread already shows, and downgrading the whole document for it would throw
    # away three real measurements.
    if len(errored) == len(runs):
        aggregate["status"] = ST_ERROR
        aggregate["reason"] = f"all {len(runs)} judge runs failed: {runs[-1].get('reason')}"
    if len(runs) > 1:
        aggregate["validation_ms"] = sum(
            r.get("validation_ms") or 0 for r in runs
        ) or None
    return aggregate


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

def merge_results(
    tier1: Dict[str, Any], tier2: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Combine the two tiers into the columns migration 046 added.

    Tier 1 WINS on correctness when the two disagree. It is arithmetic against
    the document's own control values; Tier 2 is a model's opinion. The
    production case that motivated all of this is exactly a disagreement of this
    shape — Tier 1 catches a sign error that Tier 2 passes, because the digits
    really are in the source.

    Tier 2 is the only source of completeness: Tier 1 finds a dropped row only
    when a reconcilable numeric series happens to exist.
    """
    correctness = tier1.get("correctness_score")
    if correctness is None and tier2:
        correctness = tier2.get("correctness_score")

    completeness = (tier2 or {}).get("completeness_score")

    statuses = [tier1.get("status"), (tier2 or {}).get("status")]
    # Precedence: failed > error > passed > skipped.
    #
    # ERROR OUTRANKS PASSED, which it previously did not. The old rule was
    # `ST_ERROR in statuses and ST_PASSED not in statuses`, so a tier that
    # errored was overruled by a tier that passed — and a document nothing could
    # score reported "passed". That is the exact failure this module exists to
    # prevent: a five-row extraction of a fifty-five-row statement read as a
    # clean pass, because Tier 1 had no control values to contradict and Tier 2's
    # judge had 400'd. A partial validation is not a pass.
    #
    # FAILED still outranks ERROR: a definite Tier 1 arithmetic failure is a true
    # and actionable verdict whether or not the judge also managed to run.
    #
    # A withheld tier is NOT an error and does not reach here — the runner leaves
    # tier2 as None when the flag is off or the owner is not allowlisted, so
    # `statuses` is [tier1_status, None] and a Tier-1-only pass stays a pass.
    if ST_FAILED in statuses:
        status = ST_FAILED
    elif ST_ERROR in statuses:
        status = ST_ERROR
    elif ST_PASSED in statuses:
        status = ST_PASSED
    else:
        status = ST_SKIPPED

    return {
        "validation_status": status,
        "correctness_score": correctness,
        "completeness_score": completeness,
        "validation_model": (tier2 or {}).get("validation_model"),
        "validation_ms": (tier2 or {}).get("validation_ms"),
        "validation_data": {
            "tier1": tier1.get("per_field", []),
            "tier1_checked_fields": tier1.get("checked_fields"),
            "tier1_checked_observations": tier1.get("checked_observations"),
            "tier1_correctness": tier1.get("correctness_score"),
            # Read these two together with the score above: a wide gap between
            # weighted and unweighted means one field is carrying the result, and
            # worst_field names it.
            "tier1_unweighted_correctness": tier1.get("unweighted_correctness"),
            "tier1_worst_field": tier1.get("worst_field"),
            "tier1_worst_field_score": tier1.get("worst_field_score"),
            "tier2": {
                k: (tier2 or {}).get(k)
                for k in ("status", "correctness_score", "completeness_score", "self_graded",
                          "source_truncated", "fields", "completeness_detail", "reason")
            } if tier2 else None,
        },
    }
