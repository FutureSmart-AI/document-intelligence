"""LlamaParse rejected the old parse_mode value for VISUAL (parse_page_with_lvm,
HTTP 410 "no longer supported, use tiers instead"), confirmed live 2026-08-26 against a
real document through the deployed worker. This pins the fix — VISUAL moved to the new
tier system — and that the other three modes, which still work on the old parameter,
were left untouched.
"""

from celery_utils.processing.processing_modes import (
    ProcessingMode,
    estimated_credits,
    llamaparse_settings,
    parse_mode_for,
)


def test_text_based_never_reaches_llamaparse():
    assert llamaparse_settings(ProcessingMode.TEXT_BASED) is None
    assert parse_mode_for(ProcessingMode.TEXT_BASED) is None


def test_visual_now_uses_the_tier_system_not_the_retired_parse_mode():
    settings = llamaparse_settings(ProcessingMode.VISUAL)
    assert settings == {"tier": "agentic", "version": "latest"}
    assert "parse_mode" not in settings
    assert "model" not in settings  # a tier picks its own model; there is no knob here


def test_scanned_and_layout_aware_and_mixed_still_use_the_working_parse_mode():
    # These three were confirmed live (2026-08-26) to still work on the old parameter —
    # only VISUAL's specific value was retired. Nothing about them should have moved.
    assert llamaparse_settings(ProcessingMode.SCANNED)["parse_mode"] == "parse_page_with_llm"
    assert llamaparse_settings(ProcessingMode.LAYOUT_AWARE)["parse_mode"] == "parse_page_with_llm"
    assert llamaparse_settings(ProcessingMode.MIXED)["parse_mode"] == "parse_page_with_agent"


def test_every_llamaparse_mode_has_a_billing_key():
    # A mode whose settings changed family (tier vs parse_mode) but whose billing key
    # was forgotten would silently price and log as free — see estimated_credits.
    for mode in (ProcessingMode.SCANNED, ProcessingMode.LAYOUT_AWARE,
                 ProcessingMode.VISUAL, ProcessingMode.MIXED):
        assert parse_mode_for(mode), f"{mode} has no billing key"


def test_visual_prices_as_the_agentic_tier_not_free():
    # Before the fix, reading straight off settings.get("parse_mode") would have found
    # nothing for VISUAL and silently estimated 0 credits.
    key = parse_mode_for(ProcessingMode.VISUAL)
    assert estimated_credits(key, page_count=4) == 40  # 10 credits/page x 4 pages


def test_an_unrecognised_billing_key_still_prices_as_zero_not_a_guess():
    assert estimated_credits("some-future-parameter-name", page_count=5) == 0
