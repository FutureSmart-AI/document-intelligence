"""Numbers in the answer text and in the chart come from ONE computation.

Measured on the hosted resume sample (7c37eb7d, rev 240): the chart and the
extracted data said 2 candidates have 5 years and 4 have 7, the answer text
said 1 and 3. The chart computed its table AFTER the answer was written
(graph: rag -> chart), so the answer model counted the document cards itself.

These tests run the real turn path — run_rag_agent (with a scripted retrieval
agent), the answer node's context block, chart_node — and assert that the
figures handed to the answer are exactly the rows the chart plots, for a
count, a grouped distribution and a filtered population. The spec LLM, the
retrieval agent, the answer LLM and the Chart API are fakes; the aggregation
is the real code.
"""

import asyncio
import csv
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

import chat.chart_generator as cg
from chat.chart_node import chart_node
from chat.rag_agent import agent_execution
from chat.rag_agent.answer_node import _build_context_block

KB = "numeric-test-kb"


def _doc(i, years, education, seniority, currency=None):
    return {"object_id": f"d{i}", "file_name": f"r{i:02d}.pdf",
            "metadata": {"candidate_name": f"Person {i}", "years_of_experience": years,
                         "education_level": education, "seniority_level": seniority,
                         "summary": "s"}}


# Shaped like the hosted sample's skew: two people at 5 years, four at 7.
DOCS = [
    _doc(1, 0, "bachelors", "junior"), _doc(2, 2, "bachelors", "junior"), _doc(3, 2, "masters", "junior"),
    _doc(4, 5, "bachelors", "mid_level"), _doc(5, 5, "masters", "mid_level"),
    _doc(6, 7, "bachelors", "senior"), _doc(7, 7, "masters", "senior"), _doc(8, 7, "bachelors", "mid_level"),
    _doc(9, 7, "phd", "senior"), _doc(10, 12, "masters", "lead"), _doc(11, 15, "bachelors", "principal"),
]


class _SpecLLM:
    def __init__(self, spec):
        self.spec, self.calls = spec, 0

    def with_structured_output(self, schema, include_raw=False):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        return {"raw": SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5}),
                "parsed": self.spec, "parsing_error": None}


def _spec(**kw):
    base = dict(chartable=True, reason="r", confidence="high", operation="count",
                group_label="Group", value_label="Candidates")
    base.update(kw)
    return cg.ChartSpec(**base)


def _turn(docs, shape="aggregate", applied_filter=None):
    """Run the real run_rag_agent with a retrieval agent that made one
    exhaustive retrieve_documents call, capture what the answer node was
    given, then run chart_node on the turn's rag_context."""
    artifact = {"documents": docs, "count": len(docs), "total_match_count": len(docs),
                "question_shape": shape, "population_complete": True, "truncated": False,
                "applied_filter": applied_filter}

    class Agent:
        async def ainvoke(self, state):
            return {"messages": list(state["messages"]) + [
                AIMessage(content="", tool_calls=[{"id": "c1", "name": "retrieve_documents", "args": {}}]),
                ToolMessage(content="docs", name="retrieve_documents", tool_call_id="c1", artifact=artifact)]}

    seen = {}

    async def fake_answer(**kwargs):
        seen["context"] = kwargs["retrieved_context"]
        return {"answer": "ok", "usage_metrics": {}}

    posted = {}

    async def fake_chart_api(query, owner_id, csv_data):
        posted["csv"] = csv_data
        return {"chart_generated": True}

    async def fake_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=True)

    with patch.object(agent_execution, "create_rag_agent_graph", new_callable=AsyncMock,
                      return_value=(Agent(), "platform-key")), \
         patch("chat.rag_agent.answer_node.generate_rag_answer", side_effect=fake_answer), \
         patch("chat.config.resolve_chat_config", side_effect=fake_config), \
         patch("utils.setup_env.CHART_API_URL", "https://chart.example/api"), \
         patch.object(cg, "call_chart_api", side_effect=fake_chart_api), \
         patch("utils.qdrant.usage_tracking.send_llm_usage_event", new_callable=AsyncMock):
        context = asyncio.run(agent_execution.run_rag_agent(
            query="q", knowledge_id=KB, user_id="u", owner_id="o",
            enable_retrieval_verifier=False, charts_enabled=True))
        asyncio.run(chart_node(query="q", knowledge_id=KB, owner_id="o", detailed_chunks={},
                               summaries=None, rag_context=context))
    return seen.get("context") or {}, posted.get("csv")


def _facts_table(context):
    """The rows of the Computed Figures table the answer node was shown."""
    block = _build_context_block(context)
    assert "## Computed Figures" in block, "the answer was given no computed figures"
    table = block.split("## Computed Figures", 1)[1].split("RULES:", 1)[0]
    rows = [line.strip().strip("|").split("|") for line in table.splitlines()
            if line.startswith("|") and not line.startswith("|---")]
    header, body = rows[0], rows[1:]
    return {r[0].strip(): float(r[1]) for r in body}, [h.strip() for h in header]


def _csv_table(csv_data):
    reader = list(csv.reader(io.StringIO(csv_data)))
    return {r[0]: float(r[1]) for r in reader[1:]}


def _assert_text_and_chart_agree(spec, docs, expected, **turn_kw):
    llm = _SpecLLM(spec)
    with patch.object(cg, "_extraction_llm", llm):
        context, chart_csv = _turn(docs, **turn_kw)
    figures, _ = _facts_table(context)
    assert figures == expected, f"answer figures {figures} != data {expected}"
    assert chart_csv is not None, "no chart drawn"
    assert _csv_table(chart_csv) == figures, "chart and answer figures differ"
    assert llm.calls == 1, f"the numbers were decided {llm.calls} times; text and chart must share one"


def test_grouped_distribution_years_text_equals_chart():
    expected = {}
    for d in DOCS:
        k = str(d["metadata"]["years_of_experience"])
        expected[k] = expected.get(k, 0) + 1
    _assert_text_and_chart_agree(
        _spec(group_by_field="years_of_experience", group_label="Years of experience"), DOCS,
        {k: float(v) for k, v in expected.items()})
    assert expected["5"] == 2 and expected["7"] == 4  # the hosted skew


def test_count_per_category_education_text_equals_chart():
    expected = {}
    for d in DOCS:
        k = d["metadata"]["education_level"]
        expected[k] = expected.get(k, 0) + 1
    _assert_text_and_chart_agree(
        _spec(group_by_field="education_level", group_label="Education level"), DOCS,
        {k: float(v) for k, v in expected.items()})


def test_filtered_population_is_what_both_count():
    """'Among candidates with at least 5 years, how many per seniority': the
    exhaustive call's filter states the condition, so the figures are over
    exactly the filtered population — in the text and in the chart."""
    filtered = [d for d in DOCS if d["metadata"]["years_of_experience"] >= 5]
    expected = {}
    for d in filtered:
        k = d["metadata"]["seniority_level"]
        expected[k] = expected.get(k, 0) + 1
    _assert_text_and_chart_agree(
        _spec(group_by_field="seniority_level", group_label="Seniority"), filtered,
        {k: float(v) for k, v in expected.items()},
        shape="predicate", applied_filter={"years_of_experience": {"gte": 5}})


def test_bucketed_counts_text_equals_chart():
    _assert_text_and_chart_agree(
        _spec(group_by_field="years_of_experience", bucket_edges=[0, 3, 6, 10], group_label="Years"), DOCS,
        {"0-2": 3.0, "3-5": 2.0, "6-9": 4.0, "10+": 2.0})


def _invoice(i, vendor, currency, total):
    return {"object_id": f"i{i}", "file_name": f"inv{i}.pdf",
            "metadata": {"vendor_name": vendor, "currency": currency, "total_amount": total,
                         "invoice_number": f"INV-{i}", "summary": "s"}}


def test_filtered_grouped_sum_invoice_totals_text_equals_chart():
    """'Total billed by each US-dollar vendor': a sum per group over the
    currency-filtered population."""
    invoices = [_invoice(1, "Lumen", "USD", 1000.5), _invoice(2, "Lumen", "USD", 2500.0),
                _invoice(3, "Quill", "USD", 700.25), _invoice(4, "Copper", "USD", 99.0)]
    _assert_text_and_chart_agree(
        _spec(operation="sum", group_by_field="vendor_name", value_field="total_amount",
              group_label="Vendor", value_label="Total (USD)"), invoices,
        {"Lumen": 3500.5, "Quill": 700.25, "Copper": 99.0},
        shape="predicate", applied_filter={"currency": "USD"})
