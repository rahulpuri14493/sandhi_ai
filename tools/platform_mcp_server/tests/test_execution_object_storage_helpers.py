"""Unit tests for execution_object_storage helpers (coverage for validation and parsing)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import execution_object_storage as eos

pytestmark = pytest.mark.unit


class TestReadBoundsAndKeys:
    def test_validate_read_bounds(self):
        assert eos._validate_read_bounds(-1, None) is not None
        assert eos._validate_read_bounds(0, 0) is not None
        assert eos._validate_read_bounds(0, 1) is None
        assert eos._validate_read_bounds(eos._MAX_OBJECT_BYTE_INDEX_INCLUSIVE + 1, 1) is not None
        assert eos._validate_read_bounds(0, eos._MAX_OBJECT_BYTE_INDEX_INCLUSIVE + 2) is not None

    def test_validate_object_key_string(self):
        assert eos._validate_object_key_string("", as_prefix=False) is None
        assert eos._validate_object_key_string("a\x00b") is not None
        assert "prefix" in (eos._validate_object_key_string("a\x00b", as_prefix=True) or "")
        big = "x" * 5000
        assert eos._validate_object_key_string(big, as_prefix=False) is not None
        assert eos._validate_object_key_string("ok/key", as_prefix=False) is None
        # Lone surrogate: strict UTF-8 encode fails (invalid key).
        assert eos._validate_object_key_string("a\ud800", as_prefix=False) is not None

    def test_normalize_tool_action(self):
        assert eos._normalize_tool_action({}) == "get"
        assert eos._normalize_tool_action({"action": None}) == "get"
        assert eos._normalize_tool_action({"action": "  LIST "}) == "list"
        assert eos._normalize_tool_action({"action": "  "}) == "get"
        assert eos._normalize_tool_action({"action": 9}) == "9"

    def test_validate_prefix_and_start_after(self):
        assert eos._validate_prefix_field("", "f") is None
        assert eos._validate_prefix_field("a\x00", "f") is not None
        assert eos._validate_prefix_field("x" * (eos._MCP_OBJECT_STORAGE_MAX_PREFIX_CHARS + 1), "f") is not None
        assert eos._validate_start_after_token("z" * 20_000) is not None
        assert eos._validate_start_after_token("ok") is None
        assert eos._validate_start_after_token("a\x00") is not None

    def test_max_read_bytes_env(self, monkeypatch):
        monkeypatch.setenv("MCP_OBJECT_STORAGE_MAX_READ_BYTES", "not-int")
        assert eos._object_storage_max_read_bytes() == 20 * 1024 * 1024
        monkeypatch.setenv("MCP_OBJECT_STORAGE_MAX_READ_BYTES", "500")
        assert eos._object_storage_max_read_bytes() == 1024

    def test_effective_max_read_bytes(self):
        cap = eos._effective_max_read_bytes({})
        assert cap >= 1024
        assert eos._effective_max_read_bytes({"max_read_bytes": "bad"}) == cap
        inner = eos._effective_max_read_bytes({"max_read_bytes": cap // 2})
        assert inner <= cap


class TestParseReadWindow:
    def test_byte_range_open_end_uses_cap(self):
        off, ln, err = eos._parse_read_window({"byte_range": "10-"}, 1000)
        assert err is None
        assert off == 10 and ln == 1000

    def test_byte_range_errors(self):
        _, _, e = eos._parse_read_window({"byte_range": "x-1"}, 100)
        assert e is not None
        _, _, e = eos._parse_read_window({"byte_range": "-5"}, 100)
        assert e is not None
        _, _, e = eos._parse_read_window({"byte_range": "5-3"}, 100)
        assert e is not None
        _, _, e = eos._parse_read_window({"byte_range": "5-y"}, 100)
        assert e is not None

    def test_read_offset_negative(self):
        _, _, e = eos._parse_read_window({"read_offset": -1, "read_length": 1}, 100)
        assert "read_offset must be >= 0" in (e or "")

    def test_byte_range_inclusive_span_hits_platform_bound(self):
        mx = eos._MAX_OBJECT_BYTE_INDEX_INCLUSIVE
        _, _, e = eos._parse_read_window({"byte_range": f"{mx}-{mx + 5}"}, 100)
        assert e is not None and "platform limit" in (e or "").lower()

    def test_read_offset_aliases(self):
        off, ln, err = eos._parse_read_window({"offset": 2, "length": 3}, 100)
        assert err is None
        assert (off, ln) == (2, 3)
        _, _, e = eos._parse_read_window({"read_offset": "x"}, 100)
        assert e is not None
        _, _, e = eos._parse_read_window({"read_offset": 0, "read_length": "y"}, 100)
        assert e is not None


class TestS3ParsingAndPayload:
    def test_parse_s3_content_range(self):
        assert eos._parse_s3_content_range(None) == (None, None, None)
        assert eos._parse_s3_content_range("") == (None, None, None)
        assert eos._parse_s3_content_range("nope") == (None, None, None)
        a, b, t = eos._parse_s3_content_range("bytes 0-9/100")
        assert (a, b, t) == (0, 9, 100)
        a, b, t = eos._parse_s3_content_range("bytes 0-9/*")
        assert t is None

    def test_object_too_large_json(self):
        d = json.loads(eos._object_too_large_json(999, 100))
        assert d["error"] == "object_too_large_for_full_read"

    def test_body_as_tool_result(self):
        assert eos._body_as_tool_result(b"hi") == "hi"
        out = eos._body_as_tool_result(b"\xff")
        assert "bytes_b64" in json.loads(out)

    def test_ranged_read_json(self):
        s = eos._ranged_read_json(b"x", read_offset=0, total_size=2, is_partial=True, max_cap=99)
        d = json.loads(s)
        assert d["bytes_returned"] == 1
        b64 = eos._ranged_read_json(b"\xfe", read_offset=0, total_size=None, is_partial=False, max_cap=9)
        assert "bytes_b64" in json.loads(b64)

    def test_finalize_copy_prefix_payload(self):
        pl = eos._finalize_copy_prefix_payload(
            status="ok",
            copied=["a", "b"],
            skipped_large=["s1"],
            skipped_untracked=2,
            list_calls=1,
            list_token=None,
            broke_mid_page=False,
            last_processed_key=None,
            hit_list_call_cap=False,
            src="p/",
            dst="q/",
            bucket="b",
            env_max_objs=100,
            max_bytes=1024,
        )
        assert pl["skipped_over_max_bytes_count"] == 3
        assert pl["skipped_over_max_bytes_untracked_count"] == 2

    def test_s3_copy_prefix_env_and_response_limits(self, monkeypatch):
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_OBJECTS", "not-a-number")
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_LIST_CALLS", "9999")
        a, b, c = eos._s3_copy_prefix_env_limits()
        assert a == 500 and c == 200
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_KEYS_IN_RESPONSE", "0")
        k, s = eos._s3_copy_prefix_response_limits()
        assert k == 0 and s == 80

    def test_max_skipped_keys_tracked(self, monkeypatch):
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_SKIPPED_KEYS_TRACKED", "bad")
        assert eos._s3_copy_prefix_max_skipped_keys_tracked() == 2000

    def test_parse_s3_continuation_token(self):
        assert eos._parse_s3_continuation_token({}) == (None, None)
        assert eos._parse_s3_continuation_token({"continuation_token": "  "}) == (None, None)
        t, e = eos._parse_s3_continuation_token({"continuation_token": "a\x00"})
        assert e is not None
        t, e = eos._parse_s3_continuation_token({"continuation_token": "z" * 20_000})
        assert e is not None

    def test_normalize_s3_object_key(self):
        assert eos._normalize_s3_object_key("", "b") == ""
        assert eos._normalize_s3_object_key("///", "b") == ""
        assert eos._normalize_s3_object_key("bucket/", "bucket") == ""
        assert eos._normalize_s3_object_key("s3://bucket/obj", "bucket") == "obj"
        assert eos._normalize_s3_object_key("bucket/obj", "bucket") == "obj"
        assert eos._normalize_s3_object_key("other/x", "bucket") == "other/x"

    def test_s3_write_prefix_helpers(self):
        assert eos._s3_key_allowed_for_write("pre/a", "pre") is True
        assert eos._s3_key_allowed_for_write("other", "pre") is False

    def test_finalize_copy_prefix_omits_arrays_when_caps_zero(self, monkeypatch):
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_KEYS_IN_RESPONSE", "0")
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_SKIPPED_IN_RESPONSE", "0")
        pl = eos._finalize_copy_prefix_payload(
            status="ok",
            copied=["a"],
            skipped_large=["s"],
            skipped_untracked=0,
            list_calls=1,
            list_token=None,
            broke_mid_page=False,
            last_processed_key=None,
            hit_list_call_cap=True,
            src="p/",
            dst="q/",
            bucket="b",
            env_max_objs=100,
            max_bytes=1024,
        )
        assert pl["copied_source_keys"] == []
        assert pl["skipped_over_max_bytes"] == []

    def test_finalize_truncates_copied_and_skipped_arrays(self, monkeypatch):
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_KEYS_IN_RESPONSE", "1")
        monkeypatch.setenv("MCP_S3_COPY_PREFIX_MAX_SKIPPED_IN_RESPONSE", "1")
        pl = eos._finalize_copy_prefix_payload(
            status="ok",
            copied=["a", "b"],
            skipped_large=["s1", "s2"],
            skipped_untracked=0,
            list_calls=1,
            list_token=None,
            broke_mid_page=False,
            last_processed_key=None,
            hit_list_call_cap=False,
            src="p/",
            dst="q/",
            bucket="b",
            env_max_objs=100,
            max_bytes=1024,
        )
        assert pl.get("copied_source_keys_truncated") is True
        assert pl.get("skipped_over_max_bytes_truncated") is True
        assert len(pl["copied_source_keys"]) == 1
        assert len(pl["skipped_over_max_bytes"]) == 1


class TestExecuteAzureGcsEdgeErrors:
    def test_azure_missing_container(self, monkeypatch):
        _install_min_azure(monkeypatch)
        out = eos.execute_azure_blob({"connection_string": "x"}, {"key": "k", "action": "get"})
        assert "container" in out.lower()

    def test_gcs_missing_bucket(self, monkeypatch):
        _install_min_gcs(monkeypatch)
        out = eos.execute_gcs({"project_id": "p"}, {"key": "k", "action": "get"})
        assert "bucket" in out.lower()

    def test_gcs_list_bad_page_token(self, monkeypatch):
        _install_min_gcs(monkeypatch)
        out = eos.execute_gcs(
            {"project_id": "p", "bucket": "B"},
            {"key": "", "action": "list", "page_token": "z" * 20_000},
        )
        assert "page_token" in out.lower()


def _install_min_azure(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setitem(sys.modules, "azure.storage", types.ModuleType("azure.storage"))
    m = types.ModuleType("azure.storage.blob")
    m.BlobServiceClient = MagicMock()
    monkeypatch.setitem(sys.modules, "azure.storage.blob", m)


def _install_min_gcs(monkeypatch):
    st = types.ModuleType("google.cloud.storage")
    st.Client = MagicMock()
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", types.ModuleType("google.cloud"))
    monkeypatch.setitem(sys.modules, "google.cloud.storage", st)
    sa = types.ModuleType("google.oauth2.service_account")
    sa.Credentials = MagicMock()
    monkeypatch.setitem(sys.modules, "google.oauth2", types.ModuleType("google.oauth2"))
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", sa)
