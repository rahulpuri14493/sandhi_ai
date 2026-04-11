"""Focused unit tests for services.mcp_metrics (coverage for new tool-family + label paths)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services import mcp_metrics


@pytest.fixture(autouse=True)
def _reset_mcp_metrics_globals(monkeypatch):
    """Isolate Prometheus singletons so tests do not share registry state."""
    monkeypatch.setattr(mcp_metrics, "_PROM_REGISTRY", None)
    monkeypatch.setattr(mcp_metrics, "_PROM_COUNTER", None)
    monkeypatch.setattr(mcp_metrics, "_PROM_HIST", None)
    monkeypatch.setattr(mcp_metrics, "_PROM_TOOL_FAMILY", None)
    monkeypatch.setattr(mcp_metrics, "_OTEL_METER", None)
    monkeypatch.setattr(mcp_metrics, "_OTEL_COUNTER", None)
    monkeypatch.setattr(mcp_metrics, "_OTEL_HIST", None)


def test_infer_mcp_tool_family_tags():
    assert mcp_metrics.infer_mcp_tool_family(None) == "unknown"
    assert mcp_metrics.infer_mcp_tool_family("platform_1_azure_blob_x") == "azure_blob"
    assert mcp_metrics.infer_mcp_tool_family("PLATFORM_2_S3_BUCKET") == "s3"
    assert mcp_metrics.infer_mcp_tool_family("platform_3_minio_x") == "minio"
    assert mcp_metrics.infer_mcp_tool_family("platform_4_other") == "other"


def test_normalize_target_key_hash_and_truncation(monkeypatch):
    monkeypatch.setattr("services.mcp_metrics.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MODE", "hash")
    out = mcp_metrics._normalize_target_key("platform:x:verylong")
    assert out.startswith("h:") and len(out) == 18

    monkeypatch.setattr("services.mcp_metrics.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MODE", "normalized")
    monkeypatch.setattr("services.mcp_metrics.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MAX_LEN", 200)
    n = mcp_metrics._normalize_target_key("src:mid:toolname")
    assert "toolname" in n

    monkeypatch.setattr("services.mcp_metrics.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MODE", "raw")
    monkeypatch.setattr("services.mcp_metrics.settings.MCP_GUARDRAILS_METRICS_TARGET_KEY_MAX_LEN", 20)
    t = mcp_metrics._normalize_target_key("x" * 100)
    assert len(t) == 20


def test_increment_mcp_tool_family_metric_increments_when_prom_available(monkeypatch):
    mock_counter = MagicMock()
    mock_labeled = MagicMock()
    mock_counter.labels.return_value = mock_labeled

    def _fake_ensure():
        mcp_metrics._PROM_TOOL_FAMILY = mock_counter

    monkeypatch.setattr(mcp_metrics, "_ensure_prom", _fake_ensure)
    monkeypatch.setattr("services.mcp_metrics.settings.MCP_TOOL_FAMILY_METRICS_ENABLED", True)
    mcp_metrics.increment_mcp_tool_family_metric(
        tool_name="platform_1_s3_x",
        operation_class="write_like",
        outcome="call_error",
    )
    mock_labeled.inc.assert_called_once()


def test_increment_mcp_tool_family_metric_disabled(monkeypatch):
    called = {"n": 0}

    def _ensure():
        called["n"] += 1

    monkeypatch.setattr(mcp_metrics, "_ensure_prom", _ensure)
    monkeypatch.setattr("services.mcp_metrics.settings.MCP_TOOL_FAMILY_METRICS_ENABLED", False)
    mcp_metrics.increment_mcp_tool_family_metric(tool_name="x", operation_class="read_like", outcome="success")
    assert called["n"] == 0


def test_increment_mcp_tool_family_metric_unknown_outcome_normalized(monkeypatch):
    mock_counter = MagicMock()
    mock_counter.labels.return_value = MagicMock()
    monkeypatch.setattr(mcp_metrics, "_PROM_TOOL_FAMILY", mock_counter)
    monkeypatch.setattr(mcp_metrics, "_ensure_prom", lambda: None)
    monkeypatch.setattr("services.mcp_metrics.settings.MCP_TOOL_FAMILY_METRICS_ENABLED", True)
    mcp_metrics.increment_mcp_tool_family_metric(
        tool_name="t",
        operation_class="invalid_class",
        outcome="not_a_real_outcome",
    )
    kwargs = mock_counter.labels.call_args.kwargs
    assert kwargs["operation_class"] == "read_like"
    assert kwargs["outcome"] == "unknown"


def test_observe_duration_clamps_negative(monkeypatch):
    mock_hist = MagicMock()
    mock_hist.labels.return_value = MagicMock()
    monkeypatch.setattr(mcp_metrics, "_PROM_HIST", mock_hist)
    monkeypatch.setattr(mcp_metrics, "_ensure_prom", lambda: None)
    monkeypatch.setattr(mcp_metrics, "_ensure_otlp", lambda: None)
    mcp_metrics.observe_duration(-5.0, operation_class="read_like", target_key="k", outcome="success")
    mock_hist.labels.return_value.observe.assert_called_once_with(0.0)


def test_render_prometheus_none_without_registry(monkeypatch):
    monkeypatch.setattr(mcp_metrics, "_PROM_REGISTRY", None)
    monkeypatch.setattr(mcp_metrics, "_ensure_prom", lambda: None)
    assert mcp_metrics.render_prometheus() is None
