import functools
import inspect
import re

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

import os
from utils.setup_env import OTEL_ENDPOINT

_TRACING_INITIALIZED = False
_IS_LOCAL = os.getenv("MODE", "LOCAL").upper() == "LOCAL"


def init_tracing(service_name: str):
    global _TRACING_INITIALIZED
    if _TRACING_INITIALIZED or _IS_LOCAL or not OTEL_ENDPOINT:
        return
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{OTEL_ENDPOINT}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _TRACING_INITIALIZED = True


# Use a no-op tracer locally so spans are created in-memory but never exported.
if not _IS_LOCAL:
    tracer = trace.get_tracer(__name__)
else:
    trace.set_tracer_provider(TracerProvider())
    tracer = trace.get_tracer(__name__)

_EMAIL_RE = re.compile(r"/[^/@\s]+@[^/\s]+")
_HEX_ID_RE = re.compile(r"/[0-9a-f]{24,}(?=/|$)")
_UUID_RE = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?=/|$)")


def _normalize_path(path: str) -> str:
    path = _UUID_RE.sub("/{id}", path)
    path = _HEX_ID_RE.sub("/{id}", path)
    path = _EMAIL_RE.sub("/{user_id}", path)
    return path


class TracingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")

        # Continue an incoming distributed trace when the caller sends W3C
        # traceparent headers (e.g. the eval harness). No header -> empty context,
        # so this is a no-op root span exactly as before.
        carrier = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        parent_ctx = extract(carrier)

        with tracer.start_as_current_span(
            f"{method} {path}", context=parent_ctx, kind=trace.SpanKind.SERVER
        ) as span:
            span.set_attribute("http.method", method)

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    status_code = message.get("status", 0)
                    span.set_attribute("http.status_code", status_code)
                    if status_code >= 400:
                        span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
                route = scope.get("route")
                if route is not None:
                    route_template = getattr(route, "path", path)
                else:
                    # normalize unmatched paths to avoid cardinality explosion
                    route_template = _normalize_path(path.split("?")[0])

                span.update_name(f"{method} {route_template}")
                span.set_attribute("http.route", route_template)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise


def track_time(func_or_name=None):
    def _wrap(func, span_name):
        if inspect.isasyncgenfunction(func):
            @functools.wraps(func)
            async def wrapper(*args, **kwargs):
                with tracer.start_as_current_span(span_name):
                    async for item in func(*args, **kwargs):
                        yield item
            return wrapper

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(span_name):
                return await func(*args, **kwargs)
        return wrapper

    if callable(func_or_name):
        return _wrap(func_or_name, func_or_name.__qualname__)

    def decorator(func):
        return _wrap(func, func_or_name or func.__qualname__)

    return decorator
