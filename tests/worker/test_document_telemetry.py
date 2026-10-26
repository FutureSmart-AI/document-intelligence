"""Per-document processing telemetry assembled by the worker (Contract 2).

celery_utils/shared/document_telemetry.py collects the parse step, every
enrichment attempt (attributed to the model that SERVED it, fallback visible),
embeddings and the total, and mark_task_completed / update_processing_status
write it to document.processing_telemetry. unittest style so CI runs it in the
worker image; the DB session is faked, no database is touched.
"""
import json
import unittest
from unittest.mock import patch

import celery_utils.db.db_utils as db_utils
from celery_utils.shared import document_telemetry as dt


class AssemblyTests(unittest.TestCase):
    def test_parse_extraction_fallback_embeddings_total(self):
        dt.start("doc-1")
        dt.record_parse("doc-1", parser="local_text", mode="text_based", ms=420,
                        fallback_reason=None, pages=None)
        # Primary (gemini) failed with 402, nano served the call.
        dt.record_extraction("doc-1", status="success", duration_ms=7300,
                             requested_provider="google", requested_model="gemini-3.1-flash-lite",
                             exec_meta={"provider": "openai", "model": "gpt-5.4-nano", "used_fallback": True,
                                        "fallback_reason": "primary_failed: 402 Payment Required", "attempts": 1},
                             token_usage={"input_tokens": 12000, "output_tokens": 900, "cached_input_tokens": 2000})
        dt.record_embedding("doc-1", provider="openai", model="text-embedding-3-small",
                            input_tokens=5000, duration_ms=600)
        record = dt.finish("doc-1", status="completed", total_ms=9100)

        self.assertEqual(record["total_ms"], 9100)
        self.assertEqual(record["parse"], {"parser": "local_text", "mode": "text_based", "ms": 420})
        ext, emb = record["operations"]
        self.assertEqual((ext["operation"], ext["provider"], ext["model"]), ("extraction", "openai", "gpt-5.4-nano"))
        self.assertTrue(ext["fallback"])
        self.assertEqual(ext["requested_model"], "gemini-3.1-flash-lite")
        self.assertEqual(ext["requested_provider"], "google")
        self.assertIn("402", ext["fallback_reason"])
        self.assertEqual((ext["calls"], ext["input_tokens"], ext["cached_input_tokens"], ext["output_tokens"]),
                         (1, 12000, 2000, 900))
        self.assertNotIn("estimated_cost_usd", ext)  # the API prices it, never the worker
        self.assertEqual((emb["operation"], emb["input_tokens"], emb["latency_ms"]), ("embeddings", 5000, 600))
        json.dumps(record)  # must be storable as JSONB
        self.assertIsNone(dt.finish("doc-1", status="completed"))  # closed and removed

    def test_failed_attempt_and_child_rollup(self):
        dt.start("root")
        dt.record_extraction("root", status="error", duration_ms=100, requested_provider="openai",
                             requested_model="gpt-5.4-nano",
                             exec_meta={"provider": "openai", "model": "gpt-5.4-nano", "used_fallback": False,
                                        "attempts": 2, "failure_category": "error"},
                             token_usage={"input_tokens": 0, "output_tokens": 0})
        # A crawled child page that was never started rolls up into the root.
        dt.record_embedding("child-page", provider="openai", model="text-embedding-3-small",
                            input_tokens=10, duration_ms=5)
        record = dt.finish("root", status="failed")
        ext = record["operations"][0]
        self.assertEqual((ext["calls"], ext["failed_calls"], ext["last_failure"]), (0, 2, "error"))
        self.assertEqual(record["operations"][1]["input_tokens"], 10)
        self.assertEqual(record["status"], "failed")

    def test_records_without_a_started_document_are_ignored(self):
        dt.record_parse("nobody", parser="x", mode="y", ms=1)  # must not raise
        self.assertIsNone(dt.finish("nobody", status="completed"))


class _Doc:
    processing_status = None
    task_duration_ms = None
    completed_at = None
    document_processing_mode = None


class _Session:
    def __init__(self):
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), params))

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PersistTests(unittest.TestCase):
    def test_mark_task_completed_writes_the_record(self):
        session = _Session()
        dt.start("doc-9")
        dt.record_parse("doc-9", parser="llamaparse", mode="layout_aware", ms=2000)
        with patch.object(db_utils, "get_session", lambda: session), \
                patch.object(db_utils, "_get_document", lambda s, k, o: _Doc()):
            db_utils.mark_task_completed("kid", "doc-9", processing_start_time=0)
        sql, params = session.executed[-1]
        self.assertIn("UPDATE document SET processing_telemetry", sql)
        stored = json.loads(params["telemetry"])
        self.assertEqual(stored["parse"]["parser"], "llamaparse")
        self.assertEqual(stored["status"], "completed")
        self.assertEqual((params["knowledge_id"], params["object_id"]), ("kid", "doc-9"))

    def test_a_missing_column_never_fails_the_status_write(self):
        class Broken(_Session):
            def execute(self, statement, params=None):
                raise RuntimeError('column "processing_telemetry" does not exist')

        self.assertFalse(_save_with(Broken()))  # logged, not raised


def _save_with(session):
    with patch.object(db_utils, "get_session", lambda: session):
        return db_utils.save_processing_telemetry("k", "o", {"total_ms": 1})


if __name__ == "__main__":
    unittest.main()
