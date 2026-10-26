from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, unquote

from fastapi import HTTPException
import tempfile
import os
from fastapi import UploadFile
from io import BytesIO

from utils.files.file_utils import (
    get_pdf_page_count_from_bytes,
    get_pdf_page_count_from_url,
    get_docx_page_count_from_bytes,
    get_file_text,
)
from utils.files.url_utils import extract_filename_and_content_type
from utils.logger import logger

from utils.pydantic_models_knowledge import TranscribeUsing

# File size limits
MAX_PDF_PAGES = 10
MIN_LENGTH = 10
# Kept in sync with max_file_size_mb in utils/config/usage/owner_defaults.yaml —
# that field is descriptive only (nothing reads it yet), this constant is what's
# actually enforced. Reduced from 50MB on 2026-09-03: at a 10-page cap, even a
# scanned business document rarely exceeds a few MB, so 50MB was never a
# meaningful ceiling.
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_TEXT_CHARS = 100_000  # ~50 chunks of 2000 chars


@dataclass
class FileValidationResult:
    success: bool
    file_name: Optional[str] = None
    content_type: Optional[str] = None
    object_id: Optional[str] = None
    error_detail: Optional[str] = None

async def validate_uploaded_file(file, use_llama_parse, transcript_provider, min_length=10, max_pdf_pages=10):
    """
    Validates the uploaded file for page count and size. Raises HTTPException on failure.

    **This no longer extracts text from PDFs, DOCX files, or audio.** It used to, and the
    reason it stopped is worth understanding, because it looked harmless:

    The old version called ``get_file_text`` here, inside the request, purely to check
    that the file contained at least ``min_length`` characters. But extracting text is not
    a free local operation for every format — for a LlamaParse mode it is a paid API call
    per page, and for audio it is a paid transcription. The worker then extracted the very
    same file again to actually index it. So every page and every audio minute was paid
    for **twice**, once for a check and once for real.

    It was also wrong in a subtler way. A scanned PDF holds no text at all — it is a stack
    of images. The text extractor returned an empty string, the ``min_length`` check
    rejected it, and the upload failed with "file is too short" on a document that was
    perfectly fine. The user had no way to know they were meant to tick a box first.

    So the length check moved to the worker, which extracts the file exactly once anyway
    and already has a place to record the outcome (`Document.error_detail`, surfaced by
    ``GET /v1/{knowledge_id}/documents/{object_id}/status``). What stays here is only what
    is genuinely cheap and genuinely local:

      * content-type and page-count limits, which read a header rather than the content;
      * the maximum-size guard for plain-text formats, whose readers really are free.

    The trade is that an empty or unreadable file now fails a few seconds later, as a
    ``failed`` document status rather than a rejected upload. The UI polls that status
    endpoint already.
    """
    text_types = {"text/plain", "text/markdown", "text/html", "application/octet-stream"}
    docx_content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    file_content = await file.read()
    await file.seek(0)

    # --- Page-count limits: read structure, not content, so these stay cheap ---
    if file.content_type == "application/pdf":
        page_count = await get_pdf_page_count_from_bytes(file_content)
        if page_count > max_pdf_pages:
            raise HTTPException(status_code=400, detail=f"Document has {page_count} pages, which exceeds the {max_pdf_pages}-page limit.")
    elif file.content_type == docx_content_type:
        docx_page_count = get_docx_page_count_from_bytes(file_content)
        if docx_page_count is not None and docx_page_count > max_pdf_pages:
            raise HTTPException(status_code=400, detail=f"Document has {docx_page_count} pages, which exceeds the {max_pdf_pages}-page limit.")

    # --- Size guard for plain-text formats only ---
    # Safe to extract here because these readers are local and free: no API call, no
    # per-page cost. Worth keeping in the API because it stops a huge file from being
    # uploaded to S3 before anyone notices it is too big.
    if file.content_type in text_types:
        file_text = get_file_text(file_content, file.content_type, file.filename, False, transcript_provider)
        if isinstance(file_text, dict):
            file_text = file_text.get("content", "")
        if len(file_text.strip()) > MAX_TEXT_CHARS:
            raise HTTPException(status_code=400, detail=f"Document has {len(file_text.strip()):,} characters, which exceeds the {MAX_TEXT_CHARS:,}-character limit.")

    del file_content
    return None

    # --- Previous implementation, kept for reference during the rollout ---------------
    # Extracted every uploaded file synchronously to enforce min_length. Retired because
    # it double-billed LlamaParse pages and Whisper minutes, and because it rejected
    # scanned PDFs outright (no text layer -> "too short"). The worker performs the same
    # check on the single extraction it already does. See the docstring above.
    #
    # audio_types = ["audio/mpeg", "audio/wav", "audio/ogg", "video/mpeg"]
    # if file.content_type == "application/pdf":
    #     file_content = await file.read()
    #     page_count = await get_pdf_page_count_from_bytes(file_content)
    #     if page_count > max_pdf_pages:
    #         raise HTTPException(status_code=400, detail=f"Document has {page_count} pages, which exceeds the {max_pdf_pages}-page limit.")
    #     await file.seek(0)
    #     file_text = get_file_text(file_content, file.content_type, file.filename,
    #                               use_llama_parse, transcript_provider, None)
    # elif file.content_type in audio_types:
    #     file_content = await file.read()
    #     await file.seek(0)
    #     file_text = get_file_text(file_content, file.content_type, file.filename,
    #                               use_llama_parse, transcript_provider)
    # else:
    #     file_content = await file.read()
    #     await file.seek(0)
    #     docx_page_count = None
    #     if file.content_type == docx_content_type:
    #         docx_page_count = get_docx_page_count_from_bytes(file_content)
    #         if docx_page_count is not None and docx_page_count > max_pdf_pages:
    #             raise HTTPException(status_code=400, detail=f"Document has {docx_page_count} pages, which exceeds the {max_pdf_pages}-page limit.")
    #     file_text = get_file_text(file_content, file.content_type, file.filename,
    #                               use_llama_parse, transcript_provider)
    # if isinstance(file_text, dict):
    #     file_text = file_text.get("content", "")
    # if len(file_text.strip()) < min_length:
    #     raise HTTPException(status_code=400, detail=f"File is too short (min. {min_length} characters required).")
    # is_docx_without_page_count = (file.content_type == docx_content_type and docx_page_count is None)
    # if (file.content_type in text_types or is_docx_without_page_count) and len(file_text.strip()) > MAX_TEXT_CHARS:
    #     raise HTTPException(status_code=400, detail=f"Document has {len(file_text.strip()):,} characters, which exceeds the {MAX_TEXT_CHARS:,}-character limit.")
    # return file_text

async def validate_file_url(file_url, max_pdf_pages=10, min_length=10) -> FileValidationResult:
    """
    Validates a file URL for content type, PDF page count, and text content.
    Returns FileValidationResult with success=False on validation failure instead of raising.
    """
    import httpx

    # Extract metadata from URL
    file_name = None
    try:
        file_name, content_type, object_id = await extract_filename_and_content_type(file_url)
    except Exception as e:
        # Best-effort filename extraction from URL
        try:
            parsed = urlparse(file_url)
            raw_name = unquote(parsed.path.split("/")[-1])
            file_name = "_".join(raw_name.split("_")[1:]) or raw_name
        except Exception:
            pass
        logger.error(f"Error extracting file metadata from URL: {e}")
        return FileValidationResult(success=False, file_name=file_name, error_detail=str(e))

    logger.info(f"File name: {file_name}, Content type: {content_type}, Object ID: {object_id}")

    # Validate PDF page count if applicable
    if content_type == "application/pdf":
        try:
            page_count = await get_pdf_page_count_from_url(file_url)
            if page_count > max_pdf_pages:
                logger.error(f"PDF from URL has {page_count} pages, which exceeds the {max_pdf_pages}-page limit.")
                return FileValidationResult(
                    success=False, file_name=file_name, content_type=content_type,
                    object_id=object_id,
                    error_detail=f"Document has {page_count} pages, which exceeds the {max_pdf_pages}-page limit."
                )
        except Exception as e:
            logger.error(f"Error checking PDF page count: {e}")
            return FileValidationResult(
                success=False, file_name=file_name, content_type=content_type,
                object_id=object_id, error_detail=f"Error checking PDF page count: {str(e)}"
            )

    # Validate text content for all file types
    try:
        from utils.files.s3_utils import read_file_bytes
        file_content = await read_file_bytes(file_url)

        # Check DOCX page count using already-downloaded bytes
        docx_page_count = None
        if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            docx_page_count = get_docx_page_count_from_bytes(file_content)
            if docx_page_count is not None and docx_page_count > max_pdf_pages:
                logger.error(f"DOCX from URL has {docx_page_count} pages, which exceeds the {max_pdf_pages}-page limit.")
                return FileValidationResult(
                    success=False, file_name=file_name, content_type=content_type,
                    object_id=object_id,
                    error_detail=f"Document has {docx_page_count} pages, which exceeds the {max_pdf_pages}-page limit."
                )

        # Size guard for plain-text formats only. Same reasoning as
        # validate_uploaded_file: these readers are local and free, so running them here
        # costs nothing, while audio transcription and page-by-page document parsing are
        # paid work that the worker is about to do anyway.
        text_types = {"text/plain", "text/markdown", "text/html", "application/octet-stream"}
        if content_type in text_types:
            file_text = get_file_text(file_content, content_type, file_name, False, None, None)
            if isinstance(file_text, dict):
                file_text = file_text.get("content", "")
            elif isinstance(file_text, tuple):
                # Handle legacy tuple format (backward compatibility)
                file_text = file_text[0]
            if len(file_text.strip()) > MAX_TEXT_CHARS:
                logger.error(f"File from URL exceeds {MAX_TEXT_CHARS:,}-character limit.")
                return FileValidationResult(
                    success=False, file_name=file_name, content_type=content_type,
                    object_id=object_id,
                    error_detail=f"Document has {len(file_text.strip()):,} characters, which exceeds the {MAX_TEXT_CHARS:,}-character limit."
                )

        # --- Previous implementation, kept for reference during the rollout -----------
        # Transcribed audio and parsed every document here just to enforce min_length,
        # duplicating work the worker does immediately afterwards. It also rejected
        # scanned PDFs, which have no text layer to measure. The worker now owns the
        # length check; see validate_uploaded_file's docstring.
        #
        # audio_types = ["audio/mpeg", "audio/wav", "audio/ogg", "video/mpeg"]
        # if content_type in audio_types:
        #     suffix = os.path.splitext(file_name)[-1]
        #     with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        #         tmp.write(file_content)
        #         tmp_path = tmp.name
        #     try:
        #         file_text = get_file_text(file_content, content_type, file_name,
        #                                   False, None, tmp_path)
        #     finally:
        #         os.remove(tmp_path)
        # else:
        #     file_text = get_file_text(file_content, content_type, file_name,
        #                               False, None, None)
        # if isinstance(file_text, dict):
        #     file_text = file_text.get("content", "")
        # elif isinstance(file_text, tuple):
        #     file_text = file_text[0]
        # if len(file_text.strip()) < min_length:
        #     logger.error(f"File is too short (min. {min_length} characters required).")
        #     return FileValidationResult(
        #         success=False, file_name=file_name, content_type=content_type,
        #         object_id=object_id,
        #         error_detail=f"File is too short (min. {min_length} characters required)."
        #     )
        # is_docx_without_page_count = (
        #     content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        #     and docx_page_count is None
        # )
        # if (content_type in text_types or is_docx_without_page_count) and len(file_text.strip()) > MAX_TEXT_CHARS:
        #     ... (same MAX_TEXT_CHARS return as above)

    except httpx.RequestError as e:
        logger.error(f"Could not retrieve the file from the provided URL for validation: {e}")
        return FileValidationResult(
            success=False, file_name=file_name, content_type=content_type,
            object_id=object_id,
            error_detail="Could not retrieve the file from the provided URL for validation."
        )
    except Exception as e:
        logger.error(f"Error validating file content: {e}")
        return FileValidationResult(
            success=False, file_name=file_name, content_type=content_type,
            object_id=object_id, error_detail=str(e)
        )

    return FileValidationResult(
        success=True, file_name=file_name,
        content_type=content_type, object_id=object_id
    )


async def validate_file_size(file: UploadFile) -> None:
    """
    Validate file size against maximum allowed size.
    
    Args:
        file: Uploaded file
        
    Raises:
        HTTPException: If file is too large
    """
    try:
        file_content = await file.read()
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail="File too large. Maximum size is 5MB."
            )
        # Reset file pointer after reading
        await file.seek(0)
        logger.info(f"File size validation passed for {file.filename}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating file size: {e}")
        raise HTTPException(status_code=500, detail="Error validating file size")


async def process_uploaded_file(
    file: UploadFile,
    use_llama_parse: bool,
    transcript_provider: TranscribeUsing
) -> None:
    """
    Validate an uploaded file before it is stored and queued.

    Checks size, content type and page count. Does **not** extract text — that happens
    once, in the worker, which then also enforces the minimum-length rule. See
    validate_uploaded_file's docstring for why the extraction moved.

    Args:
        file: Uploaded file
        use_llama_parse: Legacy flag, retained for signature compatibility with callers.
            No longer influences validation, since validation no longer parses anything.
        transcript_provider: Transcription provider

    Returns:
        None. Previously returned the extracted text, which no caller used for anything
        beyond the checks now performed downstream.

    Raises:
        HTTPException: If file validation fails
    """
    try:
        logger.info(f"Processing file - Filename: {file.filename}")

        # Validate file size
        await validate_file_size(file)

        # Validate structure (content type, page count, plain-text size cap)
        await validate_uploaded_file(
            file, use_llama_parse,
            transcript_provider, min_length=10,
            max_pdf_pages=10
        )

        logger.info(f"Successfully validated file: {file.filename}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing file {file.filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {e}")


async def validate_file_from_url(
    file_url: str,
    max_pdf_pages: int = 10,
    min_length: int = 10
) -> FileValidationResult:
    """
    Validate a file from URL and extract metadata.

    Args:
        file_url: URL of the file
        max_pdf_pages: Maximum PDF pages allowed
        min_length: Minimum text length required

    Returns:
        FileValidationResult with success status and metadata or error detail
    """
    result = await validate_file_url(
        file_url, max_pdf_pages=max_pdf_pages, min_length=min_length
    )
    if result.success:
        logger.info(f"Successfully validated file URL: {file_url}")
    else:
        logger.error(f"Validation failed for file URL {file_url}: {result.error_detail}")
    return result

