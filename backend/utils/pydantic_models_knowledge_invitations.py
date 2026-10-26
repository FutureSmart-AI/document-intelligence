"""Request/response models for knowledge-base sharing (invitations).

Mirrors fs-agent-chat's ``chat_services/pydantic_models_auth.py`` invitation
shapes (``CreateInvitationsRequest``/``Response``, ``InvitationsListResponse``,
``DeleteInvitationRequest``/``Response``, ``UpdateRoleRequest``/``Response``) —
see docs/plan/access-and-sharing/archive/design-share-invitations.md. Endpoint/field names are kept
the same as the source where sensible so a future UI built against the old
fs-agent-chat contract isn't surprised; ``agent_id`` in the source has no
counterpart here since the knowledge_id is always the path param, never a
body field.

Only the role vocabulary differs in scope: this service currently has no
"public" self-service invitation flow (a public knowledge base is opted into
via ``is_public``/``fullscreen_is_public`` on the config, not by inviting a
"public" role) — so ``InvitationRole`` only accepts ``editor``/``viewer``,
narrower than fs-agent-chat's full owner/editor/viewer/public role set. An
invitation can never target "owner" (there is exactly one owner, set at
creation) or "public" (not an invitable role) in either service.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, field_validator

InvitationRole = Literal["editor", "viewer"]


class InvitationUser(BaseModel):
    email: str
    role: InvitationRole = "viewer"

    @field_validator("email")
    @classmethod
    def _looks_like_email(cls, v: str) -> str:
        # A light shape check, not full RFC validation — this repo has no
        # email-validator dependency installed (pydantic's EmailStr needs it),
        # and the invited address is looked up by exact string match anyway,
        # not delivered mail, so a stricter validator buys little here.
        v = (v or "").strip()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("must be a valid email address")
        return v


class InvitationListItem(BaseModel):
    email: str
    role: str
    invited_at: Optional[str] = None


class CreateInvitationsRequest(BaseModel):
    invitations: List[InvitationUser]

    @field_validator("invitations")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("invitations must contain at least one entry")
        return v


class InvitationFailure(BaseModel):
    email: str
    reason: str


class CreateInvitationsResponse(BaseModel):
    message: str
    invited_users: List[InvitationUser]
    failed_emails: List[str]
    failures: List[InvitationFailure] = []


class InvitationsListResponse(BaseModel):
    invited_users: List[InvitationListItem]


class DeleteInvitationResponse(BaseModel):
    message: str


class SharedKnowledgeItem(BaseModel):
    knowledge_id: str
    collection_name: Optional[str] = None
    role: str
    invited_by_email: Optional[str] = None
    invited_at: Optional[str] = None


class SharedKnowledgeListResponse(BaseModel):
    shared_knowledge: List[SharedKnowledgeItem]


class UpdateRoleRequest(BaseModel):
    role: InvitationRole


class UpdateRoleResponse(BaseModel):
    message: str
    email: str
    role: InvitationRole
