from fastapi import APIRouter, HTTPException, Path, Query, Depends, Body
from typing import Optional
from utils.files.retrieval_utils import (
    _get_chunk_indexer,
    get_document_chunks_paginated,
    # get_document_metadata,
)
from utils.db.retrieval_ops import get_hybrid_search_value
from utils.logger import logger
from utils.auth.dependencies import require_permission
from utils.pydantic_models_retrieval import (
    ChunkSingleResponse,
)
from pydantic import BaseModel
from typing import List, Dict


router = APIRouter()


# Request/Response Models
class ChunkUpdateRequest(BaseModel):
    text: Optional[str] = None
    metadata: Optional[Dict] = None


class ChunkBatchDeleteRequest(BaseModel):
    chunk_ids: List[str]
    # user_id: Optional[str] = None  # derived from session


class ChunkUpdateResponse(BaseModel):
    success: bool
    message: str


class ChunkBatchDeleteResponse(BaseModel):
    success: bool
    deleted_count: int
    message: str


# 18. Get Document Chunks (with metadata)
@router.get("/v1/{knowledge_id}/documents/{object_id}/chunks")
async def get_document_chunks_with_metadata(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    object_id: str = Path(..., description="Document Object ID"),
    limit: int = Query(20, ge=1, le=100, description="Number of chunks to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    metadata: bool = Query(False, description="Include chunk metadata in response"),
    _: Optional[str] = Depends(require_permission("read")),
):
    """
    Retrieve chunks for a specific document with pagination and optional metadata.
    Includes chunk-level metadata: chunk_keywords, chunk_topic, chunk_entities.
    """
    try:
        result = await get_document_chunks_paginated(
            object_id=object_id,
            knowledge_id=knowledge_id,
            limit=limit,
            offset=offset,
            include_metadata=metadata,
        )
        return result
    except Exception as e:
        logger.error(f"Error retrieving chunks for document {object_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve document chunks: {e}")


# 19. Get Chunk by ID
@router.get("/v1/{knowledge_id}/chunks/{chunk_id}")
async def get_chunk_by_id(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    chunk_id: str = Path(..., description="Chunk ID"),
    _: Optional[str] = Depends(require_permission("read")),
):
    """
    Retrieve a single chunk by its ID.
    Response includes chunk metadata.
    """
    try:
        # Get embedding model and initialize chunk indexer
        try:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            chunk_indexer = await _get_chunk_indexer(embedding_model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize chunk indexer: {e}")
        
        # Retrieve chunk by ID
        response = await chunk_indexer.client_async.retrieve(
            collection_name=chunk_indexer.collection_name,
            ids=[chunk_id],
            with_payload=True,
        )
        
        if not response or len(response) == 0:
            raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")
        
        point = response[0]
        payload = point.payload or {}
        chunk_text = payload.get("chunk_text") or payload.get("text")
        
        chunk_data = {
            "id": str(point.id),
            "object_id": payload.get("object_id"),
            "text": chunk_text,
            "metadata": payload,
        }
        
        return ChunkSingleResponse(**chunk_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving chunk {chunk_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve chunk: {e}")


# 20. Get Chunks by IDs (Batch)
@router.post("/v1/{knowledge_id}/chunks/batch")
async def get_chunks_by_ids_batch(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    chunk_ids: List[str] = Body(..., description="List of chunk IDs"),
    _: Optional[str] = Depends(require_permission("read")),
):
    """
    Retrieve multiple chunks by their IDs in batch.
    Response includes chunk metadata.
    """
    try:
        if not chunk_ids:
            return {"chunks": []}
        
        # Get embedding model and initialize chunk indexer
        try:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            chunk_indexer = await _get_chunk_indexer(embedding_model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize chunk indexer: {e}")
        
        # Retrieve chunks by IDs
        response = await chunk_indexer.client_async.retrieve(
            collection_name=chunk_indexer.collection_name,
            ids=chunk_ids,
            with_payload=True,
        )
        
        chunks = []
        for point in response or []:
            payload = point.payload or {}
            chunk_text = payload.get("chunk_text") or payload.get("text")
            chunks.append(ChunkSingleResponse(
                id=str(point.id),
                object_id=payload.get("object_id"),
                text=chunk_text,
                metadata=payload,
            ))
        
        return {"chunks": chunks}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving chunks by IDs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve chunks: {e}")


# 21. Update Chunk
@router.patch("/v1/{knowledge_id}/chunks/{chunk_id}")
async def update_chunk(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    chunk_id: str = Path(..., description="Chunk ID"),
    payload: ChunkUpdateRequest = Body(..., description="Chunk update data"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """
    Update a chunk's text or metadata.
    """
    try:
        # Get embedding model and initialize chunk indexer
        try:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            chunk_indexer = await _get_chunk_indexer(embedding_model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize chunk indexer: {e}")
        
        # Check if chunk exists
        response = await chunk_indexer.client_async.retrieve(
            collection_name=chunk_indexer.collection_name,
            ids=[chunk_id],
            with_payload=True,
        )
        
        if not response or len(response) == 0:
            raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found")
        
        # Get current payload
        current_payload = response[0].payload or {}
        
        # Prepare update payload
        update_payload = current_payload.copy()  # Start with current payload
        
        # If text is being updated, we need to update the vector embeddings too
        if payload.text is not None and payload.text != "":
            # Update text fields in payload
            update_payload["chunk_text"] = payload.text
            
            # Update dense embeddings
            dense_embedding = chunk_indexer.embedding_function.embed_query(payload.text)
            
            # Generate sparse embeddings for hybrid search
            sparse_query_obj = next(chunk_indexer.bm25_model.query_embed(payload.text))
            sparse_embedding = sparse_query_obj.as_object()
            
            # Update the point with new embeddings and payload
            from qdrant_client.http.models import PointStruct
            point_to_update = PointStruct(
                id=chunk_id,
                vector={
                    embedding_model: dense_embedding,
                    "bm25": sparse_embedding,
                },
                payload=update_payload
            )
            
            await chunk_indexer.client_async.upsert(
                collection_name=chunk_indexer.collection_name,
                points=[point_to_update],
            )
        else:
            # Only metadata is being updated, use set_payload
            if payload.metadata is not None:
                await chunk_indexer.client_async.set_payload(
                    collection_name=chunk_indexer.collection_name,
                    payload=payload.metadata,
                    points=[chunk_id],
                )
        
        logger.info(f"Successfully updated chunk {chunk_id}")
        return ChunkUpdateResponse(
            success=True,
            message=f"Chunk {chunk_id} updated successfully"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating chunk {chunk_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update chunk: {e}")


# 22. Delete Chunks (Batch)
@router.delete("/v1/{knowledge_id}/chunks")
async def delete_chunks_batch(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    payload: ChunkBatchDeleteRequest = Body(..., description="Chunk deletion data"),
    _: Optional[str] = Depends(require_permission("delete")),
):
    """
    Delete multiple chunks by their IDs.
    """
    try:
        if not payload.chunk_ids:
            raise HTTPException(status_code=400, detail="chunk_ids cannot be empty")
        
        # Get embedding model and initialize chunk indexer
        try:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            chunk_indexer = await _get_chunk_indexer(embedding_model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize chunk indexer: {e}")
        
        # Delete chunks by IDs
        await chunk_indexer.client_async.delete(
            collection_name=chunk_indexer.collection_name,
            points_selector=payload.chunk_ids,
        )
        
        logger.info(f"Successfully deleted {len(payload.chunk_ids)} chunks")
        return ChunkBatchDeleteResponse(
            success=True,
            deleted_count=len(payload.chunk_ids),
            message=f"Successfully deleted {len(payload.chunk_ids)} chunks"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting chunks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete chunks: {e}")


