"""LlamaParse modes on an install with no LlamaParse key (2026-10-26).

* An explicit parser mode fails with a message naming the missing key, before any
  request — not LlamaParse's bare 401 on the document row.
* An auto-detected shift (scanned / table-heavy content) that cannot run keeps the
  local text AND records why on the extraction result (parse_fallback_reason,
  mode_auto_shift_target), which the document's processing telemetry carries.
"""
import unittest
from io import BytesIO
from unittest.mock import patch

from celery_utils.processing import file_utils
from celery_utils.processing.processing_modes import ProcessingMode


class ParserFallbackTests(unittest.TestCase):
    @patch.object(file_utils, "get_api_key", return_value=("", "platform-key"))
    def test_explicit_mode_without_key_names_the_missing_key(self, _key):
        with self.assertRaises(file_utils.ParserNotConfiguredError) as err:
            file_utils.read_with_llama_parse(BytesIO(b"%PDF"), "scan.pdf", owner_id=None,
                                             mode=ProcessingMode.SCANNED)
        self.assertIn("LLAMA_PARSE_API_KEY", str(err.exception))
        self.assertIn("'scanned'", str(err.exception))

    @patch.object(file_utils, "add_step_metadata")
    @patch.object(file_utils, "classify_pdf_target_mode", return_value=(ProcessingMode.SCANNED, "3 of 3 pages image-only"))
    @patch.object(file_utils, "read_pdf_to_plain_text", return_value={"content": "stray header", "page_signals": [{}]})
    @patch.object(file_utils, "get_api_key", return_value=("", "platform-key"))
    def test_auto_shift_without_key_keeps_local_text_and_says_why(self, *_mocks):
        result = file_utils._read_pdf_with_auto_shift(BytesIO(b"%PDF"), b"%PDF", "scan.pdf", ProcessingMode.AUTO)
        self.assertEqual(result["content"], "stray header")
        self.assertEqual(result["mode_auto_shift_target"], "scanned")
        self.assertIn("no LlamaParse API key", result["parse_fallback_reason"])
        self.assertIn("3 of 3 pages image-only", result["parse_fallback_reason"])
        self.assertIsNone(result.get("mode_auto_shifted_from"))

    def test_unpacked_fields_carry_the_reason(self):
        fields = file_utils._unpack_extracted_content({
            "content": "x", "parse_fallback_reason": "why", "mode_auto_shift_target": "layout_aware"})
        self.assertEqual((fields["parse_fallback_reason"], fields["mode_auto_shift_target"]), ("why", "layout_aware"))


if __name__ == "__main__":
    unittest.main()
