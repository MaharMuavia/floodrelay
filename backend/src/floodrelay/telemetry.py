"""OpenTelemetry wiring.

Exports to an OTLP collector when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (Jaeger
locally, CloudWatch in AWS) and does nothing otherwise. Never fatal: a console
that refuses to start because a tracing collector is down would be a poor trade.
"""

from __future__ import annotations

import logging

from .config import Settings

logger = logging.getLogger(__name__)

_configured = False


def setup_tracing(settings: Settings) -> str:
    """Configure the tracer provider. Returns a human-readable status."""
    global _configured
    if _configured:
        return "already configured"
    if not settings.otel_exporter_otlp_endpoint:
        _configured = True
        return "disabled (OTEL_EXPORTER_OTLP_ENDPOINT is not set)"

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
            )
        )
        trace.set_tracer_provider(provider)
        _configured = True
        return f"exporting to {settings.otel_exporter_otlp_endpoint}"
    except Exception as exc:
        _configured = True
        logger.warning("Tracing disabled: %s: %s", exc.__class__.__name__, exc)
        return f"disabled ({exc.__class__.__name__})"
