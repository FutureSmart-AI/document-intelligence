"""Read existing successful-attempt field names without inventing schema history."""

import asyncio

from sqlalchemy import select

from utils.db.session import async_session
from utils.logger import logger
from utils.models.db_tables import KnowledgeEnrichmentEvent as EV


def unknown_context():
    return {
        "status": "unknown",
        "event_key": None,
        "processed_at": None,
        "processed_fields": None,
        "definition_history": "unknown",
    }


def context_from_event(event):
    """An empty/partial legacy event is absence of evidence, not an empty schema."""
    fields = event["field_data"]
    if not isinstance(fields, list) or not fields:
        return unknown_context()
    if event["fields_configured"] != len(fields):
        return unknown_context()
    if any(
        not isinstance(field, dict)
        or not isinstance(field.get("field_name"), str)
        or not field["field_name"].strip()
        or field.get("level") not in {"document", "chunk"}
        for field in fields
    ):
        return unknown_context()
    if not event["event_key"] or not event["created_at"]:
        return unknown_context()
    if len({(field["field_name"], field["level"]) for field in fields}) != len(fields):
        return unknown_context()
    return {
        "status": "recorded",
        "event_key": str(event["event_key"]),
        "processed_at": event["created_at"].isoformat(),
        "processed_fields": sorted({
            field["field_name"] for field in fields if field["level"] == "document"
        }),
        # Events record names, not historical descriptions/types/nested schemas.
        "definition_history": "unknown",
    }


async def get_extraction_context(knowledge_id, object_ids):
    """One collection-scoped, page-bounded read; metadata survives telemetry gaps.

    Later failures do not replace stored successful output. Conversely, a later
    success with missing context must not resurrect an older schema snapshot.
    PostgreSQL DISTINCT ON selects the latest success including incomplete logs.
    """
    ids = sorted({oid for oid in object_ids if isinstance(oid, str) and oid})
    contexts = {oid: unknown_context() for oid in ids}
    if not ids:
        return contexts
    try:
        query = (
            select(EV.object_id, EV.event_key, EV.created_at, EV.field_data, EV.fields_configured)
            .where(EV.knowledge_id == knowledge_id, EV.object_id.in_(ids), EV.status == "success")
            .distinct(EV.object_id)
            .order_by(EV.object_id, EV.created_at.desc(), EV.id.desc())
        )
        async with asyncio.timeout(2):
            async with async_session() as session:
                result = await session.execute(query)
                for event in result.mappings():
                    contexts[event["object_id"]] = context_from_event(event)
    except Exception:
        logger.warning("Extraction context unavailable; returning unknown history")
    return contexts
