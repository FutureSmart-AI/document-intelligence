"""ClickUp self-serve sync endpoints — the dashboard wizard's backend.

Wizard flow (Connect -> Select docs -> Schedule -> Review) maps to:
  POST   /v1/{knowledge_id}/clickup/connect   validate token + workspace, store encrypted
  GET    /v1/{knowledge_id}/clickup/spaces     browse spaces
  GET    /v1/{knowledge_id}/clickup/folders    browse folders in a space
  PUT    /v1/{knowledge_id}/clickup/config     save selection + schedule + enabled
  GET    /v1/{knowledge_id}/clickup/config     read config (token masked)
  POST   /v1/{knowledge_id}/clickup/sync       trigger a manual run
  GET    /v1/{knowledge_id}/clickup/runs        recent runs (progress polling)
  GET    /v1/{knowledge_id}/clickup/runs/{run_id}

The crawl itself runs on the dedicated knowledge-side connector lane (Celery); these
endpoints are thin control-plane operations. The React UI lives in the dashboard repo.
"""

import uuid
from typing import List, Optional

from croniter import croniter
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from utils.auth.dependencies import require_permission, resolve_actor_id
from utils.logger import logger
from utils.connectors.clickup import (
    ClickUpAuthError,
    ClickUpConfig,
    ClickUpConnector,
    clickup_is_auth_error,
)
from utils.db import connector_ops
from utils.db.deletion_ops import set_initial_deletion_status
from utils.db.knowledge_ops import get_chatbot_search_config
from utils.db.retrieval_ops import get_document_metadata_enabled
from utils.files.retrieval_utils import list_documents_get_alias
from utils.security.crypto import encrypt_secret, decrypt_secret
from utils.setup_env import FASTAPI_SECRET_KEY, KNOWLEDGE_API_BASE_URL
from utils.tasks.knowledge_deletion_utils import perform_knowledge_deletion

router = APIRouter()

SOURCE = "clickup"

# Returned (HTTP 409) whenever the stored ClickUp token is expired/invalid, so the UI can
# tell them apart from a real server error and prompt a reconnect. 409 (not 401) avoids
# clashing with THIS API's own auth failures.
_TOKEN_EXPIRED_DETAIL = {
    "error": "clickup_token_expired",
    "message": "ClickUp token expired or invalid — please reconnect ClickUp.",
}


def _raise_clickup_error(exc: Exception, fallback_detail: str) -> None:
    """Map a ClickUp browse/verify failure to an HTTPException: a 409 'reconnect' when the
    token is expired/invalid, else the generic 502 fallback. Always raises."""
    if clickup_is_auth_error(exc):
        raise HTTPException(status_code=409, detail=_TOKEN_EXPIRED_DETAIL)
    raise HTTPException(status_code=502, detail=fallback_detail)


# --------------------------------------------------------------------- models
class ConnectRequest(BaseModel):
    api_token: str
    workspace_id: str


class ConfigRequest(BaseModel):
    space_ids: Optional[List[str]] = None
    folder_ids: Optional[List[str]] = None
    folder_names: Optional[List[str]] = None
    doc_ids: Optional[List[str]] = None  # extra docs to index by id, in addition to folders
    exclude_folder_name: Optional[List[str]] = None
    exclude_folder_id: Optional[List[str]] = None
    exclude_name_keywords: Optional[List[str]] = None
    space_docs_enabled: Optional[bool] = None
    space_doc_allowlist: Optional[List[str]] = None
    deletion_enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    schedule_cron: Optional[str] = None  # cron expr (UTC), e.g. "0 2 * * *"; wins over interval
    force_reindex: Optional[bool] = None  # one-shot: next run reprocesses all pages, then clears
    enabled: Optional[bool] = None


def _browse_connector(token: str, workspace_id: str, knowledge_id: str) -> ClickUpConnector:
    """A connector configured just enough to browse (token + workspace). The
    knowledge_* fields are required by the dataclass but unused for browse calls."""
    return ClickUpConnector(ClickUpConfig(
        knowledge_id=knowledge_id,
        knowledge_api_base_url=KNOWLEDGE_API_BASE_URL,
        knowledge_api_key=FASTAPI_SECRET_KEY,
        api_token=token,
        workspace_id=workspace_id,
    ))


async def _load_token_and_workspace(knowledge_id: str) -> tuple[str, str]:
    """Fetch stored config, decrypt the token. 400 if Connect hasn't been done."""
    cfg = await connector_ops.get_connector_config(knowledge_id, SOURCE, reveal_token=True)
    if not cfg or not cfg.get("encrypted_token"):
        raise HTTPException(status_code=400, detail="ClickUp not connected — call /connect first")
    workspace_id = (cfg.get("selection") or {}).get("workspace_id") or ""
    return decrypt_secret(cfg["encrypted_token"]), workspace_id


async def _verify_selection(knowledge_id: str, space_ids=None, doc_ids=None) -> None:
    """Reject space/doc ids that don't exist in ClickUp (400) before we store them,
    instead of saving garbage that only fails opaquely at crawl time."""
    token, workspace_id = await _load_token_and_workspace(knowledge_id)
    try:
        await _browse_connector(token, workspace_id, knowledge_id).verify_selection(
            space_ids=space_ids, doc_ids=doc_ids
        )
    except ClickUpAuthError:
        # Expired/invalid token — NOT a bad selection. Tell them to reconnect (409) rather
        # than 400-ing a space that is actually valid.
        raise HTTPException(status_code=409, detail=_TOKEN_EXPIRED_DETAIL)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"verify_selection failed for knowledge={knowledge_id}: {e}")
        _raise_clickup_error(e, "Failed to validate selection against ClickUp")


# -------------------------------------------------------------------- connect
@router.post("/v1/{knowledge_id}/clickup/connect")
async def connect(
    knowledge_id: str,
    body: ConnectRequest,
    _: Optional[str] = Depends(require_permission("write")),
):
    """Validate the ClickUp token against the workspace and store it encrypted."""
    connector = _browse_connector(body.api_token, body.workspace_id, knowledge_id)
    if not await connector.validate_credentials():
        raise HTTPException(status_code=400, detail="Invalid ClickUp API token")
    await connector_ops.upsert_connector_config(
        knowledge_id, SOURCE,
        encrypted_token=encrypt_secret(body.api_token),
        selection_patch={"workspace_id": body.workspace_id},
    )
    # Return spaces so the wizard can move straight to the Select step.
    try:
        spaces = await connector.list_spaces()
    except Exception as e:
        logger.warning(f"connect: token valid but listing spaces failed: {e}")
        spaces = []
    return {"valid": True, "workspace_id": body.workspace_id, "spaces": spaces}


# ---------------------------------------------------------------------- browse
@router.get("/v1/{knowledge_id}/clickup/spaces")
async def list_spaces(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    token, workspace_id = await _load_token_and_workspace(knowledge_id)
    try:
        spaces = await _browse_connector(token, workspace_id, knowledge_id).list_spaces()
    except Exception as e:
        logger.error(f"list_spaces failed for knowledge={knowledge_id}: {e}")
        _raise_clickup_error(e, "Failed to list ClickUp spaces")
    return {"spaces": spaces}


@router.get("/v1/{knowledge_id}/clickup/folders")
async def list_folders(
    knowledge_id: str,
    space_id: str = Query(...),
    _: Optional[str] = Depends(require_permission("read")),
):
    token, workspace_id = await _load_token_and_workspace(knowledge_id)
    try:
        folders = await _browse_connector(token, workspace_id, knowledge_id).list_folders(space_id)
    except Exception as e:
        logger.error(f"list_folders failed for knowledge={knowledge_id} space={space_id}: {e}")
        _raise_clickup_error(e, "Failed to list ClickUp folders")
    return {"folders": folders}


# ---------------------------------------------------------------------- config
@router.put("/v1/{knowledge_id}/clickup/config")
async def save_config(
    knowledge_id: str,
    body: ConfigRequest,
    _: Optional[str] = Depends(require_permission("write")),
):
    """Save the selection + schedule. Requires Connect to have run first."""
    existing = await connector_ops.get_connector_config(knowledge_id, SOURCE)
    if not existing:
        raise HTTPException(status_code=400, detail="ClickUp not connected — call /connect first")
    if body.schedule_cron is not None and not croniter.is_valid(body.schedule_cron):
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {body.schedule_cron!r}")
    if body.space_ids or body.doc_ids:
        await _verify_selection(knowledge_id, space_ids=body.space_ids, doc_ids=body.doc_ids)
    # Only patch selection keys the caller actually sent (don't clobber workspace_id
    # set at Connect, or other keys, with nulls).
    selection_patch = {
        k: v for k, v in {
            "space_ids": body.space_ids,
            "folder_ids": body.folder_ids,
            "folder_names": body.folder_names,
            "doc_ids": body.doc_ids,
            "exclude_folder_name": body.exclude_folder_name,
            "exclude_folder_id": body.exclude_folder_id,
            "exclude_name_keywords": body.exclude_name_keywords,
            "space_docs_enabled": body.space_docs_enabled,
            "space_doc_allowlist": body.space_doc_allowlist,
            "deletion_enabled": body.deletion_enabled,
            "force_reindex": body.force_reindex,
        }.items() if v is not None
    }
    # Forward interval/cron only when the client actually sent them, so an explicit
    # null clears the schedule (-> manual) while an omitted field keeps its stored value.
    # Passing body.interval_minutes unconditionally would send None for both meanings.
    sent = body.model_dump(exclude_unset=True)
    schedule_kw = {k: sent[k] for k in ("interval_minutes", "schedule_cron") if k in sent}
    saved = await connector_ops.upsert_connector_config(
        knowledge_id, SOURCE,
        selection_patch=selection_patch or None,
        enabled=body.enabled,
        **schedule_kw,
    )
    return saved


@router.get("/v1/{knowledge_id}/clickup/config")
async def get_config(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    cfg = await connector_ops.get_connector_config(knowledge_id, SOURCE)
    if not cfg:
        raise HTTPException(status_code=404, detail="No ClickUp config for this knowledge base")
    return cfg


async def _clickup_object_ids(knowledge_id: str) -> List[str]:
    """All indexed object_ids sourced from ClickUp for this knowledge base."""
    docs = await list_documents_get_alias(
        knowledge_id=knowledge_id,
        filters={"source": "clickup"},
        limit=1_000_000,  # no real cap — disconnect must see the full set
        include_source_id=True,
    )
    return [d["object_id"] if isinstance(d, dict) else d for d in docs if d]


@router.delete("/v1/{knowledge_id}/clickup/config")
async def disconnect(
    knowledge_id: str,
    purge_docs: bool = Query(
        False,
        description="Also remove every ClickUp-sourced document from the knowledge index. "
                    "Default false keeps indexed docs and only drops the sync connection.",
    ),
    background_tasks: BackgroundTasks = None,
    user_id: Optional[str] = Depends(require_permission("delete")),
):
    """Disconnect ClickUp: delete the stored config (token + selection + schedule).

    With purge_docs=true, also bulk-removes all ClickUp-sourced documents (Qdrant + DB),
    reusing the standard deletion path. Refuses while a sync is in flight.
    """
    cfg = await connector_ops.get_connector_config(knowledge_id, SOURCE)
    if not cfg:
        raise HTTPException(status_code=404, detail="No ClickUp config for this knowledge base")
    if await connector_ops.has_active_run(knowledge_id, SOURCE):
        raise HTTPException(status_code=409, detail="A sync is in progress — stop it before disconnecting")

    purged = 0
    if purge_docs:
        # The source filter is only honoured when document metadata is enabled. Without it,
        # listing falls back to a DB query that returns EVERY doc — so refuse rather than
        # risk wiping non-ClickUp documents.
        if not await get_document_metadata_enabled(knowledge_id):
            raise HTTPException(
                status_code=409,
                detail="Cannot purge docs: document metadata is disabled for this knowledge "
                       "base, so ClickUp docs can't be isolated. Disconnect without purge_docs.",
            )
        object_ids = await _clickup_object_ids(knowledge_id)
        if object_ids:
            await set_initial_deletion_status(knowledge_id=knowledge_id, object_ids=object_ids, user_id=None)
            hybrid_search, embedding_model = await get_chatbot_search_config(knowledge_id)
            background_tasks.add_task(
                perform_knowledge_deletion,
                user_id=resolve_actor_id(user_id),
                knowledge_id=knowledge_id,
                object_ids=object_ids,
                hybrid_search=hybrid_search,
                embedding_model=embedding_model,
            )
        purged = len(object_ids)

    await connector_ops.delete_connector_config(knowledge_id, SOURCE)
    return {"disconnected": True, "purge_docs": purge_docs, "docs_purged": purged}


# ------------------------------------------------------------------ trigger
@router.post("/v1/{knowledge_id}/clickup/sync")
async def trigger_sync(
    knowledge_id: str,
    force_reindex: bool = Query(
        False,
        description="Reprocess every page regardless of timestamp — use to retry past "
                    "failures or after changing the embedding model. One-shot: consumed by "
                    "the run it triggers, so the next scheduled run is unaffected.",
    ),
    _: Optional[str] = Depends(require_permission("write")),
):
    """Queue a manual sync run; the knowledge-side runner picks it up from the DB."""
    cfg = await connector_ops.get_connector_config(knowledge_id, SOURCE)
    if not cfg or cfg.get("token") is None:
        raise HTTPException(status_code=400, detail="ClickUp not connected — call /connect first")
    if not (cfg.get("selection") or {}).get("space_ids"):
        raise HTTPException(status_code=400, detail="No spaces selected — save a config first")
    if await connector_ops.has_active_run(knowledge_id, SOURCE):
        raise HTTPException(status_code=409, detail="A sync is already in progress")

    # Stash the one-shot flag in the schemaless selection blob; execute_run reads and
    # clears it so it applies only to the run we queue next.
    if force_reindex:
        await connector_ops.upsert_connector_config(
            knowledge_id, SOURCE, selection_patch={"force_reindex": True}
        )

    # Trigger == insert a 'queued' row. No broker: the runner claims it from the DB.
    run_id = str(uuid.uuid4())
    await connector_ops.create_run(run_id, knowledge_id, SOURCE, trigger="manual")
    # A manual run satisfies the imminent scheduled run: re-anchor next_sync_at so the
    # scheduler doesn't enqueue a redundant 'scheduled' run once this one completes.
    # No-op for manual-only configs (no interval/cron).
    await connector_ops.advance_next_sync(knowledge_id, SOURCE)
    return {"run_id": run_id, "status": "queued", "force_reindex": force_reindex}


# ------------------------------------------------------------------- resync
@router.post("/v1/{knowledge_id}/clickup/docs/{doc_id}/resync")
async def resync_doc(
    knowledge_id: str,
    doc_id: str,
    _: Optional[str] = Depends(require_permission("write")),
):
    """Queue a one-shot run that re-indexes just this ClickUp doc (forced, all pages).

    Backs the per-document "re-sync" row action. Unlike a full sync it doesn't need a
    space/folder selection — it crawls the single doc by id and skips the folder sweep.
    """
    cfg = await connector_ops.get_connector_config(knowledge_id, SOURCE)
    if not cfg or cfg.get("token") is None:
        raise HTTPException(status_code=400, detail="ClickUp not connected — call /connect first")
    if await connector_ops.has_active_run(knowledge_id, SOURCE):
        raise HTTPException(status_code=409, detail="A sync is already in progress")
    await _verify_selection(knowledge_id, doc_ids=[doc_id])

    # One-shot scope consumed + cleared by the runner (execute_run).
    await connector_ops.upsert_connector_config(
        knowledge_id, SOURCE, selection_patch={"resync_doc_ids": [doc_id]}
    )
    run_id = str(uuid.uuid4())
    await connector_ops.create_run(run_id, knowledge_id, SOURCE, trigger="manual")
    await connector_ops.advance_next_sync(knowledge_id, SOURCE)
    return {"run_id": run_id, "status": "queued", "doc_id": doc_id}


# -------------------------------------------------------------------- status
@router.get("/v1/{knowledge_id}/clickup/runs")
async def list_runs(
    knowledge_id: str,
    limit: int = Query(20, ge=1, le=100),
    _: Optional[str] = Depends(require_permission("read")),
):
    return {"runs": await connector_ops.get_runs(knowledge_id, SOURCE, limit=limit)}


@router.get("/v1/{knowledge_id}/clickup/runs/{run_id}")
async def get_run(
    knowledge_id: str,
    run_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    runs = await connector_ops.get_runs(knowledge_id, SOURCE, run_id=run_id, limit=1)
    if not runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[0]
