"""Mocked branches for S3 family (put, errors), Azure Blob, and GCS."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution_object_storage import execute_azure_blob, execute_gcs, execute_s3_family

pytestmark = pytest.mark.unit


class TestS3FamilyPutAndErrors:
    def test_put_dict_body_encodes_json(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "minio",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "prefix/obj.json", "action": "put", "body": {"x": 1}},
        )
        assert json.loads(out)["status"] == "ok"
        mock_client.put_object.assert_called_once()
        body = mock_client.put_object.call_args.kwargs["Body"]
        assert b'"x"' in body

    def test_put_missing_body_error(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "k", "action": "put"},
        )
        assert "body or content is required" in out.lower()

    def test_client_error_mapped(self, monkeypatch):
        from botocore.exceptions import ClientError

        err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "m"}}, "GetObject")
        mock_client = MagicMock()
        mock_client.head_object.side_effect = err
        mock_client.get_object.side_effect = err
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "missing", "action": "get"},
        )
        assert "Error" in out

    def test_get_non_utf8_returns_b64(self, monkeypatch):
        mock_body = MagicMock()
        mock_body.read.return_value = b"\xff\xfe"
        mock_client = MagicMock()
        mock_client.head_object.return_value = {"ContentLength": 2}
        mock_client.get_object.return_value = {"Body": mock_body}
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "bin", "action": "get"},
        )
        data = json.loads(out)
        assert "bytes_b64" in data

    def test_list_respects_max_keys(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "", "action": "list", "max_keys": 120},
        )
        assert mock_client.list_objects_v2.call_args.kwargs["MaxKeys"] == 120

    def test_list_rejects_oversized_continuation_token(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "", "action": "list", "continuation_token": "z" * 20_000},
        )
        assert "continuation_token" in out.lower()
        mock_client.list_objects_v2.assert_not_called()

    def test_copy_prefix_same_bucket(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "src/a.txt", "Size": 8}],
            "IsTruncated": False,
        }
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = json.loads(
            execute_s3_family(
                "minio",
                {"bucket": "b", "access_key": "a", "secret_key": "s"},
                {
                    "action": "copy_prefix",
                    "source_prefix": "src/",
                    "dest_prefix": "dst/",
                },
            )
        )
        assert out["copied_count"] == 1
        assert out["copied_source_keys"] == ["src/a.txt"]
        assert out.get("transactional") is False
        assert "idempotency_and_resume" in out
        mock_client.copy_object.assert_called_once()
        kw = mock_client.copy_object.call_args.kwargs
        assert kw["Bucket"] == "b"
        assert kw["Key"] == "dst/a.txt"

    def test_copy_prefix_failure_returns_partial_json(self, monkeypatch):
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "src/a.txt", "Size": 4},
                {"Key": "src/b.txt", "Size": 4},
            ],
            "IsTruncated": False,
        }
        mock_client.copy_object.side_effect = [None, ClientError({"Error": {"Code": "AccessDenied"}}, "CopyObject")]
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = json.loads(
            execute_s3_family(
                "s3",
                {"bucket": "b", "access_key": "a", "secret_key": "s"},
                {
                    "action": "copy_prefix",
                    "source_prefix": "src/",
                    "dest_prefix": "dst/",
                },
            )
        )
        assert out["status"] == "copy_failed"
        assert out["partial_copy"] is True
        assert out["copied_count"] == 1
        assert out["failed_source_key"] == "src/b.txt"
        assert out.get("transactional") is False

    def test_copy_prefix_caps_skipped_keys_in_memory(self, monkeypatch):
        """Many oversize objects must not grow skipped_large without bound (heavy load)."""
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_SKIPPED_KEYS_TRACKED", "3")
        # Server clamps per-object cap to at least 1024 bytes; use larger objects so they are skipped.
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_BYTES_PER_OBJECT", "1024")
        contents = [{"Key": f"src/x{i}.txt", "Size": 4096} for i in range(10)]
        mock_client = MagicMock()
        mock_client.list_objects_v2.return_value = {
            "Contents": contents,
            "IsTruncated": False,
        }
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = json.loads(
            execute_s3_family(
                "s3",
                {"bucket": "b", "access_key": "a", "secret_key": "s"},
                {
                    "action": "copy_prefix",
                    "source_prefix": "src/",
                    "dest_prefix": "dst/",
                },
            )
        )
        assert out["status"] == "ok"
        assert out["skipped_over_max_bytes_count"] == 10
        assert out["skipped_over_max_bytes_untracked_count"] == 7
        assert len(out["skipped_over_max_bytes"]) == 3
        mock_client.copy_object.assert_not_called()

    def test_get_rejects_object_larger_than_max_read(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.head_object.return_value = {"ContentLength": 500 * 1024 * 1024}
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = json.loads(
            execute_s3_family(
                "s3",
                {"bucket": "b", "access_key": "a", "secret_key": "s"},
                {"key": "huge.bin", "action": "get", "max_read_bytes": 1024},
            )
        )
        assert out.get("error") == "object_too_large_for_full_read"

    def test_get_rejects_nul_in_key(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "a\x00b", "action": "get"},
        )
        assert "NUL" in out
        mock_client.head_object.assert_not_called()

    def test_get_rejects_read_offset_beyond_platform_limit(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {
                "key": "k",
                "action": "get",
                "read_offset": 5 * 1024**4,
                "read_length": 1,
            },
        )
        assert "platform limit" in out.lower()
        mock_client.head_object.assert_not_called()

    def test_get_ranged_returns_envelope_json(self, monkeypatch):
        mock_body = MagicMock()
        mock_body.read.return_value = b"hi"
        mock_client = MagicMock()
        mock_client.get_object.return_value = {
            "Body": mock_body,
            "ContentRange": "bytes 0-1/100",
        }
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = json.loads(
            execute_s3_family(
                "s3",
                {"bucket": "b", "access_key": "a", "secret_key": "s"},
                {"key": "f.txt", "action": "get", "read_offset": 0, "read_length": 2},
            )
        )
        assert out["text"] == "hi"
        assert out["total_size"] == 100
        assert out["is_partial"] is True
        mock_client.get_object.assert_called_once()
        assert mock_client.get_object.call_args.kwargs["Range"] == "bytes=0-1"


def _fake_azure_blob_module(svc: MagicMock):
    m = types.ModuleType("azure.storage.blob")
    m.BlobServiceClient = MagicMock(from_connection_string=MagicMock(return_value=svc))
    return m


def _install_azure_stub(monkeypatch, svc: MagicMock) -> None:
    """Parent packages must exist or ``import azure.storage.blob`` fails before tests run."""
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.storage", types.ModuleType("azure.storage"))
    monkeypatch.setitem(sys.modules, "azure.storage.blob", _fake_azure_blob_module(svc))


class _AzureItemPaged:
    """Minimal stand-in for azure.core.paging ItemPaged + by_page."""

    def __init__(self, names: list, next_token: str | None = None):
        self._names = names
        self.continuation_token = next_token
        self.by_page_kwargs: dict | None = None

    def by_page(self, continuation_token=None):
        self.by_page_kwargs = {"continuation_token": continuation_token}
        return self

    def __iter__(self):
        return iter([iter([SimpleNamespace(name=n) for n in self._names])])


class TestAzureBlobMocked:
    def test_get_utf8(self, monkeypatch):
        blob = MagicMock()
        blob.get_blob_properties.return_value = SimpleNamespace(size=5)
        blob.download_blob.return_value.readall.return_value = b"hello"
        cc = MagicMock()
        cc.get_blob_client.return_value = blob
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        _install_azure_stub(monkeypatch, svc)
        out = execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "f.txt", "action": "get"},
        )
        assert out == "hello"

    def test_put_and_list(self, monkeypatch):
        blob = MagicMock()
        cc = MagicMock()
        cc.get_blob_client.return_value = blob
        cc.list_blobs.return_value = _AzureItemPaged(["a", "b"], None)
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        _install_azure_stub(monkeypatch, svc)

        out_put = execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "k", "action": "put", "body": {"z": 1}},
        )
        assert json.loads(out_put)["status"] == "ok"

        out_list = execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "pre", "action": "list"},
        )
        parsed = json.loads(out_list)
        assert parsed["blobs"] == ["a", "b"]
        assert parsed.get("is_truncated") is False

    def test_list_strips_leading_slash_for_name_starts_with(self, monkeypatch):
        blob = MagicMock()
        cc = MagicMock()
        cc.get_blob_client.return_value = blob
        cc.list_blobs.return_value = _AzureItemPaged([], None)
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        _install_azure_stub(monkeypatch, svc)
        execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "/reports/summaries/", "action": "list"},
        )
        cc.list_blobs.assert_called_once()
        assert cc.list_blobs.call_args.kwargs["name_starts_with"] == "reports/summaries/"
        assert cc.list_blobs.call_args.kwargs.get("results_per_page") == 500

    def test_list_root_allows_empty_key(self, monkeypatch):
        cc = MagicMock()
        cc.list_blobs.return_value = _AzureItemPaged([], None)
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        _install_azure_stub(monkeypatch, svc)
        execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "", "action": "list"},
        )
        assert cc.list_blobs.call_args.kwargs["name_starts_with"] is None

    def test_list_truncation_and_continuation_token(self, monkeypatch):
        cc = MagicMock()
        paged = _AzureItemPaged(["x"], "nexttok")
        cc.list_blobs.return_value = paged
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        _install_azure_stub(monkeypatch, svc)
        out = json.loads(
            execute_azure_blob(
                {"connection_string": "cs", "container": "c"},
                {"key": "p/", "action": "list", "continuation_token": "prev"},
            )
        )
        assert out["blobs"] == ["x"]
        assert out["is_truncated"] is True
        assert out["next_continuation_token"] == "nexttok"
        assert paged.by_page_kwargs == {"continuation_token": "prev"}

    def test_missing_container(self, monkeypatch):
        svc = MagicMock()
        _install_azure_stub(monkeypatch, svc)
        out = execute_azure_blob({"connection_string": "cs"}, {"key": "k", "action": "get"})
        assert "container" in out.lower()


def _install_gcs_stubs(monkeypatch, client: MagicMock) -> None:
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
    storage_mod = types.ModuleType("google.cloud.storage")
    storage_mod.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
    monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
    sa_mod = types.ModuleType("google.oauth2.service_account")

    class _Creds:
        @staticmethod
        def from_service_account_info(_info):
            return MagicMock()

    sa_mod.Credentials = _Creds
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa_mod)


class TestGcsMocked:
    def test_get_and_put(self, monkeypatch):
        blob = MagicMock()
        blob.size = 2
        blob.reload = MagicMock()
        blob.download_as_bytes.return_value = b"ok"
        bucket = MagicMock()
        bucket.blob.return_value = blob
        client = MagicMock()
        client.bucket.return_value = bucket
        _install_gcs_stubs(monkeypatch, client)

        out = execute_gcs({"bucket": "B"}, {"key": "o", "action": "get"})
        assert out == "ok"

        out_put = execute_gcs(
            {"bucket": "B"},
            {"key": "o2", "action": "put", "content": "text"},
        )
        assert json.loads(out_put)["status"] == "ok"
        blob.upload_from_string.assert_called_once()

    def test_list_objects(self, monkeypatch):
        b1 = SimpleNamespace(name="x/1")

        class _Page:
            next_page_token = None

            def __iter__(self):
                return iter([b1])

        client = MagicMock()
        client.list_blobs.return_value = _Page()
        _install_gcs_stubs(monkeypatch, client)

        out = json.loads(execute_gcs({"bucket": "B"}, {"key": "x/", "action": "list"}))
        assert "x/1" in out["objects"]
        assert out["is_truncated"] is False

    def test_list_root_allows_empty_key(self, monkeypatch):
        class _Page:
            next_page_token = None

            def __iter__(self):
                return iter([])

        client = MagicMock()
        client.list_blobs.return_value = _Page()
        _install_gcs_stubs(monkeypatch, client)
        json.loads(execute_gcs({"bucket": "B"}, {"key": "", "action": "list"}))
        assert client.list_blobs.call_args.kwargs.get("prefix") is None

    def test_list_passes_page_token_and_reports_truncation(self, monkeypatch):
        b1 = SimpleNamespace(name="a")

        class _Page:
            next_page_token = "npt1"

            def __iter__(self):
                return iter([b1])

        client = MagicMock()
        client.list_blobs.return_value = _Page()
        _install_gcs_stubs(monkeypatch, client)
        out = json.loads(
            execute_gcs({"bucket": "B"}, {"key": "p/", "action": "list", "page_token": "prev"})
        )
        assert out["objects"] == ["a"]
        assert out["is_truncated"] is True
        assert out["next_page_token"] == "npt1"
        kw = client.list_blobs.call_args.kwargs
        assert kw["page_token"] == "prev"
        assert kw["max_results"] == 500

    def test_get_strips_bucket_name_prefix_from_key(self, monkeypatch):
        blob = MagicMock()
        blob.size = 2
        blob.reload = MagicMock()
        blob.download_as_bytes.return_value = b"ok"
        bucket = MagicMock()
        bucket.blob.return_value = blob
        client = MagicMock()
        client.bucket.return_value = bucket
        _install_gcs_stubs(monkeypatch, client)
        execute_gcs({"bucket": "sandhi"}, {"key": "sandhi/reports/job-counts/x.jsonl", "action": "get"})
        bucket.blob.assert_called_once_with("reports/job-counts/x.jsonl")
