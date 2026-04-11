"""Unit tests for azure_blob_client (connection string vs account_url)."""
from __future__ import annotations

import builtins
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytestmark = pytest.mark.unit


def _stub_azure_blob_module(monkeypatch, *, bsc_class):
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.storage", types.ModuleType("azure.storage"))
    m = types.ModuleType("azure.storage.blob")
    m.BlobServiceClient = bsc_class
    monkeypatch.setitem(sys.modules, "azure.storage.blob", m)


def test_blob_service_client_from_connection_string(monkeypatch):
    from azure_blob_client import blob_service_client_from_config

    svc = MagicMock()
    BSC = MagicMock(from_connection_string=MagicMock(return_value=svc))
    _stub_azure_blob_module(monkeypatch, bsc_class=BSC)
    out = blob_service_client_from_config({"connection_string": " DefaultConn "})
    assert out is svc
    BSC.from_connection_string.assert_called_once_with("DefaultConn")


def test_blob_service_client_account_url_with_default_credential(monkeypatch):
    from azure_blob_client import blob_service_client_from_config

    svc = MagicMock()
    BSC = MagicMock(return_value=svc)
    _stub_azure_blob_module(monkeypatch, bsc_class=BSC)
    id_mod = types.ModuleType("azure.identity")
    id_mod.DefaultAzureCredential = MagicMock(return_value="cred")
    monkeypatch.setitem(sys.modules, "azure.identity", id_mod)

    out = blob_service_client_from_config({"account_url": "https://acct.blob.core.windows.net"})
    assert out is svc
    BSC.assert_called_once_with("https://acct.blob.core.windows.net", credential="cred")


def test_blob_service_client_account_url_when_identity_import_fails(monkeypatch):
    from azure_blob_client import blob_service_client_from_config

    svc = MagicMock()
    BSC = MagicMock(return_value=svc)
    _stub_azure_blob_module(monkeypatch, bsc_class=BSC)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "azure.identity":
            raise ImportError("simulated missing azure-identity")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    out = blob_service_client_from_config({"account_url": "https://acct.blob.core.windows.net"})
    assert out is svc
    BSC.assert_called_once_with(account_url="https://acct.blob.core.windows.net")


def test_blob_service_client_requires_config(monkeypatch):
    from azure_blob_client import blob_service_client_from_config

    BSC = MagicMock()
    _stub_azure_blob_module(monkeypatch, bsc_class=BSC)
    with pytest.raises(ValueError, match="account_url or connection_string"):
        blob_service_client_from_config({})
