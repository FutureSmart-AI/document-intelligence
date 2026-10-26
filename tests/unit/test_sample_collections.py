"""Sample collections are shared, read-only, and private per visitor.

A sample (knowledge_config.sample_key, migration 053) is one collection every
signed-in account may open. These tests pin what makes sharing one copy safe
(utils/auth/sample_access.py):

  * nobody but its owner or the master key changes it — no write, delete, upload,
    settings, sharing, or invitation-granted role reaches it;
  * owner-internal reads are refused too (analytics, everyone's conversations,
    the share list) — a visitor gets an allow-list of routes, not the viewer role;
  * a visitor sees, opens, continues and deletes only their own conversations;
  * generated API keys never reach a sample, and stay scoped to their own
    collection everywhere else;
  * a visitor's question spends the sample allowance, not their own trial: it is
    charged to the sample's owner even if the body names another owner;
  * a session id is never continued in a collection it does not belong to.

unittest style so CI's ``python -m unittest discover`` runs them in the API image.
Every DB lookup is stubbed.
"""

import asyncio
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

import routers.chat as chat_router_module
import routers.samples as samples_module
import utils.db.api_key_ops as api_key_ops
import utils.db.knowledge_invitations as knowledge_invitations
import utils.db.knowledge_ops as knowledge_ops
from utils.auth import dependencies, sample_access

MASTER_KEY = "test-master-key"
SAMPLE_OWNER = "document-intelligence-samples@futuresmart.ai"
SAMPLE = "kb-sample-resumes"
OTHER_SAMPLE = "kb-sample-invoices"
VISITOR = {"email": "visitor@example.test", "user_id": "uuid-visitor"}
OTHER_VISITOR = {"email": "someone@example.test", "user_id": "uuid-someone"}
VISITOR_COLLECTION = "kb-visitor-own"
NORMAL = "kb-normal"
NORMAL_OWNER = "owner@example.test"

COLLECTIONS = {
    SAMPLE: {"owner_id": SAMPLE_OWNER, "sample_key": "resumes"},
    OTHER_SAMPLE: {"owner_id": SAMPLE_OWNER, "sample_key": "invoices"},
    VISITOR_COLLECTION: {"owner_id": VISITOR["email"], "sample_key": None},
    NORMAL: {"owner_id": NORMAL_OWNER, "sample_key": None},
}
KEY_VISITOR_COLLECTION = "sk-visitor-collection"
KEY_VISITOR_ACCOUNT = "sk-visitor-account"
KEY_SAMPLE_OWNER = "sk-sample-owner"
KEYS = {
    KEY_VISITOR_COLLECTION: {"user_id": VISITOR["email"], "knowledge_id": VISITOR_COLLECTION},
    KEY_VISITOR_ACCOUNT: {"user_id": VISITOR["email"], "knowledge_id": None},
    KEY_SAMPLE_OWNER: {"user_id": SAMPLE_OWNER, "knowledge_id": SAMPLE},
}
SESSIONS = {
    "s-visitor": {"knowledge_id": SAMPLE, "user_id": VISITOR["user_id"], "user_email": VISITOR["email"]},
    "s-someone": {"knowledge_id": SAMPLE, "user_id": OTHER_VISITOR["user_id"], "user_email": OTHER_VISITOR["email"]},
    "s-visitor-elsewhere": {"knowledge_id": OTHER_SAMPLE, "user_id": VISITOR["user_id"], "user_email": VISITOR["email"]},
    "s-private": {"knowledge_id": NORMAL, "user_id": "uuid-owner", "user_email": NORMAL_OWNER},
}


async def _get_knowledge_service(knowledge_id):
    row = COLLECTIONS.get(knowledge_id)
    return {"knowledge_id": knowledge_id, **row} if row else None


async def _validate_api_key(raw_key):
    return KEYS.get(raw_key)


async def _get_session_identity(session_id):
    return SESSIONS.get(session_id)


def _request(method, route_path, knowledge_id, *, path_params=None, query=b"", body=b"", session=None):
    path_params = {"knowledge_id": knowledge_id, **(path_params or {})}
    path = route_path
    for key, value in path_params.items():
        path = path.replace("{" + key + "}", value)
    scope = {
        "type": "http", "http_version": "1.1", "method": method, "scheme": "http",
        "server": ("test", 80), "path": path, "raw_path": path.encode(), "query_string": query,
        "headers": [(b"content-type", b"application/json")], "path_params": path_params,
        "route": SimpleNamespace(path=route_path), "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    req = Request(scope, receive)
    req.scope["_test_session"] = session
    return req


class _Stubbed(unittest.TestCase):
    def setUp(self):
        self.spent = []
        self.invited_role = None

        async def _session(request, include_all_claims=False):
            return request.scope.get("_test_session")

        async def _invited_role(knowledge_id, email):
            return self.invited_role

        async def _spend(knowledge_id, email, audience, session_id):
            self.spent.append((knowledge_id, email, audience, session_id))

        self._patches = [
            patch.object(dependencies, "API_KEY_NAME", MASTER_KEY),
            patch.object(dependencies, "get_auth_details", _session),
            patch.object(api_key_ops, "validate_api_key", _validate_api_key),
            patch.object(knowledge_ops, "get_knowledge_service", _get_knowledge_service),
            patch.object(knowledge_invitations, "get_invited_role", _invited_role),
            patch.object(sample_access, "get_session_identity", _get_session_identity),
            patch.object(sample_access, "spend_sample_question", _spend),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _call(self, permission, method, route_path, knowledge_id=SAMPLE, *, session=VISITOR, key=None, **kwargs):
        req = _request(method, route_path, knowledge_id, session=session, **kwargs)
        dep = dependencies.require_permission(permission)
        return asyncio.run(dep(knowledge_id=knowledge_id, request=req, x_api_key=key)), req

    def _assert_status(self, status, *args, **kwargs):
        with self.assertRaises(HTTPException) as ctx:
            self._call(*args, **kwargs)
        self.assertEqual(ctx.exception.status_code, status, ctx.exception.detail)


class VisitorReadsTheSampleTests(_Stubbed):
    def test_visitor_reads_documents_fields_and_the_table(self):
        for method, path in [
            ("GET", "/v1/{knowledge_id}/config"),
            ("GET", "/v1/{knowledge_id}/enrichment"),
            ("GET", "/v2/{knowledge_id}/documents"),
            ("POST", "/v2/{knowledge_id}/documents/metadata/all"),
            ("GET", "/v1/{knowledge_id}/chat/config"),
        ]:
            user, req = self._call("read", method, path)
            self.assertEqual(user, VISITOR["user_id"])
            self.assertTrue(req.state.sample_visitor)

    def test_visitor_opens_one_document(self):
        user, _ = self._call("read", "GET", "/v1/{knowledge_id}/documents/{object_id}/metadata",
                             path_params={"object_id": "doc-1"})
        self.assertEqual(user, VISITOR["user_id"])


class NobodyChangesTheSampleTests(_Stubbed):
    """Every mutating surface a collection has, refused for a visitor."""

    MUTATIONS = [
        ("write", "PUT", "/v1/{knowledge_id}/config"),
        ("write", "PATCH", "/v1/{knowledge_id}/enrichment"),
        ("write", "POST", "/v1/{knowledge_id}/enrichment"),
        ("write", "PUT", "/v1/{knowledge_id}/chat/config"),
        ("write", "PUT", "/v1/{knowledge_id}/widget-config"),
        ("write", "POST", "/v1/{knowledge_id}/documents"),
        ("write", "POST", "/v1/{knowledge_id}/documents/batch"),
        ("write", "POST", "/v1/{knowledge_id}/upload-urls/batch"),
        ("delete", "DELETE", "/v1/{knowledge_id}/documents/{object_id}"),
        ("admin_delete", "DELETE", "/v1/{knowledge_id}"),
        ("invite", "POST", "/v1/{knowledge_id}/invitations"),
        ("write", "POST", "/v1/{knowledge_id}/reports"),
    ]

    def test_visitor_cannot_change_anything(self):
        for permission, method, path in self.MUTATIONS:
            with self.subTest(route=(method, path)):
                self._assert_status(403, permission, method, path, path_params={"object_id": "doc-1"})

    def test_an_invitation_never_makes_anyone_an_editor_of_the_master(self):
        self.invited_role = "editor"
        for permission, method, path in self.MUTATIONS:
            with self.subTest(route=(method, path)):
                self._assert_status(403, permission, method, path, path_params={"object_id": "doc-1"})

    def test_owner_internal_reads_are_refused(self):
        for path in [
            "/v1/{knowledge_id}/health/queries",
            "/v1/{knowledge_id}/health/scorecard",
            "/v1/{knowledge_id}/invitations",
            "/v1/{knowledge_id}/widget-config",
            "/v1/{knowledge_id}/reports",
            "/v1/{knowledge_id}/clickup/config",
        ]:
            with self.subTest(route=path):
                self._assert_status(403, "read", "GET", path)

    def test_the_sample_owner_and_the_master_key_still_can(self):
        user, _ = self._call("write", "PUT", "/v1/{knowledge_id}/config", session={"email": SAMPLE_OWNER, "user_id": "uuid-o"})
        self.assertEqual(user, "uuid-o")
        user, _ = self._call("admin_delete", "DELETE", "/v1/{knowledge_id}", session=None, key=MASTER_KEY)
        self.assertIsNone(user)

    def test_a_visitor_cannot_change_another_visitors_collection(self):
        # Not a sample: the normal RBAC path, and the visitor has no invitation.
        self._assert_status(403, "write", "PUT", "/v1/{knowledge_id}/config", NORMAL)
        self._assert_status(403, "admin_delete", "DELETE", "/v1/{knowledge_id}", NORMAL)
        self._assert_status(403, "read", "GET", "/v1/{knowledge_id}/sessions", NORMAL)


class ConversationsArePerVisitorTests(_Stubbed):
    def test_list_requires_the_callers_own_filter(self):
        for query in (b"", b"user_id=uuid-someone", b"user_id=someone%40example.test"):
            with self.subTest(query=query):
                self._assert_status(403, "read", "GET", "/v1/{knowledge_id}/sessions", query=query)
        for query in (b"user_id=uuid-visitor", b"user_id=visitor%40example.test"):
            user, _ = self._call("read", "GET", "/v1/{knowledge_id}/sessions", query=query)
            self.assertEqual(user, VISITOR["user_id"])

    def test_another_visitors_conversation_is_not_found(self):
        for method, permission, path, extra in [
            ("GET", "read", "/v1/{knowledge_id}/sessions/{session_id}", {}),
            ("DELETE", "delete", "/v1/{knowledge_id}/sessions/{session_id}", {}),
            ("PATCH", "write", "/v1/{knowledge_id}/sessions/{session_id}/messages/{chat_id}/feedback", {"chat_id": "c1"}),
        ]:
            with self.subTest(route=(method, path)):
                self._assert_status(404, permission, method, path, path_params={"session_id": "s-someone", **extra})
                self._assert_status(404, permission, method, path, path_params={"session_id": "s-visitor-elsewhere", **extra})
                user, _ = self._call(permission, method, path, path_params={"session_id": "s-visitor", **extra})
                self.assertEqual(user, VISITOR["user_id"])

    def test_chat_cannot_continue_someone_elses_conversation(self):
        for session_id in ("s-someone", "s-private", "s-visitor-elsewhere"):
            with self.subTest(session_id=session_id):
                body = ('{"query": "q", "session_id": "%s"}' % session_id).encode()
                self._assert_status(404, "use", "POST", "/v1/{knowledge_id}/chat", body=body)
        self.assertEqual(self.spent, [], "a refused question must not spend the allowance")

    def test_a_question_spends_the_allowance_once(self):
        body = b'{"query": "q", "session_id": "s-visitor"}'
        req = _request("POST", "/v1/{knowledge_id}/chat", SAMPLE, session=VISITOR, body=body)
        dep = dependencies.require_permission("use")
        asyncio.run(dep(knowledge_id=SAMPLE, request=req, x_api_key=None))
        asyncio.run(dep(knowledge_id=SAMPLE, request=req, x_api_key=None))  # router + route level
        self.assertEqual(self.spent, [(SAMPLE, VISITOR["email"], "public", "s-visitor")])

    def test_only_a_verified_internal_sign_in_spends_as_internal(self):
        def spent_as(session):
            self.spent.clear()
            req = _request("POST", "/v1/{knowledge_id}/chat", SAMPLE, session=session, body=b'{"query": "q"}')
            asyncio.run(dependencies.require_permission("use")(knowledge_id=SAMPLE, request=req, x_api_key=None))
            return self.spent[0][2]

        staff = {"email": "staff@futuresmart.ai", "user_id": "uuid-p"}
        verified = {**staff, "all_claims": {"email": "staff@futuresmart.ai", "email_verified": True}}
        unverified = {**staff, "all_claims": {"email": "staff@futuresmart.ai", "email_verified": False}}
        self.assertEqual(spent_as(verified), "internal")
        # A sign-up that never verified the address is a stranger, whatever it typed.
        self.assertEqual(spent_as(unverified), "public")
        self.assertEqual(spent_as(staff), "public")

    def test_allowance_refusal_is_a_429(self):
        async def _refuse(knowledge_id, email, audience, session_id):
            raise HTTPException(status_code=429, detail={"error": sample_access.SAMPLE_QUESTION_LIMIT_ERROR, "message": "x"})

        with patch.object(sample_access, "spend_sample_question", _refuse):
            self._assert_status(429, "use", "POST", "/v1/{knowledge_id}/chat", body=b'{"query": "q"}')


class ApiKeysStayScopedTests(_Stubbed):
    def test_no_visitor_key_reaches_a_sample(self):
        for key in (KEY_VISITOR_COLLECTION, KEY_VISITOR_ACCOUNT):
            with self.subTest(key=key):
                self._assert_status(403, "read", "GET", "/v2/{knowledge_id}/documents", session=None, key=key)
                self._assert_status(403, "use", "POST", "/v1/{knowledge_id}/chat", session=None, key=key,
                                    body=b'{"query": "q"}')

    def test_visitor_key_still_works_on_its_own_collection_only(self):
        user, _ = self._call("read", "GET", "/v2/{knowledge_id}/documents", VISITOR_COLLECTION, session=None,
                             key=KEY_VISITOR_COLLECTION)
        self.assertEqual(user, VISITOR["email"])
        self._assert_status(403, "read", "GET", "/v2/{knowledge_id}/documents", NORMAL, session=None,
                            key=KEY_VISITOR_COLLECTION)

    def test_the_sample_owners_key_is_still_read_only(self):
        self._assert_status(403, "write", "PUT", "/v1/{knowledge_id}/config", session=None, key=KEY_SAMPLE_OWNER)


class AllowanceTests(unittest.TestCase):
    LIMITS = sample_access.SampleLimits(per_day=20, total=100, global_per_day=1000)

    def _refusal(self, **counts):
        base = {"audience": "public", "user_today": 0, "user_total": 0, "global_today": 0}
        base.update(counts)
        return sample_access.allowance_refusal(limits=self.LIMITS, **base)

    def test_within_allowance(self):
        self.assertIsNone(self._refusal(user_today=19, user_total=99, global_today=999))

    def test_daily_total_and_global_ceilings(self):
        self.assertIn("today", self._refusal(user_today=20, user_total=20))
        self.assertIn("all 100", self._refusal(user_today=0, user_total=100))
        self.assertIn("tomorrow", self._refusal(global_today=1000))

    def test_internal_is_unlimited(self):
        self.assertIsNone(self._refusal(audience="internal", user_today=999, user_total=999, global_today=10**6))

    def test_internal_needs_a_verified_sign_in_for_that_address(self):
        def session(email, claims):
            return {"email": email, "all_claims": claims}

        audience = sample_access.audience_for
        self.assertEqual(audience(session("staff@FutureSmart.ai",
                                          {"email": "staff@futuresmart.ai", "email_verified": True})), "internal")
        self.assertEqual(audience(session("staff@futuresmart.ai",
                                          {"email": "staff@futuresmart.ai", "email_verified": "true"})), "internal")
        # Negative: unverified, no claims, a claim for another address, a look-alike domain.
        self.assertEqual(audience(session("new@futuresmart.ai",
                                          {"email": "new@futuresmart.ai", "email_verified": False})), "public")
        self.assertEqual(audience(session("new@futuresmart.ai", {})), "public")
        self.assertEqual(audience(session("boss@futuresmart.ai",
                                          {"email": "me@example.org", "email_verified": True})), "public")
        self.assertEqual(audience(session("me@futuresmart.ai.evil.test",
                                          {"email": "me@futuresmart.ai.evil.test", "email_verified": True})), "public")
        self.assertEqual(audience(None), "public")


class AllowListMatchesTheAppTests(unittest.TestCase):
    def test_every_allowed_route_exists(self):
        from main import app

        routes = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
        missing = set(sample_access.SAMPLE_VISITOR_ROUTES) - routes
        self.assertEqual(missing, set(), "the sample allow-list names routes the app does not have")

    def test_only_conversation_routes_change_anything(self):
        for (method, path), kind in sample_access.SAMPLE_VISITOR_ROUTES.items():
            if method in ("PUT", "PATCH", "DELETE"):
                self.assertEqual(kind, "session", (method, path))
            if method == "POST" and kind == "open":
                self.assertTrue(path.endswith(("/summaries", "/metadata/all", "/chunks/batch")), path)


class ChatChargesTheRealOwnerTests(unittest.TestCase):
    """The body's owner_id/user_id are honoured for the master key only."""

    def _post(self, caller, body):
        seen = {}

        async def _resolve_owner(knowledge_id):
            return COLLECTIONS[knowledge_id]["owner_id"]

        async def _enforce(**kwargs):
            seen.setdefault("charged", []).append(kwargs["owner_id"])

        async def _run(**kwargs):
            seen["owner_id"] = kwargs["owner_id"]
            seen["user_id"] = kwargs["user_id"]
            return {"response": "ok", "is_answered": "yes", "relevant_sources": None, "summaries": None,
                    "detailed_chunks": None, "rag_context": {"tool_responses": []}, "usage_by_model": [],
                    "service_calls": []}

        async def _config(knowledge_id, **kwargs):
            import chat.config as chat_config_module
            return chat_config_module.ResolvedChatConfig(
                llm_provider="openai", llm_model="m", temperature=0.0, system_prompt=None,
                show_sources=True, chat_enabled=True)

        async def _profile(request):
            return None, None

        async def _past(session_id, limit=10):
            return []

        async def _log(**kwargs):
            return SimpleNamespace(chat_id=uuid.UUID(int=0))

        app = FastAPI()
        app.include_router(chat_router_module.router)
        for route in chat_router_module.router.routes:
            for dep in route.dependant.dependencies:
                if dep.call.__qualname__.endswith("require_permission.<locals>.dependency"):
                    app.dependency_overrides[dep.call] = lambda: caller
        with patch.object(chat_router_module, "resolve_kb_owner_id", _resolve_owner), \
                patch.object(chat_router_module, "enforce_operation", _enforce), \
                patch.object(chat_router_module, "run_knowledge_chat_graph", _run), \
                patch.object(chat_router_module, "resolve_chat_config", _config), \
                patch.object(chat_router_module, "get_past_messages", _past), \
                patch.object(chat_router_module, "insert_chat_log", _log), \
                patch.object(sample_access, "get_session_identity", _get_session_identity), \
                patch.object(chat_router_module, "_resolve_caller_profile", _profile):
            resp = TestClient(app).post(f"/v1/{SAMPLE}/chat", json={**body, "stream": False})
        return resp, seen

    def test_a_visitor_naming_another_owner_is_charged_to_the_sample_owner(self):
        resp, seen = self._post(VISITOR["user_id"], {"query": "q", "owner_id": "victim@example.test",
                                                     "user_id": "uuid-someone"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(seen["owner_id"], SAMPLE_OWNER)
        self.assertEqual(set(seen["charged"]), {SAMPLE_OWNER})
        self.assertEqual(seen["user_id"], VISITOR["user_id"])

    def test_the_master_key_may_still_act_for_an_owner(self):
        resp, seen = self._post(None, {"query": "q", "owner_id": "acting@example.test", "user_id": "u-1"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(seen["owner_id"], "acting@example.test")
        self.assertEqual(seen["user_id"], "u-1")

    def test_a_session_from_another_collection_is_not_continued(self):
        for caller in (VISITOR["user_id"], None):
            resp, seen = self._post(caller, {"query": "q", "session_id": "s-private"})
            self.assertEqual(resp.status_code, 404, resp.text)
            self.assertNotIn("owner_id", seen)


class SamplesRouterTests(_Stubbed):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(samples_module.router)
        self.client = TestClient(app)

    def test_marking_a_sample_is_master_key_only(self):
        calls = []

        async def _set(knowledge_id, sample_key):
            calls.append((knowledge_id, sample_key))
            return True

        with patch.object(sample_access, "set_sample_key", _set):
            self.assertEqual(self.client.put(f"/v1/{NORMAL}/sample", json={"sample_key": "x1"}).status_code, 401)
            resp = self.client.put(f"/v1/{NORMAL}/sample", json={"sample_key": "resumes"},
                                   headers={"x-api-key": KEY_SAMPLE_OWNER})
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(calls, [])
            resp = self.client.put(f"/v1/{NORMAL}/sample", json={"sample_key": "Not A Slug!"},
                                   headers={"x-api-key": MASTER_KEY})
            self.assertEqual(resp.status_code, 422)
            resp = self.client.put(f"/v1/{NORMAL}/sample", json={"sample_key": "resumes"},
                                   headers={"x-api-key": MASTER_KEY})
            self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(calls, [(NORMAL, "resumes")])

    def test_listing_samples_refuses_generated_keys(self):
        resp = self.client.get("/v1/samples", headers={"x-api-key": KEY_VISITOR_ACCOUNT})
        self.assertEqual(resp.status_code, 403)

    def test_adoption_is_master_key_only(self):
        self.assertEqual(self.client.get("/v1/samples/usage").status_code, 401)


if __name__ == "__main__":
    unittest.main()
