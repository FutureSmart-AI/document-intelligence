import os
import socket
import json
import time
import logging
import urllib.request
import urllib.error
import functools
import inspect
import atexit
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import StatusCode

logger = logging.getLogger(__name__)

OTEL_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

_tracer_provider = None


def _check_collector_reachability(endpoint: str) -> bool:
    """
    Runs at startup: checks TCP + HTTP reachability of the OTel collector.
    Logs results but never raises — tracing failures must not crash the app.
    Returns True if the collector is reachable and accepting spans.
    """
    logger.info("[otel] Checking collector reachability: %s", endpoint)

    # Parse host/port from endpoint URL
    try:
        without_scheme = endpoint.split("://", 1)[1]
        host_port = without_scheme.split("/")[0]
        host, port_str = (host_port.rsplit(":", 1) if ":" in host_port else (host_port, "4318"))
        port = int(port_str)
    except Exception as e:
        logger.error("[otel] Could not parse endpoint URL '%s': %s", endpoint, e)
        return False

    # TCP check
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        logger.info("[otel] TCP connect to %s:%s OK", host, port)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logger.error(
            "[otel] TCP connect to %s:%s FAILED: %s  "
            "→ Check EC2 security group (port %s) and ECS subnet routing.",
            host, port, e, port,
        )
        return False

    # HTTP probe — send a minimal OTLP JSON span
    try:
        now_ns = str(int(time.time() * 1e9))
        payload = json.dumps({
            "resourceSpans": [{
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": "startup-probe"}}]
                },
                "scopeSpans": [{
                    "scope": {"name": "startup-probe"},
                    "spans": [{
                        "traceId": "0af7651916cd43dd8448eb211c80319c",
                        "spanId": "b7ad6b7169203331",
                        "name": "startup-connectivity-check",
                        "kind": 1,
                        "startTimeUnixNano": now_ns,
                        "endTimeUnixNano": now_ns,
                        "status": {"code": 1},
                    }]
                }]
            }]
        }).encode()

        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("[otel] Collector accepted startup probe (HTTP %s) ✓", resp.status)
            return True

    except urllib.error.HTTPError as e:
        logger.error("[otel] Collector rejected probe HTTP %s: %s", e.code, e.read().decode()[:200])
    except urllib.error.URLError as e:
        logger.error("[otel] Collector HTTP probe failed: %s", e.reason)
    except Exception as e:
        logger.error("[otel] Unexpected error during collector probe: %s", e)

    return False


def init_tracing(service_name: str):
    if not OTEL_ENDPOINT:
        return
    global _tracer_provider

    # Avoid double-init (gevent pool reuses the same process)
    if _tracer_provider is not None:
        logger.info("[otel] Tracing already initialized, skipping.")
        return

    reachable = _check_collector_reachability(OTEL_ENDPOINT)

    resource = Resource.create({"service.name": service_name})
    _tracer_provider = TracerProvider(resource=resource)

    if not reachable:
        # Collector confirmed down at startup — don't wire up a real exporter at
        # all. With SimpleSpanProcessor, every span-end synchronously calls
        # exporter.export() on the task's own thread; against a dead collector
        # that call was blocking each Celery task for ~60s (export retries),
        # which starved the whole worker (concurrency=1) since no other task
        # could run while a task sat blocked on span export. A provider with no
        # span processor is a correct no-op: spans are still created (code that
        # calls start_as_current_span keeps working) but nothing is exported.
        logger.warning(
            "[otel] Collector at %s is NOT reachable — tracing disabled for this "
            "process (no exporter attached) so span export can't block task "
            "execution. Set OTEL_EXPORTER_OTLP_ENDPOINT to a reachable collector "
            "and restart to re-enable.",
            OTEL_ENDPOINT,
        )
        trace.set_tracer_provider(_tracer_provider)
        atexit.register(_shutdown_tracing)
        logger.info("[otel] Tracing initialized for service '%s' (no-op, collector unreachable)", service_name)
        return

    # export_timeout is a safety net for a collector that goes down *after*
    # this startup check passed — caps a single export call instead of letting
    # it hang on the exporter's internal connect/retry behavior.
    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, timeout=int(os.environ.get("OTEL_EXPORT_TIMEOUT_SECONDS", "5")))

    # Use SimpleSpanProcessor when running under gevent to avoid background
    # thread issues. BatchSpanProcessor relies on a daemon thread that gets
    # starved/deadlocked under gevent monkey-patching.
    use_simple = os.environ.get("OTEL_USE_SIMPLE_PROCESSOR", "").lower() in ("1", "true")
    if use_simple:
        _tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
        logger.info("[otel] Using SimpleSpanProcessor (synchronous export)")
    else:
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(_tracer_provider)

    # Ensure spans are flushed on process exit
    atexit.register(_shutdown_tracing)

    logger.info("[otel] Tracing initialized for service '%s' → %s", service_name, OTEL_ENDPOINT)


def _shutdown_tracing():
    global _tracer_provider
    if _tracer_provider is not None:
        logger.info("[otel] Flushing and shutting down tracer provider...")
        _tracer_provider.force_flush(timeout_millis=5000)
        _tracer_provider.shutdown()
        _tracer_provider = None


import contextvars

# Per-task embedding cost, stashed so the top-level Process Document trace can
# report it. index_in_vectordb sets it; the process_and_index_* pipelines read it
# back after completion and hand it to mark_indexing_time.
_embedding_cost = contextvars.ContextVar("embedding_cost", default=(0, 0.0))


def reset_embedding_cost():
    _embedding_cost.set((0, 0.0))


def add_embedding_cost(tokens, cost_usd):
    """Accumulate (crawl mode indexes several child docs in one task)."""
    t, c = _embedding_cost.get()
    _embedding_cost.set((t + tokens, c + cost_usd))


def get_embedding_cost():
    """Returns (tokens, cost_usd) accumulated for the current task."""
    return _embedding_cost.get()


def mark_indexing_time(indexing_time_ms, **extra):
    """Attach the document's indexing time (and any extra fields) to the current
    LangSmith run so the eval can read it off the 'Process Document' trace by
    knowledge_id. No-op when LangSmith tracing is disabled."""
    try:
        import langsmith as ls
        run = ls.get_current_run_tree()
        if run is not None:
            run.add_metadata({"indexing_time_ms": indexing_time_ms, **extra})
    except Exception:
        pass


class TracingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(f"{method} {path}"):
            await self.app(scope, receive, send)


def track_time(func_or_name=None):
    def _wrap(func, span_name):
        if inspect.isasyncgenfunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                tracer = trace.get_tracer(__name__)
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        async for item in func(*args, **kwargs):
                            yield item
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        raise
            return wrapper
        elif inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                tracer = trace.get_tracer(__name__)
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        raise
            return wrapper
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                tracer = trace.get_tracer(__name__)
                with tracer.start_as_current_span(span_name) as span:
                    try:
                        return func(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        raise
            return wrapper

    if callable(func_or_name):
        return _wrap(func_or_name, func_or_name.__qualname__)
    else:
        def decorator(func):
            return _wrap(func, func_or_name or func.__qualname__)
        return decorator


# ---------------------------------------------------------------------------
# LangSmith step tracing
# ---------------------------------------------------------------------------
# Why this exists: every extraction backend (LlamaParse, Firecrawl, pdfminer,
# Whisper, ...) was instrumented with OpenTelemetry only, and in production the
# OTel collector is unreachable — init_tracing() attaches no exporter, so those
# spans are created and dropped. Meanwhile LangSmith only ever saw the second
# half of the pipeline (enrich -> embed -> index). `traced_step` opens BOTH a
# LangSmith run and an OTel span for one pipeline step, so the extraction half
# becomes visible in the same trace tree as the enrichment half.
#
# Convention: span/run share one name, using the prefixes already in this repo —
# `API:` for an external service, `Node:` for local work, `DB:` for storage.

# Extracted document text is attached to spans so you can see what each backend
# actually returned. It is capped by default: a preview answers "did this backend
# return sensible text?" while the full length field catches the case where it
# did not. Set LANGSMITH_TRACE_FULL_TEXT=true to send the whole thing.
_TEXT_PREVIEW_CHARS = int(os.environ.get("LANGSMITH_TEXT_PREVIEW_CHARS", "10000"))
_TRACE_FULL_TEXT = os.environ.get("LANGSMITH_TRACE_FULL_TEXT", "").lower() == "true"


def _clip(text):
    """Return (payload, full_length, was_truncated) for a text value on a span."""
    if text is None:
        return None, 0, False
    text = str(text)
    if _TRACE_FULL_TEXT or len(text) <= _TEXT_PREVIEW_CHARS:
        return text, len(text), False
    return text[:_TEXT_PREVIEW_CHARS], len(text), True


class _StepRecorder:
    """Handed to the body of a `traced_step` block so it can report what happened.

    Every method swallows its own errors: a tracing bug must never fail an
    ingestion job, which is the same stance init_tracing() takes for OTel.
    """

    def __init__(self, otel_span, ls_run):
        self._span = otel_span
        self._run = ls_run
        self.metadata = {}
        self.outputs = {}

    def add(self, **fields):
        """Record scalar facts about the step (route taken, page count, cost, ...)."""
        try:
            for key, value in fields.items():
                if value is None:
                    continue
                self.metadata[key] = value
                if self._span is not None:
                    self._span.set_attribute(key, value if isinstance(value, (bool, int, float)) else str(value))
        except Exception:
            pass

    def set_text(self, text, key="text"):
        """Record the text a backend produced (clipped unless full text is enabled)."""
        try:
            payload, length, truncated = _clip(text)
            self.outputs[key] = payload
            self.add(**{f"{key}_chars": length, f"{key}_preview_truncated": truncated})
        except Exception:
            pass

    def set_output(self, **fields):
        """Record non-text outputs (structured results, counts, flags)."""
        try:
            self.outputs.update({k: v for k, v in fields.items() if v is not None})
        except Exception:
            pass


@contextmanager
def traced_step(name, run_type="tool", inputs=None, **metadata):
    """Trace one pipeline step to LangSmith and OpenTelemetry at once.

    Usage:
        with traced_step("API: LlamaParse - Parse File", file_name=name, mode=mode) as step:
            text = call_llamaparse(...)
            step.set_text(text)
            step.add(page_count=pages, estimated_credits=credits)

    Records wall-clock duration on every step and marks both the run and the span
    as errored (then re-raises) if the body throws. Never raises on its own.
    """
    started = time.time()
    tracer = trace.get_tracer(__name__)
    otel_cm = None
    otel_span = None
    ls_cm = None
    ls_run = None

    try:
        otel_cm = tracer.start_as_current_span(name)
        otel_span = otel_cm.__enter__()
    except Exception:
        otel_cm = None

    try:
        import langsmith as ls
        ls_cm = ls.trace(name=name, run_type=run_type, inputs=inputs or {}, metadata=dict(metadata))
        ls_run = ls_cm.__enter__()
    except Exception:
        ls_cm = None

    recorder = _StepRecorder(otel_span, ls_run)
    recorder.add(**metadata)

    try:
        yield recorder
    except Exception as exc:
        recorder.add(error=str(exc), error_type=type(exc).__name__)
        try:
            if otel_span is not None:
                otel_span.record_exception(exc)
                otel_span.set_status(StatusCode.ERROR, str(exc))
        except Exception:
            pass
        _close_step(recorder, started, otel_cm, ls_cm, exc)
        raise
    else:
        _close_step(recorder, started, otel_cm, ls_cm, None)


def _close_step(recorder, started, otel_cm, ls_cm, exc):
    """Flush the recorder's metadata onto both backends and close them."""
    try:
        recorder.add(duration_ms=int((time.time() - started) * 1000))
    except Exception:
        pass

    try:
        if ls_cm is not None:
            run = recorder._run
            if run is not None:
                run.add_metadata(recorder.metadata)
                if recorder.outputs:
                    run.end(outputs=recorder.outputs)
            ls_cm.__exit__(type(exc) if exc else None, exc, None)
    except Exception:
        pass

    try:
        if otel_cm is not None:
            otel_cm.__exit__(type(exc) if exc else None, exc, None)
    except Exception:
        pass


def traced_reader(name, inputs=(), text_key="content", meta_keys=(), run_type="tool"):
    """Trace an extraction backend (a file reader, a scraper) to LangSmith + OTel.

    Replaces @track_time on these functions rather than stacking with it — traced_step
    already opens an OTel span under the same name, and two nested identical spans
    just make the trace harder to read.

    Declarative on purpose: the readers all return a dict, so the span's metadata is
    named by key rather than assembled by hand inside each body.

        inputs    — kwarg/arg names to record as the step's inputs (the request)
        text_key  — key in the returned dict holding the extracted text
        meta_keys — keys in the returned dict to record as metadata (pages, credits, ...)

    Anything the step cannot read is skipped silently; tracing never fails a job.
    """
    def decorator(func):
        try:
            param_names = list(inspect.signature(func).parameters)
        except (TypeError, ValueError):
            param_names = []

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            recorded = {}
            try:
                bound = dict(zip(param_names, args))
                bound.update(kwargs)
                for key in inputs:
                    if key in bound:
                        value = bound[key]
                        # ProcessingMode and friends are enums; their .value is what
                        # the rest of the pipeline logs, so match that.
                        recorded[key] = getattr(value, "value", value)
            except Exception:
                pass

            # Recorded twice on purpose: as inputs (what you read in the LangSmith
            # UI) and as metadata (what you can filter and group runs by).
            with traced_step(name, run_type=run_type, inputs=recorded, **recorded) as step:
                result = func(*args, **kwargs)
                try:
                    if isinstance(result, dict):
                        step.set_text(result.get(text_key))
                        step.add(**{k: result[k] for k in meta_keys if k in result})
                    elif isinstance(result, str):
                        step.set_text(result)
                except Exception:
                    pass
                return result

        return wrapper

    return decorator


def add_step_metadata(**fields):
    """Attach fields to whichever LangSmith run is currently open.

    For facts discovered deep inside a call that belong on an enclosing step — e.g.
    the auto-shift classifier's reason, which is decided inside the PDF reader but
    describes the routing decision above it. No-op when tracing is off.
    """
    try:
        import langsmith as ls
        run = ls.get_current_run_tree()
        if run is not None:
            run.add_metadata({k: v for k, v in fields.items() if v is not None})
    except Exception:
        pass
