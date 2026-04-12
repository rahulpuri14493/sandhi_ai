"""Unit tests for core.security edge paths."""

import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi import HTTPException

from core import security
from core.security import (
    create_access_token,
    get_current_business_user,
    get_current_developer_user,
    get_current_user_optional,
    get_password_hash,
    verify_password,
)
from models.user import User, UserRole


def test_verify_password_rejects_non_bcrypt_hash():
    assert verify_password("x", "plain-not-bcrypt") is False


def test_verify_password_returns_false_on_check_failure(monkeypatch):
    import core.security as sec

    h = get_password_hash("secret123")
    monkeypatch.setattr(sec.bcrypt, "checkpw", lambda a, b: False)
    assert verify_password("secret123", h) is False


def test_verify_password_returns_false_on_exception(monkeypatch):
    import core.security as sec

    def boom(pw, hh):
        raise RuntimeError("bcrypt broken")

    monkeypatch.setattr(sec.bcrypt, "checkpw", boom)
    assert verify_password("x", "$2b$12$" + "x" * 50) is False


def test_get_password_hash_truncates_over_72_bytes():
    long_pw = "a" * 100
    h = get_password_hash(long_pw)
    assert verify_password("a" * 72, h) is True


def test_create_access_token_with_expires_delta():
    tok = create_access_token({"sub": 5}, expires_delta=timedelta(minutes=60))
    payload = jwt.decode(tok, security.SECRET_KEY, algorithms=[security.ALGORITHM])
    assert payload["sub"] == "5"
    assert "exp" in payload


def test_get_current_user_optional_no_token(db_session):
    assert get_current_user_optional(None, db_session) is None


def test_get_current_user_optional_invalid_jwt(db_session):
    assert get_current_user_optional("not-a.jwt.token", db_session) is None


def test_get_current_business_user_rejects_developer(db_session):
    dev = User(
        email="d@example.com",
        password_hash=get_password_hash("p"),
        role=UserRole.DEVELOPER,
    )
    db_session.add(dev)
    db_session.commit()
    db_session.refresh(dev)
    with pytest.raises(HTTPException) as ei:
        get_current_business_user(dev)
    assert ei.value.status_code == 403


def test_get_current_developer_user_rejects_business(db_session):
    biz = User(
        email=f"b_{uuid.uuid4().hex[:8]}@example.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    db_session.add(biz)
    db_session.commit()
    db_session.refresh(biz)
    with pytest.raises(HTTPException) as ei:
        get_current_developer_user(biz)
    assert ei.value.status_code == 403
