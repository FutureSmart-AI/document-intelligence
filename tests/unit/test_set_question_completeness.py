"""A 'which / who / all' question must not be waved through on a ranked sample.

Hosted resume sample 7c37eb7d, rev 240, run r240r: "Which candidates are
based in or open to Bengaluru?" The agent classified it as `lookup`, made one
semantic retrieve_documents(query=…, limit=20) call with no filter, and that
top-20 held 3 of the 5 Bengaluru resumes. It answered at high confidence with
citations, and the verifier's high-confidence shortcut returned "sufficient"
without an LLM look — so the answer listed 3 of 5. These tests replay that
retrieval through verify_retrieval; the verifier LLM is a fake that records
what it was shown.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from chat.rag_agent import retrieval_verifier as rv
from chat.rag_agent.prompts import RAG_AGENT_CORE


def _doc(i, name, locations):
    return {"object_id": f"d{i}", "metadata": {"candidate_name": name, "locations": locations}, "summary": "s"}


# The r240r top-20 (abridged): 3 Bengaluru matches ranked, 2 never surfaced.
RANKED = [_doc(1, "Tanvi Joshi", ["Ahmedabad"]), _doc(2, "Ishaan Chatterjee", ["kolkata", "bengaluru"]),
          _doc(3, "Anjali Nair", ["Kochi", "Bengaluru"]), _doc(4, "Hannah Becker", ["munich"]),
          _doc(5, "Aarav Mehta", ["Bengaluru"])]


def _context(result):
    return {
        "summaries": {d["object_id"]: d for d in result["documents"]},
        "chunks": [],
        "tool_responses": [{"tool_name": "retrieve_documents", "result": result}],
        "agent_output": {"stop_reason": "answered", "confidence": "high",
                         "relevant_docs": [{"doc_id": "d2", "why": "x"}, {"doc_id": "d3", "why": "x"},
                                           {"doc_id": "d5", "why": "x"}]},
    }


class _VerifierLLM:
    def __init__(self, verdict):
        self.verdict, self.prompts = verdict, []

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages, config=None):
        self.prompts.append("\n".join(str(getattr(m, "content", m)) for m in messages))
        return self.verdict


def _verify(context):
    llm = _VerifierLLM(rv.VerifierResult(sufficient=False, confidence="high", reason="sample",
                                         missing="complete set", suggested_action="predicate filter"))

    async def fake_fallback(fn, **kwargs):
        return SimpleNamespace(value=await fn(llm))

    with patch.object(rv, "run_with_key_fallback", side_effect=fake_fallback):
        verdict = asyncio.run(rv.verify_retrieval(
            query="Which candidates are based in or open to Bengaluru?", retrieved_context=context))
    return verdict, llm


def test_a_confident_answer_from_a_ranked_sample_is_still_checked():
    context = _context({"documents": RANKED, "count": len(RANKED), "question_shape": "lookup",
                        "query": "candidates based in or open to Bengaluru", "applied_filter": None})
    verdict, llm = _verify(context)
    assert llm.prompts, "the verifier waved a ranked sample through without looking"
    assert "RANKED top-K" in llm.prompts[0] or "ranked" in llm.prompts[0].lower()
    assert "COMPLETE matching population" in llm.prompts[0]
    assert verdict.sufficient is False


def test_a_confident_answer_from_a_complete_population_keeps_the_shortcut():
    matches = [d for d in RANKED if "bengaluru" in [x.lower() for x in d["metadata"]["locations"]]]
    context = _context({"documents": matches, "count": len(matches), "question_shape": "predicate",
                        "total_match_count": len(matches), "population_complete": True,
                        "applied_filter": {"locations": "bengaluru"}})
    verdict, llm = _verify(context)
    assert not llm.prompts and verdict.sufficient is True


def test_the_agent_is_told_which_questions_are_not_lookups():
    assert "NOT lookup" in RAG_AGENT_CORE and "which candidates are based in Bengaluru" in RAG_AGENT_CORE
