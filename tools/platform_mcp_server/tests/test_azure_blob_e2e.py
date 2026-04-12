"""
Optional smoke tests: execute_platform_tool against a real Azure Storage container.

Create a private container in the Portal (e.g. sandhi / sandhi_ai_platform), then set:

  AZURE_BLOB_E2E_CONNECTION_STRING  — full connection string (recommended), or
  AZURE_BLOB_E2E_ACCOUNT_URL        — https://<account>.blob.core.windows.net
  plus AZURE_BLOB_E2E_CONTAINER

With account URL only, auth uses DefaultAzureCredential (managed identity, Azure CLI, etc.).

Values may also be read from the repo root `.env` (same keys).

Run:
  pytest tests/test_azure_blob_e2e.py -m azure_blob_e2e -v
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from app import execute_platform_tool

pytestmark = pytest.mark.azure_blob_e2e

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


def _dotenv_value(name: str) -> str:
    direct = (os.environ.get(name) or "").strip()
    if direct:
        return direct
    if not _ENV_FILE.is_file():
        return ""
    for raw in _ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() != name:
            continue
        return val.strip().strip('"').strip("'")
    return ""


def _e2e_config() -> dict | None:
    container = _dotenv_value("AZURE_BLOB_E2E_CONTAINER")
    conn = _dotenv_value("AZURE_BLOB_E2E_CONNECTION_STRING")
    url = _dotenv_value("AZURE_BLOB_E2E_ACCOUNT_URL")
    if not container:
        return None
    if conn:
        return {"container": container, "connection_string": conn}
    if url:
        return {"container": container, "account_url": url}
    return None


def _assert_ok(text: str) -> None:
    assert text and isinstance(text, str), "empty tool output"
    assert not text.startswith("Error:"), text[:2000]


def test_e2e_azure_blob_put_get_list():
    try:
        import azure.storage.blob  # noqa: F401
    except Exception:
        pytest.skip("azure-storage-blob is not installed")

    cfg = _e2e_config()
    if not cfg:
        pytest.skip(
            "set AZURE_BLOB_E2E_CONTAINER and "
            "AZURE_BLOB_E2E_CONNECTION_STRING or AZURE_BLOB_E2E_ACCOUNT_URL"
        )

    run_id = uuid.uuid4().hex[:12]
    prefix = f"sandhi-ai-e2e/{run_id}/"
    blob_key = f"{prefix}hello.txt"
    payload = f"azure-blob-e2e-{run_id}"

    put_out = execute_platform_tool(
        "azure_blob",
        cfg,
        {"action": "put", "key": blob_key, "body": payload},
    )
    if isinstance(put_out, str) and put_out.startswith("Error:"):
        pytest.skip(f"Azure Blob put failed (container, auth, or network): {put_out[:400]}")
    _assert_ok(put_out)
    assert json.loads(put_out).get("status") == "ok"

    get_out = execute_platform_tool(
        "azure_blob",
        cfg,
        {"action": "get", "key": blob_key},
    )
    _assert_ok(get_out)
    assert get_out.strip() == payload

    list_out = execute_platform_tool(
        "azure_blob",
        cfg,
        {"action": "list", "key": prefix},
    )
    _assert_ok(list_out)
    blobs = json.loads(list_out).get("blobs") or []
    assert blob_key in blobs, f"expected {blob_key!r} in {blobs!r}"
