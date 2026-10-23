"""Request/response models for API key generation, ported from
fs-agent-dashboard's api_key_management/pydantic_models.py (see
docs/plan/access-and-sharing/archive/design-api-keys.md).

Differs from upstream: no model ever carries the raw key except
`GenerateKeyResponse.api_key`, returned exactly once at generation time.
List/info responses expose only `key_prefix` (e.g. "sk-a1b2") so a UI can let
a user tell their keys apart without ever re-exposing the full value.
"""

from typing import List, Optional
from pydantic import BaseModel


class GenerateKeyRequest(BaseModel):
    knowledge_id: Optional[str] = None


class GenerateKeyResponse(BaseModel):
    api_key: str  # raw key — shown only this once
    key_prefix: str
    created_at: str
    user_id: str


class RevokeKeyRequest(BaseModel):
    key_prefix: str


class ApiKeyInfo(BaseModel):
    key_prefix: str
    knowledge_id: Optional[str] = None
    is_active: bool
    created_at: str
    revoked_at: Optional[str] = None


class ApiKeysResponse(BaseModel):
    api_keys: List[ApiKeyInfo]
