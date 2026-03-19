"""Live integration test against the running MinIO container.

Requires: docker compose with MinIO up (OBJECT_STORAGE_BACKEND=s3).
Skipped automatically when S3 is not configured or unreachable.
"""

import os
import uuid

import pytest

S3_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "").strip()
S3_KEY = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
S3_SECRET = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
S3_BUCKET = os.environ.get("S3_BUCKET", "").strip()
BACKEND = os.environ.get("OBJECT_STORAGE_BACKEND", "local").strip().lower()

LIVE_S3 = BACKEND == "s3" and all([S3_ENDPOINT, S3_KEY, S3_SECRET, S3_BUCKET])

skip_unless_live_s3 = pytest.mark.skipif(
    not LIVE_S3,
    reason="Live S3 tests require OBJECT_STORAGE_BACKEND=s3 with valid credentials",
)


@skip_unless_live_s3
class TestLiveS3:
    """End-to-end tests against the real MinIO/S3 endpoint."""

    def _client(self):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_KEY,
            aws_secret_access_key=S3_SECRET,
            region_name="us-east-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def test_bucket_exists(self):
        client = self._client()
        resp = client.head_bucket(Bucket=S3_BUCKET)
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_put_get_delete_roundtrip(self):
        client = self._client()
        key = f"test/{uuid.uuid4().hex}"
        content = b"live integration test payload"

        client.put_object(Bucket=S3_BUCKET, Key=key, Body=content, ContentType="text/plain")

        resp = client.get_object(Bucket=S3_BUCKET, Key=key)
        assert resp["Body"].read() == content
        assert resp["ContentType"] == "text/plain"

        client.delete_object(Bucket=S3_BUCKET, Key=key)

        with pytest.raises(Exception):
            client.get_object(Bucket=S3_BUCKET, Key=key)

    def test_list_objects(self):
        client = self._client()
        key = f"test/{uuid.uuid4().hex}"
        client.put_object(Bucket=S3_BUCKET, Key=key, Body=b"list-test")

        resp = client.list_objects_v2(Bucket=S3_BUCKET, Prefix="test/")
        keys = [o["Key"] for o in resp.get("Contents", [])]
        assert key in keys

        client.delete_object(Bucket=S3_BUCKET, Key=key)

    def test_health_endpoint_reports_healthy(self):
        from services.job_file_storage import verify_s3_connectivity, _s3_client_cached, _ensure_bucket_ready

        _s3_client_cached.cache_clear()
        _ensure_bucket_ready.cache_clear()

        result = verify_s3_connectivity()
        assert result["ok"] is True
        assert S3_BUCKET in result["detail"]

    @pytest.mark.asyncio
    async def test_persist_and_download_via_service(self):
        from services.job_file_storage import (
            persist_file,
            download_s3_bytes,
            delete_file,
            _s3_client_cached,
            _ensure_bucket_ready,
        )

        _s3_client_cached.cache_clear()
        _ensure_bucket_ready.cache_clear()

        content = f"service-layer test {uuid.uuid4().hex}".encode()
        meta = await persist_file("live_test.txt", content, "text/plain", job_id=9999)

        assert meta["storage"] == "s3"
        assert meta["bucket"] == S3_BUCKET

        downloaded = download_s3_bytes(meta)
        assert downloaded == content

        await delete_file(meta)

        with pytest.raises(Exception):
            download_s3_bytes(meta)
