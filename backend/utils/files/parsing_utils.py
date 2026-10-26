import os
from io import BytesIO
from typing import Optional

import httpx
from fastapi import HTTPException
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTChar
from pdfminer.pdfpage import PDFPage
from langchain_community.document_loaders import UnstructuredHTMLLoader
from docx import Document
from llama_parse import LlamaParse
from groq import Groq
from openai import OpenAI

from utils.logger import logger
from utils.setup_env import (
    LLAMA_PARSE_API_KEY,
    OPENAI_API_KEY,
    GROQ_API_KEY,
    TRANSCRIPT_PROVIDER,
)


SUPPORTED_FILE_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "audio/mpeg": [".mp3", ".mpeg", ".aac"],
    "audio/wav": [".wav"],
    "audio/x-wav": [".wav"],
    "audio/ogg": [".ogg"],
    "video/mpeg": [".mpeg"],
    "text/html": [".html", ".htm"],
    "text/markdown": [".md"],
    "application/octet-stream": [".md"],
}


def is_supported_file_type(content_type: str) -> bool:
    return content_type in SUPPORTED_FILE_TYPES


async def get_pdf_page_count_from_url(file_url: str) -> int:
    try:
        from utils.files.s3_utils import read_file_bytes
        file_content = await read_file_bytes(file_url)
        return await get_pdf_page_count_from_bytes(file_content)
    except httpx.RequestError as e:
        logger.error(f"Failed to download PDF from {file_url}: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve the file from the provided URL for validation.")
    except Exception as e:
        logger.error(f"An error occurred during page count from URL {file_url}: {e}")
        raise HTTPException(status_code=400, detail=f"Could not process the PDF from the URL. Details: {e}")


async def get_pdf_page_count_from_bytes(file_bytes: bytes) -> int:
    try:
        pdf_file = BytesIO(file_bytes)
        pages = list(PDFPage.get_pages(pdf_file, check_extractable=False))
        return len(pages)
    except Exception as e:
        logger.error(f"Failed to count PDF pages from bytes: {e}")
        raise HTTPException(status_code=400, detail="Could not process the provided PDF file. It may be corrupt.")


def get_docx_page_count_from_bytes(file_bytes: bytes) -> Optional[int]:
    """Return page count from DOCX docProps/app.xml. Returns None if not available (e.g. programmatically generated)."""
    import zipfile
    import xml.etree.ElementTree as ET
    try:
        with zipfile.ZipFile(BytesIO(file_bytes)) as z:
            if "docProps/app.xml" in z.namelist():
                with z.open("docProps/app.xml") as f:
                    ns = {"ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"}
                    elem = ET.parse(f).find("ep:Pages", ns)
                    if elem is not None and elem.text:
                        return int(elem.text)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="File is not a valid DOCX document.")
    except Exception as e:
        logger.warning(f"Could not read DOCX page count: {e}")
    return None


def read_pdf_to_plain_text(bytes_data: BytesIO):
    content = ""
    additional_info = ""
    max_font_size = 0
    try:
        pages = list(PDFPage.get_pages(bytes_data))
        total_pages = len(pages)
        if total_pages > 10:
            logger.error(f"PDF rejected: Contains {total_pages} pages, which exceeds the 10-page limit.")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Failed to count PDF pages: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "PDF_PROCESSING_ERROR",
                "message": "Could not process the PDF file.",
                "details": "Please ensure the file is a valid PDF and try again.",
            },
        )

    page_numbers_to_process = list(range(min(total_pages, 10)))

    try:
        for _, page_layout in enumerate(extract_pages(bytes_data, page_numbers=page_numbers_to_process), start=1):
            for element in page_layout:
                if isinstance(element, LTTextContainer):
                    for text_line in element:
                        line_text = text_line.get_text().strip()
                        if line_text:
                            line_font_sizes = [char.size for char in text_line if isinstance(char, LTChar)]
                            if line_font_sizes:
                                average_font_size = sum(line_font_sizes) / len(line_font_sizes)
                                content += line_text + "\n"
                                if average_font_size > max_font_size:
                                    max_font_size = average_font_size
                                    additional_info = line_text
    except Exception as e:
        logger.error(f"An error occurred while processing the PDF: {e}")
        raise

    if additional_info:
        return {"content": content, "extra_info": f"file title: {additional_info} \n"}
    else:
        return {"content": content, "extra_info": ""}


def read_txt_to_plain_text(bytes_data: BytesIO):
    content = bytes_data.read().decode("utf-8")
    return {"content": content, "extra_info": ""}


def read_html_to_plain_text(file_bytes: BytesIO):
    try:
        logger.info(f"Reading HTML file")
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as temp_file:
            temp_file.write(file_bytes.read())
            temp_file_path = temp_file.name
        loader = UnstructuredHTMLLoader(temp_file_path)
        docs = loader.load()
        extracted_content = "\n".join([doc.page_content for doc in docs])
        return {"content": extracted_content, "extra_info": ""}
    except Exception as e:
        logger.error(f"Error reading HTML file: {e}")
        raise (HTTPException(status_code=500, detail=f"Error reading HTML file: {e}"))
    finally:
        if 'temp_file_path' in locals() and temp_file_path:
            os.remove(temp_file_path)


def read_with_llama_parse(bytes_io: BytesIO, file_name: str, result_type: str = 'markdown'):
    logger.info(f"Reading file with LlamaParse")
    try:
        parser = LlamaParse(api_key=LLAMA_PARSE_API_KEY, result_type=result_type)
        documents = parser.load_data(file_path=bytes_io, extra_info={"file_name": file_name})
        doc_text = ""
        for doc in documents:
            doc_text = doc_text + doc.text
        logger.info(f"LlamaParse parsed file successfully!, page count: {len(documents)}")
        return {"content": doc_text, "extra_info": "", "llama_parse_page_count": len(documents)}
    except Exception as e:
        logger.error(f"Error reading file with LlamaParse: {e}")
        raise


def read_docx_to_plain_text(file_bytes: BytesIO):
    doc = Document(file_bytes)
    plain_text = ""
    for para in doc.paragraphs:
        plain_text += para.text + "\n"
    for table in doc.tables:
        for row in table.rows:
            row_text = "\t".join([cell.text.strip() for cell in row.cells])
            plain_text += row_text + "\n"
        plain_text += "\n"
    return {"content": plain_text, "extra_info": ""}


def read_audio_to_plain_text(file_bytes: BytesIO, transcript_provider: Optional[str] = None):
    # Resolve provider early and log after resolution
    resolved_provider = (transcript_provider or TRANSCRIPT_PROVIDER or "openai")
    # normalise
    resolved_provider = resolved_provider.strip().lower() if isinstance(resolved_provider, str) else "openai"
    logger.info(f"Transcribing Audio using {resolved_provider}.")
    if resolved_provider == 'openai':
        client = OpenAI(api_key=OPENAI_API_KEY)
        transcription = client.audio.transcriptions.create(
            model='whisper-1',
            file=file_bytes,
            response_format="text",
        )
        content = transcription
    elif resolved_provider == 'groq':
        groq_client = Groq(api_key=GROQ_API_KEY)
        transcription = groq_client.audio.transcriptions.create(
            file=file_bytes,
            model="whisper-large-v3-turbo",
            language="en",
            temperature=0.0,
            response_format="text",
        )
        content = transcription
    else:
        content = ""
    logger.info(f"Audio transcript: {content}")
    return {"content": content, "extra_info": ""}


# Content types peek_document_text can read locally, for free — no LlamaParse,
# no transcription. Audio/video are excluded: there is nothing cheap to "peek"
# without paying for a transcript, and this function exists specifically to
# avoid that spend at collection-creation time (see field_generation.py).
# A peek is a heuristic aid, not the real ingestion path (that path enforces its
# own upload cap — see MAX_FILE_SIZE in utils/validation/file_validation_utils.py)
# — cap what we're willing to run pdfminer/python-docx over here so a large
# attachment can't make collection creation slow. Independent of that cap: files
# reach this preview endpoint before the ingestion size check ever runs.
MAX_PEEKED_FILE_BYTES = 15 * 1024 * 1024

_PEEKABLE_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/html",
    "text/markdown",
}


def peek_document_text(content_type: Optional[str], filename: str, file_bytes: bytes, max_chars: int = 3000) -> Optional[str]:
    """Best-effort, zero-cost local text read of an uploaded file, for LLM
    field-schema generation (utils/config/field_generation.py) before the
    document is actually ingested.

    Deliberately narrower than get_file_text: no LlamaParse call (this runs
    before the user has chosen a document_processing_mode, and it must never
    itself be the reason a "free" collection-creation step costs credits), no
    transcription. A scanned PDF with no text layer legitimately returns an
    empty/near-empty string here — that's fine, the generator just gets less
    signal from that one file, not a failure.

    Never raises: any read failure (corrupt file, unsupported type, empty
    upload) returns None so the caller can drop that one file and continue
    with the rest.
    """
    ct = content_type or ""
    is_markdown_octet = ct == "application/octet-stream" and filename.lower().endswith(".md")
    if ct not in _PEEKABLE_TYPES and not is_markdown_octet:
        return None
    if len(file_bytes) > MAX_PEEKED_FILE_BYTES:
        logger.info(f"peek_document_text: skipping {filename!r}, {len(file_bytes)} bytes exceeds peek cap")
        return None
    try:
        bytes_io = BytesIO(file_bytes)
        bytes_io.name = filename
        bytes_io.seek(0)
        if ct == "application/pdf":
            result = read_pdf_to_plain_text(bytes_io)
        elif ct == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            result = read_docx_to_plain_text(bytes_io)
        elif ct == "text/html":
            result = read_html_to_plain_text(bytes_io)
        else:  # text/plain, text/markdown, or .md sent as application/octet-stream
            result = read_txt_to_plain_text(bytes_io)
        content = (result or {}).get("content") or ""
        return content[:max_chars] if content else None
    except Exception as e:
        logger.warning(f"peek_document_text: could not read {filename!r} ({content_type}): {e}")
        return None


def get_file_text(bytes_data: bytes, file_type: str, filename: str, use_llama_parse: bool, transcript_provider: Optional[str] = None, file_path: Optional[str] = None):
    logger.info(f"fileType: {file_type}")
    logger.info(f"Reading file with type: {file_type} and filename: {filename}")
    bytes_io = BytesIO(bytes_data)
    bytes_io.name = filename
    bytes_io.seek(0)
    try:
        if use_llama_parse:
            result_type = 'text' if file_type in ['audio/mpeg', 'audio/wav'] else 'markdown'
            extracted_content = read_with_llama_parse(bytes_io, filename, result_type)
        else:
            if file_type == "application/pdf":
                extracted_content = read_pdf_to_plain_text(bytes_io)
            elif file_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                extracted_content = read_docx_to_plain_text(bytes_io)
            elif file_type == "text/plain" or (file_type == "application/octet-stream" and filename.endswith('.md')):
                extracted_content = read_txt_to_plain_text(bytes_io)
            elif file_type == "text/html":
                extracted_content = read_html_to_plain_text(bytes_io)
            elif file_type == "text/markdown":
                extracted_content = read_txt_to_plain_text(bytes_io)
            elif file_type in ["audio/mpeg", "audio/wav", "audio/ogg", "video/mpeg"]:
                bytes_io.name = filename
                extracted_content = read_audio_to_plain_text(bytes_io, transcript_provider)
            elif file_type == "application/msword":
                raise ValueError("MS Word (.doc) files are not supported.")
            else:
                raise ValueError("Unsupported file type.")
        return extracted_content
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        raise


