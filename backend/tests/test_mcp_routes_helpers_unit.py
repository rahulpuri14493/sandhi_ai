"""Unit tests for pure helpers in api.routes.mcp."""

import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core.security import create_access_token, get_password_hash
from models.user import User, UserRole


def test_estimate_json_size_bytes_ok():
    from api.routes import mcp as m

    assert m._estimate_json_size_bytes({"a": 1}) > 0


def test_estimate_json_size_bytes_non_serializable_returns_zero():
    from api.routes import mcp as m

    class _X:
        pass

    assert m._estimate_json_size_bytes({"x": _X()}) == 0


def test_op_to_response_parses_json_and_invalid_payload():
    from api.routes import mcp as m

    now = datetime.utcnow()
    op = SimpleNamespace(
        operation_id="op1",
        idempotency_key="ik",
        tool_name="platform_1_pg",
        status="success",
        response_payload=json.dumps({"rows": 1}),
        error_message=None,
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    r = m._op_to_response(op)
    assert r.result == {"rows": 1}

    op2 = SimpleNamespace(
        operation_id="op2",
        idempotency_key="ik",
        tool_name="t",
        status="failure",
        response_payload="{broken",
        error_message="e",
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    r2 = m._op_to_response(op2)
    assert r2.result is None


def test_is_write_capable_tool_descriptor():
    from api.routes import mcp as m

    assert m._is_write_capable_tool_descriptor({"name": "customer_upsert"}) is True
    assert m._is_write_capable_tool_descriptor({"name": "read_rows"}) is False
    assert (
        m._is_write_capable_tool_descriptor(
            {"name": "x", "inputSchema": {"properties": {"operation_type": {"enum": ["merge"]}}}}
        )
        is True
    )
    assert m._is_write_capable_tool_descriptor({}) is False


def test_require_platform_tool_rejects_non_platform_name(db_session):
    from api.routes import mcp as m

    u = User(
        email=f"mcp-h-{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    with pytest.raises(HTTPException) as e:
        m._require_platform_tool_for_user(db_session, u.id, "not_a_platform_tool")
    assert e.value.status_code == 400


def test_require_platform_tool_rejects_unknown_id(db_session):
    from api.routes import mcp as m

    u = User(
        email=f"mcp-h2-{uuid.uuid4().hex[:8]}@t.com",
        password_hash=get_password_hash("p"),
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    with pytest.raises(HTTPException) as e:
        m._require_platform_tool_for_user(db_session, u.id, "platform_999999_postgres")
    assert e.value.status_code == 404
