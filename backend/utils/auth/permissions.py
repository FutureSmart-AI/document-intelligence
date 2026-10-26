"""Centralized RBAC role/permission matrix — this service's own copy.

Ported from fs-agent-chat's ``auth_lib/permissions.py`` (see
docs/plan/access-and-sharing/archive/design-share-invitations.md), which was the single source of
truth this service used to delegate to over HTTP
(``CHAT_AUTH_BASE``/``check-access``) for every non-owner permission check.
This module + ``utils/db/knowledge_invitations.py`` replace that call —
see ``utils/auth/dependencies.py::require_permission``.

Not a verbatim copy: the source's docstrings talk about "agents"; this
service's identity unit is a knowledge base (``knowledge_id`` — see
docs/overview/glossary.md), so the wording below is knowledge-flavored.
The actual role names, permission names, and the matrix values themselves
are kept byte-identical to the source — this is deliberately the same RBAC
contract fs-agent-chat has always exposed for a shared agent, now enforced
locally instead of over the network.

Permissions:
    read         – view / list resources (owner-internal payload)
    public_read  – read a sanitized, widget-safe subset of a public knowledge
                   base's config
    use          – use the knowledge base (chat / search)
    write        – create, edit, upload, update resources
    delete       – resource-level cleanup (editor-safe: documents, chunks,
                   connectors)
    admin_delete – destructive ops with broad blast radius (owner-only:
                   the knowledge base itself, API keys)
    invite       – manage user invitations (share/unshare, role changes)

Roles:
    owner / editor / viewer – authenticated users with progressively
                              narrower access.
    public                  – anonymous caller hitting an ``is_public``
                              (or ``fullscreen_is_public``) knowledge base.
                              Chat-only plus sanitized read of widget-facing
                              fields. This service's public path
                              (``utils/auth/public_access.py``) does not
                              currently route through ``role_allows`` — it has
                              its own dedicated gate — but the role is kept
                              here for parity with the source matrix and in
                              case a future caller needs to reason about it
                              generically.

Rule of thumb for choosing between delete and admin_delete:
    If undoing requires restoring from backup, contacting support, or
    invalidating credentials -> admin_delete. If the user can re-upload or
    recreate it in a minute -> delete.
"""

from typing import Dict, Set, Optional


PERMISSIONS: Dict[str, Set[str]] = {
    "owner": {"read", "public_read", "use", "write", "delete", "admin_delete", "invite"},
    "editor": {"read", "public_read", "use", "write", "delete"},
    "viewer": {"read", "public_read", "use"},
    "public": {"public_read", "use"},
}

PERMISSION_DESCRIPTIONS: Dict[str, str] = {
    "read": "view or list resources",
    "public_read": "read widget-facing config for a public knowledge base",
    "use": "use the knowledge base (chat / search)",
    "write": "create, edit, or upload resources",
    "delete": "delete resources (editor-safe: documents, chunks, connectors)",
    "admin_delete": "destructive deletions (owner-only: the knowledge base, API keys)",
    "invite": "manage user invitations",
}

VALID_ROLES = list(PERMISSIONS.keys())


def role_allows(role: Optional[str], permission: str) -> bool:
    """Check whether *role* grants *permission*.

    Returns ``False`` when *role* is ``None`` or unrecognised.
    """
    if not role:
        return False
    allowed = PERMISSIONS.get(str(role).lower().strip(), set())
    return permission in allowed


def get_permissions_for_role(role: Optional[str]) -> Set[str]:
    """Return the concrete permission set for a role."""
    if not role:
        return set()
    return set(PERMISSIONS.get(str(role).lower().strip(), set()))
