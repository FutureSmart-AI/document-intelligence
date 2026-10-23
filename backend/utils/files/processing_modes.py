"""Resolving which document processing mode a request actually asked for.

The API accepts the same intent expressed two ways, because it grew a better way of
saying it without being able to retire the old one:

  * ``document_processing_mode`` — the current field. Says what the document is like
    ("scanned", "visual"), and lets the worker decide how to read it.
  * ``use_llama_parse`` — the original boolean. Meant "parse this the fancy way", with no
    way to say *which* fancy way, and no way to distinguish a scan from a chart.

Clients migrate at their own pace, so both keep working and this module decides which one
to believe. The actual parser settings live in the worker, not here — the API's job is to
record the intent faithfully and pass it along.
"""

from typing import Optional

from utils.models.enums import DocumentProcessingMode
from utils.logger import logger


def resolve_document_processing_mode(
    document_processing_mode: Optional[DocumentProcessingMode],
    use_llama_parse: Optional[bool],
) -> DocumentProcessingMode:
    """Collapse the new enum and the legacy boolean into one effective mode.

    Precedence, and the reasoning for each step:

    1. **An explicit mode wins.** If the caller named a mode, that is the answer, even if
       they also sent the old boolean — a client that knows about modes is the more
       informed of the two voices.
    2. **``use_llama_parse=True`` becomes LAYOUT_AWARE.** This is the one mapping worth
       arguing about. The old code path asked LlamaParse for markdown with no OCR and no
       vision model, which is what LAYOUT_AWARE now does. Mapping it to SCANNED or VISUAL
       would be a defensible guess about what callers *meant*, but it would raise the
       per-page cost for every client that has not been updated, without anyone asking.
       A compatibility shim should not change what anyone is billed.
    3. **Nothing supplied means AUTO** — the caller didn't say what the document is like,
       so the worker reads it locally and auto-detects (see fs-agent-workers
       ``classify_pdf_target_mode``). That is exactly what ``use_llama_parse=False`` did
       before (TEXT_BASED's local-read-then-auto-shift path), so existing callers see no
       behaviour change — only the wire value naming "no mode was given" changes.
    """
    if document_processing_mode is not None:
        # Note the deliberate asymmetry: an explicit mode silently wins over the boolean
        # rather than erroring on the conflict. Rejecting the pair would break clients
        # that send the old field out of habit while adopting the new one.
        if use_llama_parse is not None:
            logger.info(
                f"Both document_processing_mode={document_processing_mode.value} and legacy "
                f"use_llama_parse={use_llama_parse} supplied — using the explicit mode"
            )
        return document_processing_mode

    if use_llama_parse:
        logger.info("Legacy use_llama_parse=True mapped to document_processing_mode=layout_aware")
        return DocumentProcessingMode.LAYOUT_AWARE

    return DocumentProcessingMode.AUTO


def derive_use_llama_parse(mode: DocumentProcessingMode) -> bool:
    """The legacy boolean equivalent of a mode, for the SQS payload.

    Sent alongside the mode itself for one release so that a worker running the previous
    build — which has never heard of modes — still reads the file with roughly the right
    machinery instead of defaulting to plain text. Delete once every worker is upgraded.
    """
    return mode not in (DocumentProcessingMode.TEXT_BASED, DocumentProcessingMode.AUTO)
