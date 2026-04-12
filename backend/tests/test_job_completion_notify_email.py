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


def test_maybe_notify_skips_when_smtp_incomplete(db_session, monkeypatch):
    from services import job_completion_notify_email as mod

    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_ENABLED", True)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_USER", "u@example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_PASSWORD", "")
    calls = []

    monkeypatch.setattr(mod, "_smtp_send_message", lambda **k: calls.append(1))
    u = User(email="e@test.com", password_hash="x", role=UserRole.BUSINESS)
    db_session.add(u)
    db_session.commit()
    mod.maybe_notify_job_completed_email(job_id=1, business_id=u.id, title="T")
    assert calls == []


def test_maybe_notify_skips_without_business_email(monkeypatch):
    from services import job_completion_notify_email as mod

    class _Sess:
        def query(self, _model):
            return self

        def filter(self, *_a, **_k):
            return self

        def first(self):
            return type("Biz", (), {"email": ""})()

        def close(self):
            return None

    monkeypatch.setattr(mod, "SessionLocal", _Sess)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_ENABLED", True)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_USER", "relay@example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_PASSWORD", "pw")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_LINK_BASE_URL", "")

    monkeypatch.setattr(mod, "_smtp_send_message", lambda **k: (_ for _ in ()).throw(AssertionError("no send")))
    mod.maybe_notify_job_completed_email(job_id=9, business_id=1, title="T")


def test_maybe_notify_logs_on_smtp_failure(db_session, monkeypatch):
    from services import job_completion_notify_email as mod

    monkeypatch.setattr(mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_ENABLED", True)
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_USER", "relay@example.com")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_SMTP_PASSWORD", "pw")
    monkeypatch.setattr(mod.settings, "JOB_COMPLETION_EMAIL_LINK_BASE_URL", "")

    def _boom(**_kw):
        raise OSError("smtp down")

    monkeypatch.setattr(mod, "_smtp_send_message", _boom)
    u = User(email="ok@test.com", password_hash="x", role=UserRole.BUSINESS)
    db_session.add(u)
    db_session.commit()
    mod.maybe_notify_job_completed_email(job_id=3, business_id=u.id, title="T")


def test_smtp_send_message_ssl_path(monkeypatch):
    from services import job_completion_notify_email as mod

    class _SSLCtx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            self.u, self.p = u, p

        def send_message(self, msg):
            self.subj = msg["Subject"]

    fake = _SSLCtx()
    monkeypatch.setattr("smtplib.SMTP_SSL", lambda *a, **k: fake)
    mod._smtp_send_message(
        host="h",
        port=465,
        user="u",
        password="p",
        mail_from="a@b.com",
        mail_to="c@d.com",
        subject="S",
        body="B",
        use_tls=False,
        use_ssl=True,
    )
    assert fake.subj == "S"


def test_smtp_send_message_starttls_path(monkeypatch):
    from services import job_completion_notify_email as mod

    class _SMTP:
        def __init__(self, *a, **k):
            self.ehloed = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            self.ehloed += 1

        def starttls(self, context=None):
            self.tls = True

        def login(self, u, p):
            self.u, self.p = u, p

        def send_message(self, msg):
            self.sent = True

    fake = _SMTP()
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: fake)
    mod._smtp_send_message(
        host="h",
        port=587,
        user="u",
        password="p",
        mail_from="a@b.com",
        mail_to="c@d.com",
        subject="S",
        body="B",
        use_tls=True,
        use_ssl=False,
    )
    assert fake.sent is True
