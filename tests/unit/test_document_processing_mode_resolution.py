"""Unit tests for resolve_document_processing_mode / derive_use_llama_parse
(utils/files/processing_modes.py).

Covers the 2026-09-02 change: when the UI sends neither `document_processing_mode`
nor the legacy `use_llama_parse`, the resolved mode is now `AUTO` ("auto"), not
`TEXT_BASED` ("text_based"). Behaviour must stay identical to the old default —
`AUTO` is wired the same way `TEXT_BASED` was everywhere downstream — only the wire
value changes, so a stored `text_based` can be trusted to mean an explicit choice.
"""

from utils.files.processing_modes import (
    derive_use_llama_parse,
    resolve_document_processing_mode,
)
from utils.models.enums import DocumentProcessingMode


def test_nothing_supplied_resolves_to_auto():
    assert resolve_document_processing_mode(None, None) is DocumentProcessingMode.AUTO


def test_explicit_mode_wins_over_nothing():
    assert resolve_document_processing_mode(
        DocumentProcessingMode.TEXT_BASED, None
    ) is DocumentProcessingMode.TEXT_BASED


def test_explicit_mode_wins_over_legacy_boolean():
    assert resolve_document_processing_mode(
        DocumentProcessingMode.SCANNED, True
    ) is DocumentProcessingMode.SCANNED


def test_legacy_true_maps_to_layout_aware_when_no_mode_sent():
    assert resolve_document_processing_mode(
        None, True
    ) is DocumentProcessingMode.LAYOUT_AWARE


def test_legacy_false_resolves_to_auto_like_nothing_sent():
    assert resolve_document_processing_mode(None, False) is DocumentProcessingMode.AUTO


def test_derive_use_llama_parse_false_for_auto_and_text_based():
    assert derive_use_llama_parse(DocumentProcessingMode.AUTO) is False
    assert derive_use_llama_parse(DocumentProcessingMode.TEXT_BASED) is False


def test_derive_use_llama_parse_true_for_every_other_mode():
    for mode in (
        DocumentProcessingMode.SCANNED,
        DocumentProcessingMode.LAYOUT_AWARE,
        DocumentProcessingMode.VISUAL,
        DocumentProcessingMode.MIXED,
    ):
        assert derive_use_llama_parse(mode) is True
