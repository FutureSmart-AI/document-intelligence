"""Unit tests for chat/chart_generator.py's chunk -> prompt mapping.

Regression cover for the 2026-08-24 silent-empty-chart bug: retrieval emits
chunks keyed ``chunk_text``, but the chart pipeline read only ``text``, so
every prompt was built with an empty ``Content:``. The LLM extracted blank
rows, the Chart API declined to plot them, and nothing was logged — the trace
showed five successful LLM calls and a chart that never appeared.

Pure unit tests: the module-level ``_extraction_llm`` is monkeypatched with a
fake that records the prompts it was handed — no real LLM or Chart API call.
"""

import asyncio
from types import SimpleNamespace

import chat.chart_generator as cg


def _run(coro):
    return asyncio.run(coro)


class _FakeLLM:
    """Records every prompt it is invoked with and replays canned replies."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    async def ainvoke(self, messages):
        self.prompts.append("\n".join(m.content for m in messages))
        content = self._responses.pop(0) if self._responses else "[]"
        return SimpleNamespace(content=content, usage_metadata=None)


# The shape retrieval actually returns (utils/files/retrieval_utils.py) and
# that _group_chunks passes through to chart_node verbatim.
_RETRIEVAL_CHUNK = {
    "object_id": "doc-1",
    "chunk_text": "Revenue for Q1 was 120000 INR across 3 regions.",
    "knowledge_id": "kb-1",
    "file_name": "Report.pdf",
    "relevance_score": 0.75,
    "search_method": "hybrid_knowledge_base",
}


def test_chunk_text_reads_every_retrieval_key():
    """chunk_text / text / content are all valid bodies; missing -> ''."""
    assert cg._chunk_text({"chunk_text": "a"}) == "a"
    assert cg._chunk_text({"text": "b"}) == "b"
    assert cg._chunk_text({"content": "c"}) == "c"
    assert cg._chunk_text({"chunk_text": "", "text": "fallback"}) == "fallback"
    assert cg._chunk_text({"object_id": "doc-1"}) == ""


def test_chunk_metadata_tolerates_repr_and_junk():
    assert cg._chunk_metadata({"metadata": {"title": "T"}}) == {"title": "T"}
    assert cg._chunk_metadata({"metadata": "{'title': 'T'}"}) == {"title": "T"}
    assert cg._chunk_metadata({"metadata": "not a dict"}) == {}
    assert cg._chunk_metadata({}) == {}


def test_field_determination_prompt_carries_chunk_text(monkeypatch):
    """The bug: this prompt used to read 'Content:' with nothing after it."""
    fake = _FakeLLM(['["region", "revenue"]'])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    fields = _run(cg.determine_extraction_fields("chart revenue", [_RETRIEVAL_CHUNK]))

    assert fields == ["region", "revenue"]
    assert "Revenue for Q1 was 120000 INR" in fake.prompts[0]
    assert "Content: \n" not in fake.prompts[0]


def test_extraction_prompt_carries_chunk_text_and_traces_document(monkeypatch):
    fake = _FakeLLM([
        '["region", "revenue"]',                       # field determination
        '[{"region": "West", "revenue": 120000}]',     # extraction batch
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    rows = _run(cg.extract_structured_data_with_llm([_RETRIEVAL_CHUNK], query="chart revenue"))

    assert "Revenue for Q1 was 120000 INR" in fake.prompts[1]
    assert rows == [{
        "region": "West", "revenue": 120000,
        # object_id stands in for document_id, which this shape omits.
        "document_id": "doc-1", "file_name": "Report.pdf",
    }]


def test_empty_chunk_text_skips_llm_entirely(monkeypatch):
    """An unknown chunk shape must cost zero LLM calls and say so loudly.

    The silence was the actual damage in production — the pipeline ran, billed
    and produced nothing, with no signal that retrieval's shape had changed."""
    fake = _FakeLLM([])
    monkeypatch.setattr(cg, "_extraction_llm", fake)
    warnings = []
    monkeypatch.setattr(cg.logger, "warning", warnings.append)

    csv_data = _run(cg.convert_chunks_to_csv(
        [{"object_id": "doc-1", "body": "text under a key we do not know"}],
        query="chart revenue",
    ))

    assert csv_data == ""
    assert fake.prompts == []
    assert len(warnings) == 1
    # The keys it actually saw are what makes the warning actionable.
    assert "body" in warnings[0] and "object_id" in warnings[0]


def test_csv_round_trip_from_retrieval_chunks(monkeypatch):
    """End to end for the shape from the failing trace: chunks -> CSV rows."""
    fake = _FakeLLM([
        '["region", "revenue"]',
        '[{"region": "West", "revenue": 120000}]',
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    csv_data = _run(cg.convert_chunks_to_csv([_RETRIEVAL_CHUNK], query="chart revenue"))

    lines = [line for line in csv_data.splitlines() if line.strip()]
    assert lines[0] == "document_id,file_name,region,revenue"
    assert lines[1] == "doc-1,Report.pdf,West,120000"


# --- Empty-row filtering (2026-08-25) ------------------------------------
#
# Second production defect, visible once real content started flowing: chart
# extraction emitted one row PER CHUNK unconditionally. A collection holding an
# unrelated filler PDF alongside four resumes produced 21 rows for 4 candidates
# — 14 of them all-zero rows from the filler doc — which the Chart API plotted
# as a "0 years"/"Unknown" bucket as tall as the real data.


def test_is_empty_row_recognises_the_prompt_s_own_missing_value_encoding():
    """The extraction prompt asks for ""/0 when a value is missing, so a
    no-signal chunk comes back as a full row of those."""
    fields = ["candidate_name", "years_of_experience"]
    assert cg._is_empty_row({"candidate_name": "", "years_of_experience": 0}, fields)
    assert cg._is_empty_row({"candidate_name": None, "years_of_experience": None}, fields)
    assert cg._is_empty_row({"candidate_name": "nan", "years_of_experience": 0}, fields)
    assert cg._is_empty_row({"candidate_name": "  ", "years_of_experience": 0.0}, fields)
    # Any real signal in any field keeps the row.
    assert not cg._is_empty_row({"candidate_name": "Priya", "years_of_experience": 0}, fields)
    assert not cg._is_empty_row({"candidate_name": "", "years_of_experience": 6}, fields)


def test_empty_rows_are_dropped_not_charted(monkeypatch):
    """Replays the production shape: one real candidate among filler chunks."""
    chunks = [
        {"object_id": "filler", "chunk_text": "Lorem ipsum dolor sit amet.",
         "file_name": "sample-9-page.pdf"},
        {"object_id": "filler", "chunk_text": "Table 4.1: Document format comparison",
         "file_name": "sample-9-page.pdf"},
        {"object_id": "resume-1", "chunk_text": "Priya Sharma, 6 years experience",
         "file_name": "Resume_Input.2.pdf"},
    ]
    fake = _FakeLLM([
        '["candidate_name", "years_of_experience"]',
        '[{"candidate_name": "", "years_of_experience": 0},'
        ' {"candidate_name": "", "years_of_experience": 0},'
        ' {"candidate_name": "Priya Sharma", "years_of_experience": 6}]',
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    rows = _run(cg.extract_structured_data_with_llm(chunks, query="chart experience"))

    assert rows == [{
        "candidate_name": "Priya Sharma", "years_of_experience": 6,
        "document_id": "resume-1", "file_name": "Resume_Input.2.pdf",
    }]


def test_all_empty_extraction_never_falls_back_to_raw_chunks(monkeypatch):
    """The old fallback POSTed raw chunk dicts — chunk_text, knowledge_id,
    internal ids — to the external Chart API. Nothing to chart must mean
    no CSV, not a CSV of internals."""
    fake = _FakeLLM([
        '["candidate_name"]',
        '[{"candidate_name": ""}]',
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    csv_data = _run(cg.convert_chunks_to_csv([_RETRIEVAL_CHUNK], query="chart experience"))

    assert csv_data == ""
    assert "chunk_text" not in csv_data and "knowledge_id" not in csv_data


def test_model_may_omit_irrelevant_items_and_rows_still_map_to_their_document(monkeypatch):
    """The prompt now asks the model to skip items with no relevant data.
    Rows must follow item_number back to the right chunk — reading them
    positionally would staple each row onto the wrong document."""
    chunks = [
        {"object_id": "filler-a", "chunk_text": "Lorem ipsum.", "file_name": "sample-9-page.pdf"},
        {"object_id": "filler-b", "chunk_text": "Table 4.1.", "file_name": "sample-9-page.pdf"},
        {"object_id": "resume-1", "chunk_text": "Priya Sharma, 6 years", "file_name": "Resume_Input.2.pdf"},
        {"object_id": "resume-2", "chunk_text": "Asha Rao, 2 years", "file_name": "Resume_Input.1.pdf"},
    ]
    fake = _FakeLLM([
        '["candidate_name", "years_of_experience"]',
        # Items 1 and 2 are filler and correctly omitted.
        '[{"item_number": 3, "candidate_name": "Priya Sharma", "years_of_experience": 6},'
        ' {"item_number": 4, "candidate_name": "Asha Rao", "years_of_experience": 2}]',
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    rows = _run(cg.extract_structured_data_with_llm(chunks, query="chart experience"))

    assert rows == [
        {"candidate_name": "Priya Sharma", "years_of_experience": 6,
         "document_id": "resume-1", "file_name": "Resume_Input.2.pdf"},
        {"candidate_name": "Asha Rao", "years_of_experience": 2,
         "document_id": "resume-2", "file_name": "Resume_Input.1.pdf"},
    ]
    # item_number is plumbing, never a chart column.
    assert all("item_number" not in row for row in rows)


def test_out_of_range_item_number_drops_the_row(monkeypatch):
    """Better no row than one attributed to the wrong document."""
    fake = _FakeLLM([
        '["candidate_name"]',
        '[{"item_number": 99, "candidate_name": "Ghost"}]',
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)
    warnings = []
    monkeypatch.setattr(cg.logger, "warning", warnings.append)

    rows = _run(cg.extract_structured_data_with_llm([_RETRIEVAL_CHUNK], query="chart experience"))

    assert rows == []
    assert len(warnings) == 1 and "99" in warnings[0]


def test_rows_without_item_number_still_map_positionally(monkeypatch):
    """Older/simpler model responses stay 1:1, so position remains correct."""
    chunks = [
        {"object_id": "resume-1", "chunk_text": "Priya, 6 years", "file_name": "a.pdf"},
        {"object_id": "resume-2", "chunk_text": "Asha, 2 years", "file_name": "b.pdf"},
    ]
    fake = _FakeLLM([
        '["candidate_name"]',
        '[{"candidate_name": "Priya"}, {"candidate_name": "Asha"}]',
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)

    rows = _run(cg.extract_structured_data_with_llm(chunks, query="chart experience"))

    assert [r["document_id"] for r in rows] == ["resume-1", "resume-2"]
