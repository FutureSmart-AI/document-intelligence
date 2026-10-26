"""chat/local_chart_renderer.py — charts without the external Chart API (2026-10-26).

A standalone install with only OPENAI_API_KEY used to get no chart at all:
chart_node returned early whenever CHART_API_URL was unset. These tests pin:

* the deterministic mapping into the Chart API's response contract
  (fs-agent-nl2sql chart_agent GraphGenerationResponse + chart_pydantic_models),
  per chart type, which is what the DI UI's ChatChart draws;
* that the chart-type choice is ONE structured-output call tagged
  run_name="chart_render", and that a failed or undrawable choice degrades to a
  bar chart with the reason recorded (never silently);
* the dispatch: CHART_API_URL set -> external API (hosted behaviour unchanged),
  unset -> local renderer; and chart_node no longer skips charts when it is unset.

No network, no LLM: the choice model is a fake.
"""

import asyncio
from types import SimpleNamespace

import pytest

import chat.chart_generator as cg
import chat.chart_node as chart_node_module
import chat.local_chart_renderer as lcr


def _run(coro):
    return asyncio.run(coro)


COUNT_CSV = "Primary skill,Candidates\nPython,3\nJava,2\nGo,1\n"
YEARS_CSV = "Years of experience,Candidates\n2,1\n5,2\n8,2\n12,1\n"


class _FakeChoiceLLM:
    """with_structured_output(ChartChoice, include_raw=True).ainvoke(messages, config=...)"""

    def __init__(self, choice=None, error=None):
        self.choice, self.error = choice, error
        self.configs, self.messages = [], []

    def with_structured_output(self, schema, include_raw=False):
        assert schema is lcr.ChartChoice and include_raw
        outer = self

        class _Bound:
            async def ainvoke(self, messages, config=None):
                outer.messages.append(messages)
                outer.configs.append(config)
                if outer.error:
                    raise outer.error
                raw = SimpleNamespace(usage_metadata={"input_tokens": 50, "output_tokens": 12})
                parsed = lcr.ChartChoice(**outer.choice) if outer.choice else None
                return {"raw": raw, "parsed": parsed, "parsing_error": None if parsed else "bad"}

        return _Bound()


@pytest.fixture
def fake_llm(monkeypatch):
    def _install(**kwargs):
        fake = _FakeChoiceLLM(**kwargs)
        monkeypatch.setattr(lcr, "_render_llm", fake)
        return fake
    return _install


# ── deterministic mapping ──────────────────────────────────────────────────

def test_bar_chart_maps_rows_into_the_chart_api_contract():
    columns, rows = lcr.parse_result_csv(COUNT_CSV)
    out = lcr.build_chart_response(columns, rows, {
        "chart_type": "Bar Chart", "label_column": "Primary skill", "value_column": "Candidates",
        "title": "Candidates by primary skill"}, source="llm", reason="compare groups")
    assert out == {
        "type": "chart_response",
        "chart_suggestion": "Bar Chart",
        "chart_data": {
            "data": [{"category": "Python", "value": 3}, {"category": "Java", "value": 2},
                     {"category": "Go", "value": 1}],
            "metadata": {
                "title": "Candidates by primary skill",
                "x_axis": {"label": "Primary skill", "field": "category"},
                "y_axis": {"label": "Candidates", "field": "value"},
                "legend": {"show": False},
                "colors": [lcr.DEFAULT_COLORS[0]],
                "chart_type_source": "llm",
                "chart_type_reason": "compare groups",
            },
        },
        "dataframe": {
            "columns": ["Primary skill", "Candidates"],
            "data": [{"Primary skill": "Python", "Candidates": 3}, {"Primary skill": "Java", "Candidates": 2},
                     {"Primary skill": "Go", "Candidates": 1}],
            "shape": [3, 2],
            "dtypes": {"Primary skill": "object", "Candidates": "int64"},
        },
        "chart_generated": True,
        "renderer": "local",
    }


def test_pie_uses_label_value_items_and_line_uses_x_y():
    columns, rows = lcr.parse_result_csv(COUNT_CSV)
    pie = lcr.build_chart_response(columns, rows, {"chart_type": "Pie Chart", "label_column": "Primary skill",
                                                   "value_column": "Candidates", "title": "t"}, source="llm")
    assert pie["chart_data"]["data"][0] == {"label": "Python", "value": 3}
    assert pie["chart_data"]["metadata"]["show_percentages"] is True
    assert len(pie["chart_data"]["metadata"]["colors"]) == 3

    columns, rows = lcr.parse_result_csv(YEARS_CSV)
    line = lcr.build_chart_response(columns, rows, {"chart_type": "Line Chart", "label_column": "Years of experience",
                                                    "value_column": "Candidates", "title": "t"}, source="llm")
    assert line["chart_data"]["data"][:2] == [{"x": "2", "y": 1}, {"x": "5", "y": 2}]
    assert line["chart_data"]["metadata"]["x_axis"] == {"label": "Years of experience", "field": "x"}


def test_csv_cells_become_numbers_only_when_they_are_numbers():
    columns, rows = lcr.parse_result_csv("Band,Avg\n3-5,4.5\n10+,11\nNot stated,\n")
    assert rows == [{"Band": "3-5", "Avg": 4.5}, {"Band": "10+", "Avg": 11}, {"Band": "Not stated", "Avg": None}]
    assert lcr.numeric_columns(columns, rows) == ["Avg"]


def test_default_choice_plots_the_last_numeric_column_when_the_group_is_numeric():
    columns, rows = lcr.parse_result_csv(YEARS_CSV)
    choice = lcr.default_choice(columns, rows, "distribution of experience")
    assert (choice["label_column"], choice["value_column"]) == ("Years of experience", "Candidates")


def test_validation_refuses_what_cannot_be_drawn():
    columns, rows = lcr.parse_result_csv("Skill,Delta\na,-1\nb,2\n")
    assert "negative" in lcr.validate_choice(
        {"chart_type": "Pie Chart", "label_column": "Skill", "value_column": "Delta"}, columns, rows)
    assert "not numeric" in lcr.validate_choice(
        {"chart_type": "Bar Chart", "label_column": "Delta", "value_column": "Skill"}, columns, rows)
    assert "not in the table" in lcr.validate_choice(
        {"chart_type": "Bar Chart", "label_column": "Nope", "value_column": "Delta"}, columns, rows)
    assert lcr.validate_choice(
        {"chart_type": "Bar Chart", "label_column": "Skill", "value_column": "Delta"}, columns, rows) is None


# ── the LLM choice ─────────────────────────────────────────────────────────

def test_choice_is_one_structured_call_tagged_chart_render(fake_llm):
    fake = fake_llm(choice={"chart_type": "Pie Chart", "label_column": "Primary skill",
                            "value_column": "Candidates", "title": "Share by skill", "reason": "parts of a whole"})
    chart, usage = _run(lcr.render_chart_locally("share of candidates per skill", COUNT_CSV, "owner", "kb"))
    assert chart["chart_suggestion"] == "Pie Chart"
    assert chart["renderer"] == "local" and chart["chart_data"]["metadata"]["chart_type_source"] == "llm"
    assert len(fake.configs) == 1 and fake.configs[0]["run_name"] == "chart_render"
    assert usage["input_tokens"] == 50 and usage["output_tokens"] == 12


def test_undrawable_choice_degrades_to_bar_with_the_reason(fake_llm):
    fake_llm(choice={"chart_type": "Bar Chart", "label_column": "Candidates",
                     "value_column": "Primary skill", "title": "x", "reason": "r"})
    chart, _ = _run(lcr.render_chart_locally("q", COUNT_CSV))
    meta = chart["chart_data"]["metadata"]
    assert chart["chart_suggestion"] == "Bar Chart"
    assert meta["chart_type_source"] == "default"
    assert "not numeric" in meta["chart_type_reason"]
    assert chart["chart_data"]["data"][0] == {"category": "Python", "value": 3}


def test_failed_call_degrades_visibly_never_drops_the_chart(fake_llm):
    fake_llm(error=RuntimeError("model_not_found"))
    chart, usage = _run(lcr.render_chart_locally("q", COUNT_CSV))
    assert chart["chart_generated"] is True and usage is None
    assert "model_not_found" in chart["chart_data"]["metadata"]["chart_type_reason"]


def test_tables_the_chart_api_declines_produce_no_chart(fake_llm):
    fake = fake_llm(choice={"chart_type": "Bar Chart", "label_column": "a", "value_column": "b",
                            "title": "t", "reason": "r"})
    assert _run(lcr.render_chart_locally("q", "a,b\nx,1\n")) == (None, None)
    assert _run(lcr.render_chart_locally("q", "a,b\nx,y\nz,w\n")) == (None, None)
    assert fake.configs == []  # no model call for a table that cannot be charted


# ── dispatch ───────────────────────────────────────────────────────────────

def test_configured_chart_api_keeps_the_hosted_path(monkeypatch, fake_llm):
    fake = fake_llm(choice=None)
    seen = {}

    async def _api(query, owner_id, csv_data):
        seen["csv"] = csv_data
        return {"type": "chart_response", "chart_generated": True}

    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "https://chart.example/api")
    monkeypatch.setattr(cg, "call_chart_api", _api)
    out = _run(cg.render_chart("q", "owner", COUNT_CSV, knowledge_id="kb"))
    assert out == {"type": "chart_response", "chart_generated": True} and seen["csv"] == COUNT_CSV
    assert fake.configs == []


def test_unset_chart_api_draws_locally(monkeypatch, fake_llm):
    fake_llm(choice={"chart_type": "Bar Chart", "label_column": "Primary skill",
                     "value_column": "Candidates", "title": "t", "reason": "r"})

    async def _api(*args, **kwargs):
        raise AssertionError("external Chart API must not be called when CHART_API_URL is unset")

    async def _usage(**kwargs):
        return None

    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "")
    monkeypatch.setattr(cg, "call_chart_api", _api)
    monkeypatch.setattr("utils.qdrant.usage_tracking.send_llm_usage_event", _usage)
    out = _run(cg.render_chart("q", "owner", COUNT_CSV, knowledge_id="kb"))
    assert out["renderer"] == "local" and out["chart_suggestion"] == "Bar Chart"


def test_chart_node_charts_without_a_chart_api(monkeypatch, fake_llm):
    fake_llm(choice={"chart_type": "Column Chart", "label_column": "Primary skill",
                     "value_column": "Candidates", "title": "t", "reason": "r"})

    async def _config(*args, **kwargs):
        return SimpleNamespace(include_charts=True)

    async def _usage(**kwargs):
        return None

    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "")
    monkeypatch.setattr("chat.config.resolve_chat_config", _config)
    monkeypatch.setattr("utils.qdrant.usage_tracking.send_llm_usage_event", _usage)
    aggregation = {"status": "ok", "documents": 6, "complete": True, "source": "test",
                   "rows": [{"Primary skill": "Python", "Candidates": 3.0},
                            {"Primary skill": "Java", "Candidates": 2.0},
                            {"Primary skill": "Go", "Candidates": 1.0}]}
    out = _run(chart_node_module.chart_node("how many per skill", "kb", "owner", {},
                                            summaries={}, rag_context={"aggregation": aggregation}))
    chart = out["chart_output"]
    assert chart["renderer"] == "local" and chart["chart_suggestion"] == "Column Chart"
    assert chart["chart_data"]["data"] == [{"category": "Python", "value": 3}, {"category": "Java", "value": 2},
                                           {"category": "Go", "value": 1}]
