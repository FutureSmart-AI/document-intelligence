"""Extraction knows what date "now" is, and the fallback model reasons before it answers.

Both pin the fix for the sample resumes' years-of-experience misses (2026-10-25):
"2010 - Present" was extracted as 14 years and "2023 - Present" as 2, because the
enrichment prompt carried no current date and the gpt-5.4-nano fallback, with
reasoning off, did the date arithmetic in one shot. Measured on all 24 resumes in
backend/samples/resumes/EXTRACTION-YEARS-2026-10-25.md.

unittest style so CI runs it in the worker image; the admin prompt lookup is stubbed.
"""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from celery_utils.enrichment import llm_factory, prompts

ADMIN = [{"prompt_content": "Base enrichment prompt."}]


class ReferenceDateTests(unittest.TestCase):
    def build(self, **kw):
        with patch.object(prompts, "get_admin_prompts_sync", return_value=ADMIN):
            return prompts.create_enrichment_prompt(**kw)

    def test_every_prompt_path_carries_the_reference_date(self):
        for kw in ({"include_chunks": False}, {"include_chunks": True}, {"include_deterministic_chunks": True}):
            with self.subTest(**kw):
                prompt = self.build(reference_date=date(2026, 10, 25), **kw)
                self.assertIn("## Reference date", prompt)
                self.assertIn("Today is 2026-10-25", prompt)
                self.assertIn('"Present"', prompt)

    def test_default_reference_date_is_today_utc(self):
        today = datetime.now(timezone.utc).date().isoformat()
        self.assertIn(f"Today is {today}", self.build(include_chunks=False))

    def test_reference_date_survives_collection_instructions_and_consistency_values(self):
        prompt = self.build(include_chunks=False, knowledge_prompt="Do not infer missing facts.",
                            existing_field_values={"skills": ["python"]}, reference_date=date(2026, 10, 25))
        self.assertIn("Do not infer missing facts.", prompt)
        self.assertIn("## Consistency Guidelines", prompt)
        self.assertIn("Today is 2026-10-25", prompt)


class FallbackReasoningTests(unittest.TestCase):
    def test_fallback_model_reasons_before_answering(self):
        # "none" miscounted years on 4/72 sample extractions and "low" on 2/72; "medium" on 0/72.
        self.assertEqual(llm_factory._get_reasoning_effort("gpt-5.4-nano"), "medium")


if __name__ == "__main__":
    unittest.main()
