# Standard library imports
import os
import time
import tempfile
from io import BytesIO
from typing import Optional

# Third party imports
import httpx
from docx import Document
from fastapi import HTTPException
from groq import Groq
from openai import OpenAI
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTChar
from pdfminer.pdfpage import PDFPage
import pdfplumber
from langchain_community.document_loaders import UnstructuredHTMLLoader
from tinytag import TinyTag

# Local imports
from services import celery_setup_env
from services.celery_logger import logger
from celery_utils.db.db_utils import (
    retrieve_knowledge_processing_status,
    update_processing_status,
    mark_task_completed,
)
from celery_utils.enrichment.enrichment_utils import enrich_document
from celery_utils.processing.s3_utils import upload_extracted_text_to_s3, download_file
from celery_utils.indexing.indexer import index_in_vectordb
from celery_utils.shared.user_usage_tracking import send_file_processing_event, get_owner_id_from_service_id
from celery_utils.shared.credentials_utils import get_api_key
from celery_utils.processing.processing_modes import (
    LLAMAPARSE_ELIGIBLE_TYPES,
    ProcessingMode,
    classify_docx_target_mode,
    classify_pdf_target_mode,
    estimated_credits,
    llamaparse_settings,
    parse_mode_for,
    resolve_mode,
)
from celery_utils.shared.tracing import track_time, traced_step, traced_reader, add_step_metadata, mark_indexing_time, reset_embedding_cost, get_embedding_cost
from langsmith import traceable


# --- API Keys & Config ---
FIRECRAWL_API_KEY = celery_setup_env.FIRECRAWL_API_KEY
OPENAI_API_KEY = celery_setup_env.OPENAI_API_KEY
LLAMA_PARSER_API_KEY = celery_setup_env.LLAMA_PARSE_API_KEY
GROQ_API_KEY = celery_setup_env.GROQ_API_KEY

# --- LlamaParse REST config ---
#
# Called directly over sync httpx rather than through the `llama_parse` SDK. The SDK's
# `load_data()` needs its own asyncio event loop and patches one in with
# `nest_asyncio.apply()` on every call — inside this repo's gevent worker pool that
# nesting can silently fail instead of raising (documented upstream:
# https://github.com/run-llama/llama_cloud_services/issues/344, "the code silently
# returns an empty result list rather than surfacing a clear error"). The SDK also
# defaults `ignore_errors=True`, which independently swallows a failed parse into an
# empty result rather than raising it. Both together are exactly the failure this repo
# hit live: `page_count=0, chars=0`, no exception, no job ever visible on LlamaParse's
# own platform for that request. A synchronous upload/poll/result loop against the REST
# API sidesteps both — no event loop to conflict with a gevent greenlet, and every
# failure (bad status code, ERROR/CANCELLED job, timeout) raises here instead of
# disappearing into an empty return.
LLAMA_PARSE_BASE_URL = "https://api.cloud.llamaindex.ai/api/v1/parsing"
LLAMA_PARSE_POLL_INITIAL_INTERVAL_S = 1
LLAMA_PARSE_POLL_MAX_INTERVAL_S = 5
LLAMA_PARSE_POLL_TIMEOUT_S = 1800  # under task_soft_time_limit (3000s), leaves headroom for the rest of the pipeline

qdrant_url = celery_setup_env.qdrant_url
qdrant_api_key = celery_setup_env.qdrant_api_key

# --- Supported File Types ---
# Maps MIME type -> allowed extension(s). Used to validate uploads.
SUPPORTED_FILE_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "audio/mpeg": [".mp3", ".mpeg", ".wav", ".aac", ".ogg"],
    "audio/wav": [".wav"],
    "audio/ogg": [".ogg"],
    "video/mpeg": [".mpeg"],
    "text/html": [".html", ".htm"],
    "text/markdown": [".md"],
    "application/octet-stream": [".md"]
}

# Maps file extension -> human-readable document type for metadata tagging
_EXTENSION_TO_DOC_TYPE = {
    ".txt": "Text",
    ".docx": "Word",
    ".pdf": "PDF",
    ".md": "Markdown",
    ".html": "HTML",
    ".htm": "HTML",
    ".mp3": "Audio",
    ".wav": "Audio",
    ".ogg": "Audio",
    ".aac": "Audio",
    ".mpeg": "Audio",
}

# Maps audio extension -> the container TinyTag reads it as. Raw ADTS AAC has no
# container metadata, so its duration is counted from frame headers instead.
_AUDIO_CONTAINERS = {
    '.mp3': '.mp3',
    '.wav': '.wav',
    '.ogg': '.ogg',
    '.mpeg': '.mp3',
}
_ADTS_SAMPLE_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def _adts_duration(data: bytes) -> float:
    """Duration of a raw ADTS AAC stream: 1024 samples per raw data block."""
    position, blocks, sample_rate = data.find(b'\xff'), 0, None
    while 0 <= position <= len(data) - 7:
        header = data[position:position + 7]
        rate_index = (header[2] >> 2) & 0x0F
        frame_length = ((header[3] & 0x03) << 11) | (header[4] << 3) | (header[5] >> 5)
        if (header[1] & 0xF6) != 0xF0 or rate_index >= len(_ADTS_SAMPLE_RATES) or frame_length < 7:
            position = data.find(b'\xff', position + 1)
            continue
        sample_rate = sample_rate or _ADTS_SAMPLE_RATES[rate_index]
        blocks += (header[6] & 0x03) + 1
        position += frame_length
    return blocks * 1024 / sample_rate if sample_rate else 0.0

# Maps file extension -> MIME type for content type inference when not provided
_EXTENSION_TO_CONTENT_TYPE = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
}

# --- Constants ---
MAX_PDF_PAGES = 10
MIN_TEXT_LENGTH = 10


# ---------------------------------------------------------------------------
# File Metadata Helpers
# ---------------------------------------------------------------------------

def doc_type_metadata(file_extension: str) -> str:
    """Return human-readable document type (e.g. 'PDF', 'Audio') for a given file extension."""
    return _EXTENSION_TO_DOC_TYPE.get(file_extension, "Unknown")


# ---------------------------------------------------------------------------
# File Readers — Each reader returns {"content": str, "extra_info": str, ...}
# ---------------------------------------------------------------------------

@traced_reader(
    "API: LlamaParse - Parse File",
    inputs=("file_name", "result_type", "mode", "owner_id"),
    meta_keys=("llama_parse_page_count", "llama_parse_credits", "parse_mode",
               "effective_mode", "key_type"),
)
def read_with_llama_parse(bytes_io: BytesIO, file_name: str, result_type: str = 'markdown',
                          owner_id: Optional[str] = None,
                          mode: ProcessingMode = ProcessingMode.LAYOUT_AWARE):
    """Parse file using LlamaParse API. Returns content, page count, and key type.

    ``mode`` decides how hard LlamaParse works (and therefore what it costs): whether it
    runs OCR, pays attention to table structure, or looks at the page as an image. The
    settings themselves live in processing_modes.py — see that module for why the
    mapping is centralised there rather than written out here.
    """
    settings = llamaparse_settings(mode) or {}
    # The billing/logging key for this mode, not settings.get("parse_mode") directly —
    # a tier-based mode (visual) has no "parse_mode" kwarg at all, and reading it
    # straight off settings would silently price and log it as free. See
    # processing_modes.parse_mode_for.
    parse_mode = parse_mode_for(mode)
    logger.info(
        f"Reading file with LlamaParse, file_name={file_name}, result_type={result_type}, "
        f"mode={mode.value}, parse_mode={parse_mode}"
    )
    try:
        api_key, key_type = get_api_key(owner_id, "llamaparse", LLAMA_PARSER_API_KEY)
        headers = {"Authorization": f"Bearer {api_key}"}
        # Form fields must be strings; a Python bool str()'s to "True"/"False", which
        # the multipart form parser will not reliably read as a boolean.
        form_data = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in settings.items()}

        bytes_io.seek(0)
        with httpx.Client(timeout=60.0) as client:
            upload_resp = client.post(
                f"{LLAMA_PARSE_BASE_URL}/upload",
                headers=headers,
                files={"file": (file_name, bytes_io, "application/octet-stream")},
                data=form_data,
            )
            upload_resp.raise_for_status()
            job_id = upload_resp.json()["id"]
            logger.info(f"LlamaParse job started: job_id={job_id}, file_name={file_name}")

            status = "PENDING"
            interval = LLAMA_PARSE_POLL_INITIAL_INTERVAL_S
            deadline = time.time() + LLAMA_PARSE_POLL_TIMEOUT_S
            job = {}
            while status == "PENDING":
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"LlamaParse job {job_id} still PENDING after {LLAMA_PARSE_POLL_TIMEOUT_S}s "
                        f"(file_name={file_name})"
                    )
                time.sleep(interval)
                interval = min(interval + 1, LLAMA_PARSE_POLL_MAX_INTERVAL_S)
                job_resp = client.get(f"{LLAMA_PARSE_BASE_URL}/job/{job_id}", headers=headers)
                job_resp.raise_for_status()
                job = job_resp.json()
                status = job["status"]

            if status != "SUCCESS":
                raise RuntimeError(
                    f"LlamaParse job {job_id} finished with status={status}, "
                    f"error={job.get('error_message')} (file_name={file_name})"
                )

            result_resp = client.get(
                f"{LLAMA_PARSE_BASE_URL}/job/{job_id}/result/{result_type}", headers=headers
            )
            result_resp.raise_for_status()
            result = result_resp.json()

        doc_text = result.get(result_type, "")
        job_metadata = result.get("job_metadata") or {}
        page_count = job_metadata.get("job_pages", 0)

        credits = estimated_credits(parse_mode, page_count)
        logger.info(
            f"LlamaParse completed: job_id={job_id}, page_count={page_count}, chars={len(doc_text)}, "
            f"parse_mode={parse_mode}, estimated_credits={credits}"
        )
        return {
            "content": doc_text,
            "extra_info": "",
            "llama_parse_page_count": page_count,
            "key_type": key_type,
            # Carried back so usage tracking can record what was actually billed,
            # not merely what was requested — the two differ when a mode is
            # auto-shifted (see get_file_text).
            "effective_mode": mode.value,
            "parse_mode": parse_mode,
            "llama_parse_credits": credits,
        }
    except Exception as e:
        logger.error(f"LlamaParse failed for file_name={file_name}, mode={mode.value}: {e}")
        raise

@traced_reader("Node: Extract DOCX Text", meta_keys=("docx_signals",))
def read_docx_to_plain_text(file_bytes):
    """Extract plain text from a .docx file (paragraphs + tables).

    Also counts inline shapes and tables while the document is already open —
    the same free-signal pattern PDF extraction uses (see
    `_extract_pdfplumber_signals`) — so `_read_docx_with_auto_shift` below can
    decide whether this local read is good enough without a second pass over
    the file. `doc.inline_shapes` covers embedded pictures; a floating/
    anchored image (text-wrapped rather than inline) would not be counted —
    a known blind spot, not a claim that this counts every image.
    """
    doc = Document(file_bytes)
    text_parts = []

    # Extract paragraph text
    for para in doc.paragraphs:
        if para.text.strip():
            text_parts.append(para.text)
            text_parts.append("\n")

    # Extract table cell text (tab-separated columns)
    for table in doc.tables:
        for row in table.rows:
            row_text = "\t".join(cell.text.strip() for cell in row.cells)
            if row_text.strip():
                text_parts.append(row_text)
                text_parts.append("\n")
        text_parts.append("\n")

    docx_signals = {"image_count": len(doc.inline_shapes), "table_count": len(doc.tables)}

    del doc  # Free python-docx document from memory
    plain_text = "".join(text_parts)
    del text_parts
    return {"content": plain_text, "extra_info": "", "docx_signals": docx_signals}


async def get_pdf_page_count_from_bytes(file_bytes: bytes) -> int:
    """Count pages of a PDF from raw byte content."""
    try:
        pdf_file = BytesIO(file_bytes)
        page_count = sum(1 for _ in PDFPage.get_pages(pdf_file, check_extractable=False))
        del pdf_file
        logger.info(f"PDF page count from bytes: {page_count}")
        return page_count
    except Exception as e:
        logger.error(f"Failed to count PDF pages from bytes: {e}")
        raise HTTPException(status_code=400, detail="Could not process the provided PDF file. It may be corrupt.")


async def get_pdf_page_count_from_url(file_url: str) -> int:
    """Download a PDF from a URL and count its pages (used for pre-upload validation)."""
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", file_url, follow_redirects=True) as response:
                response.raise_for_status()
                file_content = await response.aread()
        return await get_pdf_page_count_from_bytes(file_content)
    except httpx.RequestError as e:
        logger.error(f"Failed to download PDF from {file_url}: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve the file from the provided URL for validation.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error counting pages from URL {file_url}: {e}")
        raise HTTPException(status_code=400, detail=f"Could not process the PDF from the URL. Details: {e}")


def _table_rows_to_markdown(table_rows):
    """Render one pdfplumber-extracted table (list of rows, each a list of cells) as a
    GitHub-flavored markdown pipe table. `None`/empty cells become empty strings.
    Returns "" if the table has no rows.
    """
    if not table_rows:
        return ""

    def _clean_row(row):
        return [("" if cell is None else str(cell).strip()) for cell in row]

    rows = [_clean_row(row) for row in table_rows]
    header, data_rows = rows[0], rows[1:]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in data_rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _page_image_coverage(page) -> float:
    """Fraction of a pdfplumber page's area covered by its embedded images (0.0-1.0).
    Guards the divide-by-zero a zero-area page would otherwise raise.
    """
    page_area = page.width * page.height
    if not page_area:
        return 0.0
    image_area = sum(img["width"] * img["height"] for img in page.images)
    return image_area / page_area


def _extract_pdfplumber_signals(bytes_data, page_numbers_to_process):
    """One pdfplumber pass, opened once, doing double duty:

    1. Best-effort table extraction (as before) — `tables_by_index`, keyed by index
       into `page_numbers_to_process`, holds each page's tables rendered as markdown.
    2. Per-page structural signals (`char_count`, `image_coverage`, `table_count`) that
       feed `processing_modes.classify_pdf_target_mode` — see that module for what each
       signal means and the thresholds built on top of them.

    Reusing this one pass (rather than opening pdfplumber a second time just for
    signals) means `table_count` here is literally `len(tables)` from the same
    `extract_tables()` call already made for the markdown — never recomputed.

    Never raises — any failure is logged and empty results are returned, so the caller
    falls back to exactly today's pdfminer-only output with no auto-shift detection
    (a pdfplumber outage should degrade the product, not break it).
    """
    tables_by_index = {}
    page_signals = []
    try:
        # pdfminer has already consumed `bytes_data` by the time this runs, so open a fresh
        # BytesIO over the same underlying bytes rather than reusing the exhausted stream.
        pdf_bytes = bytes_data.getvalue()
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for index, page_number in enumerate(page_numbers_to_process):
                if page_number >= len(pdf.pages):
                    # Out of range (shouldn't normally happen — page count mismatch
                    # between pdfminer and pdfplumber). A zeroed signal never triggers
                    # a mode shift, which is the safe default here.
                    page_signals.append({"char_count": 0, "image_coverage": 0.0, "table_count": 0})
                    continue
                page = pdf.pages[page_number]
                tables = page.extract_tables()
                table_count = len(tables)

                page_signals.append({
                    "char_count": len(page.chars),
                    "image_coverage": _page_image_coverage(page),
                    "table_count": table_count,
                })

                if not tables:
                    continue
                logger.info(f"pdfplumber found {table_count} table(s) on page {page_number + 1}")
                markdown_tables = [_table_rows_to_markdown(table) for table in tables]
                markdown_tables = [t for t in markdown_tables if t]
                if markdown_tables:
                    tables_by_index[index] = "\n\n".join(markdown_tables)
    except Exception as e:
        logger.error(f"pdfplumber signal extraction failed, continuing without tables/signals: {e}")
        return {}, []
    return tables_by_index, page_signals


@traced_reader(
    "Node: Extract PDF Text",
    meta_keys=("page_count", "pages_extracted", "pages_truncated"),
)
def read_pdf_to_plain_text(bytes_data):
    """Extract plain text from a PDF using pdfminer. Processes up to MAX_PDF_PAGES pages.
    Also identifies the title by tracking the largest font size across lines.

    Additionally runs pdfplumber (best-effort) over the same page range to detect tables,
    rendering each as a markdown pipe table inserted right after that page's text so
    reading order is preserved. Table extraction is purely additive: any pdfplumber
    failure, or a PDF with no tables, leaves the pdfminer-derived content untouched.
    """
    page_texts = []  # text per page, same order as page_numbers_to_process
    additional_info = ""
    max_font_size = 0

    # Count total pages to enforce page limit
    try:
        total_pages = sum(1 for _ in PDFPage.get_pages(bytes_data))
        if total_pages > MAX_PDF_PAGES:
            # NOT a rejection — the log line used to say "PDF rejected", which is what
            # nothing here does. The document is silently TRUNCATED to the first
            # MAX_PDF_PAGES pages and then enriched, indexed and marked completed, so an
            # 80-page contract yields 10 pages of text with no user-visible signal that
            # 70 are missing. Kept as truncation (changing it to a hard failure is a
            # product call, not a logging one) but reported honestly, and surfaced on the
            # result as pages_truncated so callers can act on it.
            logger.warning(
                f"PDF truncated: reading first {MAX_PDF_PAGES} of {total_pages} pages; "
                f"the remaining {total_pages - MAX_PDF_PAGES} are not extracted"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to count PDF pages: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "PDF_PROCESSING_ERROR",
                "message": "Could not process the PDF file.",
                "details": "Please ensure the file is a valid PDF and try again."
            }
        )

    page_numbers_to_process = list(range(min(total_pages, MAX_PDF_PAGES)))
    logger.info(f"Processing {len(page_numbers_to_process)} of {total_pages} PDF pages")

    try:
        for page_layout in extract_pages(bytes_data, page_numbers=page_numbers_to_process):
            page_parts = []
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    for text_line in element:
                        line_text = text_line.get_text().strip()
                        if not line_text:
                            continue
                        line_font_sizes = [char.size for char in text_line if isinstance(char, LTChar)]
                        if line_font_sizes:
                            avg_font_size = sum(line_font_sizes) / len(line_font_sizes)
                            page_parts.append(line_text)
                            page_parts.append("\n")
                            # Track the line with the largest font as the likely title
                            if avg_font_size > max_font_size:
                                max_font_size = avg_font_size
                                additional_info = line_text
            page_texts.append("".join(page_parts))
            del page_layout  # Free page layout tree after each page
    except Exception as e:
        logger.error(f"Error extracting text from PDF page: {e}")
        raise

    # Best-effort table extraction (+ per-page structural signals) over the same page
    # range. Wrapped so a pdfplumber failure can never break the free pdfminer-only
    # path above.
    tables_by_page_index, page_signals = _extract_pdfplumber_signals(bytes_data, page_numbers_to_process)

    content_parts = []
    for index, page_text in enumerate(page_texts):
        content_parts.append(page_text)
        table_markdown = tables_by_page_index.get(index)
        if table_markdown:
            content_parts.append(table_markdown)
            content_parts.append("\n")
    del page_texts

    content = "".join(content_parts)
    del content_parts
    extra_info = f"file title: {additional_info} \n" if additional_info else ""
    logger.info(f"PDF extraction complete: chars={len(content)}, pages={total_pages}, title_detected={bool(additional_info)}")
    # page_count is kept for callers/analytics that still want a simple whole-document
    # summary. page_signals (per page: char_count, image_coverage, table_count) is what
    # the auto-shift decision actually runs on now — see
    # processing_modes.classify_pdf_target_mode and _read_pdf_with_auto_shift below.
    return {
        "content": content, "extra_info": extra_info, "page_count": total_pages,
        "page_signals": page_signals,
        # True when content covers only the first MAX_PDF_PAGES of page_count pages.
        "pages_truncated": total_pages > MAX_PDF_PAGES,
        "pages_extracted": min(total_pages, MAX_PDF_PAGES),
    }


@traced_reader("Node: Extract Text File")
def read_txt_to_plain_text(bytes_data):
    """Read a plain text or markdown file from a BytesIO stream."""
    content = bytes_data.read().decode("utf-8")
    return {"content": content, "extra_info": ""}


@traced_reader("Node: Extract HTML Text")
def read_html_to_plain_text(file_bytes: BytesIO):
    """Convert HTML content to plain text using UnstructuredHTMLLoader.
    Writes to a temp file because the loader requires a file path.
    """
    temp_file_path = None
    try:
        logger.info("Reading HTML file via UnstructuredHTMLLoader")
        file_content = file_bytes.read()
        del file_bytes  # Free BytesIO from memory

        # Write to temp file (UnstructuredHTMLLoader needs a file path)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        del file_content  # Free raw HTML bytes

        loader = UnstructuredHTMLLoader(temp_file_path)
        docs = loader.load()
        extracted_content = "\n".join(doc.page_content for doc in docs)
        del docs
        del loader

        logger.info(f"HTML extraction complete: chars={len(extracted_content)}")
        return {"content": extracted_content, "extra_info": ""}
    except Exception as e:
        logger.error(f"Error reading HTML file: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading HTML file: {e}")
    finally:
        # Always clean up the temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                logger.error(f"Error removing temp file {temp_file_path}: {e}")


def get_audio_duration(file_path) -> float:
    """Get the duration of an audio file in seconds."""
    try:
        file_path_str = str(file_path)
        ext = os.path.splitext(file_path_str)[1].lower()
        if ext == '.aac':
            with open(file_path_str, 'rb') as audio_file:
                return _adts_duration(audio_file.read())
        container = _AUDIO_CONTAINERS.get(ext)
        if not container:
            logger.error(f"Unsupported audio format for duration: {ext}")
            return 0
        with open(file_path_str, 'rb') as audio_file:
            return TinyTag.get(file_obj=audio_file, filename=f"audio{container}").duration or 0
    except Exception as e:
        raise ValueError(f"Error getting audio duration for {file_path}: {e}")


@traced_reader(
    "API: Whisper - Transcribe Audio",
    inputs=("transcript_provider", "owner_id"),
    meta_keys=("key_type", "provider", "model"),
)
def read_audio_to_plain_text(file_bytes: BytesIO, transcript_provider: Optional[str] = None, owner_id: Optional[str] = None):
    """Transcribe audio to text using OpenAI Whisper or Groq Whisper."""
    if transcript_provider is None:
        transcript_provider = celery_setup_env.transcript_provider
    logger.info(f"Transcribing audio using provider={transcript_provider}")

    if transcript_provider == 'openai':
        api_key, key_type = get_api_key(owner_id, "openai", OPENAI_API_KEY)
        client = OpenAI(api_key=api_key)
        model = "whisper-1"
        temperature = None
        content = client.audio.transcriptions.create(model=model, file=file_bytes, response_format="text")

    elif transcript_provider == 'groq':
        api_key, key_type = get_api_key(owner_id, "groq", GROQ_API_KEY)
        client = Groq(api_key=api_key)
        model = "whisper-large-v3-turbo"
        temperature = 0.0
        content = client.audio.transcriptions.create(
            file=file_bytes, model=model, language="en", temperature=temperature, response_format="text"
        )
    else:
        raise ValueError(f"Unsupported transcript provider: {transcript_provider}")

    logger.info(f"Audio transcription complete: provider={transcript_provider}, model={model}, chars={len(str(content))}")
    return {
        "content": content, "extra_info": "", "key_type": key_type,
        "model": model, "provider": transcript_provider, "temperature": temperature
    }


# ---------------------------------------------------------------------------
# File Router — Routes file to the correct reader based on type
# ---------------------------------------------------------------------------

def get_file_text(bytes_data, file_type, filename, mode: ProcessingMode = ProcessingMode.TEXT_BASED,
                  transcript_provider=None, file_path=None, owner_id: Optional[str] = None):
    """Route file bytes to the appropriate reader based on MIME type and processing mode.
    Returns a dict with at least {"content": str, "extra_info": str}.

    Two rules govern the routing, and both are deliberate:

    1. **A mode only applies to real document formats.** Plain text, markdown, HTML and
       audio are read by their own readers no matter what mode was asked for, because
       "is it scanned?" is a meaningless question about a .txt file. (This is a change
       from the older behaviour, where the LlamaParse flag hijacked *every* file type
       including audio.)

    2. **A text-based PDF or DOCX that turns out to hold more than plain text gets
       upgraded automatically.** See the auto-shift blocks below.
    """
    logger.info(f"Routing file: type={file_type}, filename={filename}, mode={mode.value}")
    bytes_io = BytesIO(bytes_data)
    bytes_io.name = filename
    bytes_io.seek(0)

    use_llamaparse = (
        llamaparse_settings(mode) is not None
        and file_type in LLAMAPARSE_ELIGIBLE_TYPES
    )

    # LangSmith step for the routing decision itself. The per-backend steps below
    # show WHICH extractor ran; this one shows WHY it was picked, and — for a PDF or
    # DOCX that got auto-shifted onto a paid backend — that the shift happened at all.
    # Before this, the shift was only ever a log line.
    with traced_step(
        "Node: Route File To Extractor",
        inputs={"file_type": file_type, "filename": filename, "mode": mode.value},
        file_type=str(file_type),
        filename=str(filename),
        mode_requested=mode.value,
        bytes_in=len(bytes_data) if bytes_data is not None else 0,
    ) as route_step:
        try:
            if use_llamaparse:
                extracted_content = read_with_llama_parse(bytes_io, filename, 'markdown', owner_id=owner_id, mode=mode)
            elif file_type == "application/pdf":
                extracted_content = _read_pdf_with_auto_shift(
                    bytes_io, bytes_data, filename, mode, owner_id=owner_id
                )
            elif file_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                extracted_content = _read_docx_with_auto_shift(
                    bytes_io, bytes_data, filename, mode, owner_id=owner_id
                )
            elif file_type == "text/plain" or (file_type == "application/octet-stream" and filename.endswith('.md')):
                extracted_content = read_txt_to_plain_text(bytes_io)
            elif file_type == "text/html":
                extracted_content = read_html_to_plain_text(bytes_io)
            elif file_type == "text/markdown":
                extracted_content = read_txt_to_plain_text(bytes_io)
            elif file_type in ["audio/mpeg", "audio/wav", "audio/ogg", "video/mpeg"]:
                bytes_io.name = filename
                extracted_content = read_audio_to_plain_text(bytes_io, transcript_provider, owner_id=owner_id)
            elif file_type == "application/msword":
                raise ValueError("MS Word (.doc) files are not supported.")
            else:
                raise ValueError("Unsupported file type.")

            del bytes_io  # Free BytesIO wrapper

            # Every reader gets stamped with the mode that actually ran, so the code
            # downstream never has to re-derive it. Readers that reached LlamaParse have
            # already filled these in (including an auto-shifted one), so only the local
            # readers need the default.
            if isinstance(extracted_content, dict):
                extracted_content.setdefault("effective_mode", ProcessingMode.TEXT_BASED.value)
                extracted_content.setdefault("parse_mode", None)
                extracted_content.setdefault("llama_parse_credits", 0)
                # What the router actually did, on the trace: which mode ran, whether
                # it was auto-shifted from the requested one, and what it cost.
                route_step.add(
                    mode_effective=extracted_content.get("effective_mode"),
                    mode_auto_shifted_from=extracted_content.get("mode_auto_shifted_from"),
                    auto_shifted=bool(extracted_content.get("mode_auto_shifted_from")),
                    parse_mode=extracted_content.get("parse_mode"),
                    llama_parse_credits=extracted_content.get("llama_parse_credits"),
                    pages_truncated=extracted_content.get("pages_truncated"),
                    page_count=extracted_content.get("page_count") or extracted_content.get("llama_parse_page_count"),
                )
                route_step.set_text(extracted_content.get("content"))
            return extracted_content
        except Exception as e:
            logger.error(f"Error in get_file_text for filename={filename}: {e}")
            raise


def _read_pdf_with_auto_shift(bytes_io: BytesIO, bytes_data, filename: str,
                              mode: ProcessingMode, owner_id: Optional[str] = None):
    """Read a PDF locally, and switch to the right paid mode by ourselves if the free
    read reveals the document needs more than plain text extraction.

    Why this exists: a scanned PDF is a stack of photographs — there is no text inside
    it to find, so a text extractor returns an empty string, and the document would
    then fail as "too short" even though nothing is wrong with it. A PDF full of tables
    reads fine as prose but loses all its structure. A PDF with charts/diagrams loses
    whatever the image was showing. In every one of these cases the free path already
    ran (for free) and told us something worth knowing — someone uploading such a file
    has no reason to know they were supposed to tick a box first.

    So we look, using the same pdfplumber pass that already runs for table detection
    (see read_pdf_to_plain_text -> _extract_pdfplumber_signals). Each page's signals
    are classified independently and aggregated into one target mode (see
    processing_modes.classify_pdf_target_mode for the exact rules) — TEXT_BASED means
    nothing looked wrong, and the free result is returned untouched.

    When a target mode is found, the free result (pdfminer text + pdfplumber's inline
    table markdown) is *discarded entirely* and the whole document is re-read via
    LlamaParse in that mode — a deliberate choice: pdfplumber's local table extraction
    is a free detector signal here, not a permanent extraction path, once a shift is
    triggered. This mirrors exactly how the scanned-only version of this function
    worked before it was generalised.

    Only ever upgrades *towards* doing more (paid) work, and only for PDFs. If the
    caller already asked for a LlamaParse mode, this function is not reached at all —
    get_file_text only calls it when `mode` resolves to the free local path.
    """
    local_result = read_pdf_to_plain_text(bytes_io)
    page_signals = local_result.get("page_signals", [])

    target_mode, reason = classify_pdf_target_mode(page_signals)

    # The classifier's verdict, on the enclosing routing step — this used to exist
    # only as a log line, so nobody could tell from a trace why a document had been
    # re-read on a paid backend.
    add_step_metadata(
        auto_shift_target=target_mode.value,
        auto_shift_reason=reason,
        pages_classified=len(page_signals),
    )

    if target_mode == ProcessingMode.TEXT_BASED:
        return local_result

    logger.info(f"Auto-shifting mode {mode.value} -> {target_mode.value}: reason={reason} (filename={filename})")
    try:
        retry_io = BytesIO(bytes_data)
        retry_io.name = filename
        retry_io.seek(0)
        result = read_with_llama_parse(
            retry_io, filename, 'markdown', owner_id=owner_id, mode=target_mode
        )
        # Flagged so the auto-shift is visible in usage analytics rather than looking
        # like the user picked this mode themselves — these cost money and someone will
        # eventually ask why.
        result["mode_auto_shifted_from"] = mode.value
        return result
    except Exception as e:
        # Reprocessing in the detected mode is a best-effort rescue of a document that
        # would otherwise come back worse than it should. If it cannot run, hand back
        # the local result — the free path is still a valid (if inferior) answer.
        logger.error(f"Auto-shift to {target_mode.value} failed for filename={filename}, falling back to local text: {e}")
        return local_result


def _read_docx_with_auto_shift(bytes_io: BytesIO, bytes_data, filename: str,
                               mode: ProcessingMode, owner_id: Optional[str] = None):
    """Read a DOCX locally, and switch to a paid mode if the free read reveals an
    embedded image or a table — the same pattern as `_read_pdf_with_auto_shift`,
    applied to the one other format LlamaParse already accepts
    (`LLAMAPARSE_ELIGIBLE_TYPES`).

    Why this exists: `read_docx_to_plain_text` extracts paragraphs and tables (as
    tab-separated text) but never touches embedded pictures at all — an image in a
    DOCX is silently and permanently dropped today (see
    `accept_docx_with_image.docx` in fs-agent-knowledge's fixtures). A DOCX full of
    tables reads fine as prose but loses the structure LlamaParse's table flags
    would keep. Both are things the free read already told us, for free, the same
    way a PDF's free read tells us it's a scan.

    `classify_docx_target_mode` looks at the signals `read_docx_to_plain_text`
    already collected in the same pass (see `docx_signals`) — TEXT_BASED means
    nothing looked wrong, and the free result is returned untouched.

    Only ever upgrades *towards* doing more (paid) work, and only for DOCX. If the
    caller already asked for a LlamaParse mode, this function is not reached at
    all — get_file_text only calls it when `mode` resolves to the free local path.
    """
    local_result = read_docx_to_plain_text(bytes_io)
    signals = local_result.get("docx_signals", {"image_count": 0, "table_count": 0})

    target_mode, reason = classify_docx_target_mode(signals)

    # Same as the PDF path above: put the classifier's verdict on the routing step.
    add_step_metadata(
        auto_shift_target=target_mode.value,
        auto_shift_reason=reason,
        docx_image_count=signals.get("image_count"),
        docx_table_count=signals.get("table_count"),
    )

    if target_mode == ProcessingMode.TEXT_BASED:
        return local_result

    logger.info(f"Auto-shifting mode {mode.value} -> {target_mode.value}: reason={reason} (filename={filename})")
    try:
        retry_io = BytesIO(bytes_data)
        retry_io.name = filename
        retry_io.seek(0)
        result = read_with_llama_parse(
            retry_io, filename, 'markdown', owner_id=owner_id, mode=target_mode
        )
        # Flagged so the auto-shift is visible in usage analytics rather than looking
        # like the user picked this mode themselves — these cost money and someone will
        # eventually ask why.
        result["mode_auto_shifted_from"] = mode.value
        return result
    except Exception as e:
        # Reprocessing in the detected mode is a best-effort rescue of a document that
        # would otherwise come back worse than it should. If it cannot run, hand back
        # the local result — the free path is still a valid (if inferior) answer.
        logger.error(f"Auto-shift to {target_mode.value} failed for filename={filename}, falling back to local text: {e}")
        return local_result


# ---------------------------------------------------------------------------
# Content Type Inference
# ---------------------------------------------------------------------------

def _infer_content_type(file_extension: str) -> Optional[str]:
    """Infer MIME type from file extension when content_type is not provided."""
    if file_extension in _EXTENSION_TO_CONTENT_TYPE:
        return _EXTENSION_TO_CONTENT_TYPE[file_extension]
    if file_extension in [".mp3", ".mpeg", ".wav", ".aac", ".ogg"]:
        return "audio/mpeg"
    return None


# ---------------------------------------------------------------------------
# Text Extraction Pipeline
# ---------------------------------------------------------------------------

@track_time("Node: Extract Text Pipeline")
def extract_text_pipeline(file_path, unique_filename, content_type, file_extension,
                          mode: ProcessingMode = ProcessingMode.TEXT_BASED,
                          transcript_provider=None, knowledge_id=None, object_id=None, owner_id: Optional[str] = None):
    """Full text extraction pipeline: read file -> parse -> get audio duration.
    Returns (result_dict, audio_seconds, file_path).
    Cleans up the temp file in finally block.
    """
    logger.info(f"Starting text extraction: filename={unique_filename}, type={content_type}, ext={file_extension}")
    start_time = time.time()
    file_content = None
    try:
        if not file_path or not os.path.exists(file_path):
            logger.error(f"File not found at path: {file_path}")
            return {"Error": "File not found at the specified path."}

        # Infer content type from extension if not provided
        if not content_type:
            content_type = _infer_content_type(file_extension)
            logger.info(f"Inferred content_type={content_type} from extension={file_extension}")

        # Read file bytes from disk
        with open(file_path, "rb") as f:
            file_content = f.read()

        # Parse file content into text
        try:
            result = get_file_text(file_content, content_type, unique_filename, mode, transcript_provider, file_path, owner_id=owner_id)
            del file_content  # Free file bytes after successful parsing
            file_content = None
        except ValueError:
            logger.error(f"Unsupported file type: {content_type}")
            raise Exception("Invalid file type, please upload valid file")

        time_taken = time.time() - start_time
        logger.info(f"Text extraction complete in {time_taken:.2f}s for filename={unique_filename}")

        # Calculate audio duration for audio/video files
        audio_seconds = None
        if content_type.startswith('audio/') or content_type.startswith('video/'):
            audio_seconds = get_audio_duration(file_path)
            logger.info(f"Audio duration: {audio_seconds:.1f}s for filename={unique_filename}")

        return result, audio_seconds, file_path
    except Exception as e:
        logger.error(f"Text extraction failed for knowledge_id={knowledge_id}, object_id={object_id}: {e}")
        update_processing_status(knowledge_id, object_id, "failed")
        raise
    finally:
        # Free file bytes if still held
        if file_content is not None:
            del file_content
        # Clean up temp file from disk
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Cleaned up temp file: {file_path}")
            except Exception as e:
                logger.error(f"Error cleaning up temp file {file_path}: {e}")


# ---------------------------------------------------------------------------
# Extraction Result Unpacking
# ---------------------------------------------------------------------------

def _unpack_extracted_content(extracted_content):
    """Normalize extraction result into a consistent dict regardless of return format.
    Handles dict (standard), tuple (legacy), and string (fallback) formats.
    """
    fields = {
        "extracted_text": "", "extra_info": "", "llama_parse_page_count": 0,
        "extraction_key_type": None, "transcript_model": None,
        "transcript_provider_used": None, "transcript_temperature": None,
        # What actually ran, as opposed to what was asked for. Defaults describe the
        # free local path so a reader that reports nothing is costed at zero.
        "effective_mode": ProcessingMode.TEXT_BASED.value, "parse_mode": None,
        "llama_parse_credits": 0, "mode_auto_shifted_from": None,
    }

    if isinstance(extracted_content, dict):
        fields["extracted_text"] = extracted_content.get("content", "")
        fields["extra_info"] = extracted_content.get("extra_info", "")
        fields["llama_parse_page_count"] = extracted_content.get("llama_parse_page_count", 0)
        fields["extraction_key_type"] = extracted_content.get("key_type")
        fields["transcript_model"] = extracted_content.get("model")
        fields["transcript_provider_used"] = extracted_content.get("provider")
        fields["transcript_temperature"] = extracted_content.get("temperature")
        fields["effective_mode"] = extracted_content.get("effective_mode", ProcessingMode.TEXT_BASED.value)
        fields["parse_mode"] = extracted_content.get("parse_mode")
        fields["llama_parse_credits"] = extracted_content.get("llama_parse_credits", 0)
        fields["mode_auto_shifted_from"] = extracted_content.get("mode_auto_shifted_from")
    elif isinstance(extracted_content, tuple) and len(extracted_content) == 2:
        # Legacy tuple format: (text, extra_info)
        fields["extracted_text"], fields["extra_info"] = extracted_content
    else:
        # Fallback: treat as raw string
        fields["extracted_text"] = str(extracted_content)

    del extracted_content  # Free original content from memory
    return fields


def _determine_extraction_method(parse_mode: Optional[str], content_type: str, transcript_provider: Optional[str]) -> str:
    """Determine the extraction method label for usage tracking events.

    Derived from the parse mode that actually ran, not from the mode that was requested
    — a request for VISUAL on a .txt file still reads as "local", because that is what
    happened.

    The returned values are deliberately unchanged from before ("llama_parse", "local",
    "whisper_*"). Analytics groups on this label and there are existing rows carrying it,
    so making it more specific here would split the history in two. The finer detail
    (which parse mode, what it cost) travels as event metadata instead.
    """
    if parse_mode:
        return "llama_parse"
    if content_type.startswith('audio/') or content_type.startswith('video/'):
        if transcript_provider in ("openai", "groq"):
            return f"whisper_{transcript_provider}"
        return "local"
    return "local"


# ---------------------------------------------------------------------------
# Main Pipeline: Download -> Extract -> Enrich -> Index -> Track
# ---------------------------------------------------------------------------

@track_time("Node: Process And Index File")
@traceable(name="Process Document")
def process_and_index_file(user_id: Optional[str], knowledge_id, object_id, file_url, file_name, content_type,
                           use_llama_parse, contextualise_chunks, chunk_size, hybrid_search, embedding_model,
                           transcript_provider, block_chunking=False, enable_chunking=True,
                           extra_metadata=None, reindex=False, document_processing_mode=None):
    """End-to-end file processing pipeline called by Celery workers.
    Steps: download -> validate -> extract text -> enrich with LLM -> index in vectordb -> track usage.

    ``document_processing_mode`` is the newer way to say how the file should be read;
    ``use_llama_parse`` is the older boolean that meant roughly the same thing with less
    precision. Both are accepted so that messages already sitting in the queue when this
    deploys still process correctly — resolve_mode() sorts out which to believe.
    """
    logger.info(f"Starting file processing: file_name={file_name}, knowledge_id={knowledge_id}, object_id={object_id}")
    mode = resolve_mode(document_processing_mode, use_llama_parse)
    logger.info(
        f"Resolved processing mode: {mode.value} "
        f"(requested={document_processing_mode!r}, legacy_use_llama_parse={use_llama_parse!r})"
    )
    processing_start_time = time.time()
    reset_embedding_cost()
    owner_id = get_owner_id_from_service_id(knowledge_id)

    try:
        # Skip if already processed (idempotency check)
        status = retrieve_knowledge_processing_status(knowledge_id, object_id)
        if status == "completed":
            logger.info(f"Task already completed for object_id={object_id}, skipping")
            return

        # Step 1: Download file from S3/URL
        logger.info(f"Downloading file from {file_url}")
        file_path, file_extension, unique_filename = download_file(file_url)
        logger.info(f"Downloaded: filename={unique_filename}, type={content_type}, ext={file_extension}")

        # Validate file type against supported types
        if content_type not in SUPPORTED_FILE_TYPES or file_extension not in SUPPORTED_FILE_TYPES[content_type]:
            logger.error(f"Unsupported file type: {content_type} ({file_extension})")
            update_processing_status(knowledge_id, object_id, "failed", error_detail=f"Unsupported file type: {content_type} ({file_extension})")
            return

        # Step 2: Extract text from file
        update_processing_status(knowledge_id, object_id, "extracting")
        extracted_content, audio_seconds, file_path = extract_text_pipeline(
            file_path, unique_filename, content_type, file_extension, mode,
            transcript_provider, knowledge_id, object_id, owner_id=owner_id
        )

        # Unpack extraction result into normalized fields
        fields = _unpack_extracted_content(extracted_content)
        extracted_text = fields["extracted_text"]
        extra_info = fields["extra_info"]

        char_count = len(extracted_text.strip())
        logger.info(f"Extracted {char_count} chars from {file_name}")

        if char_count < MIN_TEXT_LENGTH:
            logger.error(f"Extracted text too short ({char_count} chars) for file_name={file_name}")
            update_processing_status(knowledge_id, object_id, "failed", error_detail=f"Extracted text too short ({char_count} chars), minimum is {MIN_TEXT_LENGTH}")
            raise HTTPException(status_code=400, detail="File is empty or too short. It must contain at least 10 characters of text.")

        doc_type = doc_type_metadata(file_extension)

        # Step 3: Enrich — add metadata, summaries, and chunk the document via LLM
        update_processing_status(knowledge_id, object_id, "enriching")
        logger.info(f"Enriching document: knowledge_id={knowledge_id}, doc_type={doc_type}")
        enriched_parts = [f"File Name: {file_name}\n\n"]
        if extra_info:
            enriched_parts.append(f"Extra Info: {extra_info}\n\n")
        enriched_parts.append(f"Extracted Text: {extracted_text}\n")
        enriched_text = "".join(enriched_parts)
        del enriched_parts  # Free list from memory

        enriched_document, chunks_only = enrich_document(
            enriched_text, knowledge_id, user_id, object_id,
            chunk_size=chunk_size, block_chunking=block_chunking,
            enable_chunking=enable_chunking, owner_id=owner_id,
            # The mode that read the file also picks the model that reads the result —
            # anything but text_based produces messier text and enriches with the
            # stronger model (see processing_modes.enrichment_override_for_mode).
            processing_mode=mode,
        )
        del enriched_text  # Free enriched text after LLM processing
        enriched_document["file_name"] = file_name

        # Step 4: Index — store enriched document and chunks in vector database
        update_processing_status(knowledge_id, object_id, "indexing")

        # Delete stale doc+chunks before reindexing when triggered by freshness check
        if reindex and extra_metadata and extra_metadata.get("source_id"):
            _source_id = extra_metadata["source_id"]
            logger.info(f"Reindex: deleting stale doc+chunks for source_id='{_source_id}' knowledge_id='{knowledge_id}'")
            from celery_utils.indexing.indexer import delete_document_by_source_id, delete_chunks_by_source_id
            delete_document_by_source_id(knowledge_id=knowledge_id, source_id=_source_id, embedding_model=embedding_model)
            delete_chunks_by_source_id(knowledge_id=knowledge_id, source_id=_source_id, embedding_model=embedding_model)

        logger.info(f"Indexing in vectordb: knowledge_id={knowledge_id}, chunks_only={chunks_only}")
        index_in_vectordb(
            object_id=object_id, knowledge_id=knowledge_id, user_id=user_id,
            enriched_metadata=enriched_document, embedding_model=embedding_model,
            chunks_only=chunks_only, owner_id=owner_id,
            extra_metadata=extra_metadata,
        )
        logger.info(f"Indexed enriched document in vectordb for object_id={object_id}")
        del enriched_document  # Free enriched document after indexing

        # Step 5: Upload raw extracted text to S3 for reference
        upload_extracted_text_to_s3(extracted_text=extracted_text, file_name=file_name, object_id=object_id)

        # Step 6: Mark task as completed and record duration
        processing_time_ms = mark_task_completed(
            knowledge_id, object_id, processing_start_time,
            effective_mode=fields["effective_mode"],
        )
        embed_tokens, embed_cost_usd = get_embedding_cost()
        mark_indexing_time(processing_time_ms, knowledge_id=knowledge_id, object_id=object_id,
                           char_count=char_count, embedding_tokens=embed_tokens,
                           embedding_cost_usd=embed_cost_usd)
        logger.info(f"File processing complete: file_name={file_name}, knowledge_id={knowledge_id}, duration={processing_time_ms}ms")

        # Step 7: Send usage tracking event
        extraction_method = _determine_extraction_method(fields["parse_mode"], content_type, transcript_provider)
        send_file_processing_event(
            file_extension=file_extension,
            extraction_method=extraction_method,
            char_count=char_count,
            llama_parse_page_count=fields["llama_parse_page_count"],
            audio_seconds=audio_seconds,
            duration_ms=processing_time_ms,
            knowledge_id=knowledge_id,
            object_id=object_id,
            key_type=fields["extraction_key_type"],
            provider=fields["transcript_provider_used"],
            model=fields["transcript_model"],
            temperature=fields["transcript_temperature"],
            # Cost varies by up to 10x across modes, so the mode has to travel with the
            # event — page count alone cannot tell a 3-credit page from a 30-credit one.
            requested_mode=mode.value,
            effective_mode=fields["effective_mode"],
            parse_mode=fields["parse_mode"],
            llama_parse_credits=fields["llama_parse_credits"],
            mode_auto_shifted_from=fields["mode_auto_shifted_from"],
        )

    except Exception as e:
        logger.error(f"File processing failed: file_name={file_name}, knowledge_id={knowledge_id}: {e}")
        update_processing_status(knowledge_id, object_id, "failed", error_detail=str(e))
