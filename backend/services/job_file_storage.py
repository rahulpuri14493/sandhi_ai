import asyncio
import logging
import tempfile
import uuid
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import lru_cache

from core.config import settings

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("uploads/jobs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

PRIVATE_FILE_KEYS = {"path", "bucket", "key", "storage"}


def _is_s3_backend() -> bool:
    return (settings.OBJECT_STORAGE_BACKEND or "local").strip().lower() == "s3"


def _file_ext(name: str) -> str:
    return Path(name).suffix.lower()


def sanitize_filename(name: str) -> str:
    """
    Keep a safe, filesystem/object-store-friendly filename.
    - strip directory traversal
    - normalize unsupported characters
    - cap length while preserving extension
    """
    base = Path(name or "").name.strip()
    if not base:
        return "document.bin"
    # Replace risky/special chars with underscore
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)
    base = re.sub(r"\s+", " ", base).strip()
    if base in {".", ".."}:
        base = "document.bin"
    ext = Path(base).suffix
    stem = Path(base).stem
    max_len = 180
    if len(base) > max_len:
        keep = max_len - len(ext)
        stem = stem[: max(1, keep)]
        base = f"{stem}{ext}"
    return base


def _build_metadata(*, file_id: str, name: str, content_type: str, size: int, path: Optional[str] = None, bucket: Optional[str] = None, key: Optional[str] = None, storage: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": file_id,
        "name": name,
        "type": content_type or "application/octet-stream",
        "size": int(size),
    }
    if path:
        out["path"] = path
    if storage:
        out["storage"] = storage
    if bucket:
        out["bucket"] = bucket
    if key:
        out["key"] = key
    return out


def redact_file_metadata(file_info: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in file_info.items() if k not in PRIVATE_FILE_KEYS}


def has_readable_source(file_info: Dict[str, Any]) -> bool:
    if file_info.get("path"):
        return True
    return bool(file_info.get("storage") == "s3" and file_info.get("bucket") and file_info.get("key"))


def _require_s3_settings() -> str:
    bucket = (settings.S3_BUCKET or "").strip()
    if not bucket:
        raise RuntimeError("S3_BUCKET is required when OBJECT_STORAGE_BACKEND=s3")
    if not (settings.S3_ENDPOINT_URL or "").strip():
        raise RuntimeError("S3_ENDPOINT_URL is required when OBJECT_STORAGE_BACKEND=s3")
    if not (settings.S3_ACCESS_KEY_ID or "").strip() or not (settings.S3_SECRET_ACCESS_KEY or "").strip():
        raise RuntimeError("S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY are required when OBJECT_STORAGE_BACKEND=s3")
    return bucket


def _s3_client():
    """
    Build a boto3 S3 client tuned for production Ceph RGW workloads.

    Key hardening knobs (all configurable via env):
      - TCP keepalive — prevents firewall/LB idle-connection resets
      - Signature v4  — required by modern RGW / S3 implementations
      - Connection pooling + retries with exponential back-off
      - Explicit connect & read timeouts
    """
    import boto3
    from botocore.config import Config

    retry_mode = (settings.S3_RETRY_MODE or "standard").strip() or "standard"
    sig_version = (settings.S3_SIGNATURE_VERSION or "s3v4").strip()

    return boto3.client(
        "s3",
        endpoint_url=(settings.S3_ENDPOINT_URL or "").strip() or None,
        aws_access_key_id=(settings.S3_ACCESS_KEY_ID or "").strip(),
        aws_secret_access_key=(settings.S3_SECRET_ACCESS_KEY or "").strip(),
        region_name=(settings.S3_REGION or "us-east-1").strip(),
        config=Config(
            signature_version=sig_version,
            s3={"addressing_style": (settings.S3_ADDRESSING_STYLE or "path").strip()},
            connect_timeout=max(1, int(settings.S3_CONNECT_TIMEOUT_SECONDS)),
            read_timeout=max(1, int(settings.S3_READ_TIMEOUT_SECONDS)),
            max_pool_connections=max(10, int(settings.S3_MAX_POOL_CONNECTIONS)),
            retries={"mode": retry_mode, "max_attempts": max(1, int(settings.S3_MAX_ATTEMPTS))},
            tcp_keepalive=bool(settings.S3_TCP_KEEPALIVE),
        ),
    )


@lru_cache(maxsize=1)
def _s3_client_cached():
    # boto3 clients are thread-safe and should be reused for pooling.
    return _s3_client()


@lru_cache(maxsize=1)
def _ensure_bucket_ready() -> str:
    """
    Verify the configured S3 bucket exists (or auto-create it).

    Distinguishes bucket-not-found (404) from auth/network failures so
    misconfigured credentials or unreachable endpoints fail fast with a
    clear message instead of silently trying to create buckets.
    """
    bucket = _require_s3_settings()
    client = _s3_client_cached()
    try:
        client.head_bucket(Bucket=bucket)
        return bucket
    except client.exceptions.NoSuchBucket:
        pass  # Bucket genuinely missing — may auto-create below.
    except Exception as exc:
        # Inspect botocore error code for 404 (some S3-compat endpoints
        # don't raise NoSuchBucket but return a 404 ClientError).
        error_code = _extract_error_code(exc)
        if error_code in ("404", "NoSuchBucket"):
            pass  # Treat as missing bucket.
        elif error_code in ("403", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            raise RuntimeError(
                f"S3 authentication failed for bucket '{bucket}'. "
                "Check S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY."
            ) from exc
        else:
            raise RuntimeError(
                f"S3 connectivity check failed for bucket '{bucket}': {exc}. "
                "Check S3_ENDPOINT_URL, network, and credentials."
            ) from exc

    if not settings.S3_AUTO_CREATE_BUCKET:
        raise RuntimeError(
            f"S3 bucket '{bucket}' does not exist and S3_AUTO_CREATE_BUCKET is off."
        )
    logger.info("S3 bucket %s missing; creating it", bucket)
    region = (settings.S3_REGION or "us-east-1").strip()
    if region == "us-east-1":
        client.create_bucket(Bucket=bucket)
    else:
        client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    return bucket


def _extract_error_code(exc: Exception) -> str:
    """Pull the S3/botocore error code string from a ClientError or HTTP status."""
    resp = getattr(exc, "response", None) or {}
    code = (resp.get("Error") or {}).get("Code") or ""
    if code:
        return str(code)
    http_code = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return str(http_code) if http_code else ""


def verify_s3_connectivity() -> dict:
    """
    Probe S3 endpoint reachability, authentication, and bucket access.
    Returns a dict with 'ok' (bool) and 'detail' (str).
    Designed for startup health checks and the /health endpoint.
    """
    if not _is_s3_backend():
        return {"ok": True, "detail": "storage=local (S3 not enabled)"}
    try:
        _require_s3_settings()
    except RuntimeError as e:
        return {"ok": False, "detail": f"config: {e}"}
    try:
        bucket = _ensure_bucket_ready()
        return {"ok": True, "detail": f"bucket={bucket} reachable"}
    except RuntimeError as e:
        return {"ok": False, "detail": str(e)}
    except Exception as e:
        return {"ok": False, "detail": f"unexpected: {e}"}


async def persist_file(name: str, data: bytes, content_type: Optional[str], *, job_id: Optional[int] = None) -> Dict[str, Any]:
    safe_name = sanitize_filename(name)
    file_id = str(uuid.uuid4())
    ct = content_type or "application/octet-stream"
    if _is_s3_backend():
        bucket = _ensure_bucket_ready()
        job_segment = str(job_id) if job_id is not None else "unassigned"
        key = f"jobs/{job_segment}/{file_id}_{safe_name}"

        def _upload():
            _s3_client_cached().put_object(Bucket=bucket, Key=key, Body=data, ContentType=ct)

        await asyncio.to_thread(_upload)
        return _build_metadata(file_id=file_id, name=safe_name, content_type=ct, size=len(data), storage="s3", bucket=bucket, key=key)

    file_path = UPLOAD_DIR / f"{file_id}_{safe_name}"
    file_path.write_bytes(data)
    return _build_metadata(file_id=file_id, name=safe_name, content_type=ct, size=len(data), path=str(file_path))


async def delete_file(file_info: Dict[str, Any]) -> None:
    if file_info.get("storage") == "s3" and file_info.get("bucket") and file_info.get("key"):
        bucket = file_info["bucket"]
        key = file_info["key"]

        def _delete():
            _s3_client_cached().delete_object(Bucket=bucket, Key=key)

        try:
            await asyncio.to_thread(_delete)
        except Exception as e:
            logger.warning("Failed deleting S3 object bucket=%s key=%s: %s", bucket, key, e)
        return

    p = file_info.get("path")
    if p:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed deleting local file path=%s: %s", p, e)


def delete_file_sync(file_info: Dict[str, Any]) -> None:
    if file_info.get("storage") == "s3" and file_info.get("bucket") and file_info.get("key"):
        try:
            _s3_client_cached().delete_object(Bucket=file_info["bucket"], Key=file_info["key"])
        except Exception as e:
            logger.warning("Failed deleting S3 object bucket=%s key=%s: %s", file_info.get("bucket"), file_info.get("key"), e)
        return
    p = file_info.get("path")
    if p:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed deleting local file path=%s: %s", p, e)


def open_local_download_path(file_info: Dict[str, Any]) -> Path:
    file_path = Path(file_info["path"])
    if not file_path.exists():
        raise FileNotFoundError("File no longer exists on server")
    return file_path


def download_s3_bytes(file_info: Dict[str, Any]) -> bytes:
    if file_info.get("storage") != "s3" or not file_info.get("bucket") or not file_info.get("key"):
        raise ValueError("Invalid S3 file metadata")
    resp = _s3_client_cached().get_object(Bucket=file_info["bucket"], Key=file_info["key"])
    return resp["Body"].read()


def open_s3_download_stream(file_info: Dict[str, Any]):
    """
    Return a streaming body + metadata for S3-compatible object downloads.
    Avoids loading full objects into memory.
    """
    if file_info.get("storage") != "s3" or not file_info.get("bucket") or not file_info.get("key"):
        raise ValueError("Invalid S3 file metadata")
    resp = _s3_client_cached().get_object(Bucket=file_info["bucket"], Key=file_info["key"])
    return resp["Body"], resp.get("ContentType") or "application/octet-stream", resp.get("ContentLength")


async def materialize_to_temp_path(file_info: Dict[str, Any]) -> str:
    """
    Return a local readable path for analyzers that require filesystem access.
    For local files this returns the original path.
    For S3-backed files this downloads to a temp file and returns that path.
    Caller is responsible for deleting temp files via cleanup_temp_path.
    """
    if file_info.get("path"):
        return file_info["path"]

    if file_info.get("storage") == "s3":
        data = await asyncio.to_thread(download_s3_bytes, file_info)
        suffix = _file_ext(file_info.get("name", "")) or ""
        with tempfile.NamedTemporaryFile(prefix="jobdoc_", suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            return tmp.name

    raise ValueError("File metadata has no readable source")


def cleanup_temp_path(file_info: Dict[str, Any], local_path: str) -> None:
    # Only remove temp files we created for S3-backed entries
    if file_info.get("storage") != "s3":
        return
    try:
        Path(local_path).unlink(missing_ok=True)
    except OSError:
        pass

