"""Request/response schemas for owner-scoped BYOK credential management,
ported from fs-agent-usage-limits' services/pydantic_models_credentials.py (see
utils/models/usage_credentials.py for the scoping rationale — migration 039).
"""

from pydantic import BaseModel, Field
from typing import Literal, List, Optional


class CredentialAddRequest(BaseModel):
    provider: Literal["openai", "anthropic", "llamaparse", "google", "groq", "firecrawl", "deepseek", "minimax"] = Field(
        ..., description="Provider name"
    )
    api_key: str = Field(..., min_length=10, description="The API key to store (will be encrypted)")

    class Config:
        json_schema_extra = {"example": {"provider": "openai", "api_key": "replace-with-your-provider-api-key"}}


class CredentialData(BaseModel):
    """One stored provider key, as shown to a dashboard caller — masked, plus its health.

    The three health fields exist so the UI can distinguish "you have a key" from
    "you have a key that stopped working", which is the whole point of tracking
    validity: an owner whose key was revoked needs to be told, since their requests
    are quietly being served by the platform key until they replace it.
    """

    provider: str
    mask: str = Field(..., description="Masked version of the key, e.g. 'sk-...345'")
    is_valid: bool = Field(..., description="False once the provider rejected this key — the platform key is covering")
    updated_at: str
    last_error_type: Optional[str] = Field(
        None, description="Why it was last rejected: auth_error, low_balance, rate_limit, …"
    )
    last_error_at: Optional[str] = Field(None, description="ISO8601 UTC of that rejection")
    last_validated_at: Optional[str] = Field(
        None, description="ISO8601 UTC when this key last provably worked; null = never verified"
    )


class CredentialAddResponse(BaseModel):
    status: Literal["success"] = "success"
    data: CredentialData


class CredentialListResponse(BaseModel):
    data: List[CredentialData] = Field(default_factory=list)


class CredentialDeleteResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str


class CredentialErrorResponse(BaseModel):
    status: Literal["error"] = "error"
    code: str
    message: str


class ProviderListResponse(BaseModel):
    llm_providers: List[str]
    document_and_web_providers: List[str]


class InternalCredentialResponse(BaseModel):
    """INTERNAL ONLY — plaintext key, never returned to a dashboard/UI caller."""

    provider: str
    api_key: str
    is_valid: bool


class InternalCredentialItem(BaseModel):
    owner_id: str
    provider: str
    api_key: str
    is_valid: bool


class InternalCredentialListResponse(BaseModel):
    data: List[InternalCredentialItem] = Field(default_factory=list)


class CredentialFailureReport(BaseModel):
    """A caller (fs-agent-workers) reporting that a provider rejected the owner's
    BYOK key, so fs-agent-knowledge — the only side that holds the credential row —
    can demote it. See utils/usage/credential_health.py."""

    error_type: Literal["auth_error", "low_balance", "rate_limit", "provider_error", "other"] = Field(
        ..., description="Only auth_error/low_balance demote the key; anything else is recorded but left in service"
    )
    message: str = Field("", description="The provider's own error text, for the log line")


class CredentialFailureReportResponse(BaseModel):
    status: Literal["success"] = "success"
    demoted: bool = Field(..., description="True if this report flipped the key to invalid")
