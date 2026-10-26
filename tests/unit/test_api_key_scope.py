"""Generated (``sk-``) API keys are scoped to their collection and to read/use.

Security audit 2026-10-23 (internal/security/SECURITY-AUDIT-2026-10-23.md): a key
minted for collection A was accepted on every collection its owner could reach, with
the owner's full permission set — settings, deletion, sharing, account usage — and
several routes treated any non-session caller as the master key (list every tenant's
collections, create collections for other owners). The Share screen promises "A key
can read answers and the documents behind them; it can never change settings."

These tests pin that promise:
  * resource scope — a key only reaches the collection it was minted for;
  * action scope   — read/use, plus uploading and deleting documents (the public
                     API's documented write surface); never settings, collection
                     deletion, sharing, keys, account usage, or admin routes.

unittest style so CI's ``python -m unittest discover`` runs them in the API image.
DB lookups (validate_api_key, get_knowledge_service, invitations) are stubbed.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import routers.admin_config as admin_config_module
import routers.knowledge_config_new as knowledge_config_module
import routers.knowledge_health as knowledge_health_module
import routers.knowledge_management as knowledge_management_module
import routers.search as search_module
import utils.db.api_key_ops as api_key_ops
import utils.db.knowledge_invitations as knowledge_invitations
import utils.db.knowledge_ops as knowledge_ops
from utils.auth import dependencies

MASTER_KEY = "test-master-key"
OWNER = "owner@example.test"
COLLECTION_A = "kb-aaaaaaaa"
COLLECTION_B = "kb-bbbbbbbb"
KEY_A = "sk-collection-a-key"
KEY_ACCOUNT = "sk-account-wide-key"
KEYS = {
    KEY_A: {"user_id": OWNER, "knowledge_id": COLLECTION_A},
    KEY_ACCOUNT: {"user_id": OWNER, "knowledge_id": None},
}
OWNED = {COLLECTION_A: OWNER, COLLECTION_B: OWNER}


async def _validate_api_key(raw_key):
    return KEYS.get(raw_key)


async def _get_knowledge_service(knowledge_id):
    owner = OWNED.get(knowledge_id)
    return {"knowledge_id": knowledge_id, "owner_id": owner} if owner else None


async def _no_invitation(knowledge_id, user_email):
    return None


async def _no_session(request, include_all_claims=False):
    return None


def _request(method, path, route_path, path_params):
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "server": ("test", 80), "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [], "path_params": path_params,
        "route": SimpleNamespace(path=route_path),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


class _Stubbed(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(dependencies, "API_KEY_NAME", MASTER_KEY),
            patch.object(dependencies, "get_auth_details", _no_session),
            patch.object(api_key_ops, "validate_api_key", _validate_api_key),
            patch.object(knowledge_ops, "get_knowledge_service", _get_knowledge_service),
            patch.object(knowledge_invitations, "get_invited_role", _no_invitation),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class RequirePermissionKeyScopeTests(_Stubbed):
    """The dependency every /v1/{knowledge_id}/... route goes through."""

    def _call(self, permission, key, knowledge_id, method="GET", route_path="/v1/{knowledge_id}/documents"):
        dep = dependencies.require_permission(permission)
        req = _request(method, route_path.replace("{knowledge_id}", knowledge_id), route_path,
                       {"knowledge_id": knowledge_id})
        return asyncio.run(dep(knowledge_id=knowledge_id, request=req, x_api_key=key))

    def _assert_forbidden(self, *args, **kwargs):
        with self.assertRaises(HTTPException) as ctx:
            self._call(*args, **kwargs)
        self.assertEqual(ctx.exception.status_code, 403)

    # resource scope -----------------------------------------------------------------
    def test_key_cannot_read_another_collection(self):
        self._assert_forbidden("read", KEY_A, COLLECTION_B)

    def test_key_cannot_chat_or_search_another_collection(self):
        self._assert_forbidden("use", KEY_A, COLLECTION_B, method="POST", route_path="/v1/{knowledge_id}/chat")

    def test_key_reads_and_chats_its_own_collection(self):
        self.assertEqual(self._call("read", KEY_A, COLLECTION_A), OWNER)
        self.assertEqual(self._call("use", KEY_A, COLLECTION_A, method="POST", route_path="/v1/{knowledge_id}/chat"), OWNER)

    def test_account_wide_key_reaches_owned_collections_for_reads(self):
        self.assertEqual(self._call("use", KEY_ACCOUNT, COLLECTION_B, method="POST", route_path="/v1/{knowledge_id}/search"), OWNER)

    # action scope -------------------------------------------------------------------
    def test_key_cannot_change_collection_settings(self):
        self._assert_forbidden("write", KEY_A, COLLECTION_A, method="PUT", route_path="/v1/{knowledge_id}/config")

    def test_key_cannot_change_enrichment_fields_or_chat_config(self):
        self._assert_forbidden("write", KEY_A, COLLECTION_A, method="PATCH", route_path="/v1/{knowledge_id}/enrichment")
        self._assert_forbidden("write", KEY_A, COLLECTION_A, method="PUT", route_path="/v1/{knowledge_id}/chat/config")

    def test_key_cannot_delete_the_collection(self):
        self._assert_forbidden("delete", KEY_A, COLLECTION_A, method="DELETE", route_path="/v1/{knowledge_id}")

    def test_key_cannot_share_or_admin_delete(self):
        self._assert_forbidden("invite", KEY_A, COLLECTION_A, method="POST", route_path="/v1/{knowledge_id}/invitations")
        self._assert_forbidden("admin_delete", KEY_A, COLLECTION_A, method="DELETE", route_path="/v1/{knowledge_id}")

    def test_account_wide_key_cannot_change_settings_either(self):
        self._assert_forbidden("write", KEY_ACCOUNT, COLLECTION_A, method="PUT", route_path="/v1/{knowledge_id}/config")

    def test_key_may_upload_and_delete_documents_in_its_collection(self):
        self.assertEqual(self._call("write", KEY_A, COLLECTION_A, method="POST", route_path="/v1/{knowledge_id}/documents"), OWNER)
        self.assertEqual(self._call("delete", KEY_A, COLLECTION_A, method="DELETE",
                                    route_path="/v1/{knowledge_id}/documents/{object_id}"), OWNER)

    def test_key_cannot_upload_into_another_collection(self):
        self._assert_forbidden("write", KEY_A, COLLECTION_B, method="POST", route_path="/v1/{knowledge_id}/documents")

    def test_master_key_still_bypasses(self):
        self.assertIsNone(self._call("write", MASTER_KEY, COLLECTION_B, method="PUT", route_path="/v1/{knowledge_id}/config"))


class AccountLevelRoutesRefuseKeysTests(_Stubbed):
    """Routes outside a single collection: a key never reaches them."""

    def test_require_owner_refuses_a_key(self):
        dep = dependencies.require_owner()
        req = _request("GET", f"/v1/owners/{OWNER}/usage/credentials", "/v1/owners/{owner_id}/usage/credentials", {"owner_id": OWNER})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(dep(owner_id=OWNER, request=req, x_api_key=KEY_A))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_session_or_master_dependency_refuses_a_key(self):
        req = _request("GET", "/v1/health/knowledge-bases", "/v1/health/knowledge-bases", {})
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(dependencies.get_api_key_or_session(req, KEY_A))
        self.assertEqual(ctx.exception.status_code, 403)

    def _client(self, *routers):
        app = FastAPI()
        for r in routers:
            app.include_router(r)
        return TestClient(app)

    def test_key_cannot_list_another_owners_collections(self):
        client = self._client(knowledge_management_module.router)
        resp = client.get("/v1", params={"owner_id": "someone-else@example.test"}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 403)

    def test_key_without_owner_id_gets_its_own_scope_not_every_collection(self):
        """No owner_id used to mean "every collection in the system" for any
        non-session caller. A key is pinned to its own owner and collection."""
        def _all_collections_query():
            raise AssertionError("a key must never reach the all-collections listing")

        async def _owned(owner_id):
            self.assertEqual(owner_id, OWNER)
            return [COLLECTION_A, COLLECTION_B]

        async def _shared(email):
            return []
        client = self._client(knowledge_management_module.router)
        with patch.object(knowledge_management_module, "async_session", _all_collections_query), \
                patch.object(knowledge_management_module, "get_knowledge_services_by_owner", _owned), \
                patch.object(knowledge_management_module, "list_shared_knowledge", _shared):
            resp = client.get("/v1", headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["knowledge_services"], [COLLECTION_A])

    def test_collection_key_lists_only_its_collection(self):
        async def _owned(owner_id):
            self.assertEqual(owner_id, OWNER)
            return [COLLECTION_A, COLLECTION_B]

        async def _shared(email):
            return []
        client = self._client(knowledge_management_module.router)
        with patch.object(knowledge_management_module, "get_knowledge_services_by_owner", _owned), \
                patch.object(knowledge_management_module, "list_shared_knowledge", _shared):
            resp = client.get("/v1", params={"owner_id": OWNER}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["knowledge_services"], [COLLECTION_A])

    def test_key_cannot_create_a_collection_for_anyone(self):
        client = self._client(knowledge_management_module.router)
        resp = client.post("/v1", json={"owner_id": "victim@example.test", "collection_name": "x"}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 403)

    def test_key_cannot_read_any_owners_health_grid(self):
        client = self._client(knowledge_health_module.router)
        resp = client.get("/v1/health/knowledge-bases", params={"owner_id": "someone-else@example.test"}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 403)

    def test_key_cannot_change_settings_over_http(self):
        client = self._client(knowledge_config_module.router)
        resp = client.put(f"/v1/{COLLECTION_A}/config", json={}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 403)

    def test_key_cannot_search_another_collection_over_http(self):
        client = self._client(search_module.router)
        resp = client.post(f"/v1/{COLLECTION_B}/search", json={"query": "q"}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 403)

    def test_model_catalog_still_open_to_a_key(self):
        client = self._client(knowledge_config_module.router)
        resp = client.get("/v1/chat-models", headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 200, resp.text)


class AdminEnrichmentConfigTests(_Stubbed):
    """Global enrichment defaults were served — and writable — with no credentials."""

    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(admin_config_module.router)
        self.client = TestClient(app)

    def test_anonymous_patch_is_refused(self):
        resp = self.client.patch("/v1/admin/enrichment-config", json={"enrichment_temperature": 0.5})
        self.assertEqual(resp.status_code, 401)

    def test_anonymous_read_is_refused(self):
        self.assertEqual(self.client.get("/v1/admin/enrichment-config").status_code, 401)

    def test_generated_key_is_refused(self):
        resp = self.client.patch("/v1/admin/enrichment-config", json={"enrichment_temperature": 0.5}, headers={"x-api-key": KEY_A})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
