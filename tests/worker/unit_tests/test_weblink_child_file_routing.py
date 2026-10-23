"""Weblink ingestion: a crawled child that is really a file, and a scrape that failed.

Two independent defects on the weblink path, both of which silently produced a
"completed" document holding the wrong content:

1. `extract_text_from_url` caught every scraper exception and returned a
   success-shaped dict whose `content` was `f"Error fetching content: {e}"`. That
   string is longer than MIN_CONTENT_LENGTH (5), so it passed the length gate in
   `_process_single_url`, was sent to the enrichment LLM, and was indexed as though
   it were the page. It now raises, which lets the caller mark the document failed.

2. FireCrawl renders every crawled child to markdown before this module sees it, so a
   PDF or DOCX behind a crawled link was read as FireCrawl's markdown — LlamaParse,
   the scanned/layout_aware/visual settings and the PDF auto-shift were all
   unreachable for children. `_reread_child_as_file` HEAD-checks each child and
   re-reads a file one through the same `get_file_text` an upload uses.

3. `_get_content_type_from_url` returned whatever the HEAD gave it, including nothing.
   A server answering HEAD with 405, or with no Content-Type, left the MIME empty, which
   fell through the routing to `raise ValueError("Unsupported content type: ")` — an
   ordinary URL failing to ingest with an error naming no type, because there wasn't one.
   It now falls back to the URL's extension, then to HTML.

The rules under test:
- A scraper failure raises rather than returning error text as content.
- A child whose MIME type is a file type is re-read through get_file_text.
- A child that is an ordinary HTML page is left alone (returns None), so the
  existing FireCrawl-markdown behaviour is unchanged.
- A HEAD failure or a failed re-read falls back to FireCrawl's markdown rather than
  failing the whole crawl — one bad child must not lose the other nine.
- A URL whose type the server won't state still resolves: extension first, then HTML.
- A served Content-Type always beats the extension guess.

Pure unit tests: httpx and get_file_text are monkeypatched, so no network call and
no parser ever runs.
"""

import pytest

import celery_utils.processing.weblink_utils as weblink_utils


# ---------------------------------------------------------------------------
# _is_file_mime
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mime,expected", [
    ("application/pdf", True),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", True),
    # Legacy .doc is excluded on purpose — get_file_text cannot read it, so re-reading a
    # child as one would download the file only to fall back to FireCrawl's markdown.
    ("application/msword", False),
    ("text/plain", True),
    ("text/html", False),
    ("image/png", False),
    ("", False),
])
def test_is_file_mime_classifies_only_reader_backed_types(mime, expected):
    assert weblink_utils._is_file_mime(mime) is expected


# ---------------------------------------------------------------------------
# extract_text_from_url — a failure must not become content
# ---------------------------------------------------------------------------

def test_scraper_failure_raises_instead_of_returning_error_text(monkeypatch):
    """The regression this file exists for: an error string indexed as the page."""
    def _boom(url):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(weblink_utils, "process_url_with_langchain", _boom)

    with pytest.raises(RuntimeError, match="connection reset"):
        weblink_utils.extract_text_from_url("https://example.com", scrape_using="langchain")


def test_unsupported_scraper_still_raises(monkeypatch):
    with pytest.raises(ValueError, match="Unsupported scraping method"):
        weblink_utils.extract_text_from_url("https://example.com", scrape_using="nope")


# ---------------------------------------------------------------------------
# _reread_child_as_file
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content=b"%PDF-1.4 fake bytes", headers=None):
        self.content = content
        self.headers = headers or {}


class _FakeClient:
    """Stands in for httpx.Client as a context manager."""

    def __init__(self, mime="text/html", head_raises=False):
        self._mime = mime
        self._head_raises = head_raises

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def head(self, url, follow_redirects=False):
        if self._head_raises:
            raise RuntimeError("HEAD not allowed")
        return _FakeResponse(headers={"Content-Type": self._mime})

    def get(self, url):
        return _FakeResponse()


def _patch_client(monkeypatch, **kwargs):
    monkeypatch.setattr(weblink_utils.httpx, "Client", lambda *a, **k: _FakeClient(**kwargs))


def test_html_child_is_left_to_firecrawl(monkeypatch):
    """An ordinary crawled page must keep its existing behaviour — None means 'no change'."""
    _patch_client(monkeypatch, mime="text/html")

    result = weblink_utils._reread_child_as_file("https://example.com/page", use_llama_parse=False)

    assert result is None


def test_pdf_child_is_rerouted_through_the_file_reader(monkeypatch):
    """The fix: a PDF behind a crawled link reaches get_file_text with the crawl's mode."""
    _patch_client(monkeypatch, mime="application/pdf")
    seen = {}

    def _fake_get_file_text(file_bytes, mime_type, filename, mode, owner_id=None):
        seen["mime_type"] = mime_type
        seen["filename"] = filename
        seen["mode"] = mode
        return {"content": "extracted table text", "llama_parse_credits": 30,
                "effective_mode": "layout_aware", "parse_mode": "parse_page_with_llm"}

    monkeypatch.setattr(weblink_utils, "get_file_text", _fake_get_file_text)

    result = weblink_utils._reread_child_as_file(
        "https://example.com/report.pdf", use_llama_parse=False,
        document_processing_mode="layout_aware",
    )

    assert result is not None
    assert result["content"] == "extracted table text"
    assert result["llama_parse_credits"] == 30
    assert result["mime_type"] == "application/pdf"
    assert seen["mime_type"] == "application/pdf"
    assert seen["filename"] == "report.pdf"
    # The crawl's mode reaches the reader — this is what makes the tabular re-read happen.
    assert seen["mode"] == weblink_utils.ProcessingMode.LAYOUT_AWARE


def test_head_failure_on_an_extensionless_url_keeps_firecrawl_markdown(monkeypatch):
    """A HEAD-hostile server must not fail the crawl — one bad child, nine good ones.

    With no extension to fall back on the type resolves to HTML, so the child keeps
    FireCrawl's markdown. A .pdf URL behind the same server does still get re-read —
    see test_head_exception_still_resolves_a_type.
    """
    _patch_client(monkeypatch, head_raises=True)

    result = weblink_utils._reread_child_as_file("https://example.com/page", use_llama_parse=False)

    assert result is None


def test_failed_reread_falls_back_to_firecrawl_markdown(monkeypatch):
    """A parser blowing up on one child is a fallback, not a crawl-wide failure."""
    _patch_client(monkeypatch, mime="application/pdf")

    def _boom(*args, **kwargs):
        raise RuntimeError("LlamaParse 500")

    monkeypatch.setattr(weblink_utils, "get_file_text", _boom)

    result = weblink_utils._reread_child_as_file(
        "https://example.com/report.pdf", use_llama_parse=False,
    )

    assert result is None


# ---------------------------------------------------------------------------
# _get_content_type_from_url — a HEAD-hostile server must not fail the ingest
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://example.com/report.pdf", "application/pdf"),
    ("https://example.com/report.PDF?download=1", "application/pdf"),
    ("https://example.com/notes.txt", "text/plain"),
    ("https://example.com/readme.md", "text/plain"),
    ("https://example.com/some/page", "text/html"),
    ("https://example.com/", "text/html"),
])
def test_missing_content_type_falls_back_to_extension_then_html(url, expected):
    """A 405 on HEAD, or a 200 with no Content-Type, used to raise
    'Unsupported content type: ' and fail an otherwise ordinary URL."""
    client = _FakeClient(mime="")

    assert weblink_utils._get_content_type_from_url(client, url) == expected


def test_head_exception_still_resolves_a_type():
    client = _FakeClient(head_raises=True)

    assert weblink_utils._get_content_type_from_url(client, "https://example.com/a.pdf") == "application/pdf"
    assert weblink_utils._get_content_type_from_url(client, "https://example.com/a") == "text/html"


def test_served_content_type_always_wins_over_the_extension():
    """A .pdf URL that the server says is HTML is HTML — the header is the better signal."""
    client = _FakeClient(mime="text/html")

    assert weblink_utils._get_content_type_from_url(client, "https://example.com/a.pdf") == "text/html"
