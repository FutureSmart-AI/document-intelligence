"""
Task utility functions for chatbot knowledge management.
Contains Celery task dispatch and management operations.
"""

from typing import Optional
from utils.celery_workers.app import app as celery_app
from utils.logger import logger
from utils.pydantic_models_knowledge import TranscribeUsing, ScrapeUsing, ScrapingMethod


def dispatch_file_processing_task(
    task_id: str,
    object_id: str,
    knowledge_id: str,
    file_url: str,
    user_id: str,
    file_name: str,
    content_type: str,
    use_llama_parse: bool,
    contextualise_chunks: bool,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    transcript_provider: TranscribeUsing,
    enable_chunking: bool = True,
    extra_metadata: Optional[dict] = None,
    reindex: bool = False,
    document_processing_mode: Optional[str] = None,
) -> None:
    """
    Dispatch a Celery task for file processing and indexing.

    Args:
        task_id: Unique task identifier
        object_id: Object identifier
        knowledge_id: Knowledge identifier
        file_url: S3 URL of the file
        user_id: User identifier
        file_name: Original filename
        content_type: MIME type of the file
        use_llama_parse: Whether to use LlamaParse (legacy — derived from
            document_processing_mode; see below for why it is still sent)
        document_processing_mode: What the document's content is like
            ("text_based", "scanned", "layout_aware", "visual", "mixed")
        contextualise_chunks: Whether to contextualise chunks
        chunk_size: Size of chunks for processing
        hybrid_search: Hybrid search configuration
        embedding_model: Embedding model to use
        transcript_provider: Transcription provider
    """
    try:
        celery_app.send_task(
            "workers.tasks.process_and_index_file",
            task_id=task_id,
            kwargs={
                "object_id": object_id,
                "knowledge_id": knowledge_id,
                "file_url": file_url,
                "user_id": user_id,
                "file_name": file_name,
                "content_type": content_type,
                # Both the mode and the legacy boolean go on the wire during the
                # rollout. A worker on the previous build ignores the mode and still
                # reads the boolean; a worker on the new build prefers the mode and only
                # falls back to the boolean. That way neither side has to deploy first,
                # and messages already sitting in the queue keep working either way.
                # Drop use_llama_parse once every worker is upgraded.
                "use_llama_parse": use_llama_parse,
                "document_processing_mode": document_processing_mode,
                "contextualise_chunks": contextualise_chunks,
                "chunk_size": chunk_size,
                "hybrid_search": hybrid_search,
                "embedding_model": embedding_model,
                "transcript_provider": transcript_provider,
                "enable_chunking": enable_chunking,
                "extra_metadata": extra_metadata,
                "reindex": reindex,
            },
            headers={
                'message-deduplication-id': object_id,
                'message-group-id': knowledge_id
            },
        )
        logger.info(f"Dispatched file processing task: task_id={task_id}, user_id={user_id}, knowledge_id={knowledge_id}, object_id={object_id}, file_name={file_name}")
    except Exception as e:
        logger.error(f"Error dispatching file processing task: {e}")
        raise


def dispatch_text_processing_task(
    task_id: str,
    user_id: str,
    object_id: str,
    knowledge_id: str,
    text: str,
    text_url: str,
    file_name: str,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    contextualise_chunks: bool,
    enable_chunking: bool = True
) -> None:
    """
    Dispatch a Celery task for text processing and indexing.
    
    Args:
        task_id: Unique task identifier
        user_id: User identifier
        chatbot_id: Chatbot identifier
        knowledge_id: Knowledge identifier
        text: Text content to process
        text_url: S3 URL of the text
        file_name: Generated filename for the text
        chunk_size: Size of chunks for processing
        hybrid_search: Hybrid search configuration
        embedding_model: Embedding model to use
        contextualise_chunks: Whether to contextualise chunks
    """
    try:
        celery_app.send_task(
            "workers.tasks.process_and_index_text",
            task_id=task_id,
            kwargs={
                "user_id": user_id,
                "object_id": object_id,
                "knowledge_id": knowledge_id,
                "text": text,
                "text_url": text_url,
                "file_name": file_name,
                "chunk_size": chunk_size,
                "hybrid_search": hybrid_search,
                "embedding_model": embedding_model,
                "contextualise_chunks": contextualise_chunks,
                "enable_chunking": enable_chunking
            },
            headers={
                'message-deduplication-id': object_id,
                'message-group-id': knowledge_id
            },
        )
        logger.info(f"Dispatched text processing task: task_id={task_id}, user_id={user_id}, knowledge_id={knowledge_id}, object_id={object_id}, text_length={len(text)}")
    except Exception as e:
        logger.error(f"Error dispatching text processing task: {e}")
        raise


def dispatch_weblink_processing_task(
    task_id: str,
    user_id: str,
    object_id: str,
    knowledge_id: str,
    weblink: str,
    use_llama_parse: bool,
    scrape_using: ScrapeUsing,
    scraping_method: ScrapingMethod,
    max_depth: int,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    contextualise_chunks: bool,
    enable_chunking: bool = True,
    document_processing_mode: Optional[str] = None,
) -> None:
    """
    Dispatch a Celery task for weblink processing and indexing.

    Args:
        task_id: Unique task identifier
        user_id: User identifier
        chatbot_id: Chatbot identifier
        knowledge_id: Knowledge identifier
        weblink: URL to process
        use_llama_parse: Whether to use LlamaParse
        scrape_using: Scraping service to use
        scraping_method: Scraping method
        max_depth: Maximum depth for scraping
        chunk_size: Size of chunks for processing
        hybrid_search: Hybrid search configuration
        embedding_model: Embedding model to use
        contextualise_chunks: Whether to contextualise chunks
        document_processing_mode: What the document's content is like
            ("text_based", "scanned", "layout_aware", "visual", "mixed"). Only takes effect
            when the weblink resolves to a file (PDF/DOCX/plain text) rather than an HTML
            page — an HTML scrape has no scanned/layout/visual dimension for this to describe.
    """
    try:
        celery_app.send_task(
            "workers.tasks.process_and_index_weblink",
            task_id=task_id,
            kwargs={
                "user_id": user_id,
                "object_id": object_id,
                "knowledge_id": knowledge_id,
                "weblink": weblink,
                "use_llama_parse": use_llama_parse,
                "scrape_using": scrape_using,
                "scraping_method": scraping_method,
                "max_depth": max_depth,
                "chunk_size": chunk_size,
                "hybrid_search": hybrid_search,
                "embedding_model": embedding_model,
                "contextualise_chunks": contextualise_chunks,
                "enable_chunking": enable_chunking,
                "document_processing_mode": document_processing_mode,
            },
            headers={
                'message-deduplication-id': object_id,
                'message-group-id': knowledge_id
            }
        )
        logger.info(f"Dispatched weblink processing task: task_id={task_id}, user_id={user_id}, knowledge_id={knowledge_id}, object_id={object_id}, weblink={weblink}, mode={document_processing_mode}")
    except Exception as e:
        logger.error(f"Error dispatching weblink processing task: {e}")
        raise


def log_task_dispatch(task_type: str, task_id: str, **kwargs) -> None:
    """
    Log task dispatch information.
    
    Args:
        task_type: Type of task being dispatched
        task_id: Unique task identifier
        **kwargs: Additional logging parameters
    """
    logger.info(f"Generating Celery jobs for task_id={task_id} {task_type} processing and indexing ...")
