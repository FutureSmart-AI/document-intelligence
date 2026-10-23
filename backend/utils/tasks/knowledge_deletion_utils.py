import asyncio
import time

from utils.logger import logger
from utils.files.retrieval_utils import (
    _get_document_indexer,
    _get_chunk_indexer,
)
from utils.db.db_utils import (
    delete_chatbot_knowledge,
    set_knowledge_deletion_error,
)


async def perform_knowledge_deletion(
    knowledge_id: str,
    object_ids: list,
    user_id: str = None,
    hybrid_search: bool = False,
    embedding_model: str = None,
):
    # Shared log context — included in every line so logs are grep-able by knowledge_id or object_id
    ctx = f"knowledge_id={knowledge_id} object_ids={object_ids}"
    t_total = time.time()

    try:
        logger.info(f"Deletion started | {ctx}")

        # Step 1: Remove vectors from Qdrant (document-level and chunk-level collections in parallel)
        t = time.time()
        document_indexer = await _get_document_indexer(embedding_model)
        chunk_indexer = await _get_chunk_indexer(embedding_model)
        await asyncio.gather(
            document_indexer.delete_document(object_ids, knowledge_id=knowledge_id),
            chunk_indexer.delete_chunks(object_ids, knowledge_id=knowledge_id),
        )
        logger.info(f"Qdrant deletion complete | {ctx} | took={time.time() - t:.2f}s")

        # Step 2: Soft-delete the document row in Postgres and set deletion_status=completed
        t = time.time()
        await delete_chatbot_knowledge(knowledge_id, object_ids, user_id)
        logger.info(f"DB deletion complete | {ctx} | took={time.time() - t:.2f}s")

    except Exception as e:
        # Mark deletion_status=error so the polling endpoint surfaces a terminal state
        await set_knowledge_deletion_error(user_id, knowledge_id, object_ids)
        logger.error(f"Deletion failed | {ctx} | error={e}")
        raise

    finally:
        logger.info(f"Deletion finished | {ctx} | total={time.time() - t_total:.2f}s")
