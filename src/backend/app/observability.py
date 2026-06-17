from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        trace_id, span_id = current_trace_context()
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    return logging.getLogger(settings.service_name)


def configure_tracing(settings: Settings, logger: logging.Logger) -> None:
    if not settings.otlp_endpoint:
        logger.info("otel_exporter_disabled", extra={"extra": {"reason": "missing OTEL_EXPORTER_OTLP_ENDPOINT"}})
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        logger.exception("otel_import_failed")
        return

    resource = Resource.create(
        {
            "service.name": settings.service_name,
            "deployment.environment": settings.environment,
            "service.namespace": "agentic-bi",
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info("otel_exporter_enabled", extra={"extra": {"endpoint": settings.otlp_endpoint}})


def instrument_fastapi(app: Any, logger: logging.Logger) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except Exception:
        logger.exception("fastapi_instrumentation_import_failed")
        return
    FastAPIInstrumentor.instrument_app(app)


def get_tracer(name: str):
    try:
        from opentelemetry import trace
    except Exception:
        return _NoopTracer()
    return trace.get_tracer(name)


def current_trace_context() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace
    except Exception:
        return None, None
    span = trace.get_current_span()
    context = span.get_span_context()
    if not context or not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def set_attribute(self, _key: str, _value: Any) -> None:
        return None

    def record_exception(self, _error: BaseException) -> None:
        return None


class _NoopTracer:
    def start_as_current_span(self, _name: str) -> _NoopSpan:
        return _NoopSpan()


class LangfuseRecorder:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.client = None
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            logger.info("langfuse_disabled", extra={"extra": {"reason": "missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY"}})
            return
        try:
            from langfuse import Langfuse

            self.client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            logger.info("langfuse_enabled", extra={"extra": {"host": settings.langfuse_host}})
        except Exception:
            logger.exception("langfuse_init_failed")

    @contextmanager
    def generation(
        self,
        *,
        name: str,
        trace_id: str,
        input_text: str,
        model: str,
        metadata: dict[str, Any],
    ) -> Iterator["_LangfuseGenerationHandle"]:
        handle = _LangfuseGenerationHandle()
        if self.client is None:
            yield handle
            return

        try:
            trace = self.client.trace(
                id=trace_id,
                name="terminal_chat",
                input=input_text,
                metadata=metadata,
                tags=[self.settings.environment, self.settings.source_app_id],
            )
            generation = trace.generation(
                name=name,
                model=model,
                input=input_text,
                metadata=metadata,
            )
            handle.trace = trace
            handle.generation = generation
            yield handle
        except Exception:
            self.logger.exception("langfuse_generation_start_failed")
            yield handle

    def flush(self) -> None:
        if self.client is None:
            return
        try:
            self.client.flush()
        except Exception:
            self.logger.exception("langfuse_flush_failed")


class _LangfuseGenerationHandle:
    trace: Any = None
    generation: Any = None

    def end(self, *, output: str, usage: dict[str, int], status: str, status_message: str | None = None) -> None:
        if self.generation is None:
            return
        try:
            self.generation.end(
                output=output,
                usage=usage,
                level="ERROR" if status == "error" else "DEFAULT",
                status_message=status_message,
            )
            if self.trace is not None:
                self.trace.update(output=output)
        except Exception:
            logging.getLogger(__name__).exception("langfuse_generation_end_failed")
