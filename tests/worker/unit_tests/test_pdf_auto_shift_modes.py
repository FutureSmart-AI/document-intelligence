"""Generalizes the PDF auto-shift from "scanned only, decided on a whole-document
character average" to "any of scanned/layout_aware/visual/mixed, decided per page".

The free pdfminer+pdfplumber read still runs first (it's free); the pdfplumber pass
that already runs for table detection is reused to compute per-page structural
signals (char_count, image_coverage, table_count — see
processing_modes.classify_pdf_target_mode). If those signals say the document needs
more than plain text, the free result is discarded entirely and the whole document is
re-read via LlamaParse in the detected mode. No trigger firing on any page means the
free result is returned untouched and LlamaParse is never called.

These tests are pure: no real PDF file, no database, no network. pdfminer's
extract_pages/PDFPage.get_pages, pdfplumber.open, and read_with_llama_parse are all
monkeypatched.
"""

import sys
import types
from contextlib import contextmanager
from io import BytesIO

import pytest

# Same import-time stub as test_pdf_table_extraction.py — file_utils transitively
# imports celery_utils.indexing.indexer, which pulls in heavy, unrelated packages
# (qdrant-client, sentence-transformers, ...) that nothing here exercises.
if "celery_utils.indexing.client" not in sys.modules:
    _fake_indexer_client = types.ModuleType("celery_utils.indexing.client")
    _fake_indexer_client.MultiQdrantDocumentIndexer = object
    _fake_indexer_client.MultiQdrantChunkIndexer = object
    _fake_indexer_client.EMBEDDING_MODELS = {}
    sys.modules["celery_utils.indexing.client"] = _fake_indexer_client

import celery_utils.processing.file_utils as file_utils
from celery_utils.processing.processing_modes import ProcessingMode


# ── fakes standing in for pdfminer's layout objects (same shapes as
#    test_pdf_table_extraction.py — isinstance() checks run against names bound in
#    file_utils, so these are monkeypatched in as those names) ──────────────────────

class _FakeChar:
    def __init__(self, size=10.0):
        self.size = size


class _FakeTextLine:
    def __init__(self, text, font_size=10.0):
        self._text = text
        self._chars = [_FakeChar(font_size) for _ in text]

    def get_text(self):
        return self._text

    def __iter__(self):
        return iter(self._chars)


class _FakeTextContainer:
    def __init__(self, lines):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)


@pytest.fixture(autouse=True)
def _patch_pdfminer_types(monkeypatch):
    monkeypatch.setattr(file_utils, "LTTextContainer", _FakeTextContainer)
    monkeypatch.setattr(file_utils, "LTChar", _FakeChar)


def _page_layout(*line_texts):
    return [_FakeTextContainer([_FakeTextLine(t) for t in line_texts])]


def _patch_page_count(monkeypatch, total_pages):
    monkeypatch.setattr(
        file_utils.PDFPage, "get_pages", staticmethod(lambda *a, **k: [object()] * total_pages)
    )


def _patch_extract_pages(monkeypatch, page_layouts):
    monkeypatch.setattr(file_utils, "extract_pages", lambda *a, **k: iter(page_layouts))


# ── fake standing in for a pdfplumber page, carrying the new structural signals ─────

class _FakePlumberPage:
    def __init__(self, tables=None, char_count=200, image_coverage=0.0,
                width=100.0, height=100.0):
        self._tables = tables or []
        self.width = width
        self.height = height
        self.chars = [object()] * char_count
        # A single image whose area is `image_coverage` fraction of the page area —
        # matches how _page_image_coverage computes it (sum of img width*height over
        # page width*height), so tests can specify coverage directly.
        image_area = image_coverage * width * height
        self.images = [{"width": image_area / height, "height": height}] if image_area else []

    def extract_tables(self):
        return self._tables


class _FakePlumberPDF:
    def __init__(self, pages):
        self.pages = pages


def _patch_pdfplumber_open(monkeypatch, pages):
    @contextmanager
    def _fake_open(_stream):
        yield _FakePlumberPDF(pages)

    monkeypatch.setattr(file_utils.pdfplumber, "open", _fake_open)


def _bytes_data():
    return BytesIO(b"%PDF-1.4 fake pdf bytes")


class _FakeLlamaParseCall:
    """Records the mode(s) it was called with and returns a canned dict, standing in
    for read_with_llama_parse — a real call would hit the network."""

    def __init__(self, content="LLAMAPARSE OUTPUT", raise_exc=None):
        self.calls = []
        self._content = content
        self._raise_exc = raise_exc

    def __call__(self, bytes_io, filename, result_type, owner_id=None, mode=ProcessingMode.LAYOUT_AWARE):
        self.calls.append(mode)
        if self._raise_exc:
            raise self._raise_exc
        return {
            "content": self._content, "extra_info": "", "llama_parse_page_count": 1,
            "key_type": "shared", "effective_mode": mode.value, "parse_mode": "parse_page_with_llm",
            "llama_parse_credits": 3,
        }


# ── tests ────────────────────────────────────────────────────────────────────────────

def test_table_page_shifts_to_layout_aware(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("Some ordinary text with a table below.")])
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(tables=[[["Name", "Amount"], ["Alice", "10"]]], char_count=200, image_coverage=0.0),
    ])
    fake_llamaparse = _FakeLlamaParseCall(content="LAYOUT AWARE OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "doc.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.LAYOUT_AWARE]
    assert result["content"] == "LAYOUT AWARE OUTPUT"  # free result discarded entirely
    assert result["mode_auto_shifted_from"] == ProcessingMode.TEXT_BASED.value


def test_low_chars_and_high_image_coverage_shifts_to_scanned(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("x")])  # a stray char, like a watermark
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(char_count=5, image_coverage=0.9),  # sparse text, page is basically one image
    ])
    fake_llamaparse = _FakeLlamaParseCall(content="OCR OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "scan.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.SCANNED]
    assert result["content"] == "OCR OUTPUT"
    assert result["mode_auto_shifted_from"] == ProcessingMode.TEXT_BASED.value


def test_sparse_text_without_dominant_image_does_not_trigger_scanned(monkeypatch):
    """The false-positive this generalization fixes: a page with genuinely sparse text
    (a caption, a title slide) but no big image must NOT be classified scanned."""
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("Short caption.")])
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(char_count=5, image_coverage=0.0),  # sparse, but no image at all
    ])
    fake_llamaparse = _FakeLlamaParseCall()
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "sparse.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == []  # never called — stayed TEXT_BASED
    assert result["content"] == "Short caption.\n"  # free result, unchanged


def test_moderate_image_coverage_with_real_text_shifts_to_visual(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("A paragraph explaining the chart below in detail.")])
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(char_count=200, image_coverage=0.4),  # real text + a real chart
    ])
    fake_llamaparse = _FakeLlamaParseCall(content="VISUAL OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "chart.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.VISUAL]
    assert result["content"] == "VISUAL OUTPUT"


def test_two_pages_triggering_different_conditions_shifts_to_mixed(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=2)
    _patch_extract_pages(monkeypatch, [
        _page_layout("Page one has a table."),
        _page_layout("x"),
    ])
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(tables=[[["A", "B"], ["1", "2"]]], char_count=200, image_coverage=0.0),
        _FakePlumberPage(char_count=5, image_coverage=0.9),  # looks scanned
    ])
    fake_llamaparse = _FakeLlamaParseCall(content="MIXED OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "mixed.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.MIXED]
    assert result["content"] == "MIXED OUTPUT"


def test_no_triggers_stays_text_based_and_never_calls_llamaparse(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("A perfectly ordinary text page.")])
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(char_count=200, image_coverage=0.0),
    ])
    fake_llamaparse = _FakeLlamaParseCall()
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "plain.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == []
    assert result["content"] == "A perfectly ordinary text page.\n"
    assert "mode_auto_shifted_from" not in result


def test_llamaparse_failure_during_shift_falls_back_to_free_result(monkeypatch):
    _patch_page_count(monkeypatch, total_pages=1)
    _patch_extract_pages(monkeypatch, [_page_layout("Some text with a table below.")])
    _patch_pdfplumber_open(monkeypatch, [
        _FakePlumberPage(tables=[[["A", "B"], ["1", "2"]]], char_count=200, image_coverage=0.0),
    ])
    fake_llamaparse = _FakeLlamaParseCall(raise_exc=RuntimeError("LlamaParse API down"))
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    # Must not raise — falls back to the free-path result.
    result = file_utils._read_pdf_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "doc.pdf", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.LAYOUT_AWARE]
    assert "Some text with a table below." in result["content"]
    assert "mode_auto_shifted_from" not in result  # never got set — this is the raw free result
