"""Run-level usage telemetry for a chat turn (chat/run_usage.py, Contract 1).

The collector must count EVERY model call of a turn, attribute it to an
operation, show the model that actually served it (fallbacks visible), price it
with chat/pricing.py, and never count an unpriced model as $0. These tests drive
real LangChain chat models (fake ones from langchain_core, so no network) through
the real callback machinery, so a broken hook registration fails here.
"""
import asyncio
import unittest
from typing import Any, List, Optional

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from chat import pricing
from chat.run_usage import (
    build_usage_block,
    collect_run_usage,
    cost_breakdown_by_model,
    in_operation,
    price_operations,
    record_embedding,
    usage_operation,
)


class _Model(GenericFakeChatModel):
    """A fake chat model that reports a provider/model and token usage the way
    langchain_openai does (ls_provider/ls_model_name + usage_metadata)."""

    provider: str = "openai"
    model_name: str = "gpt-5.4-nano"
    usage: dict = {}
    fail: bool = False

    def _get_ls_params(self, stop: Optional[List[str]] = None, **kwargs: Any):
        return {"ls_provider": self.provider, "ls_model_name": self.model_name, "ls_model_type": "chat"}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.fail:
            raise RuntimeError("provider 503")
        msg = AIMessage(content="ok", usage_metadata=self.usage,
                        response_metadata={"model_name": f"{self.model_name}-2026-03-17"})
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _model(model="gpt-5.4-nano", provider="openai", inp=100, out=10, cached=0, write=0, fail=False):
    details = {}
    if cached:
        details["cache_read"] = cached
    if write:
        details["cache_creation"] = write
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    if details:
        usage["input_token_details"] = details
    return _Model(messages=iter([]), provider=provider, model_name=model, usage=usage, fail=fail)


def _ops(block):
    return {(r["operation"], r["model"]): r for r in block["operations"]}


class CollectorTests(unittest.TestCase):
    def test_counts_every_call_and_attributes_operations(self):
        async def turn():
            with collect_run_usage() as c:
                with usage_operation("query_refinement"):
                    await _model(inp=50, out=5).ainvoke("q")
                await in_operation("answer", _model("gpt-5.6-luna", inp=1000, out=200).ainvoke("q"))
                await in_operation("answer", _model("gpt-5.6-luna", inp=500, out=100).ainvoke("q"))
                # No marker anywhere: must still be counted, as other_calls.
                await _model(inp=7, out=3).ainvoke("q")
            return build_usage_block(c, 1234, [{"service": "rag"}])

        block = asyncio.run(turn())
        ops = _ops(block)
        self.assertEqual(ops[("query_refinement", "gpt-5.4-nano")]["calls"], 1)
        answer = ops[("answer", "gpt-5.6-luna")]
        self.assertEqual((answer["calls"], answer["input_tokens"], answer["output_tokens"]), (2, 1500, 300))
        self.assertEqual(ops[("other_calls", "gpt-5.4-nano")]["input_tokens"], 7)
        self.assertEqual(block["input_tokens"], 50 + 1500 + 7)
        self.assertEqual(block["output_tokens"], 5 + 300 + 3)
        self.assertEqual(block["total_tokens"], block["input_tokens"] + block["output_tokens"])
        self.assertEqual(block["latency_ms"], 1234)
        self.assertEqual(block["service_calls"], [{"service": "rag"}])
        self.assertEqual(block["price_table"], pricing.PRICE_TABLE_VERSION)
        self.assertTrue(block["cost_complete"])
        self.assertIn("answer", block["operation_latency_ms"])
        # Totals reconcile: the operations add up to the top-level numbers.
        self.assertEqual(sum(r["input_tokens"] for r in block["operations"]), block["input_tokens"])
        self.assertAlmostEqual(
            sum(r["estimated_cost_usd"] for r in block["operations"]), block["estimated_cost_usd"], places=6)

    def test_fallback_is_visible_with_requested_model(self):
        async def turn():
            with collect_run_usage() as c:
                with usage_operation("retrieval_agent"):
                    try:
                        await _model("gpt-5.6-luna", fail=True).ainvoke("q")
                    except RuntimeError:
                        pass
                    await _model("gpt-5.4-mini", inp=300, out=30).ainvoke("q")
            return build_usage_block(c, 10)

        block = asyncio.run(turn())
        ops = _ops(block)
        served = ops[("retrieval_agent", "gpt-5.4-mini")]
        self.assertTrue(served["fallback"])
        self.assertEqual(served["requested_model"], "gpt-5.6-luna")
        self.assertIn("provider 503", served["fallback_reason"])
        failed = ops[("retrieval_agent", "gpt-5.6-luna")]
        self.assertEqual((failed["calls"], failed["failed_calls"]), (0, 1))
        self.assertIsNone(failed["estimated_cost_usd"])  # no tokens: nothing to price
        self.assertTrue(block["cost_complete"])

    def test_unpriced_model_is_null_not_zero(self):
        async def turn():
            with collect_run_usage() as c:
                with usage_operation("answer"):
                    await _model("gemini-3.1-flash-lite", provider="google_genai", inp=100, out=10).ainvoke("q")
                    await _model("gpt-5.4-nano", inp=100, out=10).ainvoke("q")
            return build_usage_block(c, 5)

        block = asyncio.run(turn())
        gem = _ops(block)[("answer", "gemini-3.1-flash-lite")]
        self.assertEqual(gem["provider"], "google")
        self.assertIsNone(gem["estimated_cost_usd"])
        self.assertFalse(block["cost_complete"])
        self.assertEqual(block["unpriced_models"], ["google/gemini-3.1-flash-lite"])
        nano = pricing.estimate_cost("openai", "gpt-5.4-nano", 100, 10)
        self.assertAlmostEqual(block["estimated_cost_usd"], nano, places=8)
        line = {(l["provider"], l["model"]): l for l in cost_breakdown_by_model(block)}
        self.assertIsNone(line[("google", "gemini-3.1-flash-lite")]["cost_usd"])

    def test_tags_and_run_name_win_over_the_context_marker(self):
        async def turn():
            with collect_run_usage() as c:
                with usage_operation("answer"):
                    await _model(inp=1, out=1).ainvoke("q", config={"run_name": "chart_render"})
                    await _model(inp=2, out=1).ainvoke("q", config={"tags": ["di_op:follow_up"]})
            return build_usage_block(c, 1)

        ops = {r["operation"] for r in asyncio.run(turn())["operations"]}
        self.assertEqual(ops, {"chart_render", "follow_up"})

    def test_chain_run_name_reaches_the_inner_call_through_its_tags(self):
        """chat/local_chart_renderer.py names a CHAIN run "chart_render" with a
        matching tag; the chat-model call inside is a child run named after its
        class, so attribution must come through the inherited tag/metadata."""
        from langchain_core.prompts import ChatPromptTemplate

        async def turn():
            with collect_run_usage() as c:
                with usage_operation("answer"):
                    chain = ChatPromptTemplate.from_messages([("human", "{q}")]) | _model(inp=3, out=1)
                    await chain.ainvoke({"q": "x"}, config={
                        "run_name": "chart_render", "tags": ["chart_render"],
                        "metadata": {"operation": "chart_render"}})
            return build_usage_block(c, 1)

        self.assertEqual([r["operation"] for r in asyncio.run(turn())["operations"]], ["chart_render"])

    def test_embeddings_are_their_own_row_and_priced(self):
        async def turn():
            with collect_run_usage() as c:
                record_embedding("openai", "text-embedding-3-small", 1_000_000, 40)
            return build_usage_block(c, 50)

        block = asyncio.run(turn())
        emb = block["operations"][0]
        self.assertEqual(emb["operation"], "embeddings")
        self.assertAlmostEqual(emb["estimated_cost_usd"], 0.02, places=8)
        self.assertEqual(block["input_tokens"], 0)  # LLM totals exclude embeddings
        self.assertAlmostEqual(block["estimated_cost_usd"], 0.02, places=8)

    def test_outside_a_turn_nothing_is_collected(self):
        record_embedding("openai", "text-embedding-3-small", 10, 1)  # must not raise
        asyncio.run(_model().ainvoke("q"))  # no collector: no error


class HealthEventAgreesWithApiTests(unittest.TestCase):
    """Analytics and the API must report the same tokens and cost for a turn."""

    def test_health_event_totals_come_from_the_usage_block(self):
        import uuid

        from chat.db.health_events import build_health_event

        async def turn():
            with collect_run_usage() as c:
                with usage_operation("retrieval_verifier"):  # never in usage_by_model
                    await _model("gpt-5.4-nano", inp=400, out=40).ainvoke("q")
                with usage_operation("answer"):
                    await _model("gpt-5.6-luna", inp=1000, out=100).ainvoke("q")
            return build_usage_block(c, 900)

        block = asyncio.run(turn())
        row = build_health_event(
            knowledge_id="kb", owner_id=None, user_id=None, session_id=None,
            chat_id=str(uuid.uuid4()), query="q", latency_ms=900, key_type="master_key",
            final_state={"usage": block, "is_answered": "yes",
                         # The partial node-reported view the row used to be built from.
                         "usage_by_model": [{"provider": "openai", "model": "gpt-5.6-luna",
                                             "input_tokens": 1000, "output_tokens": 100}]},
        )
        self.assertEqual(row["input_tokens"], block["input_tokens"])
        self.assertEqual(row["output_tokens"], block["output_tokens"])
        self.assertEqual(row["input_tokens"], 1400)  # verifier included
        self.assertAlmostEqual(row["cost_usd"], block["estimated_cost_usd"], places=8)
        self.assertEqual(row["model"], "gpt-5.6-luna")
        self.assertEqual({l["model"] for l in row["cost_breakdown"]}, {"gpt-5.4-nano", "gpt-5.6-luna"})


class PricingTests(unittest.TestCase):
    def test_verified_2026_10_26_rates(self):
        per_m = lambda model, **kw: pricing.estimate_cost("openai", model, kw.get("i", 0), kw.get("o", 0),
                                                          kw.get("c", 0), kw.get("w", 0))
        M = 1_000_000
        self.assertAlmostEqual(per_m("gpt-5.6-luna", i=M), 0.20)
        self.assertAlmostEqual(per_m("gpt-5.6-luna", o=M), 1.20)
        self.assertAlmostEqual(per_m("gpt-5.6-terra", i=M), 2.00)
        self.assertAlmostEqual(per_m("gpt-5.6-terra", o=M), 12.00)
        self.assertAlmostEqual(per_m("gpt-5.4-nano", i=M, o=M), 0.20 + 1.25)
        self.assertAlmostEqual(per_m("gpt-5.4-mini", i=M, o=M), 0.75 + 4.50)
        self.assertAlmostEqual(per_m("text-embedding-3-small", i=M), 0.02)
        self.assertEqual(pricing.PRICE_TABLE_VERSION, "openai-standard-2026-10-26")

    def test_cached_and_cache_write_tokens(self):
        M = 1_000_000
        # luna: 1M input of which 600k cache read + 100k cache write, 300k fresh.
        cost = pricing.estimate_cost("openai", "gpt-5.6-luna", M, 0, 600_000, 100_000)
        self.assertAlmostEqual(cost, 0.3 * 0.20 + 0.6 * 0.02 + 0.1 * 0.25, places=8)
        # nano has no cache-write tier: a write prices at the input rate.
        cost = pricing.estimate_cost("openai", "gpt-5.4-nano", M, 0, 500_000, 0)
        self.assertAlmostEqual(cost, 0.5 * 0.20 + 0.5 * 0.02, places=8)

    def test_price_operations_cached_tokens_reach_the_cost(self):
        rows = price_operations([{"operation": "answer", "provider": "openai", "model": "gpt-5.4-mini",
                                  "input_tokens": 1_000_000, "cached_input_tokens": 1_000_000,
                                  "output_tokens": 0, "calls": 1}])
        self.assertAlmostEqual(rows["operations"][0]["estimated_cost_usd"], 0.075, places=8)


if __name__ == "__main__":
    unittest.main()
