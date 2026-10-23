import time
from io import BytesIO
from typing import Optional

import httpx

from services.celery_logger import logger
from celery_utils.db.db_utils import retrieve_knowledge_processing_status, update_processing_status, mark_task_completed
from celery_utils.enrichment.enrichment_utils import enrich_document
from celery_utils.indexing.indexer import index_in_vectordb
from celery_utils.processing.s3_utils import upload_extracted_text_to_s3
from celery_utils.shared.user_usage_tracking import send_text_processing_event, get_owner_id_from_service_id
from celery_utils.shared.tracing import track_time, mark_indexing_time, reset_embedding_cost, get_embedding_cost
from langsmith import traceable


def _read_text_from_s3_presigned(url: str) -> str:
    """Download raw text content from an S3 presigned URL."""
    with httpx.Client() as client:
        r = client.get(url)
        r.raise_for_status()
        return BytesIO(r.content).getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# Main Pipeline: Extract -> Enrich -> Index -> Track
# ---------------------------------------------------------------------------

@track_time("Node: Process And Index Text")
@traceable(name="Process Document")
def process_and_index_text(user_id: Optional[str], knowledge_id: str, object_id: str, text: str, text_url: str,
                           file_name: str, chunk_size: Optional[int], hybrid_search, embedding_model,
                           contextualise_chunks: Optional[bool] = False, agentic_rag: Optional[bool] = False,
                           enable_chunking: bool = True):
    """End-to-end raw text processing pipeline called by Celery workers.
    Steps: fetch from S3 -> enrich with LLM -> index in vectordb -> track usage.
    """
    logger.info(f"Starting text processing: knowledge_id={knowledge_id}, object_id={object_id}, file_name={file_name}")
    processing_start_time = time.time()
    reset_embedding_cost()
    owner_id = get_owner_id_from_service_id(knowledge_id)

    try:
        # Skip if already processed (idempotency check)
        status = retrieve_knowledge_processing_status(knowledge_id, object_id)
        if status == "completed":
            logger.info(f"Task already completed for object_id={object_id}, skipping")
            return

        # Step 1: Fetch text content from S3 presigned URL
        update_processing_status(knowledge_id, object_id, "extracting")
        logger.info(f"Fetching text from S3 for object_id={object_id}")
        try:
            text = _read_text_from_s3_presigned(text_url)
            logger.info(f"Fetched text from S3: chars={len(text)}")
        except Exception as e:
            logger.error(f"Failed to read text from S3 URL: {e}")
            raise

        # Step 2: Enrich — add metadata, summaries, and chunk the text via LLM
        update_processing_status(knowledge_id, object_id, "enriching")
        logger.info(f"Enriching text document: knowledge_id={knowledge_id}")
        enriched_document, chunks_only = enrich_document(
            text, knowledge_id, user_id, object_id,
            chunk_size=chunk_size, enable_chunking=enable_chunking, owner_id=owner_id
        )

        # Step 3: Index — store enriched document and chunks in vector database
        update_processing_status(knowledge_id, object_id, "indexing")
        enriched_document["file_name"] = file_name
        logger.info(f"Indexing in vectordb: knowledge_id={knowledge_id}, chunks_only={chunks_only}")
        index_in_vectordb(
            object_id=object_id, knowledge_id=knowledge_id, user_id=user_id,
            enriched_metadata=enriched_document, embedding_model=embedding_model,
            chunks_only=chunks_only, owner_id=owner_id
        )
        logger.info(f"Indexed enriched document in vectordb for object_id={object_id}")
        del enriched_document  # Free enriched document after indexing

        # Step 4: Upload raw text to S3 for reference
        upload_extracted_text_to_s3(extracted_text=text, file_name=file_name, object_id=object_id)

        # Step 5: Mark task as completed and record duration
        processing_time_ms = mark_task_completed(knowledge_id, object_id, processing_start_time)
        char_count = len(text.strip())
        embed_tokens, embed_cost_usd = get_embedding_cost()
        mark_indexing_time(processing_time_ms, knowledge_id=knowledge_id, object_id=object_id,
                           char_count=char_count, embedding_tokens=embed_tokens,
                           embedding_cost_usd=embed_cost_usd)
        logger.info(f"Text processing complete: knowledge_id={knowledge_id}, duration={processing_time_ms}ms, chars={char_count}")

        # Step 6: Send usage tracking event
        send_text_processing_event(
            char_count=char_count,
            duration_ms=processing_time_ms,
            knowledge_id=knowledge_id,
            object_id=object_id,
            key_type=None
        )

    except Exception as e:
        update_processing_status(knowledge_id, object_id, "failed", error_detail=str(e))
        logger.error(f"Text processing failed: knowledge_id={knowledge_id}, object_id={object_id}: {e}")
        raise
