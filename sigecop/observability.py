import os


_configured = False


def configure_observability():
    global _configured
    if _configured or os.getenv("OTEL_ENABLED", "false").lower() != "true":
        return

    from django.conf import settings
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True
