"""gpt-5.6-luna-low through the worker: registry -> client -> fallback -> records.

Production selects it by config row, so every worker path that receives a model NAME
must turn "gpt-5.6-luna-low" into the wire model gpt-5.6-luna at reasoning "low",
and every record of the call (exec_meta -> document telemetry, health and usage
events) must name the WIRE model, which is what the provider bills and every price
table is keyed on, with the configured name and effort beside it.

Plain gpt-5.6-luna must keep "none": it is also RICH_MODE_ENRICHMENT_MODEL.

unittest style so CI runs it in the worker image; no network, no database.
"""
import os
import unittest
from unittest.mock import patch

from celery_utils.enrichment import llm_factory
from celery_utils.enrichment.llm_retry import _exec_meta
from celery_utils.shared import document_telemetry as dt

NO_OVERRIDE = {"ENRICHMENT_REASONING_EFFORT": ""}


class VariantResolutionTests(unittest.TestCase):
    def test_provider_and_effort(self):
        with patch.dict(os.environ, NO_OVERRIDE):
            self.assertEqual(llm_factory.get_provider_from_model("gpt-5.6-luna-low"), "openai")
            self.assertEqual(llm_factory._api_model("gpt-5.6-luna-low"), "gpt-5.6-luna")
            self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.6-luna-low"), "low")
            self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.6-luna"), "none")

    def test_rich_mode_model_is_untouched(self):
        from celery_utils.processing.processing_modes import RICH_MODE_ENRICHMENT_MODEL

        self.assertEqual(RICH_MODE_ENRICHMENT_MODEL, "gpt-5.6-luna")
        with patch.dict(os.environ, NO_OVERRIDE):
            self.assertEqual(llm_factory._get_reasoning_effort(RICH_MODE_ENRICHMENT_MODEL), "none")

    def test_client_sends_the_wire_model_at_low(self):
        with patch.dict(os.environ, NO_OVERRIDE), \
                patch.object(llm_factory, "OPENAI_API_KEY", "sk-offline-test"):
            llm, key_type = llm_factory.get_enrichment_llm(None, "openai", "gpt-5.6-luna-low",
                                                           force_platform_key=True)
        self.assertEqual(llm.model_name, "gpt-5.6-luna")
        self.assertEqual(llm.reasoning, {"effort": "low"})
        self.assertEqual(key_type, "platform-key")

    def test_fallback_from_the_config_row(self):
        row = {"enrichment_fallback_model": "gpt-5.6-luna-low", "enrichment_fallback_provider": None}
        with patch.dict(os.environ, NO_OVERRIDE), \
                patch.object(llm_factory, "OPENAI_API_KEY", "sk-offline-test"), \
                patch("celery_utils.db.db_utils.get_enrichment_default_config_sync", return_value=row), \
                patch("celery_utils.enrichment.llm_factory.get_api_key",
                      return_value=("sk-offline-test", "platform-key")):
            fb = llm_factory.get_fallback_enrichment_llm(None)
        self.assertEqual((fb.model_name, fb.provider), ("gpt-5.6-luna-low", "openai"))
        self.assertEqual(fb.llm.model_name, "gpt-5.6-luna")
        self.assertEqual(fb.llm.reasoning, {"effort": "low"})

    def test_primary_from_the_config_row(self):
        row = {"enrichment_model": "gpt-5.6-luna-low", "enrichment_provider": "openai",
               "enrichment_temperature": 0.0}
        with patch("celery_utils.db.db_utils.get_enrichment_default_config_sync", return_value=row):
            self.assertEqual(llm_factory.get_enrichment_config(None),
                             ("openai", "gpt-5.6-luna-low", 0.0))


class RecordTests(unittest.TestCase):
    def test_exec_meta_names_the_wire_model_and_the_effort(self):
        with patch.dict(os.environ, NO_OVERRIDE):
            meta = _exec_meta("gpt-5.6-luna-low", "openai", False, 1)
        self.assertEqual(meta["model"], "gpt-5.6-luna")
        self.assertEqual(meta["configured_model"], "gpt-5.6-luna-low")
        self.assertEqual(meta["reasoning_effort"], "low")
        # A base model has no configured_model; its effort is still recorded.
        with patch.dict(os.environ, NO_OVERRIDE):
            meta = _exec_meta("gpt-5.4-nano", "openai", True, 1)
        self.assertEqual((meta["model"], meta["reasoning_effort"]), ("gpt-5.4-nano", "medium"))
        self.assertNotIn("configured_model", meta)
        # A non-reasoning model carries no effort.
        self.assertNotIn("reasoning_effort", _exec_meta("gemini-3.1-flash-lite", "google", False, 1))

    def test_document_telemetry_records_wire_model_requested_variant_and_effort(self):
        with patch.dict(os.environ, NO_OVERRIDE):
            meta = _exec_meta("gpt-5.6-luna-low", "openai", False, 1)
        dt.start("doc-variant")
        dt.record_extraction("doc-variant", status="success", duration_ms=5700,
                             requested_provider="openai", requested_model="gpt-5.6-luna-low",
                             exec_meta=meta, token_usage={"input_tokens": 1000, "output_tokens": 200})
        record = dt.finish("doc-variant", status="completed", total_ms=6000)
        ext = record["operations"][0]
        self.assertEqual((ext["model"], ext["requested_model"], ext["reasoning_effort"], ext["fallback"]),
                         ("gpt-5.6-luna", "gpt-5.6-luna-low", "low", False))
        self.assertEqual(ext["configured_model"], "gpt-5.6-luna-low")


if __name__ == "__main__":
    unittest.main()
