"""Shared BlobServiceClient construction for azure_blob MCP and artifact writes."""
from __future__ import annotations

from typing import Any, Dict


def blob_service_client_from_config(config: Dict[str, Any]):
    """
    Prefer connection_string; otherwise account_url with DefaultAzureCredential when
    azure-identity is installed (managed identity / Azure CLI / env-based auth).
    """
    from azure.storage.blob import BlobServiceClient

    conn = (config.get("connection_string") or "").strip()
    account_url = (config.get("account_url") or "").strip()
    if conn:
        return BlobServiceClient.from_connection_string(conn)
    if not account_url:
        raise ValueError("account_url or connection_string required")
    try:
        from azure.identity import DefaultAzureCredential

        return BlobServiceClient(account_url, credential=DefaultAzureCredential())
    except ImportError:
        return BlobServiceClient(account_url=account_url)
