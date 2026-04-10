import logging
from typing import Dict, Optional

from core.config import settings

logger = logging.getLogger(__name__)

_PROM_REGISTRY = None
_PROM_COUNTER = None
_PROM_HIST = None
_OTEL_METER = None
_OTEL_COUNTER = None
_OTEL_HIST = None


def _ensure_prom():
    global _PROM_REGISTRY, _PROM_COUNTER, _PROM_HIST
    if _PROM_COUNTER is not None and _PROM_HIST is not None:
        return
    try:
        from prometheus_client import CollectorRegistry, Counter, Histogram
    except Exception:
        return
    if _PROM_REGISTRY is None:
        _PROM_REGISTRY = CollectorRegistry()
    _PROM_COUNTER = Counter(
        "mcp_guardrail_events_total",
        "MCP guardrail events",
        ["event", "code", "operation_class", "target_key"],
        registry=_PROM_REGISTRY,
    )
    _PROM_HIST = Histogram(
        "mcp_guardrail_call_duration_seconds",
        "MCP guardrail call duration seconds",
        ["operation_class", "target_key", "outcome"],
        registry=_PROM_REGISTRY,
        buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60),
    )


def _ensure_otlp():
    global _OTEL_METER, _OTEL_COUNTER, _OTEL_HIST
    if _OTEL_COUNTER is not None and _OTEL_HIST is not None:
        return
    if not bool(getattr(settings, "MCP_GUARDRAILS_OTLP_ENABLED", False)):
        return
    endpoint = (getattr(settings, "MCP_GUARDRAILS_OTLP_ENDPOINT", None) or "").strip()
    if not endpoint:
        return
    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    except Exception:
        logger.warning("mcp_metrics: OTLP dependencies unavailable; skipping OTLP metrics")
        return
    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=bool(getattr(settings, "MCP_GUARDRAILS_OTLP_INSECURE", True)))
    reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    _OTEL_METER = metrics.get_meter("sandhi.mcp.guardrails")
    _OTEL_COUNTER = _OTEL_METER.create_counter("mcp_guardrail_events_total")
    _OTEL_HIST = _OTEL_METER.create_histogram("mcp_guardrail_call_duration_seconds", unit="s")


def increment_event(event: str, *, code: str = "none", operation_class: str = "read_like", target_key: str = "unknown") -> None:
    _ensure_prom()
    _ensure_otlp()
    if _PROM_COUNTER is not None:
        _PROM_COUNTER.labels(event=event, code=code, operation_class=operation_class, target_key=target_key).inc()
    if _OTEL_COUNTER is not None:
        _OTEL_COUNTER.add(
            1,
            {
                "event": event,
                "code": code,
                "operation_class": operation_class,
                "target_key": target_key,
            },
        )


def observe_duration(seconds: float, *, operation_class: str, target_key: str, outcome: str) -> None:
    _ensure_prom()
    _ensure_otlp()
    if _PROM_HIST is not None:
        _PROM_HIST.labels(operation_class=operation_class, target_key=target_key, outcome=outcome).observe(max(0.0, float(seconds)))
    if _OTEL_HIST is not None:
        _OTEL_HIST.record(
            max(0.0, float(seconds)),
            {"operation_class": operation_class, "target_key": target_key, "outcome": outcome},
        )


def render_prometheus() -> Optional[bytes]:
    _ensure_prom()
    if _PROM_REGISTRY is None:
        return None
    try:
        from prometheus_client import generate_latest
    except Exception:
        return None
    return generate_latest(_PROM_REGISTRY)
