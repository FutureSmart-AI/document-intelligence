"""Knowledge-base sharing (invitations) — Share agent, Feature 6.

Ported from fs-agent-chat's routers/agent_invitations_router.py (see
docs/plan/access-and-sharing/archive/design-share-invitations.md). This is genuinely new to
fs-agent-knowledge: before this router, sharing a knowledge base with an
editor/viewer, and enforcing that role on every subsequent request, was
entirely owned by fs-agent-chat — this service only had an owner fast-path
and delegated every other permission check to fs-agent-chat's
``GET /v1/auth/check-access`` over HTTP. That HTTP call is now gone; see
``utils/auth/dependencies.py::require_permission``.

Endpoints (mirrors the source's shapes so a UI built against the old
fs-agent-chat contract isn't surprised):
    POST   /v1/{knowledge_id}/invitations              — invite one or more users
    GET    /v1/{knowledge_id}/invitations               — list invitations
    DELETE /v1/{knowledge_id}/invitations/{user_email}  — revoke an invitation
    PATCH  /v1/{knowledge_id}/invitations/{user_email}  — change a collaborator's role
    GET    /v1/invitations/shared                       — list knowledge shared with a user (reverse lookup, added 2026-08-25)

Auth: only the owner can invite/revoke/change roles (``require_permission
("invite")`` — the permission matrix grants ``invite`` to the ``owner`` role
only, see ``utils/auth/permissions.py``). Listing uses ``require_permission
("read")``, same as every other read endpoint in this service, so an
invited editor/viewer can see who else has access.

Unlike the source, there is no ``owner_email`` request/query field anywhere
here — this service always resolves the owner from ``knowledge_id`` itself
(``KnowledgeConfig.owner_id``), so there is nothing to disambiguate.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from utils.auth.auth_utils import extract_owner_id_from_session
from utils.auth.dependencies import (
    MASTER_API_KEY_ACTOR,
    get_api_key_or_session,
    require_permission,
)
from utils.db.knowledge_invitations import (
    create_invitations as db_create_invitations,
    delete_invitation as db_delete_invitation,
    list_invitations as db_list_invitations,
    list_shared_knowledge as db_list_shared_knowledge,
    update_invitation_role as db_update_invitation_role,
)
from utils.logger import logger
from utils.pydantic_models_knowledge_invitations import (
    CreateInvitationsRequest,
    CreateInvitationsResponse,
    DeleteInvitationResponse,
    InvitationsListResponse,
    SharedKnowledgeListResponse,
    UpdateRoleRequest,
    UpdateRoleResponse,
)

router = APIRouter()


def _resolve_actor_email(auth: dict) -> str:
    """Best-effort email for the caller, for ``invited_by_email``/``updated_by``.

    Only reachable past ``require_permission("invite")`` when the caller is
    the knowledge base's owner (or the master key) — invited editors/viewers
    never hold the ``invite`` permission (see utils/auth/permissions.py), so
    there is no case here where this resolves to someone other than the real
    owner. Mirrors the auth_type branching ``require_permission`` itself
    uses internally.
    """
    auth_type = auth.get("auth_type")
    if auth_type == "master_api_key":
        return MASTER_API_KEY_ACTOR
    if auth_type == "user_api_key":
        return auth.get("user_id") or MASTER_API_KEY_ACTOR
    session_data = auth.get("session_data") or {}
    return extract_owner_id_from_session(session_data) or session_data.get("email") or MASTER_API_KEY_ACTOR


def _resolve_shared_lookup_email(auth: dict, user_id_override: Optional[str]) -> Optional[str]:
    """Resolve which invited-user email ``GET /v1/invitations/shared`` should
    look up. A session caller may only ever resolve to their own email — the
    same "you can only list your own" rule ``list_knowledge`` enforces for
    ``owner_id``. Only the master API key (or a generated per-user key with
    no override) may pass ``user_id_override`` to look up someone else."""
    auth_type = auth.get("auth_type")
    if auth_type == "session":
        session_data = auth.get("session_data") or {}
        caller_email = extract_owner_id_from_session(session_data) or session_data.get("email")
        if user_id_override and (
            not caller_email or user_id_override.strip().lower() != caller_email.strip().lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only list knowledge shared with your own account",
            )
        return caller_email
    if auth_type == "user_api_key":
        return user_id_override or auth.get("user_id")
    # master_api_key
    return user_id_override


@router.get("/v1/invitations/shared", response_model=SharedKnowledgeListResponse)
async def get_shared_knowledge(
    user_id: Optional[str] = Query(
        None,
        description=(
            "Optional override for which invited-user email to list shared knowledge for "
            "(master API key only — a session caller may only ever list their own). "
            "If omitted, the authenticated session email is used."
        ),
    ),
    auth: dict = Depends(get_api_key_or_session),
):
    """List knowledge bases shared with a user (invited email) — the reverse
    of ``GET /v1/{knowledge_id}/invitations``.

    Mirrors fs-agent-chat's ``GET /agents/shared``
    (``agent_registry/router.py``/``agent_registry/db/agent_crud.py::
    list_shared_agents``), a piece of the source's discovery surface the
    original port (docs/plan/access-and-sharing/) never carried over.
    Without it, an invited editor/viewer passes every ``require_permission``
    check on a shared knowledge base but has no way to discover it —
    ``GET /v1`` (``routers/knowledge_management.py::list_knowledge``) only
    ever lists collections the caller owns. See decisions.md, 2026-08-25.

    No ``require_permission`` dependency here, same as the source — this
    doesn't check access to one specific ``knowledge_id``, it lists whichever
    ones the resolved email happens to be invited to.
    """
    resolved_email = _resolve_shared_lookup_email(auth, user_id)
    if not resolved_email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing user identity: provide user_id or authenticate with a session that includes email.",
        )
    try:
        shared = await db_list_shared_knowledge(resolved_email)
    except Exception as e:
        logger.error(f"Error listing shared knowledge for user_email={resolved_email}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
    return {"shared_knowledge": shared}


@router.post(
    "/v1/{knowledge_id}/invitations",
    response_model=CreateInvitationsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitations(
    knowledge_id: str,
    request: CreateInvitationsRequest,
    auth: dict = Depends(get_api_key_or_session),
    _: Optional[str] = Depends(require_permission("invite")),
):
    actor_email = _resolve_actor_email(auth)
    logger.info(
        f"Invitations create requested knowledge_id={knowledge_id} by={actor_email} "
        f"count={len(request.invitations)}"
    )
    invitations = [item.model_dump() for item in request.invitations]
    try:
        result = await db_create_invitations(knowledge_id, actor_email, invitations)
    except Exception as e:
        logger.error(f"Error creating invitations for knowledge_id={knowledge_id}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

    if result["invited_users"] and result["failed_emails"]:
        message = "Invitations sent with some failures"
    elif result["invited_users"]:
        message = "Invitations sent successfully"
    else:
        message = "Invitations failed"
    logger.info(
        f"Invitations create completed knowledge_id={knowledge_id} "
        f"invited={len(result['invited_users'])} failed={len(result['failed_emails'])}"
    )
    return {
        "message": message,
        "invited_users": result["invited_users"],
        "failed_emails": result["failed_emails"],
        "failures": result.get("failures", []),
    }


@router.get("/v1/{knowledge_id}/invitations", response_model=InvitationsListResponse)
async def get_invitations(
    knowledge_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        invited_users = await db_list_invitations(knowledge_id)
    except Exception as e:
        logger.error(f"Error listing invitations for knowledge_id={knowledge_id}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
    return {"invited_users": invited_users}


@router.delete(
    "/v1/{knowledge_id}/invitations/{user_email}",
    response_model=DeleteInvitationResponse,
)
async def delete_invitation(
    knowledge_id: str,
    user_email: str,
    _: Optional[str] = Depends(require_permission("invite")),
):
    try:
        deleted = await db_delete_invitation(knowledge_id, user_email)
    except Exception as e:
        logger.error(f"Error deleting invitation knowledge_id={knowledge_id} user_email={user_email}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
    if not deleted:
        raise HTTPException(status_code=404, detail="Invitation not found")
    logger.info(f"Invitation revoked knowledge_id={knowledge_id} user_email={user_email}")
    return {"message": "Invitation removed successfully"}


@router.patch(
    "/v1/{knowledge_id}/invitations/{user_email}",
    response_model=UpdateRoleResponse,
)
async def update_invitation_role(
    knowledge_id: str,
    user_email: str,
    request: UpdateRoleRequest,
    auth: dict = Depends(get_api_key_or_session),
    _: Optional[str] = Depends(require_permission("invite")),
):
    actor_email = _resolve_actor_email(auth)
    try:
        updated = await db_update_invitation_role(
            knowledge_id, user_email, request.role, actor=actor_email
        )
    except Exception as e:
        logger.error(f"Error updating invitation role knowledge_id={knowledge_id} user_email={user_email}: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
    if not updated:
        raise HTTPException(status_code=404, detail="Invitation not found")
    logger.info(
        f"Invitation role updated knowledge_id={knowledge_id} user_email={user_email} "
        f"role={request.role} by={actor_email}"
    )
    return {"message": "Role updated successfully", "email": user_email, "role": request.role}
