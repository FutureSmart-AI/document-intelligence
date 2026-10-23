"""
Processing handlers for different types of knowledge content.
Contains functions for processing files, URLs, text, and weblinks.
"""

from io import BytesIO
from typing import Optional, Union
import traceback
from fastapi import UploadFile, HTTPException
from utils.files.s3_utils import upload_extracted_text_to_s3, upload_file_to_s3
from utils.pydantic_models_knowledge import TranscribeUsing, ScrapeUsing, ScrapingMethod
from utils.models.enums import DocumentProcessingMode
from utils.logger import logger

# Import utility functions from other modules
from utils.common_utils import (
    generate_object_id,
    generate_task_id,
    get_valid_filename,
    validate_text_content,
)
from utils.files.file_utils import (
    upload_file_to_s3_storage,
)
from utils.validation.file_validation_utils import (
    process_uploaded_file,
    validate_file_from_url,
    FileValidationResult,
)
from utils.validation.validation_utils import (
    validate_weblink_url,
)
from utils.db.db_utils import insert_extracted_text
from utils.db.knowledge_ops import mark_document_processing
from utils.tasks.task_utils import (
    dispatch_file_processing_task,
    dispatch_text_processing_task,
    dispatch_weblink_processing_task,
    log_task_dispatch,
)

async def process_uploaded_file_handler(
    file: UploadFile,
    user_id: Optional[str],
    chatbot_id: Optional[str],
    use_llama_parse: bool,
    transcript_provider: TranscribeUsing,
    contextualise_chunks: bool,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    enable_chunking: bool = True,
    extra_metadata: Optional[dict] = None,
    reindex: bool = False,
    existing_object_id: Optional[str] = None,
    document_processing_mode: Optional[str] = None,
) -> str:
    """Process an uploaded file and dispatch Celery task."""
    try:
        logger.info(
            f"Processing uploaded file: {file.filename} for chatbot {chatbot_id}, "
            f"mode={document_processing_mode}"
        )

        # Validate the file (type, page count). Text extraction deliberately does NOT
        # happen here — see process_uploaded_file for why that moved to the worker.
        await process_uploaded_file(file, use_llama_parse, transcript_provider)

        # Reset file pointer for S3 upload
        await file.seek(0)

        # Reindex reuses the existing object_id so the doc is replaced (same S3 key, same
        # DB row) instead of duplicated; a fresh ingest mints a new id.
        object_id = existing_object_id or await generate_object_id()
        file_url = await upload_file_to_s3_storage(object_id, file.filename, file.content_type, file.file)

        if existing_object_id:
            # Reindex: keep the existing DB row, flip it back to processing, and refresh
            # the freshness metadata so last_modified tracks the new source revision.
            await mark_document_processing(knowledge_id=chatbot_id, object_id=object_id, user_id=user_id,
                                           extra_metadata=extra_metadata,
                                           document_processing_mode=document_processing_mode)
        else:
            # Insert database record
            await insert_extracted_text(
                user_id=user_id,
                knowledge_id=chatbot_id,
                object_id=object_id,
                file_name=file.filename,
                use_llama_parse=use_llama_parse,
                contextualise_chunks=contextualise_chunks,
                chunk_size=chunk_size,
                content_type="file",
                source_url=file_url,
                extra_metadata=extra_metadata,
                document_processing_mode=document_processing_mode,
            )

        # Dispatch Celery task
        task_id = await generate_task_id()
        log_task_dispatch("file", task_id)

        dispatch_file_processing_task(
            task_id=task_id,
            object_id=object_id,
            file_url=file_url,
            user_id=user_id,
            knowledge_id=chatbot_id,
            file_name=file.filename,
            content_type=file.content_type,
            use_llama_parse=use_llama_parse,
            contextualise_chunks=contextualise_chunks,
            chunk_size=chunk_size,
            hybrid_search=hybrid_search,
            embedding_model=embedding_model,
            transcript_provider=transcript_provider,
            enable_chunking=enable_chunking,
            extra_metadata=extra_metadata,
            reindex=reindex,
            document_processing_mode=document_processing_mode,
        )

        return object_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing uploaded file {file.filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {e}")


async def process_file_url_handler(
    file_url: str,
    user_id: Optional[str],
    chatbot_id: Optional[str],
    use_llama_parse: bool,
    transcript_provider: TranscribeUsing,
    contextualise_chunks: bool,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    *,
    existing_object_id: Optional[str] = None,
    enable_chunking: bool = True,
    batch_id: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
    reindex: bool = False,
    document_processing_mode: Optional[str] = None,
) -> Union[str, FileValidationResult]:
    """Process a file from URL and dispatch Celery task.
    Returns object_id on success, or FileValidationResult on validation failure."""
    try:
        logger.info(
            f"Processing file URL: {file_url} for chatbot {chatbot_id}, "
            f"mode={document_processing_mode}"
        )

        # Validate file URL and extract metadata
        result = await validate_file_from_url(
            file_url, max_pdf_pages=10, min_length=10
        )
        if not result.success:
            # Insert DB record with 'invalid' status if not in reindex mode
            if not existing_object_id:
                invalid_object_id = result.object_id or await generate_object_id()
                result.object_id = invalid_object_id
                await insert_extracted_text(
                    user_id=user_id,
                    knowledge_id=chatbot_id,
                    object_id=invalid_object_id,
                    file_name=result.file_name,
                    content_type="file",
                    batch_id=batch_id,
                    processing_status="invalid",
                    source_url=file_url,
                    error_detail=result.error_detail,
                )
            return result

        file_name = result.file_name
        content_type = result.content_type
        object_id = result.object_id

        # Reindex mode: use existing object_id and skip DB insert
        if existing_object_id:
            object_id = existing_object_id
            # Refresh freshness metadata (last_modified) so the dedup key tracks the
            # new source revision instead of staying frozen at the original ingest.
            await mark_document_processing(knowledge_id=chatbot_id, object_id=object_id, user_id=user_id,
                                           extra_metadata=extra_metadata,
                                           document_processing_mode=document_processing_mode)
        else:
            # Insert database record
            await insert_extracted_text(
                user_id=user_id,
                knowledge_id=chatbot_id,
                object_id=object_id,
                file_name=file_name,
                use_llama_parse=use_llama_parse,
                contextualise_chunks=contextualise_chunks,
                chunk_size=chunk_size,
                content_type="file",
                batch_id=batch_id,
                source_url=file_url,
                extra_metadata=extra_metadata,
                document_processing_mode=document_processing_mode,
            )

        # Dispatch Celery task
        task_id = await generate_task_id()
        log_task_dispatch("file", task_id)

        dispatch_file_processing_task(
            task_id=task_id,
            object_id=object_id,
            knowledge_id=chatbot_id,
            file_url=file_url,
            user_id=user_id,
            file_name=file_name,
            content_type=content_type,
            use_llama_parse=use_llama_parse,
            contextualise_chunks=contextualise_chunks,
            chunk_size=chunk_size,
            hybrid_search=hybrid_search,
            embedding_model=embedding_model,
            transcript_provider=transcript_provider,
            enable_chunking=enable_chunking,
            extra_metadata=extra_metadata,
            reindex=reindex,
            document_processing_mode=document_processing_mode,
        )

        return object_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing file URL {file_url}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing file URL: {e}")


async def process_text_content_handler(
    text: Optional[str],
    text_url: Optional[str],
    user_id: Optional[str],
    knowledge_id: str,
    contextualise_chunks: bool,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    *,
    existing_object_id: Optional[str] = None,
    enable_chunking: bool = True,
) -> str:
    """Process plain text content and dispatch Celery task."""
    try:
        logger.info(f"Processing text content for knowledge {knowledge_id}")
        
        # Validate text content
            # raise HTTPException(status_code=400, detail="Text content not able to be uploaded to S3")
        # Generate or reuse object ID and filename
        object_id = existing_object_id or await generate_object_id()
        if text and not text_url:
            validate_text_content(text, min_length=10)
            file_name = await get_valid_filename(text)
            file_obj = BytesIO(text.encode('utf-8'))
            text_url = await upload_file_to_s3(knowledge_id, file_name, "text/plain", file_obj)
        
        # Insert database record unless skipping; mark processing if reindex
        if existing_object_id:
            await mark_document_processing(knowledge_id=knowledge_id, object_id=object_id, user_id=user_id, content=text)
        else:
            await insert_extracted_text(
                user_id=user_id,
                knowledge_id=knowledge_id,
                object_id=object_id,
                contextualise_chunks=contextualise_chunks,
                chunk_size=chunk_size,
                file_name=file_name,
                content_type="plain_text",
                content=text,
                # Not the caller's choice, and deliberately so: pasted text has no parse
                # step to configure — it is already text — so text_based is the only
                # honest description of how it was read, whatever mode the request named.
                document_processing_mode=DocumentProcessingMode.TEXT_BASED.value,
            )
        
        # Dispatch Celery task
        task_id = await generate_task_id()
        log_task_dispatch("text", task_id)
        
        dispatch_text_processing_task(
            task_id=task_id,
            user_id=user_id,
            knowledge_id=knowledge_id,
            object_id=object_id,
            text=text,
            text_url=text_url,
            file_name=file_name,
            chunk_size=chunk_size,
            hybrid_search=hybrid_search,
            embedding_model=embedding_model,
            contextualise_chunks=contextualise_chunks,
            enable_chunking=enable_chunking
        )
        
        return object_id
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing text content: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error processing text: {e}")


async def process_weblink_handler(
    weblink: str,
    user_id: Optional[str],
    chatbot_id: Optional[str],
    use_llama_parse: bool,
    scrape_using: ScrapeUsing,
    scraping_method: ScrapingMethod,
    max_depth: int,
    contextualise_chunks: bool,
    chunk_size: Optional[int],
    hybrid_search: bool,
    embedding_model: str,
    *,
    existing_object_id: Optional[str] = None,
    enable_chunking: bool = True,
    document_processing_mode: Optional[str] = None,
) -> str:
    """Process a weblink and dispatch Celery task.

    document_processing_mode only takes effect if the weblink turns out to be a file
    (PDF/DOCX/plain text) rather than an HTML page — see weblink_utils._process_file_content
    in fs-agent-workers, which is the only reader that consults it.
    """
    try:
        logger.info(f"Processing weblink: {weblink} for chatbot {chatbot_id}")
        
        # Validate weblink
        validate_weblink_url(weblink)

        # Removed two dead limits that used to sit here:
        #
        # - validate_weblink_count(max_limit=20): its `raise` was commented out with
        #   the note "to maintain legacy behavior", so it logged an error and returned.
        #   The caller still ran get_user_weblink_count first, so every weblink upload
        #   paid for a real COUNT query feeding a check that could not fail.
        # - validate_firecrawl_usage_limits(): computed `estimated_pages` and did
        #   nothing with it. Its `except ImportError` was a fossil of a removed call
        #   into the old standalone usage-limits service.
        #
        # Both were keyed by user_id (which matches nothing else in the quota system).
        # The real ceiling now lives on OwnerPlatformLimits.max_knowledge_items and is
        # enforced in the routers before the ingest reaches this handler.
        # See docs/plan/usage-and-limits/archive/design-limit-enforcement.md §8a.
        
        # Generate or reuse object ID
        object_id = existing_object_id or await generate_object_id()
        
        # Insert database record unless skipping; mark processing if reindex
        if existing_object_id:
            await mark_document_processing(knowledge_id=chatbot_id, object_id=object_id, user_id=user_id)
        else:
            await insert_extracted_text(
                user_id=user_id,
                knowledge_id=chatbot_id,
                object_id=object_id,
                file_name=object_id,
                use_llama_parse=use_llama_parse,
                url=weblink,
                contextualise_chunks=contextualise_chunks,
                chunk_size=chunk_size,
                content_type="url",
                # Recorded even though it only bites when the URL turns out to be a file
                # (see this handler's docstring): the listing shows what was requested,
                # and a weblink that resolves to a PDF is read in exactly this mode.
                document_processing_mode=document_processing_mode,
            )
        
        # Dispatch Celery task
        task_id = await generate_task_id()
        log_task_dispatch("weblink", task_id)
        
        dispatch_weblink_processing_task(
            task_id=task_id,
            user_id=user_id,
            object_id=object_id,
            knowledge_id=chatbot_id,
            weblink=weblink,
            use_llama_parse=use_llama_parse,
            scrape_using=scrape_using,
            scraping_method=scraping_method,
            max_depth=max_depth,
            chunk_size=chunk_size,
            hybrid_search=hybrid_search,
            embedding_model=embedding_model,
            contextualise_chunks=contextualise_chunks,
            enable_chunking=enable_chunking,
            document_processing_mode=document_processing_mode,
        )

        return object_id

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing weblink {weblink}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing weblink: {e}")
