"""Document processing modes — what the user says about their documents, and the
LlamaParse configuration each of those statements maps to.

The idea behind this module is worth stating plainly, because it is the whole reason it
exists as a separate file.

The UI never asks the user to pick a parser. It asks what their *documents are like*:
"is this a normal text document, a scan, something where the layout carries the meaning,
something where the picture carries the meaning?" That is a question a person can answer
about their own files without knowing anything about PDF internals. Which parser to run,
and with which flags, is then our problem — and the translation from "what the content is"
to "which machinery handles it" lives here, in exactly one place.

Keeping it in one place matters more than it might look. The alternative is that the
mapping gets inlined at each call site, and then the pricing table drifts away from the
settings table, and a mode gets added in one place but not the other. One dict, one
lookup, one file to change.

Terminology, since the two words look interchangeable but are not:
  * a **processing mode** is the user's answer ("scanned") — our vocabulary, stable,
    safe to store and show in a UI.
  * a **parse mode** is LlamaParse's parameter (``parse_page_with_llm``) — their
    vocabulary, which can change when they release a new API.
Mapping one to the other is this module's only job.

That last sentence held up sooner than expected: LlamaParse v2 (2026-08) retired the
``parse_page_with_lvm`` value with an HTTP 410 — "no longer supported, use tiers
instead" — in favour of a flat ``tier`` + ``version`` ladder (``fast`` <
``cost_effective`` < ``agentic`` < ``agentic_plus``) that is not a drop-in replacement:
a tier is a whole pipeline, not a parameter layered onto one. ``scanned``,
``layout_aware`` and ``mixed`` still resolve to a working ``parse_mode`` value
(confirmed live, 2026-08-26) — only ``visual`` needed to move. So this module now speaks
both parameter families at once, one mode at a time; ``_BILLING_KEY`` below is what lets
logging and cost estimation stay indifferent to which family a given mode happens to use.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TypedDict


class ProcessingMode(str, Enum):
    """What the document's content is like. Mirrors ``DocumentProcessingMode`` in
    fs-agent-knowledge — the two services exchange these as plain strings over SQS, so
    the values (not the member names) are the contract between them.
    """

    AUTO = "auto"                  # caller sent no mode at all — let the worker detect it
    TEXT_BASED = "text_based"      # the text is already in the file; just read it
    SCANNED = "scanned"            # pictures of text — needs OCR
    LAYOUT_AWARE = "layout_aware"  # tables/columns: position carries meaning
    VISUAL = "visual"              # charts/diagrams: the image carries meaning
    MIXED = "mixed"                # more than one of the above in one collection


# The model the agent mode pins. Pinned rather than left to LlamaParse's own default
# because credit cost varies by model (see _CREDITS_PER_PAGE) — an unpinned model means
# an unpredictable bill. VISUAL no longer has an equivalent: a tier is a self-contained
# pipeline with its own model selection built in, not a parameter you layer a model onto
# (confirmed against LlamaParse's current tier docs, 2026-08-26) — there is no
# vendor_multimodal_model_name-equivalent knob for a tier.
AGENT_MODEL = "gemini-2.5-flash"

# Mode -> the LlamaParse keyword arguments that serve it.
#
# A mode absent from this dict (AUTO, TEXT_BASED) is handled by our own local readers
# and never reaches LlamaParse at all — that is the "free" path, and it is the default.
_LLAMAPARSE_SETTINGS: Dict[ProcessingMode, Dict[str, Any]] = {
    # Pictures of text. high_res_ocr is the flag that actually does the reading.
    ProcessingMode.SCANNED: {
        "parse_mode": "parse_page_with_llm",
        "high_res_ocr": True,
    },
    # Tables and columns. The table flags below are what keep a table from collapsing
    # into an unreadable run of words once it becomes text.
    ProcessingMode.LAYOUT_AWARE: {
        "parse_mode": "parse_page_with_llm",
        "high_res_ocr": True,
        "adaptive_long_table": True,
        "outlined_table_extraction": True,
        "output_tables_as_HTML": True,
    },
    # Charts and diagrams. LlamaParse's old vision-only parse_mode (parse_page_with_lvm)
    # is the value it retired (see the module docstring) — "agentic" is the cheapest
    # tier its own docs credit with real chart reading rather than "basic" charts
    # (cost_effective, one tier down). No `model` kwarg: a tier picks its own model.
    ProcessingMode.VISUAL: {
        "tier": "agentic",
        "version": "latest",
    },
    # A bit of everything. The agent mode is the broadest single setting available, so
    # it is the safest choice when we cannot promise what the next page holds.
    ProcessingMode.MIXED: {
        "parse_mode": "parse_page_with_agent",
        "model": AGENT_MODEL,
        "high_res_ocr": True,
        "adaptive_long_table": True,
        "outlined_table_extraction": True,
        "output_tables_as_HTML": True,
    },
}

# Which billing/logging key a mode's own LlamaParse settings actually correspond to —
# kept separate from _LLAMAPARSE_SETTINGS above because that dict holds the literal SDK
# kwargs, and those kwargs now come from two different parameter families (a legacy
# `parse_mode` string for scanned/layout_aware/mixed, a `tier` for visual). Everything
# downstream that logs or prices a parse — read_with_llama_parse, usage-tracking
# metadata — should key off this, not off `settings.get("parse_mode")` directly, or a
# tier-based mode silently prices and logs as if it cost nothing.
_BILLING_KEY: Dict[ProcessingMode, str] = {
    ProcessingMode.SCANNED: "parse_page_with_llm",
    ProcessingMode.LAYOUT_AWARE: "parse_page_with_llm",
    ProcessingMode.VISUAL: "tier:agentic",
    ProcessingMode.MIXED: "parse_page_with_agent",
}

# LlamaParse's published credit cost per page, per billing key (see _BILLING_KEY above).
#
# Treat these as an ESTIMATE for our own analytics, never as the billed truth — the
# authority is the invoice. Source, re-check when bumping llama-parse or when a mode's
# tier changes: https://developers.llamaindex.ai/llamaparse/general/pricing/
#
# The parse_page_* entries are what's still actually in use (scanned/layout_aware/mixed).
# The tier:* entries cover the full v2 ladder, not just the one this module currently
# assigns to a mode (visual → agentic) — so that moving another mode onto it later, or
# reasoning about the alternative not taken, doesn't require re-deriving these numbers.
_CREDITS_PER_PAGE: Dict[str, int] = {
    "parse_page_without_llm": 1,
    "parse_page_with_llm": 3,
    "parse_page_with_agent": 10,
    "tier:fast": 1,
    "tier:cost_effective": 3,
    "tier:agentic": 10,
    "tier:agentic_plus": 45,
}

# Only real document formats have a scanned/layout/visual dimension. A .txt or .md file
# is already text and a sound file is already handled by transcription, so a mode never
# changes how those are read — sending them through LlamaParse would just cost money for
# a worse result.
LLAMAPARSE_ELIGIBLE_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})

# Below this many characters per page, a PDF is treated as image-only ("scanned").
#
# The reasoning: a PDF that really contains text yields hundreds of characters per page.
# A scan yields almost nothing — its pages are pictures, and the only text a text
# extractor finds is the odd stray character from a header or a watermark. Dividing by
# the page count rather than using one flat number keeps a long, genuinely sparse
# document (a photo book with captions, say) from being misread as a scan.
SCANNED_PDF_CHARS_PER_PAGE = 50


def resolve_mode(document_processing_mode: Optional[str], use_llama_parse: Optional[bool]) -> ProcessingMode:
    """Work out which mode to actually run.

    Handles three generations of caller at once, newest first:

    1. a caller that sends ``document_processing_mode`` — believe it;
    2. an older caller that only knows the ``use_llama_parse`` boolean — translate it;
    3. a caller that sends neither — fall back to AUTO, which behaves exactly like the
       historical default (read locally, auto-shift if the content needs more).

    ``use_llama_parse=True`` becomes LAYOUT_AWARE because that is what the old code
    actually did: it asked LlamaParse for markdown with no OCR or vision flags set.
    Mapping it to SCANNED or VISUAL instead would quietly raise the bill for every
    caller that has not been updated yet, which is not a thing a compatibility shim
    should ever do.
    """
    if document_processing_mode:
        try:
            return ProcessingMode(document_processing_mode)
        except ValueError:
            # An unknown mode is a producer/consumer version mismatch, not a reason to
            # drop the document. Fall through to the boolean, which is always safe.
            pass
    if use_llama_parse:
        return ProcessingMode.LAYOUT_AWARE
    return ProcessingMode.AUTO


def llamaparse_settings(mode: ProcessingMode) -> Optional[Dict[str, Any]]:
    """The LlamaParse kwargs for a mode, or None if the mode uses our local readers.

    A None return is the signal for "this one is free" — callers branch on it rather
    than comparing against TEXT_BASED, so adding another local-only mode later does not
    require hunting down every comparison.
    """
    settings = _LLAMAPARSE_SETTINGS.get(mode)
    return dict(settings) if settings else None


def parse_mode_for(mode: ProcessingMode) -> Optional[str]:
    """The billing/logging key a mode resolves to, or None for local (see _BILLING_KEY).

    Despite the name, this is no longer always a literal LlamaParse ``parse_mode``
    string — ``visual`` resolves to ``"tier:agentic"``, which is not something the SDK
    accepts as a kwarg value, only something this module and ``estimated_credits`` below
    understand. Named for what every existing caller already expects to get back.
    """
    return _BILLING_KEY.get(mode)


def estimated_credits(billing_key: Optional[str], page_count: int) -> int:
    """Estimated LlamaParse credits for a parse of ``page_count`` pages.

    ``billing_key`` is a value from ``_BILLING_KEY`` / ``parse_mode_for`` — pass that,
    not raw SDK kwargs, or a tier-based mode prices as 0 rather than what it costs.
    Zero for the local path, which is the point of having a local path at all. An
    unrecognised key also scores zero rather than guessing — an obviously wrong zero in
    a dashboard is easier to spot and fix than a plausible wrong number.
    """
    if not billing_key or page_count <= 0:
        return 0
    return _CREDITS_PER_PAGE.get(billing_key, 0) * page_count


def looks_like_scanned_pdf(text: str, page_count: int) -> bool:
    """Does this PDF look like it is made of images rather than text?

    Called after a local text extraction has already run and come back nearly empty.
    Cheap to ask, because the extraction it judges was free.

    Superseded for the auto-shift decision by ``classify_pdf_target_mode`` below (a
    per-page structural pass instead of a whole-document character average), but kept
    as-is: it is a small, independent, still-correct check and other callers may still
    reasonably want "is this whole document sparse" without a page-level breakdown.
    """
    char_count = len((text or "").strip())
    if page_count <= 0:
        # No page count to normalise against — fall back to "is there anything at all".
        return char_count < 10
    return (char_count / page_count) < SCANNED_PDF_CHARS_PER_PAGE


# ---------------------------------------------------------------------------
# Per-page PDF structural classification -> target processing mode
# ---------------------------------------------------------------------------
#
# The auto-shift used to judge a whole document by one average (looks_like_scanned_pdf
# above): total characters over total pages. That average hides two failure modes at
# once — a document that is mostly fine but has one page of real trouble reads as
# "fine" when averaged, and a document that is genuinely sparse-but-real (a slide deck
# with terse bullet text, a photo book with short captions) can read as "scanned" even
# though no page is actually a picture of text. Judging each page on its own signals,
# then aggregating the *decisions* rather than the raw numbers, fixes both.

# A page whose extractable text is below SCANNED_PDF_CHARS_PER_PAGE chars is only
# treated as "scanned" (a picture of text) if it is ALSO dominated by one big image —
# otherwise it is just a sparse text page (bullets, a title slide, a caption), which is
# the false positive flagged during this session's review. 0.6 means "the image(s)
# cover more than 60% of the page area" — chosen as a round number comfortably above
# ordinary figure/diagram coverage (which rarely fills more than half a page) and
# comfortably below "the whole page is one image" (a real scan is normally ~1.0, minus
# margins pdfplumber's bounding boxes don't quite reach). Tune with real documents;
# this is a starting point, not a measured constant.
SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.6

# The lower bound of "meaningful" image coverage for the VISUAL trigger — below this,
# a small logo or icon shouldn't flip a whole document into a paid re-read. 0.2 is
# picked to sit clearly under SCANNED_IMAGE_COVERAGE_THRESHOLD, leaving a real band
# (0.2-0.6) for "a chart or diagram takes up a meaningful chunk of an otherwise
# text-bearing page" — exactly the case VISUAL exists for.
#
# Caveat stated plainly because it matters: pdfplumber can only tell us a rectangular
# image occupies X% of the page area. It cannot tell "a chart" from "a photo" from
# "a scanned watermark" — those all look identical as a bounding box and a fill ratio.
# This heuristic is a proxy, not a classifier, and is expected to need tuning once real
# documents are run through it.
VISUAL_IMAGE_COVERAGE_THRESHOLD = 0.2


class PdfPageSignals(TypedDict):
    """One page's free structural signals, gathered from the same pdfplumber pass that
    already runs for table extraction (see _extract_pdfplumber_signals in
    file_utils.py) — computed there, classified here, so pdfplumber is only opened once.
    """
    char_count: int
    image_coverage: float
    table_count: int


def classify_pdf_page(page: PdfPageSignals) -> Optional[ProcessingMode]:
    """Which mode (if any) a single page's signals call for.

    Order matters: a table check comes first because a page can be simultaneously
    sparse-looking (few real text chars once a big table is excluded) and full of
    structure — that page needs LAYOUT_AWARE, not a scan verdict pulled from a
    character count a table was always going to make look low.
    """
    if page["table_count"] > 0:
        return ProcessingMode.LAYOUT_AWARE

    sparse_text = page["char_count"] < SCANNED_PDF_CHARS_PER_PAGE
    if sparse_text and page["image_coverage"] >= SCANNED_IMAGE_COVERAGE_THRESHOLD:
        return ProcessingMode.SCANNED

    if VISUAL_IMAGE_COVERAGE_THRESHOLD <= page["image_coverage"] < SCANNED_IMAGE_COVERAGE_THRESHOLD \
            and not sparse_text:
        return ProcessingMode.VISUAL

    return None


def classify_pdf_target_mode(pages: List[PdfPageSignals]) -> Tuple[ProcessingMode, str]:
    """Aggregate every page's classification into one document-level target mode.

    Returns ``(target_mode, reason)`` — ``reason`` is a short human-readable string
    naming which condition(s) fired and on which pages, meant to go straight into the
    auto-shift log line so a shift is never silent (see _read_pdf_with_auto_shift).

    Rule: more than one distinct mode triggered anywhere across the document ->
    MIXED (a page needing a table read and a different page looking scanned means no
    single specialised mode covers the document). Exactly one distinct mode triggered
    (on one page or many) -> that mode. Nothing triggered -> TEXT_BASED, meaning "keep
    the free result exactly as it is".
    """
    triggers: Dict[ProcessingMode, List[int]] = {}
    for page_number, page in enumerate(pages, start=1):
        target = classify_pdf_page(page)
        if target is not None:
            triggers.setdefault(target, []).append(page_number)

    if not triggers:
        return ProcessingMode.TEXT_BASED, "no page triggered a shift"

    reason = ", ".join(
        f"{mode.value} (pages {page_numbers})" for mode, page_numbers in triggers.items()
    )

    if len(triggers) > 1:
        return ProcessingMode.MIXED, reason
    (single_mode,) = triggers.keys()
    return single_mode, reason


# ---------------------------------------------------------------------------
# DOCX structural classification -> target processing mode
# ---------------------------------------------------------------------------
#
# DOCX is the one other format LlamaParse accepts (LLAMAPARSE_ELIGIBLE_TYPES above),
# so it gets the same auto-shift treatment as a PDF, at a coarser grain: python-docx
# gives a whole-document image/table count, not a per-page breakdown, so there is no
# per-page aggregation step here the way classify_pdf_target_mode has one.


class DocxSignals(TypedDict):
    """A DOCX's free structural signals, gathered by the same python-docx pass that
    already runs for text extraction (see read_docx_to_plain_text) — computed there,
    classified here, so the document is only opened once."""
    image_count: int
    table_count: int


def classify_docx_target_mode(signals: DocxSignals) -> Tuple[ProcessingMode, str]:
    """Which mode (if any) a DOCX's signals call for.

    Mirrors classify_pdf_target_mode's aggregation rule at document granularity: an
    embedded image found alongside a table means no single specialised mode covers
    the document -> MIXED. Exactly one of the two -> that mode. Neither -> TEXT_BASED,
    meaning "keep the free result exactly as it is".

    A table triggers LAYOUT_AWARE even though read_docx_to_plain_text already emits
    tab-separated table text locally — the same generosity classify_pdf_page applies
    to a PDF table, on the reasoning that LlamaParse's table flags keep structure
    (header association, merged cells) a flat tab-join throws away.
    """
    triggers: Dict[ProcessingMode, str] = {}
    if signals["table_count"] > 0:
        triggers[ProcessingMode.LAYOUT_AWARE] = f"{signals['table_count']} table(s)"
    if signals["image_count"] > 0:
        triggers[ProcessingMode.VISUAL] = f"{signals['image_count']} image(s)"

    if not triggers:
        return ProcessingMode.TEXT_BASED, "no image or table found"

    reason = ", ".join(f"{mode.value} ({why})" for mode, why in triggers.items())

    if len(triggers) > 1:
        return ProcessingMode.MIXED, reason
    (single_mode,) = triggers.keys()
    return single_mode, reason


# ---------------------------------------------------------------------------
# HTML weblink scraping: which backend, and which of its options, per mode
# ---------------------------------------------------------------------------
#
# ``scrape_using`` is no longer a caller-facing choice (fs-agent-knowledge
# docs/plan/document-processing/decisions.md, 2026-08-24) — the mode decides which
# scraper reads an HTML page, the same way it already decides which LlamaParse settings
# read a file. This is the one place that mapping lives, for the same reason
# ``_LLAMAPARSE_SETTINGS`` above is one dict: so the mapping cannot drift between two
# call sites, and so "what does mode X actually do for a weblink" has one answer instead
# of an inferred one.
#
# Only two backends are real candidates. Newspaper3k is free and does plain-text
# extraction with no options — no tables, no rendering, no proxy. FireCrawl costs a
# credit per page and can render JS, keep table/column structure, and follow links. A
# crawl only exists through FireCrawl (Newspaper/LangChain have no crawl API), so
# ``scraping_method == "crawl"`` always wins over whatever the mode would otherwise pick.
_HTML_SCRAPER_FOR_MODE: Dict[ProcessingMode, str] = {
    # AUTO means "no mode was given" — for HTML there is nothing to detect after the
    # fact (unlike a PDF's per-page auto-shift), so it just gets the same free scraper
    # TEXT_BASED does.
    ProcessingMode.AUTO: "newspaper",
    ProcessingMode.TEXT_BASED: "newspaper",
    # HTML has no scanned/OCR dimension — nothing distinguishes this from TEXT_BASED
    # today. Stated explicitly rather than left to fall through a default, because an
    # unstated fallback is exactly what "no clear mapping" looks like from outside.
    ProcessingMode.SCANNED: "newspaper",
    ProcessingMode.LAYOUT_AWARE: "firecrawl",
    # No image/vision consumer exists anywhere in this worker — grepping for
    # image_url/base64/vision/screenshot/lvm across celery_utils turns up only the LVM
    # mode table above (fs-agent-knowledge plan.md §3e). So VISUAL cannot do anything
    # beyond LAYOUT_AWARE for a weblink until that missing step is built (tracked as O5,
    # not implemented). Mapped the same on purpose, not by omission.
    ProcessingMode.VISUAL: "firecrawl",
    ProcessingMode.MIXED: "firecrawl",
}

# Whether FireCrawl's own boilerplate-stripping (onlyMainContent, on by default) should
# run. False keeps surrounding structure — tables, columns — that "main content only"
# would otherwise strip out along with the nav/ads it's meant to remove.
_FIRECRAWL_KEEP_STRUCTURE_MODES = frozenset({
    ProcessingMode.LAYOUT_AWARE, ProcessingMode.VISUAL, ProcessingMode.MIXED,
})


def resolve_html_scraper(mode: ProcessingMode, scraping_method: str) -> str:
    """Which scraper backend reads an HTML weblink, for this mode and method.

    An unrecognised mode falls back to FireCrawl (the more capable, safer default) rather
    than Newspaper — the same "obviously wrong is better than plausibly wrong" reasoning
    as ``estimated_credits`` above; silently downgrading an unrecognised mode to the free
    reader would look like nothing went wrong.
    """
    if scraping_method == "crawl":
        return "firecrawl"
    return _HTML_SCRAPER_FOR_MODE.get(mode, "firecrawl")


def firecrawl_only_main_content(mode: ProcessingMode) -> bool:
    """FireCrawl's ``onlyMainContent`` setting for this mode. ``True`` is FireCrawl's own
    default, so an unrecognised mode gets ordinary (cheaper-to-read, boilerplate-free)
    behaviour rather than a surprise change in output shape.
    """
    return mode not in _FIRECRAWL_KEEP_STRUCTURE_MODES


# ---------------------------------------------------------------------------
# Enrichment: which LLM reads the extracted text back
# ---------------------------------------------------------------------------
#
# Everything above decides how the text gets *out* of the document. This decides which
# model reads that text afterwards to pull out metadata, summaries and chunks — and it
# lives here, next to the parse settings, because the two are the same decision seen
# from opposite ends: a mode is picked precisely because the document is hard to read,
# and the output of a hard-to-read document is itself harder to read.
#
# TEXT_BASED extraction returns ordinary prose, which the configured default model
# (cheap, fast) handles perfectly well. The other modes deliberately return something
# messier — OCR output with stray page furniture, tables re-emitted as HTML, a vision
# model's prose description of a chart. Handing that to the cheap default is where
# enrichment quality drops, so those modes pin a stronger model for the enrichment call
# only. The parse itself is unaffected.
#
# This is a *default*, not a rule: a knowledge base whose own config names an enrichment
# model keeps that model in every mode. See ``get_enrichment_config`` in
# celery_utils/enrichment/llm_factory.py for where this sits in the fallback chain.
RICH_MODE_ENRICHMENT_PROVIDER = "openai"
RICH_MODE_ENRICHMENT_MODEL = "gpt-5.6-luna"

# The modes whose extracted text is plain enough for the ordinary default. Written as a
# set of the exceptions rather than as a set of the overridden modes so that a mode added
# later gets the stronger model unless someone deliberately says it doesn't need it —
# the safe direction to be wrong in is "too capable", not "quietly worse". AUTO belongs
# here too: before any auto-shift happens the extracted text is the same plain read
# TEXT_BASED produces, so it deserves the same (cheap) enrichment model.
_PLAIN_TEXT_MODES = frozenset({ProcessingMode.AUTO, ProcessingMode.TEXT_BASED})


def enrichment_override_for_mode(
    mode: Optional[Any],
) -> Optional[Tuple[str, str]]:
    """The ``(provider, model)`` a mode asks enrichment to use, or None for "no opinion".

    ``None`` back means *leave the configured default alone* — which is what an unknown
    mode, a missing mode, and TEXT_BASED all get. Callers that were never told a mode
    (an older queued message, the plain-text ingest path) therefore behave exactly as
    they did before this existed.

    Accepts either the enum or its wire string, because the mode crosses a queue as a
    plain string and not every call site converts it back first.
    """
    if mode is None:
        return None
    if not isinstance(mode, ProcessingMode):
        try:
            mode = ProcessingMode(mode)
        except ValueError:
            # Unknown mode = version mismatch between services. Changing the model on a
            # guess is worse than leaving the configured one in place.
            return None
    if mode in _PLAIN_TEXT_MODES:
        return None
    return RICH_MODE_ENRICHMENT_PROVIDER, RICH_MODE_ENRICHMENT_MODEL
