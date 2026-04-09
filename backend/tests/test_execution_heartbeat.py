from datetime import datetime, timedelta

from services import execution_heartbeat as hb


class _FakeDB:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class _FakeStep:
    def __init__(self):
        self.id = 11
        self.job_id = 7
        self.agent_id = 5
        self.step_order = 2
        self.job = type("J", (), {"execution_token": "tok-1"})()
        self.live_phase = None
        self.live_phase_started_at = None
        self.live_reason_code = None
        self.live_reason_detail = None
        self.live_trace_id = None
        self.live_attempt = None
        self.last_activity_at = None
        self.last_progress_at = None
        self.stuck_since = datetime.utcnow()
        self.stuck_reason = "old"


def test_publish_step_heartbeat_persists_phase_and_progress(monkeypatch):
    db = _FakeDB()
    step = _FakeStep()
    fixed_now = datetime(2026, 1, 1, 0, 0, 0)
    monkeypatch.setattr(hb, "_utc_now", lambda: fixed_now)
    monkeypatch.setattr(hb, "_get_redis_client", lambda: None)

    hb.publish_step_heartbeat(
        db=db,
        step=step,
        phase="calling_agent",
        reason_code="agent_call_start",
        message="calling",
        trace_id="trace-123",
        attempt=1,
        max_retries=3,
        meaningful_progress=True,
        commit_db=True,
    )

    assert step.live_phase == "calling_agent"
    assert step.live_phase_started_at == fixed_now
    assert step.live_reason_code == "agent_call_start"
    assert step.live_trace_id == "trace-123"
    assert step.live_attempt == 1
    assert step.last_activity_at == fixed_now
    assert step.last_progress_at == fixed_now
    assert step.stuck_since is None
    assert step.stuck_reason is None
    assert db.commits == 1


def test_publish_step_heartbeat_throttles_db_for_same_phase(monkeypatch):
    db = _FakeDB()
    step = _FakeStep()
    now = datetime(2026, 1, 1, 0, 0, 0)
    step.live_phase = "calling_agent"
    step.last_activity_at = now
    step.live_reason_code = "old_reason"

    monkeypatch.setattr(hb, "_utc_now", lambda: now + timedelta(seconds=5))
    monkeypatch.setattr(hb, "_get_redis_client", lambda: None)

    hb.publish_step_heartbeat(
        db=db,
        step=step,
        phase="calling_agent",
        reason_code="agent_call_start",
        message="still calling",
        meaningful_progress=False,
        commit_db=True,
    )

    # No DB snapshot update expected due to throttle interval.
    assert step.live_reason_code == "old_reason"
    assert db.commits == 0


def test_publish_step_heartbeat_writes_redis_payload(monkeypatch):
    db = _FakeDB()
    step = _FakeStep()
    calls = {}

    class _FakeRedis:
        def setex(self, key, ttl, value):
            calls["key"] = key
            calls["ttl"] = ttl
            calls["value"] = value

    monkeypatch.setattr(hb, "_get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr(hb, "_utc_now", lambda: datetime(2026, 1, 1, 0, 0, 0))

    hb.publish_step_heartbeat(
        db=db,
        step=step,
        phase="planning",
        reason_code="payload_ready",
        message="payload ready",
        reason_detail={"kind": "planning", "elapsed_ms": 12, "loop": {"round_idx": 3}},
        meaningful_progress=False,
        commit_db=False,
    )

    assert calls["key"] == "sandhi:step_live:v1:7:11"
    assert isinstance(calls["ttl"], int)
    assert b'"phase":"planning"' in calls["value"]
    assert b'"reason_detail_json"' in calls["value"]
