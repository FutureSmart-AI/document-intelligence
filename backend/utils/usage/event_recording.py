"""Writes UsageEvent + UsageMetric rows, ported from fs-agent-usage-limits'
utils/usage_event_recording.py, rewritten to:
  - use this repo's shared async session (utils/db/session.py) with the ORM
    instead of a second raw asyncpg connection pool
  - take owner_id as the scope (from the route's `/v1/owners/{owner_id}/...` path
    for HTTP callers), with knowledge_id an optional per-event attribution. In-process
    callers that only hold a knowledge_id use record_single_event(), which resolves
    the owner from it.

owner_id is the required column on UsageEvent (see utils/models/usage_events.py,
migration 039) — an event with no resolvable owner_id is dropped, not inserted
with a null owner, since there's nowhere for it to be reported under this model.

This is also the direct in-process replacement for the old outbound HTTP call
in utils/qdrant/usage_tracking.py's post_processing_event() — see
record_single_event(), the function that call site now invokes directly.
"""

from typing import List, Optional, Tuple
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from utils.logger import logger
from utils.db.session import async_session
from utils.models.usage_events import UsageEvent, UsageMetric
from utils.pydantic_models_usage_events import (
    UsageEventRequest,
    UsageEventsResponse,
    FailedEvent,
)


def _validate_timestamp(timestamp: str) -> Optional[str]:
    """Returns an error string if invalid, else None."""
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt > now:
            return "timestamp cannot be in the future"
        if (now - dt) > timedelta(days=7):
            return "timestamp cannot be older than 7 days"
        return None
    except (ValueError, AttributeError) as e:
        return f"Invalid ISO8601 timestamp: {e}"


async def record_usage_events(owner_id: str, events: List[UsageEventRequest]) -> UsageEventsResponse:
    """Record a batch of usage events for one account, with partial success
    handling — one failed event does not roll back the others.

    owner_id comes from the route path. Each event's own optional knowledge_id
    attributes it to one knowledge base for breakdown purposes; events in a batch
    may name different ones, or none.
    """
    logger.info(f"Recording {len(events)} usage events for owner_id={owner_id}")

    processed = 0
    failed_events: List[FailedEvent] = []

    async with async_session() as session:
        for index, event in enumerate(events):
            error = _validate_timestamp(event.timestamp)
            if error:
                failed_events.append(FailedEvent(index=index, error=error, event=event.dict()))
                continue

            try:
                db_event = UsageEvent(
                    event_id=uuid4(),
                    knowledge_id=event.knowledge_id,
                    owner_id=owner_id,
                    key_type=event.key_type,
                    service_type=event.service_type,
                    operation=event.operation,
                    label=event.label,
                    session_id=event.session_id,
                    request_id=event.request_id,
                    operation_id=event.operation_id or str(uuid4()),
                    timestamp=datetime.fromisoformat(event.timestamp.replace("Z", "+00:00")),
                    event_metadata=event.metadata,
                )
                session.add(db_event)
                await session.flush()  # assigns db_event.event_id for the metrics FK

                for metric in event.metrics or []:
                    session.add(UsageMetric(event_id=db_event.event_id, metric_name=metric.name, metric_value=metric.value))

                await session.commit()
                processed += 1
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to insert usage event {index} for owner_id={owner_id}: {e}")
                failed_events.append(FailedEvent(index=index, error=f"Database insert failed: {e}", event=event.dict()))

    logger.info(f"Recorded {processed} events, {len(failed_events)} failed for owner_id={owner_id}")
    return UsageEventsResponse(processed_count=processed, failed_count=len(failed_events), failed_events=failed_events)


async def record_single_event(
    knowledge_id: Optional[str],
    service_type: str,
    operation: str,
    label: Optional[str] = None,
    owner_id: Optional[str] = None,
    key_type: Optional[str] = None,
    session_id: Optional[str] = None,
    request_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    metrics: Optional[List[Tuple[str, float]]] = None,
) -> None:
    """Direct in-process recorder for internal callers (e.g.
    utils/qdrant/usage_tracking.py) that previously POSTed to the standalone
    usage service over HTTP. Fire-and-forget: logs and swallows failures so a
    usage-tracking hiccup never breaks the embedding/LLM call it's tracking.

    metrics is a list of (name, value) pairs, matching the old event payload's
    "metrics": [{"name": ..., "value": ...}] shape.
    """
    resolved_owner_id = owner_id or await _resolve_owner_id(knowledge_id)
    if not resolved_owner_id:
        logger.warning(f"record_single_event: dropping event, no resolvable owner_id (knowledge_id={knowledge_id}, operation={operation})")
        return
    try:
        async with async_session() as session:
            db_event = UsageEvent(
                event_id=uuid4(),
                knowledge_id=knowledge_id,
                owner_id=resolved_owner_id,
                key_type=key_type,
                service_type=service_type,
                operation=operation,
                label=label,
                session_id=session_id,
                request_id=request_id,
                operation_id=str(uuid4()),
                timestamp=datetime.now(timezone.utc),
                event_metadata=metadata,
            )
            session.add(db_event)
            await session.flush()
            for name, value in metrics or []:
                session.add(UsageMetric(event_id=db_event.event_id, metric_name=name, metric_value=value))
            await session.commit()
    except Exception as e:
        logger.error(f"record_single_event failed for knowledge_id={knowledge_id}, operation={operation}: {e}")


async def _resolve_owner_id(knowledge_id: Optional[str]) -> Optional[str]:
    """Best-effort owner_id lookup from knowledge_config. Never raises."""
    if not knowledge_id:
        return None
    try:
        from utils.db.knowledge_ops import get_knowledge_service

        service = await get_knowledge_service(knowledge_id)
        return service.get("owner_id") if service else None
    except Exception as e:
        logger.debug(f"Could not resolve owner_id for knowledge_id={knowledge_id}: {e}")
        return None
