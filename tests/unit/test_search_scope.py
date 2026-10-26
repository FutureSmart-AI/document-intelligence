"""Real Qdrant filtering/fusion and real HTTP route; deterministic test vectors only."""
import unittest
import os
import uuid
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient, models

from routers import search
from utils.qdrant import qdrant_utils as q


KB = 'scope-test-kb'
ANSWER = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'
DELETED = '00000000-0000-4000-8000-000000000003'
UNKNOWN = '00000000-0000-4000-8000-000000000093'
FOREIGN = '00000000-0000-4000-8000-000000000004'
MODEL = 'text-embedding-3-small'


class SearchScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Use the supported server: the client's local fusion mode does not
        # enforce the outer query_filter in the same way as server queries.
        url = os.getenv('SCOPE_TEST_QDRANT_URL', 'http://127.0.0.1:6333')
        if not url.startswith('http://127.0.0.1:'):
            raise ValueError('Scope regressions require a dedicated loopback Qdrant test server')
        self.client = AsyncQdrantClient(url=url)
        self.collection = 'scope-test-' + uuid.uuid4().hex
        for attempt in range(30):
            try:
                await self.client.get_collections()
                break
            except Exception:
                if attempt==29:raise
                await asyncio.sleep(.2)
        await self.client.create_collection(self.collection, vectors_config={MODEL: models.VectorParams(size=4, distance=models.Distance.COSINE)}, sparse_vectors_config={'bm25': models.SparseVectorParams()})
        documents = [(ANSWER, KB, 'The Cedar emergency acknowledgement time is 93 minutes.', [1., 0., 0., 0.]), (OTHER, KB, 'This contract covers office paint colours only; it states no emergency acknowledgement time.', [.8, .2, 0., 0.]), (DELETED, KB, 'Deleted prior policy.', [.7, .3, 0., 0.]), (FOREIGN, 'other-kb', 'Foreign collection secret.', [1., 0., 0., 0.])]
        await self.client.upsert(self.collection, points=[models.PointStruct(id=oid, vector={MODEL: vector, 'bm25': models.SparseVector(indices=[7], values=[1.])}, payload={'object_id':oid, 'knowledge_id':knowledge, 'chunk_text':text}) for oid, knowledge, text, vector in documents])
        await self.client.delete(self.collection, points_selector=models.PointIdsList(points=[DELETED]), wait=True)
        self.indexer = q.MultiQdrantChunkIndexer.__new__(q.MultiQdrantChunkIndexer)
        self.indexer.client_async = self.client
        self.indexer.collection_name = self.collection
        self.indexer.embedding_model_name = MODEL
        self.indexer.embedding_function = SimpleNamespace(embed_query=lambda query:[1., 0., 0., 0.])
        self.indexer.bm25_model = SimpleNamespace(query_embed=lambda query:iter([SimpleNamespace(as_object=lambda:{'indices':[7], 'values':[1.]})]))
        self.patches = [patch.object(search, 'get_hybrid_search_value', new_callable=AsyncMock, return_value=(True, MODEL)), patch.object(search, '_get_chunk_indexer', new_callable=AsyncMock, return_value=self.indexer), patch.object(q, 'get_credential_for_knowledge', new_callable=AsyncMock, return_value=(None, 'platform-key')), patch.object(q, 'send_embedding_event', new_callable=AsyncMock), patch.object(q, 'calculate_embedding_metrics', return_value=(3, 8))]
        for item in self.patches:item.start()
        app = FastAPI()
        app.include_router(search.router)
        route = next(route for route in app.routes if getattr(route, 'path', None)=='/v1/{knowledge_id}/search')
        for dependency in route.dependant.dependencies:app.dependency_overrides[dependency.call] = lambda:'scope-owner'
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://scope.test')

    async def asyncTearDown(self):
        await self.http.aclose()
        for item in reversed(self.patches):item.stop()
        await self.client.delete_collection(self.collection)
        await self.client.close()

    async def request(self, mode, **scope):
        return await self.http.post('/v1/'+KB+'/search', json={'query':'What is the emergency acknowledgement time?', 'num_chunks':5, 'mode':mode, **scope})

    async def test_unscoped_and_null_scope_keep_positive_control_and_collection_isolation(self):
        for mode in ['hybrid', 'non_hybrid']:
            for scope in [{}, {'object_ids':None}]:
                response = await self.request(mode, **scope)
                self.assertEqual(response.status_code, 200)
                self.assertEqual({c['object_id'] for c in response.json()['chunks']}, {ANSWER, OTHER})

    async def test_nonanswering_selected_document_is_the_only_returned_source(self):
        for mode in ['hybrid', 'non_hybrid']:
            response = await self.request(mode, object_ids=[OTHER])
            self.assertEqual(response.status_code, 200)
            self.assertEqual({c['object_id'] for c in response.json()['chunks']}, {OTHER})

    async def test_known_answer_scope_preserves_exact_chunk(self):
        for mode in ['hybrid', 'non_hybrid']:
            response = await self.request(mode, object_ids=[ANSWER])
            self.assertEqual(response.status_code, 200)
            self.assertEqual([c['object_id'] for c in response.json()['chunks']], [ANSWER])
            self.assertIn('93 minutes', response.json()['chunks'][0]['chunk_text'])

    async def test_unknown_deleted_foreign_and_empty_scopes_never_broaden(self):
        for mode in ['hybrid', 'non_hybrid']:
            for ids in [[UNKNOWN], [DELETED], [FOREIGN], []]:
                with self.subTest(mode=mode, ids=ids):
                    response = await self.request(mode, object_ids=ids)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {'chunks':[]})

    async def test_unsupported_filters_and_misspelled_scope_fail_validation(self):
        for name in ['filters', 'metadata_filter', 'object_id']:
            response = await self.request('hybrid', **{name:{'document_type':'contract'}})
            self.assertEqual(response.status_code, 422)
            self.assertTrue(any(error['type']=='extra_forbidden' for error in response.json()['detail']))

    async def test_indexer_document_scope_defaults_to_hard_boundary(self):
        for method in [self.indexer.hybrid_chunk_search, self.indexer.search_chunks_semantic_only]:
            for ids in [[UNKNOWN], []]:
                self.assertEqual(await method(query='emergency', knowledge_id=KB, object_ids=ids, limit=5), [])

    async def test_queryless_document_fetch_keeps_the_same_boundary(self):
        for ids, expected in [([OTHER], {OTHER}), ([UNKNOWN], set()), ([DELETED], set()), ([], set())]:
            response = await self.http.post('/v1/'+KB+'/search', json={'object_ids':ids})
            self.assertEqual(response.status_code, 200)
            self.assertEqual({c['object_id'] for c in response.json()['chunks']}, expected)

    async def test_internal_soft_expansion_remains_an_explicit_opt_in(self):
        for method in [self.indexer.hybrid_chunk_search, self.indexer.search_chunks_semantic_only]:
            chunks = await method(query='emergency', knowledge_id=KB, object_ids=[UNKNOWN], limit=5, allow_scope_expansion=True)
            self.assertEqual({c['object_id'] for c in chunks}, {ANSWER, OTHER})

    async def test_internal_chat_adapter_explicitly_preserves_its_relevance_hint_expansion(self):
        from chat import knowledge_adapter as adapter
        with patch.object(adapter, 'get_hybrid_search_value', new_callable=AsyncMock, return_value=(True, MODEL)), patch.object(adapter, '_get_chunk_indexer', new_callable=AsyncMock, return_value=self.indexer):
            for mode in ['hybrid', 'non_hybrid']:
                chunks = await adapter.search_chunks(KB, query='emergency', object_ids=[UNKNOWN], mode=mode)
                self.assertEqual({c['object_id'] for c in chunks}, {ANSWER, OTHER})


if __name__=='__main__':unittest.main()
