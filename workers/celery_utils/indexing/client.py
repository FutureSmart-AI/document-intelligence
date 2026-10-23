import os
import time
import logging
from uuid import uuid5, NAMESPACE_URL
from qdrant_client import QdrantClient, AsyncQdrantClient
from qdrant_client.http import models
from langchain_qdrant import QdrantVectorStore
from langchain_huggingface import HuggingFaceEmbeddings
from qdrant_client.http.models import Filter, FieldCondition, MatchValue, MatchAny, FusionQuery
from langchain_openai import OpenAIEmbeddings
from fastembed import SparseTextEmbedding
from typing import List, Union, Optional
from sentence_transformers import SentenceTransformer

from langsmith import trace as ls_trace
from services.celery_logger import logger
import services.celery_setup_env as setup_env
from celery_utils.shared.user_usage_tracking import send_embedding_event
from celery_utils.shared.credentials_utils import get_api_key
from celery_utils.shared.tracing import track_time
from celery_utils.shared.embedding_models import EMBEDDING_MODELS

OPENAI_API_KEY = setup_env.OPENAI_API_KEY
qdrant_url = setup_env.qdrant_url
# qdrant_url = "http://localhost:6333"
qdrant_api_key = setup_env.qdrant_api_key


def _get_embedding_provider_and_model(embedding_model: str) -> tuple:
    """
    Get provider and model name from embedding model identifier.
    
    Args:
        embedding_model: Embedding model identifier
        
    Returns:
        Tuple of (provider, model_name)
    """
    model_config = EMBEDDING_MODELS.get(embedding_model)
    if not model_config:
        return "huggingface", embedding_model
    
    if model_config["type"] == "openai":
        return "openai", model_config["model_name"]
    elif model_config["type"] == "sentence_transformer":
        return "huggingface", model_config["model_name"]
    else:
        return "huggingface", embedding_model


import tiktoken
enc = tiktoken.get_encoding("cl100k_base")  # For text-embedding-3-* or ada-002

def _estimate_tokens(text: str) -> int:
    """
    Estimate token count for OpenAI embeddings.
    Uses approximation: ~4 characters per token for English text.
    
    Args:
        text: Text to estimate tokens for
        
    Returns:
        Estimated token count
    """
    # Single text
    token_count = len(enc.encode(text))
    print("token_count for embedding(using tiktoken): ", token_count)
    
    return token_count


def calculate_embedding_metrics(
    text: str,
    embedding_model: str,
    embedding_function: Optional[Union[HuggingFaceEmbeddings, OpenAIEmbeddings]] = None
) -> tuple[int, int]:
    """
    Calculate embedding metrics (tokens and char count) without sending events.
    
    Args:
        text: Text that was embedded
        embedding_model: Embedding model identifier
        embedding_function: Optional embedding function instance (for accurate token counting)
        
    Returns:
        Tuple of (embedding_tokens, embedding_char_count)
    """
    try:
        # Calculate metrics
        embedding_char_count = len(text)
        
        # Get provider for token calculation logic
        provider, _ = _get_embedding_provider_and_model(embedding_model)
        
        # Calculate tokens based on model type
        if provider == "huggingface" and embedding_function is not None:
            # For sentence transformer models, use the actual tokenizer
            try:
                # Access the underlying SentenceTransformer model
                if isinstance(embedding_function, HuggingFaceEmbeddings):
                    # HuggingFaceEmbeddings has a 'model' attribute that contains the SentenceTransformer
                    if hasattr(embedding_function, 'model') and hasattr(embedding_function.model, 'tokenizer'):
                        model = embedding_function.model
                    else:
                        # Fallback: create a new SentenceTransformer instance
                        model_config = EMBEDDING_MODELS.get(embedding_model)
                        if model_config and model_config["type"] == "sentence_transformer":
                            model = SentenceTransformer(model_config["model_name"])
                        else:
                            raise ValueError(f"Cannot access tokenizer for model {embedding_model}")
                    
                    # Calculate tokens using tokenizer (after truncation if needed)
                    encoded = model.tokenizer(text, truncation=True, max_length=model.max_seq_length, return_tensors="pt")
                    embedding_tokens = encoded.input_ids.shape[1]  # Includes [CLS] and [SEP]
                else:
                    # Fallback to estimation if not a HuggingFaceEmbeddings instance
                    embedding_tokens = _estimate_tokens(text)
            except Exception as e:
                logger.warning(f"Error calculating tokens with tokenizer for {embedding_model}: {e}. Falling back to estimation.")
                embedding_tokens = _estimate_tokens(text)
        else:
            # For OpenAI models or when embedding_function is not provided, use tiktoken estimation
            embedding_tokens = _estimate_tokens(text)
        
        return embedding_tokens, embedding_char_count
    except Exception as e:
        logger.error(f"Error calculating embedding metrics: {e}")
        # Return safe defaults
        return 0, len(text)


def track_embedding_usage(
    text: str,
    embedding_model: str,
    knowledge_id: str,
    object_id: str,
    duration_ms: int,
    embedding_function: Optional[Union[HuggingFaceEmbeddings, OpenAIEmbeddings]] = None,
    key_type: Optional[str] = None
):
    """
    Track embedding generation usage (legacy function - sends event immediately).
    Consider using calculate_embedding_metrics and sending aggregated events instead.

    Args:
        text: Text that was embedded
        embedding_model: Embedding model identifier
        knowledge_id: Knowledge ID
        object_id: Object ID
        duration_ms: Duration of embedding generation in milliseconds
        embedding_function: Optional embedding function instance (for accurate token counting)
        key_type: "owner-key" or "platform-key"
    """
    try:
        provider, model_name = _get_embedding_provider_and_model(embedding_model)
        embedding_tokens, embedding_char_count = calculate_embedding_metrics(
            text, embedding_model, embedding_function
        )

        send_embedding_event(
            provider=provider,
            model=model_name,
            embedding_tokens=embedding_tokens,
            char_count=embedding_char_count,
            duration_ms=duration_ms,
            knowledge_id=knowledge_id,
            object_id=object_id,
            key_type=key_type
        )
    except Exception as e:
        logger.error(f"Error tracking embedding usage: {e}")

class MultiQdrantDocumentIndexer:
    def __init__(self, embedding_model):
        self.embedding_model_name = embedding_model
        self.model_config = EMBEDDING_MODELS.get(embedding_model)
        if not self.model_config:
            raise ValueError(f"Unsupported embedding model: {embedding_model}")
        if self.model_config["type"] == "sentence_transformer":
            self.embedding_function = HuggingFaceEmbeddings(model_name = self.model_config["model_name"])
        elif self.model_config["type"] == "openai":
            # Store default API key and model name for on-demand creation
            self.default_api_key = OPENAI_API_KEY
            self.openai_model_name = self.model_config["model_name"]
            # Create default embedding function for backward compatibility
            self.embedding_function = OpenAIEmbeddings(model=self.openai_model_name, api_key=self.default_api_key)
        else:
            raise ValueError(f"Unsupported model type: {self.model_config['type']}")
        
        
        # Initialize BM25 sparse embeddings model for hybrid search
        self.bm25_model = SparseTextEmbedding("Qdrant/bm25")
        self.collection_name = f"agentic-rag-documents-{embedding_model}"
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
    
    def _get_embedding_function(self, api_key: Optional[str] = None):
        """
        Get embedding function, creating a new OpenAIEmbeddings instance if owner-specific API key is provided.
        
        Args:
            api_key (Optional[str]): Owner-specific API key. If None, returns cached embedding_function.
            
        Returns:
            Union[HuggingFaceEmbeddings, OpenAIEmbeddings]: The embedding function to use
        """
        # For HuggingFace models, always use cached instance (no API key needed)
        if self.model_config["type"] == "sentence_transformer":
            return self.embedding_function
        
        # For OpenAI models, create new instance if owner-specific key is provided
        if api_key is not None and api_key != self.default_api_key:
            return OpenAIEmbeddings(model=self.openai_model_name, api_key=api_key)
        
        # Otherwise, use cached default embedding function
        return self.embedding_function
        
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
                logging.info(f"Hybrid Collection Created with name {self.collection_name}")
            # self.vectors = QdrantVectorStore.from_existing_collection(
            #         collection_name=self.collection_name,
            #         embedding=self.embedding_function,
            #         url=qdrant_url,
            #         vector_name=self.embedding_model_name,  # Specify the dense vector name
            #         api_key= qdrant_api_key
            #     )   
            logging.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logging.error(f"Timeout occurred: {e}")
            logging.info("Attempting to reinitialize Qdrant connection...")
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
                logging.info(f"Hybrid Collection Created with name {self.collection_name}")
            # self.vectors = QdrantVectorStore.from_existing_collection(
            #         collection_name=self.collection_name,
            #         embedding=self.embedding_function,
            #         url=qdrant_url,
            #         vector_name=self.embedding_model_name,  # Specify the dense vector name
            #         api_key= qdrant_api_key
            #     )   
            logging.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logging.error(f"Timeout occurred: {e}")
            logging.info("Attempting to reinitialize Qdrant connection...")
            time.sleep(5)  # Wait for 5 seconds before retrying
            self.initialize_qdrant()  # Recursive call to retry initialization
            
    @track_time("Node: Process Document")
    def index_document_in_qdrantdb(self, object_id, document_payload, summary, knowledge_id=None, owner_api_key: Optional[str] = None):
        """
        Index extracted document level text in QdrantDB with hybrid embeddings.

        Args:
            object_id: Object ID
            document_payload: Document payload
            summary: Summary text to embed
            knowledge_id: Knowledge ID for usage tracking (optional)
            owner_api_key: Optional owner-specific API key for OpenAI embeddings

        Returns:
            Tuple of (embedding_tokens, embedding_char_count) for aggregated usage tracking
        """
        from opentelemetry import trace as otel_trace
        tracer = otel_trace.get_tracer(__name__)
        try:
            # Get appropriate embedding function (owner-specific or default)
            embedding_func = self._get_embedding_function(owner_api_key)

            # Generate dense embedding
            with tracer.start_as_current_span("Embedding: Document Dense"), \
                    ls_trace(name="Embedding: Document Dense", run_type="embedding"):
                dense_embedding = embedding_func.embed_query(summary)

            # Calculate embedding metrics (don't send event here - will be aggregated)
            embedding_tokens = 0
            embedding_char_count = 0
            if knowledge_id:
                embedding_tokens, embedding_char_count = calculate_embedding_metrics(
                    text=summary,
                    embedding_model=self.embedding_model_name,
                    embedding_function=embedding_func
                )

            # Generate sparse embedding
            with tracer.start_as_current_span("Embedding: Document Sparse (BM25)"), \
                    ls_trace(name="Embedding: Document Sparse (BM25)", run_type="embedding"):
                sparse_embed_obj = next(self.bm25_model.embed(summary))
                sparse_embedding = sparse_embed_obj.as_object()

            # Deterministic id keyed on the document so re-processing overwrites the
            # doc-level point instead of appending a duplicate under a random id.
            point_id = uuid5(NAMESPACE_URL, f"{knowledge_id}:{object_id}:document")

            with tracer.start_as_current_span("Qdrant: Upsert Document"):
                self.client.upsert(
                collection_name=self.collection_name,
                points=[{
                    "id": str(point_id),
                    "vector": {
                        self.embedding_model_name: dense_embedding,
                        "bm25": sparse_embedding
                    },
                    "payload": document_payload
                    }]
                )
            logging.info(f"Successfully indexed document with hybrid embeddings for object_id {object_id}")
            return embedding_tokens, embedding_char_count
        except Exception as e:
            logger.error(f"Error indexing document with object_id {object_id} in QdrantDB: {e}")
            raise

    # async def vector_search(self, query_text, chatbot_id=None, user_id=None, top_k=5):
    #     """
    #     Perform hybrid vector search in QdrantDB with chatbot_id and user_id filtering.
    #     Uses both dense and sparse embeddings with RRF fusion.
        
    #     Parameters:
    #     - query_text (str): The text to search for
    #     - chatbot_id (str): Optional chatbot identifier to filter results
    #     - user_id (str): Optional user identifier to filter results
    #     - top_k (int): Maximum number of results to return
        
    #     Returns:
    #     - List of search results with scores and payloads
    #     """
    #     try:
    #         # Generate dense embedding for the query text
    #         dense_query_embedding = self.embedding_function.embed_query(query_text)
            
    #         # Generate sparse embedding for the query text
    #         sparse_query_obj = next(self.bm25_model.query_embed(query_text))
    #         sparse_query_embedding = sparse_query_obj.as_object()
            
    #         # Prepare filter condition if filtering parameters are provided
    #         filter_condition = None
    #         conditions = []
            
    #         if chatbot_id:
    #             conditions.append(
    #                 FieldCondition(key="chatbot_id", match=MatchValue(value=chatbot_id))
    #             )
            
    #         if user_id:
    #             conditions.append(
    #                 FieldCondition(key="user_id", match=MatchValue(value=user_id))
    #             )
            
    #         if conditions:
    #             filter_condition = Filter(must=conditions) if len(conditions) > 1 else conditions[0]
            
    #         # Define prefetch limit for hybrid search
    #         prefetch_limit = top_k * 2
            
    #         # Set up prefetch for both dense and sparse vectors
    #         prefetch = [
    #             models.Prefetch(
    #                 query=dense_query_embedding,
    #                 using=self.embedding_model_name,
    #                 limit=prefetch_limit
    #             ),
    #             models.Prefetch(
    #                 query=models.SparseVector(**sparse_query_embedding),
    #                 using="bm25",
    #                 limit=prefetch_limit
    #             )
    #         ]
            
    #         # Use FusionQuery (Reciprocal Rank Fusion) to fuse the prefetch results
    #         fusion_query = FusionQuery(fusion=models.Fusion.RRF)
            
    #         # Perform the hybrid search
    #         search_results = await self.client_async.query_points(
    #             collection_name=self.collection_name,
    #             prefetch=prefetch,
    #             query=fusion_query,
    #             query_filter=filter_condition,
    #             with_payload=True
    #         )
            
    #         results = search_results.points
    #         logging.info(f"Hybrid search returned {len(results)} results")
        
    #         # Check if search_results is None or empty
    #         if not results:
    #             logging.warning("Hybrid search returned no results")
    #             return []

    #         return [str(result.id) for result in results]

    #     except Exception as e:
    #         logger.error(f"Error performing hybrid vector search in QdrantDB: {e}")
    #         raise

    # async def delete_document(self, document_id: Union[str, List[str]]):
    #     """
    #     Delete a document or multiple documents from the Qdrant collection by ID(s).
        
    #     Parameters:
    #     - document_id (Union[str, List[str]]): The ID(s) of the document(s) to delete
        
    #     Returns:
    #     - bool: True if deletion was successful
    #     """
    #     try:
    #         # Handle both single ID and list of IDs
    #         if isinstance(document_id, list):
    #             await self.client_async.delete(
    #                 collection_name=self.collection_name,
    #                 points_selector=document_id
    #             )
    #             doc_id_display = f"{len(document_id)} documents"
    #         else:
    #             await self.client_async.delete(
    #                 collection_name=self.collection_name,
    #                 points_selector=[document_id]
    #             )
    #             doc_id_display = document_id
                
    #         logging.info(f"Successfully deleted {doc_id_display}")
    #         return True
    #     except Exception as e:
    #         logger.error(f"Error deleting document(s) {document_id} from QdrantDB: {e}")
    #         raise

class MultiQdrantChunkIndexer:
    def __init__(self, embedding_model):
        self.embedding_model_name = embedding_model
        self.model_config = EMBEDDING_MODELS.get(embedding_model)
        if not self.model_config:
            raise ValueError(f"Unsupported embedding model: {embedding_model}")
        if self.model_config["type"] == "sentence_transformer":
            self.embedding_function = HuggingFaceEmbeddings(model_name = self.model_config["model_name"])
        elif self.model_config["type"] == "openai":
            # Store default API key and model name for on-demand creation
            self.default_api_key = OPENAI_API_KEY
            self.openai_model_name = self.model_config["model_name"]
            # Create default embedding function for backward compatibility
            self.embedding_function = OpenAIEmbeddings(model=self.openai_model_name, api_key=self.default_api_key)
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
    
    def _get_embedding_function(self, api_key: Optional[str] = None):
        """
        Get embedding function, creating a new OpenAIEmbeddings instance if owner-specific API key is provided.
        
        Args:
            api_key (Optional[str]): Owner-specific API key. If None, returns cached embedding_function.
            
        Returns:
            Union[HuggingFaceEmbeddings, OpenAIEmbeddings]: The embedding function to use
        """
        # For HuggingFace models, always use cached instance (no API key needed)
        if self.model_config["type"] == "sentence_transformer":
            return self.embedding_function
        
        # For OpenAI models, create new instance if owner-specific key is provided
        if api_key is not None and api_key != self.default_api_key:
            return OpenAIEmbeddings(model=self.openai_model_name, api_key=api_key)
        
        # Otherwise, use cached default embedding function
        return self.embedding_function
        
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
                logging.info(f"Hybrid Collection Created with name {self.collection_name}")
            # self.vectors = QdrantVectorStore.from_existing_collection(
            #         collection_name=self.collection_name,
            #         embedding=self.embedding_function,
            #         url=qdrant_url,
            #         vector_name=self.embedding_model_name,  # Specify the dense vector name
            #         api_key= qdrant_api_key
            #     )
            logging.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logging.error(f"Timeout occurred: {e}")
            logging.info("Attempting to reinitialize Qdrant connection...")
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
                logging.info(f"Hybrid Collection Created with name {self.collection_name}")
            # self.vectors = QdrantVectorStore.from_existing_collection(
            #         collection_name=self.collection_name,
            #         embedding=self.embedding_function,
            #         url=qdrant_url,
            #         vector_name=self.embedding_model_name,  # Specify the dense vector name
            #         api_key= qdrant_api_key
            #     )
            logging.info(f"Qdrant Hybrid Vector Store Initialized with name {self.collection_name}")
        except Exception as e:
            logging.error(f"Timeout occurred: {e}")
            logging.info("Attempting to reinitialize Qdrant connection...")
            time.sleep(5)  # Wait for 5 seconds before retrying
            self.initialize_qdrant()  # Recursive call to retry initialization
            
    @track_time("Node: Process Chunks")
    def index_chunk_in_qdrantdb(self, object_id, chunk_records, knowledge_id=None, owner_api_key: Optional[str] = None):
        """
        Index extracted Chunk level document text in QdrantDB with hybrid embeddings

        Args:
            object_id: Object ID
            chunk_records: List of chunk records to index
            knowledge_id: Knowledge ID for usage tracking (optional)
            owner_api_key: Optional owner-specific API key for OpenAI embeddings

        Returns:
            Tuple of (total_embedding_tokens, total_embedding_char_count) for aggregated usage tracking
        """
        from opentelemetry import trace as otel_trace
        tracer = otel_trace.get_tracer(__name__)
        try:
            # Get appropriate embedding function (owner-specific or default)
            embedding_func = self._get_embedding_function(owner_api_key)

            # Process each chunk record to add hybrid embeddings
            hybrid_chunk_records = []
            total_embedding_tokens = 0
            total_embedding_char_count = 0

            with tracer.start_as_current_span("Embedding: Chunks Dense + Sparse") as embed_span, \
                    ls_trace(name="Embedding: Chunks Dense + Sparse", run_type="embedding") as embed_run:
                for chunk_record in chunk_records:
                    # Extract chunk text for embedding generation
                    chunk_text = chunk_record.get('embedded_text', '')
                    if not chunk_text:
                        logging.warning(f"No chunk_text found in chunk record for knowledge_id {knowledge_id}")
                        continue

                    # Generate dense embedding
                    dense_embedding = embedding_func.embed_query(chunk_text)

                    # Calculate embedding metrics (accumulate for aggregated event)
                    if knowledge_id:
                        embedding_tokens, embedding_char_count = calculate_embedding_metrics(
                            text=chunk_text,
                            embedding_model=self.embedding_model_name,
                            embedding_function=embedding_func
                        )
                        total_embedding_tokens += embedding_tokens
                        total_embedding_char_count += embedding_char_count

                    # Generate sparse embedding
                    sparse_embed_obj = next(self.bm25_model.embed(chunk_text))
                    sparse_embedding = sparse_embed_obj.as_object()

                    # Update the chunk record with hybrid vectors
                    hybrid_chunk_record = {
                        "id": chunk_record.get('id'),
                        "vector": {
                            self.embedding_model_name: dense_embedding,
                            "bm25": sparse_embedding
                        },
                        "payload": chunk_record.get('payload', {})
                    }
                    hybrid_chunk_records.append(hybrid_chunk_record)

                embed_span.set_attribute("chunk_count", len(hybrid_chunk_records))
                if embed_run is not None:
                    embed_run.add_metadata({"chunk_count": len(hybrid_chunk_records)})

            if hybrid_chunk_records:
                with tracer.start_as_current_span("Qdrant: Upsert Chunks") as upsert_span:
                    upsert_span.set_attribute("chunk_count", len(hybrid_chunk_records))
                    self.client.upsert(
                        collection_name=self.collection_name,
                        points=hybrid_chunk_records
                    )
                logging.info(f"Successfully indexed {len(hybrid_chunk_records)} chunks with hybrid embeddings for knowledge_id {knowledge_id}")
            else:
                logging.warning(f"No valid chunks to index for knowledge_id {knowledge_id}")

            return total_embedding_tokens, total_embedding_char_count

        except Exception as e:
            logger.error(f"Error indexing chunk level with knowledge_id: {knowledge_id} in QdrantDB: {e}")
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
                "document_id": "..."
            },
            ...
        }
        """
        try:
            if not chunk_ids:
                logging.warning("No chunk IDs provided.")
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

    async def get_all_chunks_for_document(self, object_id: Union[str, List[str]]) -> List[dict]:
        """
        Retrieves all chunks for a specified object ID or list of object IDs from the Qdrant database.
        
        Parameters:
        - object_id (Union[str, List[str]]): A single object ID or a list of object IDs to retrieve chunks for
        
        Returns:
        - List[dict]: A list of chunk data dictionaries
        """
        try:
            # Create a filter based on whether object_id is a single ID or a list
            if isinstance(object_id, list):
                # For multiple object IDs, use MatchAny
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
                # For a single object ID, use MatchValue
                filter_condition = Filter(
                    must=[
                        FieldCondition(
                            key="object_id",
                            match=MatchValue(value=object_id)
                        )
                    ]
                )
                doc_id_display = object_id
            
            # Get all chunks (no vector search, just filtering by object_id)
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
            
            logging.info(f"Retrieved {len(chunks)} chunks for {doc_id_display}")
            return chunks
            
        except Exception as e:
            logging.error(f"Error getting chunks for object ID {object_id}: {e}")
            # Return an empty list rather than raising to make the function more robust
            return []

    # async def search_relevant_chunks_for_query(self, document_ids: Union[str, List[str]], query: str, limit: int = 1000) -> List[dict]:
    #     """
    #     Performs hybrid semantic search to find the most relevant chunks for a query across specified documents.
    #     Returns all chunks with relevancy score > 0.5 (50%).
        
    #     Parameters:
    #     - document_ids (Union[str, List[str]]): A single document ID or a list of document IDs to search within
    #     - query (str): The user's query to find relevant chunks for
    #     - limit (int): Maximum number of chunks to retrieve for filtering (default: 1000)
        
    #     Returns:
    #     - List[dict]: A list of the most relevant chunk data dictionaries
    #     """
    #     try:
    #         # Convert single document_id to list for consistent handling
    #         if isinstance(document_ids, str):
    #             document_ids = [document_ids]
    #             doc_id_display = document_ids[0]
    #         else:
    #             doc_id_display = f"{len(document_ids)} documents"
            
    #         logging.info(f"Searching for relevant chunks across {doc_id_display} for query: {query}")
            
    #         # Generate dense embedding for the query
    #         dense_query_embedding = self.embedding_function.embed_query(query)
            
    #         # Generate sparse embedding for the query
    #         sparse_query_obj = next(self.bm25_model.query_embed(query))
    #         sparse_query_embedding = sparse_query_obj.as_object()
            
    #         # Create filter to limit search to specified documents
    #         filter_condition = Filter(
    #             must=[
    #                 FieldCondition(
    #                     key="document_id",
    #                     match=MatchAny(any=document_ids)
    #                 )
    #             ]
    #         )
            
    #         # Define prefetch limit for hybrid search
    #         prefetch_limit = limit
            
    #         # Set up prefetch for both dense and sparse vectors
    #         prefetch = [
    #             models.Prefetch(
    #                 query=dense_query_embedding,
    #                 using=self.embedding_model_name,
    #                 limit=prefetch_limit
    #             ),
    #             models.Prefetch(
    #                 query=models.SparseVector(**sparse_query_embedding),
    #                 using="bm25",
    #                 limit=prefetch_limit
    #             )
    #         ]
            
    #         # Use FusionQuery (Reciprocal Rank Fusion) to fuse the prefetch results
    #         fusion_query = FusionQuery(fusion=models.Fusion.RRF)
            
    #         # Perform hybrid semantic search with a higher limit to find all relevant chunks
    #         search_results = await self.client_async.query_points(
    #             collection_name=self.collection_name,
    #             prefetch=prefetch,
    #             query=fusion_query,
    #             query_filter=filter_condition,
    #             with_payload=True,
    #             limit=limit
    #         )
            
    #         results = search_results.points
            
    #         # Process results and filter by relevance score (greater than 0.5 or 50%)
    #         chunks = []
    #         relevant_count = 0
    #         total_count = len(results)
            
    #         for point in results:
    #             # Skip chunks with relevance score below 0.5 (50%)
    #             # if point.score < 0.5:
    #             #     continue
                
    #             relevant_count += 1
    #             chunk_data = point.payload if hasattr(point, 'payload') else {}
    #             # Add vector ID, document ID and relevance score
    #             chunk_data["id"] = point.id
    #             chunk_data["document_id"] = chunk_data.get("document_id")  # Ensure document_id is included
    #             chunk_data["relevance_score"] = point.score
    #             chunks.append(chunk_data)
            
    #         logging.info(f"Found {relevant_count} chunks with relevancy > 50% out of {total_count} chunks searched across {doc_id_display} using hybrid search")
    #         return chunks
            
    #     except Exception as e:
    #         logging.error(f"Error searching relevant chunks for query '{query}' in documents {document_ids}: {e}")
    #         # Return empty list rather than raising to make the function more robust
    #         return []
    
    # async def search_chunks_semantic_only(self, query: str, chatbot_id: str, user_id: str, limit: int = 3) -> List[dict]:
    #     """
    #     Performs pure semantic search across ALL chunks for a chatbot/user without document filtering.
    #     Returns top chunks by semantic similarity regardless of which document they belong to.
        
    #     Parameters:
    #     - query (str): The user's query to find semantically similar chunks for
    #     - chatbot_id (str): Chatbot ID to filter chunks by
    #     - user_id (str): User ID to filter chunks by  
    #     - limit (int): Maximum number of top chunks to return (default: 3)
        
    #     Returns:
    #     - List[dict]: A list of the most semantically similar chunk data dictionaries, each containing:
    #         - id: The chunk ID
    #         - document_id: The ID of the document this chunk belongs to
    #         - chunk_text: The text content of the chunk
    #         - relevance_score: The semantic relevance score of the chunk to the query
    #         - Additional metadata from the chunk payload
    #     """
    #     try:
    #         logging.info(f"Performing semantic-only search for chatbot_id: {chatbot_id}, user_id: {user_id}, query: {query}")
            
    #         # Generate embedding for the query
    #         query_embedding = self.embedding_function.embed_query(query)
            
    #         # Create filter to limit search to chatbot_id and user_id only (no document filtering)
    #         filter_condition = Filter(
    #             must=[
    #                 FieldCondition(key="chatbot_id", match=MatchValue(value=chatbot_id)),
    #                 FieldCondition(key="user_id", match=MatchValue(value=user_id))
    #             ]
    #         )
            
    #         # Perform semantic search across all chunks for this chatbot/user
    #         search_results = await self.client_async.search(
    #             collection_name=self.collection_name,
    #             query_vector=query_embedding,
    #             limit=limit,  # Return only top N chunks
    #             query_filter=filter_condition
    #         )
            
    #         # Process results and include all returned chunks (no score filtering for semantic-only)
    #         chunks = []
    #         for point in search_results:
    #             chunk_data = point.payload if hasattr(point, 'payload') else {}
    #             # Add vector ID, document ID and relevance score
    #             chunk_data["id"] = point.id
    #             chunk_data["document_id"] = chunk_data.get("document_id")
    #             chunk_data["relevance_score"] = point.score
    #             chunk_data["search_method"] = "semantic_only"  # Tag for identification
    #             chunks.append(chunk_data)
            
    #         logging.info(f"Found {len(chunks)} chunks via semantic-only search for chatbot_id: {chatbot_id}, user_id: {user_id}")
    #         return chunks
            
    #     except Exception as e:
    #         logging.error(f"Error in semantic-only chunk search for query '{query}': {e}")
    #         # Return empty list rather than raising to make the function more robust
    #         return []
     
    # async def hybrid_chunk_search(self, document_ids: Union[str, List[str]], query: str, chatbot_id: str, user_id: str, 
    #                              document_limit: int = 3, semantic_limit: int = 3) -> List[dict]:
    #     """
    #     Performs hybrid search combining document-filtered search and pure semantic search.
    #     Returns union of both approaches with deduplication.
        
    #     Parameters:
    #     - document_ids (Union[str, List[str]]): Document IDs to search within for document-filtered approach
    #     - query (str): The user's query
    #     - chatbot_id (str): Chatbot ID to filter chunks by
    #     - user_id (str): User ID to filter chunks by
    #     - document_limit (int): Max chunks to return from document-filtered search (default: 3)
    #     - semantic_limit (int): Max chunks to return from semantic-only search (default: 3)
        
    #     Returns:
    #     - List[dict]: Combined and deduplicated list of chunks from both search methods
    #     """
    #     try:
    #         logging.info(f"Starting hybrid chunk search with document_limit={document_limit}, semantic_limit={semantic_limit}")
            
    #         # Path 1: Document-filtered search (existing method)
    #         logging.info("Executing document-filtered search...")
    #         document_chunks = await self.search_relevant_chunks_for_query(
    #             document_ids=document_ids,
    #             query=query,
    #             limit=1000  # Get all relevant chunks, then limit after filtering
    #         )
            
    #         # Limit document chunks to top N by relevance score
    #         document_chunks = sorted(document_chunks, key=lambda x: x.get('relevance_score', 0), reverse=True)[:document_limit]
            
    #         # Tag chunks with search method
    #         for chunk in document_chunks:
    #             chunk["search_method"] = "document_filtered"
            
    #         logging.info(f"Document-filtered search returned {len(document_chunks)} chunks")
            
    #         # Path 2: Pure semantic search (new method)
    #         logging.info("Executing semantic-only search...")
    #         semantic_chunks = await self.search_chunks_semantic_only(
    #             query=query,
    #             chatbot_id=chatbot_id,
    #             user_id=user_id,
    #             limit=semantic_limit
    #         )
            
    #         logging.info(f"Semantic-only search returned {len(semantic_chunks)} chunks")
            
    #         # Combine and deduplicate by chunk ID
    #         seen_chunk_ids = set()
    #         combined_chunks = []
            
    #         # Add document-filtered chunks first (they have document context)
    #         for chunk in document_chunks:
    #             chunk_id = chunk.get("id")
    #             if chunk_id and chunk_id not in seen_chunk_ids:
    #                 seen_chunk_ids.add(chunk_id)
    #                 combined_chunks.append(chunk)
            
    #         # Add semantic-only chunks that aren't already included
    #         for chunk in semantic_chunks:
    #             chunk_id = chunk.get("id")
    #             if chunk_id and chunk_id not in seen_chunk_ids:
    #                 seen_chunk_ids.add(chunk_id)
    #                 combined_chunks.append(chunk)
            
    #         logging.info(f"Hybrid search combined {len(document_chunks)} document-filtered + {len(semantic_chunks)} semantic-only chunks")
    #         logging.info(f"After deduplication: {len(combined_chunks)} unique chunks")
            
    #         return combined_chunks
            
    #     except Exception as e:
    #         logging.error(f"Error in hybrid chunk search: {e}")
    #         # Fallback to document-filtered search only
    #         logging.info("Falling back to document-filtered search only")
    #         return await self.search_relevant_chunks_for_query(document_ids, query, limit=document_limit)


    # async def delete_chunks(self, document_id: Union[str, List[str]]):
    #     """
    #     Delete all chunks associated with a document or multiple documents.
        
    #     Parameters:
    #     - document_id (Union[str, List[str]]): A single document ID or a list of document IDs to delete chunks for
        
    #     Returns:
    #     - bool: True if deletion was successful
    #     """
    #     try:
    #         # Create filter based on whether document_id is a single ID or a list
    #         if isinstance(document_id, list):
    #             filter_condition = Filter(
    #                 must=[
    #                     FieldCondition(
    #                         key="document_id",
    #                         match=MatchAny(any=document_id)
    #                     )
    #                 ]
    #             )
    #             doc_id_display = f"{len(document_id)} documents"
    #         else:
    #             filter_condition = Filter(
    #                 must=[
    #                     FieldCondition(
    #                         key="document_id",
    #                         match=MatchValue(value=document_id)
    #                     )
    #                 ]
    #             )
    #             doc_id_display = document_id
            
    #         # Delete all chunks with the specified document_id(s)
    #         await self.client_async.delete(
    #             collection_name=self.collection_name,
    #             points_selector=filter_condition
    #         )
            
    #         logging.info(f"Successfully deleted all chunks for {doc_id_display}")
    #         return True
    #     except Exception as e:
    #         logger.error(f"Error deleting chunks for document ID {document_id}: {e}")
    #         raise
