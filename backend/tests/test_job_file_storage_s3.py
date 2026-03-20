"""Unit tests for S3 code paths in services/job_file_storage.py.

Complements test_job_file_storage.py (which covers the local backend) by
exercising every S3 branch using a mocked boto3 client -- no real endpoint
needed.
"""

import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from services.job_file_storage import (
    _is_s3_backend,
    _require_s3_settings,
    _extract_error_code,
    _ensure_bucket_ready,
    _s3_client_cached,
    verify_s3_connectivity,
    persist_file,
    delete_file,
    delete_file_sync,
    download_s3_bytes,
    open_s3_download_stream,
    materialize_to_temp_path,
    cleanup_temp_path,
    _wait_for_object_visibility,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s3_settings(mp, **overrides):
    """Apply standard S3 env settings via monkeypatch on the settings object."""
    from core.config import settings

    defaults = {
        "OBJECT_STORAGE_BACKEND": "s3",
        "S3_ENDPOINT_URL": "http://minio:9000",
        "S3_ACCESS_KEY_ID": "testkey",
        "S3_SECRET_ACCESS_KEY": "testsecret",
        "S3_BUCKET": "test-bucket",
        "S3_REGION": "us-east-1",
        "S3_ADDRESSING_STYLE": "path",
        "S3_AUTO_CREATE_BUCKET": False,
        "S3_SIGNATURE_VERSION": "s3v4",
        "S3_CONNECT_TIMEOUT_SECONDS": 5,
        "S3_READ_TIMEOUT_SECONDS": 60,
        "S3_MAX_POOL_CONNECTIONS": 10,
        "S3_RETRY_MODE": "standard",
        "S3_MAX_ATTEMPTS": 3,
        "S3_TCP_KEEPALIVE": True,
        "S3_OPERATION_RETRY_ATTEMPTS": 4,
        "S3_OPERATION_RETRY_BASE_DELAY_SECONDS": 0.2,
        "S3_OPERATION_RETRY_MAX_DELAY_SECONDS": 2.0,
        "S3_OPERATION_RETRY_JITTER_SECONDS": 0.1,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        mp.setattr(settings, k, v)


def _make_mock_client():
    """Return a MagicMock that behaves like a minimal boto3 S3 client."""
    client = MagicMock()
    client.exceptions = SimpleNamespace(
        NoSuchBucket=type("NoSuchBucket", (Exception,), {}),
    )
    return client


def _clear_lru_caches():
    """Clear lru_cache on module-level cached functions so mocks take effect."""
    _s3_client_cached.cache_clear()
    _ensure_bucket_ready.cache_clear()


@pytest.fixture(autouse=True)
def _reset_s3_caches():
    """Clear S3 LRU caches before and after every test in this module."""
    _clear_lru_caches()
    yield
    _clear_lru_caches()


# ---------------------------------------------------------------------------
# _require_s3_settings
# ---------------------------------------------------------------------------

class TestRequireS3Settings:
    def test_returns_bucket_when_valid(self, monkeypatch):
        _s3_settings(monkeypatch)
        assert _require_s3_settings() == "test-bucket"

    def test_raises_when_bucket_missing(self, monkeypatch):
        _s3_settings(monkeypatch, S3_BUCKET="")
        with pytest.raises(RuntimeError, match="S3_BUCKET"):
            _require_s3_settings()

    def test_raises_when_endpoint_missing(self, monkeypatch):
        _s3_settings(monkeypatch, S3_ENDPOINT_URL="")
        with pytest.raises(RuntimeError, match="S3_ENDPOINT_URL"):
            _require_s3_settings()

    def test_raises_when_access_key_missing(self, monkeypatch):
        _s3_settings(monkeypatch, S3_ACCESS_KEY_ID="")
        with pytest.raises(RuntimeError, match="S3_ACCESS_KEY_ID"):
            _require_s3_settings()

    def test_raises_when_secret_key_missing(self, monkeypatch):
        _s3_settings(monkeypatch, S3_SECRET_ACCESS_KEY="")
        with pytest.raises(RuntimeError, match="S3_SECRET_ACCESS_KEY"):
            _require_s3_settings()


# ---------------------------------------------------------------------------
# _extract_error_code
# ---------------------------------------------------------------------------

class TestExtractErrorCode:
    def test_extracts_from_error_dict(self):
        exc = Exception()
        exc.response = {"Error": {"Code": "NoSuchBucket"}}
        assert _extract_error_code(exc) == "NoSuchBucket"

    def test_extracts_http_status(self):
        exc = Exception()
        exc.response = {"ResponseMetadata": {"HTTPStatusCode": 403}}
        assert _extract_error_code(exc) == "403"

    def test_empty_when_no_response(self):
        assert _extract_error_code(Exception()) == ""

    def test_prefers_error_code_over_http_status(self):
        exc = Exception()
        exc.response = {
            "Error": {"Code": "InvalidAccessKeyId"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }
        assert _extract_error_code(exc) == "InvalidAccessKeyId"


# ---------------------------------------------------------------------------
# _ensure_bucket_ready
# ---------------------------------------------------------------------------

class TestEnsureBucketReady:
    def test_returns_bucket_when_exists(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            assert _ensure_bucket_ready() == "test-bucket"
        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_auto_creates_bucket_when_missing_and_enabled(self, monkeypatch):
        _s3_settings(monkeypatch, S3_AUTO_CREATE_BUCKET=True)
        mock_client = _make_mock_client()
        mock_client.head_bucket.side_effect = mock_client.exceptions.NoSuchBucket()

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            result = _ensure_bucket_ready()
        assert result == "test-bucket"
        mock_client.create_bucket.assert_called_once_with(Bucket="test-bucket")

    def test_auto_creates_with_location_for_non_us_east(self, monkeypatch):
        _s3_settings(monkeypatch, S3_AUTO_CREATE_BUCKET=True, S3_REGION="eu-west-1")
        mock_client = _make_mock_client()
        mock_client.head_bucket.side_effect = mock_client.exceptions.NoSuchBucket()

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            _ensure_bucket_ready()
        mock_client.create_bucket.assert_called_once_with(
            Bucket="test-bucket",
            CreateBucketConfiguration={"LocationConstraint": "eu-west-1"},
        )

    def test_raises_when_missing_and_auto_create_off(self, monkeypatch):
        _s3_settings(monkeypatch, S3_AUTO_CREATE_BUCKET=False)
        mock_client = _make_mock_client()
        mock_client.head_bucket.side_effect = mock_client.exceptions.NoSuchBucket()

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            with pytest.raises(RuntimeError, match="does not exist"):
                _ensure_bucket_ready()

    def test_raises_on_auth_failure_403(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        exc = Exception("forbidden")
        exc.response = {"Error": {"Code": "403"}}
        mock_client.head_bucket.side_effect = exc

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            with pytest.raises(RuntimeError, match="authentication failed"):
                _ensure_bucket_ready()

    def test_raises_on_invalid_access_key(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        exc = Exception("bad key")
        exc.response = {"Error": {"Code": "InvalidAccessKeyId"}}
        mock_client.head_bucket.side_effect = exc

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            with pytest.raises(RuntimeError, match="authentication failed"):
                _ensure_bucket_ready()

    def test_raises_on_unknown_error(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        exc = Exception("connection refused")
        exc.response = {"ResponseMetadata": {"HTTPStatusCode": 500}}
        mock_client.head_bucket.side_effect = exc

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            with pytest.raises(RuntimeError, match="connectivity check failed"):
                _ensure_bucket_ready()

    def test_treats_404_client_error_as_missing_bucket(self, monkeypatch):
        _s3_settings(monkeypatch, S3_AUTO_CREATE_BUCKET=True)
        mock_client = _make_mock_client()
        exc = Exception("not found")
        exc.response = {"Error": {"Code": "404"}}
        mock_client.head_bucket.side_effect = exc

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _clear_lru_caches()
            result = _ensure_bucket_ready()
        assert result == "test-bucket"
        mock_client.create_bucket.assert_called_once()


# ---------------------------------------------------------------------------
# verify_s3_connectivity (S3 mode)
# ---------------------------------------------------------------------------

class TestVerifyS3ConnectivityS3:
    def test_success(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            result = verify_s3_connectivity()
        assert result["ok"] is True
        assert "test-bucket" in result["detail"]

    def test_config_error(self, monkeypatch):
        _s3_settings(monkeypatch, S3_BUCKET="")
        result = verify_s3_connectivity()
        assert result["ok"] is False
        assert "config" in result["detail"].lower()

    def test_connection_failure(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        exc = Exception("timeout")
        exc.response = {"ResponseMetadata": {"HTTPStatusCode": 500}}
        mock_client.head_bucket.side_effect = exc

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            result = verify_s3_connectivity()
        assert result["ok"] is False
        assert "connectivity" in result["detail"].lower()

    def test_unexpected_exception(self, monkeypatch):
        _s3_settings(monkeypatch)

        with patch("services.job_file_storage._ensure_bucket_ready", side_effect=TypeError("boom")):
            result = verify_s3_connectivity()
        assert result["ok"] is False
        assert "unexpected" in result["detail"].lower()


# ---------------------------------------------------------------------------
# persist_file (S3 path)
# ---------------------------------------------------------------------------

class TestPersistFileS3:
    @pytest.mark.asyncio
    async def test_uploads_to_s3_and_returns_metadata(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            meta = await persist_file("report.pdf", b"PDF_CONTENT", "application/pdf", job_id=42)

        assert meta["name"] == "report.pdf"
        assert meta["type"] == "application/pdf"
        assert meta["size"] == len(b"PDF_CONTENT")
        assert meta["storage"] == "s3"
        assert meta["bucket"] == "test-bucket"
        assert meta["key"].startswith("jobs/42/")
        assert meta["key"].endswith("_report.pdf")
        assert "id" in meta

        mock_client.put_object.assert_called_once()
        # Upload waits for object to become visible before returning.
        mock_client.head_object.assert_called_once_with(Bucket="test-bucket", Key=meta["key"])
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Body"] == b"PDF_CONTENT"
        assert call_kwargs["ContentType"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_uses_unassigned_when_no_job_id(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            meta = await persist_file("f.txt", b"data", "text/plain")

        assert "jobs/unassigned/" in meta["key"]

    @pytest.mark.asyncio
    async def test_defaults_content_type_to_octet_stream(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            meta = await persist_file("blob", b"x", None, job_id=1)

        assert meta["type"] == "application/octet-stream"
        assert mock_client.put_object.call_args[1]["ContentType"] == "application/octet-stream"


# ---------------------------------------------------------------------------
# download_s3_bytes
# ---------------------------------------------------------------------------

class TestDownloadS3Bytes:
    def test_downloads_object(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_body = MagicMock()
        mock_body.read.return_value = b"file-content-here"
        mock_client.get_object.return_value = {"Body": mock_body}

        meta = {"storage": "s3", "bucket": "test-bucket", "key": "jobs/1/abc_f.txt"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            data = download_s3_bytes(meta)

        assert data == b"file-content-here"
        mock_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="jobs/1/abc_f.txt")

    def test_raises_on_invalid_metadata(self):
        with pytest.raises(ValueError, match="Invalid S3 file metadata"):
            download_s3_bytes({})

    def test_raises_when_storage_not_s3(self):
        with pytest.raises(ValueError):
            download_s3_bytes({"storage": "local", "bucket": "b", "key": "k"})

    def test_raises_when_key_missing(self):
        with pytest.raises(ValueError):
            download_s3_bytes({"storage": "s3", "bucket": "b"})


# ---------------------------------------------------------------------------
# open_s3_download_stream
# ---------------------------------------------------------------------------

class TestOpenS3DownloadStream:
    def test_returns_stream_metadata(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_body = MagicMock()
        mock_client.get_object.return_value = {
            "Body": mock_body,
            "ContentType": "application/pdf",
            "ContentLength": 1024,
        }

        meta = {"storage": "s3", "bucket": "b", "key": "k"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            body, ct, cl = open_s3_download_stream(meta)

        assert body is mock_body
        assert ct == "application/pdf"
        assert cl == 1024

    def test_defaults_content_type(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.get_object.return_value = {"Body": MagicMock()}

        meta = {"storage": "s3", "bucket": "b", "key": "k"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _, ct, cl = open_s3_download_stream(meta)

        assert ct == "application/octet-stream"
        assert cl is None

    def test_raises_on_invalid_metadata(self):
        with pytest.raises(ValueError, match="Invalid S3 file metadata"):
            open_s3_download_stream({"storage": "s3"})


# ---------------------------------------------------------------------------
# delete_file / delete_file_sync (S3 path)
# ---------------------------------------------------------------------------

class TestDeleteFileS3:
    @pytest.mark.asyncio
    async def test_deletes_s3_object(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()

        meta = {"storage": "s3", "bucket": "test-bucket", "key": "jobs/1/abc.txt"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            await delete_file(meta)

        mock_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="jobs/1/abc.txt")

    @pytest.mark.asyncio
    async def test_logs_warning_on_s3_error(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.delete_object.side_effect = Exception("s3 error")

        meta = {"storage": "s3", "bucket": "b", "key": "k"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            await delete_file(meta)  # should not raise

    def test_sync_deletes_s3_object(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()

        meta = {"storage": "s3", "bucket": "test-bucket", "key": "jobs/1/abc.txt"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            delete_file_sync(meta)

        mock_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="jobs/1/abc.txt")

    def test_sync_logs_warning_on_s3_error(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.delete_object.side_effect = Exception("s3 error")

        meta = {"storage": "s3", "bucket": "b", "key": "k"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            delete_file_sync(meta)  # should not raise


# ---------------------------------------------------------------------------
# materialize_to_temp_path (S3 path)
# ---------------------------------------------------------------------------

class TestMaterializeToTempPathS3:
    @pytest.mark.asyncio
    async def test_downloads_to_temp_file(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_body = MagicMock()
        mock_body.read.return_value = b"s3 file content"
        mock_client.get_object.return_value = {"Body": mock_body}

        meta = {"storage": "s3", "bucket": "b", "key": "k", "name": "doc.txt"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            path = await materialize_to_temp_path(meta)

        assert Path(path).exists()
        assert Path(path).read_bytes() == b"s3 file content"
        assert path.endswith(".txt")

        Path(path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_preserves_extension(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_body = MagicMock()
        mock_body.read.return_value = b"pdf data"
        mock_client.get_object.return_value = {"Body": mock_body}

        meta = {"storage": "s3", "bucket": "b", "key": "k", "name": "report.pdf"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            path = await materialize_to_temp_path(meta)

        assert path.endswith(".pdf")
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# _s3_client (construction smoke test)
# ---------------------------------------------------------------------------

class TestS3ClientConstruction:
    def test_builds_client_with_correct_config(self, monkeypatch):
        _s3_settings(monkeypatch)
        _clear_lru_caches()

        import boto3 as _boto3
        mock_boto3 = MagicMock()
        monkeypatch.setattr(_boto3, "client", mock_boto3.client)

        with patch.dict("sys.modules", {"boto3": mock_boto3}):
            from services.job_file_storage import _s3_client
            _s3_client()

        mock_boto3.client.assert_called_once()
        call_kwargs = mock_boto3.client.call_args
        assert call_kwargs[1]["endpoint_url"] == "http://minio:9000"
        assert call_kwargs[1]["aws_access_key_id"] == "testkey"
        assert call_kwargs[1]["aws_secret_access_key"] == "testsecret"
        assert call_kwargs[1]["region_name"] == "us-east-1"


# ---------------------------------------------------------------------------
# Integration-style test: full upload → download → delete round-trip (mocked)
# ---------------------------------------------------------------------------

class TestS3RoundTrip:
    @pytest.mark.asyncio
    async def test_upload_download_delete(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}

        content = b"Round-trip test content for S3 storage."
        stored_objects = {}

        def fake_put(Bucket, Key, Body, ContentType):
            stored_objects[Key] = {"body": Body, "ct": ContentType}

        def fake_get(Bucket, Key):
            obj = stored_objects[Key]
            body = MagicMock()
            body.read.return_value = obj["body"]
            return {"Body": body, "ContentType": obj["ct"], "ContentLength": len(obj["body"])}

        def fake_delete(Bucket, Key):
            stored_objects.pop(Key, None)

        mock_client.put_object.side_effect = fake_put
        mock_client.get_object.side_effect = fake_get
        mock_client.delete_object.side_effect = fake_delete

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            meta = await persist_file("roundtrip.txt", content, "text/plain", job_id=99)
            assert meta["storage"] == "s3"
            assert len(stored_objects) == 1

            downloaded = download_s3_bytes(meta)
            assert downloaded == content

            body, ct, cl = open_s3_download_stream(meta)
            assert body.read() == content
            assert ct == "text/plain"
            assert cl == len(content)

            await delete_file(meta)
            assert len(stored_objects) == 0


class TestWaitForObjectVisibility:
    def test_wait_retries_until_visible(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        attempts = {"n": 0}

        def fake_head_object(Bucket, Key):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise Exception("not yet visible")
            return {}

        mock_client.head_object.side_effect = fake_head_object
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            _wait_for_object_visibility("b", "k", attempts=5, delay_seconds=0.001)

        assert attempts["n"] == 3

    def test_wait_raises_after_exhausted_retries(self, monkeypatch):
        _s3_settings(monkeypatch)
        mock_client = _make_mock_client()
        mock_client.head_object.side_effect = Exception("still missing")
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            with pytest.raises(RuntimeError, match="not yet visible"):
                _wait_for_object_visibility("b", "k", attempts=3, delay_seconds=0.001)


class TestS3OperationRetries:
    @pytest.mark.asyncio
    async def test_upload_retries_on_transient_error(self, monkeypatch):
        _s3_settings(
            monkeypatch,
            S3_OPERATION_RETRY_ATTEMPTS=3,
            S3_OPERATION_RETRY_BASE_DELAY_SECONDS=0.001,
            S3_OPERATION_RETRY_MAX_DELAY_SECONDS=0.001,
            S3_OPERATION_RETRY_JITTER_SECONDS=0.0,
        )
        mock_client = _make_mock_client()
        mock_client.head_bucket.return_value = {}
        mock_client.head_object.return_value = {}
        mock_client.put_object.side_effect = [Exception("timeout"), {}]

        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client), \
             patch("services.job_file_storage.time.sleep", return_value=None):
            meta = await persist_file("retry.txt", b"abc", "text/plain", job_id=1)

        assert meta["storage"] == "s3"
        assert mock_client.put_object.call_count == 2

    def test_download_retries_on_transient_error(self, monkeypatch):
        _s3_settings(
            monkeypatch,
            S3_OPERATION_RETRY_ATTEMPTS=3,
            S3_OPERATION_RETRY_BASE_DELAY_SECONDS=0.001,
            S3_OPERATION_RETRY_MAX_DELAY_SECONDS=0.001,
            S3_OPERATION_RETRY_JITTER_SECONDS=0.0,
        )
        mock_client = _make_mock_client()
        body = MagicMock()
        body.read.return_value = b"data"
        mock_client.get_object.side_effect = [Exception("connection reset"), {"Body": body}]

        meta = {"storage": "s3", "bucket": "test-bucket", "key": "jobs/1/f.txt"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client), \
             patch("services.job_file_storage.time.sleep", return_value=None):
            out = download_s3_bytes(meta)

        assert out == b"data"
        assert mock_client.get_object.call_count == 2

    def test_download_does_not_retry_non_transient_auth_error(self, monkeypatch):
        _s3_settings(
            monkeypatch,
            S3_OPERATION_RETRY_ATTEMPTS=5,
            S3_OPERATION_RETRY_BASE_DELAY_SECONDS=0.001,
            S3_OPERATION_RETRY_MAX_DELAY_SECONDS=0.001,
            S3_OPERATION_RETRY_JITTER_SECONDS=0.0,
        )
        mock_client = _make_mock_client()
        exc = Exception("invalid auth")
        exc.response = {"Error": {"Code": "InvalidAccessKeyId"}}
        mock_client.get_object.side_effect = exc

        meta = {"storage": "s3", "bucket": "test-bucket", "key": "jobs/1/f.txt"}
        with patch("services.job_file_storage._s3_client_cached", return_value=mock_client):
            with pytest.raises(Exception):
                download_s3_bytes(meta)

        assert mock_client.get_object.call_count == 1
