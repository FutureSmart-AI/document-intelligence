"""Pure unit tests for processing_modes.classify_docx_target_mode — the DOCX
auto-shift decision added 2026-09-02, mirroring classify_pdf_target_mode at
document (not per-page) granularity, since python-docx only gives a
whole-document image/table count. See test_docx_auto_shift_modes.py for the
file_utils-level integration (python-docx and read_with_llama_parse mocked).
"""

from celery_utils.processing.processing_modes import (
    ProcessingMode,
    classify_docx_target_mode,
)


def _signals(image_count=0, table_count=0):
    return {"image_count": image_count, "table_count": table_count}


def test_no_image_or_table_stays_text_based():
    mode, reason = classify_docx_target_mode(_signals())
    assert mode == ProcessingMode.TEXT_BASED
    assert "no image or table" in reason


def test_table_only_triggers_layout_aware():
    mode, reason = classify_docx_target_mode(_signals(table_count=2))
    assert mode == ProcessingMode.LAYOUT_AWARE
    assert "2 table" in reason


def test_image_only_triggers_visual():
    mode, reason = classify_docx_target_mode(_signals(image_count=1))
    assert mode == ProcessingMode.VISUAL
    assert "1 image" in reason


def test_image_and_table_together_becomes_mixed():
    mode, reason = classify_docx_target_mode(_signals(image_count=1, table_count=1))
    assert mode == ProcessingMode.MIXED
    assert "layout_aware" in reason and "visual" in reason
