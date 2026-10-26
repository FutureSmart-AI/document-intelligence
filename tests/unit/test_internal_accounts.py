"""Internal accounts (utils/usage/internal_accounts.py): a verified FutureSmart /
AI Demos sign-in lifts every trial ceiling; nobody else's limits change.

  * only a session whose auth-service claims say ``email_verified`` for the
    session's own email, on exactly futuresmart.ai or aidemos.com, qualifies;
  * an account on those domains that never signed in verified (the sample
    collections' platform owner, an owner_id typed by a master-key script) keeps
    its configured limits;
  * a public account over its daily message allowance is still refused (429);
  * a lookup failure fails closed — normal limits apply;
  * the internal spend report is master-key only.

unittest style so CI's ``python -m unittest discover`` runs them in the API image.
Every DB read and write is stubbed with an in-memory table.
"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from utils.pydantic_models_usage_limits import CurrentUsageInfo, EstimatedMetric, LimitsInfo, Operation
from utils.usage import enforcement
from utils.usage import internal_accounts as ia
from utils.usage import limit_checking

TRIAL = LimitsInfo(
    max_agents=3, max_indexed_characters=1_000_000, max_knowledge_items=50,
    max_conversations_per_month=100, max_messages_per_month=500, max_llm_tokens_per_month=1_000_000,
    max_documents=40, max_urls=10, max_messages_per_day=50, max_cost_usd_per_month=10.0,
)

SEND_MESSAGE = [EstimatedMetric(name="messages", value=1), EstimatedMetric(name="messages_today", value=1)]


def session(email, verified=True, claim_email=None):
    return {"email": email, "all_claims": {
        "email": email if claim_email is None else claim_email, "email_verified": verified}}


def over_everything():
    return CurrentUsageInfo(
        indexed_characters=10**9, knowledge_items=10**6, agents=10**4, documents=10**6, urls=10**6,
        messages_today=10**5, cost_usd_this_month=10**4, conversations_this_month=10**5,
        messages_this_month=10**6, llm_tokens_this_month=10**10,
    )


class _Base(unittest.IsolatedAsyncioTestCase):
    """An in-memory internal_accounts table, trial defaults for every owner, and
    usage far past every ceiling."""

    def setUp(self):
        ia._internal_cache.clear()
        ia._recorded_at.clear()
        self.table = set()

        async def write(owner_id):
            self.table.add(owner_id)

        async def read(owner_id):
            return owner_id in self.table

        async def no_config_row(session_, owner_id):
            return None

        async def usage(session_, owner_id, *args, **kwargs):
            return over_everything()

        for target, value in (
            ("utils.usage.internal_accounts._write_verification", write),
            ("utils.usage.internal_accounts._read_verification", read),
            ("utils.usage.owner_config.get_owner_config", no_config_row),
            ("utils.usage.limit_checking.get_current_usage", usage),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(ia._internal_cache.clear)
        self.addCleanup(ia._recorded_at.clear)


class WhoQualifies(unittest.TestCase):
    def test_verified_internal_addresses(self):
        for email in ("staff@futuresmart.ai", "Researcher@AIDemos.com", "  x@futuresmart.ai "):
            self.assertEqual(ia.verified_internal_email(session(email)), email.strip().lower())
        self.assertEqual(ia.verified_internal_email(session("a@aidemos.com", verified="true")), "a@aidemos.com")

    def test_everything_else(self):
        cases = {
            "unverified": session("a@futuresmart.ai", verified=False),
            "unverified string": session("a@futuresmart.ai", verified="false"),
            "claim missing": {"email": "a@futuresmart.ai", "all_claims": {"email": "a@futuresmart.ai"}},
            "no claims fetched": {"email": "a@futuresmart.ai"},
            "someone else's claims": session("a@futuresmart.ai", claim_email="b@futuresmart.ai"),
            "subdomain": session("a@mail.futuresmart.ai"),
            "look-alike": session("a@futuresmart.ai.example.com"),
            "suffix": session("a@notfuturesmart.ai"),
            "no local part": session("@futuresmart.ai"),
            "public": session("a@example.org"),
            "nothing": None,
        }
        for name, data in cases.items():
            with self.subTest(name):
                self.assertIsNone(ia.verified_internal_email(data))


class RecordingAndReading(_Base):
    async def test_verified_session_records_the_account(self):
        await ia.record_verified_session(session("Staff@FutureSmart.ai"))
        self.assertEqual(self.table, {"staff@futuresmart.ai"})
        self.assertTrue(await ia.is_internal_owner("staff@futuresmart.ai"))

    async def test_unverified_or_public_session_records_nothing(self):
        await ia.record_verified_session(session("a@futuresmart.ai", verified=False))
        await ia.record_verified_session(session("a@example.org"))
        self.assertEqual(self.table, set())
        self.assertFalse(await ia.is_internal_owner("a@futuresmart.ai"))

    async def test_internal_domain_owner_that_never_signed_in_is_not_internal(self):
        self.assertFalse(await ia.is_internal_owner("document-intelligence-samples@futuresmart.ai"))

    async def test_a_row_for_a_public_address_grants_nothing(self):
        self.table.add("someone@example.org")
        self.assertFalse(await ia.is_internal_owner("someone@example.org"))

    async def test_public_owner_never_reaches_the_table(self):
        async def explode(owner_id):
            raise AssertionError("a public owner must not be looked up")

        with patch("utils.usage.internal_accounts._read_verification", explode):
            self.assertFalse(await ia.is_internal_owner("someone@example.org"))
            self.assertFalse(await ia.is_internal_owner(None))

    async def test_lookup_failure_fails_closed(self):
        async def broken(owner_id):
            raise RuntimeError("db down")

        with patch("utils.usage.internal_accounts._read_verification", broken):
            self.assertFalse(await ia.is_internal_owner("staff@futuresmart.ai"))
            limits = await limit_checking.get_owner_limits(None, "staff@futuresmart.ai")
        self.assertIsNotNone(limits.max_messages_per_day)

    async def test_record_failure_never_raises(self):
        async def broken(owner_id):
            raise RuntimeError("db down")

        with patch("utils.usage.internal_accounts._write_verification", broken):
            await ia.record_verified_session(session("staff@futuresmart.ai"))


class Limits(_Base):
    async def test_internal_owner_has_no_ceilings(self):
        self.table.add("staff@futuresmart.ai")
        limits = await limit_checking.get_owner_limits(None, "staff@futuresmart.ai")
        self.assertTrue(all(value is None for value in limits.model_dump().values()))

    async def test_public_and_unverified_owners_keep_trial_ceilings(self):
        for owner in ("someone@example.org", "a@futuresmart.ai"):
            with self.subTest(owner):
                limits = await limit_checking.get_owner_limits(None, owner)
                self.assertIsNotNone(limits.max_messages_per_day)
                self.assertIsNotNone(limits.max_messages_per_month)

    async def test_internal_owner_is_allowed_past_every_trial_limit(self):
        self.table.add("staff@futuresmart.ai")
        operations = {
            Operation.send_message: SEND_MESSAGE,
            Operation.start_conversation: [EstimatedMetric(name="conversations", value=1)],
            Operation.create_knowledge_item: [EstimatedMetric(name="knowledge_items", value=1),
                                              EstimatedMetric(name="documents", value=1)],
            Operation.create_agent: [EstimatedMetric(name="agents", value=1)],
            Operation.llm_call: [EstimatedMetric(name="llm_tokens", value=1000)],
        }
        for operation, metrics in operations.items():
            with self.subTest(operation.value):
                result = await limit_checking.check_operation_allowed(None, "staff@futuresmart.ai", operation, metrics)
                self.assertTrue(result.allowed)
                self.assertEqual(result.would_exceed, [])

    async def test_public_owner_over_the_daily_message_limit_is_refused(self):
        async def trial(session_, owner_id):
            return TRIAL

        with patch("utils.usage.limit_checking.get_owner_limits", trial):
            result = await limit_checking.check_operation_allowed(
                None, "someone@example.org", Operation.send_message, SEND_MESSAGE)
        self.assertFalse(result.allowed)
        self.assertIn("max_messages_per_day", {e.limit_name for e in result.would_exceed})

    async def test_the_gate_refuses_public_and_allows_internal(self):
        """Through enforce_operation, the one gate every route uses, enforcement on."""
        class _Session:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        with patch.dict("os.environ", {"USAGE_ENFORCE_LIMITS": "true"}), \
                patch("utils.usage.enforcement.async_session", lambda: _Session()):
            with self.assertRaises(HTTPException) as refused:
                await enforcement.enforce_operation("someone@example.org", Operation.send_message, SEND_MESSAGE)
            self.assertEqual(refused.exception.status_code, 429)

            self.table.add("staff@futuresmart.ai")
            allowed = await enforcement.enforce_operation("staff@futuresmart.ai", Operation.send_message, SEND_MESSAGE)
        self.assertTrue(allowed is not None and allowed.allowed)


class WhereVerificationComesFrom(_Base):
    async def test_only_a_claims_bearing_auth_result_records(self):
        from utils.auth import dependencies

        async def fetch(request, include_all_claims=False):
            return session("staff@futuresmart.ai") if include_all_claims else {"email": "staff@futuresmart.ai"}

        class _State:
            pass

        class _Request:
            def __init__(self):
                self.state = _State()

        with patch.object(dependencies, "_fetch_auth_details", fetch):
            await dependencies.get_auth_details(_Request(), include_all_claims=False)
            self.assertEqual(self.table, set())
            await dependencies.get_auth_details(_Request(), include_all_claims=True)
        self.assertEqual(self.table, {"staff@futuresmart.ai"})


class SpendReport(unittest.TestCase):
    def test_requires_the_master_key(self):
        from routers.usage_limits import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        self.assertEqual(client.get("/v1/usage/internal-accounts").status_code, 401)
        self.assertEqual(
            client.get("/v1/usage/internal-accounts", headers={"x-api-key": "not-the-master-key"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
