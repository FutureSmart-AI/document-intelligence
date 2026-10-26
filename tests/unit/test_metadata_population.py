"""Metadata-aware QA: the structured population a filter returns must be exact.

These drive the REAL chat retrieval path — knowledge_adapter →
list_documents_by_filter → Qdrant scroll, and retrieve_documents_tool on top of
it — against a real Qdrant (POPULATION_TEST_QDRANT_URL, as CI runs it) or the
in-memory client. Every expected set is computed from the fixture rows below,
never typed by hand.

Each test pins a defect measured on the hosted 24-resume sample (2026-10-25):
  * multi-field filters were OR-ed: {"education_level": "masters",
    "languages": "hindi"} returned 17 documents instead of 4;
  * a range ({"years_of_experience": {"gt": 10}}) and {"$ne": ...} matched
    NOTHING on the filter-only path every count / predicate / ranking uses;
  * $or was cut to its first branch, $not/$nor dropped, $contains turned into
    equality, and an unknown filter key was dropped — each silently;
  * the answer's "how many" number was the LARGEST total of any call, and
    uncited members of a complete population were dropped from the answer;
  * a verifier retry discarded the complete population the first attempt had
    fetched.
"""
import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage
from qdrant_client import AsyncQdrantClient, models

from chat import knowledge_adapter
from chat.rag_agent import agent_execution
from chat.rag_agent.answer_node import _build_context_block
from chat.rag_tools import consolidated_tools
from chat.rag_tools.helpers import UnsupportedMetadataFilter, normalize_metadata_filter
from chat.rag_tools.population import compute_population, field_stats
from utils.files import retrieval_utils

KB = "population-test-kb"
MODEL = "text-embedding-3-small"

ROWS = [
    # name, years, seniority, education, skills, languages, industries
    ("Asha Rao", 8, "senior", "masters", ["python", "aws", "kubernetes"], ["english", "hindi"], ["fintech"]),
    ("Ben Ortiz", 12, "lead", "masters", ["python", "sql"], ["english", "spanish"], ["healthcare"]),
    ("Chen Wei", 14, "principal", "bachelors", ["go", "kubernetes", "terraform"], ["english", "mandarin"], ["fintech"]),
    ("Dara Singh", 1, "junior", "bachelors", ["sql", "python"], ["english", "hindi"], ["retail"]),
    ("Eli Haddad", 9, "senior", "bachelors", ["kubernetes", "terraform", "python"], ["arabic", "english"], ["logistics"]),
    ("Farah Khan", 4, "mid_level", "phd", ["python", "pytorch"], ["english", "urdu", "hindi"], ["ai"]),
    ("Gita Menon", 11, "senior", "masters", ["sql", "product strategy"], ["english", "hindi"], ["edtech"]),
    ("Hugo Brandt", 5, "mid_level", "masters", ["aws", "terraform"], ["german", "english"], ["insurance"]),
]
FOREIGN_NAME = "Foreign Person"


def oid(i):
    return f"00000000-0000-4000-8000-{i:012d}"


def payload(i, row, kb=KB):
    name, years, seniority, education, skills, languages, industries = row
    return {"object_id": oid(i), "knowledge_id": kb, "file_name": f"r{i:02d}.pdf",
            "candidate_name": name, "years_of_experience": years, "seniority_level": seniority,
            "education_level": education, "skills": skills, "languages": languages,
            "industries": industries, "document_type": "resume",
            "summary": f"Resume of {name}."}


PAYLOADS = [payload(i, r) for i, r in enumerate(ROWS, start=1)]
FIELD_TYPES = {"candidate_name": "str", "years_of_experience": "int", "seniority_level": "str",
               "education_level": "str", "skills": "List[str]", "languages": "List[str]",
               "industries": "List[str]", "document_type": "str", "summary": "str"}


def names(pred):
    return sorted(p["candidate_name"] for p in PAYLOADS if pred(p))


class PopulationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = os.getenv("POPULATION_TEST_QDRANT_URL")
        self.client = AsyncQdrantClient(url=url) if url else AsyncQdrantClient(location=":memory:")
        self.collection = "population-test-" + uuid.uuid4().hex
        for attempt in range(30):
            try:
                await self.client.get_collections()
                break
            except Exception:
                if attempt == 29:
                    raise
                await asyncio.sleep(.2)
        await self.client.create_collection(
            self.collection, vectors_config={MODEL: models.VectorParams(size=4, distance=models.Distance.COSINE)})
        points = [models.PointStruct(id=p["object_id"], vector={MODEL: [1., 0., 0., 0.]}, payload=p) for p in PAYLOADS]
        foreign = payload(99, (FOREIGN_NAME, 30, "senior", "masters", ["python"], ["hindi"], ["fintech"]), kb="other-kb")
        points.append(models.PointStruct(id=foreign["object_id"], vector={MODEL: [1., 0., 0., 0.]}, payload=foreign))
        await self.client.upsert(self.collection, points=points, wait=True)
        for field, schema in (("years_of_experience", models.PayloadSchemaType.INTEGER),
                              ("knowledge_id", models.PayloadSchemaType.KEYWORD)):
            await self.client.create_payload_index(self.collection, field_name=field, field_schema=schema, wait=True)

        indexer = SimpleNamespace(client_async=self.client, collection_name=self.collection)
        by_id = {p["object_id"]: p for p in PAYLOADS}

        async def details(knowledge_id, object_ids):
            return [{"object_id": o, "summary": by_id[o]["summary"], "file_name": by_id[o]["file_name"],
                     "metadata": {k: v for k, v in by_id[o].items() if k not in ("object_id", "knowledge_id")}}
                    for o in object_ids if o in by_id]

        self.patches = [
            patch.object(retrieval_utils, "get_document_metadata_enabled", new_callable=AsyncMock, return_value=True),
            patch.object(retrieval_utils, "get_hybrid_search_value", new_callable=AsyncMock, return_value=(False, MODEL)),
            patch.object(retrieval_utils, "_get_document_indexer", new_callable=AsyncMock, return_value=indexer),
            patch.object(consolidated_tools, "knowledge_get_metadata_fields", new_callable=AsyncMock,
                         return_value={"fields": [{"field_name": k, "field_type": v} for k, v in FIELD_TYPES.items()]}),
            patch.object(consolidated_tools, "knowledge_get_document_details", side_effect=details),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        await self.client.delete_collection(self.collection)
        await self.client.close()

    async def listed(self, flt):
        """The filter-only call exhaustive shapes make, with the normalized filter."""
        docs = await knowledge_adapter.list_documents_with_metadata(
            knowledge_id=KB, metadata_filter=normalize_metadata_filter(flt), include_metadata=True, limit=500)
        return sorted(d["metadata"]["candidate_name"] for d in docs)

    async def tool(self, **kwargs):
        args = {"question_shape": "aggregate", "reasoning": "test", "knowledge_id": KB, **kwargs}
        return await consolidated_tools.retrieve_documents_tool.coroutine(**args)

    # ---- filter execution on the exhaustive path -----------------------

    async def test_fields_are_and_ed_not_or_ed(self):
        got = await self.listed({"education_level": "masters", "languages": "hindi"})
        self.assertEqual(got, names(lambda p: p["education_level"] == "masters" and "hindi" in p["languages"]))

    async def test_numeric_ranges_are_applied(self):
        self.assertEqual(await self.listed({"years_of_experience": {"$gt": 10}}),
                         names(lambda p: p["years_of_experience"] > 10))
        self.assertEqual(await self.listed({"years_of_experience": {"$gte": 4, "$lte": 9}}),
                         names(lambda p: 4 <= p["years_of_experience"] <= 9))

    async def test_range_combined_with_an_exact_field(self):
        got = await self.listed({"seniority_level": "senior", "years_of_experience": {"$gte": 9}})
        self.assertEqual(got, names(lambda p: p["seniority_level"] == "senior" and p["years_of_experience"] >= 9))

    async def test_exclusions(self):
        self.assertEqual(await self.listed({"seniority_level": {"$ne": "senior"}}),
                         names(lambda p: p["seniority_level"] != "senior"))
        self.assertEqual(await self.listed({"skills": {"$nin": ["python"]}}),
                         names(lambda p: "python" not in p["skills"]))
        self.assertEqual(await self.listed({"$nor": [{"education_level": "masters"}]}),
                         names(lambda p: p["education_level"] != "masters"))

    async def test_or_across_fields_keeps_every_branch(self):
        got = await self.listed({"$or": [{"industries": "fintech"}, {"years_of_experience": {"$gt": 10}}]})
        self.assertEqual(got, names(lambda p: "fintech" in p["industries"] or p["years_of_experience"] > 10))

    async def test_list_field_must_contain_both_values(self):
        got = await self.listed({"$and": [{"skills": "kubernetes"}, {"skills": "terraform"}]})
        self.assertEqual(got, names(lambda p: {"kubernetes", "terraform"} <= set(p["skills"])))

    async def test_population_never_leaves_the_collection(self):
        got = await self.listed({"skills": "python"})
        self.assertNotIn(FOREIGN_NAME, got)
        self.assertEqual(got, names(lambda p: "python" in p["skills"]))

    async def test_a_filter_the_database_cannot_build_raises_instead_of_matching_nothing(self):
        with self.assertRaises(Exception):
            await knowledge_adapter.list_documents_with_metadata(
                knowledge_id=KB, metadata_filter={"years_of_experience": {"between": [1, 3]}}, include_metadata=True)

    # ---- retrieve_documents tool ---------------------------------------

    async def test_count_is_the_database_total_with_stats(self):
        content, artifact = await self.tool(metadata_filter={"skills": "python"},
                                            stats_fields=["years_of_experience", "education_level"])
        expected = names(lambda p: "python" in p["skills"])
        self.assertEqual(artifact["total_match_count"], len(expected))
        self.assertTrue(artifact["population_complete"])
        years = [p["years_of_experience"] for p in PAYLOADS if "python" in p["skills"]]
        stats = {s["field"]: s for s in artifact["stats"]}
        self.assertEqual(stats["years_of_experience"]["sum"], sum(years))
        self.assertEqual(stats["years_of_experience"]["max"], max(years))
        self.assertIn(f"all {len(expected)} matching documents", content)

    async def test_order_by_ranks_the_complete_population(self):
        _, artifact = await self.tool(question_shape="selection", metadata_filter={"skills": "python"},
                                      order_by={"field": "years_of_experience", "direction": "desc"})
        top = max((p for p in PAYLOADS if "python" in p["skills"]), key=lambda p: p["years_of_experience"])
        self.assertEqual(artifact["ranking"][0]["value"], top["years_of_experience"])
        self.assertEqual(artifact["documents"][0]["object_id"], top["object_id"])

    async def test_unknown_field_is_refused_not_dropped(self):
        content, artifact = await self.tool(metadata_filter={"skills": "python", "seniorty": "senior"})
        self.assertIn("seniorty", artifact.get("error", ""))
        self.assertEqual(artifact["documents"], [])

    async def test_a_whole_collection_ranking_needs_no_filter(self):
        """"Who are the three most experienced" has no condition. The tool used to
        reject a call with no query / filter / ids, so the ranking ran over 0
        documents and the agent fell back to a chunk sample."""
        _, artifact = await self.tool(question_shape="selection",
                                      order_by={"field": "years_of_experience", "direction": "desc"})
        self.assertNotIn("error", artifact)
        self.assertEqual(artifact["total_match_count"], len(PAYLOADS))
        self.assertTrue(artifact["population_complete"])
        expected = sorted(PAYLOADS, key=lambda p: -p["years_of_experience"])[:3]
        self.assertEqual([r["value"] for r in artifact["ranking"][:3]], [p["years_of_experience"] for p in expected])
        self.assertNotIn(FOREIGN_NAME, [d["metadata"]["candidate_name"] for d in artifact["documents"]])

    async def test_an_aggregate_is_never_answered_from_a_chunk_sample(self):
        content, artifact = await consolidated_tools.retrieve_chunks_tool.coroutine(
            question_shape="aggregate", reasoning="t", knowledge_id=KB, query="seniority", num_chunks=10)
        self.assertEqual(artifact["chunks"], [])
        self.assertIn("retrieve_documents", artifact["error"])
        _, artifact = await consolidated_tools.retrieve_chunks_tool.coroutine(
            question_shape="predicate", reasoning="t", knowledge_id=KB, query="x")
        self.assertIn("retrieve_documents", artifact["error"])

    async def test_range_through_the_tool(self):
        _, artifact = await self.tool(question_shape="predicate",
                                      metadata_filter={"years_of_experience": {"$gt": 10}})
        self.assertEqual(sorted(d["metadata"]["candidate_name"] for d in artifact["documents"]),
                         names(lambda p: p["years_of_experience"] > 10))


class NormalizerTests(unittest.TestCase):
    def test_or_is_kept_whole(self):
        out = normalize_metadata_filter({"$or": [{"a": "x"}, {"b": {"$gt": 3}}]})
        self.assertEqual(out, {"$or": [{"a": "x"}, {"b": {"gt": 3}}]})

    def test_negations_are_kept(self):
        self.assertEqual(normalize_metadata_filter({"$not": [{"a": "x"}]}), {"$not": [{"a": "x"}]})
        self.assertEqual(normalize_metadata_filter({"any": [{"key": "a", "value": "x"}, {"key": "b", "value": "y"}]}),
                         {"$or": [{"a": "x"}, {"b": "y"}]})

    def test_contains_is_not_turned_into_equality(self):
        self.assertEqual(normalize_metadata_filter({"title": {"$contains": "engineer"}}),
                         {"title": {"op": "contains", "value": "engineer"}})

    def test_unknown_operator_is_refused(self):
        with self.assertRaises(UnsupportedMetadataFilter):
            normalize_metadata_filter({"title": {"$regex": "eng.*"}})

    def test_disjoint_and_is_flattened_and_same_field_and_is_kept(self):
        self.assertEqual(normalize_metadata_filter({"$and": [{"a": "x"}, {"b": "y"}]}), {"a": "x", "b": "y"})
        self.assertEqual(normalize_metadata_filter({"$and": [{"s": "x"}, {"s": "y"}]}),
                         {"$and": [{"s": "x"}, {"s": "y"}]})


class PopulationMathTests(unittest.TestCase):
    docs = [{"object_id": p["object_id"], "file_name": p["file_name"], "metadata": p} for p in PAYLOADS]

    def test_value_counts_count_documents_once_and_ignore_case(self):
        docs = self.docs + [{"object_id": "x", "metadata": {"languages": ["Hindi", "hindi"]}}]
        stats = field_stats(docs, "languages")
        hindi = next(v for v in stats["value_counts"] if v["value"].lower() == "hindi")
        self.assertEqual(hindi["documents"], sum(1 for p in PAYLOADS if "hindi" in p["languages"]) + 1)

    def test_ties_share_a_rank(self):
        docs = [{"object_id": "a", "metadata": {"y": 5}}, {"object_id": "b", "metadata": {"y": 5}},
                {"object_id": "c", "metadata": {"y": 2}}, {"object_id": "d", "metadata": {}}]
        _, extra = compute_population(docs, order_by={"field": "y", "direction": "desc"})
        self.assertEqual([r["rank"] for r in extra["ranking"]], [1, 1, 3, None])


class AnswerContextTests(unittest.TestCase):
    def _ctx(self, tool_responses, cited):
        summaries = {}
        for tr in tool_responses:
            for d in tr["result"].get("documents", []):
                summaries[d["object_id"]] = {"file_name": d["object_id"], "metadata": d.get("metadata", {})}
        return {"summaries": summaries, "chunks": [], "tool_responses": tool_responses,
                "agent_output": {"relevant_docs": [{"doc_id": c, "why": "x"} for c in cited]}}

    def test_the_latest_population_sets_the_count_not_the_largest(self):
        broad = {"result": {"question_shape": "aggregate", "total_match_count": 24, "population_complete": True,
                            "applied_filter": {"document_type": "resume"},
                            "documents": [{"object_id": f"d{i}"} for i in range(24)]}}
        precise = {"result": {"question_shape": "aggregate", "total_match_count": 18, "population_complete": True,
                              "applied_filter": {"skills": "python"},
                              "documents": [{"object_id": f"d{i}"} for i in range(18)]}}
        block = _build_context_block(self._ctx([broad, precise], cited=[]))
        self.assertIn("total_match_count = 18", block)
        self.assertNotIn("total_match_count = 24", block)

    def test_uncited_members_of_a_complete_population_stay_in_the_answer(self):
        pop = {"result": {"question_shape": "predicate", "total_match_count": 3, "population_complete": True,
                          "applied_filter": {"y": {"gt": 10}},
                          "documents": [{"object_id": f"p{i}", "metadata": {"name": f"N{i}"}} for i in range(3)]}}
        block = _build_context_block(self._ctx([pop], cited=["p0"]))
        for i in range(3):
            self.assertIn(f"p{i}", block)


class VerifierRetryKeepsEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_retry_does_not_discard_the_first_attempts_population(self):
        population = {"documents": [{"object_id": f"d{i}", "metadata": {"n": i}, "summary": "s"} for i in range(5)],
                      "count": 5, "total_match_count": 5, "question_shape": "aggregate",
                      "population_complete": True, "applied_filter": {"skills": "python"}}
        chunks = {"chunks": [{"object_id": "d0", "chunk_id": "c1", "chunk_text": "x"}], "count": 1}

        def attempt(messages, name, call_id, artifact):
            return {"messages": list(messages) + [
                AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": {}}]),
                ToolMessage(content="r", name=name, tool_call_id=call_id, artifact=artifact)]}

        calls = []

        class FakeAgent:
            async def ainvoke(self, state):
                calls.append(1)
                if len(calls) == 1:
                    return attempt(state["messages"], "retrieve_documents", "a1", population)
                return attempt(state["messages"], "retrieve_chunks", "a2", chunks)

        verdict = SimpleNamespace(sufficient=False, confidence="high", missing="m", reason="r", suggested_action="",
                                  model_dump=lambda: {"sufficient": False})
        captured = {}

        async def fake_answer(**kwargs):
            captured.update(kwargs)
            return {"answer": "ok", "usage_metrics": {}}

        with patch.object(agent_execution, "create_rag_agent_graph", new_callable=AsyncMock,
                          return_value=(FakeAgent(), "platform-key")), \
             patch("chat.rag_agent.retrieval_verifier.verify_retrieval", new_callable=AsyncMock, return_value=verdict), \
             patch("chat.rag_agent.retrieval_verifier.format_replan_message", return_value="retry"), \
             patch("chat.rag_agent.answer_node.generate_rag_answer", side_effect=fake_answer):
            await agent_execution.run_rag_agent(
                query="how many", knowledge_id=KB, user_id="u", max_retrieval_retries=1,
                enable_retrieval_verifier=True)
        context = captured["retrieved_context"]
        self.assertEqual(len(calls), 2)
        shapes = [tr["result"].get("question_shape") for tr in context.get("tool_responses", [])
                  if isinstance(tr.get("result"), dict)]
        self.assertIn("aggregate", shapes)
        self.assertEqual(len(context.get("summaries", {})), 5)


class SmallCollectionDirectPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_document_reaches_the_answer_not_only_chunk_hits(self):
        from chat import rag_node

        captured = {}

        async def fake_answer(**kwargs):
            captured.update(kwargs)
            return {"answer": "ok", "usage_metrics": {}}

        async def details(knowledge_id, object_ids):
            return [{"object_id": o, "summary": "s", "metadata": {"n": o}} for o in object_ids]

        ids = [oid(i) for i in range(1, 6)]
        with patch.object(rag_node, "_traced_search_chunks", new_callable=AsyncMock,
                          return_value=[{"object_id": ids[0], "chunk_id": "c", "chunk_text": "t"}]), \
             patch.object(rag_node, "_traced_get_document_details", side_effect=details), \
             patch("chat.knowledge_adapter.get_docs", new_callable=AsyncMock, return_value=ids), \
             patch("chat.rag_agent.answer_node.generate_rag_answer", side_effect=fake_answer):
            await rag_node._small_kb_direct("how many", KB, len(ids))
        self.assertEqual(sorted(captured["retrieved_context"]["summaries"]), ids)


if __name__ == "__main__":
    unittest.main()
