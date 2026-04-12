import logging
import hashlib
from typing import Any, Dict, Optional

from core.config import settings

logger = logging.getLogger(__name__)

_PROM_REGISTRY = None
_PROM_COUNTER = None
_PROM_HIST = None
_PROM_TOOL_FAMILY: Any = None
_OTEL_METER = None
_OTEL_COUNTER = None
_OTEL_HIST = None


def _normalize_target_key(target_key: str) -> str:
    raw = str(target_key or "unknown")
    mode = str(getattr(settings, "MCP_GUARDRAILS_METRICS_TARGET_KEY_MODE", "raw") or "raw").strip().lower()
    max_len = int(getattr(settings, "MCP_GUARDRAILS_METRICS_TARGET_KEY_MAX_LEN", 120) or 120)
    if mode == "hash":
        return f"h:{hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()[:16]}"
    if mode == "normalized":
        # Keep source + tool suffix, collapse middle cardinality-heavy path details.
        parts = raw.split(":")
        if len(parts) >= 2:
            source = parts[0]
            tool = parts[-1]
            norm = f"{source}:*: {tool}".replace(": ", ":")
        else:
            norm = raw
        raw = norm
    if len(raw) > max(16, max_len):
        return raw[: max(16, max_len)]
    return raw


def _ensure_prom():
    global _PROM_REGISTRY, _PROM_COUNTER, _PROM_HIST, _PROM_TOOL_FAMILY
    try:
        from prometheus_client import CollectorRegistry, Counter, Histogram
    except Exception:
        return
    if _PROM_REGISTRY is None:
        _PROM_REGISTRY = CollectorRegistry()
    if _PROM_COUNTER is None:
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
    if _PROM_TOOL_FAMILY is None:
        _PROM_TOOL_FAMILY = Counter(
            "mcp_platform_tool_calls_total",
            "Platform MCP tool calls by inferred family (low-cardinality)",
            ["tool_family", "operation_class", "outcome"],
            registry=_PROM_REGISTRY,
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


def infer_mcp_tool_family(tool_name: Optional[str]) -> str:
    """Collapse platform_N_foo_bar into a small label set for Prometheus."""
    if not tool_name:
        return "unknown"
    t = str(tool_name).lower()
    for tag in (
        "minio",
        "ceph",
        "azure_blob",
        "gcs",
        "snowflake",
        "postgres",
        "mysql",
        "sqlserver",
        "databricks",
        "bigquery",
        "pinecone",
        "chroma",
        "weaviate",
        "qdrant",
        "elasticsearch",
        "pageindex",
        "slack",
        "teams",
        "smtp",
        "github",
        "notion",
        "rest_api",
        "filesystem",
        "vector_db",
    ):
        if tag in t:
            return tag
    if "s3" in t and "minio" not in t:
        return "s3"
    return "other"


def increment_mcp_tool_family_metric(
    *,
    tool_name: Optional[str],
    operation_class: str,
    outcome: str,
) -> None:
    """Outcome: success | guardrail_error | call_error (per attempt, matches histogram outcomes)."""
    if not bool(getattr(settings, "MCP_TOOL_FAMILY_METRICS_ENABLED", True)):
        return
    _ensure_prom()
    if _PROM_TOOL_FAMILY is None:
        return
    fam = infer_mcp_tool_family(tool_name)
    oc = str(operation_class or "read_like").strip().lower() or "read_like"
    oc = oc if oc in ("read_like", "write_like") else "read_like"
    out = str(outcome or "unknown").strip().lower() or "unknown"
    if out not in ("success", "guardrail_error", "call_error"):
        out = "unknown"
    _PROM_TOOL_FAMILY.labels(tool_family=fam, operation_class=oc, outcome=out).inc()


def increment_event(event: str, *, code: str = "none", operation_class: str = "read_like", target_key: str = "unknown") -> None:
    label_target = _normalize_target_key(target_key)
    ev = str(event or "unknown")[:64]
    cd = str(code or "none")[:128]
    _ensure_prom()
    _ensure_otlp()
    if _PROM_COUNTER is not None:
        _PROM_COUNTER.labels(event=ev, code=cd, operation_class=operation_class, target_key=label_target).inc()
    if _OTEL_COUNTER is not None:
        _OTEL_COUNTER.add(
            1,
            {
                "event": ev,
                "code": cd,
                "operation_class": operation_class,
                "target_key": label_target,
            },
        )


def observe_duration(seconds: float, *, operation_class: str, target_key: str, outcome: str) -> None:
    label_target = _normalize_target_key(target_key)
    oc = str(operation_class or "read_like").strip().lower() or "read_like"
    oc = oc if oc in ("read_like", "write_like") else "read_like"
    out = str(outcome or "unknown").strip().lower() or "unknown"
    if out not in ("success", "guardrail_error", "call_error"):
        out = "unknown"
    _ensure_prom()
    _ensure_otlp()
    if _PROM_HIST is not None:
        _PROM_HIST.labels(operation_class=oc, target_key=label_target, outcome=out).observe(max(0.0, float(seconds)))
    if _OTEL_HIST is not None:
        _OTEL_HIST.record(
            max(0.0, float(seconds)),
            {"operation_class": oc, "target_key": label_target, "outcome": out},
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
