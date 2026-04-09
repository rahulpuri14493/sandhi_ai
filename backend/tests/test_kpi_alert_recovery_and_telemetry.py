from datetime import datetime, timedelta

from core.security import create_access_token, get_password_hash
from models.agent import Agent, AgentStatus, PricingModel
from models.job import Job, JobStatus, WorkflowStep
from models.user import User, UserRole
from services import business_kpi_alerts, developer_kpi_alerts


def _mk_user(db_session, *, role: UserRole, email: str):
    u = User(email=email, password_hash=get_password_hash("pw123456"), role=role)
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u, create_access_token({"sub": u.id})


class _FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def setex(self, key, _ttl, value):
        self.kv[key] = value


class _FakeHttpClient:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json):
        self.sink.append({"url": url, "json": json})
        return None


def test_business_kpi_recovery_alert(monkeypatch):
    redis = _FakeRedis()
    sent = []
    monkeypatch.setattr(business_kpi_alerts, "_get_redis_client", lambda: redis)
    monkeypatch.setattr(
        business_kpi_alerts.httpx,
        "Client",
        lambda timeout=6.0: _FakeHttpClient(sent),
    )
    monkeypatch.setattr(business_kpi_alerts.settings, "BUSINESS_KPI_ALERTS_ENABLED", True)
    monkeypatch.setattr(
        business_kpi_alerts.settings, "BUSINESS_KPI_ALERT_WEBHOOK_URL", "https://example.test/hook"
    )
    monkeypatch.setattr(business_kpi_alerts.settings, "BUSINESS_KPI_ALERT_COOLDOWN_SECONDS", 60)

    # Seed prior unhealthy alert meta so recovery is eligible.
    redis.setex(
        business_kpi_alerts._meta_key(7),
        3600,
        '{"last_alert_sent_at":"2026-01-01T00:00:00+00:00","last_alert_status":"at_risk"}',
    )
    business_kpi_alerts.maybe_send_business_kpi_alert(
        business_id=7,
        business_email="biz@example.com",
        kpis={"sla": {"status": "healthy"}},
    )
    assert len(sent) == 1
    assert sent[0]["json"]["event"] == "business_kpi_sla_recovered"


def test_developer_kpi_recovery_alert(monkeypatch):
    redis = _FakeRedis()
    sent = []
    monkeypatch.setattr(developer_kpi_alerts, "_get_redis_client", lambda: redis)
    monkeypatch.setattr(
        developer_kpi_alerts.httpx,
        "Client",
        lambda timeout=6.0: _FakeHttpClient(sent),
    )
    monkeypatch.setattr(developer_kpi_alerts.settings, "DEVELOPER_KPI_ALERTS_ENABLED", True)
    monkeypatch.setattr(
        developer_kpi_alerts.settings, "DEVELOPER_KPI_ALERT_WEBHOOK_URL", "https://example.test/hook"
    )
    monkeypatch.setattr(developer_kpi_alerts.settings, "DEVELOPER_KPI_ALERT_COOLDOWN_SECONDS", 60)

    redis.setex(
        developer_kpi_alerts._meta_key(11),
        3600,
        '{"last_alert_sent_at":"2026-01-01T00:00:00+00:00","last_alert_status":"breached"}',
    )
    developer_kpi_alerts.maybe_send_developer_kpi_alert(
        developer_id=11,
        developer_email="dev@example.com",
        kpis={"sla": {"status": "healthy"}},
    )
    assert len(sent) == 1
    assert sent[0]["json"]["event"] == "developer_kpi_sla_recovered"


def test_business_dashboard_reason_detail_plain_text_fallback(client, db_session):
    business, biz_token = _mk_user(db_session, role=UserRole.BUSINESS, email="biz-telemetry@example.com")
    developer, _ = _mk_user(db_session, role=UserRole.DEVELOPER, email="dev-telemetry@example.com")
    headers = {"Authorization": f"Bearer {biz_token}"}

    agent = Agent(
        developer_id=developer.id,
        name="Telemetry Agent",
        description="Telemetry",
        capabilities=["analyze"],
        input_schema={},
        output_schema={},
        pricing_model=PricingModel.PAY_PER_USE,
        price_per_task=1.0,
        price_per_communication=0.1,
        status=AgentStatus.ACTIVE,
        api_endpoint="https://agent.telemetry.example.com",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)

    job = Job(business_id=business.id, title="Telemetry Job", description="x", status=JobStatus.IN_PROGRESS)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="in_progress",
        started_at=datetime.utcnow() - timedelta(seconds=10),
        live_phase="calling_tool",
        live_reason_code="tool_error",
        live_reason_detail="tool returned non-json payload",
    )
    db_session.add(step)
    db_session.commit()

    resp = client.get("/api/businesses/agents/performance", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["agents"], "Expected at least one agent row"
    latest = data["agents"][0].get("latest_runtime") or {}
    detail = latest.get("reason_detail") or {}
    assert isinstance(detail, dict)
    assert "message" in detail
    assert "non-json payload" in str(detail.get("message"))

