"""S3-compatible, Azure Blob, and GCS interactive execution."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Tuple

from execution_common import _resolve_s3_compatible_endpoint, _truncate_for_log

logger = logging.getLogger(__name__)

_PUT_BODY_MISSING_LAST_LOG: Dict[Tuple[str, str], float] = {}


def _required_s3_write_prefix(config: Dict[str, Any]) -> str:
    """Normalized prefix (no slashes); empty = no restriction."""
    p = (config.get("write_key_prefix") or os.environ.get("MCP_S3_WRITE_KEY_PREFIX") or "").strip()
    return p.strip("/")


def _s3_key_allowed_for_write(key: str, required_prefix: str) -> bool:
    if not required_prefix:
        return True
    k = key.strip().lstrip("/")
    p = required_prefix.strip().strip("/")
    return k == p or k.startswith(p + "/")


def execute_s3_family(
    tool_type: str,
    config: Dict[str, Any],
    arguments: Dict[str, Any],
) -> str:
    """S3 / MinIO / Ceph: list/get/put object."""
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        return "Error: boto3 is not installed"
    action = (arguments.get("action") or "get").strip().lower()
    key = (arguments.get("key") or "").strip()
    if not key:
        return "Error: key is required"
    write_prefix = _required_s3_write_prefix(config)
    if action in ("put", "write") and not _s3_key_allowed_for_write(key, write_prefix):
        return (
            "Error: key must be under the configured write prefix "
            f"'{write_prefix}/' (set write_key_prefix on the tool or MCP_S3_WRITE_KEY_PREFIX). "
            f"Refused key: {key!r}"
        )
    bucket = (config.get("bucket") or "").strip()
    if not bucket:
        return "Error: bucket not configured"
    endpoint = _resolve_s3_compatible_endpoint(tool_type, config)
    ak = (config.get("access_key") or config.get("access_key_id") or "").strip()
    sk = (config.get("secret_key") or config.get("secret_access_key") or "").strip()
    region = (config.get("region") or os.environ.get("S3_REGION") or "us-east-1").strip()
    kwargs = {}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        kwargs["region_name"] = region
    client = boto3.client("s3", **kwargs)
    put_hint = "n/a"
    if action in ("put", "write"):
        b = arguments.get("body")
        if b is None:
            b = arguments.get("content")
        if isinstance(b, str):
            put_hint = f"{len(b)} chars"
        elif isinstance(b, dict):
            put_hint = f"dict ~{len(json.dumps(b))} chars"
        elif b is not None:
            put_hint = "binary/non-str"
    _log = logger.debug if action in ("put", "write") and put_hint == "n/a" else logger.info
    _log(
        "MCP s3_family tool_type=%s action=%s endpoint=%s bucket=%s key=%s payload=%s",
        tool_type,
        action,
        endpoint or "(default)",
        bucket,
        _truncate_for_log(key, 400),
        put_hint,
    )
    try:
        if action == "list":
            prefix = key.rstrip("/") + "/" if not key.endswith("/") else key
            r = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=500)
            keys = [o.get("Key") for o in r.get("Contents") or []]
            return json.dumps({"keys": keys}, indent=2)
        if action in ("get", "read"):
            obj = client.get_object(Bucket=bucket, Key=key)
            data = obj["Body"].read()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"bytes_b64": __import__("base64").b64encode(data).decode("ascii")})
        if action in ("put", "write"):
            body = arguments.get("body")
            if body is None and arguments.get("content") is not None:
                body = arguments.get("content")
            if isinstance(body, dict):
                body = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            elif body is None:
                tkey = (bucket, key)
                now = time.monotonic()
                prev = _PUT_BODY_MISSING_LAST_LOG.get(tkey, 0.0)
                if now - prev >= 60.0:
                    _PUT_BODY_MISSING_LAST_LOG[tkey] = now
                    logger.warning(
                        "MCP s3_family put/write missing body or content (bucket=%s key=%s). "
                        "Repeats for this bucket+key within 60s are logged at debug only.",
                        bucket,
                        _truncate_for_log(key, 400),
                    )
                else:
                    logger.debug(
                        "MCP s3_family put/write missing body (repeat bucket=%s key=%s)",
                        bucket,
                        _truncate_for_log(key, 200),
                    )
                return (
                    "Error: body or content is required for put/write (interactive MinIO/S3). "
                    "For job outputs from workflow artifacts, use write_execution_mode=platform and "
                    "output_contract write_targets — do not call put with an empty body."
                )
            client.put_object(Bucket=bucket, Key=key, Body=body)
            return json.dumps({"status": "ok", "bucket": bucket, "key": key})
        return f"Error: unknown action {action}"
    except ClientError as e:
        logger.exception("S3 family error")
        return f"Error: {e}"
    except Exception as e:
        logger.exception("S3 family error")
        return f"Error: {e}"


def execute_azure_blob(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    action = (arguments.get("action") or "get").strip().lower()
    key = (arguments.get("key") or "").strip()
    if not key:
        return "Error: key is required"
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        return "Error: azure-storage-blob is not installed"
    account_url = (config.get("account_url") or "").strip()
    container = (config.get("container") or "").strip()
    conn = (config.get("connection_string") or "").strip()
    if not container:
        return "Error: container not configured"
    try:
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
        elif account_url:
            # Default credential chain when no connection string
            svc = BlobServiceClient(account_url=account_url)
        else:
            return "Error: account_url or connection_string required"
        cc = svc.get_container_client(container)
        blob = cc.get_blob_client(key)
        if action in ("get", "read"):
            data = blob.download_blob().readall()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"bytes_b64": __import__("base64").b64encode(data).decode("ascii")})
        if action in ("put", "write"):
            body = arguments.get("body") or arguments.get("content") or ""
            if isinstance(body, dict):
                body = json.dumps(body)
            blob.upload_blob(str(body).encode("utf-8") if not isinstance(body, bytes) else body, overwrite=True)
            return json.dumps({"status": "ok"})
        if action == "list":
            names = [b.name for b in cc.list_blobs(name_starts_with=key)]
            return json.dumps({"blobs": names}, indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        logger.exception("Azure blob error")
        return f"Error: {e}"


def execute_gcs(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    action = (arguments.get("action") or "get").strip().lower()
    key = (arguments.get("key") or "").strip()
    if not key:
        return "Error: key is required"
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
    except ImportError:
        return "Error: google-cloud-storage is not installed"
    project = (config.get("project_id") or "").strip()
    bucket_name = (config.get("bucket") or "").strip()
    if not bucket_name:
        return "Error: bucket not configured"
    creds_json = config.get("credentials_json")
    if creds_json:
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = storage.Client(project=project, credentials=creds)
    else:
        client = storage.Client(project=project or None)
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        if action in ("get", "read"):
            data = blob.download_as_bytes()
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return json.dumps({"bytes_b64": __import__("base64").b64encode(data).decode("ascii")})
        if action in ("put", "write"):
            body = arguments.get("body") or arguments.get("content") or ""
            if isinstance(body, dict):
                body = json.dumps(body)
            blob.upload_from_string(
                str(body) if not isinstance(body, (bytes, bytearray)) else body,
                content_type="application/json",
            )
            return json.dumps({"status": "ok"})
        if action == "list":
            names = [b.name for b in client.list_blobs(bucket_name, prefix=key)]
            return json.dumps({"objects": names}, indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        logger.exception("GCS error")
        return f"Error: {e}"
