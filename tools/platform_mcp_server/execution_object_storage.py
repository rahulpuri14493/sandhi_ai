"""S3-compatible, Azure Blob, and GCS interactive execution."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

from azure_blob_client import blob_service_client_from_config
from execution_common import _resolve_s3_compatible_endpoint, _truncate_for_log, safe_tool_error

logger = logging.getLogger(__name__)

_PUT_BODY_MISSING_LAST_LOG: Dict[Tuple[str, str], float] = {}
# Bound memory if clients spam distinct bucket+key pairs for empty puts.
_PUT_BODY_MISSING_LOG_MAX_KEYS = 4096

# Reject absurdly large list tokens (DoS / accidental paste) before vendor SDK calls.
_MCP_OBJECT_STORAGE_MAX_TOKEN_CHARS = 10_000

# S3 object key: 1024 UTF-8 octets max; margin for multibyte / tooling.
_MCP_OBJECT_STORAGE_MAX_OBJECT_KEY_UTF8 = 3072
# List/copy prefixes and similar (not the same as per-key limit).
_MCP_OBJECT_STORAGE_MAX_PREFIX_CHARS = 8192
# AWS single-object size bound (5 TiB); caps read_offset / Range construction.
_MAX_OBJECT_BYTE_INDEX_INCLUSIVE = 5 * 1024**4 - 1

_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")


def _validate_read_bounds(off: int, ln: Optional[int]) -> Optional[str]:
    """Prevent pathological Range headers and unbounded integers from user input."""
    if off < 0:
        return "Error: read_offset must be >= 0"
    if off > _MAX_OBJECT_BYTE_INDEX_INCLUSIVE:
        return "Error: read_offset exceeds platform limit"
    if ln is not None:
        if ln < 1:
            return "Error: read_length must be >= 1"
        end_incl = off + ln - 1
        if end_incl > _MAX_OBJECT_BYTE_INDEX_INCLUSIVE:
            return "Error: byte range exceeds platform limit"
    return None


def _validate_object_key_string(key: str, *, as_prefix: bool = False) -> Optional[str]:
    """Reject NUL and oversize strings (path traversal markers are valid in object keys; size/NUL are the risk)."""
    if not key:
        return None
    label = "prefix" if as_prefix else "key"
    if "\x00" in key:
        return f"Error: {label} must not contain NUL bytes"
    try:
        enc = key.encode("utf-8")
    except UnicodeEncodeError:
        return f"Error: {label} must be valid UTF-8"
    lim = _MCP_OBJECT_STORAGE_MAX_PREFIX_CHARS if as_prefix else _MCP_OBJECT_STORAGE_MAX_OBJECT_KEY_UTF8
    if len(enc) > lim:
        return f"Error: {label} exceeds maximum length"
    return None


def _normalize_tool_action(arguments: Dict[str, Any], *, default: str = "get") -> str:
    """Coerce action to string so non-string values do not raise in strip()."""
    raw = arguments.get("action")
    if raw is None:
        return default
    s = str(raw).strip().lower()
    return s if s else default


def _validate_prefix_field(s: str, field: str) -> Optional[str]:
    if not s:
        return None
    if "\x00" in s:
        return f"Error: {field} must not contain NUL bytes"
    if len(s) > _MCP_OBJECT_STORAGE_MAX_PREFIX_CHARS:
        return f"Error: {field} exceeds maximum length"
    return None


def _validate_start_after_token(s: str) -> Optional[str]:
    if not s:
        return None
    if len(s) > _MCP_OBJECT_STORAGE_MAX_TOKEN_CHARS:
        return "Error: start_after exceeds maximum length"
    if "\x00" in s:
        return "Error: start_after must not contain NUL bytes"
    return None


def _object_storage_max_read_bytes() -> int:
    """Upper bound for a single get/read into process memory (overridable per request, capped)."""
    try:
        v = int(os.environ.get("MCP_OBJECT_STORAGE_MAX_READ_BYTES", str(20 * 1024 * 1024)) or 20971520)
    except (TypeError, ValueError):
        v = 20 * 1024 * 1024
    return max(1024, min(262_144_000, v))


def _effective_max_read_bytes(arguments: Dict[str, Any]) -> int:
    cap = _object_storage_max_read_bytes()
    raw = arguments.get("max_read_bytes") or arguments.get("maxReadBytes")
    if raw is None:
        return cap
    try:
        req = int(raw)
    except (TypeError, ValueError):
        return cap
    return max(1024, min(cap, max(1, req)))


def _parse_read_window(
    arguments: Dict[str, Any], max_cap: int
) -> Tuple[int, Optional[int], Optional[str]]:
    """
    Returns (offset, length_or_none, error_message).
    length None => caller may read whole object if size <= max_cap (after Head).
    Explicit ranged read when length is set.
    """
    br = arguments.get("byte_range") or arguments.get("byteRange")
    if br is not None and str(br).strip():
        parts = str(br).strip().split("-", 1)
        try:
            start = int(parts[0].strip())
        except (TypeError, ValueError):
            return 0, None, "Error: invalid byte_range (start)"
        if start < 0:
            return 0, None, "Error: byte_range start must be >= 0"
        if len(parts) == 1 or not parts[1].strip():
            vb = _validate_read_bounds(start, max_cap)
            if vb:
                return 0, None, vb
            return start, max_cap, None
        try:
            end_incl = int(parts[1].strip())
        except (TypeError, ValueError):
            return 0, None, "Error: invalid byte_range (end)"
        if end_incl < start:
            return 0, None, "Error: byte_range end before start"
        ln = end_incl - start + 1
        ln = min(max(1, ln), max_cap)
        vb = _validate_read_bounds(start, ln)
        if vb:
            return 0, None, vb
        return start, ln, None

    ro = arguments.get("read_offset")
    if ro is None:
        ro = arguments.get("offset")
    rl = arguments.get("read_length") or arguments.get("length")
    if ro is None and rl is None:
        return 0, None, None
    try:
        start = int(ro or 0)
    except (TypeError, ValueError):
        return 0, None, "Error: invalid read_offset"
    if start < 0:
        return 0, None, "Error: read_offset must be >= 0"
    if rl is None:
        vb = _validate_read_bounds(start, max_cap)
        if vb:
            return 0, None, vb
        return start, max_cap, None
    try:
        ln = int(rl)
    except (TypeError, ValueError):
        return 0, None, "Error: invalid read_length"
    ln = min(max(1, ln), max_cap)
    vb = _validate_read_bounds(start, ln)
    if vb:
        return 0, None, vb
    return start, ln, None


def _body_as_tool_result(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return json.dumps({"bytes_b64": base64.b64encode(data).decode("ascii")})


def _ranged_read_json(
    data: bytes, *, read_offset: int, total_size: Optional[int], is_partial: bool, max_cap: int
) -> str:
    payload: Dict[str, Any] = {
        "read_offset": read_offset,
        "bytes_returned": len(data),
        "is_partial": is_partial,
        "max_read_bytes_cap": max_cap,
    }
    if total_size is not None:
        payload["total_size"] = total_size
    try:
        payload["encoding"] = "utf-8"
        payload["text"] = data.decode("utf-8")
    except UnicodeDecodeError:
        payload["encoding"] = "bytes_b64"
        payload["bytes_b64"] = base64.b64encode(data).decode("ascii")
    return json.dumps(payload, indent=2)


def _parse_s3_content_range(cr: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if not cr:
        return None, None, None
    m = _CONTENT_RANGE_RE.match(str(cr).strip())
    if not m:
        return None, None, None
    a, b, tot = m.group(1), m.group(2), m.group(3)
    try:
        start, end = int(a), int(b)
        total = None if tot == "*" else int(tot)
        return start, end, total
    except (TypeError, ValueError):
        return None, None, None


def _object_too_large_json(content_length: int, max_cap: int) -> str:
    return json.dumps(
        {
            "error": "object_too_large_for_full_read",
            "content_length": content_length,
            "max_read_bytes": max_cap,
            "hint": (
                "Use read_offset and read_length, or byte_range (inclusive start-end), "
                "to read a window within max_read_bytes."
            ),
        },
        indent=2,
    )


_COPY_PREFIX_IDEMPOTENCY_HINT = (
    "copy_prefix is not transactional: a failure after some successes may leave a partial destination prefix. "
    "Per-key copy_object is idempotent (re-copy overwrites). To continue a capped batch, pass next_continuation_token "
    "or next_start_after from the last response; on mid-batch copy failure, retry with the same tokens plus any "
    "next_start_after returned below."
)


def _finalize_copy_prefix_payload(
    *,
    status: str,
    copied: list[str],
    skipped_large: list[str],
    skipped_untracked: int = 0,
    list_calls: int,
    list_token: str | None,
    broke_mid_page: bool,
    last_processed_key: str | None,
    hit_list_call_cap: bool,
    src: str,
    dst: str,
    bucket: str,
    env_max_objs: int,
    max_bytes: int,
    copy_failure: str | None = None,
) -> Dict[str, Any]:
    more_pages = bool(list_token)
    resume_same_page = broke_mid_page and bool(last_processed_key) and not more_pages
    is_truncated = more_pages or resume_same_page or hit_list_call_cap
    rk, rsk = _s3_copy_prefix_response_limits()
    su = max(0, int(skipped_untracked))
    total_skipped = len(skipped_large) + su
    payload_cp: Dict[str, Any] = {
        "status": status,
        "transactional": False,
        "idempotency_and_resume": _COPY_PREFIX_IDEMPOTENCY_HINT,
        "bucket": bucket,
        "source_prefix": src,
        "dest_prefix": dst,
        "copied_count": len(copied),
        "is_truncated": is_truncated,
        "list_calls": list_calls,
        "max_objects_cap": env_max_objs,
        "max_bytes_per_object_cap": max_bytes,
    }
    if copy_failure:
        payload_cp["partial_copy"] = True
        payload_cp["copy_failure"] = copy_failure
    if rk <= 0:
        payload_cp["copied_source_keys"] = []
        payload_cp["copied_source_keys_omitted"] = len(copied)
    elif len(copied) > rk:
        payload_cp["copied_source_keys"] = copied[:rk]
        payload_cp["copied_source_keys_truncated"] = True
        payload_cp["copied_source_keys_omitted"] = len(copied) - rk
    else:
        payload_cp["copied_source_keys"] = list(copied)
    if rsk <= 0:
        payload_cp["skipped_over_max_bytes"] = []
        payload_cp["skipped_over_max_bytes_count"] = total_skipped
    elif len(skipped_large) > rsk:
        payload_cp["skipped_over_max_bytes"] = skipped_large[:rsk]
        payload_cp["skipped_over_max_bytes_truncated"] = True
        payload_cp["skipped_over_max_bytes_omitted"] = len(skipped_large) - rsk
        payload_cp["skipped_over_max_bytes_count"] = total_skipped
    else:
        payload_cp["skipped_over_max_bytes"] = list(skipped_large)
        payload_cp["skipped_over_max_bytes_count"] = total_skipped
    if su:
        payload_cp["skipped_over_max_bytes_untracked_count"] = su
    if list_token:
        payload_cp["next_continuation_token"] = list_token
    if resume_same_page and last_processed_key:
        payload_cp["next_start_after"] = last_processed_key
    if hit_list_call_cap:
        payload_cp["list_call_cap_reached"] = True
    return payload_cp


def _s3_copy_prefix_env_limits() -> Tuple[int, int, int]:
    """Per-RPC caps for copy_prefix (protect platform under heavy load)."""
    try:
        max_objs = int(os.environ.get("MCP_S3_COPY_PREFIX_MAX_OBJECTS", "500") or 500)
    except (TypeError, ValueError):
        max_objs = 500
    max_objs = max(1, min(5000, max_objs))
    try:
        max_bytes = int(
            os.environ.get("MCP_S3_COPY_PREFIX_MAX_BYTES_PER_OBJECT", str(52_428_800)) or 52428800
        )
    except (TypeError, ValueError):
        max_bytes = 52_428_800
    max_bytes = max(1024, min(500 * 1024 * 1024, max_bytes))
    try:
        max_list_calls = int(os.environ.get("MCP_S3_COPY_PREFIX_MAX_LIST_CALLS", "40") or 40)
    except (TypeError, ValueError):
        max_list_calls = 40
    max_list_calls = max(1, min(200, max_list_calls))
    return max_objs, max_bytes, max_list_calls


def _s3_copy_prefix_response_limits() -> Tuple[int, int]:
    """Cap arrays embedded in JSON (model context + process memory under heavy copy_prefix traffic)."""
    try:
        kcap = int(os.environ.get("MCP_S3_COPY_PREFIX_MAX_KEYS_IN_RESPONSE", "80") or 80)
    except (TypeError, ValueError):
        kcap = 80
    kcap = max(0, min(500, kcap))
    try:
        scap = int(os.environ.get("MCP_S3_COPY_PREFIX_MAX_SKIPPED_IN_RESPONSE", "80") or 80)
    except (TypeError, ValueError):
        scap = 80
    scap = max(0, min(500, scap))
    return kcap, scap


def _s3_copy_prefix_max_skipped_keys_tracked() -> int:
    """Cap in-RAM skipped key strings when a prefix is mostly over max_bytes (heavy load)."""
    try:
        n = int(os.environ.get("MCP_S3_COPY_PREFIX_MAX_SKIPPED_KEYS_TRACKED", "2000") or 2000)
    except (TypeError, ValueError):
        n = 2000
    return max(1, min(50_000, n))


def _parse_s3_continuation_token(arguments: Dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (token, error_message). error_message set when token is unusable."""
    ct = arguments.get("continuation_token") or arguments.get("continuationToken")
    if ct is None:
        return None, None
    cts = str(ct).strip()
    if not cts:
        return None, None
    if "\x00" in cts:
        return None, "Error: continuation_token must not contain NUL bytes"
    if len(cts) > _MCP_OBJECT_STORAGE_MAX_TOKEN_CHARS:
        return None, "Error: continuation_token exceeds maximum length"
    return cts, None


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


def _normalize_s3_object_key(key: str, bucket: str) -> str:
    """
    Normalize user/model-supplied keys for S3 / MinIO / Ceph (same boto3 semantics).

    - Strip leading slashes (S3 keys are not filesystem paths).
    - Strip a mistaken ``bucket/object`` prefix when it matches the configured bucket.
    - Accept ``s3://bucket/object`` when the host part matches the configured bucket.
    """
    k = (key or "").strip()
    if not k:
        return ""
    k = k.lstrip("/")
    if not k:
        return ""
    b = (bucket or "").strip()
    if not b:
        return k
    blo = b.lower()
    klo = k.lower()
    if klo == blo or klo == f"{blo}/":
        return ""
    if klo.startswith("s3://"):
        rest = k[5:]
        if "/" not in rest:
            if rest.lower() == blo:
                return ""
            return rest.lstrip("/")
        bpart, _, opart = rest.partition("/")
        if bpart.lower() == blo:
            return opart.lstrip("/")
        return k
    if len(k) >= len(b) + 1 and k[len(b)] == "/" and k[: len(b)].lower() == blo:
        return k[len(b) + 1 :].lstrip("/")
    return k


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
    action = _normalize_tool_action(arguments)
    key_raw = (arguments.get("key") or "").strip()
    if not key_raw and action not in ("list", "copy_prefix"):
        return "Error: key is required"

    bucket = (config.get("bucket") or "").strip()
    if not bucket:
        return "Error: bucket not configured"

    key = _normalize_s3_object_key(key_raw, bucket)

    if action not in ("list", "copy_prefix") and not key:
        return "Error: key is required"

    if action in ("get", "read", "put", "write") and key:
        err_k = _validate_object_key_string(key, as_prefix=False)
        if err_k:
            return err_k
    # list: validate final prefix only (adding "/" can push UTF-8 length past the raw key check).

    write_prefix = _required_s3_write_prefix(config)
    if action in ("put", "write") and not _s3_key_allowed_for_write(key, write_prefix):
        return (
            "Error: key must be under the configured write prefix "
            f"'{write_prefix}/' (set write_key_prefix on the tool or MCP_S3_WRITE_KEY_PREFIX). "
            f"Refused key: {key!r}"
        )
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

    if action == "copy_prefix":
        src = (arguments.get("source_prefix") or key_raw or "").strip().lstrip("/")
        dst = (arguments.get("dest_prefix") or arguments.get("destination_prefix") or "").strip().lstrip("/")
        if not src or not dst:
            return "Error: copy_prefix requires source_prefix (or key) and dest_prefix"
        if not src.endswith("/"):
            src = src + "/"
        if not dst.endswith("/"):
            dst = dst + "/"
        for label, val in (("source_prefix", src), ("dest_prefix", dst)):
            err_pf = _validate_prefix_field(val, label)
            if err_pf:
                return err_pf
        env_max_objs, max_bytes, max_list_calls = _s3_copy_prefix_env_limits()
        req_max = arguments.get("max_objects") or arguments.get("maxObjects")
        max_objects = env_max_objs
        if req_max is not None:
            try:
                max_objects = min(env_max_objs, max(1, int(req_max)))
            except (TypeError, ValueError):
                max_objects = env_max_objs
        list_page_size = 500
        raw_lp = arguments.get("max_keys") or arguments.get("maxKeys")
        if raw_lp is not None:
            try:
                list_page_size = min(1000, max(1, int(raw_lp)))
            except (TypeError, ValueError):
                list_page_size = 500
        # Smaller list pages when copying few objects (less control-plane load per RPC).
        if max_objects <= 200:
            list_page_size = min(list_page_size, max(max_objects * 4, 32))
        start_after = (arguments.get("start_after") or arguments.get("copy_start_after") or "").strip()
        err_sa = _validate_start_after_token(start_after)
        if err_sa:
            return err_sa
        skip_track_cap = _s3_copy_prefix_max_skipped_keys_tracked()
        skipped_untracked = 0
        copied: list[str] = []
        skipped_large: list[str] = []
        list_calls = 0
        list_token: str | None = None
        broke_mid_page = False
        last_processed_key: str | None = None
        try:
            while len(copied) < max_objects and list_calls < max_list_calls:
                list_calls += 1
                snapshot_list_token = list_token
                snapshot_start_after = start_after or None
                list_kw: Dict[str, Any] = dict(Bucket=bucket, Prefix=src, MaxKeys=list_page_size)
                if list_token:
                    list_kw["ContinuationToken"] = list_token
                elif start_after:
                    list_kw["StartAfter"] = start_after
                    start_after = ""
                r = client.list_objects_v2(**list_kw)
                page_truncated = bool(r.get("IsTruncated"))
                next_token = r.get("NextContinuationToken")
                next_token = str(next_token).strip() if next_token else None
                contents = r.get("Contents") or []
                for i, obj in enumerate(contents):
                    if len(copied) >= max_objects:
                        broke_mid_page = i < len(contents)
                        break
                    sk = (obj.get("Key") or "").strip()
                    if not sk or sk.endswith("/"):
                        continue
                    try:
                        sz = int(obj.get("Size") or 0)
                    except (TypeError, ValueError):
                        sz = 0
                    if sz > max_bytes:
                        if len(skipped_large) < skip_track_cap:
                            skipped_large.append(sk)
                        else:
                            skipped_untracked += 1
                        last_processed_key = sk
                        continue
                    if not sk.startswith(src):
                        continue
                    rel = sk[len(src) :]
                    dk = (dst + rel).lstrip("/")
                    if not _s3_key_allowed_for_write(dk, write_prefix):
                        return (
                            "Error: dest key must be under the configured write prefix "
                            f"'{write_prefix}/' (set write_key_prefix on the tool or MCP_S3_WRITE_KEY_PREFIX). "
                            f"Refused dest: {dk!r}"
                        )
                    try:
                        client.copy_object(
                            Bucket=bucket,
                            Key=dk,
                            CopySource={"Bucket": bucket, "Key": sk},
                        )
                    except ClientError as e:
                        logger.warning(
                            "MCP s3_family copy_prefix copy_object failed key=%s (%s)",
                            _truncate_for_log(sk, 200),
                            type(e).__name__,
                        )
                        hit_list_call_cap = list_calls >= max_list_calls and len(copied) < max_objects
                        broke_mid_page = True
                        pl = _finalize_copy_prefix_payload(
                            status="copy_failed",
                            copied=copied,
                            skipped_large=skipped_large,
                            skipped_untracked=skipped_untracked,
                            list_calls=list_calls,
                            list_token=next_token if page_truncated else None,
                            broke_mid_page=True,
                            last_processed_key=last_processed_key,
                            hit_list_call_cap=hit_list_call_cap,
                            src=src,
                            dst=dst,
                            bucket=bucket,
                            env_max_objs=env_max_objs,
                            max_bytes=max_bytes,
                            copy_failure=type(e).__name__,
                        )
                        pl["failed_source_key"] = sk
                        if snapshot_list_token:
                            pl["resume_list_continuation_token"] = snapshot_list_token
                        elif snapshot_start_after:
                            pl["resume_start_after"] = snapshot_start_after
                        return json.dumps(pl, indent=2)
                    copied.append(sk)
                    last_processed_key = sk
                if broke_mid_page:
                    list_token = next_token if page_truncated else None
                    break
                if not page_truncated:
                    list_token = None
                    break
                if not next_token:
                    list_token = None
                    break
                list_token = next_token
            hit_list_call_cap = list_calls >= max_list_calls and len(copied) < max_objects
            payload_cp = _finalize_copy_prefix_payload(
                status="ok",
                copied=copied,
                skipped_large=skipped_large,
                skipped_untracked=skipped_untracked,
                list_calls=list_calls,
                list_token=list_token,
                broke_mid_page=broke_mid_page,
                last_processed_key=last_processed_key,
                hit_list_call_cap=hit_list_call_cap,
                src=src,
                dst=dst,
                bucket=bucket,
                env_max_objs=env_max_objs,
                max_bytes=max_bytes,
            )
            return json.dumps(payload_cp, indent=2)
        except ClientError as e:
            logger.warning("MCP s3_family copy_prefix list failed (%s)", type(e).__name__)
            hit_list_call_cap = list_calls >= max_list_calls and len(copied) < max_objects
            pl = _finalize_copy_prefix_payload(
                status="copy_prefix_list_failed",
                copied=copied,
                skipped_large=skipped_large,
                skipped_untracked=skipped_untracked,
                list_calls=list_calls,
                list_token=list_token,
                broke_mid_page=broke_mid_page,
                last_processed_key=last_processed_key,
                hit_list_call_cap=hit_list_call_cap,
                src=src,
                dst=dst,
                bucket=bucket,
                env_max_objs=env_max_objs,
                max_bytes=max_bytes,
                copy_failure=type(e).__name__,
            )
            return json.dumps(pl, indent=2)

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
            # S3 object keys do not start with "/"; Prefix "/" matches nothing. Bucket name
            # is not a key prefix inside the bucket—use key="" (or omit misleading "/" only).
            if not key or key == "/":
                prefix = ""
            elif key.endswith("/"):
                prefix = key
            else:
                prefix = key.rstrip("/") + "/"
            err_lp = _validate_object_key_string(prefix, as_prefix=True) if prefix else None
            if err_lp:
                return err_lp
            max_keys = 500
            raw_mk = arguments.get("max_keys") or arguments.get("maxKeys") or arguments.get("max_results") or arguments.get("maxResults")
            if raw_mk is not None:
                try:
                    max_keys = min(5000, max(1, int(raw_mk)))
                except (TypeError, ValueError):
                    max_keys = 500
            list_kw: Dict[str, Any] = dict(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
            cts, ct_err = _parse_s3_continuation_token(arguments)
            if ct_err:
                return ct_err
            if cts:
                list_kw["ContinuationToken"] = cts
            r = client.list_objects_v2(**list_kw)
            keys = [k for k in (o.get("Key") for o in r.get("Contents") or []) if k]
            payload: Dict[str, Any] = {
                "keys": keys,
                "is_truncated": bool(r.get("IsTruncated")),
            }
            nct = r.get("NextContinuationToken")
            if nct:
                payload["next_continuation_token"] = nct
            return json.dumps(payload, indent=2)
        if action in ("get", "read"):
            max_cap = _effective_max_read_bytes(arguments)
            off, ln, rw_err = _parse_read_window(arguments, max_cap)
            if rw_err:
                return rw_err
            if ln is not None:
                end_incl = off + ln - 1
                try:
                    resp = client.get_object(Bucket=bucket, Key=key, Range=f"bytes={off}-{end_incl}")
                except ClientError as e:
                    return safe_tool_error("S3 get_object", e)
                data = resp["Body"].read()
                _, _, total = _parse_s3_content_range(resp.get("ContentRange"))
                is_partial = total is None or off + len(data) < total
                return _ranged_read_json(
                    data, read_offset=off, total_size=total, is_partial=is_partial, max_cap=max_cap
                )
            try:
                h = client.head_object(Bucket=bucket, Key=key)
                cl = int(h.get("ContentLength") or 0)
            except ClientError:
                try:
                    resp = client.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max_cap - 1}")
                except ClientError as e:
                    return safe_tool_error("S3 get_object", e)
                data = resp["Body"].read()
                _, _, total = _parse_s3_content_range(resp.get("ContentRange"))
                if total is not None and total > max_cap:
                    return _object_too_large_json(total, max_cap)
                if total is not None and len(data) >= total:
                    return _body_as_tool_result(data)
                return _ranged_read_json(
                    data, read_offset=0, total_size=total, is_partial=True, max_cap=max_cap
                )
            if cl > max_cap:
                return _object_too_large_json(cl, max_cap)
            try:
                obj = client.get_object(Bucket=bucket, Key=key)
                data = obj["Body"].read()
            except ClientError as e:
                return safe_tool_error("S3 get_object", e)
            return _body_as_tool_result(data)
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
        return safe_tool_error("S3 operation", e)
    except Exception as e:
        return safe_tool_error("S3 operation", e)


def execute_azure_blob(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    action = _normalize_tool_action(arguments)
    key_raw = (arguments.get("key") or "").strip()
    if action != "list" and not key_raw:
        return "Error: key is required"
    # Blob names do not use a leading slash; models often pass "/reports/foo" and get zero list results.
    key = key_raw.lstrip("/")
    if action != "list" and not key:
        return "Error: key is required"
    if action in ("get", "read", "put", "write") and key:
        err_k = _validate_object_key_string(key, as_prefix=False)
        if err_k:
            return err_k
    if action == "list" and key:
        err_p = _validate_object_key_string(key, as_prefix=True)
        if err_p:
            return err_p
    try:
        import azure.storage.blob  # noqa: F401
    except ImportError:
        return "Error: azure-storage-blob is not installed"
    container = (config.get("container") or "").strip()
    if not container:
        return "Error: container not configured"
    try:
        svc = blob_service_client_from_config(config)
        cc = svc.get_container_client(container)
        if action in ("get", "read"):
            max_cap = _effective_max_read_bytes(arguments)
            off, ln, rw_err = _parse_read_window(arguments, max_cap)
            if rw_err:
                return rw_err
            blob = cc.get_blob_client(key)
            if ln is not None:
                try:
                    dl = blob.download_blob(offset=off, length=ln)
                    data = dl.readall()
                except Exception as e:
                    return safe_tool_error("Azure blob download", e)
                try:
                    props = blob.get_blob_properties()
                    total = int(props.size) if props.size is not None else None
                except Exception:
                    total = None
                is_partial = total is not None and off + len(data) < total
                return _ranged_read_json(
                    data, read_offset=off, total_size=total, is_partial=is_partial, max_cap=max_cap
                )
            try:
                props = blob.get_blob_properties()
                cl = int(props.size) if props.size is not None else 0
            except Exception as e:
                return safe_tool_error("Azure blob properties", e)
            if cl > max_cap:
                return _object_too_large_json(cl, max_cap)
            try:
                data = blob.download_blob().readall()
            except Exception as e:
                return safe_tool_error("Azure blob download", e)
            return _body_as_tool_result(data)
        if action in ("put", "write"):
            blob = cc.get_blob_client(key)
            body = arguments.get("body") or arguments.get("content") or ""
            if isinstance(body, dict):
                body = json.dumps(body)
            blob.upload_blob(str(body).encode("utf-8") if not isinstance(body, bytes) else body, overwrite=True)
            return json.dumps({"status": "ok"})
        if action == "list":
            max_res = 500
            raw_mr = arguments.get("max_results") or arguments.get("maxResults")
            if raw_mr is not None:
                try:
                    max_res = min(5000, max(1, int(raw_mr)))
                except (TypeError, ValueError):
                    max_res = 500
            ct_arg = arguments.get("continuation_token") or arguments.get("continuationToken")
            ct_arg = str(ct_arg).strip() if ct_arg else None
            if ct_arg and len(ct_arg) > _MCP_OBJECT_STORAGE_MAX_TOKEN_CHARS:
                return "Error: continuation_token exceeds maximum length"
            if ct_arg and "\x00" in ct_arg:
                return "Error: continuation_token must not contain NUL bytes"
            prefix = key if key else None
            blob_iter = cc.list_blobs(name_starts_with=prefix, results_per_page=max_res)
            pages = blob_iter.by_page(continuation_token=ct_arg)
            page_iter = iter(pages)
            try:
                first_page = next(page_iter)
            except StopIteration:
                names = []
            else:
                names = [b.name for b in first_page]
            next_ct = getattr(pages, "continuation_token", None)
            next_ct_s = str(next_ct).strip() if next_ct else ""
            payload_az: Dict[str, Any] = {
                "blobs": names,
                "is_truncated": bool(next_ct_s),
            }
            if next_ct_s:
                payload_az["next_continuation_token"] = next_ct_s
            return json.dumps(payload_az, indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        return safe_tool_error("Azure blob error", e)


def execute_gcs(config: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    action = _normalize_tool_action(arguments)
    key_raw = (arguments.get("key") or "").strip()
    if not key_raw and action != "list":
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
    # Object names do not include the bucket; models often pass "sandhi/reports/foo" while the
    # tool already targets bucket "sandhi", which makes blob() look for a non-existent key.
    key = key_raw.lstrip("/")
    bucket_prefix = f"{bucket_name}/"
    if key.startswith(bucket_prefix):
        key = key[len(bucket_prefix) :]
    key = key.strip()
    if action != "list" and not key:
        return "Error: key is required"
    if action in ("get", "read", "put", "write") and key:
        err_k = _validate_object_key_string(key, as_prefix=False)
        if err_k:
            return err_k
    if action == "list" and key:
        err_p = _validate_object_key_string(key, as_prefix=True)
        if err_p:
            return err_p
    creds_json = config.get("credentials_json")
    if creds_json:
        if isinstance(creds_json, str):
            if len(creds_json) > 262_144:
                return "Error: credentials_json exceeds maximum length"
            if "\x00" in creds_json:
                return "Error: credentials_json must not contain NUL bytes"
        info = json.loads(creds_json) if isinstance(creds_json, str) else creds_json
        creds = service_account.Credentials.from_service_account_info(info)
        client = storage.Client(project=project, credentials=creds)
    else:
        client = storage.Client(project=project or None)
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        if action in ("get", "read"):
            max_cap = _effective_max_read_bytes(arguments)
            off, ln, rw_err = _parse_read_window(arguments, max_cap)
            if rw_err:
                return rw_err
            try:
                blob.reload()
            except Exception as e:
                return safe_tool_error("GCS blob reload", e)
            total_sz = int(blob.size) if blob.size is not None else None
            if ln is not None:
                end_excl = off + ln
                try:
                    data = blob.download_as_bytes(start=off, end=end_excl)
                except Exception as e:
                    return safe_tool_error("GCS download", e)
                is_partial = total_sz is not None and end_excl < total_sz
                return _ranged_read_json(
                    data, read_offset=off, total_size=total_sz, is_partial=is_partial, max_cap=max_cap
                )
            if total_sz is not None and total_sz > max_cap:
                return _object_too_large_json(total_sz, max_cap)
            try:
                data = blob.download_as_bytes()
            except Exception as e:
                return safe_tool_error("GCS download", e)
            return _body_as_tool_result(data)
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
            list_prefix = key or None
            if list_prefix:
                err_lp = _validate_object_key_string(list_prefix, as_prefix=True)
                if err_lp:
                    return err_lp
            max_res = 500
            raw_mr = arguments.get("max_results") or arguments.get("maxResults")
            if raw_mr is not None:
                try:
                    max_res = min(5000, max(1, int(raw_mr)))
                except (TypeError, ValueError):
                    max_res = 500
            page_token = arguments.get("page_token") or arguments.get("pageToken")
            if page_token is not None:
                page_token = str(page_token).strip() or None
            if page_token and len(page_token) > _MCP_OBJECT_STORAGE_MAX_TOKEN_CHARS:
                return "Error: page_token exceeds maximum length"
            if page_token and "\x00" in page_token:
                return "Error: page_token must not contain NUL bytes"
            iterator = client.list_blobs(
                bucket_name,
                prefix=list_prefix,
                max_results=max_res,
                page_token=page_token,
            )
            # Defensive cap: never consume an unbounded iterator if the client library misbehaves.
            names: list[str] = []
            for i, b in enumerate(iterator):
                names.append(b.name)
                if i + 1 >= max_res:
                    break
            next_pt = getattr(iterator, "next_page_token", None)
            payload: Dict[str, Any] = {
                "objects": names,
                "is_truncated": bool(next_pt),
            }
            if next_pt:
                payload["next_page_token"] = next_pt
            return json.dumps(payload, indent=2)
        return f"Error: unknown action {action}"
    except Exception as e:
        return safe_tool_error("GCS error", e)
