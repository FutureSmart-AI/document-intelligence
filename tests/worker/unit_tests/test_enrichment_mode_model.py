"""Which model enriches a document, and how the processing mode changes that answer.

The rule under test, in one sentence: a document read in any mode other than
``text_based`` is enriched by the stronger model (``openai/gpt-5.6-luna``) unless its
knowledge base names an enrichment model of its own.

Why it matters: the mode is chosen because the document is hard to read (a scan, a page
of tables, a chart). What comes back out of that parse is correspondingly messier than
plain prose, and the cheap default model reads it noticeably worse. text_based documents
are unaffected — they keep whatever the platform default is.

These tests are pure: no database, no network. ``get_enrichment_config`` reaches for the
global default row through one function, which is monkeypatched here.
"""

import pytest

from celery_utils.enrichment.llm_factory import get_enrichment_config
from celery_utils.processing.processing_modes import (
    ProcessingMode,
    RICH_MODE_ENRICHMENT_MODEL,
    RICH_MODE_ENRICHMENT_PROVIDER,
    enrichment_override_for_mode,
)

GLOBAL_DEFAULT = {
    "enrichment_model": "gemini-3.5-flash-lite",
    "enrichment_provider": "google",
    "enrichment_temperature": 0.0,
}

RICH_MODES = [
    ProcessingMode.SCANNED,
    ProcessingMode.LAYOUT_AWARE,
    ProcessingMode.VISUAL,
    ProcessingMode.MIXED,
]


class _KnowledgeConfig:
    """The two fields get_enrichment_config actually reads off a config row."""

    def __init__(self, model=None, provider=None, temperature=None):
        self.enrichment_model = model
        self.enrichment_provider = provider
        self.enrichment_temperature = temperature


@pytest.fixture
def global_default(monkeypatch):
    """Pretend the enrichment_default_config table says Gemini, as production does."""
    import celery_utils.db.db_utils as db_utils

    monkeypatch.setattr(
        db_utils, "get_enrichment_default_config_sync", lambda: dict(GLOBAL_DEFAULT)
    )


# ── the mapping itself ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", RICH_MODES)
def test_every_non_text_mode_asks_for_the_stronger_model(mode):
    assert enrichment_override_for_mode(mode) == (
        RICH_MODE_ENRICHMENT_PROVIDER,
        RICH_MODE_ENRICHMENT_MODEL,
    )


def test_text_based_has_no_opinion():
    assert enrichment_override_for_mode(ProcessingMode.TEXT_BASED) is None


def test_no_mode_at_all_has_no_opinion():
    # An older queued message, or the plain-text ingest path. Must behave as before.
    assert enrichment_override_for_mode(None) is None


def test_the_wire_string_works_as_well_as_the_enum():
    # The mode crosses a queue as a plain string; not every call site converts it back.
    assert enrichment_override_for_mode("visual") == (
        RICH_MODE_ENRICHMENT_PROVIDER,
        RICH_MODE_ENRICHMENT_MODEL,
    )
    assert enrichment_override_for_mode("text_based") is None


def test_an_unknown_mode_changes_nothing():
    # A version mismatch between the two services is not a reason to guess at a model.
    assert enrichment_override_for_mode("holographic") is None


# ── where it sits in the fallback chain ────────────────────────────────────────

@pytest.mark.parametrize("mode", RICH_MODES)
def test_mode_replaces_the_global_default(global_default, mode):
    provider, model, temperature = get_enrichment_config(None, processing_mode=mode)
    assert (provider, model) == (RICH_MODE_ENRICHMENT_PROVIDER, RICH_MODE_ENRICHMENT_MODEL)
    assert temperature == 0.0


def test_text_based_keeps_the_global_default(global_default):
    provider, model, _ = get_enrichment_config(
        None, processing_mode=ProcessingMode.TEXT_BASED
    )
    assert (provider, model) == ("google", "gemini-3.5-flash-lite")


def test_omitting_the_mode_keeps_the_global_default(global_default):
    # Every caller that predates this parameter lands here.
    provider, model, _ = get_enrichment_config(None)
    assert (provider, model) == ("google", "gemini-3.5-flash-lite")


@pytest.mark.parametrize("mode", RICH_MODES)
def test_a_knowledge_bases_own_model_outranks_the_mode(global_default, mode):
    config = _KnowledgeConfig(model="gpt-5.4-mini", provider="openai", temperature=0.2)
    provider, model, temperature = get_enrichment_config(config, processing_mode=mode)
    assert (provider, model, temperature) == ("openai", "gpt-5.4-mini", 0.2)


def test_a_config_row_with_no_model_does_not_block_the_mode(global_default):
    # A row exists (most do) but nobody ever set an enrichment model on it — that is the
    # platform default in disguise, so the mode still wins.
    config = _KnowledgeConfig(model=None)
    provider, model, _ = get_enrichment_config(
        config, processing_mode=ProcessingMode.SCANNED
    )
    assert (provider, model) == (RICH_MODE_ENRICHMENT_PROVIDER, RICH_MODE_ENRICHMENT_MODEL)


# ── the mode actually reaching the strategy ────────────────────────────────────
#
# The chain is: process_and_index_file resolves the mode -> enrich_document -> the chosen
# strategy -> get_enrichment_config. Every link has to pass it on; a missing keyword in
# the middle is silent (the default None just means "no override"), so it gets a test.

class _ConfigRow:
    """A knowledge_config row shaped for the dispatcher's strategy selection."""

    def __init__(self, doc=True, chunk=False, deterministic=False):
        self.document_level_metadata = doc
        self.chunk_level_metadata = chunk
        self.use_deterministic_chunking = deterministic
        self.enrichment_enabled = True
        self.enrichment_provider = None
        self.enrichment_model = None
        self.enrichment_temperature = None


@pytest.mark.parametrize(
    "row, strategy_name",
    [
        (_ConfigRow(doc=True, chunk=False), "enrich_document_strategy_6"),
        (_ConfigRow(doc=True, chunk=True, deterministic=False), "enrich_document_strategy_1"),
    ],
)
def test_dispatcher_hands_the_mode_to_the_strategy(monkeypatch, row, strategy_name):
    from celery_utils.enrichment import dispatcher
    from celery_utils.enrichment import enrichment_strategies

    monkeypatch.setattr(dispatcher, "get_knowledge_config_sync", lambda knowledge_id: row)

    seen = {}

    def _fake_strategy(*args, **kwargs):
        seen.update(kwargs)
        return {"metadata": {}}

    monkeypatch.setattr(enrichment_strategies, strategy_name, _fake_strategy)

    dispatcher.enrich_document(
        "some text", "kb-1", "user-1", "obj-1",
        processing_mode=ProcessingMode.VISUAL,
    )

    assert seen["processing_mode"] is ProcessingMode.VISUAL
