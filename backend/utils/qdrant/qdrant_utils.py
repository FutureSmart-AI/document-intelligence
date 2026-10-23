import time
import requests
import json
from datetime import datetime
# from uuid import uuid4
from utils.logger import logger
from utils.tracing import tracer, track_time
import utils.setup_env as setup_env
from qdrant_client import QdrantClient, AsyncQdrantClient
# from qdrant_client.http.models import VectorParams, Distance, SparseVectorParams, Modifier
from qdrant_client.http import models
from langchain_qdrant import QdrantVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from qdrant_client.http.models import Filter, FieldCondition, MatchValue, Range, MatchAny, Prefetch, FusionQuery, SparseVector
from langchain_openai import OpenAIEmbeddings
# from sentence_transformers import SentenceTransformer
from fastembed import SparseTextEmbedding
# from fastapi import HTTPException, status
from typing import List, Union, Optional
from utils.db.knowledge_setting_ops import get_knowledge_config
from utils.qdrant.usage_tracking import (
    calculate_embedding_metrics,
    send_embedding_event,
    _get_embedding_provider_and_model,
)
from utils.usage.credentials import get_credential_for_knowledge
from utils.models.embedding_models import EMBEDDING_MODELS
import asyncio

OPENAI_API_KEY = setup_env.OPENAI_API_KEY
FASTAPI_SECRET_KEY = setup_env.FASTAPI_SECRET_KEY
qdrant_url = setup_env.qdrant_url
qdrant_api_key = setup_env.qdrant_api_key
USAGE_TRACKING_BASE_URL = setup_env.USAGE_TRACKING_BASE_URL if hasattr(setup_env, 'USAGE_TRACKING_BASE_URL') else None


document_indexer = None
chunk_indexer = None

async def initialize_qdrant_document_indexer(embedding_model):
    global document_indexer
    document_indexer = MultiQdrantDocumentIndexer(embedding_model)
    await document_indexer.initialize_qdrant_async()
    return document_indexer

async def initialize_qdrant_document_chunk_indexer(embedding_model):
    global chunk_indexer
    chunk_indexer = MultiQdrantChunkIndexer(embedding_model)
    await chunk_indexer.initialize_qdrant_async()
    return chunk_indexer

def initialize_qdrant_document_indexer_sync():
    global document_indexer
    document_indexer = MultiQdrantDocumentIndexer()
    document_indexer.initialize_qdrant()
    return document_indexer

def initialize_qdrant_document_chunk_indexer_sync():
    global chunk_indexer
    chunk_indexer = MultiQdrantChunkIndexer()
    chunk_indexer.initialize_qdrant()
    return chunk_indexer

class MultiQdrantDocumentIndexer:
    def __init__(self, embedding_model):
        self.embedding_model_name = embedding_model
        self.model_config = EMBEDDING_MODELS.get(embedding_model)
        if not self.model_config:
            raise ValueError(f"Unsupported embedding model: {embedding_model}")
        if self.model_config["type"] == "sentence_transformer":
            self.embedding_function = HuggingFaceEmbeddings(model_name = self.model_config["model_name"])
        elif self.model_config["type"] == "openai":
            self.embedding_function = OpenAIEmbeddings(model=self.model_config["model_name"], api_key=OPENAI_API_KEY)
        else:
            raise ValueError(f"Unsupported model type: {self.model_config['type']}")
        
        
        # Initialize BM25 sparse embeddings model for hybrid search
        self.bm25_model = SparseTextEmbedding("Qdrant/bm25")
        self.collection_name = f"agentic-rag-documents-{embedding_model}"
        self.client = QdrantClient(    
            url = qdrant_url, 
            api_key = qdrant_api_key,
            timeout=300,
            check_compatibility=False
            
            )
        self.client_async = AsyncQdrantClient(    
            url = qdrant_url, 
            api_key = qdrant_api_key,
            timeout=300,
            check_compatibility=False
            )
        
    def initialize_qdrant(self):
        try:
            collections = self.client.get_collections()
            # print("collections: ",collections)
            if self.collection_name not in [col.name for col in collections.collections]:
                # Create collection with both dense and sparse vector support
                dense_vector_params = models.VectorParams(
                    size=self.model_config["dimension"],
                    distance=models.Distance.COSINE)
                sparse_vector_params = models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )
                
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={self.embedding_model_name: dense_vector_params},
                    sparse_vectors_config={"bm25": sparse_vector_params}
                )
                logger.info(f"Hybrid Collection Created with name {self.collection_name}")
            self.vectors = QdrantVectorStore.from_existing_collection(
                    collection_name=self.collection_name,
                    embedding=self.embedding_function,
                    url=qdrant_url,
                    vector_name=self.embedding_model_name,  # Specify the dense vector name
                    api_key= qdrant_api_key
                )   
            logger.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logger.error(f"Timeout occurred: {e}")
            logger.info("Attempting to reinitialize Qdrant connection...")
            time.sleep(5)  # Wait for 5 seconds before retrying
            self.initialize_qdrant()  # Recursive call to retry initialization

    async def initialize_qdrant_async(self):
        try:
            collections = await self.client_async.get_collections()
            # print("collections: ",collections)
            if self.collection_name not in [col.name for col in collections.collections]:
                # Create collection with both dense and sparse vector support
                dense_vector_params = models.VectorParams(
                    size=self.model_config["dimension"],
                    distance=models.Distance.COSINE)
                sparse_vector_params = models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )

                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={self.embedding_model_name: dense_vector_params},
                    sparse_vectors_config={"bm25": sparse_vector_params}
                )
                logger.info(f"Hybrid Collection Created with name {self.collection_name}")
            self.vectors = QdrantVectorStore.from_existing_collection(
                    collection_name=self.collection_name,
                    embedding=self.embedding_function,
                    url=qdrant_url,
                    vector_name=self.embedding_model_name,  # Specify the dense vector name
                    api_key= qdrant_api_key
                )
            # Payload index on source_id — enables fast filter-based scroll/delete; idempotent
            await self.client_async.create_payload_index(
                collection_name=self.collection_name,
                field_name="source_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            logger.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logger.error(f"Timeout occurred: {e}")
            logger.info("Attempting to reinitialize Qdrant connection...")
            time.sleep(5)  # Wait for 5 seconds before retrying
            self.initialize_qdrant()  # Recursive call to retry initialization

    async def delete_document_by_source_id(self, knowledge_id: str, source_id: str):
        """Delete the doc-level point for a source document before reindexing."""
        try:
            logger.info(f"vDB: deleting stale doc source_id='{source_id}' knowledge_id='{knowledge_id}'")
            await self.client_async.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(filter=Filter(must=[
                    FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id))),
                    FieldCondition(key="source_id", match=MatchValue(value=str(source_id))),
                ])),
                wait=True,
            )
            logger.info(f"vDB: deleted stale doc for source_id='{source_id}'")
        except Exception as e:
            logger.error(f"vDB: delete_document_by_source_id failed source_id='{source_id}': {e}")
            raise

    @track_time("API: Qdrant - Delete Document")
    async def delete_document(self, object_id: Union[str, List[str]], knowledge_id: Optional[str] = None):
        """
        Delete a document or multiple documents from the Qdrant collection by payload filter.
        
        Parameters:
        - object_id (Union[str, List[str]]): The payload object_id value(s) of the document(s) to delete
        - knowledge_id (Optional[str]): Optional knowledge_id guard for safer deletion
        
        Returns:
        - bool: True if deletion was successful
        """
        try:
            filter_conditions = []

            if isinstance(object_id, list):
                filter_conditions.append(
                    FieldCondition(
                        key="object_id",
                        match=MatchAny(any=object_id)
                    )
                )
                doc_id_display = f"{len(object_id)} documents"
            else:
                filter_conditions.append(
                    FieldCondition(
                        key="object_id",
                        match=MatchValue(value=object_id)
                    )
                )
                doc_id_display = object_id

            if knowledge_id:
                filter_conditions.append(
                    FieldCondition(
                        key="knowledge_id",
                        match=MatchValue(value=knowledge_id)
                    )
                )

            await self.client_async.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=Filter(must=filter_conditions)
                ),
                wait=True,
            )

            logger.info(f"Successfully deleted {doc_id_display}")
            return True
        except Exception as e:
            logger.error(f"Error deleting document(s) {object_id} from QdrantDB: {e}")
            raise

class MultiQdrantChunkIndexer:
    def __init__(self, embedding_model):
        self.embedding_model_name = embedding_model
        self.model_config = EMBEDDING_MODELS.get(embedding_model)
        if not self.model_config:
            raise ValueError(f"Unsupported embedding model: {embedding_model}")
        if self.model_config["type"] == "sentence_transformer":
            self.embedding_function = HuggingFaceEmbeddings(model_name = self.model_config["model_name"])
        elif self.model_config["type"] == "openai":
            self.embedding_function = OpenAIEmbeddings(model=self.model_config["model_name"], api_key=OPENAI_API_KEY)
        else:
            raise ValueError(f"Unsupported model type: {self.model_config['type']}")
        
        # Initialize BM25 sparse embeddings model for hybrid search
        self.bm25_model = SparseTextEmbedding("Qdrant/bm25")
        self.collection_name = f"agentic-rag-chunks-{embedding_model}"
        self.client = QdrantClient(    
            url = qdrant_url, 
            api_key = qdrant_api_key,
            timeout=300
            )
        self.client_async = AsyncQdrantClient(    
            url = qdrant_url, 
            api_key = qdrant_api_key,
            timeout=300
            )
        
    def initialize_qdrant(self):
        try:
            collections = self.client.get_collections()
            if self.collection_name not in [col.name for col in collections.collections]:
                # Create collection with both dense and sparse vector support
                dense_vector_params = models.VectorParams(
                    size=self.model_config["dimension"],
                    distance=models.Distance.COSINE)
                sparse_vector_params = models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )
                
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={self.embedding_model_name: dense_vector_params},
                    sparse_vectors_config={"bm25": sparse_vector_params}
                )
                logger.info(f"Hybrid Collection Created with name {self.collection_name}")
            self.vectors = QdrantVectorStore.from_existing_collection(
                    collection_name=self.collection_name,
                    embedding=self.embedding_function,
                    url=qdrant_url,
                    vector_name=self.embedding_model_name,  # Specify the dense vector name
                    api_key= qdrant_api_key
                )
            logger.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logger.error(f"Timeout occurred: {e}")
            logger.info("Attempting to reinitialize Qdrant connection...")
            time.sleep(5)  # Wait for 5 seconds before retrying
            self.initialize_qdrant()  # Recursive call to retry initialization

    async def initialize_qdrant_async(self):
        try:
            collections = await self.client_async.get_collections()
            if self.collection_name not in [col.name for col in collections.collections]:
                # Create collection with both dense and sparse vector support
                dense_vector_params = models.VectorParams(
                    size=self.model_config["dimension"],
                    distance=models.Distance.COSINE)
                sparse_vector_params = models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )

                await self.client_async.create_collection(
                    collection_name=self.collection_name,
                    vectors_config={self.embedding_model_name: dense_vector_params},
                    sparse_vectors_config={"bm25": sparse_vector_params}
                )
                logger.info(f"Hybrid Collection Created with name {self.collection_name}")
            self.vectors = QdrantVectorStore.from_existing_collection(
                    collection_name=self.collection_name,
                    embedding=self.embedding_function,
                    url=qdrant_url,
                    vector_name=self.embedding_model_name,  # Specify the dense vector name
                    api_key= qdrant_api_key
                )
            # Payload index on source_id — enables fast filter-based scroll/delete; idempotent
            await self.client_async.create_payload_index(
                collection_name=self.collection_name,
                field_name="source_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            logger.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logger.error(f"Timeout occurred: {e}")
            logger.info("Attempting to reinitialize Qdrant connection...")
            time.sleep(5)  # Wait for 5 seconds before retrying
            self.initialize_qdrant()  # Recursive call to retry initialization

    async def get_source_last_modified(self, knowledge_id: str, source_id: str) -> Optional[int]:
        """
        Scroll 1 chunk for this knowledge_id + source_id, return stored last_modified (epoch ms).
        Returns None if no matching point found (doc not yet indexed).
        """
        results, _ = await self.client_async.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id))),
                FieldCondition(key="source_id", match=MatchValue(value=str(source_id))),
            ]),
            limit=1,
            with_payload=["last_modified"],
            with_vectors=False,
        )
        return results[0].payload.get("last_modified") if results else None

    async def delete_chunks_by_source_id(self, knowledge_id: str, source_id: str):
        """Delete all chunks for a source document before reindexing."""
        try:
            logger.info(f"vDB: deleting stale chunks source_id='{source_id}' knowledge_id='{knowledge_id}'")
            await self.client_async.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(filter=Filter(must=[
                    FieldCondition(key="knowledge_id", match=MatchValue(value=str(knowledge_id))),
                    FieldCondition(key="source_id", match=MatchValue(value=str(source_id))),
                ])),
                wait=True,
            )
            logger.info(f"vDB: deleted stale chunks for source_id='{source_id}'")
        except Exception as e:
            logger.error(f"vDB: delete_chunks_by_source_id failed source_id='{source_id}': {e}")
            raise

    @track_time("API: Qdrant - Delete Chunks")
    async def delete_chunks(self, object_id: Union[str, List[str]], knowledge_id: Optional[str] = None):
        """
        Delete all chunks associated with a document or multiple documents.
        
        Parameters:
        - object_id (Union[str, List[str]]): A single document ID or a list of document IDs to delete chunks for
        - knowledge_id (Optional[str]): Optional knowledge_id guard for safer deletion
        
        Returns:
        - bool: True if deletion was successful
        """
        try:
            # Create filter based on whether object_id is a single ID or a list
            if isinstance(object_id, list):
                filter_condition = Filter(
                    must=[
                        FieldCondition(
                            key="object_id",
                            match=MatchAny(any=object_id)
                        )
                    ]
                )
                doc_id_display = f"{len(object_id)} documents"
            else:
                filter_condition = Filter(
                    must=[
                        FieldCondition(
                            key="object_id",
                            match=MatchValue(value=object_id)
                        )
                    ]
                )
                doc_id_display = object_id

            if knowledge_id:
                filter_condition.must.append(
                    FieldCondition(
                        key="knowledge_id",
                        match=MatchValue(value=knowledge_id)
                    )
                )

            # Delete all chunks with the specified object_id(s)
            await self.client_async.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(filter=filter_condition),
                wait=True,
            )
            
            logger.info(f"Successfully deleted all chunks for {doc_id_display}")
            return True
        except Exception as e:
            logger.error(f"Error deleting chunks for document ID {object_id}: {e}")
            raise
        
    
    def get_metadata_from_chunks(self, chunk_ids: list):
        """
        Retrieve topics, keywords, and entities for each chunk ID,
        returned as a dict with chunk_id as the key.

        Returns:
        - dict: {
            "chunk_id_1": {
                "chunk_topic": "...",
                "chunk_keywords": [...],
                "chunk_entities": [...],
                "object_id": "..."
            },
            ...
        }
        """
        try:
            if not chunk_ids:
                logger.warning("No chunk IDs provided.")
                return {}

            response = self.client.retrieve(
                collection_name=self.collection_name,
                ids=chunk_ids,
                with_payload=True
            )

            chunk_metadata = {}

            for point in response:
                payload = point.payload or {}

                chunk_metadata[str(point.id)] = {
                    "chunk_topic": payload.get("chunk_topic"),
                    "chunk_keywords": payload.get("chunk_keywords", []),
                    "chunk_entities": payload.get("chunk_entities", []),
                    "object_id": payload.get("object_id")
                }

            return chunk_metadata

        except Exception as e:
            logger.error(f"Error retrieving metadata from chunk IDs: {e}")
            raise

    @track_time("API: Qdrant - Get All Chunks")
    async def get_all_chunks_for_document(self, object_id: Union[str, List[str]], knowledge_id: Optional[str] = None) -> List[dict]:
        """
        Retrieves all chunks for a specified document ID or list of document IDs from the Qdrant database.
        
        Parameters:
        - object_id (Union[str, List[str]]): A single document ID or a list of document IDs to retrieve chunks for
        - knowledge_id (Optional[str]): Knowledge ID to filter chunks by
        
        Returns:
        - List[dict]: A list of chunk data dictionaries
        """
        try:
            # Create filter conditions
            filter_conditions = []
            
            # Add object_id filter
            if isinstance(object_id, list):
                # For multiple document IDs, use MatchAny
                filter_conditions.append(
                    FieldCondition(
                        key="object_id",
                        match=MatchAny(any=object_id)
                    )
                )
                doc_id_display = f"{len(object_id)} documents"
            else:
                # For a single document ID, use MatchValue
                filter_conditions.append(
                    FieldCondition(
                        key="object_id",
                        match=MatchValue(value=object_id)
                    )
                )
                doc_id_display = object_id
            
            # Add knowledge_id filter if provided
            if knowledge_id:
                filter_conditions.append(
                    FieldCondition(
                        key="knowledge_id",
                        match=MatchValue(value=knowledge_id)
                    )
                )
            
            filter_condition = Filter(must=filter_conditions)
            
            # Get all chunks (no vector search, just filtering by object_id)
            with tracer.start_as_current_span("vDB: Scroll All Chunks For Document"):
                response = await self.client_async.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=filter_condition
                    # limit=100  # Increase limit to handle multiple documents
                )
            
            chunks = []
            # Handle the response format - either tuple or object with points attribute
            if isinstance(response, tuple) and len(response) > 0:
                records = response[0]
                for point in records:
                    chunk_data = point.payload if hasattr(point, 'payload') else {}
                    # Add vector ID if needed
                    chunk_data["id"] = point.id
                    chunks.append(chunk_data)
            elif hasattr(response, 'points'):
                for point in response.points:
                    chunk_data = point.payload if hasattr(point, 'payload') else {}
                    # Add vector ID if needed
                    chunk_data["id"] = point.id
                    chunks.append(chunk_data)
            
            logger.info(f"Retrieved {len(chunks)} chunks for {doc_id_display}")
            return chunks
            
        except Exception as e:
            logger.error(f"Error getting chunks for document ID {object_id}: {e}")
            # Return an empty list rather than raising to make the function more robust
            return []

    @track_time("API: Qdrant - Search Relevant Chunks")
    async def search_relevant_chunks_for_query(self, object_ids: Optional[Union[str, List[str]]], query: str, user_id: Optional[str] = None, knowledge_id: Optional[str] = None, limit: int = 1000) -> List[dict]:
        """
        Performs hybrid semantic search to find the most relevant chunks for a query.
        Can search within specific documents or across the entire knowledge base if no object_ids provided.
        Returns all chunks with relevancy score > 0.5 (50%).

        Parameters:
        - object_ids (Optional[Union[str, List[str]]]): Document IDs to search within (optional)
        - query (str): The user's query to find relevant chunks for
        - user_id (Optional[str]): User ID to filter chunks by
        - knowledge_id (Optional[str]): Knowledge ID to filter chunks by
        - limit (int): Maximum number of chunks to retrieve for filtering (default: 1000)

        Returns:
        - List[dict]: A list of the most relevant chunk data dictionaries
        """
        try:
            # Handle object_ids parameter
            if object_ids is None:
                # Search across entire knowledge base
                doc_id_display = "entire knowledge base"
                logger.info(f"Searching for relevant chunks across {doc_id_display} for query: {query}")
            elif isinstance(object_ids, str):
                object_ids = [object_ids]
                doc_id_display = object_ids[0]
                logger.info(f"Searching for relevant chunks across {doc_id_display} for query: {query}")
            else:
                doc_id_display = f"{len(object_ids)} documents"
                logger.info(f"Searching for relevant chunks across {doc_id_display} for query: {query}")

            # Resolve owner credential to use owner's API key if available
            provider, model_name = _get_embedding_provider_and_model(self.embedding_model_name)
            owner_api_key, key_type = None, "platform-key"
            if knowledge_id:
                owner_api_key, key_type = await get_credential_for_knowledge(knowledge_id, provider)

            # Swap to owner API key for OpenAI embeddings if available
            original_api_key = None
            if owner_api_key and isinstance(self.embedding_function, OpenAIEmbeddings):
                original_api_key = self.embedding_function.openai_api_key
                self.embedding_function.openai_api_key = owner_api_key
                logger.info(f"Using owner API key for embedding generation, knowledge_id={knowledge_id}")

            try:
                # Generate dense embedding for the query (with tracking)
                embedding_start_time = time.time()
                dense_query_embedding = self.embedding_function.embed_query(query)

                # Generate sparse embedding for the query
                sparse_query_obj = next(self.bm25_model.query_embed(query))
                sparse_query_embedding = sparse_query_obj.as_object()
                embedding_duration_ms = (time.time() - embedding_start_time) * 1000
            finally:
                # Restore platform API key
                if original_api_key is not None:
                    self.embedding_function.openai_api_key = original_api_key

            # Calculate embedding metrics
            embedding_tokens, char_count = calculate_embedding_metrics(
                text=query,
                embedding_model=self.embedding_model_name,
                embedding_function=self.embedding_function
            )

            # Send embedding tracking event
            if knowledge_id:
                await send_embedding_event(
                    provider=provider,
                    model=model_name,
                    embedding_tokens=embedding_tokens,
                    char_count=char_count,
                    duration_ms=int(embedding_duration_ms),
                    knowledge_id=knowledge_id,
                    object_id=object_ids[0] if isinstance(object_ids, list) and len(object_ids) == 1 else None,
                    key_type=key_type
                )

            # Create filter to limit search to user_id, knowledge_id and optionally object_ids
            filter_conditions = []

            # Add object_id filter only if object_ids is provided
            if object_ids is not None:
                filter_conditions.append(
                    FieldCondition(key="object_id", match=MatchAny(any=object_ids))
                )

            # if user_id:
            #     filter_conditions.append(
            #         FieldCondition(key="user_id", match=MatchValue(value=user_id))
            #     )

            if knowledge_id:
                filter_conditions.append(
                    FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id))
                )

            filter_condition = Filter(must=filter_conditions) if filter_conditions else None

            # Define prefetch limit for hybrid search
            prefetch_limit = limit

            # Set up prefetch for both dense and sparse vectors
            prefetch = [
                models.Prefetch(
                    query=dense_query_embedding,
                    using=self.embedding_model_name,
                    limit=prefetch_limit
                ),
                models.Prefetch(
                    query=models.SparseVector(**sparse_query_embedding),
                    using="bm25",
                    limit=prefetch_limit
                )
            ]

            # Use FusionQuery (Reciprocal Rank Fusion) to fuse the prefetch results
            fusion_query = FusionQuery(fusion=models.Fusion.RRF)

            # Perform hybrid semantic search with a higher limit to find all relevant chunks
            with tracer.start_as_current_span("vDB: Query Points Hybrid Search"):
                search_results = await self.client_async.query_points(
                    collection_name=self.collection_name,
                    prefetch=prefetch,
                    query=fusion_query,
                    query_filter=filter_condition,
                    with_payload=True,
                    limit=limit
                )

            results = search_results.points

            # Process results and filter by relevance score (greater than 0.5 or 50%)
            chunks = []
            relevant_count = 0
            total_count = len(results)

            for point in results:
                # Skip chunks with relevance score below 0.5 (50%)
                # if point.score < 0.5:
                #     continue

                relevant_count += 1
                chunk_data = point.payload if hasattr(point, 'payload') else {}
                # Add vector ID, document ID and relevance score
                chunk_data["id"] = point.id
                chunk_data["object_id"] = chunk_data.get("object_id")  # Ensure object_id is included
                chunk_data["relevance_score"] = point.score
                chunks.append(chunk_data)

            logger.info(f"Found {relevant_count} chunks with relevancy > 50% out of {total_count} chunks searched across {doc_id_display} using hybrid search")
            return chunks

        except Exception as e:
            logger.error(f"Error searching relevant chunks for query '{query}' in documents {object_ids}: {e}")
            # Return empty list rather than raising to make the function more robust
            return []
    
    @track_time("API: Qdrant - Semantic Search")
    async def search_chunks_semantic_only(self, query: str, knowledge_id: str, user_id: Optional[str] = None, object_ids: Optional[Union[str, List[str]]] = None, limit: int = 3, *, allow_scope_expansion: bool = False) -> List[dict]:
        """
        Performs pure semantic search (dense vectors only) across chunks for a knowledge base.
        Can search within specific documents or across the entire knowledge base if no object_ids provided.
        Returns top chunks by semantic similarity.
        Document scope is hard by default. Only internal relevance-hint callers
        may explicitly opt into expansion when their selected documents are empty.

        Parameters:
        - query (str): The user's query to find semantically similar chunks for
        - knowledge_id (str): Knowledge ID to filter chunks by
        - user_id (Optional[str]): User ID to filter chunks by
        - object_ids (Optional[Union[str, List[str]]]): Document IDs to search within (optional)
        - limit (int): Maximum number of top chunks to return (default: 3)

        Returns:
        - List[dict]: A list of the most semantically similar chunk data dictionaries, each containing:
            - id: The chunk ID
            - object_id: The ID of the document this chunk belongs to
            - chunk_text: The text content of the chunk
            - relevance_score: The semantic relevance score of the chunk to the query
            - Additional metadata from the chunk payload
        """
        try:
            if object_ids == [] and not allow_scope_expansion:
                return []
            # Handle object_ids parameter
            if object_ids is None:
                doc_id_display = "entire knowledge base"
                logger.info(f"Performing semantic-only search across {doc_id_display} for knowledge_id: {knowledge_id}, query: {query}")
            elif isinstance(object_ids, str):
                object_ids = [object_ids]
                doc_id_display = object_ids[0]
                logger.info(f"Performing semantic-only search across {doc_id_display} for knowledge_id: {knowledge_id}, query: {query}")
            else:
                doc_id_display = f"{len(object_ids)} documents"
                logger.info(f"Performing semantic-only search across {doc_id_display} for knowledge_id: {knowledge_id}, query: {query}")

            # Resolve owner credential to use owner's API key if available
            provider, model_name = _get_embedding_provider_and_model(self.embedding_model_name)
            owner_api_key, key_type = await get_credential_for_knowledge(knowledge_id, provider)

            # Swap to owner API key for OpenAI embeddings if available
            original_api_key = None
            if owner_api_key and isinstance(self.embedding_function, OpenAIEmbeddings):
                original_api_key = self.embedding_function.openai_api_key
                self.embedding_function.openai_api_key = owner_api_key
                logger.info(f"Using owner API key for embedding generation, knowledge_id={knowledge_id}")

            try:
                # Generate embedding for the query (with tracking)
                embedding_start_time = time.time()
                query_embedding = self.embedding_function.embed_query(query)
                embedding_duration_ms = (time.time() - embedding_start_time) * 1000
            finally:
                # Restore platform API key
                if original_api_key is not None:
                    self.embedding_function.openai_api_key = original_api_key

            # Calculate embedding metrics
            embedding_tokens, char_count = calculate_embedding_metrics(
                text=query,
                embedding_model=self.embedding_model_name,
                embedding_function=self.embedding_function
            )

            # Send embedding tracking event
            await send_embedding_event(
                provider=provider,
                model=model_name,
                embedding_tokens=embedding_tokens,
                char_count=char_count,
                duration_ms=int(embedding_duration_ms),
                knowledge_id=knowledge_id,
                object_id=object_ids[0] if isinstance(object_ids, list) and len(object_ids) == 1 else None,
                key_type=key_type
            )

            # Create filter to limit search to knowledge_id and optionally object_ids
            filter_conditions = [
                FieldCondition(key="knowledge_id", match=MatchValue(value=knowledge_id))
            ]

            # Add object_id filter only if object_ids is provided
            if object_ids is not None:
                filter_conditions.append(
                    FieldCondition(key="object_id", match=MatchAny(any=object_ids))
                )

            # if user_id:
            #     filter_conditions.append(
            #         FieldCondition(key="user_id", match=MatchValue(value=user_id))
            #     )

            filter_condition = Filter(must=filter_conditions)

            # Perform semantic search across all chunks for this chatbot/user
            with tracer.start_as_current_span("vDB: Query Points Semantic Search"):
                query_response = await self.client_async.query_points(
                    collection_name=self.collection_name,
                    query=query_embedding,
                    using=self.embedding_model_name,
                    query_filter=filter_condition,
                    with_payload=True,
                    limit=limit
                )

            results = query_response.points if hasattr(query_response, "points") else []

            # Process results and include all returned chunks (no score filtering for semantic-only)
            chunks = []
            for point in results:
                payload = getattr(point, "payload", {}) or {}
                chunk_data = dict(payload)
                # Add vector ID, document ID and relevance score
                chunk_data["id"] = point.id
                chunk_data["object_id"] = chunk_data.get("object_id")
                chunk_data["relevance_score"] = getattr(point, "score", None)
                chunk_data["search_method"] = "semantic_only"  # Tag for identification
                chunks.append(chunk_data)

            # If no chunks found and we had object_ids, try again without document filtering
            if not chunks and object_ids is not None and allow_scope_expansion:
                logger.info(f"No chunks found with document filtering, retrying without object_ids for query: {query}")
                # Recursively call the function with object_ids=None to search entire knowledge base
                fallback_chunks = await self.search_chunks_semantic_only(
                    query=query,
                    knowledge_id=knowledge_id,
                    user_id=user_id,
                    object_ids=None,  # Search entire knowledge base
                    limit=limit
                )

                # Tag fallback chunks with fallback search method
                for chunk in fallback_chunks:
                    chunk["search_method"] = "semantic_only_fallback"

                return fallback_chunks

            logger.info(f"Found {len(chunks)} chunks via semantic-only search for knowledge_id: {knowledge_id}")
            return chunks

        except Exception as e:
            logger.error(f"Error in semantic-only chunk search for query '{query}': {e}")
            # Return empty list rather than raising to make the function more robust
            return []
     
    @track_time("API: Qdrant - Hybrid Search")
    async def hybrid_chunk_search(self, object_ids: Optional[Union[str, List[str]]], query: str, knowledge_id: str, user_id: Optional[str] = None,
                                 limit: int = 3, *, allow_scope_expansion: bool = False) -> List[dict]:
        """
        Performs hybrid search (dense + sparse vectors) within specified documents.
        Document IDs are a hard boundary unless an internal relevance-hint caller
        explicitly enables scope expansion. Public caller scope must not expand.

        Note: Embedding tracking is handled by search_relevant_chunks_for_query().

        Parameters:
        - object_ids (Union[str, List[str]]): Document IDs to search within
        - query (str): The user's query
        - knowledge_id (str): Knowledge ID to filter chunks by
        - user_id (Optional[str]): User ID to filter chunks by
        - limit (int): Maximum number of chunks to return (default: 3)

        Returns:
        - List[dict]: List of relevant chunks from the hybrid search
        """
        try:
            if object_ids == [] and not allow_scope_expansion:
                return []
            logger.info(f"Starting hybrid chunk search with limit={limit}")

            # If no object_ids provided, search entire knowledge base directly
            if object_ids is None:
                logger.info("No object_ids provided, searching entire knowledge base")
                search_limit = max(limit * 3, 50)  # Get 3x the needed amount or minimum 50
                chunks = await self.search_relevant_chunks_for_query(
                    object_ids=None,  # Search entire knowledge base
                    query=query,
                    user_id=user_id,
                    knowledge_id=knowledge_id,
                    limit=search_limit
                )

                # Sort by relevance score and limit to requested amount
                chunks = sorted(chunks, key=lambda x: x.get('relevance_score', 0), reverse=True)[:limit]

                # Tag chunks with knowledge base search method
                for chunk in chunks:
                    chunk["search_method"] = "hybrid_knowledge_base"
            else:
                # First attempt: Search within specified documents
                search_limit = max(limit * 3, 50)  # Get 3x the needed amount or minimum 50
                chunks = await self.search_relevant_chunks_for_query(
                    object_ids=object_ids,
                    query=query,
                    user_id=user_id,
                    knowledge_id=knowledge_id,
                    limit=search_limit
                )

                # Sort by relevance score and limit to requested amount
                chunks = sorted(chunks, key=lambda x: x.get('relevance_score', 0), reverse=True)[:limit]

                # If no chunks found in specified documents, fallback to entire knowledge base
                if not chunks and allow_scope_expansion:
                    logger.info("No chunks found in specified documents, falling back to entire knowledge base search")
                    chunks = await self.search_relevant_chunks_for_query(
                        object_ids=None,  # Search entire knowledge base
                        query=query,
                        user_id=user_id,
                        knowledge_id=knowledge_id,
                        limit=search_limit
                    )

                    # Sort by relevance score and limit to requested amount
                    chunks = sorted(chunks, key=lambda x: x.get('relevance_score', 0), reverse=True)[:limit]

                    # Tag chunks with fallback search method
                    for chunk in chunks:
                        chunk["search_method"] = "hybrid_fallback"
                else:
                    # Tag chunks with regular search method
                    for chunk in chunks:
                        chunk["search_method"] = "hybrid"

            logger.info(f"Hybrid search returned {len(chunks)} chunks")
            return chunks

        except Exception as e:
            logger.error(f"Error in hybrid chunk search: {e}")
            return []


# --- Module-level helpers for freshness check and reindex (called from API layer / worker) ---

async def get_source_last_modified_from_chunks(
    knowledge_id: str,
    source_id: str,
    embedding_model: str,
) -> Optional[int]:
    """Called from the API layer before dispatching Celery task. Non-fatal on error."""
    global chunk_indexer
    try:
        if chunk_indexer is None or chunk_indexer.embedding_model_name != embedding_model:
            await initialize_qdrant_document_chunk_indexer(embedding_model)
        return await chunk_indexer.get_source_last_modified(knowledge_id, source_id)
    except Exception as e:
        logger.warning(f"vDB: freshness check failed for source_id='{source_id}': {e}")
        return None  # allow indexing to proceed


# async def delete_document_by_source_id(
#     knowledge_id: str,
#     source_id: str,
#     embedding_model: str,
# ) -> None:
#     """Delete doc-level point. Called from worker before reindexing."""
#     global document_indexer
#     try:
#         if document_indexer is None or document_indexer.embedding_model_name != embedding_model:
#             await initialize_qdrant_document_indexer(embedding_model)
#         await document_indexer.delete_document_by_source_id(knowledge_id, source_id)
#     except Exception as e:
#         logger.error(f"vDB: delete_document_by_source_id failed: {e}")
#         raise


# async def delete_chunks_by_source_id(
#     knowledge_id: str,
#     source_id: str,
#     embedding_model: str,
# ) -> None:
#     """Delete all chunk points. Called from worker before reindexing."""
#     global chunk_indexer
#     try:
#         if chunk_indexer is None or chunk_indexer.embedding_model_name != embedding_model:
#             await initialize_qdrant_document_chunk_indexer(embedding_model)
#         await chunk_indexer.delete_chunks_by_source_id(knowledge_id, source_id)
#     except Exception as e:
#         logger.error(f"vDB: delete_chunks_by_source_id failed: {e}")
#         raise
