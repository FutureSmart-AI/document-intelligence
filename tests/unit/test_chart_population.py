"""Unit tests for the chart population path (chat/chart_generator.py,
chat/chart_node.py), added 2026-10-25.

Regression cover for three defects measured on the hosted app:

* an exhaustive aggregate retrieval returns every document's extracted fields
  and NO chunks, and chart_node returned no chart because it only looked at
  chunks ("distribution of candidates by years of experience" over 24 resumes);
* charts were built one row per retrieved CHUNK and posted to the Chart API
  unaggregated, so a category was plotted once per row ("Python" three times,
  each valued 3) and a chunk missing the fact became an "Unknown" bar;
* a 0/1-row table was still posted, and the Chart API answered 500.

Pure unit tests: the spec LLM and the Chart API are fakes — no real call.
"""

import asyncio
import json
import csv
import io
from types import SimpleNamespace

import chat.chart_generator as cg
import chat.chart_node as chart_node_module


def _run(coro):
    return asyncio.run(coro)


def _resume(oid, name, years, skill, seniority, skills=None):
    return {
        "object_id": oid,
        "file_name": f"{name.lower().replace(' ', '-')}.pdf",
        "metadata": {
            "candidate_name": name,
            "years_of_experience": years,
            "primary_skill": skill,
            "seniority_level": seniority,
            "skills": skills or [skill],
            "summary": "long prose that is not a chart dimension",
            "work_experience": [{"company": "X", "start_year": 2020}],
        },
    }


RESUMES = [
    _resume("d1", "Asha Verma", 2, "Python", "junior"),
    _resume("d2", "Daniel Okafor", 5, "Java", "mid_level"),
    _resume("d3", "Mei Lin", 8, "Python", "senior", ["Python", "PyTorch"]),
    _resume("d4", "Rafael Gomez", 3, "JavaScript", "mid_level"),
    _resume("d5", "Priya Raman", 12, "Java", "principal", ["Java", "Kubernetes"]),
    _resume("d6", "Tom Becker", 5, "Python", "mid_level", ["Python", "Spark"]),
    _resume("d7", "Fatima Zahra", 1, "JavaScript", "junior"),
    _resume("d8", "Kenji Watanabe", 8, "Go", "senior"),
]


def _exhaustive_rag_context(docs, truncated=False):
    """What run_rag_agent leaves behind for an aggregate question: one
    retrieve_documents artifact with every matching document, no chunks."""
    return {
        "summaries": {d["object_id"]: {**d} for d in docs},
        "chunks": [],
        "tool_responses": [{
            "tool_name": "retrieve_documents",
            "result": {
                "documents": docs,
                "count": len(docs),
                "question_shape": "aggregate",
                "total_match_count": len(docs),
                "truncated": truncated,
            },
        }],
    }


class _FakeStructuredLLM:
    """Stands in for llm.with_structured_output(ChartSpec, include_raw=True)."""

    def __init__(self, specs):
        self.specs = list(specs)
        self.prompts = []

    def with_structured_output(self, schema, include_raw=False):
        assert schema is cg.ChartSpec and include_raw
        return self

    async def ainvoke(self, messages):
        self.prompts.append("\n".join(m.content for m in messages))
        usage = {"input_tokens": 10, "output_tokens": 5}
        return {"raw": SimpleNamespace(usage_metadata=usage), "parsed": self.specs.pop(0), "parsing_error": None}


def _spec(**kw):
    base = dict(chartable=True, reason="r", confidence="high", operation="count",
                group_label="Group", value_label="Candidates")
    base.update(kw)
    return cg.ChartSpec(**base)


def _population(docs=RESUMES):
    return _run(cg.build_population("kb", None, _exhaustive_rag_context(docs)))[0]


def test_aggregate_over_an_incomplete_population_is_not_charted(monkeypatch):
    monkeypatch.setattr(cg, "_extraction_llm", _FakeStructuredLLM([_spec(group_by_field="seniority_level")]))
    sent = _capture_chart_api(monkeypatch)
    out, _ = _run(cg.process_chart_visualization(
        "candidates per seniority level", "owner-1", [], documents=_population(), population_complete=False))
    assert out is None and "csv" not in sent


def test_per_document_bars_over_an_incomplete_population_still_chart(monkeypatch):
    monkeypatch.setattr(cg, "_extraction_llm", _FakeStructuredLLM([_spec(
        operation="per_document", group_by_field="candidate_name", value_field="years_of_experience")]))
    sent = _capture_chart_api(monkeypatch)
    out, _ = _run(cg.process_chart_visualization(
        "years per candidate", "owner-1", [], documents=_population(RESUMES[:3]), population_complete=False))
    assert out["chart_generated"] is True and len(_table(sent["csv"])) == 3


def _table(csv_text):
    return [dict(r) for r in csv.DictReader(io.StringIO(csv_text))]


# ── chart_node: the population reaches the chart even with zero chunks ─────

def test_exhaustive_aggregate_with_no_chunks_still_charts(monkeypatch):
    """The hosted failure: rag returned 24 summaries and 0 chunks, and the old
    chart_node returned chart_output=None without trying."""
    async def _fake_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=True)

    seen = {}

    async def _fake_process(query, owner_id, chunks, knowledge_id=None, documents=None, population_complete=True):
        seen["chunks"], seen["documents"] = chunks, documents
        return {"chart_generated": True}, None

    monkeypatch.setattr("chat.config.resolve_chat_config", _fake_config)
    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "https://chart.example/api")
    monkeypatch.setattr("chat.chart_generator.process_chart_visualization", _fake_process)

    rag_context = _exhaustive_rag_context(RESUMES)
    result = _run(chart_node_module.chart_node(
        query="distribution of candidates by years of experience",
        knowledge_id="kb", owner_id="owner-1", detailed_chunks={},
        summaries={}, rag_context=rag_context,
    ))

    assert result["chart_output"] == {"chart_generated": True}
    assert seen["chunks"] == []
    assert sorted(d["object_id"] for d in seen["documents"]) == [f"d{i}" for i in range(1, 9)]


def test_graph_chart_step_charts_an_exhaustive_turn_with_no_chunks(monkeypatch):
    """Same failure through the real graph wrapper and real generator, with only
    the spec LLM and the Chart API faked: state carries rag's summaries and
    rag_context (every document, zero chunks), and the chart must be built
    from them and carry the right numbers."""
    import chat.graph as graph

    async def _fake_config(knowledge_id, **kwargs):
        return SimpleNamespace(include_charts=True)

    monkeypatch.setattr("chat.config.resolve_chat_config", _fake_config)
    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "https://chart.example/api")
    monkeypatch.setattr(cg, "_extraction_llm", _FakeStructuredLLM([
        _spec(group_by_field="years_of_experience", bucket_edges=[0, 3, 6, 10],
              group_label="Years of experience")]))
    sent = _capture_chart_api(monkeypatch)

    rag_context = _exhaustive_rag_context(RESUMES)
    result = _run(graph._chart_graph_node({
        "query": "distribution of candidates by years of experience",
        "knowledge_id": "kb", "owner_id": "owner-1", "session_id": "s1",
        "detailed_chunks": {}, "summaries": rag_context["summaries"], "rag_context": rag_context,
    }))

    assert result["chart_output"] == {"type": "chart_response", "chart_generated": True}
    assert _table(sent["csv"]) == [
        {"Years of experience": "0-2", "Candidates": "2"},
        {"Years of experience": "3-5", "Candidates": "3"},
        {"Years of experience": "6-9", "Candidates": "2"},
        {"Years of experience": "10+", "Candidates": "1"},
    ]


# ── build_population ────────────────────────────────────────────────────────

def _predicate_rag_context(ranked, shape="predicate"):
    """A semantic-scope predicate retrieval: every ranked doc, matches and not."""
    ctx = _exhaustive_rag_context(ranked)
    ctx["tool_responses"][0]["result"]["question_shape"] = shape
    return ctx


def _collection_of(monkeypatch, n):
    async def _fake_get_docs(knowledge_id, metadata_filter=None):
        return [f"id{i}" for i in range(n)]

    monkeypatch.setattr("chat.knowledge_adapter.get_docs", _fake_get_docs)


def test_aggregate_population_is_the_exhaustive_set_not_the_cited_docs():
    cited = {"d1": RESUMES[0]}  # the answer cited one doc; the count covered eight
    population, source, complete = _run(cg.build_population("kb", cited, _exhaustive_rag_context(RESUMES)))
    assert (source, complete, len(population)) == ("exhaustive:aggregate", True, 8)
    fields = population[0]["fields"]
    assert fields["years_of_experience"] == 2
    assert "summary" not in fields and "work_experience" not in fields


def test_predicate_population_is_the_selected_docs_not_every_ranked_one():
    """Measured on the hosted sample: a predicate retrieval returned 21 ranked
    docs and the answer matched 8. Charting the 21 would count non-matches."""
    selected = {d["object_id"]: d for d in RESUMES[:3]}
    population, source, complete = _run(cg.build_population("kb", selected, _predicate_rag_context(RESUMES)))
    assert source == "exhaustive:predicate:selected" and complete is True
    assert [d["object_id"] for d in population] == ["d1", "d2", "d3"]


def test_predicate_with_no_citations_keeps_the_whole_retrieval():
    population, _, complete = _run(cg.build_population("kb", {}, _predicate_rag_context(RESUMES, "selection")))
    assert len(population) == 8 and complete is True


def test_truncated_exhaustive_population_is_incomplete():
    population, _, complete = _run(cg.build_population("kb", None, _exhaustive_rag_context(RESUMES, truncated=True)))
    assert len(population) == 8 and complete is False


def test_retrieved_sample_of_the_collection_is_incomplete(monkeypatch):
    """The hosted 24-resume case: the agent answered from a top-k chunk search
    that reached 10 documents. The chart must not call that the distribution."""
    _collection_of(monkeypatch, 24)
    cited = {d["object_id"]: d for d in RESUMES[:3]}
    population, source, complete = _run(cg.build_population("kb", cited, {"tool_responses": []}))
    assert [d["object_id"] for d in population] == ["d1", "d2", "d3"]
    assert source == "cited:3of24" and complete is False


def test_cited_population_covering_the_collection_is_complete(monkeypatch):
    _collection_of(monkeypatch, 8)
    cited = {d["object_id"]: d for d in RESUMES}
    _, source, complete = _run(cg.build_population("kb", cited, {"tool_responses": []}))
    assert source == "cited:8of8" and complete is True


def test_documents_behind_cited_chunks_join_the_population(monkeypatch):
    """rag_node logged 'Retrieved 0 summaries, 10 chunks': the population is
    the chunks' documents, with their fields fetched."""
    _collection_of(monkeypatch, 2)

    async def _fake_details(knowledge_id, object_ids):
        return [{"object_id": d["object_id"], "metadata": d["metadata"], "file_name": d["file_name"]}
                for d in RESUMES if d["object_id"] in object_ids]

    monkeypatch.setattr("chat.knowledge_adapter.get_document_details", _fake_details)
    chunks = [{"document_id": "d1", "chunk_text": "a"}, {"document_id": "d1", "chunk_text": "b"},
              {"object_id": "d2", "chunk_text": "c"}]
    population, _, complete = _run(cg.build_population("kb", {}, {"tool_responses": []}, chunks))
    assert [d["object_id"] for d in population] == ["d1", "d2"]
    assert population[1]["fields"]["years_of_experience"] == 5 and complete is True


def test_population_fetches_fields_that_did_not_travel_with_retrieval(monkeypatch):
    bare = [{"object_id": d["object_id"], "file_name": d["file_name"]} for d in RESUMES[:2]]
    ctx = _exhaustive_rag_context(bare)
    ctx["summaries"] = {}

    async def _fake_details(knowledge_id, object_ids):
        assert sorted(object_ids) == ["d1", "d2"]
        return [{"object_id": d["object_id"], "metadata": d["metadata"]} for d in RESUMES[:2]]

    monkeypatch.setattr("chat.knowledge_adapter.get_document_details", _fake_details)
    population, _, _ = _run(cg.build_population("kb", None, ctx))
    assert [d["fields"]["years_of_experience"] for d in population] == [2, 5]


# ── aggregate_population: the numbers ──────────────────────────────────────

def test_count_by_exact_numeric_value_matches_ground_truth():
    rows = cg.aggregate_population(_population(), _spec(group_by_field="years_of_experience"))
    assert [(r["Group"], r["Candidates"]) for r in rows] == [
        (1, 1.0), (2, 1.0), (3, 1.0), (5, 2.0), (8, 2.0), (12, 1.0)
    ]
    assert sum(r["Candidates"] for r in rows) == 8


def test_count_by_buckets_counts_each_document_once():
    rows = cg.aggregate_population(
        _population(), _spec(group_by_field="years_of_experience", bucket_edges=[0, 3, 6, 10]))
    assert [(r["Group"], r["Candidates"]) for r in rows] == [
        ("0-2", 2.0), ("3-5", 3.0), ("6-9", 2.0), ("10+", 1.0)
    ]


def test_count_by_category_is_one_bar_per_category():
    """Old output: eight bars, 'Python' three times each valued 3."""
    rows = cg.aggregate_population(_population(), _spec(group_by_field="primary_skill"))
    assert [(r["Group"], r["Candidates"]) for r in rows] == [
        ("Python", 3.0), ("Java", 2.0), ("JavaScript", 2.0), ("Go", 1.0)
    ]


def test_count_over_a_list_field_counts_a_document_once_per_value():
    rows = dict((r["Group"], r["Candidates"]) for r in
                cg.aggregate_population(_population(), _spec(group_by_field="skills")))
    assert rows["Python"] == 3.0 and rows["Java"] == 2.0 and rows["PyTorch"] == 1.0


def test_count_uses_the_extracted_field_not_a_text_guess():
    """Old output re-read seniority from chunk text: three 'Unknown' bars and
    'Mid' once, while the extracted field (and the answer) said mid_level x3."""
    rows = dict((r["Group"], r["Candidates"]) for r in
                cg.aggregate_population(_population(), _spec(group_by_field="seniority_level")))
    assert rows == {"mid_level": 3.0, "junior": 2.0, "senior": 2.0, "principal": 1.0}


def test_missing_values_are_one_honest_bucket_not_dropped():
    docs = RESUMES[:3] + [{"object_id": "d9", "file_name": "x.pdf",
                           "metadata": {"candidate_name": "No Skill", "primary_skill": "unknown"}}]
    rows = cg.aggregate_population(_population(docs), _spec(group_by_field="primary_skill"))
    assert rows[-1] == {"Group": "Not stated", "Candidates": 1.0}
    assert sum(r["Candidates"] for r in rows) == 4


def test_per_document_value_and_group_average():
    pop = _population()
    per_doc = cg.aggregate_population(pop, _spec(
        operation="per_document", group_by_field="candidate_name", value_field="years_of_experience"))
    assert per_doc[0] == {"Group": "Priya Raman", "Candidates": 12.0} and len(per_doc) == 8
    avg = dict((r["Group"], r["Candidates"]) for r in cg.aggregate_population(pop, _spec(
        operation="average", group_by_field="primary_skill", value_field="years_of_experience")))
    assert avg == {"Python": 5.0, "Java": 8.5, "JavaScript": 2.0, "Go": 8.0}


def test_spec_naming_an_absent_field_yields_no_rows():
    assert cg.aggregate_population(_population(), _spec(group_by_field="salary_band")) == []


# ── process_chart_visualization end to end (fake LLM + fake Chart API) ─────

def _capture_chart_api(monkeypatch):
    sent = {}

    async def _fake_call(query, owner_id, csv_data):
        sent["csv"] = csv_data
        return {"type": "chart_response", "chart_generated": True}

    monkeypatch.setattr(cg, "call_chart_api", _fake_call)
    # The external API is the configured renderer here (hosted path); with it
    # unset, chat/local_chart_renderer.py draws instead (test_local_chart_renderer.py).
    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "https://chart.example/api")
    return sent


def test_chart_api_receives_the_aggregated_result_table(monkeypatch):
    fake = _FakeStructuredLLM([_spec(group_by_field="primary_skill",
                                     group_label="Primary skill", value_label="Candidates")])
    monkeypatch.setattr(cg, "_extraction_llm", fake)
    sent = _capture_chart_api(monkeypatch)

    out, usage = _run(cg.process_chart_visualization(
        "count of candidates per primary skill", "owner-1", [], knowledge_id="kb",
        documents=_population()))

    assert out["chart_generated"] is True
    assert _table(sent["csv"]) == [
        {"Primary skill": "Python", "Candidates": "3"},
        {"Primary skill": "Java", "Candidates": "2"},
        {"Primary skill": "JavaScript", "Candidates": "2"},
        {"Primary skill": "Go", "Candidates": "1"},
    ]
    assert usage["input_tokens"] == 10
    # The spec prompt names every field the LLM may choose, with the population size.
    assert "years_of_experience" in fake.prompts[0] and "Documents: 8" in fake.prompts[0]


def test_single_row_result_never_reaches_the_chart_api(monkeypatch):
    one = [RESUMES[0], _resume("d2", "B", 4, "Python", "junior")]
    monkeypatch.setattr(cg, "_extraction_llm", _FakeStructuredLLM([_spec(group_by_field="primary_skill")]))
    sent = _capture_chart_api(monkeypatch)
    out, _ = _run(cg.process_chart_visualization("skills", "owner-1", [], documents=_population(one)))
    assert out is None and "csv" not in sent


def test_not_chartable_spec_skips_the_chart_api(monkeypatch):
    monkeypatch.setattr(cg, "_extraction_llm", _FakeStructuredLLM([_spec(chartable=False)]))
    sent = _capture_chart_api(monkeypatch)
    out, _ = _run(cg.process_chart_visualization("what is Asha's email?", "owner-1", [], documents=_population()))
    assert out is None and "csv" not in sent


def test_text_only_value_is_read_once_per_document(monkeypatch):
    """When the needed value is in no extracted field, the chunks are read per
    DOCUMENT: three chunks of one resume are one extraction item, one row."""
    chunks = [
        {"document_id": "d1", "chunk_text": "Asha. Notice period: 30 days."},
        {"document_id": "d1", "chunk_text": "Asha. Hobbies: chess."},
        {"document_id": "d1", "chunk_text": "Asha. Education: B.Sc."},
        {"document_id": "d2", "chunk_text": "Daniel. Notice period: 60 days."},
    ]

    class _Hybrid(_FakeStructuredLLM):
        def __init__(self, specs, extraction_replies):
            super().__init__(specs)
            self.replies = list(extraction_replies)
            self.extraction_prompts = []

        async def ainvoke(self, messages):
            text = "\n".join(m.content for m in messages)
            if "data extraction expert" in text or "data architect" in text:
                self.extraction_prompts.append(text)
                return SimpleNamespace(content=self.replies.pop(0), usage_metadata=None)
            return await super().ainvoke(messages)

    fake = _Hybrid(
        # The model, seeing the value is in no field, says chartable=false too —
        # measured on gpt-5.4-mini for "count of candidates per primary skill".
        [_spec(chartable=False, needs_document_text=True),
         _spec(group_by_field="notice_period_days", group_label="Notice period (days)")],
        ['["notice_period_days"]',
         '[{"item_number": 1, "notice_period_days": 30}, {"item_number": 2, "notice_period_days": 60}]'],
    )
    monkeypatch.setattr(cg, "_extraction_llm", fake)
    sent = _capture_chart_api(monkeypatch)

    _run(cg.process_chart_visualization(
        "distribution of notice periods", "owner-1", chunks, documents=_population(RESUMES[:2])))

    extraction = fake.extraction_prompts[-1]
    assert extraction.count("--- ITEM") == 2  # two documents, not four chunks
    assert _table(sent["csv"]) == [
        {"Notice period (days)": "30", "Candidates": "1"},
        {"Notice period (days)": "60", "Candidates": "1"},
    ]


# ── call_chart_api identifies this service to the shared Chart API ─────────

class _FakeResponse:
    status = 200

    async def json(self):
        return {"type": "chart_response", "chart_generated": True}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    seen = {}

    def post(self, url, data=None, headers=None):
        _FakeSession.seen = {"url": url, "headers": headers}
        return _FakeResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_chart_api_call_carries_the_service_key_when_configured(monkeypatch):
    """Without it, the hosted Chart API applied the Database Agent's per-owner
    key policy to DI owner ids and refused every DI-only owner ('No OpenAI API
    key is configured for this workspace' — every sample-collection chart)."""
    monkeypatch.setattr(cg.aiohttp, "ClientSession", _FakeSession)
    monkeypatch.setattr(cg, "CHART_API_URL", "https://chart.example/api")
    monkeypatch.setattr(cg, "CHART_SERVICE_KEY", "svc-key")
    out = _run(cg.call_chart_api("q", "samples@example.com", "a,b\nx,1\ny,2\n"))
    assert out["chart_generated"] is True
    assert _FakeSession.seen["headers"] == {"X-Chart-Service-Key": "svc-key"}

    monkeypatch.setattr(cg, "CHART_SERVICE_KEY", "")
    _run(cg.call_chart_api("q", "samples@example.com", "a,b\nx,1\ny,2\n"))
    assert _FakeSession.seen["headers"] is None


def test_only_the_named_values_are_charted_and_a_missing_one_is_zero():
    rows = cg.aggregate_population(_population(), _spec(
        group_by_field="skills", only_values=["python", "Java", "Rust"]))
    assert [(r["Group"], r["Candidates"]) for r in rows] == [
        ("Python", 3.0), ("Java", 2.0), ("Rust", 0.0)
    ]


def test_a_spec_that_admits_its_field_is_only_related_reads_the_documents(monkeypatch):
    """Measured on hosted: 'count of candidates per primary skill' was charted by
    the full skills list ('agile delivery' 8) when the model picked the closest
    field. The spec now says whether its field is exact; when it is not, the
    value is read per document instead."""
    chunks = [{"document_id": d["object_id"], "chunk_text": f"Primary skill: {d['metadata']['primary_skill']}"}
              for d in RESUMES]
    for d in RESUMES:
        d["metadata"].pop("primary_skill", None)

    class _Hybrid(_FakeStructuredLLM):
        async def ainvoke(self, messages):
            text = "\n".join(m.content for m in messages)
            if "data architect" in text:
                return SimpleNamespace(content='["primary_skill"]', usage_metadata=None)
            if "data extraction expert" in text:
                items = text.count("--- ITEM")
                skills = [line.rsplit(": ", 1)[1] for line in text.splitlines() if line.startswith("Content: Primary skill")]
                rows = [{"item_number": i + 1, "primary_skill": s} for i, s in enumerate(skills[:items])]
                return SimpleNamespace(content=json.dumps(rows), usage_metadata=None)
            return await super().ainvoke(messages)

    fake = _Hybrid([
        _spec(group_by_field="skills", asked_dimension="primary skill", dimension_field_is_exact=False),
        _spec(group_by_field="primary_skill", asked_dimension="primary skill", group_label="Primary skill"),
    ])
    monkeypatch.setattr(cg, "_extraction_llm", fake)
    sent = _capture_chart_api(monkeypatch)
    try:
        _run(cg.process_chart_visualization(
            "count of candidates per primary skill", "owner-1", chunks, documents=_population()))
    finally:
        for d, skill in zip(RESUMES, ["Python", "Java", "Python", "JavaScript", "Java", "Python", "JavaScript", "Go"]):
            d["metadata"]["primary_skill"] = skill
    assert _table(sent["csv"]) == [
        {"Primary skill": "Python", "Candidates": "3"},
        {"Primary skill": "Java", "Candidates": "2"},
        {"Primary skill": "JavaScript", "Candidates": "2"},
        {"Primary skill": "Go", "Candidates": "1"},
    ]


def test_a_slow_collection_count_costs_the_chart_not_the_turn(monkeypatch):
    async def _hangs(knowledge_id, metadata_filter=None):
        await asyncio.sleep(30)

    monkeypatch.setattr("chat.knowledge_adapter.get_docs", _hangs)
    monkeypatch.setattr(cg, "_COUNT_TIMEOUT_S", 0.05)
    cited = {d["object_id"]: d for d in RESUMES}
    _, source, complete = _run(cg.build_population("kb", cited, {"tool_responses": []}))
    assert source == "cited:8of?" and complete is False


def test_the_retrieval_layers_population_complete_verdict_wins():
    """retrieve_documents sets population_complete=False for an explicit-ids
    fetch or a partial result even when it is not truncated."""
    ctx = _exhaustive_rag_context(RESUMES)
    ctx["tool_responses"][0]["result"]["population_complete"] = False
    _, _, complete = _run(cg.build_population("kb", None, ctx))
    assert complete is False
    ctx["tool_responses"][0]["result"]["population_complete"] = True
    _, _, complete = _run(cg.build_population("kb", None, ctx))
    assert complete is True


def test_a_complete_filtered_predicate_population_is_charted_whole():
    """The retrieval's filter expressed the condition (since di-metadata-qa
    PR55), so every returned doc satisfies it; the answer keeps all of them and
    the chart must too, whatever subset was cited."""
    ctx = _predicate_rag_context(RESUMES)
    ctx["tool_responses"][0]["result"].update(
        population_complete=True, applied_filter={"years_of_experience": {"$gt": 1}})
    cited = {d["object_id"]: d for d in RESUMES[:2]}
    population, source, complete = _run(cg.build_population("kb", cited, ctx))
    assert len(population) == 8 and source == "exhaustive:predicate" and complete is True


def test_a_kind_only_filter_leaves_the_population_to_the_agents_selection():
    ctx = _predicate_rag_context(RESUMES)
    ctx["tool_responses"][0]["result"].update(
        population_complete=True, applied_filter={"$and": [{"document_type": "resume"}]})
    cited = {d["object_id"]: d for d in RESUMES[:2]}
    population, source, _ = _run(cg.build_population("kb", cited, ctx))
    assert [d["object_id"] for d in population] == ["d1", "d2"] and source == "exhaustive:predicate:selected"


# ── a follow-up answered from an earlier turn's documents still charts ─────
# Hosted 2026-10-25, sample resumes: "Which candidates know Python and AWS and have
# at least 5 years of experience?" (8 candidates), then "plot thier exprince as bar
# chart". The agent reused the 8 documents (reuse gate, no tool call), the turn had
# no fresh evidence, build_population returned nothing, and no chart was drawn
# (knowledge_chat_logs 3419: chart_output null).

def _reuse_turn_context(prior_docs, cited=None):
    """What run_rag_agent leaves behind for a reuse turn: no tool response, no
    summaries or chunks, the earlier turn's documents under prior_turn_docs."""
    ctx = {"tool_responses": [], "summaries": {}, "chunks": [],
           "prior_turn_docs": [{"object_id": d["object_id"], "file_name": d["file_name"],
                                "summary": "resume", "metadata": d["metadata"]} for d in prior_docs]}
    if cited is not None:
        ctx["agent_output"] = {"stop_reason": "answered", "confidence": "high",
                               "relevant_docs": [{"doc_id": oid} for oid in cited]}
    return ctx


def test_reuse_turn_population_is_the_prior_turn_documents(monkeypatch):
    _collection_of(monkeypatch, 24)
    population, source, complete = _run(cg.build_population("kb", {}, _reuse_turn_context(RESUMES[:5]), []))
    assert [d["object_id"] for d in population] == ["d1", "d2", "d3", "d4", "d5"]
    assert population[2]["fields"]["years_of_experience"] == 8
    assert source == "prior_turn:5of24" and complete is False


def test_reuse_turn_population_keeps_only_the_cited_prior_documents(monkeypatch):
    _collection_of(monkeypatch, 24)
    population, _, _ = _run(cg.build_population("kb", {}, _reuse_turn_context(RESUMES[:5], cited=["d2", "d5"]), []))
    assert [d["object_id"] for d in population] == ["d2", "d5"]


def test_fresh_evidence_wins_over_prior_turn_documents(monkeypatch):
    """Same gating as the answer node: a turn that retrieved its own evidence is
    grounded on it, never on an earlier turn's documents."""
    _collection_of(monkeypatch, 24)
    population, source, _ = _run(cg.build_population(
        "kb", {"d7": RESUMES[6]}, _reuse_turn_context(RESUMES[:5]), []))
    assert [d["object_id"] for d in population] == ["d7"] and source.startswith("cited")


def test_reuse_turn_follow_up_charts_one_bar_per_prior_document(monkeypatch):
    """End to end through the turn's shared aggregation and chart_node: the
    follow-up plots each earlier candidate's years, and the answer and the chart
    get the same rows."""
    _collection_of(monkeypatch, 24)
    monkeypatch.setattr(cg, "_extraction_llm", _FakeStructuredLLM([_spec(
        operation="per_document", group_by_field="candidate_name", value_field="years_of_experience",
        group_label="Candidate", value_label="Years of experience")]))
    ctx = _reuse_turn_context(RESUMES[:4], cited=["d1", "d2", "d3", "d4"])
    aggregation = _run(cg.build_structured_aggregation("plot thier exprince as bar chart", "kb", "owner-1", {}, ctx, []))
    assert aggregation["status"] == "ok" and aggregation["source"] == "prior_turn:4of24"
    assert {r["Candidate"]: r["Years of experience"] for r in aggregation["rows"]} == {
        "Asha Verma": 2, "Daniel Okafor": 5, "Mei Lin": 8, "Rafael Gomez": 3}

    sent = _capture_chart_api(monkeypatch)
    monkeypatch.setattr("chat.config.resolve_chat_config",
                        _async_return(SimpleNamespace(include_charts=True)))
    monkeypatch.setattr("utils.setup_env.CHART_API_URL", "http://chart.test")
    ctx["aggregation"] = {k: v for k, v in aggregation.items() if k != "usage"}
    out = _run(chart_node_module.chart_node("plot thier exprince as bar chart", "kb", "owner-1", {},
                                            summaries={}, rag_context=ctx))
    assert out["chart_output"]["chart_generated"] is True
    assert {r["Candidate"]: float(r["Years of experience"]) for r in _table(sent["csv"])} == {
        "Asha Verma": 2.0, "Daniel Okafor": 5.0, "Mei Lin": 8.0, "Rafael Gomez": 3.0}


def _async_return(value):
    async def _f(*args, **kwargs):
        return value
    return _f
