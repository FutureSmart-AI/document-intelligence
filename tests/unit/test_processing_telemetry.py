"""Per-document processing telemetry (Contract 2): worker record -> priced API view.

The worker (workers/celery_utils/shared/document_telemetry.py) stores an
unpriced record; the API prices it on read with chat/pricing.py. These tests pin
the pricing/assembly rules: fallback stays visible, cached tokens are discounted,
an unpriced model is null (never $0) and makes the total incomplete.
"""
import unittest

from chat import pricing
from utils.processing_telemetry import price_processing_telemetry

RAW = {
    "version": 1,
    "status": "completed",
    "total_ms": 9100,
    "parse": {"parser": "local_text", "mode": "text_based", "ms": 420},
    "operations": [
        {"operation": "extraction", "provider": "openai", "model": "gpt-5.4-nano",
         "requested_provider": "google", "requested_model": "gemini-3.1-flash-lite",
         "fallback": True, "fallback_reason": "primary_failed: 402",
         "calls": 1, "input_tokens": 12000, "cached_input_tokens": 2000, "output_tokens": 900,
         "latency_ms": 7300},
        {"operation": "embeddings", "provider": "openai", "model": "text-embedding-3-small",
         "requested_model": "text-embedding-3-small", "fallback": False,
         "calls": 1, "input_tokens": 5000, "cached_input_tokens": 0, "output_tokens": 0, "latency_ms": 600},
    ],
}


class PriceProcessingTelemetryTests(unittest.TestCase):
    def test_priced_view(self):
        view = price_processing_telemetry(RAW)
        self.assertEqual(view["total_ms"], 9100)
        self.assertEqual(view["parse"]["parser"], "local_text")
        ext, emb = view["operations"]
        self.assertTrue(ext["fallback"])
        self.assertEqual(ext["requested_model"], "gemini-3.1-flash-lite")
        self.assertEqual(ext["model"], "gpt-5.4-nano")
        expect_ext = pricing.estimate_cost("openai", "gpt-5.4-nano", 12000, 900, 2000, 0)
        self.assertAlmostEqual(ext["estimated_cost_usd"], expect_ext, places=8)
        self.assertAlmostEqual(emb["estimated_cost_usd"], 5000 / 1e6 * 0.02, places=8)
        self.assertAlmostEqual(view["estimated_cost_usd"], expect_ext + 5000 / 1e6 * 0.02, places=6)
        self.assertTrue(view["cost_complete"])
        self.assertEqual(view["unpriced_models"], [])
        self.assertEqual(view["price_table"], pricing.PRICE_TABLE_VERSION)

    def test_unpriced_model_is_null_and_incomplete(self):
        raw = {"total_ms": 10, "operations": [
            {"operation": "extraction", "provider": "google", "model": "gemini-3.1-flash-lite",
             "calls": 1, "input_tokens": 100, "output_tokens": 10, "latency_ms": 5}]}
        view = price_processing_telemetry(raw)
        self.assertIsNone(view["operations"][0]["estimated_cost_usd"])
        self.assertFalse(view["cost_complete"])
        self.assertEqual(view["unpriced_models"], ["google/gemini-3.1-flash-lite"])
        self.assertIsNone(view["estimated_cost_usd"])  # nothing priced: unknown, not $0
        self.assertFalse(view["operations"][0]["fallback"])

    def test_no_record(self):
        self.assertIsNone(price_processing_telemetry(None))
        self.assertIsNone(price_processing_telemetry({}))


if __name__ == "__main__":
    unittest.main()
