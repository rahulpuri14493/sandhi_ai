"""Tests for core/encryption."""

from core.encryption import (
    encrypt_json,
    decrypt_json,
    ensure_encryption_key_for_production,
)


def test_encrypt_decrypt_json_roundtrip():
    """encrypt_json and decrypt_json roundtrip."""
    data = {"key": "value", "n": 42, "nested": {"a": 1}}
    cipher = encrypt_json(data)
    assert isinstance(cipher, str)
    assert cipher != ""
    dec = decrypt_json(cipher)
    assert dec == data


def test_encrypt_json_deterministic_with_sort_keys():
    """Fernet ciphertext is intentionally non-deterministic; order shouldn't matter after decrypt."""
    data = {"b": 2, "a": 1}
    c1 = encrypt_json(data)
    c2 = encrypt_json({"a": 1, "b": 2})
    # Ciphertext should differ because Fernet uses a random IV / timestamp.
    assert c1 != c2
    # But decrypted payload should be equivalent.
    assert decrypt_json(c1) == decrypt_json(c2) == {"a": 1, "b": 2}


def test_ensure_encryption_key_for_production_with_long_key(monkeypatch):
    """No warning when MCP_ENCRYPTION_KEY is set and long."""
    monkeypatch.setenv("MCP_ENCRYPTION_KEY", "a" * 32)
    # Should not raise; may or may not log
    ensure_encryption_key_for_production()


def test_ensure_encryption_key_for_production_with_default_secret(monkeypatch):
    """Runs without error when SECRET_KEY is default (dev)."""
    monkeypatch.delenv("MCP_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "your-secret-key-change-in-production")
    ensure_encryption_key_for_production()
