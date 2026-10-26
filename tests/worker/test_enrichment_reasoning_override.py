"""ENRICHMENT_REASONING_EFFORT overrides the registry's reasoning effort (model evaluation only).

Unset, every model keeps its registry value (gpt-5.4-nano reasons at "medium", the
production fallback since 2026-10-25). Set, the value reaches the OpenAI client the
worker builds, so evals/model_eval can measure nano at "low" through the real worker.

unittest style so CI runs it in the worker image.
"""
import os
import unittest
from unittest.mock import patch

from celery_utils.enrichment import llm_factory


class ReasoningOverrideTests(unittest.TestCase):
    def test_unset_keeps_registry_values(self):
        with patch.dict(os.environ, {"ENRICHMENT_REASONING_EFFORT": ""}):
            self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.4-nano"), "medium")
            self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.6-luna"), "none")

    def test_override_replaces_registry_value(self):
        with patch.dict(os.environ, {"ENRICHMENT_REASONING_EFFORT": "Low"}):
            self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.4-nano"), "low")
            self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.6-luna"), "low")

    def test_override_reaches_the_openai_client(self):
        with patch.dict(os.environ, {"ENRICHMENT_REASONING_EFFORT": "low"}), \
                patch.object(llm_factory, "OPENAI_API_KEY", "sk-offline-test"):
            llm, _ =llm_factory.get_enrichment_llm(None, "openai", "gpt-5.4-nano", force_platform_key=True)
        self.assertEqual(llm.reasoning, {"effort": "low"})
        self.assertEqual(llm.model_name, "gpt-5.4-nano")

    def test_unknown_value_is_rejected(self):
        with patch.dict(os.environ, {"ENRICHMENT_REASONING_EFFORT": "maximum"}):
            with self.assertRaises(ValueError):
                llm_factory._get_reasoning_effort("gpt-5.4-nano")


if __name__ == "__main__":
    unittest.main()
