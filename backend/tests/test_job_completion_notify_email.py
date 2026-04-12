"""Tests for optional job-completion SMTP notification."""

from models.user import User, UserRole


def test_maybe_notify_skips_when_disabled(db_session, monkeypatch):
    from services import job_completion_notify_email as mod

    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_ENABLED", False)
    calls = []

    def _no_smtp(**_kw):
        calls.append(1)

    monkeypatch.setattr(mod, "_smtp_send_message", _no_smtp)
    u = User(
        email="biz_notify@test.com",
        password_hash="x",
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()
    mod.maybe_notify_job_completed_email(job_id=1, business_id=u.id, title="T")
    assert calls == []


def test_maybe_notify_sends_when_enabled(db_session, monkeypatch):
    from services import job_completion_notify_email as mod

    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(mod, "SessionLocal", lambda: db_session)

    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_ENABLED", True)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_PORT", 587)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_USER", "relay@example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_PASSWORD", "secret")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_FROM", "")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_LINK_BASE_URL", "http://localhost:3000/sandhi_ai")
    captured = {}

    def _fake_send(**kw):
        captured.update(kw)

    monkeypatch.setattr(mod, "_smtp_send_message", _fake_send)
    u = User(
        email="owner@gmail.com",
        password_hash="x",
        role=UserRole.BUSINESS,
    )
    db_session.add(u)
    db_session.commit()

    mod.maybe_notify_job_completed_email(job_id=42, business_id=u.id, title="My job")

    assert captured.get("mail_to") == "owner@gmail.com"
    assert "42" in (captured.get("body") or "")
    assert "My job" in (captured.get("body") or "")
    assert "finished successfully" in (captured.get("body") or "").lower()
    assert "/jobs/42" in (captured.get("body") or "")
