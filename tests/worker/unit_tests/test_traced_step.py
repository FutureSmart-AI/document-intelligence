"""Tests for the LangSmith step tracing helpers.

The point of these: tracing wraps every extraction backend, so a bug in the wrapper
would break ingestion for every document type at once. Two properties matter more
than the metadata itself — the wrapped function still returns what it returned
before, and a broken tracer never takes an ingestion job down with it.
"""
import sys
import types
import pytest

from celery_utils.shared import tracing


class _FakeRun:
    def __init__(self):
        self.metadata = {}
        self.outputs = None

    def add_metadata(self, fields):
        self.metadata.update(fields)

    def end(self, outputs=None):
        self.outputs = outputs


class _FakeTraceCM:
    def __init__(self, run, recorder):
        self._run = run
        self._recorder = recorder

    def __enter__(self):
        return self._run

    def __exit__(self, *exc):
        return False


def _install_fake_langsmith(monkeypatch, runs):
    """Swap in a stand-in `langsmith` module so no network call is made."""
    module = types.ModuleType("langsmith")

    def trace(name, run_type="tool", inputs=None, metadata=None):
        run = _FakeRun()
        run.name = name
        run.run_type = run_type
        run.inputs = inputs
        run.metadata.update(metadata or {})
        runs.append(run)
        return _FakeTraceCM(run, None)

    module.trace = trace
    module.get_current_run_tree = lambda: runs[-1] if runs else None
    monkeypatch.setitem(sys.modules, "langsmith", module)
    return runs


def test_traced_step_records_metadata_and_text(monkeypatch):
    runs = _install_fake_langsmith(monkeypatch, [])

    with tracing.traced_step("API: Fake Backend", mode="visual") as step:
        step.add(page_count=7, estimated_credits=21.0)
        step.set_text("hello world")

    assert len(runs) == 1
    meta = runs[0].metadata
    assert meta["mode"] == "visual"
    assert meta["page_count"] == 7
    assert meta["estimated_credits"] == 21.0
    assert meta["text_chars"] == len("hello world")
    assert meta["text_preview_truncated"] is False
    assert "duration_ms" in meta
    assert runs[0].outputs["text"] == "hello world"


def test_traced_step_clips_long_text_but_keeps_true_length(monkeypatch):
    runs = _install_fake_langsmith(monkeypatch, [])
    monkeypatch.setattr(tracing, "_TEXT_PREVIEW_CHARS", 10)
    monkeypatch.setattr(tracing, "_TRACE_FULL_TEXT", False)

    with tracing.traced_step("Node: Fake") as step:
        step.set_text("x" * 5000)

    assert len(runs[0].outputs["text"]) == 10
    # The true length still reaches the trace, so a half-read document cannot
    # masquerade as a whole one just because the preview was clipped.
    assert runs[0].metadata["text_chars"] == 5000
    assert runs[0].metadata["text_preview_truncated"] is True


def test_traced_step_marks_error_and_reraises(monkeypatch):
    runs = _install_fake_langsmith(monkeypatch, [])

    with pytest.raises(ValueError, match="boom"):
        with tracing.traced_step("API: Fake Backend"):
            raise ValueError("boom")

    assert runs[0].metadata["error"] == "boom"
    assert runs[0].metadata["error_type"] == "ValueError"


def test_traced_step_survives_a_broken_tracer(monkeypatch):
    """A tracing failure must not fail the ingestion job."""
    broken = types.ModuleType("langsmith")

    def exploding_trace(*a, **k):
        raise RuntimeError("langsmith is down")

    broken.trace = exploding_trace
    broken.get_current_run_tree = exploding_trace
    monkeypatch.setitem(sys.modules, "langsmith", broken)

    with tracing.traced_step("API: Fake Backend") as step:
        step.add(page_count=3)
        step.set_text("still fine")
        result = "work completed"

    assert result == "work completed"


def test_traced_reader_reads_metadata_off_the_returned_dict(monkeypatch):
    runs = _install_fake_langsmith(monkeypatch, [])

    @tracing.traced_reader(
        "API: LlamaParse - Parse File",
        inputs=("file_name", "mode"),
        meta_keys=("llama_parse_page_count", "llama_parse_credits"),
    )
    def fake_reader(file_name, mode="visual"):
        return {
            "content": "parsed text",
            "llama_parse_page_count": 12,
            "llama_parse_credits": 36.0,
            "ignored_key": "not recorded",
        }

    out = fake_reader("cv.pdf", mode="visual")

    assert out["content"] == "parsed text"  # passthrough unchanged
    meta = runs[0].metadata
    assert meta["file_name"] == "cv.pdf"
    assert meta["mode"] == "visual"
    assert meta["llama_parse_page_count"] == 12
    assert meta["llama_parse_credits"] == 36.0
    assert "ignored_key" not in meta
    assert runs[0].outputs["text"] == "parsed text"


def test_traced_reader_unwraps_enum_valued_inputs(monkeypatch):
    """ProcessingMode is an enum; the trace should show `visual`, not the repr."""
    runs = _install_fake_langsmith(monkeypatch, [])

    from celery_utils.processing.processing_modes import ProcessingMode

    @tracing.traced_reader("Node: Fake", inputs=("mode",))
    def fake_reader(mode):
        return {"content": "ok"}

    fake_reader(ProcessingMode.VISUAL)
    assert runs[0].metadata["mode"] == ProcessingMode.VISUAL.value


def test_traced_reader_propagates_the_backend_error(monkeypatch):
    runs = _install_fake_langsmith(monkeypatch, [])

    @tracing.traced_reader("API: Fake Backend")
    def failing_reader():
        raise TimeoutError("backend timed out")

    with pytest.raises(TimeoutError, match="backend timed out"):
        failing_reader()

    assert runs[0].metadata["error_type"] == "TimeoutError"


def test_extract_text_from_url_still_dispatches_after_tracing_wrap(monkeypatch):
    """The scraper dispatcher's body was re-indented into a tracing block — prove each
    branch still routes to the backend it did before, and that an unknown scraper
    still raises rather than returning an error-shaped success."""
    # Import before the fake langsmith module is installed — weblink_utils pulls in
    # langchain_core, which imports RunTree from the real package at import time.
    from celery_utils.processing import weblink_utils as wu

    runs = _install_fake_langsmith(monkeypatch, [])

    monkeypatch.setattr(wu, "_download_and_parse_article",
                        lambda article: {"content": "newspaper text"})
    monkeypatch.setattr(wu, "process_url_with_firecrawl",
                        lambda **kw: {"content": "firecrawl text", "pages_processed": 4,
                                      "crawled_urls": [{"url": "u1"}, {"url": "u2"}]})
    monkeypatch.setattr(wu, "process_url_with_langchain",
                        lambda url: {"content": "langchain text"})

    assert wu.extract_text_from_url("http://x", "newspaper", "scrape", 1)["content"] == "newspaper text"
    assert wu.extract_text_from_url("http://x", "langchain", "scrape", 1)["content"] == "langchain text"

    out = wu.extract_text_from_url("http://x", "firecrawl", "crawl", 2)
    assert out["content"] == "firecrawl text"
    crawl_run = [r for r in runs if r.name == "Node: Route URL To Scraper"][-1]
    assert crawl_run.metadata["pages_processed"] == 4
    assert crawl_run.metadata["child_urls"] == 2

    with pytest.raises(ValueError, match="Unsupported scraping method"):
        wu.extract_text_from_url("http://x", "nope", "scrape", 1)
