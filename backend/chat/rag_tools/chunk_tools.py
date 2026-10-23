"""
Chunk-level tools for RAG agent.

Only the 3 deep-read tools are ported (get_document_chunks_tool,
get_chunks_batch_tool, get_chunk_metadata_tool) — search_chunks_tool is
legacy, superseded by retrieve_chunks_tool in consolidated_tools.py, and is
excluded from RAG_TOOLS in the donor's collections.py.
"""
import logging
from typing import Dict, List, Optional, Any
from langchain_core.tools import tool
from utils.logger import logger

from chat.knowledge_adapter import (
    get_document_chunks_paginated as knowledge_get_document_chunks_paginated,
    get_chunk_metadata_by_id as knowledge_get_chunk_metadata_by_id,
    get_chunks_batch as knowledge_get_chunks_batch,
)

from chat.rag_tools.schemas import (
    GetDocumentChunksInput,
    GetChunksBatchInput,
    GetChunkMetadataInput,
)
from chat.rag_tools.renderers import (
    render_chunks,
    render_chunk_metadata,
)


@tool("get-document-chunks", args_schema=GetDocumentChunksInput, response_format="content_and_artifact")
async def get_document_chunks_tool(
    knowledge_id: str,
    object_id: str
):
    """Retrieve all raw text chunks belonging to a specific document.

    Use this tool when you need to read large sections of a document sequentially.

    Returns:
        Tuple of (compact Markdown content, full paginated artifact dict).
    """
    logger.info(f"RAG Tool: Getting chunks for document {object_id}")

    try:
        result = await knowledge_get_document_chunks_paginated(
            knowledge_id=knowledge_id,
            object_id=object_id,
        )
        chunks = result.get("chunks", []) if isinstance(result, dict) else []
        logger.info(f"Retrieved {len(chunks)} chunks for document")
        content = render_chunks(
            chunks=chunks,
            parent_object_id=object_id,
        )
        return content, result
    except Exception as e:
        logger.error(f"Error in get_document_chunks_tool: {e}")
        artifact = {"chunks": [], "total": 0, "error": str(e)}
        return render_chunks(
            chunks=[], parent_object_id=object_id, error=str(e),
        ), artifact


@tool("get-chunks-batch", args_schema=GetChunksBatchInput, response_format="content_and_artifact")
async def get_chunks_batch_tool(
    knowledge_id: str,
    chunk_ids: List[str]
):
    """Retrieve the text content for a specific list of chunk IDs.

    Use this tool for deep dive analysis on a specific set of text chunks.

    Returns:
        Tuple of (compact Markdown content, full structured artifact dict).
    """
    logger.info(f"RAG Tool: Getting batch of {len(chunk_ids)} chunks")

    try:
        chunks = await knowledge_get_chunks_batch(
            knowledge_id=knowledge_id,
            chunk_ids=chunk_ids
        )
        logger.info(f"Retrieved {len(chunks)} chunks in batch")
        artifact = {"chunks": chunks, "count": len(chunks)}
        return render_chunks(chunks=chunks), artifact
    except Exception as e:
        logger.error(f"Error in get_chunks_batch_tool: {e}")
        artifact = {"chunks": [], "count": 0, "error": str(e)}
        return render_chunks(chunks=[], error=str(e)), artifact


@tool("get-chunk-metadata", args_schema=GetChunkMetadataInput, response_format="content_and_artifact")
async def get_chunk_metadata_tool(
    knowledge_id: str,
    chunk_id: str
):
    """Retrieve specific metadata for a single chunk.

    Use this tool to verify the source page number, timestamp, or category of a text snippet.

    Returns:
        Tuple of (compact Markdown content, full structured metadata dict).
    """
    logger.info(f"RAG Tool: Getting metadata for chunk {chunk_id}")

    try:
        metadata = await knowledge_get_chunk_metadata_by_id(
            knowledge_id=knowledge_id,
            chunk_id=chunk_id
        )
        logger.info(f"Retrieved metadata for chunk {chunk_id}")
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
        return render_chunk_metadata(chunk_id=chunk_id, metadata=metadata), metadata
    except Exception as e:
        logger.error(f"Error in get_chunk_metadata_tool: {e}")
        artifact = {"chunk_id": chunk_id, "error": str(e)}
        return render_chunk_metadata(
            chunk_id=chunk_id, metadata={}, error=str(e),
        ), artifact
