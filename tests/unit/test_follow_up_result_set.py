"""A follow-up about the previous answer's documents charts exactly those documents.

Regression cover for the model-evaluation case ``fu_python_aws_years_chart``
(evals/model_eval/cases.py): turn 1 "Which candidates know Python and AWS and
have 5+ years of experience?" answers 8 people; turn 2 "chart their years" must
chart those 8. Both failure modes were read from the LangSmith traces of
workflow run 40299739518:

* filter loss (gpt-5.4-mini, gpt-5.4-nano): turn 1 retrieved with
  ``skills $in [python, aws]`` (any of, 14 matches) and the answer narrowed it to
  8; nothing recorded the 8. Turn 2 ran the same retrieval again, cited the
  right 8, and the chart took all 14 because the complete population's filter
  "states the condition".
* dropped member (gpt-5.6-luna, gpt-5.4-mini): turn 2 answered from turn 1's 8
  documents and cited all 8, but mis-copied one object_id; exact-id gating kept
  7 for both the answer and the chart.

Pure unit tests: the chart-spec LLM is a fake, no network or DB.
"""

import asyncio

import chat.chart_generator as cg
import chat.evidence_builder as evidence_builder
from chat.rag_agent import answer_node
from chat.rag_agent.citations import canonicalize_citations, known_doc_ids, resolve_doc_id
from chat.rag_agent.result_set import carried_population, result_ids_from_handles


def _run(coro):
    return asyncio.run(coro)


# (object_id, name, years, skills) — the eval's resume collection, ids from the traces.
MATCHING = [
    ("aba0a5f0-5c47-446c-9051-8dae13ba39e8", "Siddharth Rao", 13, ["python", "aws"]),
    ("b4307e75-657e-49ac-ac0f-052d54a1206a", "Rahul Verma", 5, ["aws", "python"]),
    ("29bdbc78-6c17-49fb-ae3a-3f5c1c3736a1", "Oliver Schmidt", 10, ["python", "aws"]),
    ("4f62c07f-a5e0-4802-b522-bd808e8c6b31", "Aarav Mehta", 8, ["python", "aws"]),
    ("7d96213b-49e6-4c1e-a593-8f611d9d87b4", "Anjali Nair", 6, ["python", "aws"]),
    ("21fc26a1-2347-4b08-b0cd-c4050d6e3a90", "Rohan Deshpande", 15, ["python", "aws"]),
    ("b060bf1b-43f3-4e57-a22b-5ac267495f4e", "Nadia Haddad", 9, ["python", "aws"]),
    ("8f633108-6ac9-4382-aa8d-aac2f0ca28c3", "Arjun Malhotra", 7, ["python", "aws"]),
]
ANY_OF_ONLY = [  # python OR aws, 5+ years — returned by `skills $in [python, aws]`
    ("e1000000-0000-4000-8000-000000000001", "Michael Brennan", 18, ["aws"]),
    ("e1000000-0000-4000-8000-000000000002", "Daniel Okafor", 16, ["python"]),
    ("e1000000-0000-4000-8000-000000000003", "Priya Raghunathan", 12, ["python"]),
    ("e1000000-0000-4000-8000-000000000004", "Fatima Sheikh", 10, ["aws"]),
    ("e1000000-0000-4000-8000-000000000005", "Zoya Ahmed", 7, ["python"]),
    ("e1000000-0000-4000-8000-000000000006", "Emily Carter", 5, ["aws"]),
]
EXPECTED = {name: years for _, name, years, _ in MATCHING}


def _doc(oid, name, years, skills):
    return {"object_id": oid, "file_name": f"{name.lower().replace(' ', '-')}.pdf",
            "summary": f"Resume for {name}",
            "metadata": {"candidate_name": name, "years_of_experience": years, "skills": skills,
                         "document_type": "resume"}}


def _any_of_retrieval():
    """The retrieve_documents call both turns made with the chat-mini/nano models."""
    docs = [_doc(*r) for r in MATCHING + ANY_OF_ONLY]
    return {"tool_name": "retrieve_documents", "result": {
        "documents": docs, "count": len(docs), "question_shape": "predicate",
        "total_match_count": len(docs), "population_complete": True, "truncated": False,
        "applied_filter": {"skills": {"$in": ["python", "aws"]}, "years_of_experience": {"$gte": 5}},
    }}


def _cite(ids):
    return [{"doc_id": oid, "why": "matches"} for oid in ids]


class _FakeSpecLLM:
    def with_structured_output(self, schema, include_raw=False):
        return self

    async def ainvoke(self, messages):
        spec = cg.ChartSpec(chartable=True, reason="r", confidence="high", operation="per_document",
                            group_by_field="candidate_name", value_field="years_of_experience",
                            group_label="Candidate", value_label="Years of experience")
        return {"raw": None, "parsed": spec, "parsing_error": None}


def _collection_of(monkeypatch, n):
    async def _fake_get_docs(knowledge_id, metadata_filter=None):
        return [f"id{i}" for i in range(n)]

    monkeypatch.setattr("chat.knowledge_adapter.get_docs", _fake_get_docs)


def _chart_rows(monkeypatch, rag_context):
    _collection_of(monkeypatch, 24)
    monkeypatch.setattr(cg, "_extraction_llm", _FakeSpecLLM())
    aggregation = _run(cg.build_structured_aggregation("chart their years", "kb", "owner-1", {}, rag_context, []))
    assert aggregation["status"] == "ok", aggregation
    return {r["Candidate"]: r["Years of experience"] for r in aggregation["rows"]}, aggregation["source"]


# ── filter loss: the previous answer's result set is carried, not re-derived ──

def _turn1_persisted_evidence():
    """Turn 1 as the chat-mini run left it: the any-of retrieval (14), the agent
    citing 12, and the answer presenting the 8 that know both."""
    turn1 = {
        "tool_responses": [_any_of_retrieval()],
        "summaries": {d["object_id"]: d for d in (_doc(*r) for r in MATCHING + ANY_OF_ONLY)},
        "agent_output": {"relevant_docs": _cite([r[0] for r in MATCHING] + [r[0] for r in ANY_OF_ONLY[:4]])},
        "answer_result": {"query": "Which candidates know Python and AWS and have 5+ years of experience?",
                          "object_ids": [r[0] for r in MATCHING]},
    }
    return _run(evidence_builder.build_grouped_evidence(turn1))


def test_the_answer_result_set_is_persisted_first_with_its_documents():
    groups = _turn1_persisted_evidence()
    first = groups[0]
    assert first["source"] == "answer_result_set" and first["complete_population"] == 8
    assert [d["object_id"] for d in first["documents"]] == [r[0] for r in MATCHING]
    assert first["documents"][1]["metadata"]["candidate_name"] == "Rahul Verma"
    assert first["query"].startswith("Which candidates know Python and AWS")
    # The retrieval trail is still persisted as before (an exhaustive group is never cited-trimmed).
    assert groups[1]["complete_population"] == 14


def test_follow_up_about_the_previous_result_charts_exactly_that_set_after_a_looser_retrieval(monkeypatch):
    """Turn 2 re-ran turn 1's any-of retrieval (14) and cited the right 8; the
    chart used to plot all 14."""
    prior_evidence = _turn1_persisted_evidence()
    agent_output = {"stop_reason": "answered", "about_previous_result": True,
                    "relevant_docs": _cite([r[0] for r in MATCHING])}
    turn2 = {"tool_responses": [_any_of_retrieval()], "summaries": {}, "chunks": [],
             "agent_output": agent_output,
             "carried_population": carried_population(prior_evidence, agent_output)}
    rows, source = _chart_rows(monkeypatch, turn2)
    assert rows == EXPECTED and source == "previous_result:8"


def test_follow_up_answered_from_prior_documents_charts_the_previous_result(monkeypatch):
    prior_evidence = _turn1_persisted_evidence()
    agent_output = {"stop_reason": "answered", "about_previous_result": True, "relevant_docs": []}
    turn2 = {"tool_responses": [], "summaries": {}, "chunks": [], "agent_output": agent_output,
             "carried_population": carried_population(prior_evidence, agent_output)}
    rows, _ = _chart_rows(monkeypatch, turn2)
    assert rows == EXPECTED


def test_nothing_is_carried_unless_the_agent_says_the_question_is_about_that_set():
    prior_evidence = _turn1_persisted_evidence()
    assert carried_population(prior_evidence, {"about_previous_result": False}) is None
    assert carried_population(prior_evidence, {}) is None
    assert carried_population(prior_evidence[1:], {"about_previous_result": True}) is None


def test_the_answer_is_grounded_on_the_carried_set_not_the_retrieval_it_ran_again():
    prior_evidence = _turn1_persisted_evidence()
    agent_output = {"about_previous_result": True, "relevant_docs": _cite([r[0] for r in MATCHING])}
    turn2 = {"tool_responses": [_any_of_retrieval()],
             "summaries": {d["object_id"]: d for d in (_doc(*r) for r in MATCHING + ANY_OF_ONLY)},
             "chunks": [], "agent_output": agent_output,
             "carried_population": carried_population(prior_evidence, agent_output)}
    text, handles = answer_node._build_context(turn2)
    assert sorted(handles) == sorted(r[0] for r in MATCHING)
    assert "Michael Brennan" not in text and "total_match_count = 14" not in text


# ── the answer names its result set by [Doc N], mapped back to ids ──

def test_answer_handles_cover_fresh_then_prior_documents_in_shown_order():
    fresh = [_doc(*r) for r in MATCHING[:2]]
    prior = [_doc(*r) for r in MATCHING[2:4]]
    ctx = {"summaries": {d["object_id"]: d for d in fresh}, "chunks": [], "tool_responses": [],
           "prior_turn_docs": prior,
           "agent_output": {"relevant_docs": _cite([d["object_id"] for d in fresh + prior])}}
    text, handles = answer_node._build_context(ctx)
    assert handles == [d["object_id"] for d in fresh + prior]
    assert "[Doc 1]" in text and "[Doc 3]" in text and "[Doc 4]" in text
    assert result_ids_from_handles(handles, [4, 1, 1, 9, "x"]) == [handles[3], handles[0]]


# ── dropped member: a mis-copied cited id resolves to the document shown ──

LUNA_MISCOPY = "b4307e75-657e-49ac-0f052d54a1206a"   # run 40299739518, baseline, Rahul Verma
MINI_MISCOPY = "8f633108-6ac9-4382-aa8d-a8c2f0ca28c3"  # run 40299739518, chat-mini, Arjun Malhotra


def _reuse_turn(cited):
    """Turn 2 answered from turn 1's 8 documents with no tool call."""
    return {"tool_responses": [], "summaries": {}, "chunks": [],
            "prior_turn_docs": [_doc(*r) for r in MATCHING],
            "agent_output": {"stop_reason": "answered", "relevant_docs": _cite(cited)}}


def test_both_observed_miscopies_resolve_to_the_document_the_agent_was_shown():
    known = [r[0] for r in MATCHING + ANY_OF_ONLY]
    assert resolve_doc_id(LUNA_MISCOPY, known) == MATCHING[1][0]
    assert resolve_doc_id(MINI_MISCOPY, known) == MATCHING[7][0]
    assert resolve_doc_id(" [" + MATCHING[0][0].upper() + "] ", known) == MATCHING[0][0]


def test_an_id_that_could_mean_two_documents_or_none_is_not_guessed():
    known = ["11111111-aaaa-4000-8000-000000000001", "11111111-bbbb-4000-8000-000000000002"]
    assert resolve_doc_id("11111111-cccc-4000-8000-000000000003", known) is None
    assert resolve_doc_id("22222222-cccc-4000-8000-000000000003", known) is None
    # head names one document, tail names another: ambiguous
    assert resolve_doc_id("11111111-aaaa-4000-8000-000000000002",
                          ["11111111-aaaa-4000-8000-000000000009", "33333333-aaaa-4000-8000-000000000002"]) is None


def test_reuse_turn_with_a_miscopied_citation_charts_all_eight(monkeypatch):
    cited = [r[0] for r in MATCHING]
    cited[1], cited[7] = LUNA_MISCOPY, MINI_MISCOPY
    ctx = _reuse_turn(cited)
    stats = canonicalize_citations(ctx["agent_output"], known_doc_ids(ctx))
    assert stats == {"repaired": 2, "unresolved": 0}
    assert ctx["agent_output"]["relevant_docs"][1]["cited_as"] == LUNA_MISCOPY
    rows, source = _chart_rows(monkeypatch, ctx)
    assert rows == EXPECTED and source.startswith("prior_turn:8")
    # The answer node's grounding gate keeps all eight as well.
    _, handles = answer_node._build_context(ctx)
    assert sorted(handles) == sorted(cited_id for cited_id, *_ in MATCHING)
