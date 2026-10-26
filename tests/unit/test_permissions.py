"""Unit tests for the local RBAC role/permission matrix
(utils/auth/permissions.py) — ported from fs-agent-chat's
auth_lib/permissions.py, see docs/plan/access-and-sharing/archive/design-share-invitations.md.

Pure unit tests: no DB, no network, no FastAPI app needed.
"""

from utils.auth.permissions import (
    PERMISSIONS,
    VALID_ROLES,
    get_permissions_for_role,
    role_allows,
)


def test_owner_has_every_permission():
    for permission in ("read", "public_read", "use", "write", "delete", "admin_delete", "invite"):
        assert role_allows("owner", permission), f"owner should grant {permission}"


def test_editor_permissions():
    assert role_allows("editor", "read")
    assert role_allows("editor", "write")
    assert role_allows("editor", "delete")
    assert role_allows("editor", "use")
    assert role_allows("editor", "public_read")
    assert not role_allows("editor", "admin_delete")
    assert not role_allows("editor", "invite")


def test_viewer_permissions():
    assert role_allows("viewer", "read")
    assert role_allows("viewer", "use")
    assert role_allows("viewer", "public_read")
    assert not role_allows("viewer", "write")
    assert not role_allows("viewer", "delete")
    assert not role_allows("viewer", "admin_delete")
    assert not role_allows("viewer", "invite")


def test_public_permissions():
    assert role_allows("public", "public_read")
    assert role_allows("public", "use")
    assert not role_allows("public", "read")
    assert not role_allows("public", "write")
    assert not role_allows("public", "delete")
    assert not role_allows("public", "invite")


def test_unknown_or_missing_role_denies_everything():
    assert role_allows(None, "read") is False
    assert role_allows("", "read") is False
    assert role_allows("nonexistent-role", "read") is False


def test_role_matching_is_case_and_whitespace_insensitive():
    assert role_allows(" Editor ", "write") is True
    assert role_allows("VIEWER", "read") is True


def test_get_permissions_for_role():
    assert get_permissions_for_role("owner") == PERMISSIONS["owner"]
    assert get_permissions_for_role("editor") == PERMISSIONS["editor"]
    assert get_permissions_for_role(None) == set()
    assert get_permissions_for_role("nonexistent-role") == set()


def test_valid_roles_matches_matrix_keys():
    assert set(VALID_ROLES) == set(PERMISSIONS.keys())
    assert set(VALID_ROLES) == {"owner", "editor", "viewer", "public"}


def test_matrix_matches_fs_agent_chat_source_exactly():
    """Locks the matrix to the source contract this was ported from
    (fs-agent-chat's auth_lib/permissions.py:31-36) — a UI/consumer built
    against that contract should see the identical shape here."""
    assert PERMISSIONS == {
        "owner": {"read", "public_read", "use", "write", "delete", "admin_delete", "invite"},
        "editor": {"read", "public_read", "use", "write", "delete"},
        "viewer": {"read", "public_read", "use"},
        "public": {"public_read", "use"},
    }
