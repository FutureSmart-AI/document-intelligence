"""Pure unit tests for processing_modes.classify_pdf_page / classify_pdf_target_mode —
the per-page structural classification that replaced the old whole-document character
average (looks_like_scanned_pdf) for the PDF auto-shift decision. See
test_pdf_auto_shift_modes.py for the file_utils-level integration (pdfplumber/pdfminer
mocked, read_with_llama_parse call verified).
"""

from celery_utils.processing.processing_modes import (
    ProcessingMode,
    SCANNED_IMAGE_COVERAGE_THRESHOLD,
    SCANNED_PDF_CHARS_PER_PAGE,
    VISUAL_IMAGE_COVERAGE_THRESHOLD,
    classify_pdf_page,
    classify_pdf_target_mode,
)


def _page(char_count=200, image_coverage=0.0, table_count=0):
    return {"char_count": char_count, "image_coverage": image_coverage, "table_count": table_count}


# ── classify_pdf_page ────────────────────────────────────────────────────────────────

def test_table_present_wins_over_everything_else():
    page = _page(char_count=1, image_coverage=0.95, table_count=1)  # would also look scanned
    assert classify_pdf_page(page) == ProcessingMode.LAYOUT_AWARE


def test_sparse_text_with_dominant_image_is_scanned():
    page = _page(char_count=SCANNED_PDF_CHARS_PER_PAGE - 1, image_coverage=SCANNED_IMAGE_COVERAGE_THRESHOLD)
    assert classify_pdf_page(page) == ProcessingMode.SCANNED


def test_sparse_text_without_a_dominant_image_is_not_scanned():
    # The false-positive this generalization fixes: sparse text alone isn't enough.
    page = _page(char_count=SCANNED_PDF_CHARS_PER_PAGE - 1, image_coverage=0.0)
    assert classify_pdf_page(page) is None


def test_moderate_image_coverage_with_real_text_is_visual():
    page = _page(char_count=SCANNED_PDF_CHARS_PER_PAGE, image_coverage=VISUAL_IMAGE_COVERAGE_THRESHOLD)
    assert classify_pdf_page(page) == ProcessingMode.VISUAL


def test_image_coverage_below_visual_threshold_triggers_nothing():
    page = _page(char_count=500, image_coverage=VISUAL_IMAGE_COVERAGE_THRESHOLD - 0.01)
    assert classify_pdf_page(page) is None


def test_image_coverage_at_scanned_threshold_with_real_text_is_scanned_not_visual():
    # At/above the scanned threshold falls out of the visual band even when the text
    # itself isn't sparse — a full-bleed image with a text sidebar reads as "mostly a
    # scan" once coverage crosses this line, per the VISUAL band being
    # [VISUAL_THRESHOLD, SCANNED_THRESHOLD).
    page = _page(char_count=500, image_coverage=SCANNED_IMAGE_COVERAGE_THRESHOLD)
    assert classify_pdf_page(page) is None  # not sparse -> not SCANNED; >= threshold -> not VISUAL either


def test_ordinary_text_page_triggers_nothing():
    page = _page(char_count=500, image_coverage=0.0, table_count=0)
    assert classify_pdf_page(page) is None


# ── classify_pdf_target_mode ─────────────────────────────────────────────────────────

def test_no_pages_trigger_anything_stays_text_based():
    pages = [_page(), _page(), _page()]
    mode, reason = classify_pdf_target_mode(pages)
    assert mode == ProcessingMode.TEXT_BASED
    assert "no page" in reason


def test_single_condition_on_multiple_pages_stays_that_mode():
    pages = [
        _page(table_count=1),
        _page(),  # ordinary page in between
        _page(table_count=2),
    ]
    mode, reason = classify_pdf_target_mode(pages)
    assert mode == ProcessingMode.LAYOUT_AWARE
    assert "layout_aware" in reason


def test_two_distinct_conditions_across_pages_becomes_mixed():
    pages = [
        _page(table_count=1),
        _page(char_count=5, image_coverage=0.9),  # scanned
    ]
    mode, reason = classify_pdf_target_mode(pages)
    assert mode == ProcessingMode.MIXED
    assert "layout_aware" in reason and "scanned" in reason


def test_empty_page_list_stays_text_based():
    mode, _reason = classify_pdf_target_mode([])
    assert mode == ProcessingMode.TEXT_BASED
