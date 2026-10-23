"""2026-09-02: the no-mode-supplied fallback in resolve_mode() changed from
ProcessingMode.TEXT_BASED to a new ProcessingMode.AUTO ("auto"), mirroring the same
change in fs-agent-knowledge's resolve_document_processing_mode. The point of AUTO is
to say "nobody chose a mode" without borrowing TEXT_BASED's value, which used to mean
both "confirmed plain text" and "no opinion" at once.

These tests pin two things: resolve_mode's fallback behaviour, and that AUTO is wired
identically to TEXT_BASED everywhere else a mode gets looked up — the whole point of
this change is that behaviour for an unspecified mode does not move.
"""

from celery_utils.processing.processing_modes import (
    ProcessingMode,
    enrichment_override_for_mode,
    llamaparse_settings,
    resolve_html_scraper,
    resolve_mode,
)


def test_nothing_supplied_resolves_to_auto():
    assert resolve_mode(None, None) is ProcessingMode.AUTO
    assert resolve_mode("", None) is ProcessingMode.AUTO
    assert resolve_mode(None, False) is ProcessingMode.AUTO


def test_explicit_mode_string_wins():
    assert resolve_mode("scanned", None) is ProcessingMode.SCANNED


def test_legacy_true_still_maps_to_layout_aware():
    assert resolve_mode(None, True) is ProcessingMode.LAYOUT_AWARE


def test_unknown_mode_falls_through_to_boolean_then_auto():
    assert resolve_mode("some-future-mode", True) is ProcessingMode.LAYOUT_AWARE
    assert resolve_mode("some-future-mode", None) is ProcessingMode.AUTO


def test_auto_never_reaches_llamaparse_like_text_based():
    assert llamaparse_settings(ProcessingMode.AUTO) is None
    assert llamaparse_settings(ProcessingMode.AUTO) == llamaparse_settings(ProcessingMode.TEXT_BASED)


def test_auto_uses_the_same_html_scraper_as_text_based():
    assert resolve_html_scraper(ProcessingMode.AUTO, "scrape") == "newspaper"
    assert resolve_html_scraper(ProcessingMode.AUTO, "scrape") == \
        resolve_html_scraper(ProcessingMode.TEXT_BASED, "scrape")


def test_auto_keeps_the_cheap_enrichment_model_like_text_based():
    assert enrichment_override_for_mode(ProcessingMode.AUTO) is None
    assert enrichment_override_for_mode("auto") is None
