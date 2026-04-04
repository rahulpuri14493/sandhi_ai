"""Targeted tests for main.py (startup helpers, middleware, lifespan, top-level routes)."""

import os
import uuid

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

import main as main_mod
from core.security import create_access_token, get_password_hash
from main import RequestLoggingMiddleware
from models.user import User, UserRole


def test_run_alembic_startup_with_retries_succeeds_after_failures(monkeypatch):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("db not ready")

    monkeypatch.setattr(main_mod, "run_alembic_upgrade", flaky)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    main_mod._run_alembic_startup_with_retries(max_attempts=5)
    assert calls["n"] == 2


def test_run_alembic_startup_with_retries_raises_last_error(monkeypatch):
    monkeypatch.setattr(
        main_mod,
        "run_alembic_upgrade",
        lambda: (_ for _ in ()).throw(ConnectionError("permanent")),
    )
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    with pytest.raises(ConnectionError, match="permanent"):
        main_mod._run_alembic_startup_with_retries(max_attempts=3)


def test_log_s3_startup_status_ok(monkeypatch, caplog):
    monkeypatch.setattr(
        main_mod,
        "verify_s3_connectivity",
        lambda: {"ok": True, "detail": "all good"},
    )
    with caplog.at_level("INFO"):
        main_mod._log_s3_startup_status()
    assert "passed" in caplog.text.lower()


def test_log_s3_startup_status_warning(monkeypatch, caplog):
    monkeypatch.setattr(
        main_mod,
        "verify_s3_connectivity",
        lambda: {"ok": False, "detail": "unreachable"},
    )
    with caplog.at_level("WARNING"):
        main_mod._log_s3_startup_status()
    assert "FAILED" in caplog.text


@pytest.mark.asyncio
async def test_lifespan_starts_scheduler_when_enabled(monkeypatch):
    started = []
    stopped = []

    monkeypatch.setattr(main_mod.settings, "DISABLE_SCHEDULER", False, raising=False)
    monkeypatch.setattr(
        main_mod._scheduler_service,
        "start",
        lambda *a, **k: started.append(1),
    )
    monkeypatch.setattr(
        main_mod._scheduler_service,
        "stop",
        lambda *a, **k: stopped.append(1),
    )

    async with main_mod.lifespan(main_mod.app):
        assert started == [1]
    assert stopped == [1]


@pytest.mark.asyncio
async def test_lifespan_stops_scheduler_when_disabled(monkeypatch):
    stopped = []
    monkeypatch.setattr(main_mod.settings, "DISABLE_SCHEDULER", True, raising=False)
    monkeypatch.setattr(
        main_mod._scheduler_service,
        "stop",
        lambda *a, **k: stopped.append(1),
    )
    async with main_mod.lifespan(main_mod.app):
        pass
    assert stopped == [1]


def _minimal_scope(*, path="/p", client=("10.0.0.1", 4444), extra_headers=None):
    hdrs = list(extra_headers or [])
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": hdrs,
        "client": client,
        "server": ("127.0.0.1", 8000),
    }


@pytest.mark.asyncio
async def test_request_logging_middleware_success_and_request_id_header():
    app = Starlette()
    mw = RequestLoggingMiddleware(app)

    async def ok_next(request: Request):
        r = Response(content=b'{"x":1}', media_type="application/json", status_code=201)
        return r

    req = Request(_minimal_scope(path="/api/x", extra_headers=[(b"x-request-id", b"fixed-id")]))
    resp = await mw.dispatch(req, ok_next)
    assert resp.status_code == 201
    assert resp.headers["x-request-id"] == "fixed-id"


@pytest.mark.asyncio
async def test_request_logging_middleware_generates_request_id():
    app = Starlette()
    mw = RequestLoggingMiddleware(app)

    async def ok_next(request: Request):
        return Response(status_code=204)

    req = Request(_minimal_scope(path="/z"))
    resp = await mw.dispatch(req, ok_next)
    assert resp.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_request_logging_middleware_call_next_exception():
    app = Starlette()
    mw = RequestLoggingMiddleware(app)

    async def boom(request: Request):
        raise ValueError("handler boom")

    req = Request(_minimal_scope(path="/err"))
    with pytest.raises(ValueError, match="handler boom"):
        await mw.dispatch(req, boom)


@pytest.mark.asyncio
async def test_request_logging_middleware_print_failure_still_returns(monkeypatch):
    app = Starlette()
    mw = RequestLoggingMiddleware(app)

    def bad_print(*a, **k):
        raise OSError("stdout closed")

    monkeypatch.setattr("builtins.print", bad_print)

    async def ok_next(request: Request):
        return Response(status_code=200)

    req = Request(_minimal_scope())
    resp = await mw.dispatch(req, ok_next)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_logging_middleware_json_response_preview(monkeypatch, caplog):
    monkeypatch.setenv("LOG_API_RESPONSE_BODY", "1")
    app = Starlette()
    mw = RequestLoggingMiddleware(app)

    async def ok_next(request: Request):
        return Response(
            content=b'{"hello":"world"}',
            media_type="application/json; charset=utf-8",
        )

    req = Request(_minimal_scope())
    with caplog.at_level("INFO"):
        resp = await mw.dispatch(req, ok_next)
    assert resp.status_code == 200
    assert any("preview=" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_request_logging_middleware_json_preview_truncation(monkeypatch, caplog):
    monkeypatch.setenv("LOG_API_RESPONSE_BODY", "true")
    app = Starlette()
    mw = RequestLoggingMiddleware(app)
    big = b'{"k":"' + (b"x" * 5000) + b'"}'

    async def ok_next(request: Request):
        return Response(content=big, media_type="application/json")

    req = Request(_minimal_scope())
    with caplog.at_level("INFO"):
        await mw.dispatch(req, ok_next)
    assert any("truncated" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_request_logging_middleware_preview_log_error_suppressed(monkeypatch):
    """Inner except around response preview must not fail the request."""
    monkeypatch.setenv("LOG_API_RESPONSE_BODY", "1")
    app = Starlette()
    mw = RequestLoggingMiddleware(app)

    real_info = main_mod.request_logger.info

    def selective_info(*a, **kw):
        if a and isinstance(a[0], str) and "preview=" in a[0]:
            raise RuntimeError("logging backend down")
        return real_info(*a, **kw)

    monkeypatch.setattr(main_mod.request_logger, "info", selective_info)

    async def ok_next(request: Request):
        return Response(
            content=b'{"a":1}',
            media_type="application/json",
            status_code=200,
        )

    req = Request(_minimal_scope())
    resp = await mw.dispatch(req, ok_next)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_request_logging_middleware_no_client_host():
    app = Starlette()
    mw = RequestLoggingMiddleware(app)
    scope = _minimal_scope()
    del scope["client"]

    async def ok_next(request: Request):
        return Response(status_code=200)

    req = Request(scope)
    resp = await mw.dispatch(req, ok_next)
    assert resp.status_code == 200


def test_healthz_unauthenticated(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_and_health_with_auth(client, db_session, monkeypatch):
    monkeypatch.setattr(
        main_mod,
        "verify_s3_connectivity",
        lambda: {"ok": True, "detail": "ok"},
    )
    monkeypatch.setattr(
        main_mod,
        "get_queue_health",
        lambda: {"ok": True, "detail": "ok"},
    )

    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"maincov-{unique}@test.com",
        password_hash=get_password_hash("pw"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": str(user.id)})
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/", headers=headers)
    assert r.status_code == 200
    assert r.json().get("message") == "Sandhi AI API"

    r2 = client.get("/health", headers=headers)
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "healthy"
    assert body["storage"]["ok"] is True
    assert body["queue"]["ok"] is True


def test_health_degraded_when_storage_down(client, db_session, monkeypatch):
    monkeypatch.setattr(
        main_mod,
        "verify_s3_connectivity",
        lambda: {"ok": False, "detail": "down"},
    )
    monkeypatch.setattr(
        main_mod,
        "get_queue_health",
        lambda: {"ok": True, "detail": "ok"},
    )

    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"maindeg-{unique}@test.com",
        password_hash=get_password_hash("pw"),
        role=UserRole.BUSINESS,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token({"sub": str(user.id)})

    r = client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
