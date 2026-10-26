"""On-demand export of a completed report run to Markdown / JSON / CSV.

Ported from fs-agent-dashboard's `batch_query_reports/exporters.py` (see
docs/plan/batch-query-reports/schema.md). Exports are generated from
the persisted run (`utils/reports/db_utils.py::get_run_full`'s output) and
are NOT stored — generated fresh on every request (spec §4.5).

Trimmed vs. the donor: no SQL-agent `execution_details` rendering (this
service's chat graph is RAG-only) — see decisions.md.
"""
import csv
import io
import json
from typing import Any, Dict

# Leading characters that spreadsheet apps (Excel, Sheets, LibreOffice) treat as the start of a
# formula. A report's query/answer text is attacker-controlled by anyone with write access to the
# knowledge base, so it must not reach a CSV cell verbatim — see CWE-1236 (CSV injection).
_FORMULA_LEAD_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Neutralize a value that would otherwise be interpreted as a spreadsheet formula."""
    if value and value[0] in _FORMULA_LEAD_CHARS:
        return "'" + value
    return value


def to_json(run: Dict[str, Any]) -> str:
    return json.dumps(run, indent=2, ensure_ascii=False)


def to_markdown(run: Dict[str, Any]) -> str:
    s = run.get("summary", {})
    lines = [
        f"# {run.get('name') or 'Report Run'}",
        "",
        f"- **Run ID:** {run.get('run_id')}",
        f"- **Report ID:** {run.get('report_id')}",
        f"- **Knowledge base:** {run.get('knowledge_id')}",
        f"- **Status:** {run.get('status')}",
        f"- **Executed at:** {run.get('executed_at')}",
        "",
        "## Summary",
        "",
        f"- Total queries: {s.get('total_queries', 0)}",
        f"- Completed: {s.get('completed_queries', 0)}",
        f"- Failed: {s.get('failed_queries', 0)}",
        f"- Total time: {s.get('total_time_ms') if s.get('total_time_ms') is not None else '—'} ms",
        f"- Avg response time: {s.get('avg_response_time_ms', 0)} ms",
        f"- Total tokens: {s.get('total_tokens', 0)}",
    ]
    if run.get("overall_summary"):
        lines += ["", "## Overall Analysis", "", run["overall_summary"]]

    for i, group in enumerate(run.get("query_groups", []), start=1):
        lines += ["", f"## Group {i} — {group.get('type')}", ""]
        if group.get("type") == "sequential" and group.get("conversation_flow"):
            lines += ["### Conversation flow", ""]
            for turn in group["conversation_flow"]:
                lines += [f"**Q:** {turn.get('question')}", "", f"**A:** {turn.get('answer') or '_(no answer)_'}", ""]
        for r in group.get("results", []):
            lines += [f"### {r.get('question')}", ""]
            if r.get("error"):
                lines += [f"> Error: {r['error']}", ""]
            else:
                lines += [r.get("answer") or "_(no answer)_", ""]
            srcs = r.get("sources_used") or []
            if srcs:
                lines += [f"_Sources used: {len(srcs)}_", ""]
            meta = []
            if r.get("response_time_ms") is not None:
                meta.append(f"{r['response_time_ms']} ms")
            if r.get("token_usage") is not None:
                meta.append(f"{r['token_usage']} tokens")
            if meta:
                lines += ["_" + " · ".join(meta) + "_", ""]
    return "\n".join(lines)


def to_csv(run: Dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "group_type", "question", "answer", "sources_used_count",
        "retrieved_documents_count", "response_time_ms", "token_usage", "error",
    ])
    for group in run.get("query_groups", []):
        gtype = group.get("type")
        for r in group.get("results", []):
            writer.writerow([
                gtype,
                _csv_safe(r.get("question") or ""),
                _csv_safe(r.get("answer") or ""),
                len(r.get("sources_used") or []),
                len(r.get("retrieved_documents") or []),
                r.get("response_time_ms") if r.get("response_time_ms") is not None else "",
                r.get("token_usage") if r.get("token_usage") is not None else "",
                _csv_safe(r.get("error") or ""),
            ])
    return buf.getvalue()


# format -> (renderer, media_type, extension)
EXPORTERS = {
    "json": (to_json, "application/json", "json"),
    "md": (to_markdown, "text/markdown", "md"),
    "csv": (to_csv, "text/csv", "csv"),
}
