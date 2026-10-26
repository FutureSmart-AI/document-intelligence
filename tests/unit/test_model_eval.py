"""Offline checks for evals/model_eval: grading, chart comparison, telemetry fallbacks, config
verification, dataset integrity and the report. No network, no model calls; stdlib only, so CI
runs it on the runner's own Python.

    python -m unittest discover -s tests/unit -p test_model_eval.py
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals/model_eval"))

import cases  # noqa: E402
import grade  # noqa: E402
import run  # noqa: E402
import usage  # noqa: E402


def passes(**verdicts):
    """Two judge passes with the given per-field verdicts (a tuple gives pass 1 / pass 2)."""
    out = []
    for i in range(2):
        out.append(SimpleNamespace(fields=[SimpleNamespace(field=f, verdict=(v[i] if isinstance(v, tuple) else v),
                                                           reason=f"{f} reason")
                                           for f, v in verdicts.items()]))
    return out


INVOICE_SPEC = {"exact": {"invoice_number": "code", "currency": "code", "total_amount": "amount",
                          "issue_date": "date", "due_date": "date"},
                "judged": ["vendor_name", "payment_terms"]}
INVOICE = {"id": "i01", "file": "pdf/x.pdf", "layout": "band",
           "fields": {"invoice_number": "BCH-2026-0418", "currency": "EUR", "total_amount": 3237.0,
                      "issue_date": "2026-04-30", "due_date": "2026-05-30", "vendor_name": "Bramblecloud Hosting Ltd.",
                      "payment_terms": "net 30"}}


class ExactFields(unittest.TestCase):
    def test_kinds(self):
        self.assertTrue(grade.exact_ok("email", "a@b.test", " A@B.test "))
        self.assertTrue(grade.exact_ok("integer", 16, 16.0))
        self.assertFalse(grade.exact_ok("integer", 16, 14))
        self.assertTrue(grade.exact_ok("integer", 10, 3, alternatives=[3]))
        self.assertFalse(grade.exact_ok("integer", 10, 10.5))
        self.assertTrue(grade.exact_ok("amount", 3237.0, "3,237.00"))
        self.assertFalse(grade.exact_ok("amount", 3237.0, 3237.1))
        self.assertTrue(grade.exact_ok("date", "2026-05-30", "2026-05-30"))
        self.assertFalse(grade.exact_ok("date", "2026-05-30", "30 May 2026"))
        self.assertTrue(grade.exact_ok("code", "EUR", "eur"))
        self.assertFalse(grade.exact_ok("enum", "senior", "Senior"))
        self.assertFalse(grade.exact_ok("integer", 16, None))


class DocumentGrade(unittest.TestCase):
    def test_counts_status_and_completeness(self):
        got = {"invoice_number": "BCH-2026-0418", "currency": "EUR", "total_amount": 3237.0, "issue_date": None,
               "due_date": "2026-06-30", "vendor_name": "Bramblecloud Hosting Ltd", "payment_terms": "Net 30"}
        g = grade.grade_document(INVOICE_SPEC, INVOICE, got, passes(vendor_name="correct", payment_terms=("correct", "partial")))
        f = g["fields"]
        self.assertEqual(g["fields_total"], 7)
        self.assertEqual(f["issue_date"]["status"], "missing")
        self.assertEqual(f["due_date"]["status"], "wrong")
        self.assertTrue(f["vendor_name"]["correct"])
        # both passes must say correct
        self.assertFalse(f["payment_terms"]["correct"])
        self.assertFalse(f["payment_terms"]["agree"])
        self.assertEqual(g["fields_correct"], 4)
        self.assertFalse(g["complete"])
        self.assertEqual((g["required_fields"], g["required_filled"]), (7, 6))

    def test_unexpected_value_when_truth_is_empty(self):
        rec = {**INVOICE, "fields": {**INVOICE["fields"], "due_date": None}}
        got = dict(INVOICE["fields"], due_date="2026-05-30")
        g = grade.grade_document(INVOICE_SPEC, rec, got, passes(vendor_name="correct", payment_terms="correct"))
        self.assertEqual(g["fields"]["due_date"]["status"], "unexpected")
        self.assertTrue(g["complete"])

    def test_nested_metadata_is_found(self):
        meta = {"object_id": "x", "metadata": {"currency": "EUR", "line_items": [{"amount": 1}]}}
        self.assertEqual(grade.find_fields(meta, ["currency", "vendor_name"]), meta["metadata"])


class JudgeSchema(unittest.TestCase):
    @unittest.skipUnless(__import__("importlib").util.find_spec("pydantic") and __import__("importlib").util.find_spec("openai"),
                         "needs pydantic + openai")
    def test_score_py_schema_resolves_when_loaded_by_path(self):
        # score.py uses `from __future__ import annotations`; loaded by path, its forward references
        # resolve only if the module is registered in sys.modules (the first workflow run failed here).
        schema = grade.resume_score().ResumeJudgement
        parsed = schema.model_validate({"fields": [{"field": "skills", "expected_items": 2, "extracted_items": 2,
                                                    "matched_items": 2, "verdict": "correct", "reason": "same"}]})
        self.assertEqual(parsed.fields[0].verdict, "correct")


class Charts(unittest.TestCase):
    def test_exact_and_regrouped(self):
        self.assertTrue(grade.compare_chart({"bachelors": 15, "masters": 8, "phd": 1},
                                            {"Bachelors": 15, "masters": 8.0, "phd": 1})["exact"])
        r = grade.compare_chart({"2": 2, "3": 1, "8": 4}, {"2-3": 3, "4+": 4})
        self.assertTrue(r["exact"], r)

    def test_wrong_extra_and_missing_chart(self):
        r = grade.compare_chart({"a": 2, "b": 1}, {"a": 2, "b": 2, "c": 1})
        self.assertFalse(r["exact"])
        self.assertIn("b", r["wrong"])
        self.assertEqual(r["extra"], ["c"])
        self.assertFalse(grade.compare_chart({"a": 1}, None)["exact"])

    def test_follow_up_population_per_person_and_distribution(self):
        people = {"Ann": 6.0, "Bo": 8.0, "Cy": 8.0}
        self.assertTrue(grade.compare_population_chart(people, {"Ann": 6, "Bo": 8, "Cy": 8})["exact"])
        dist = grade.compare_population_chart(people, {"6": 1, "8": 2})
        self.assertTrue(dist["exact"], dist)
        self.assertEqual(dist["reading"], "distribution")
        # the regression: only the "5+ years" filter kept, so the chart covers more people
        wide = grade.compare_population_chart(people, {"6": 1, "8": 2, "12": 3})
        self.assertFalse(wide["exact"])
        self.assertEqual(wide["chart_population"], 6)
        self.assertFalse(grade.compare_population_chart(people, {"Ann": 6, "Bo": 8, "Cy": 8, "Dee": 12})["exact"])

    def test_answer_right_about_the_documents_is_not_a_qa_failure(self):
        calls = []

        def fake(client, case, answer):
            calls.append(case["expected"])
            ok = case["expected"] == answer
            return {"verdicts": ["pass" if ok else "fail"] * 2, "agree": True, "pass": ok, "reason": "r",
                    "missing": [], "wrong": []}

        original = grade.judge_answer
        grade.judge_answer = fake
        try:
            case = {"question": "q", "kind": "number", "expected": 0}
            self.assertEqual(grade.judge_either(None, case, 4, truth=4)["graded_against"], "truth")
            self.assertEqual(grade.judge_either(None, case, 0, truth=4)["graded_against"], "extracted")
            self.assertFalse(grade.judge_either(None, case, 7, truth=4)["pass"])
            calls.clear()
            grade.judge_either(None, case, 7, truth=None)
            self.assertEqual(calls, [0], "no second judgement when the truth agrees or is absent")
        finally:
            grade.judge_answer = original

    def test_no_chart_population_result_is_complete(self):
        r = grade.compare_population_chart({"Ann": 6.0}, None)
        self.assertEqual((r["exact"], r["expected_population"]), (False, 1))

    def test_chart_rows_from_response(self):
        body = {"data": {"knowledge": {"chart_output": {"chart_data": {"data": [{"category": "x", "value": "2"}]}}}}}
        self.assertEqual(grade.chart_rows(body), {"x": 2.0})
        self.assertIsNone(grade.chart_rows({"data": {"knowledge": {"chart_output": None}}}))

    def test_sources(self):
        self.assertTrue(grade.sources_ok(["r03-daniel-okafor.pdf"], ["R03-Daniel-Okafor.pdf", "x.pdf"]))
        self.assertFalse(grade.sources_ok(["r03-daniel-okafor.pdf"], ["x.pdf"]))
        self.assertIsNone(grade.sources_ok(None, []))


class Dataset(unittest.TestCase):
    def test_fixtures_and_computed_expectations_agree(self):
        ds = run.load_dataset()
        self.assertEqual(run.check_dataset(ds), [])
        self.assertEqual(len(ds["collections"]["resumes"]["records"]), 24)
        self.assertEqual(len(ds["collections"]["invoices"]["records"]), 18)
        self.assertEqual(len(ds["sha256"]), 64)

    def test_follow_up_population_on_truth_is_eight(self):
        ds = run.load_dataset()
        built = {c["id"]: c for c in cases.build_cases(run.truth_tables(ds),
                                                       {n: c["records"] for n, c in ds["collections"].items()},
                                                       cases.load_invoice_questions())}
        fu = built["fu_python_aws_years_chart"]
        self.assertEqual(len(fu["expected_population"]), 8)
        self.assertEqual(fu["turns"][1], "chart their years")
        self.assertEqual(built["p01"]["expected_sources"], ["r03-daniel-okafor.pdf"])

    def test_truth_differs_flags_extraction_errors(self):
        ds = run.load_dataset()
        tables = run.truth_tables(ds)
        tables["resumes"]["extracted"] = [dict(d) for d in tables["resumes"]["extracted"]]
        tables["resumes"]["extracted"][0]["years_of_experience"] = 1  # an extraction miss
        built = {c["id"]: c for c in cases.build_cases(tables, {n: c["records"] for n, c in ds["collections"].items()},
                                                       cases.load_invoice_questions())}
        self.assertIn("truth_differs", built["n03"])
        self.assertNotIn("truth_differs", built["f01"])

    def test_sha_ignores_line_endings_of_text_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
            a.write_bytes(b'{"x": 1}\r\n')
            b.write_bytes(b'{"x": 1}\n')
            self.assertEqual(run._bytes(a), run._bytes(b))

    def test_configs_resolve_with_allowed_models_only(self):
        allowed = {"gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.6-luna", "gpt-5.6-terra", "text-embedding-3-small"}
        names = sorted(p.stem for p in (ROOT / "evals/model_eval/configs").glob("*.json"))
        self.assertIn("baseline-nano-medium", names)
        # An extraction registry variant ("gpt-5.6-luna-low") is allowed when its WIRE model
        # is: the API's registry mirror (stdlib only) says what it sends.
        spec = importlib.util.spec_from_file_location(
            "di_model_registry", ROOT / "backend" / "utils" / "enrichment" / "model_registry.py")
        registry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(registry)
        for name in names:
            config = run.load_config(name)
            models = run.expected_models(config)
            extraction = models["extraction"]
            if extraction:
                self.assertIsNotNone(registry.get(extraction), f"{name}: {extraction} is not a registry model")
            self.assertLessEqual(models["any"] | {registry.wire_model(extraction)}, allowed, name)
        base = run.expected_models(run.load_config("baseline-nano-medium"))
        self.assertEqual((base["extraction"], base["by_operation"]["answer"], base["by_operation"]["retrieval_agent"],
                          base["chart"]), ("gpt-5.4-nano", "gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.4-mini"))
        # chat-only candidates share the baseline's extraction, so their RAG suite can reuse its collections
        self.assertEqual(run.extraction_identity(run.load_config("chat-nano")),
                         run.extraction_identity(run.load_config("baseline-nano-medium")))


class Telemetry(unittest.TestCase):
    MODELS = run.expected_models(run.load_config("baseline-nano-medium"))

    def test_contract_usage_is_preferred(self):
        u = usage.from_chat_usage({"latency_ms": 8120, "estimated_cost_usd": 0.0031, "cost_complete": True,
                                   "operations": [{"operation": "answer", "model": "gpt-5.6-luna", "fallback": False},
                                                  {"operation": "query_refinement", "model": "gpt-5.4-nano"}]})
        self.assertEqual((u["source"], u["server_latency_ms"], u["estimated_cost_usd"]), ("api", 8120, 0.0031))
        self.assertTrue(usage.chat_config_verified(u, self.MODELS)["verified"])
        self.assertIsNone(usage.from_chat_usage({"input_tokens": 3, "service_calls": []}))

    def test_wrong_operation_model_and_fallback_fail_verification(self):
        u = usage.from_chat_usage({"operations": [{"operation": "answer", "model": "gpt-5.4-mini"}]})
        check = usage.chat_config_verified(u, self.MODELS)
        self.assertFalse(check["verified"])
        u = usage.from_chat_usage({"operations": [{"operation": "answer", "model": "gpt-5.6-luna", "fallback": True}]})
        self.assertFalse(usage.chat_config_verified(u, self.MODELS)["verified"])

    def test_trace_fallback_prices_tokens(self):
        runs = [{"name": "ChatOpenAI", "model": "gpt-5.6-luna", "input_tokens": 1_000_000, "cached_input_tokens": 0,
                 "output_tokens": 0},
                {"name": "ChatOpenAI", "model": "gpt-5.4-nano", "input_tokens": 0, "output_tokens": 1_000_000}]
        u = usage.from_llm_runs(runs)
        self.assertEqual(u["source"], "langsmith")
        self.assertAlmostEqual(u["estimated_cost_usd"], 0.20 + 1.25)
        self.assertTrue(u["cost_complete"])
        self.assertTrue(usage.chat_config_verified(u, self.MODELS)["verified"])
        outside = usage.from_llm_runs([{"name": "x", "model": "gpt-4o-mini", "input_tokens": 1, "output_tokens": 1}])
        self.assertFalse(outside["cost_complete"])
        self.assertFalse(usage.chat_config_verified(outside, self.MODELS)["verified"])

    def test_cached_input_priced_at_cached_rate(self):
        self.assertAlmostEqual(usage.price("openai/gpt-5.4-nano", 1_000_000, 0, cached=1_000_000), 0.02)
        self.assertIsNone(usage.price("google/gemini-3.1-flash-lite", 10, 10))

    def test_registry_variant_config_is_verified_against_its_wire_model(self):
        # profile-luna-low selects "gpt-5.6-luna-low" by name, as production does; the API
        # records the wire model it billed plus the configured name and the effort.
        t = usage.from_processing_telemetry({"total_ms": 6000, "estimated_cost_usd": 0.0005, "cost_complete": True,
                                             "operations": [{"operation": "extraction", "model": "gpt-5.6-luna",
                                                             "configured_model": "gpt-5.6-luna-low",
                                                             "requested_model": "gpt-5.6-luna-low",
                                                             "reasoning_effort": "low", "fallback": False}]})
        self.assertEqual(t["extraction_models"], ["gpt-5.6-luna"])
        self.assertEqual(t["reasoning_efforts"], ["low"])
        self.assertTrue(usage.extraction_config_verified(t, "gpt-5.6-luna-low")["verified"])
        self.assertFalse(usage.extraction_config_verified(t, "gpt-5.6-luna")["verified"])
        h = usage.from_enrichment_health([
            {"created_at": "1", "status": "success", "model": "gpt-5.6-luna", "requested_model": "gpt-5.6-luna-low",
             "used_fallback": False, "input_tokens": 1000, "output_tokens": 200, "duration_ms": 5000}])
        self.assertTrue(usage.extraction_config_verified(h, "gpt-5.6-luna-low")["verified"])
        self.assertIsNotNone(usage.price("gpt-5.6-luna-low", 1000, 200))

    def test_document_telemetry_and_extraction_verification(self):
        t = usage.from_processing_telemetry({"total_ms": 9000, "estimated_cost_usd": 0.001, "cost_complete": True,
                                             "operations": [{"operation": "extraction", "model": "gpt-5.4-nano",
                                                             "fallback": False, "latency_ms": 7000},
                                                            {"operation": "embeddings", "model": "text-embedding-3-small"}]})
        self.assertTrue(usage.extraction_config_verified(t, "gpt-5.4-nano")["verified"])
        self.assertFalse(usage.extraction_config_verified(t, "gpt-5.6-luna")["verified"])
        h = usage.from_enrichment_health([
            {"created_at": "1", "status": "error", "model": "google/gemini-3.1-flash-lite", "used_fallback": False,
             "input_tokens": 0, "output_tokens": 0, "duration_ms": 300},
            {"created_at": "2", "status": "success", "model": "gpt-5.4-nano", "requested_model": "gemini-3.1-flash-lite",
             "used_fallback": True, "fallback_reason": "402", "input_tokens": 1000, "output_tokens": 800, "duration_ms": 9000}])
        self.assertFalse(h["cost_complete"])
        self.assertEqual(h["extraction_ms"], 9300)
        check = usage.extraction_config_verified(h, "gpt-5.4-nano")
        self.assertFalse(check["verified"], "a silent fallback must fail the run")
        self.assertFalse(usage.extraction_config_verified(None, "gpt-5.4-nano")["verified"])


class Robustness(unittest.TestCase):
    def test_trace_lookup_failure_never_raises(self):
        t = usage.LangSmithTraces(None, "p")
        t.enabled = True

        def boom(*a, **k):
            raise TimeoutError("read timed out")

        t._turn = boom
        got = t.turn("chat-1")
        self.assertEqual((got["trace_id"], got["runs"]), (None, []))
        self.assertIn("TimeoutError", got["error"])

    def test_failed_answers_are_not_config_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            path = out / "rag-x.jsonl"
            rows = [{"http_status": [None], "failure": "timeout", "config_verified": False, "correct": False},
                    {"http_status": [200], "config_verified": True, "correct": True}]
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            run.finish(out, path, {}, "x", "rag", 2)  # does not raise
            rows.append({"http_status": [200], "config_verified": False, "config_problems": ["served gpt-4o-mini"]})
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            with self.assertRaises(SystemExit):
                run.finish(out, path, {}, "x", "rag", 3)
            with self.assertRaises(SystemExit):
                run.finish(out, path, {}, "x", "rag", 4)  # a record is missing


class Report(unittest.TestCase):
    def test_report_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            field = {"correct": True, "status": "correct", "expected": 1, "extracted": 1}
            miss = {"correct": False, "status": "wrong", "expected": 16, "extracted": 14, "verdicts": ["wrong", "wrong"],
                    "agree": True}
            ext = [{"config": "baseline-nano-medium", "repeat": 1, "collection": "resumes", "doc_id": "r03", "status": "completed",
                    "fields": {"years_of_experience": miss, "skills": field}, "fields_correct": 1, "fields_total": 2,
                    "complete": True, "all_correct": False, "latency_ms": 9000, "estimated_cost_usd": 0.001,
                    "cost_complete": False, "cost_source": "enrichment_health", "config_verified": True,
                    "served_models": ["gpt-5.4-nano"]}]
            rag = [{"config": "baseline-nano-medium", "case_id": "fu_python_aws_years_chart", "category": "follow_up",
                    "correct": False, "failure": "follow_up_chart", "reason": "chart covered 14",
                    "chart": {"chart_population": 14, "expected_population": 8}, "client_latency_ms": 9000,
                    "server_latency_ms": None, "estimated_cost_usd": 0.004, "cost_complete": True, "cost_source": "langsmith",
                    "config_verified": True, "served_models": ["gpt-5.6-luna"]},
                   {"config": "baseline-nano-medium", "case_id": "f01", "category": "exact_filter", "correct": True,
                    "client_latency_ms": 5000, "server_latency_ms": None, "estimated_cost_usd": 0.002, "cost_complete": True,
                    "cost_source": "langsmith", "config_verified": True, "sources_ok": None, "served_models": []}]
            (out / "extraction-baseline-nano-medium.jsonl").write_text("\n".join(json.dumps(r) for r in ext) + "\n")
            (out / "rag-baseline-nano-medium.jsonl").write_text("\n".join(json.dumps(r) for r in rag) + "\n")
            s = run.report(out)
            e = s["configs"]["baseline-nano-medium"]
            self.assertEqual(e["extraction"]["summary"]["fields_correct"], 1)
            self.assertEqual(e["extraction"]["summary"]["numeric_exact"], "0/1")
            self.assertEqual(e["rag"]["summary"]["correct"], 1)
            self.assertEqual(e["rag"]["summary"]["latency_ms_p50"], 7000)
            self.assertEqual(e["rag"]["follow_up_regression"]["chart_population"], [14])
            md = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("| baseline-nano-medium |", md)
            self.assertIn("r03.years_of_experience", md)
            self.assertEqual(len(s["failures"]), 2)


if __name__ == "__main__":
    unittest.main()
