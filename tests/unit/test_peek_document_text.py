"""Unit tests for utils/files/parsing_utils.py::peek_document_text — the
cheap, local-only document peek used by the LLM field generator
(utils/config/field_generation.py) at collection-creation time. Must never
call LlamaParse/transcription and must never raise."""

from utils.files.parsing_utils import peek_document_text, MAX_PEEKED_FILE_BYTES


def test_peeks_plain_text():
    text = peek_document_text("text/plain", "notes.txt", b"Hello world")
    assert text == "Hello world"


def test_peeks_markdown_sent_as_octet_stream():
    text = peek_document_text("application/octet-stream", "readme.md", b"# Title\nBody")
    assert text == "# Title\nBody"


def test_truncates_to_max_chars():
    text = peek_document_text("text/plain", "big.txt", b"x" * 10000, max_chars=100)
    assert text is not None
    assert len(text) == 100


def test_unsupported_content_type_returns_none():
    assert peek_document_text("audio/mpeg", "voice.mp3", b"binary-ish") is None


def test_corrupt_pdf_returns_none_not_raises():
    assert peek_document_text("application/pdf", "broken.pdf", b"not a real pdf") is None


def test_oversized_file_is_skipped():
    huge = b"x" * (MAX_PEEKED_FILE_BYTES + 1)
    assert peek_document_text("text/plain", "huge.txt", huge) is None


def test_none_content_type_returns_none():
    assert peek_document_text(None, "mystery", b"some bytes") is None
