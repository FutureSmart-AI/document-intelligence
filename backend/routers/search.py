from fastapi import APIRouter, HTTPException, Path, Depends, Body
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Optional
from utils.files.retrieval_utils import (
    _get_chunk_indexer,
)
from utils.db.retrieval_ops import get_hybrid_search_value
from utils.logger import logger
from utils.auth.dependencies import require_permission


router = APIRouter(dependencies=[Depends(require_permission("use"))])


# Request/Response Models
class ChunkUpdateRequest(BaseModel):
    text: Optional[str] = None
    metadata: Optional[Dict] = None


class ChunkBatchDeleteRequest(BaseModel):
    chunk_ids: List[str]
    user_id: Optional[str] = None


class ChunkUpdateResponse(BaseModel):
    success: bool
    message: str


class ChunkBatchDeleteResponse(BaseModel):
    success: bool
    deleted_count: int
    message: str


class SearchRequest(BaseModel):
    """Request model for search endpoint"""
    model_config = ConfigDict(extra="forbid")
    query: Optional[str] = Field(None, description="Search query text")
    num_chunks: int = Field(3, ge=1, le=100, description="Number of chunks to return")
    object_ids: Optional[List[str]] = Field(None, description="Hard document scope. Omit or use null for the whole knowledge base; [] returns no chunks. Metadata filters are not supported by this endpoint.")
    mode: Optional[str] = Field("hybrid", description="Search mode: 'hybrid' or 'non_hybrid'")


# 17. Search Knowledge (Semantic/Hybrid)
@router.post("/v1/{knowledge_id}/search")
async def search_knowledge(
    knowledge_id: str = Path(..., description="Knowledge ID"),
    request: SearchRequest = Body(..., description="Search request parameters"),
    user_id: Optional[str] = Depends(require_permission("use")),
):
    """
    Search for chunks using semantic or hybrid search.

    This endpoint is an improvement on /chunks/search from knowledge_retrieval.py.
    """
    try:

        # Extract parameters from request body
        query = request.query
        num_chunks = request.num_chunks
        parsed_object_ids = request.object_ids
        mode = request.mode

        if parsed_object_ids == []:
            return {"chunks": []}
        
        # Log received request for debugging
        logger.debug(f"Search request received - query: {query}, num_chunks: {num_chunks}, object_ids: {parsed_object_ids}, mode: {mode}")
        
        # Initialize chunk indexer
        try:
            _hybrid, embedding_model = await get_hybrid_search_value(knowledge_id)
            chunk_indexer = await _get_chunk_indexer(embedding_model)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize chunk indexer: {e}")
        
        chunks: list[dict] = []
        try:
            if query:
                # Choose retrieval based on mode
                search_mode = (mode or "hybrid").lower()
                if search_mode not in {"hybrid", "non_hybrid"}:
                    raise HTTPException(status_code=400, detail="Invalid mode. Use 'hybrid' or 'non_hybrid'")
                
                if search_mode == "hybrid":
                    chunks = await chunk_indexer.hybrid_chunk_search(
                        object_ids=parsed_object_ids,
                        user_id=user_id,
                        query=query,
                        knowledge_id=knowledge_id,
                        limit=num_chunks,
                        allow_scope_expansion=False,
                    )
                else:  # non_hybrid
                    chunks = await chunk_indexer.search_chunks_semantic_only(
                        user_id=user_id,
                        query=query,
                        knowledge_id=knowledge_id,
                        object_ids=parsed_object_ids,
                        limit=num_chunks,
                        allow_scope_expansion=False,
                    )
            else:
                if not parsed_object_ids:
                    raise HTTPException(status_code=400, detail="object_ids required when query is not provided")
                all_chunks = await chunk_indexer.get_all_chunks_for_document(
                    object_id=parsed_object_ids, 
                    knowledge_id=knowledge_id
                )
                chunks = all_chunks[:num_chunks]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Chunk retrieval failed: {e}")
        
        return {"chunks": chunks}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chunk search failed: {e}")

