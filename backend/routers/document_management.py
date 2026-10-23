from typing import Optional, List
import json
import ast

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File, Form, Path, Query, BackgroundTasks
from fastapi.responses import JSONResponse

from utils.files.parsing_utils import read_txt_to_plain_text
from utils.logger import logger
from utils.auth.dependencies import require_permission, resolve_actor_id, resolve_kb_owner_id
from utils.pydantic_models_usage_limits import EstimatedMetric, Operation
from utils.usage.enforcement import enforce_operation
from utils.usage.pipeline_providers import resolve_pipeline_providers
from utils.db.knowledge_setting_ops import get_knowledge_config

from utils.files.file_utils import (
    generate_s3_presigned_urls,
    sanitize_filename,
    enhance_knowledge_with_presigned_urls,
    generate_presigned_url,
    generate_presigned_url_v2,
    # read_md_from_s3_presigned,
)
from utils.common_utils import generate_object_id
from utils.validation.input_validation_utils import validate_chatbot_input
from utils.models.enums import DocumentProcessingMode
from utils.files.processing_modes import (
    derive_use_llama_parse,
    resolve_document_processing_mode,
)
from utils.validation.validation_utils import (
    normalize_empty_strings,
    validate_chunk_size,
    validate_required_data_provided,
    validate_knowledge_item_exists,
    validate_file_content_type,
    validate_max_depth,
)
from utils.tasks.processing_handlers import (
    process_uploaded_file_handler,
    process_file_url_handler,
    process_text_content_handler,
    process_weblink_handler,
)
from utils.validation.file_validation_utils import FileValidationResult
from utils.tasks.knowledge_deletion_utils import perform_knowledge_deletion
from utils.db.db_utils import (
    retrieve_knowledge_processing_status,
    retrieve_knowledge_deletion_status,
    set_initial_deletion_status,
    get_chatbot_search_config,
    retrieve_chatbot_knowledge,
    retrieve_chatbot_knowledge_v2,
)
from utils.db.enrichment_db_utils import upsert_field_manifest
from utils.pydantic_models_knowledge import (
    TranscribeUsing,
    ScrapeUsing,
    ScrapingMethod,
    GetDocumentSummariesRequest,
)
from utils.files.retrieval_utils import get_summaries_service, list_documents_get_alias, search_documents_service
from utils.pydantic_models_retrieval import DocumentListResponse, DocumentSummariesResponse, SummaryItem
from utils.tracing import tracer
from utils.db.knowledge_ops import get_document_by_source_id


router = APIRouter()


# 8. Get Upload URL
@router.post("/v1/{knowledge_id}/upload-url")
async def get_upload_url(
    knowledge_id: str,
    file_name: str = Form(...),
    content_type: str = Form(...),
    _: Optional[str] = Depends(require_permission("write")),
):
    try:
        with tracer.start_as_current_span("Node: Validate Upload Request"):
            if not file_name:
                raise HTTPException(status_code=400, detail="file_name is required")
            if not content_type:
                raise HTTPException(status_code=400, detail="content_type is required")
            validate_file_content_type(content_type)

        with tracer.start_as_current_span("Node: Prepare Upload Target"):
            sanitized_file_name = sanitize_filename(file_name)
            object_id = await generate_object_id()
            object_key = f"rag_demo_dev/user_uploads/{object_id}_{sanitized_file_name}"

        with tracer.start_as_current_span("Node: Generate Upload Urls"):
            upload_presigned_url, download_presigned_url = await generate_s3_presigned_urls(object_key, content_type)

        return {
            "object_id": object_id,
            "presigned_url": upload_presigned_url,
            "download_presigned_url": download_presigned_url,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating upload URL: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while generating upload URL: {e}")


# 9. Add Document
@router.post("/v1/{knowledge_id}/documents")
async def add_document(
    knowledge_id: str,
    # user_id: Optional[str] = Form(None),  # derived from session
    text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    file_url: Optional[str] = Form(None),
    weblink: Optional[str] = Form(None),
    # Deprecated in favour of document_processing_mode, kept because existing clients
    # still send it. Defaults to None rather than False so that "not sent" is
    # distinguishable from "explicitly asked for the plain-text reader" — without that,
    # an old client's implicit False would look like a deliberate choice and could
    # override the new field.
    use_llama_parse: Optional[bool] = Form(None),
    document_processing_mode: Optional[DocumentProcessingMode] = Form(
        None,
        description=(
            "What the document's content is like: auto (default — reads locally and "
            "detects), text_based, scanned, layout_aware, visual, or mixed. Ignored for "
            "plain text content, which has no scanned/layout/visual dimension. For a "
            "weblink it applies two ways: a URL that resolves to a file (PDF/DOCX/plain "
            "text) picks LlamaParse settings, exactly like an uploaded file; a URL that "
            "resolves to an HTML page picks the scraper (auto/text_based/scanned -> "
            "Newspaper, layout_aware/visual/mixed -> FireCrawl with boilerplate-stripping "
            "off) — see "
            "fs-agent-workers/docs/plan/document-processing/archive/design-weblink-modes.md for the full table. Omit to "
            "inherit the legacy use_llama_parse behaviour."
        ),
    ),
    transcript_provider: TranscribeUsing = Form(default=TranscribeUsing.option1),
    # Deprecated: the scraper is now chosen from document_processing_mode (see
    # fs-agent-workers celery_utils/processing/processing_modes.py:resolve_html_scraper),
    # the same way the mode already chooses LlamaParse settings for a file. Kept accepted,
    # not removed, so a caller that still sends it does not break — its value has no effect.
    scrape_using: ScrapeUsing = Form(
        default=ScrapeUsing.option2,
        description="Deprecated, no effect. The scraper is now chosen from document_processing_mode.",
    ),
    scraping_method: ScrapingMethod = Form(default=ScrapingMethod.option1),
    # Default 1, not 0. Despite the name this is not a depth limit — the worker never
    # passes it to FireCrawl, whose crawl is bounded by its own 10-page CRAWL_LIMIT. What
    # it actually gates is whether crawled child pages become documents at all
    # (`weblink_utils.py`: `if max_depth > 0 and scraping_method == 'crawl'`). At 0, a
    # crawl was billed for up to 10 pages and then had every child silently discarded —
    # so "crawl" behaved exactly like "scrape" while costing more. 1 makes the results
    # of a crawl actually get indexed, which is the only reason to ask for one.
    max_depth: Optional[int] = Form(default=1),
    chunk_size: Optional[int] = Form(2000),
    enable_chunking: Optional[bool] = Form(True),
    extra_metadata: Optional[str] = Form(None, description="Optional JSON string of arbitrary key-value metadata"),
    user_id: Optional[str] = Depends(require_permission("write")),
):
    try:
        contextualise_chunks = False # deprecated
        with tracer.start_as_current_span("Node: Validate Document Request"):
            validate_max_depth(max_depth)
            if file and file.content_type:
                validate_file_content_type(file.content_type)


        with tracer.start_as_current_span("Node: Resolve Processing Mode"):
            # Settled first, before anything else reads either field, so everything
            # downstream carries one unambiguous answer instead of re-deriving it from
            # two fields that are allowed to disagree.
            processing_mode = resolve_document_processing_mode(document_processing_mode, use_llama_parse)
            use_llama_parse = derive_use_llama_parse(processing_mode)
            logger.info(f"Document processing mode for knowledge_id={knowledge_id}: {processing_mode.value}")

        with tracer.start_as_current_span("Node: Normalize Document Input"):
            user_id = resolve_actor_id(user_id)
            (user_id, knowledge_id, text, weblink, use_llama_parse, scrape_using,
            scraping_method, transcript_provider, contextualise_chunks, chunk_size) = validate_chatbot_input(
                user_id=user_id, chatbot_id=knowledge_id, text=text, weblink=weblink,
                use_llama_parse=use_llama_parse, scrape_using=scrape_using,
                scraping_method=scraping_method, transcript_provider=transcript_provider,
                contextualise_chunks=contextualise_chunks, chunk_size=chunk_size
            )

        logger.info(f"Processing request for user_id: {user_id}, knowledge_id: {knowledge_id}")

        with tracer.start_as_current_span("Node: Finalize Document Input"):
            file, chunk_size = normalize_empty_strings(file, chunk_size)
            chunk_size = validate_chunk_size(chunk_size)

        with tracer.start_as_current_span("Node: Load Knowledge Config"):
            # Ingestion reads the config; it deliberately does not create it. PUT /config is the
            # single place a collection is initialized, so failing loudly here is the intended
            # behaviour for a collection that skipped that step — the alternative (initializing
            # lazily on first upload) puts the same defaulting logic in two places and leaves no
            # single call that can be pointed at as where setup happened.
            hybrid_search, embedding_model = await get_chatbot_search_config(knowledge_id)
            if not embedding_model:
                raise HTTPException(status_code=400, detail="Embedding model not found. Please set the embedding model in the knowledge config.")

        with tracer.start_as_current_span("Node: Enforce Knowledge Item Limit"):
            # Placed after the config load on purpose: the exemption depends on which
            # providers this pipeline will pay for, and two of them (embedding model,
            # enrichment provider) are collection config, not request fields.
            #
            # The ceiling counts any ingested unit — file, URL, text, scraped page,
            # crawled child, synced page — across every knowledge base the owner has.
            # It is waived entirely when the owner funds every provider in the pipeline;
            # when they do not, the 429 names the missing ones so the client can prompt
            # for the specific key that would lift the restriction.
            _kb_owner_id = await resolve_kb_owner_id(knowledge_id)
            _kcfg = await get_knowledge_config(knowledge_id) or {}
            _pipeline_providers = resolve_pipeline_providers(
                embedding_model=embedding_model,
                enrichment_enabled=_kcfg.get("enrichment_enabled", True),
                enrichment_provider=_kcfg.get("enrichment_provider"),
                has_file=bool(file),
                file_name=getattr(file, "filename", None) if file else None,
                content_type=getattr(file, "content_type", None) if file else None,
                processing_mode=processing_mode,
                transcript_provider=getattr(transcript_provider, "value", transcript_provider),
                has_weblink=bool(weblink),
                scrape_using=getattr(scrape_using, "value", scrape_using),
            )
            # One knowledge item per request here. A crawl fans out into more rows in
            # the worker; that guard lives in fs-agent-workers (see the task docs).
            #
            # The source-specific metric matches what this row's content_type will be
            # (see utils/tasks/processing_handlers.py): a file or file-by-URL writes
            # content_type='file' and counts against max_documents; a weblink writes
            # 'url' and counts against max_urls; pasted text ('plain_text') has no
            # sub-cap, only the combined knowledge_items ceiling.
            _ingest_metrics = [EstimatedMetric(name="knowledge_items", value=1)]
            if file is not None or file_url:
                _ingest_metrics.append(EstimatedMetric(name="documents", value=1))
            elif weblink:
                _ingest_metrics.append(EstimatedMetric(name="urls", value=1))
            await enforce_operation(
                owner_id=_kb_owner_id,
                operation=Operation.create_knowledge_item,
                estimated_metrics=_ingest_metrics,
                source="ingest:single",
                byok_exempt_if_all_of=_pipeline_providers,
            )

        with tracer.start_as_current_span("Node: Parse Extra Metadata"):
            parsed_extra_metadata: Optional[dict] = None
            if extra_metadata and extra_metadata.strip():
                try:
                    parsed_extra_metadata = json.loads(extra_metadata)
                except json.JSONDecodeError:
                    raise HTTPException(status_code=422, detail="extra_metadata must be a valid JSON string.")
                if not isinstance(parsed_extra_metadata, dict):
                    raise HTTPException(status_code=422, detail="extra_metadata must be a JSON object (dict), not a list or scalar.")
                _RESERVED_KEYS = {"object_id", "knowledge_id", "created_at", "chunk_text"}
                bad_keys = _RESERVED_KEYS.intersection(parsed_extra_metadata.keys())
                if bad_keys:
                    raise HTTPException(
                        status_code=422,
                        detail=f"extra_metadata contains reserved keys: {sorted(bad_keys)}. Reserved: {sorted(_RESERVED_KEYS)}."
                    )

        reindex = False
        existing_object_id: Optional[str] = None
        with tracer.start_as_current_span("Node: Freshness Check"):
            if parsed_extra_metadata:
                _source_id = parsed_extra_metadata.get("source_id")
                _last_modified = parsed_extra_metadata.get("last_modified")
                if _source_id and _last_modified is not None:
                    try:
                        _incoming_ts = int(_last_modified)
                    except (TypeError, ValueError):
                        _incoming_ts = None
                    # DB-authoritative dedup: the document row is written synchronously at
                    # submit, so it exists even while the page is still indexing. This both
                    # prevents reindex from minting a duplicate object_id and closes the race
                    # where the same source_id is posted twice before its chunks land.
                    existing = await get_document_by_source_id(
                        knowledge_id=str(knowledge_id),
                        source_id=str(_source_id),
                    )
                    if existing is not None:
                        stored_ts = existing.get("last_modified")
                        # In flight → never spawn a second copy of an in-progress page.
                        if existing.get("processing_status") == "processing":
                            logger.info(f"Skipping reindex: source_id='{_source_id}' already processing (object_id={existing['object_id']})")
                            return JSONResponse({"status": "skipped", "source_id": _source_id})
                        # A prior attempt that ended in 'failed' must ALWAYS be retried,
                        # even when the source's timestamp is unchanged — otherwise the
                        # timestamp dedup below would skip it forever and the failure sticks
                        # (e.g. a transient download/parse error never self-heals).
                        _prior_failed = existing.get("processing_status") == "failed"
                        # Up to date → skip (but never short-circuit a failed doc's retry).
                        if (not _prior_failed and _incoming_ts is not None
                                and stored_ts is not None and stored_ts >= _incoming_ts):
                            logger.info(f"Skipping reindex: source_id='{_source_id}' last_modified={_incoming_ts} <= stored={stored_ts}")
                            return JSONResponse({"status": "skipped", "source_id": _source_id})
                        # Changed, prior failure, or stored ts unknown → reindex IN PLACE:
                        # reuse the existing object_id so the doc is replaced, not duplicated.
                        reindex = True
                        existing_object_id = existing["object_id"]
                        logger.info(f"Reindex triggered: source_id='{_source_id}' stored={stored_ts} < new={_incoming_ts} (reusing object_id={existing_object_id})")

        with tracer.start_as_current_span("Node: Sync Field Manifest"):
            # Register the user-defined field names so GET /enrichment can return the
            # complete field set deterministically (not sampled). Non-fatal: a manifest
            # failure must never block ingestion.
            if parsed_extra_metadata:
                try:
                    await upsert_field_manifest(knowledge_id, parsed_extra_metadata)
                except Exception as e:
                    logger.warning(f"Field manifest upsert failed (non-fatal) for {knowledge_id}: {e}")

        with tracer.start_as_current_span("Node: Validate Data Source"):
            validate_required_data_provided(file, text, weblink, file_url)

        object_id: Optional[str] = None
        if file:
            with tracer.start_as_current_span("Node: Dispatch File Upload"):
                object_id = await process_uploaded_file_handler(
                    file,
                    user_id,
                    knowledge_id,
                    use_llama_parse,
                    transcript_provider,
                    contextualise_chunks,
                    chunk_size,
                    hybrid_search,
                    embedding_model,
                    enable_chunking,
                    extra_metadata=parsed_extra_metadata,
                    reindex=reindex,
                    existing_object_id=existing_object_id,
                    document_processing_mode=processing_mode,
                )
        elif file_url:
            with tracer.start_as_current_span("Node: Dispatch File Url"):
                result = await process_file_url_handler(
                    file_url,
                    user_id,
                    knowledge_id,
                    use_llama_parse,
                    transcript_provider,
                    contextualise_chunks,
                    chunk_size,
                    hybrid_search,
                    embedding_model,
                    existing_object_id=existing_object_id,
                    enable_chunking=enable_chunking,
                    extra_metadata=parsed_extra_metadata,
                    reindex=reindex,
                    document_processing_mode=processing_mode,
                )
            if isinstance(result, FileValidationResult):
                return {
                    "object_id": result.object_id,
                    "file_name": result.file_name,
                    "message": result.error_detail,
                    "status": "invalid",
                }
            object_id = result
        elif text:
            with tracer.start_as_current_span("Node: Dispatch Text Content"):
                object_id = await process_text_content_handler(
                    text,
                    None,
                    user_id,
                    knowledge_id,
                    contextualise_chunks,
                    chunk_size,
                    hybrid_search,
                    embedding_model,
                    enable_chunking=enable_chunking,
                )
        elif weblink:
            with tracer.start_as_current_span("Node: Dispatch Weblink"):
                object_id = await process_weblink_handler(
                    weblink,
                    user_id,
                    knowledge_id,
                    use_llama_parse,
                    scrape_using,
                    scraping_method,
                    max_depth,
                    contextualise_chunks,
                    chunk_size,
                    hybrid_search,
                    embedding_model,
                    enable_chunking=enable_chunking,
                    document_processing_mode=processing_mode,
                )

        return {
            "object_id": object_id,
            "message": f"Document upload initiated in background for knowledge {knowledge_id}",
            "status": "processing",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding document: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while adding document: {e}")


# 10. List Documents
@router.get("/v1/{knowledge_id}/documents")
async def list_documents(
    knowledge_id: str,
    content_type: Optional[str] = Query(None),
    metadata_filter: Optional[str] = Query(None, description="Optional JSON-encoded metadata filter dict"),
    include_source_id: bool = Query(False, description="When true (with metadata_filter), return [{object_id, source_id}] instead of bare object_ids"),
    include_metadata: bool = Query(False, description="When true (with metadata_filter), return [{object_id, metadata}] with full payload inline in a single call (avoids per-doc metadata fetches)"),
    limit: Optional[int] = Query(None, description="Max docs for the filtered path (default 15; raise for full-folder reconcile sweeps)"),
    match_all: bool = Query(False, description="Optional. false (default) = loose OR (match at least one), preserving legacy behavior; true = strict AND (every metadata_filter field must match, e.g. to scope by space_id)"),
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        # If metadata_filter is provided, use filtering logic
        if metadata_filter:
            with tracer.start_as_current_span("Node: Parse Metadata Filter"):
                filters = {}
                try:
                    decoded = json.loads(metadata_filter)
                    if isinstance(decoded, dict):
                        filters.update({k: v for k, v in decoded.items() if v is not None})
                except Exception:
                    try:
                        decoded = ast.literal_eval(metadata_filter)
                        if isinstance(decoded, dict):
                            filters.update({k: v for k, v in decoded.items() if v is not None})
                    except Exception:
                        pass

            with tracer.start_as_current_span("Node: Query Filtered Documents"):
                docs = await list_documents_get_alias(
                    knowledge_id=knowledge_id,
                    user_id=None,
                    filters=filters,
                    limit=limit if limit is not None else 15,
                    # include_metadata takes precedence: the downstream filter checks
                    # include_source_id first and returns the slim view, so suppress it
                    # here when the caller wants full metadata (source_id is in the payload).
                    include_source_id=include_source_id and not include_metadata,
                    include_metadata=include_metadata,
                    # match_all=True -> strict AND filtering (every metadata field must match),
                    # so callers can scope precisely (e.g. one space_id) instead of loose OR.
                    match_all=match_all,
                )

            # metadata path returns [{object_id, metadata}] in one call — lets callers
            # tally/render without a per-doc metadata fetch. (DB fallback can't serve this;
            # it yields bare ids, normalized to {object_id, metadata:{}} so callers don't crash.)
            if include_metadata:
                documents = [
                    d if isinstance(d, dict) else {"object_id": d, "metadata": {}}
                    for d in docs
                ]
                return {"documents": documents}

            # slim path returns [{object_id, source_id}]; default stays backward-compatible
            if include_source_id:
                # DB fallback (metadata disabled) yields bare id strings — normalize to slim shape
                # with source_id=None so callers can detect the unsafe-to-reconcile view.
                documents = [
                    d if isinstance(d, dict) else {"object_id": d, "source_id": None}
                    for d in docs
                ]
                return {"documents": documents}
            return DocumentListResponse(object_ids=docs)
        
        # Default behavior when no metadata_filter is provided
        with tracer.start_as_current_span("Node: Load Documents"):
            object_items = await retrieve_chatbot_knowledge(user_id=None, knowledge_id=knowledge_id, content_type=content_type)
        if object_items:
            with tracer.start_as_current_span("Node: Attach Presigned Urls"):
                object_items = await enhance_knowledge_with_presigned_urls(object_items)
        return {"document": object_items or []}
    except Exception as e:
        logger.error(f"Error listing documents: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while listing documents: {e}")


# 10b. List Documents V2 - with 7-day presigned URLs, DB caching, pagination and sort
_V2_DOCUMENTS_SORT_FIELDS = {"file_name", "status", "created_time", "updated_time"}


@router.get("/v2/{knowledge_id}/documents")
async def list_documents_v2(
    knowledge_id: str,
    content_type: Optional[str] = Query(None),
    file_name: Optional[str] = Query(None, description="Search uploaded items by file name (case-insensitive partial match)"),
    object_id: Optional[str] = Query(None, description="Search uploaded items by object ID (case-insensitive partial match)"),
    page: int = Query(1, ge=1, description="1-indexed page number."),
    page_size: int = Query(20, ge=1, le=200, description="Documents per page."),
    sort_by: str = Query("created_time", description=f"One of: {', '.join(sorted(_V2_DOCUMENTS_SORT_FIELDS))}."),
    order: str = Query("desc", description="'asc' or 'desc'."),
    _: Optional[str] = Depends(require_permission("read")),
):
    """List documents with long-lived presigned URLs (7-day expiry with DB caching).

    Supports the Knowledge Upload Status window: search uploaded items by
    file_name or object_id, and get direct links to both the original
    uploaded document and its parsed/extracted document. Also backs the
    Knowledge screen's document table — this endpoint previously returned
    every matching document in one response with no pagination or sort,
    which was fine at small counts and wrong at any real one.

    Sorted and paginated here in Python, not in SQL: retrieve_chatbot_knowledge_v2
    groups crawled sub-pages under their parent document (sub_urls) AFTER
    fetching every matching row, so a SQL-level OFFSET/LIMIT could split a
    parent from its own children across pages. Sorting/slicing the
    already-grouped result keeps every parent with its children on one
    page — the cost is holding the knowledge base's full filtered document
    set in memory for the sort, the same tradeoff the v2 tabular data view
    (get_data_view_tabular_v2) already makes for its multi-field sort.
    Presigned URLs are generated only for the returned page, not the whole
    set, so pagination is also what keeps this endpoint's S3 URL-signing
    cost bounded.

    Returns:
        - source_presigned_url: Presigned URL for original uploaded file (7-day expiry, cached in DB)
        - extracted_s3_url: Presigned URL for extracted markdown text (7-day expiry)
        - source: where the document came from — "clickup" for a connector-synced page,
          otherwise the row's content_type value unchanged ("file" / "url" / "plain_text").
          Only "clickup" is new: a synced page is posted through the ordinary file_url
          path, so content_type calls it a "file" and cannot tell the two apart; the
          connector's identity survives only in extra_metadata.
        - document_processing_mode: the mode the document was requested to be read in
          ("text_based", "scanned", "layout_aware", "visual", "mixed"). Requested, not
          effective — the worker may auto-shift a mode it finds wrong (an image-only PDF
          shifts to OCR) and does not report that back to this service.
        - total_count / current_page / total_pages: pagination metadata
    """
    if sort_by not in _V2_DOCUMENTS_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"sort_by must be one of: {', '.join(sorted(_V2_DOCUMENTS_SORT_FIELDS))}",
        )
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order must be 'asc' or 'desc'")

    try:
        with tracer.start_as_current_span("Node: Load Documents V2"):
            object_items = await retrieve_chatbot_knowledge_v2(
                user_id=None,
                knowledge_id=knowledge_id,
                content_type=content_type,
                file_name=file_name,
                object_id=object_id,
            ) or []

        with tracer.start_as_current_span("Node: Sort And Paginate Documents V2"):
            # Nulls always sort last regardless of direction (typical table-sort
            # UX) rather than flipping to the front on a naive reverse=True.
            with_value = [item for item in object_items if item.get(sort_by) is not None]
            without_value = [item for item in object_items if item.get(sort_by) is None]
            with_value.sort(key=lambda item: item[sort_by], reverse=(order == "desc"))
            object_items = with_value + without_value

            total_count = len(object_items)
            start = (page - 1) * page_size
            page_items = object_items[start:start + page_size]

        if page_items:
            with tracer.start_as_current_span("Node: Attach Cached Presigned Urls"):
                page_items = await generate_presigned_url_v2(page_items)

        total_pages = max(1, (total_count + page_size - 1) // page_size) if total_count else 1
        return {
            "document": page_items,
            "total_count": total_count,
            "current_page": page,
            "total_pages": total_pages,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing documents v2: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while listing documents: {e}")


# 11. Delete Document
@router.delete("/v1/{knowledge_id}/documents/{object_id}")
async def delete_document(
    knowledge_id: str,
    object_id: str,
    background_tasks: BackgroundTasks = None,
    user_id: Optional[str] = Depends(require_permission("delete")),
):
    try:
        actor_id = resolve_actor_id(user_id)
        with tracer.start_as_current_span("Node: Initialize Deletion"):
            deletion_status = await set_initial_deletion_status(knowledge_id=knowledge_id, object_ids=[object_id], user_id=None)
            if not deletion_status:
                raise HTTPException(status_code=404, detail=f"Document {object_id} not found in knowledge {knowledge_id}")

        with tracer.start_as_current_span("Node: Load Deletion Config"):
            hybrid_search, embedding_model = await get_chatbot_search_config(knowledge_id)

        with tracer.start_as_current_span("Node: Enqueue Deletion Task"):
            background_tasks.add_task(
                perform_knowledge_deletion,
                user_id=actor_id,
                knowledge_id=knowledge_id,
                object_ids=[object_id],
                hybrid_search=hybrid_search,
                embedding_model=embedding_model,
            )

        return {"message": "Deletion started in Background", "deletion_status": deletion_status}
    except Exception as e:
        logger.error(f"Error initiating deletion: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while initiating deletion: {e}")


# 12. Get Document Status
@router.get("/v1/{knowledge_id}/documents/{object_id}/status")
async def get_document_status(
    knowledge_id: str,
    object_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        with tracer.start_as_current_span("Node: Load Document Status"):
            status, error_detail = await retrieve_knowledge_processing_status(knowledge_id=knowledge_id, object_id=object_id)
        with tracer.start_as_current_span("Node: Validate Document Status"):
            validate_knowledge_item_exists(status, object_id, knowledge_id)
        return {
            "knowledge_id": knowledge_id,
            "object_id": object_id,
            "status": status,
            # Only meaningful when status == "failed" — the plain-language
            # rewording for a non-technical user is the UI's job, this is
            # the raw reason stored on the row (Document.error_detail).
            "error_detail": error_detail,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking document status: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred while checking status: {e}")


# 13. Get Deletion Status
@router.get("/v1/{knowledge_id}/documents/{object_id}/deletion-status")
async def get_document_deletion_status(
    knowledge_id: str,
    object_id: str,
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        with tracer.start_as_current_span("Node: Load Deletion Status"):
            status = await retrieve_knowledge_deletion_status(knowledge_id=knowledge_id, object_id=object_id)
            if status is None:
                raise HTTPException(status_code=404, detail="Knowledge item not found")
        return {"knowledge_id": knowledge_id, "object_id": object_id, "status": status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking deletion status: {e}")
        raise HTTPException(status_code=500, detail=f"Error checking deletion status: {e}")


# 14. Get Document Summaries
@router.post("/v1/{knowledge_id}/documents/summaries", response_model=DocumentSummariesResponse)
async def get_document_summaries_v2(
    knowledge_id: str,
    request: GetDocumentSummariesRequest = Body(
        default=GetDocumentSummariesRequest(object_ids=[]),
        # openapi_examples={
        #     "hybrid_search": {
        #         "summary": "Hybrid search (default)",
        #         "description": "Semantic + BM25 RRF search over document summaries. Pass query param in URL.",
        #         "value": {"object_ids": []},
        #     },
        #     "search_with_metadata_prefilter": {
        #         "summary": "Hybrid search + metadata pre-filter",
        #         "description": "Narrows candidate set by metadata before vector scoring. Pass query + metadata_filter in URL.",
        #         "value": {"object_ids": []},
        #     },
        #     "filter_only": {
        #         "summary": "Metadata filter only (no query)",
        #         "description": "Scroll documents matching metadata_filter. Pass metadata_filter in URL.",
        #         "value": {"object_ids": []},
        #     },
        #     "direct_object_ids": {
        #         "summary": "Direct object IDs lookup",
        #         "description": "Fetch summaries for specific documents. No query or metadata_filter needed.",
        #         "value": {"object_ids": ["doc-id-1", "doc-id-2"]},
        #     },
        # },
    ),
    query: Optional[str] = Query(None, description="Search query over document summaries"),
    mode: Optional[str] = Query("hybrid", description="Search mode: 'hybrid' (default, RRF fusion) or 'semantic' (dense-only)"),
    metadata_filter: Optional[str] = Query(None, description="Optional JSON-encoded metadata filter dict"),
    match_all: bool = Query(False, description="Optional. Metadata-only path (no query): false (default) = loose OR; true = strict AND (e.g. to scope by space_id). The search path (query set) already applies filters as AND."),
    limit: Optional[int] = Query(None, description="Optional limit for number of documents"),
    include_metadata: bool = Query(False, description="Include document metadata with summaries"),
    include_summary: bool = Query(True, description="Include document summary in response (default: true)"),
    _: Optional[str] = Depends(require_permission("read")),
):
    try:
        scores: dict[str, float] = {}
        inline_metadata: dict[str, dict] = {}

        def _parse_metadata_filter(raw: str) -> dict:
            try:
                decoded = json.loads(raw)
            except Exception:
                try:
                    decoded = ast.literal_eval(raw)
                except Exception:
                    return {}
            return {k: v for k, v in decoded.items() if v is not None} if isinstance(decoded, dict) else {}

        if query:
            with tracer.start_as_current_span("Node: Parse Summary Filters"):
                filters = _parse_metadata_filter(metadata_filter) if metadata_filter else {}

            search_limit = limit if limit is not None else 10
            with tracer.start_as_current_span("Node: Semantic Document Search"):
                ranked = await search_documents_service(
                    knowledge_id=knowledge_id,
                    query=query,
                    metadata_filter=filters or None,
                    limit=search_limit,
                    mode=mode or "hybrid",
                    include_metadata=include_metadata,
                )
            object_ids = [r["object_id"] for r in ranked]
            scores = {r["object_id"]: r["score"] for r in ranked}
            if include_metadata:
                inline_metadata = {r["object_id"]: r.get("metadata", {}) for r in ranked}

        elif metadata_filter:
            with tracer.start_as_current_span("Node: Parse Summary Filters"):
                filters = _parse_metadata_filter(metadata_filter)
                if request.object_ids:
                    # body object_ids narrow the filter; metadata_filter.object_id takes lower priority
                    filters["object_id"] = request.object_ids
                filter_limit = limit if limit is not None else 10

            with tracer.start_as_current_span("Node: Resolve Summary Object Ids"):
                filter_results = await list_documents_get_alias(
                    knowledge_id=knowledge_id,
                    user_id=None,
                    filters=filters,
                    limit=filter_limit,
                    include_metadata=include_metadata,
                    match_all=match_all,
                )
            if include_metadata and filter_results and isinstance(filter_results[0], dict):
                object_ids = [r["object_id"] for r in filter_results]
                inline_metadata = {r["object_id"]: r.get("metadata", {}) for r in filter_results}
            else:
                object_ids = filter_results

        else:
            with tracer.start_as_current_span("Node: Resolve Summary Object Ids"):
                object_ids = request.object_ids if request.object_ids else []

        summaries_raw: dict = {}
        if include_summary or (include_metadata and not inline_metadata):
            with tracer.start_as_current_span("Node: Load Document Summaries"):
                # Search and filter paths already carry payload in inline_metadata — pass include_metadata=False
                # to avoid a redundant Qdrant fetch. Direct object_ids path has no prior payload, so fetch it here.
                summaries_raw = await get_summaries_service(
                    knowledge_id,
                    object_ids,
                    include_metadata=include_metadata and not inline_metadata,
                )
                if include_metadata and not inline_metadata:
                    inline_metadata = {oid: d.get("metadata", {}) for oid, d in summaries_raw.items()}

        logger.info(
            f"get_document_summaries: knowledge={knowledge_id} resolved={len(object_ids)} "
            f"include_metadata={include_metadata} include_summary={include_summary}"
        )

        with tracer.start_as_current_span("Node: Build Summary Response"):
            result: dict[str, SummaryItem] = {}
            for oid in object_ids:
                d = summaries_raw.get(oid, {"summary": "", "filename": oid})
                result[oid] = SummaryItem(
                    summary=d.get("summary", "") if include_summary else None,
                    filename=d.get("filename", oid),
                    score=scores.get(oid),
                    metadata=inline_metadata.get(oid) if include_metadata else None,
                )

        if not include_summary:
            payload = {
                oid: item.model_dump(exclude={"summary"})
                for oid, item in result.items()
            }
            return JSONResponse(content={"summaries": payload})

        return DocumentSummariesResponse(summaries=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"get_document_summaries failed knowledge={knowledge_id} query={query!r}: {e}")
        raise HTTPException(status_code=500, detail=f"Summaries fetch failed: {e}")

