from typing import Optional, List
from urllib.parse import urlparse, unquote
from datetime import datetime, timezone
import asyncio
import boto3
from fastapi import APIRouter, Depends, HTTPException, Path, Body, Form, File, UploadFile

from utils.logger import logger
from utils.auth.dependencies import require_permission, resolve_actor_id, resolve_kb_owner_id
from utils.pydantic_models_usage_limits import EstimatedMetric, Operation
from utils.usage.enforcement import enforce_operation
from utils.usage.pipeline_providers import resolve_pipeline_providers
from utils.db.knowledge_setting_ops import get_knowledge_config
from utils.files.file_utils import (
    generate_s3_presigned_urls,
    sanitize_filename,
)
from utils.common_utils import generate_object_id
from utils.validation.validation_utils import validate_file_content_type
from utils.setup_env import BUCKET_NAME, AWS_REGION
from utils.pydantic_models_knowledge import (
    BatchUploadUrlsRequest,
    BatchUploadUrlsResponse,
    BatchUploadFileResponse,
    BatchDocumentsResponse,
    BatchDocumentStatus,
    BatchStatusResponse,
    TranscribeUsing,
)
from utils.db.knowledge_ops import (
    get_documents_by_batch_id,
    get_chatbot_search_config,
    insert_extracted_text,
    create_or_update_batch,
    get_batch_by_id,
)
from utils.tasks.processing_handlers import process_file_url_handler
from utils.models.enums import DocumentProcessingMode
from utils.files.processing_modes import (
    derive_use_llama_parse,
    resolve_document_processing_mode,
    ensure_parser_configured,
)
from utils.validation.file_validation_utils import FileValidationResult
from utils.db.knowledge_ops import mark_document_invalid
from utils.tracing import tracer

router = APIRouter()


# Batch Upload URLs Generation
@router.post("/v1/{knowledge_id}/upload-urls/batch", response_model=BatchUploadUrlsResponse)
async def batch_upload_urls(
    knowledge_id: str = Path(..., description="Knowledge service identifier"),
    request: BatchUploadUrlsRequest = Body(..., description="Batch upload request"),
    _: Optional[str] = Depends(require_permission("write")),
):
    """
    Generate presigned S3 URLs for multiple files in a single request.
    Returns a batch_id for tracking.
    """
    try:
        with tracer.start_as_current_span("Node: Validate Batch Upload Request"):
            if not request.files or len(request.files) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="files array is required and must not be empty"
                )

            if len(request.files) > 50:
                raise HTTPException(
                    status_code=400,
                    detail="Maximum 50 files allowed per batch"
                )

            for file in request.files:
                if not file.file_name or not file.content_type:
                    raise HTTPException(
                        status_code=400,
                        detail="Each file must have file_name and content_type"
                    )
                validate_file_content_type(file.content_type)

        uploads: List[BatchUploadFileResponse] = []
        with tracer.start_as_current_span("Node: Generate Batch Upload Urls"):
            for file in request.files:
                try:
                    sanitized_file_name = sanitize_filename(file.file_name)
                    object_id = await generate_object_id()
                    object_key = f"rag_demo_dev/user_uploads/{object_id}_{sanitized_file_name}"
                    upload_presigned_url, download_presigned_url = await generate_s3_presigned_urls(
                        object_key,
                        file.content_type
                    )

                    uploads.append(BatchUploadFileResponse(
                        object_id=object_id,
                        file_name=file.file_name,
                        presigned_url=upload_presigned_url,
                        download_presigned_url=download_presigned_url,
                    ))
                except HTTPException:
                    raise
                except Exception as e:
                    logger.error(f"Error generating upload URL for file {file.file_name}: {e}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"An unexpected error occurred while generating upload URL for {file.file_name}: {e}"
                    )

        return BatchUploadUrlsResponse(
            uploads=uploads,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating batch upload URLs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while generating batch upload URLs: {e}"
        )


# Batch Document Processing
@router.post("/v1/{knowledge_id}/documents/batch", response_model=BatchDocumentsResponse)
async def batch_process_documents(
    knowledge_id: str = Path(..., description="Knowledge service identifier"),
    files: Optional[List[UploadFile]] = File(default=None, description="Optional collection of files to upload and process"),
    file_urls: Optional[str] = Form(default=None, description="Comma-separated file URLs to process"),
    enable_chunking: bool = Form(default=True, description="Enable chunking (default: true)"),
    use_llama_parse: Optional[bool] = Form(default=None, description="Deprecated — use document_processing_mode. Defaults to None so 'not sent' stays distinguishable from an explicit false."),
    document_processing_mode: Optional[DocumentProcessingMode] = Form(
        default=None,
        description=(
            "What the documents' content is like: auto (default — reads locally and "
            "detects), text_based, scanned, layout_aware, visual, or mixed. Applies to "
            "every file in the batch."
        ),
    ),
    chunk_size: int = Form(default=2000, description="Chunk size for processing (default: 2000)"),
    transcript_provider: TranscribeUsing = Form(default=TranscribeUsing.option1, description="Transcript provider: 'openai' or 'groq' (default: 'openai')"),
    user_id: Optional[str] = Depends(require_permission("write")),
):
    """
    Process multiple files with shared configuration.
    All files inherit identical processing settings.

    Can process files via direct upload (files parameter) or via URLs (file_urls parameter).
    Files can be uploaded via batch_upload_urls endpoint first, then their URLs
    are provided here for processing.

    All configuration parameters are provided as form fields.
    If not provided, default values will be used.
    """
    # One mode for the whole batch, resolved once. The endpoint already promises that
    # "all files inherit identical processing settings", so there is nothing per-file to
    # decide here — a caller wanting different modes sends separate batches.
    processing_mode = resolve_document_processing_mode(document_processing_mode, use_llama_parse)
    use_llama_parse = derive_use_llama_parse(processing_mode)
    logger.info(
        f"Batch process documents request: files: {files}, file_urls: {file_urls}, "
        f"enable_chunking: {enable_chunking}, document_processing_mode: {processing_mode.value}, "
        f"chunk_size: {chunk_size}, transcript_provider: {transcript_provider}"
    )
    user_id = resolve_actor_id(user_id)
    try:
        with tracer.start_as_current_span("Node: Resolve Batch Inputs"):
            parsed_file_urls: List[str] = []
            if file_urls:
                parsed_file_urls = [url.strip() for url in file_urls.split(",") if url.strip()]

            if (not files or len(files) == 0) and len(parsed_file_urls) == 0:
                raise HTTPException(
                    status_code=400,
                    detail="Either files or file_urls must be provided"
                )

            total_files = (len(files) if files else 0) + len(parsed_file_urls)
            if total_files > 50:
                raise HTTPException(
                    status_code=400,
                    detail="Maximum 50 files allowed per batch (combined files and file_urls)"
                )


        with tracer.start_as_current_span("Node: Create Batch Record"):
            batch_id = await generate_object_id()
            logger.info(f"Generated new batch_id: {batch_id} for knowledge_id: {knowledge_id}")
            total_documents = (len(files) if files else 0) + len(parsed_file_urls)
            await create_or_update_batch(
                batch_id=batch_id,
                knowledge_id=knowledge_id,
                user_id=user_id,
                status="requested",
                total_documents=total_documents
            )

        with tracer.start_as_current_span("Node: Load Knowledge Config"):
            try:
                # Reads the config, does not create it — see the equivalent call in
                # document_management.add_document for why initialization stays in PUT /config only.
                hybrid_search, embedding_model = await get_chatbot_search_config(knowledge_id)
                if embedding_model is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Knowledge config exists but is not initialized. Set it first with PUT /api/knowledge/v1/{knowledge_id}/config."
                    )
            except ValueError as e:
                raise HTTPException(
                    status_code=404,
                    detail=str(e)
                )

        with tracer.start_as_current_span("Node: Enforce Knowledge Item Limit"):
            # After the config load, because the exemption depends on which providers
            # this pipeline pays for and two of them (embedding model, enrichment
            # provider) live on the collection config rather than the request.
            #
            # All-or-nothing on purpose: a 50-file batch against 10 remaining slots is
            # one refusal, not 10 accepted and 40 dropped into a per-file status field.
            # One mode for the whole batch means one provider set for the whole batch,
            # which matches this endpoint's "all files inherit identical settings"
            # contract.
            _kb_owner_id = await resolve_kb_owner_id(knowledge_id)
            _kcfg = await get_knowledge_config(knowledge_id) or {}
            _batch_is_audio = any(
                (f.filename or "").lower().endswith(
                    (".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac")
                )
                for f in (files or [])
            ) or any(
                u.lower().split("?")[0].endswith(
                    (".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac")
                )
                for u in parsed_file_urls
            )
            _pipeline_providers = resolve_pipeline_providers(
                embedding_model=embedding_model,
                enrichment_enabled=_kcfg.get("enrichment_enabled", True),
                enrichment_provider=_kcfg.get("enrichment_provider"),
                has_file=True,
                # A batch is heterogeneous, so the provider set is the union over the
                # batch: if any file is audio, transcription is in play; the shared mode
                # decides LlamaParse for the rest. Union rather than per-file because the
                # exemption is all-or-nothing anyway — a batch that would need one key
                # the owner lacks is not exempt whichever file needs it.
                file_name="batch.mp3" if _batch_is_audio else "batch.pdf",
                content_type=None,
                processing_mode=processing_mode,
                transcript_provider=getattr(transcript_provider, "value", transcript_provider),
                has_weblink=False,
            )
            if _batch_is_audio and processing_mode is not None:
                # Mixed batch: audio contributes its transcription provider (handled
                # above) and the non-audio files still route through the shared mode.
                _pipeline_providers |= resolve_pipeline_providers(
                    embedding_model=embedding_model,
                    enrichment_enabled=False,
                    has_file=True,
                    file_name="batch.pdf",
                    processing_mode=processing_mode,
                )
            # A batch is file-URLs only, and every row it writes is content_type='file'
            # (utils/tasks/processing_handlers.py) — so the whole batch counts against
            # max_documents as well as the combined knowledge_items ceiling.
            await enforce_operation(
                owner_id=_kb_owner_id,
                operation=Operation.create_knowledge_item,
                estimated_metrics=[
                    EstimatedMetric(name="knowledge_items", value=total_files),
                    EstimatedMetric(name="documents", value=total_files),
                ],
                source="ingest:batch",
                byok_exempt_if_all_of=_pipeline_providers,
            )
            # Any non-audio file in a LlamaParse mode needs a LlamaParse key.
            _audio_ext = (".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".flac")
            _batch_has_document = any(
                not (f.filename or "").lower().endswith(_audio_ext) for f in (files or [])
            ) or any(not u.lower().split("?")[0].endswith(_audio_ext) for u in parsed_file_urls)
            if _batch_has_document:
                await ensure_parser_configured(processing_mode, _kb_owner_id)

        document_statuses: List[BatchDocumentStatus] = []

        if files and len(files) > 0:
            with tracer.start_as_current_span("Node: Process Uploaded Batch Files"):
                for file in files:
                    if not file.filename:
                        continue

                    if file.content_type:
                        validate_file_content_type(file.content_type)

                    try:
                        object_id = await generate_object_id()
                        sanitized_file_name = sanitize_filename(file.filename)
                        object_key = f"rag_demo_dev/user_uploads/{object_id}_{sanitized_file_name}"

                        with tracer.start_as_current_span("Node: Upload Batch File To S3"):
                            def upload():
                                s3_client = boto3.client("s3")
                                s3_client.upload_fileobj(
                                    file.file,
                                    BUCKET_NAME,
                                    object_key,
                                    ExtraArgs={
                                        'ACL': 'public-read',
                                        'ContentType': file.content_type or "application/octet-stream",
                                    },
                                )

                            await asyncio.get_event_loop().run_in_executor(None, upload)

                        file_url = f"https://{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{object_key}"

                        with tracer.start_as_current_span("Node: Insert Batch Document"):
                            await insert_extracted_text(
                                user_id=user_id,
                                knowledge_id=knowledge_id,
                                object_id=object_id,
                                file_name=file.filename,
                                content_type="file",
                                batch_id=batch_id,
                                processing_status='processing',
                                source_url=file_url,
                                # Batch inserts its own row and then calls the file_url
                                # handler with existing_object_id, so that handler skips
                                # its insert — the mode has to be written here or the row
                                # never gets one.
                                document_processing_mode=processing_mode,
                            )

                        logger.info(
                            f"Created document with object_id: {object_id}, batch_id: {batch_id} "
                            f"for knowledge_id: {knowledge_id}"
                        )

                        with tracer.start_as_current_span("Node: Dispatch Batch File Processing"):
                            handler_result = await process_file_url_handler(
                                file_url=file_url,
                                user_id=user_id,
                                chatbot_id=knowledge_id,
                                use_llama_parse=use_llama_parse,
                                transcript_provider=transcript_provider,
                                contextualise_chunks=False,
                                chunk_size=chunk_size,
                                hybrid_search=hybrid_search if hybrid_search is not None else True,
                                embedding_model=embedding_model,
                                enable_chunking=enable_chunking,
                                document_processing_mode=processing_mode,
                                existing_object_id=object_id,
                                batch_id=batch_id,
                            )

                        if isinstance(handler_result, FileValidationResult):
                            with tracer.start_as_current_span("Node: Mark Batch Document Invalid"):
                                await mark_document_invalid(
                                    knowledge_id=knowledge_id,
                                    object_id=object_id,
                                    error_detail=handler_result.error_detail,
                                )
                            document_statuses.append(BatchDocumentStatus(
                                object_id=object_id,
                                file_name=file.filename or "unknown",
                                status="invalid",
                                error_detail=handler_result.error_detail,
                            ))
                        else:
                            document_statuses.append(BatchDocumentStatus(
                                object_id=object_id,
                                file_name=file.filename,
                                status="processing"
                            ))

                    except Exception as e:
                        logger.error(f"Error uploading file {file.filename}: {e}")
                        document_statuses.append(BatchDocumentStatus(
                            object_id="unknown",
                            file_name=file.filename or "unknown",
                            status="failed",
                            error_detail=str(e),
                        ))

        async def _process_one_url(file_url: str) -> BatchDocumentStatus:
            parsed = urlparse(file_url)
            path = parsed.path
            filename_with_object_id = unquote(path.split("/")[-1])
            if "_" in filename_with_object_id:
                file_name = "_".join(filename_with_object_id.split("_")[1:])
            else:
                file_name = filename_with_object_id

            try:
                with tracer.start_as_current_span("Node: Dispatch Batch File Url"):
                    result = await process_file_url_handler(
                        file_url=file_url,
                        user_id=user_id,
                        chatbot_id=knowledge_id,
                        use_llama_parse=use_llama_parse,
                        transcript_provider=transcript_provider,
                        contextualise_chunks=False,
                        chunk_size=chunk_size,
                        hybrid_search=hybrid_search if hybrid_search is not None else True,
                        embedding_model=embedding_model,
                        enable_chunking=enable_chunking,
                        document_processing_mode=processing_mode,
                        batch_id=batch_id,
                    )

                if isinstance(result, FileValidationResult):
                    return BatchDocumentStatus(
                        object_id=result.object_id or "unknown",
                        file_name=result.file_name or file_name,
                        status="invalid",
                        error_detail=result.error_detail,
                    )

                return BatchDocumentStatus(
                    object_id=result,
                    file_name=file_name,
                    status="processing"
                )
            except Exception as e:
                logger.error(f"Error processing file URL {file_url} in batch {batch_id}: {e}")
                return BatchDocumentStatus(
                    object_id="unknown",
                    file_name=file_name,
                    status="failed",
                    error_detail=str(e),
                )

        if parsed_file_urls:
            with tracer.start_as_current_span("Node: Process Batch File Urls"):
                results = await asyncio.gather(
                    *[_process_one_url(url) for url in parsed_file_urls],
                    return_exceptions=False,
                )
                document_statuses.extend(results)

        with tracer.start_as_current_span("Node: Finalize Batch Status"):
            successful_count = sum(1 for doc in document_statuses if doc.status == "processing")
            failed_count = sum(1 for doc in document_statuses if doc.status in ("error", "failed", "invalid"))

            if failed_count == len(document_statuses):
                batch_status = "failed"
            elif successful_count > 0:
                batch_status = "requested"
            else:
                batch_status = "requested"

            await create_or_update_batch(
                batch_id=batch_id,
                knowledge_id=knowledge_id,
                user_id=user_id,
                status=batch_status,
                total_documents=len(document_statuses)
            )

        logger.info(
            f"Batch processing completed for batch_id: {batch_id}, knowledge_id: {knowledge_id}, "
            f"documents processed: {len(document_statuses)}"
        )

        return BatchDocumentsResponse(
            batch_id=batch_id,
            status=batch_status,
            message=f"Batch document processing initiated for {successful_count} documents"
                    + (f", {failed_count} failed" if failed_count > 0 else ""),
            documents=document_statuses
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing batch documents: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred while processing batch: {str(e)}"
        )


# Batch Status Tracking
@router.get("/v1/{knowledge_id}/batch/{batch_id}/status", response_model=BatchStatusResponse)
async def get_batch_status(
    knowledge_id: str = Path(..., description="Knowledge service identifier"),
    batch_id: str = Path(..., description="Batch identifier"),
    user_id: Optional[str] = Depends(require_permission("read")),
):
    """
    Check processing status for all documents in a batch.
    Returns aggregated counts and individual document statuses.
    """
    try:
        with tracer.start_as_current_span("Node: Load Batch Documents"):
            documents = await get_documents_by_batch_id(
                knowledge_id=knowledge_id,
                batch_id=batch_id,
                user_id=user_id
            )

            if not documents:
                raise HTTPException(
                    status_code=404,
                    detail="Batch not found"
                )

        with tracer.start_as_current_span("Node: Load Batch Metadata"):
            elapsed_since_created_ms = None
            batch_record = await get_batch_by_id(batch_id)
            if batch_record and batch_record.created_at:
                now = datetime.now(timezone.utc)
                created_at_aware = batch_record.created_at.replace(tzinfo=timezone.utc)
                elapsed_since_created_ms = round((now - created_at_aware).total_seconds() * 1000, 2)

        with tracer.start_as_current_span("Node: Build Batch Status Response"):
            total = len(documents)
            completed = 0
            processing = 0
            failed = 0
            document_statuses: List[BatchDocumentStatus] = []

            for doc in documents:
                status = doc.processing_status or "processing"

                if status == "completed":
                    completed += 1
                elif status in ["processing", "pending"]:
                    processing += 1
                elif status in ["failed", "invalid"]:
                    failed += 1
                else:
                    processing += 1

                processing_duration_ms = None
                if doc.completed_at and doc.created_at:
                    completed_at = doc.completed_at.replace(tzinfo=timezone.utc) if doc.completed_at.tzinfo is None else doc.completed_at
                    created_at = doc.created_at.replace(tzinfo=timezone.utc) if doc.created_at.tzinfo is None else doc.created_at
                    processing_duration_ms = round((completed_at - created_at).total_seconds() * 1000, 2)

                document_statuses.append(BatchDocumentStatus(
                    object_id=doc.object_id,
                    file_name=doc.file_name or "",
                    status=status,
                    processing_duration_ms=processing_duration_ms,
                    error_detail=doc.error_detail,
                ))

        return BatchStatusResponse(
            batch_id=batch_id,
            total=total,
            completed=completed,
            processing=processing,
            failed=failed,
            documents=document_statuses,
            elapsed_since_created_ms=elapsed_since_created_ms,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking batch status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error checking batch status: {str(e)}"
        )
