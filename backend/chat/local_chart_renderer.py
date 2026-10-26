"""In-process chart renderer — the chart path when no external Chart API is configured.

The hosted product posts the chart's result CSV to the FutureSmart Chart API
(``CHART_API_URL``, fs-agent-nl2sql ``chart_agent``). A standalone installation has
no such service, and until 2026-10-26 that meant ``chart_node`` silently returned no
chart at all: an install with only ``OPENAI_API_KEY`` lost the product's charts.

The numbers were never the Chart API's job — ``chart_generator.aggregate_population``
already computes the result table deterministically, before the answer is written.
What the Chart API added is (1) a choice of chart type and of which columns to plot,
and (2) the JSON shape the UI draws. This module does both in-process:

  * the **choice** is semantic (which chart answers *this* question, which column is
    the category and which the measure), so it is one OpenAI structured-output call
    (``ChartChoice``) — never a keyword rule. The call runs under
    ``run_name="chart_render"`` so per-turn usage attributes it to that operation;
  * the **mapping** is mechanical: rows go into exactly the response contract the
    Chart API returns (``GraphGenerationResponse`` in fs-agent-nl2sql
    ``chart_agent/models/graph_pydantic_models.py``):

        {"type": "chart_response", "chart_suggestion": "Bar Chart",
         "chart_data": {"data": [...], "metadata": {...}},
         "dataframe": {"columns", "data", "shape", "dtypes"},
         "chart_generated": true}

    with the per-type item shapes of ``chart_pydantic_models.py`` (bar/column
    ``{category, value}``, pie ``{label, value}``, line ``{x, y}``), which is what
    the DI frontend's ``ChatChart`` renders. One extra key, ``"renderer": "local"``,
    says which path produced the chart; the UI ignores it.

The column choice is validated deterministically (named columns exist, the measure is
numeric on every plotted row, a pie has no negative slice). A failed or invalid choice
degrades to a bar chart of the first label column against the first numeric column,
and records why in ``chart_data.metadata.chart_type_source`` — visible, never silent.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Literal, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from utils.logger import logger
from utils.setup_env import CHART_RENDER_MODEL, CHART_RENDER_PROVIDER

# The chart types the DI UI draws from a two-column result table. Stacked, bubble,
# scatter and geo need shapes (segments, three measures, coordinates) a grouped
# result table does not have, so offering them would only invite an invalid pick.
LocalChartType = Literal["Bar Chart", "Column Chart", "Pie Chart", "Line Chart"]
LOCAL_CHART_TYPES: Tuple[str, ...] = ("Bar Chart", "Column Chart", "Pie Chart", "Line Chart")

# The Chart API's default palette order is not part of the contract; these are the
# DI UI's own chart defaults (frontend/src/platform/chat-core/charts/constants.ts).
DEFAULT_COLORS: List[str] = [
    "#4f46e5", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444",
    "#8b5cf6", "#14b8a6", "#f97316", "#64748b", "#ec4899",
]

MAX_PIE_SLICES = 12
RUN_NAME = "chart_render"

# Test seam, same contract as chart_generator._extraction_llm: when set, the choice
# call goes to this object instead of a real model resolved through BYOK.
_render_llm: Optional[Any] = None


class ChartChoice(BaseModel):
    """The model's decision about how to draw one result table."""

    chart_type: LocalChartType = Field(
        description=(
            "Bar Chart for comparing categories (the default for counts per group); Column "
            "Chart for the same with few, short category labels; Pie Chart only for parts "
            "of one whole (counts or sums of non-negative values that add up to a total, "
            "at most 12 groups) — never for averages or per-document values; Line Chart "
            "only when the category is ordered in time or sequence (dates, years, "
            "ordered ranges) and the question is about a trend."
        )
    )
    label_column: str = Field(description="The column whose values name each bar/slice/point, exactly as written.")
    value_column: str = Field(description="The numeric column to plot, exactly as written.")
    title: str = Field(description="A short chart title that states what is plotted, under 70 characters.")
    reason: str = Field(description="One sentence: why this chart type answers the question.")


_CHOICE_PROMPT = (
    "You choose how to draw a chart for a question about a document collection. The "
    "numbers are already computed; you only pick the chart type, which column labels each "
    "item, which numeric column is plotted, and a short title. Use the column names exactly "
    "as given."
)


def parse_result_csv(csv_data: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """CSV text -> (columns, rows) with numeric cells converted to int/float."""
    reader = csv.DictReader(io.StringIO(csv_data or ""))
    columns = [c for c in (reader.fieldnames or []) if c is not None]
    rows: List[Dict[str, Any]] = []
    for raw in reader:
        row = {}
        for col in columns:
            row[col] = _coerce(raw.get(col))
        rows.append(row)
    return columns, rows


def _coerce(value: Any) -> Any:
    """A CSV cell as int/float when it is a finite number, else the stripped text."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        number = float(text)
    except ValueError:
        return text
    if number != number or number in (float("inf"), float("-inf")):
        return text
    if number.is_integer() and "." not in text and "e" not in text.lower():
        return int(number)
    return number


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_columns(columns: List[str], rows: List[Dict[str, Any]]) -> List[str]:
    """Columns whose every non-empty cell is a number (and at least one is)."""
    out = []
    for col in columns:
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if values and all(_is_number(v) for v in values):
            out.append(col)
    return out


def default_choice(columns: List[str], rows: List[Dict[str, Any]], query: str) -> Optional[Dict[str, str]]:
    """The mechanical fallback: a bar chart of the first column against the LAST numeric
    column. chart_generator's result tables are ``[group, value]`` in that order, and a
    group can itself be numeric (years of experience), so "last numeric" is the measure.
    None when the table has no numeric column to plot."""
    numeric = numeric_columns(columns, rows)
    if not numeric:
        return None
    value = numeric[-1]
    labels = [c for c in columns if c != value]
    label = labels[0] if labels else value
    return {"chart_type": "Bar Chart", "label_column": label, "value_column": value,
            "title": (query or "Chart")[:70]}


def validate_choice(choice: Dict[str, str], columns: List[str], rows: List[Dict[str, Any]]) -> Optional[str]:
    """None when the choice can be drawn as stated; otherwise the reason it cannot."""
    if choice.get("chart_type") not in LOCAL_CHART_TYPES:
        return f"unknown chart type {choice.get('chart_type')!r}"
    label, value = choice.get("label_column"), choice.get("value_column")
    if label not in columns:
        return f"label column {label!r} is not in the table"
    if value not in numeric_columns(columns, rows):
        return f"value column {value!r} is not numeric in the table"
    if choice["chart_type"] == "Pie Chart":
        values = [r.get(value) for r in rows if r.get(value) is not None]
        if any(v < 0 for v in values):
            return "a pie chart cannot show negative values"
        if len(rows) > MAX_PIE_SLICES:
            return f"a pie chart of {len(rows)} slices is unreadable (max {MAX_PIE_SLICES})"
    return None


def build_chart_response(
    columns: List[str], rows: List[Dict[str, Any]], choice: Dict[str, str], *, source: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Map the result table into the Chart API's GraphGenerationResponse shape.
    Deterministic: the same rows and choice always produce the same JSON."""
    chart_type = choice["chart_type"]
    label_col, value_col = choice["label_column"], choice["value_column"]
    plotted = [r for r in rows if _is_number(r.get(value_col))]
    title = (choice.get("title") or "").strip() or f"{value_col} by {label_col}"

    def label_of(row: Dict[str, Any]) -> str:
        value = row.get(label_col)
        return "Not stated" if value is None else str(value)

    if chart_type == "Pie Chart":
        data = [{"label": label_of(r), "value": r[value_col]} for r in plotted]
        metadata: Dict[str, Any] = {
            "title": title,
            "legend": {"show": True, "position": "right", "title": label_col},
            "colors": DEFAULT_COLORS[: max(len(data), 1)],
            "show_percentages": True,
            "total_label": f"Total {value_col}",
        }
    elif chart_type == "Line Chart":
        data = [{"x": label_of(r), "y": r[value_col]} for r in plotted]
        metadata = {
            "title": title,
            "x_axis": {"label": label_col, "field": "x"},
            "y_axis": {"label": value_col, "field": "y"},
            "line": {"color": DEFAULT_COLORS[0], "width": 2, "smooth": False},
            "legend": {"show": False},
        }
    else:  # Bar Chart / Column Chart share BarColumnChartData
        data = [{"category": label_of(r), "value": r[value_col]} for r in plotted]
        metadata = {
            "title": title,
            "x_axis": {"label": label_col, "field": "category"},
            "y_axis": {"label": value_col, "field": "value"},
            "legend": {"show": False},
            "colors": DEFAULT_COLORS[:1],
        }
    metadata["chart_type_source"] = source
    if reason:
        metadata["chart_type_reason"] = reason

    dtypes = {}
    for col in columns:
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            dtypes[col] = "int64"
        elif values and all(_is_number(v) for v in values):
            dtypes[col] = "float64"
        else:
            dtypes[col] = "object"
    return {
        "type": "chart_response",
        "chart_suggestion": chart_type,
        "chart_data": {"data": data, "metadata": metadata},
        "dataframe": {
            "columns": list(columns),
            "data": [dict(r) for r in rows],
            "shape": [len(rows), len(columns)],
            "dtypes": dtypes,
        },
        "chart_generated": True,
        "renderer": "local",
    }


async def _choose(
    query: str, columns: List[str], rows: List[Dict[str, Any]],
    owner_id: Optional[str], knowledge_id: Optional[str],
) -> Tuple[Optional[ChartChoice], Optional[Dict[str, Any]], Optional[str]]:
    """One structured-output call. Returns (choice, usage, error)."""
    preview = rows[:30]
    messages = [
        SystemMessage(content=_CHOICE_PROMPT),
        HumanMessage(content=(
            f"Question: {query}\n\nColumns: {columns}\nNumeric columns: {numeric_columns(columns, rows)}\n"
            f"Rows ({len(rows)} total, first {len(preview)} shown): {preview}"
        )),
    ]
    config = {"run_name": RUN_NAME, "tags": [RUN_NAME], "metadata": {"operation": RUN_NAME}}
    try:
        if _render_llm is not None:  # test seam
            result = await _render_llm.with_structured_output(ChartChoice, include_raw=True).ainvoke(messages, config=config)
            provider, key_type = CHART_RENDER_PROVIDER, "platform-key"
        else:
            from chat.byok_fallback import run_with_key_fallback

            outcome = await run_with_key_fallback(
                lambda llm: llm.with_structured_output(ChartChoice, include_raw=True).ainvoke(messages, config=config),
                owner_id=owner_id, knowledge_id=knowledge_id, source=RUN_NAME,
                provider=CHART_RENDER_PROVIDER, model=CHART_RENDER_MODEL, temperature=0,
                for_function_calling=True,
            )
            result, provider, key_type = outcome.value, outcome.provider, outcome.key_type
    except Exception as e:  # provider/network failure -> visible degrade, never a lost turn
        return None, None, f"chart type call failed: {type(e).__name__}: {e}"[:300]
    usage = None
    raw = result.get("raw") if isinstance(result, dict) else None
    meta = getattr(raw, "usage_metadata", None)
    if isinstance(meta, dict):
        usage = {"provider": provider, "model": CHART_RENDER_MODEL, "key_type": key_type,
                 "input_tokens": meta.get("input_tokens", 0) or 0,
                 "output_tokens": meta.get("output_tokens", 0) or 0}
    parsed = result.get("parsed") if isinstance(result, dict) else result
    if not isinstance(parsed, ChartChoice):
        error = result.get("parsing_error") if isinstance(result, dict) else None
        return None, usage, f"chart type answer unparseable: {error}"[:300]
    return parsed, usage, None


async def render_chart_locally(
    query: str, csv_data: str, owner_id: Optional[str] = None, knowledge_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Result CSV -> (Chart API-shaped chart dict, usage of the choice call).

    Returns (None, usage) when the table has fewer than two rows or no numeric column —
    the same tables the Chart API declines. Never raises."""
    try:
        columns, rows = parse_result_csv(csv_data)
    except Exception as e:
        logger.warning(f"[chart_render] Could not parse result CSV: {e}")
        return None, None
    if len(rows) < 2 or not numeric_columns(columns, rows):
        logger.info(f"[chart_render] Nothing to chart ({len(rows)} row(s), numeric={numeric_columns(columns, rows)})")
        return None, None

    choice, usage, error = await _choose(query, columns, rows, owner_id, knowledge_id)
    if choice is not None:
        picked = choice.model_dump()
        invalid = validate_choice(picked, columns, rows)
        if invalid is None:
            logger.info(
                f"[chart_render] {picked['chart_type']} of {picked['value_column']} by "
                f"{picked['label_column']} ({len(rows)} rows, model={CHART_RENDER_MODEL}): {choice.reason}"
            )
            return build_chart_response(columns, rows, picked, source="llm", reason=choice.reason), usage
        error = f"model choice not drawable ({invalid})"
    fallback = default_choice(columns, rows, query)
    if fallback is None:
        return None, usage
    logger.warning(f"[chart_render] Using default bar chart: {error}")
    return build_chart_response(columns, rows, fallback, source="default", reason=error), usage
