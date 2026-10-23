"""file_utils-level integration for the DOCX auto-shift added 2026-09-02 —
_read_docx_with_auto_shift, mirroring _read_pdf_with_auto_shift (see
test_pdf_auto_shift_modes.py) for the one other format LlamaParse accepts.

python-docx's Document and read_with_llama_parse are both mocked; no real .docx
file, no database, no network.
"""

import sys
import types
from io import BytesIO

import pytest

# Same import-time stub as test_pdf_auto_shift_modes.py — file_utils transitively
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


# ── fakes standing in for python-docx's Document ────────────────────────────────────

class _FakeCell:
    def __init__(self, text):
        self.text = text


class _FakeRow:
    def __init__(self, cell_texts):
        self.cells = [_FakeCell(t) for t in cell_texts]


class _FakeTable:
    def __init__(self, rows):
        self.rows = [_FakeRow(r) for r in rows]


class _FakeParagraph:
    def __init__(self, text):
        self.text = text


class _FakeDocument:
    def __init__(self, paragraphs=None, tables=None, inline_shape_count=0):
        self.paragraphs = [_FakeParagraph(t) for t in (paragraphs or [])]
        self.tables = [_FakeTable(rows) for rows in (tables or [])]
        self.inline_shapes = [object()] * inline_shape_count


def _patch_document(monkeypatch, fake_doc):
    monkeypatch.setattr(file_utils, "Document", lambda _bytes: fake_doc)


def _bytes_data():
    return BytesIO(b"PK\x03\x04 fake docx bytes")


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

def test_table_shifts_to_layout_aware(monkeypatch):
    _patch_document(monkeypatch, _FakeDocument(
        paragraphs=["Some ordinary text."],
        tables=[[["Name", "Amount"], ["Alice", "10"]]],
    ))
    fake_llamaparse = _FakeLlamaParseCall(content="LAYOUT AWARE OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_docx_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "doc.docx", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.LAYOUT_AWARE]
    assert result["content"] == "LAYOUT AWARE OUTPUT"  # free result discarded entirely
    assert result["mode_auto_shifted_from"] == ProcessingMode.TEXT_BASED.value


def test_embedded_image_shifts_to_visual(monkeypatch):
    _patch_document(monkeypatch, _FakeDocument(
        paragraphs=["A paragraph next to a picture."], inline_shape_count=1,
    ))
    fake_llamaparse = _FakeLlamaParseCall(content="VISUAL OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_docx_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "picture.docx", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.VISUAL]
    assert result["content"] == "VISUAL OUTPUT"


def test_image_and_table_together_shifts_to_mixed(monkeypatch):
    _patch_document(monkeypatch, _FakeDocument(
        paragraphs=["Text."], tables=[[["A", "B"], ["1", "2"]]], inline_shape_count=2,
    ))
    fake_llamaparse = _FakeLlamaParseCall(content="MIXED OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_docx_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "mixed.docx", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.MIXED]
    assert result["content"] == "MIXED OUTPUT"


def test_plain_docx_stays_text_based_and_never_calls_llamaparse(monkeypatch):
    _patch_document(monkeypatch, _FakeDocument(paragraphs=["A perfectly ordinary paragraph."]))
    fake_llamaparse = _FakeLlamaParseCall()
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_docx_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "plain.docx", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == []
    assert result["content"] == "A perfectly ordinary paragraph.\n"
    assert "mode_auto_shifted_from" not in result


def test_llamaparse_failure_during_shift_falls_back_to_free_result(monkeypatch):
    _patch_document(monkeypatch, _FakeDocument(
        paragraphs=["Some text."], tables=[[["A", "B"], ["1", "2"]]],
    ))
    fake_llamaparse = _FakeLlamaParseCall(raise_exc=RuntimeError("LlamaParse API down"))
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_docx_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "doc.docx", ProcessingMode.TEXT_BASED
    )

    assert fake_llamaparse.calls == [ProcessingMode.LAYOUT_AWARE]
    # fell back to the free local result — paragraphs plus the tab-joined table
    assert result["content"] == "Some text.\nA\tB\n1\t2\n\n"


def test_auto_mode_also_triggers_the_shift(monkeypatch):
    # AUTO is wired identically to TEXT_BASED — the point of this session's earlier
    # change was that the no-mode-supplied default keeps exactly this behaviour.
    _patch_document(monkeypatch, _FakeDocument(
        paragraphs=["Text."], inline_shape_count=1,
    ))
    fake_llamaparse = _FakeLlamaParseCall(content="VISUAL OUTPUT")
    monkeypatch.setattr(file_utils, "read_with_llama_parse", fake_llamaparse)

    result = file_utils._read_docx_with_auto_shift(
        _bytes_data(), _bytes_data().getvalue(), "picture.docx", ProcessingMode.AUTO
    )

    assert fake_llamaparse.calls == [ProcessingMode.VISUAL]
    assert result["mode_auto_shifted_from"] == ProcessingMode.AUTO.value
