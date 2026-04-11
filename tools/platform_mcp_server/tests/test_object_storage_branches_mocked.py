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

        mock_client = MagicMock()
        mock_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "m"}}, "GetObject"
        )
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
        mock_client.get_object.return_value = {"Body": mock_body}
        monkeypatch.setattr("boto3.client", lambda *a, **k: mock_client)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"key": "bin", "action": "get"},
        )
        data = json.loads(out)
        assert "bytes_b64" in data


def _fake_azure_blob_module(svc: MagicMock):
    m = types.ModuleType("azure.storage.blob")
    m.BlobServiceClient = MagicMock(from_connection_string=MagicMock(return_value=svc))
    return m


class TestAzureBlobMocked:
    def test_get_utf8(self, monkeypatch):
        blob = MagicMock()
        blob.download_blob.return_value.readall.return_value = b"hello"
        cc = MagicMock()
        cc.get_blob_client.return_value = blob
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        monkeypatch.setitem(sys.modules, "azure.storage.blob", _fake_azure_blob_module(svc))
        out = execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "f.txt", "action": "get"},
        )
        assert out == "hello"

    def test_put_and_list(self, monkeypatch):
        blob = MagicMock()
        cc = MagicMock()
        cc.get_blob_client.return_value = blob
        cc.list_blobs.return_value = [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
        svc = MagicMock()
        svc.get_container_client.return_value = cc
        monkeypatch.setitem(sys.modules, "azure.storage.blob", _fake_azure_blob_module(svc))

        out_put = execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "k", "action": "put", "body": {"z": 1}},
        )
        assert json.loads(out_put)["status"] == "ok"

        out_list = execute_azure_blob(
            {"connection_string": "cs", "container": "c"},
            {"key": "pre", "action": "list"},
        )
        names = json.loads(out_list)["blobs"]
        assert names == ["a", "b"]

    def test_missing_container(self, monkeypatch):
        svc = MagicMock()
        monkeypatch.setitem(sys.modules, "azure.storage.blob", _fake_azure_blob_module(svc))
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
        client = MagicMock()
        client.list_blobs.return_value = [b1]
        _install_gcs_stubs(monkeypatch, client)

        out = execute_gcs({"bucket": "B"}, {"key": "x/", "action": "list"})
        assert "x/1" in out
