"""Unit tests for services/job_file_storage.py (local backend, no real S3)."""

from pathlib import Path

import pytest

from services.job_file_storage import (
    sanitize_filename,
    redact_file_metadata,
    has_readable_source,
    _is_s3_backend,
    verify_s3_connectivity,
    persist_file,
    delete_file,
    delete_file_sync,
    open_local_download_path,
    materialize_to_temp_path,
    cleanup_temp_path,
    PRIVATE_FILE_KEYS,
)


# ---------- sanitize_filename ----------

class TestSanitizeFilename:
    def test_normal_name_unchanged(self):
        assert sanitize_filename("report.pdf") == "report.pdf"

    def test_strips_directory_traversal(self):
        assert sanitize_filename("../../etc/passwd") == "passwd"

    def test_special_chars_replaced(self):
        result = sanitize_filename("my file (1)!@#$.txt")
        assert "@" not in result
        assert "#" not in result
        assert "$" not in result
        assert result.endswith(".txt")

    def test_empty_or_blank_returns_default(self):
        assert sanitize_filename("") == "document.bin"
        assert sanitize_filename("   ") == "document.bin"
        assert sanitize_filename(None) == "document.bin"

    def test_dot_names_rejected(self):
        assert sanitize_filename(".") == "document.bin"
        assert sanitize_filename("..") == "document.bin"

    def test_long_name_truncated_preserving_extension(self):
        long_name = "a" * 300 + ".pdf"
        result = sanitize_filename(long_name)
        assert len(result) <= 180
        assert result.endswith(".pdf")


# ---------- redact_file_metadata ----------

class TestRedactFileMetadata:
    def test_removes_private_keys(self):
        meta = {
            "id": "abc",
            "name": "f.txt",
            "type": "text/plain",
            "size": 100,
            "path": "/some/path",
            "bucket": "b",
            "key": "k",
            "storage": "s3",
        }
        redacted = redact_file_metadata(meta)
        for k in PRIVATE_FILE_KEYS:
            assert k not in redacted
        assert redacted["id"] == "abc"
        assert redacted["name"] == "f.txt"

    def test_passthrough_when_no_private_keys(self):
        meta = {"id": "x", "name": "y", "type": "t", "size": 1}
        assert redact_file_metadata(meta) == meta


# ---------- has_readable_source ----------

class TestHasReadableSource:
    def test_local_path(self):
        assert has_readable_source({"path": "/a/b.txt"}) is True

    def test_s3_source(self):
        assert has_readable_source({"storage": "s3", "bucket": "b", "key": "k"}) is True

    def test_missing_fields(self):
        assert has_readable_source({}) is False
        assert has_readable_source({"storage": "s3"}) is False
        assert has_readable_source({"storage": "s3", "bucket": "b"}) is False


# ---------- _is_s3_backend ----------

class TestIsS3Backend:
    def test_local_default(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")
        assert _is_s3_backend() is False

    def test_s3_enabled(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "s3")
        assert _is_s3_backend() is True

    def test_s3_uppercase_trimmed(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", " S3 ")
        assert _is_s3_backend() is True


# ---------- verify_s3_connectivity (local mode) ----------

class TestVerifyS3ConnectivityLocal:
    def test_returns_ok_when_local(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")
        result = verify_s3_connectivity()
        assert result["ok"] is True
        assert "local" in result["detail"].lower() or "not enabled" in result["detail"].lower()


# ---------- persist_file (local) ----------

class TestPersistFileLocal:
    @pytest.mark.asyncio
    async def test_persist_creates_file_and_returns_metadata(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")

        meta = await persist_file("test.txt", b"hello world", "text/plain", job_id=42)

        assert meta["name"] == "test.txt"
        assert meta["type"] == "text/plain"
        assert meta["size"] == 11
        assert "id" in meta
        assert "path" in meta
        assert Path(meta["path"]).exists()

        # Cleanup
        Path(meta["path"]).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_persist_sanitizes_filename(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")

        meta = await persist_file("../../evil.txt", b"x", None, job_id=1)
        assert ".." not in meta["name"]
        assert meta["name"] == "evil.txt"

        Path(meta["path"]).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_persist_without_job_id(self, monkeypatch):
        from core.config import settings
        monkeypatch.setattr(settings, "OBJECT_STORAGE_BACKEND", "local")

        meta = await persist_file("f.txt", b"data", "text/plain")
        assert "path" in meta
        Path(meta["path"]).unlink(missing_ok=True)


# ---------- delete_file / delete_file_sync (local) ----------

class TestDeleteFileLocal:
    @pytest.mark.asyncio
    async def test_delete_removes_local_file(self, tmp_path):
        p = tmp_path / "to_delete.txt"
        p.write_text("bye")
        meta = {"path": str(p)}

        await delete_file(meta)
        assert not p.exists()

    @pytest.mark.asyncio
    async def test_delete_missing_file_no_error(self):
        meta = {"path": "/nonexistent/path/xyz.txt"}
        await delete_file(meta)  # should not raise

    def test_delete_sync_removes_local_file(self, tmp_path):
        p = tmp_path / "sync_del.txt"
        p.write_text("bye")
        meta = {"path": str(p)}

        delete_file_sync(meta)
        assert not p.exists()

    def test_delete_sync_missing_no_error(self):
        delete_file_sync({"path": "/nonexistent/xyz.txt"})  # no raise

    @pytest.mark.asyncio
    async def test_delete_empty_meta_no_error(self):
        await delete_file({})  # no path, no s3 — no-op


# ---------- open_local_download_path ----------

class TestOpenLocalDownloadPath:
    def test_returns_path_when_exists(self, tmp_path):
        p = tmp_path / "dl.txt"
        p.write_text("content")
        result = open_local_download_path({"path": str(p)})
        assert result == p

    def test_raises_when_missing(self):
        with pytest.raises(FileNotFoundError):
            open_local_download_path({"path": "/nonexistent/file.txt"})


# ---------- materialize_to_temp_path (local) ----------

class TestMaterializeToTempPathLocal:
    @pytest.mark.asyncio
    async def test_returns_original_path_for_local(self, tmp_path):
        p = tmp_path / "local.txt"
        p.write_text("hi")
        meta = {"path": str(p), "name": "local.txt"}

        result = await materialize_to_temp_path(meta)
        assert result == str(p)

    @pytest.mark.asyncio
    async def test_raises_for_no_source(self):
        with pytest.raises(ValueError, match="no readable source"):
            await materialize_to_temp_path({"name": "orphan.txt"})


# ---------- cleanup_temp_path ----------

class TestCleanupTempPath:
    def test_no_op_for_local_files(self, tmp_path):
        p = tmp_path / "keep.txt"
        p.write_text("keep me")
        cleanup_temp_path({"path": str(p)}, str(p))
        assert p.exists()  # not deleted

    def test_removes_temp_for_s3(self, tmp_path):
        p = tmp_path / "temp.txt"
        p.write_text("temp s3")
        cleanup_temp_path({"storage": "s3", "bucket": "b", "key": "k"}, str(p))
        assert not p.exists()
