"""Run extraction validation over enrichment events and write the scores back.

The IO half of the validation feature. `extraction_validator.py` holds the pure
checks — no DB, no network, no LLM plumbing — so they stay testable without a
database; this module fetches what they need and persists what they produce.
Same split as `cost_finalizer.py` against `langsmith_cost.py`.

WHY THIS IS A SEPARATE PASS, NOT PART OF INGESTION
--------------------------------------------------
Validation runs AFTER the enrichment event exists, triggered on demand, rather
than inline in the worker. Three reasons:

* Ingestion latency stays untouched. Enrichment already takes 5-15s per
  document; adding a judge call would make the user wait for a number only the
  analytics screen reads.
* It is re-runnable. A judge prompt improvement, or a newly-configured field,
  can be applied to documents already ingested — inline validation would freeze
  each row's verdict at ingestion time forever.
* A judge failure cannot fail an ingestion that already succeeded.

The trade: a row is unvalidated until this runs, which is why
`validation_status` distinguishes NULL ("never validated") from every other
value, and why nothing here writes a score it did not actually compute.

SOURCE OF TRUTH FOR "THE SOURCE TEXT"
-------------------------------------
Groundedness is judged against the document's INDEXED chunk text, not the
original PDF bytes. That is deliberate: the extractor saw the parsed text, so
grading it against the raw file would penalise it for parser losses it never
had a chance to see, and those are already measured separately by comparing
processing modes. Judging against the indexed text answers the question the
metric is for — did the model faithfully use what it was given.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, select, update

from utils.knowledge_health.extraction_validator import (
    ST_ERROR,
    ST_SKIPPED,
    merge_results,
    run_deterministic_checks,
    run_deterministic_checks_traced,
    run_llm_judge_repeated,
    run_llm_judge,
)
from utils.logger import logger
from utils.models.db_tables import KnowledgeEnrichmentEvent as EV
from utils.setup_env import (
    EXTRACTION_VALIDATION_LLM_ENABLED,
    EXTRACTION_COMPLETENESS_ENABLED,
    EXTRACTION_VALIDATION_ALLOWED_OWNERS,
    EXTRACTION_VALIDATION_MAX_DOCS,
    EXTRACTION_VALIDATION_EFFORT,
    EXTRACTION_VALIDATION_MODEL,
    EXTRACTION_VALIDATION_PROVIDER,
    EXTRACTION_VALIDATION_REPEATS,
)

# Chunk text pulled per document to form the judged source. Enough for a
# multi-page document without turning one validate call into a huge prompt; the
# judge result records `source_truncated` when it bites.
_MAX_CHUNKS = 40


async def _load_source_text(knowledge_id: str, object_id: str) -> str:
    """Concatenate the document's indexed chunk text.

    Returns "" when nothing is retrievable — the caller then SKIPS the LLM tier
    rather than judging against an empty source, which would mark every value
    unsupported and manufacture a 0.0 correctness score out of an IO failure.
    """
    from chat.knowledge_adapter import get_document_chunks_paginated

    try:
        # The adapter takes (knowledge_id, object_id, user_id) only, and pins
        # limit=20/offset=0 internally. It was previously called with page= and
        # page_size=, which raised TypeError on every call; the broad except
        # below turned that into "" and the caller read "" as "no source
        # available", so the LLM tier was skipped silently on every document and
        # the judge never ran once. Hence the split handling below: a wrong-call
        # bug must not look like an empty document.
        result = await get_document_chunks_paginated(
            knowledge_id=knowledge_id, object_id=object_id
        )
    except (TypeError, AttributeError, ImportError):
        # A programming error, not a data condition. Re-raise so it surfaces as a
        # 500 and gets fixed, instead of degrading into a permanent no-op.
        logger.error(
            f"[validation] source-text loader called incorrectly for "
            f"object_id={object_id} — this is a bug, not a missing document",
            exc_info=True,
        )
        raise
    except Exception as e:
        # Genuine IO trouble (Qdrant unreachable, timeout). Degrading is correct
        # here: judging against an empty source would mark every value
        # unsupported and manufacture a 0.0 out of an infrastructure failure.
        logger.warning(
            f"[validation] could not load source text for object_id={object_id}: "
            f"{type(e).__name__}: {e}"
        )
        return ""

    chunks = (result or {}).get("chunks") or (result or {}).get("data") or []
    parts: List[str] = []
    for c in chunks:
        if isinstance(c, dict):
            t = c.get("chunk_text") or c.get("text") or ""
            if t:
                parts.append(str(t))
    if not parts:
        logger.warning(
            f"[validation] no indexed chunk text for object_id={object_id} — "
            f"LLM tier will be skipped for this document"
        )
    return "\n\n".join(parts)


async def _load_extracted(knowledge_id: str, object_id: str) -> Dict[str, Any]:
    """The extracted metadata for one document, as stored in Qdrant."""
    try:
        from chat.knowledge_adapter import get_document_metadata

        meta = await get_document_metadata(knowledge_id=knowledge_id, object_id=object_id)
        if isinstance(meta, dict):
            # The adapter may wrap the payload; accept either shape.
            return meta.get("metadata") if isinstance(meta.get("metadata"), dict) else meta
        return {}
    except Exception as e:
        logger.warning(
            f"[validation] could not load extracted metadata for object_id={object_id}: "
            f"{type(e).__name__}: {e}"
        )
        return {}


async def _load_field_configs(knowledge_id: str) -> List[Dict[str, Any]]:
    """Active field configurations for the knowledge base."""
    try:
        from utils.db.enrichment_db_utils import get_field_configurations_sqlmodel_v2

        configs = await get_field_configurations_sqlmodel_v2(knowledge_id)
        out: List[Dict[str, Any]] = []
        for c in configs or []:
            d = c if isinstance(c, dict) else getattr(c, "__dict__", {})
            out.append(dict(d))
        return out
    except Exception as e:
        logger.warning(
            f"[validation] could not load field configs for knowledge_id={knowledge_id}: "
            f"{type(e).__name__}: {e}"
        )
        return []


async def _llm_tier_allowed(session, knowledge_id: str) -> tuple:
    """(allowed, reason) for running the PAID Tier 2 judge on this knowledge base.

    Keyed on the knowledge base's owner rather than the caller, because the
    master API key bypasses permission checks and resolves to a caller identity
    of None — a caller-side gate would not constrain our own scripts at all.

    An unowned knowledge base is allowed: master-key-created collections carry no
    owner and our test collections are exactly those. That is a known gap, not an
    oversight — an unowned base is never gated.
    """
    if not EXTRACTION_VALIDATION_ALLOWED_OWNERS:
        return True, None
    from sqlalchemy import select as _select
    from utils.models.db_tables import KnowledgeConfig

    owner = (await session.execute(
        _select(KnowledgeConfig.owner_id).where(
            KnowledgeConfig.knowledge_id == knowledge_id)
    )).scalar_one_or_none()
    if owner is None:
        return True, None
    if owner in EXTRACTION_VALIDATION_ALLOWED_OWNERS:
        return True, None
    return False, (
        "LLM validation tier withheld: this knowledge base's owner is not in "
        "EXTRACTION_VALIDATION_ALLOWED_OWNERS. Tier 1 deterministic checks ran; "
        "correctness reflects those alone and completeness is unavailable."
    )


@contextmanager
def _trace_validation(knowledge_id: str, object_id: str, event):
    """One LangSmith run per validated document, with both tiers beneath it.

    Yields the RunTree, or None when tracing is off or unavailable. Every failure
    mode here yields None rather than raising: a validation must not fail because
    an observability backend is unreachable, and this module's whole history is
    of failures hidden by defensive defaults — so the degradation is logged.
    """
    try:
        from langsmith.run_helpers import trace
    except Exception:
        yield None
        return
    try:
        with trace(
            name="ExtractionValidation",
            run_type="chain",
            tags=["extraction-validation", "parent"],
            inputs={"knowledge_id": knowledge_id, "object_id": object_id},
            metadata={
                "knowledge_id": knowledge_id,
                "object_id": object_id,
                # The model being GRADED, not the grader. Kept distinct because
                # conflating them is how a self-graded score gets read as an
                # independent one.
                "enriching_model": getattr(event, "model", None),
                "enriching_provider": getattr(event, "provider", None),
                "document_processing_mode": getattr(event, "document_processing_mode", None),
            },
        ) as rt:
            yield rt
    except Exception as e:
        logger.warning(
            f"[validation] tracing unavailable for object_id={object_id}: "
            f"{type(e).__name__}: {e} — validation continues untraced"
        )
        yield None


def _stamp_parent(rt, tier1, tier2, merged, llm_block_reason) -> None:
    """Put the correctness/completeness x deterministic/LLM matrix on the parent run.

    Each cell carries its own status string, because a bare NULL has meant three
    different things in this module's short life: the tier was never asked, the
    tier was asked and failed, and the tier ran and had nothing countable to
    measure. Only the third is a legitimate absence, and a reader comparing
    models in LangSmith cannot tell them apart from the number alone.
    """
    if rt is None:
        return
    t2 = tier2 or {}

    def _completeness_llm_status() -> str:
        if tier2 is None:
            return "withheld" if llm_block_reason else "not_requested"
        if t2.get("status") == ST_ERROR:
            return "tier_failed"
        if not EXTRACTION_COMPLETENESS_ENABLED:
            return "disabled_by_flag"
        if t2.get("completeness_score") is None:
            return "no_countable_field"
        return "scored"

    try:
        rt.metadata.update({
            # --- correctness ---
            "correctness_deterministic": tier1.get("correctness_score"),
            "correctness_deterministic_status": tier1.get("status"),
            "correctness_llm": t2.get("correctness_score"),
            "correctness_final": merged.get("correctness_score"),
            # Tier 1 wins on correctness when both produced a number: it is
            # arithmetic against the document's own control values, while Tier 2
            # is a model's opinion. Recorded so the precedence is auditable
            # rather than inferred from which number happens to match.
            "correctness_source": (
                "tier1_deterministic" if tier1.get("correctness_score") is not None
                else ("tier2_llm" if t2.get("correctness_score") is not None else "none")
            ),
            # --- completeness ---
            "completeness_deterministic": None,
            "completeness_deterministic_status": "unavailable_by_design",
            "completeness_llm": t2.get("completeness_score"),
            "completeness_llm_status": _completeness_llm_status(),
            # The spread, so a reader knows whether a gap between two models is
            # a difference or just sampling. With repeats=1 these equal the score
            # and runs=1 says plainly that it is one sample.
            "correctness_llm_min": t2.get("correctness_min"),
            "correctness_llm_max": t2.get("correctness_max"),
            "correctness_llm_scored_runs": t2.get("correctness_scored_runs"),
            "completeness_llm_min": t2.get("completeness_min"),
            "completeness_llm_max": t2.get("completeness_max"),
            "completeness_llm_scored_runs": t2.get("completeness_scored_runs"),
            "judge_runs": t2.get("judge_runs"),
            "judge_errored_runs": t2.get("judge_errored_runs"),
            "judge_effort": t2.get("judge_effort"),
            "completeness_final": merged.get("completeness_score"),
            # --- verdict and the actionable detail ---
            "validation_status_final": merged.get("validation_status"),
            "tier1_status": tier1.get("status"),
            "tier2_status": t2.get("status"),
            "tier2_reason": t2.get("reason"),
            "llm_tier_withheld": llm_block_reason,
            "worst_field": tier1.get("worst_field"),
            "worst_field_score": tier1.get("worst_field_score"),
            "unweighted_correctness": tier1.get("unweighted_correctness"),
            # A wide gap between weighted and unweighted correctness means one
            # field is carrying the whole result — the dilution this module was
            # built to expose.
            "weighted_unweighted_gap": (
                round(tier1["unweighted_correctness"] - tier1["correctness_score"], 4)
                if tier1.get("correctness_score") is not None
                and tier1.get("unweighted_correctness") is not None else None
            ),
            "over_extracted_fields": t2.get("over_extracted_fields"),
            "self_graded": t2.get("self_graded"),
            "source_truncated": t2.get("source_truncated"),
        })
        rt.outputs = {
            "correctness": merged.get("correctness_score"),
            "completeness": merged.get("completeness_score"),
            "status": merged.get("validation_status"),
        }
    except Exception:
        pass


async def validate_one_event(
    session,
    knowledge_id: str,
    event,
    field_configs: List[Dict[str, Any]],
    *,
    use_llm: bool = True,
    owner_id: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    judge_effort: Optional[str] = None,
    repeats: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate one enrichment event and UPDATE its row. Never raises.

    The four judge overrides exist so the grader's own variance can be measured
    without a redeploy: model, provider and effort are otherwise baked into the
    image via .env.prod, which made every A/B of the judge a build cycle.

    Returns a small summary dict for the endpoint's response.
    """
    object_id = event.object_id
    if not object_id:
        # Nothing to fetch a document by. Recorded as skipped rather than left
        # NULL so a re-run does not keep retrying a row it can never validate.
        await _persist(session, event, {
            "validation_status": ST_SKIPPED, "correctness_score": None,
            "completeness_score": None, "validation_model": None,
            "validation_ms": None,
            "validation_data": {"reason": "event has no object_id"},
        })
        return {"event_key": str(event.event_key), "status": ST_SKIPPED, "reason": "no object_id"}

    extracted = await _load_extracted(knowledge_id, object_id)
    if not extracted:
        await _persist(session, event, {
            "validation_status": ST_SKIPPED, "correctness_score": None,
            "completeness_score": None, "validation_model": None,
            "validation_ms": None,
            "validation_data": {"reason": "no extracted metadata retrievable"},
        })
        return {"event_key": str(event.event_key), "status": ST_SKIPPED, "reason": "no extracted metadata"}

    # ONE parent run per document, with both tiers as children. Before this the
    # only trace was the judge's own, so LangSmith showed the paid tier alone and
    # a reader could not see the free tier's verdict, the two disagreeing, or
    # which of the four numbers (correctness/completeness x deterministic/LLM)
    # actually exists for this document. `_trace_validation` degrades to a no-op
    # context manager if tracing is unavailable — observability must never be
    # able to fail a validation.
    with _trace_validation(knowledge_id, object_id, event) as parent_rt:
        # Tier 1 always. Free, deterministic, and the tier that actually caught
        # the production defect this feature exists for. Called through the
        # traced wrapper so it appears in LangSmith beside the paid tier instead
        # of being invisible there for want of an LLM call.
        tier1 = run_deterministic_checks_traced(
            extracted, field_configs, knowledge_id=knowledge_id, object_id=object_id
        )

        tier2: Optional[Dict[str, Any]] = None
        llm_ok, llm_block_reason = await _llm_tier_allowed(session, knowledge_id)
        if use_llm and EXTRACTION_VALIDATION_LLM_ENABLED and llm_ok:
            source_text = await _load_source_text(knowledge_id, object_id)
            if source_text:
                tier2 = await run_llm_judge_repeated(
                    repeats=repeats or EXTRACTION_VALIDATION_REPEATS,
                    source_text=source_text,
                    extracted=extracted,
                    field_configs=field_configs,
                    owner_id=owner_id or event.owner_id,
                    knowledge_id=knowledge_id,
                    judge_provider=judge_provider or EXTRACTION_VALIDATION_PROVIDER,
                    judge_model=judge_model or EXTRACTION_VALIDATION_MODEL,
                    judge_effort=judge_effort or EXTRACTION_VALIDATION_EFFORT,
                    # Passed so a self-graded result is RECORDED as such rather than
                    # presented next to independent scores as if comparable.
                    enriching_model=event.model,
                )
            else:
                # No source => no groundedness opinion. Explicitly not an error and
                # explicitly not a zero.
                tier2 = {
                    "status": ST_ERROR, "reason": "source text unavailable",
                    "correctness_score": None, "completeness_score": None,
                    "validation_model": EXTRACTION_VALIDATION_MODEL,
                    "validation_ms": None,
                }

        merged = merge_results(tier1, tier2)
        # Completeness is gated independently of correctness, and the gate has to
        # apply to the WRITE, not only to the read-side label. Tier 2 is its only
        # source, so without this a judge run would persist a completeness_score
        # while ModelComparisonResponse.completeness reported status "disabled" —
        # the API asserting the metric did not run over stored evidence that it did.
        if not EXTRACTION_COMPLETENESS_ENABLED:
            merged["completeness_score"] = None
        # The whole 2x2 on the parent run: correctness and completeness, each from
        # the deterministic tier and the LLM tier, plus which value won and why.
        # This is the view the database row cannot give — the row stores only the
        # merged number, so a disagreement between tiers is invisible there.
        _stamp_parent(parent_rt, tier1, tier2, merged, llm_block_reason)
        await _persist(session, event, merged)
        return {
            "event_key": str(event.event_key),
            "object_id": object_id,
            "status": merged["validation_status"],
            "correctness_score": merged["correctness_score"],
            "completeness_score": merged["completeness_score"],
            "validation_model": merged["validation_model"],
            "llm_tier_withheld": llm_block_reason,
            # Why Tier 2 produced nothing, surfaced over HTTP rather than left in the
            # container logs. A judge that 400s records status=error with NULL scores,
            # which over the wire is indistinguishable from a judge that was never
            # asked to run — that ambiguity hid a `temperature` rejection across a
            # whole 12-cell experiment. `None` here means Tier 2 had no complaint.
            "tier2_reason": tier2.get("reason") if tier2 else None,
            # The spread over HTTP as well: a caller comparing models needs to
            # see that 0.81 came from four runs ranging 0.73-1.0, not from one.
            "correctness_llm": (tier2 or {}).get("correctness_score"),
            "correctness_llm_min": (tier2 or {}).get("correctness_min"),
            "correctness_llm_max": (tier2 or {}).get("correctness_max"),
            "completeness_llm_min": (tier2 or {}).get("completeness_min"),
            "completeness_llm_max": (tier2 or {}).get("completeness_max"),
            "judge_runs": (tier2 or {}).get("judge_runs"),
            "judge_errored_runs": (tier2 or {}).get("judge_errored_runs"),
            "tier1_checked_fields": tier1.get("checked_fields"),
            # Surfaced in the response, not just buried in validation_data: the
            # aggregate is dilution-prone by construction and the worst field is what
            # a reader can act on.
            "worst_field": tier1.get("worst_field"),
            "worst_field_score": tier1.get("worst_field_score"),
            "unweighted_correctness": tier1.get("unweighted_correctness"),
        }


async def _persist(session, event, merged: Dict[str, Any]) -> None:
    """Write the validation columns onto one event row.

    A targeted UPDATE by event_key rather than an ORM flush of the whole row:
    the enrichment columns must not be rewritten by a validation pass, and an
    UPDATE naming only these columns makes that structural instead of a thing to
    remember.
    """
    values = {
        "validation_status": merged.get("validation_status"),
        "validation_model": merged.get("validation_model"),
        "correctness_score": merged.get("correctness_score"),
        "completeness_score": merged.get("completeness_score"),
        "validation_ms": merged.get("validation_ms"),
        "validation_data": merged.get("validation_data"),
    }
    cost = merged.get("validation_cost_usd")
    if cost is not None:
        values["validation_cost_usd"] = cost
    await session.execute(
        update(EV.__table__).where(EV.__table__.c.event_key == event.event_key).values(**values)
    )
    await session.commit()


async def validate_window(
    session,
    knowledge_id: str,
    since: datetime,
    until: datetime,
    *,
    only_unvalidated: bool = True,
    use_llm: bool = True,
    limit: Optional[int] = None,
    object_id: Optional[str] = None,
    judge_provider: Optional[str] = None,
    judge_model: Optional[str] = None,
    judge_effort: Optional[str] = None,
    repeats: Optional[int] = None,
) -> Dict[str, Any]:
    """Validate the events in a window, newest first.

    `only_unvalidated=True` (the default) is what makes this safe to call
    repeatedly: it picks up rows whose `validation_status IS NULL` and leaves
    already-scored rows alone. Pass False to deliberately re-score, e.g. after
    changing the judge prompt.

    `limit` defaults to EXTRACTION_VALIDATION_MAX_DOCS. The response always
    reports `remaining`, so a caller can see that a window was only partly
    covered instead of reading a partial pass as a complete one.
    """
    cap = limit or EXTRACTION_VALIDATION_MAX_DOCS

    filters = [EV.knowledge_id == knowledge_id]
    if object_id:
        # An explicit document ignores the window, same convention as
        # get_enrichment_documents: an exact id is a lookup, not a search.
        filters.append(EV.object_id == object_id)
    else:
        filters.append(EV.created_at >= since)
        filters.append(EV.created_at < until)
    if only_unvalidated:
        filters.append(EV.validation_status.is_(None))

    total = (await session.execute(
        select(EV).where(and_(*filters)).order_by(EV.created_at.desc())
    )).scalars().all()

    field_configs = await _load_field_configs(knowledge_id)
    if not field_configs:
        return {
            "validated": 0, "skipped": 0, "failed": 0, "errors": 0,
            "matched": len(total), "remaining": len(total),
            "note": "no active field configurations — nothing to validate against",
            "results": [],
        }

    batch = total[:cap]
    results = []
    for ev in batch:
        try:
            results.append(
                await validate_one_event(
                    session, knowledge_id, ev, field_configs,
                    use_llm=use_llm, owner_id=ev.owner_id,
                    judge_provider=judge_provider, judge_model=judge_model,
                    judge_effort=judge_effort, repeats=repeats,
                )
            )
        except Exception as e:
            # One bad document must not abort the batch.
            logger.error(
                f"[validation] event_key={ev.event_key} failed: {type(e).__name__}: {e}",
                exc_info=True,
            )
            results.append({"event_key": str(ev.event_key), "status": ST_ERROR, "reason": str(e)})

    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for r in results:
        counts[r.get("status", "error")] = counts.get(r.get("status", "error"), 0) + 1

    return {
        "matched": len(total),
        "processed": len(batch),
        "remaining": max(len(total) - len(batch), 0),
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "skipped": counts.get("skipped", 0),
        "errors": counts.get("error", 0),
        "llm_tier_used": bool(use_llm and EXTRACTION_VALIDATION_LLM_ENABLED),
        "judge_model": (judge_model or EXTRACTION_VALIDATION_MODEL)
        if (use_llm and EXTRACTION_VALIDATION_LLM_ENABLED) else None,
        # Echoed so a response can be read on its own terms: an override
        # means these numbers are not comparable with the deployed default.
        "judge_effort": judge_effort or EXTRACTION_VALIDATION_EFFORT,
        "judge_repeats": repeats or EXTRACTION_VALIDATION_REPEATS,
        "results": results,
    }
