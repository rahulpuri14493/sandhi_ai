"""Unit tests for configuration."""
import pytest

from core.config import settings


def test_settings_has_required_attributes():
    """Settings object has all required configuration attributes."""
    assert hasattr(settings, "DATABASE_URL")
    assert hasattr(settings, "SECRET_KEY")
    assert hasattr(settings, "ALGORITHM")
    assert hasattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES")
    assert hasattr(settings, "PLATFORM_COMMISSION_RATE")
    assert hasattr(settings, "MCP_TOOL_MAX_ARGUMENT_BYTES")
    assert hasattr(settings, "MCP_TOOL_DEFAULT_TIMEOUT_SECONDS")
    assert hasattr(settings, "MCP_TOOL_MAX_TIMEOUT_SECONDS")
    assert hasattr(settings, "MCP_WRITE_OPERATION_MAX_ATTEMPTS")
    assert hasattr(settings, "MCP_WRITE_OPERATION_RETRY_BASE_DELAY_SECONDS")
    assert hasattr(settings, "MCP_WRITE_OPERATION_RETRY_MAX_DELAY_SECONDS")
    assert hasattr(settings, "MCP_WRITE_OPERATION_RETRY_JITTER_SECONDS")
    assert hasattr(settings, "JOB_EXECUTION_BACKEND")
    assert hasattr(settings, "JOB_EXECUTION_STRICT_QUEUE")
    assert hasattr(settings, "CELERY_BROKER_URL")
    assert hasattr(settings, "CELERY_RESULT_BACKEND")
    assert hasattr(settings, "CELERY_WORKER_AUTOSCALE_MAX")
    assert hasattr(settings, "CELERY_WORKER_AUTOSCALE_MIN")
    assert hasattr(settings, "CELERY_WORKER_CONCURRENCY")
    assert hasattr(settings, "CELERY_EXECUTE_MAX_RETRIES")
    assert hasattr(settings, "CELERY_EXECUTE_RETRY_BACKOFF_SECONDS")


def test_settings_has_s3_storage_attributes():
    """Settings object has all S3/MinIO storage configuration attributes."""
    s3_attrs = [
        "OBJECT_STORAGE_BACKEND",
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_BUCKET",
        "S3_REGION",
        "S3_ADDRESSING_STYLE",
        "S3_CONNECT_TIMEOUT_SECONDS",
        "S3_READ_TIMEOUT_SECONDS",
        "S3_MAX_POOL_CONNECTIONS",
        "S3_RETRY_MODE",
        "S3_MAX_ATTEMPTS",
        "S3_AUTO_CREATE_BUCKET",
        "S3_SIGNATURE_VERSION",
        "S3_TCP_KEEPALIVE",
        "JOB_UPLOAD_MAX_FILE_BYTES",
        "ZIP_EXTRACT_RETRY_ATTEMPTS",
        "ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS",
        "ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS",
        "ZIP_EXTRACT_RETRY_JITTER_SECONDS",
    ]
    for attr in s3_attrs:
        assert hasattr(settings, attr), f"Missing config attribute: {attr}"


def test_settings_s3_defaults():
    """S3 settings have sensible defaults for local development."""
    assert settings.OBJECT_STORAGE_BACKEND in ("local", "s3")
    assert settings.S3_REGION  # non-empty default
    assert settings.S3_ADDRESSING_STYLE in ("path", "virtual")
    assert settings.S3_SIGNATURE_VERSION in ("s3v4", "s3")
    assert isinstance(settings.S3_TCP_KEEPALIVE, bool)
    assert settings.S3_CONNECT_TIMEOUT_SECONDS >= 1
    assert settings.S3_READ_TIMEOUT_SECONDS >= 1
    assert settings.S3_MAX_POOL_CONNECTIONS >= 10
    assert settings.S3_MAX_ATTEMPTS >= 1
    assert settings.JOB_UPLOAD_MAX_FILE_BYTES > 0
    assert settings.ZIP_EXTRACT_RETRY_ATTEMPTS >= 1
    assert settings.ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS >= 0
    assert settings.ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS >= settings.ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS


def test_settings_sensible_defaults():
    """Settings have sensible default values."""
    assert settings.ALGORITHM == "HS256"
    assert settings.PLATFORM_COMMISSION_RATE >= 0
    assert settings.PLATFORM_COMMISSION_RATE <= 1
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES > 0
    assert len(settings.SECRET_KEY) > 0
