"""read_pdf_to_plain_text stayed pdfminer-only for plain text, but a table in a
digitally-native PDF used to flatten into an unreadable run of words on the free local
path (no LlamaParse call). This adds a best-effort pdfplumber pass over the same page
range to render any detected tables as GitHub-flavored markdown, inserted right after
that page's text so reading order is preserved.

The rule under test:
- A page with a detected table gets a markdown pipe table appended right after its text.
- A page with no tables leaves `content` byte-for-byte identical to before this change.
- Any pdfplumber failure (open, or table extraction) is swallowed — the function still
  returns the pdfminer-derived content, never raises.

These tests are pure: no real PDF file, no database, no network. `extract_pages` and
`PDFPage.get_pages` (pdfminer) and `pdfplumber.open` are all monkeypatched.
"""

import sys
import types
from contextlib import contextmanager
from io import BytesIO

import pytest

# celery_utils.processing.file_utils transitively imports celery_utils.indexing.indexer,
# which imports celery_utils.indexing.client — a module that pulls in qdrant-client,
# sentence-transformers, fastembed, langchain-huggingface, etc. None of that is exercised
# by read_pdf_to_plain_text, so it is stubbed out here rather than requiring those (heavy,
# unrelated) packages just to import this test module.
if "celery_utils.indexing.client" not in sys.modules:
    _fake_indexer_client = types.ModuleType("celery_utils.indexing.client")
    _fake_indexer_client.MultiQdrantDocumentIndexer = object
    _fake_indexer_client.MultiQdrantChunkIndexer = object
    _fake_indexer_client.EMBEDDING_MODELS = {}
    sys.modules["celery_utils.indexing.client"] = _fake_indexer_client

import celery_utils.processing.file_utils as file_utils
from celery_utils.processing.file_utils import read_pdf_to_plain_text


# ── fakes standing in for pdfminer's layout objects ─────────────────────────────────
# isinstance() checks in read_pdf_to_plain_text run against the names bound in
# file_utils (LTTextContainer, LTChar), so these fakes are monkeypatched in as those
# names rather than trying to construct real pdfminer objects.

class _FakeChar:
    def __init__(self, size=10.0):
        self.size = size


class _FakeTextLine:
    """One line of text: iterating it yields its chars, get_text() returns the string."""

    def __init__(self, text, font_size=10.0):
        self._text = text
        self._chars = [_FakeChar(font_size) for _ in text]

    def get_text(self):
        return self._text

    def __iter__(self):
        return iter(self._chars)


class _FakeTextContainer:
    """A block of text: iterating it yields its lines."""

    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)


@pytest.fixture(autouse=True)
def _patch_pdfminer_types(monkeypatch):
    # Both isinstance() checks in read_pdf_to_plain_text run against these two names.
    monkeypatch.setattr(file_utils, "LTTextContainer", _FakeTextContainer)
    monkeypatch.setattr(file_utils, "LTChar", _FakeChar)


def _page_layout(*line_texts):
    """Build one fake page_layout: a single text container holding one line per text."""
    return [_FakeTextContainer([_FakeTextLine(t) for t in line_texts])]


def _patch_page_count(monkeypatch, total_pages):
    monkeypatch.setattr(
        file_utils.PDFPage, "get_pages", staticmethod(lambda *a, **k: [object()] * total_pages)
    )


def _patch_extract_pages(monkeypatch, page_layouts):
    monkeypatch.setattr(
        file_utils, "extract_pages", lambda *a, **k: iter(page_layouts)
    )


class _FakePlumberPage:
    # width/height/images/chars are only exercised by the auto-shift signal
    # classification added alongside table extraction (see
    # test_pdf_auto_shift_modes.py) — defaulted here to "a normal, fully-text page with
    # no images" so these table-only tests keep testing table extraction in isolation.
    def __init__(self, tables, width=100, height=100, images=None, chars=None):
        self._tables = tables
        self.width = width
        self.height = height
        self.images = images if images is not None else []
        self.chars = chars if chars is not None else [object()] * 200

    def extract_tables(self):
        return self._tables


class _FakePlumberPDF:
    def __init__(self, pages):
        self.pages = pages


def _patch_pdfplumber_open(monkeypatch, pages_tables):
    """pages_tables: list of "tables" per page, each a list of tables (list of rows)."""

    @contextmanager
    def _fake_open(_stream):
        yield _FakePlumberPDF([_FakePlumberPage(t) for t in pages_tables])

    monkeypatch.setattr(file_utils.pdfplumber, "open", _fake_open)


def _bytes_data():
    # getvalue() must work regardless of read position — real content is irrelevant
    # since extract_pages/PDFPage.get_pages/pdfplumber.open are all monkeypatched.
    return BytesIO(b"%PDF-1.4 fake pdf bytes")


# ── tests ────────────────────────────────────────────────────────────────────────────

def test_page_with_table_embeds_markdown_table_after_its_text(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("Some heading", "Some body text")])
    table = [["Name", "Amount"], ["Alice", "10"], ["Bob", None]]
    _patch_pdfplumber_open(monkeypatch, pages_tables=[[table]])

    result = read_pdf_to_plain_text(_bytes_data())

    content = result["content"]
    assert "Some heading" in content
    assert "Some body text" in content
    # Text comes before the table (reading order preserved).
    assert content.index("Some body text") < content.index("| Name | Amount |")
    assert "| Name | Amount |" in content
    assert "| --- | --- |" in content
    assert "| Alice | 10 |" in content
    # None cell becomes an empty string, not the literal "None".
    assert "| Bob |  |" in content
    assert result["page_count"] == 1
    assert result["extra_info"]  # title still detected as before


def test_page_without_tables_leaves_content_byte_for_byte_identical(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("Plain paragraph, no tables here.")])
    _patch_pdfplumber_open(monkeypatch, pages_tables=[[]])  # no tables found

    result = read_pdf_to_plain_text(_bytes_data())

    assert result["content"] == "Plain paragraph, no tables here.\n"


def test_pdfplumber_open_failure_falls_back_to_pdfminer_only_content(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("Text that must survive.")])

    def _raise(_stream):
        raise RuntimeError("pdfplumber blew up")

    monkeypatch.setattr(file_utils.pdfplumber, "open", _raise)

    result = read_pdf_to_plain_text(_bytes_data())

    # No raise, and content matches exactly what pdfminer alone would have produced.
    assert result["content"] == "Text that must survive.\n"
    assert result["page_count"] == 1


def test_extract_tables_failure_falls_back_to_pdfminer_only_content(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("More text that must survive.")])

    class _BoomPage:
        def extract_tables(self):
            raise RuntimeError("table extraction blew up")

    @contextmanager
    def _fake_open(_stream):
        yield _FakePlumberPDF([_BoomPage()])

    monkeypatch.setattr(file_utils.pdfplumber, "open", _fake_open)

    result = read_pdf_to_plain_text(_bytes_data())

    assert result["content"] == "More text that must survive.\n"


def test_multi_page_tables_inserted_after_the_right_page(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=2)
    _patch_extract_pages(
        monkeypatch,
        [_page_layout("Page one text"), _page_layout("Page two text")],
    )
    table_page_2 = [["A", "B"], ["1", "2"]]
    _patch_pdfplumber_open(monkeypatch, pages_tables=[[], [table_page_2]])

    result = read_pdf_to_plain_text(_bytes_data())
    content = result["content"]

    assert content.index("Page one text") < content.index("Page two text")
    assert content.index("Page two text") < content.index("| A | B |")
