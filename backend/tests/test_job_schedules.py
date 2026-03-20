"""Unit tests for job schedule CRUD endpoints (one-time only, one per job).

Covers:
- Happy path: create, get, update (singular endpoints)
- Schema validation: past date rejected, invalid timezone rejected
- One-per-job constraint: duplicate schedule returns 400
- Authorization: wrong user cannot access another user's schedules
- Role independence: developer users can manage schedules on their own jobs
- 404 cases: job not found, schedule not found
- Status transitions: active → inactive clears next_run_time
- Job status transitions: creating schedule → IN_QUEUE, rescheduling → IN_QUEUE
- Execution guards: blocked during IN_PROGRESS
- next_run_time is computed on create and recomputed on update
- Cascade: deleting a job removes its schedule
- Creating a job with inline schedule_scheduled_at via POST /api/jobs
- List all schedules with pagination and filters
- Rerun endpoint for failed and cancelled jobs
- Execution history endpoint
"""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from db.database import get_db
from main import app
from models.agent import Agent
from models.job import Job, JobSchedule, JobStatus, ScheduleStatus, WorkflowStep
from models.user import User, UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FUTURE_DT = (datetime.utcnow() + timedelta(days=30)).isoformat()

ONE_TIME_PAYLOAD = {
    "timezone": "UTC",
    "scheduled_at": FUTURE_DT,
}


def _make_user(db_session, role=UserRole.BUSINESS):
    unique = uuid.uuid4().hex[:8]
    user = User(
        email=f"user-{unique}@test.com",
        password_hash=get_password_hash("pass123"),
        role=role,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_job(db_session, user, status=JobStatus.DRAFT):
    job = Job(
        business_id=user.id,
        title="Test Job",
        description="desc",
        status=status,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_agent(db_session, developer):
    agent = Agent(
        developer_id=developer.id,
        name=f"Agent-{uuid.uuid4().hex[:6]}",
        pricing_model="pay_per_use",
        price_per_task=1.0,
        price_per_communication=0.1,
        api_endpoint="http://example.com/api",
        status="active",
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


def _make_step(db_session, job, agent):
    step = WorkflowStep(
        job_id=job.id,
        agent_id=agent.id,
        step_order=1,
        status="completed",
        cost=1.0,
    )
    db_session.add(step)
    db_session.commit()
    db_session.refresh(step)
    return step


def _auth_headers(user):
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def schedule_client(db_session):
    """TestClient with DB override, returns (client, business_user, job)."""
    user = _make_user(db_session)
    job = _make_job(db_session, user)

    def override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c, user, job
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Schema validation (no DB needed)
# ---------------------------------------------------------------------------

class TestScheduleSchemaValidation:
    def test_valid_schedule(self):
        from schemas.job import JobScheduleCreate
        s = JobScheduleCreate(**ONE_TIME_PAYLOAD)
        assert s.scheduled_at is not None
        assert s.timezone == "UTC"

    def test_past_date_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with pytest.raises(ValidationError, match="scheduled_at must be in the future"):
            JobScheduleCreate(scheduled_at=past, timezone="UTC")

    def test_invalid_timezone_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(scheduled_at=FUTURE_DT, timezone="Fake/Zone")

    def test_missing_scheduled_at_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(timezone="UTC")

    def test_update_all_none_allowed(self):
        from schemas.job import JobScheduleUpdate
        s = JobScheduleUpdate()
        assert s.scheduled_at is None
        assert s.timezone is None
        assert s.status is None

    def test_update_past_date_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleUpdate
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with pytest.raises(ValidationError, match="scheduled_at must be in the future"):
            JobScheduleUpdate(scheduled_at=past)


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/schedule — create (singular)
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    def test_create_schedule(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "message" in body
        assert "data" in body
        data = body["data"]
        assert data["job_id"] == job.id
        assert data["timezone"] == "UTC"
        assert data["status"] == "active"
        assert data["scheduled_at"] is not None
        assert data["next_run_time"] is not None

    def test_create_schedule_inactive_has_no_next_run(self, schedule_client):
        client, user, job = schedule_client
        payload = {**ONE_TIME_PAYLOAD, "status": "inactive"}
        resp = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=payload,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["next_run_time"] is None

    def test_duplicate_schedule_rejected(self, schedule_client):
        client, user, job = schedule_client
        resp1 = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp1.status_code == 201

        resp2 = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp2.status_code == 400
        assert "already exists" in resp2.json()["detail"]

    def test_create_schedule_job_not_found(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.post(
            "/api/jobs/99999/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 404

    def test_create_schedule_wrong_user(self, schedule_client, db_session):
        client, _, job = schedule_client
        other_user = _make_user(db_session)
        resp = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(other_user),
        )
        assert resp.status_code == 404

    def test_create_schedule_unauthenticated(self, schedule_client):
        client, _, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/schedule — get (singular)
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_get_existing_schedule(self, schedule_client):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        resp = client.get(f"/api/jobs/{job.id}/schedule", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert resp.json()["job_id"] == job.id
        assert resp.json()["timezone"] == "UTC"

    def test_get_schedule_not_found(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get(f"/api/jobs/{job.id}/schedule", headers=_auth_headers(user))
        assert resp.status_code == 404

    def test_get_schedule_wrong_user(self, schedule_client, db_session):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        other_user = _make_user(db_session)
        resp = client.get(f"/api/jobs/{job.id}/schedule", headers=_auth_headers(other_user))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/jobs/{job_id}/schedule — update (singular)
# ---------------------------------------------------------------------------

class TestUpdateSchedule:
    def test_update_scheduled_at(self, schedule_client):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        new_dt = (datetime.utcnow() + timedelta(days=60)).isoformat()
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"scheduled_at": new_dt},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["next_run_time"] is not None

    def test_disable_schedule_clears_next_run_time(self, schedule_client):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"status": "inactive"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "inactive"
        assert resp.json()["data"]["next_run_time"] is None

    def test_re_enable_schedule_sets_next_run_time(self, schedule_client):
        client, user, job = schedule_client
        payload = {**ONE_TIME_PAYLOAD, "status": "inactive"}
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=payload,
            headers=_auth_headers(user),
        )
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"status": "active"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "active"
        assert resp.json()["data"]["next_run_time"] is not None

    def test_update_timezone(self, schedule_client):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"timezone": "Asia/Kolkata"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["timezone"] == "Asia/Kolkata"

    def test_update_schedule_not_found(self, schedule_client):
        client, user, job = schedule_client
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"status": "inactive"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 404

    def test_update_wrong_user_forbidden(self, schedule_client, db_session):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        other_user = _make_user(db_session)
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"status": "inactive"},
            headers=_auth_headers(other_user),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cascade: deleting a job should remove its schedule
# ---------------------------------------------------------------------------

class TestCascadeDelete:
    def test_schedule_deleted_with_job(self, schedule_client, db_session):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        client.delete(f"/api/jobs/{job.id}", headers=_auth_headers(user))

        remaining = db_session.query(JobSchedule).filter(
            JobSchedule.job_id == job.id
        ).all()
        assert remaining == []


# ---------------------------------------------------------------------------
# POST /api/jobs — inline schedule fields create schedule automatically
# ---------------------------------------------------------------------------

class TestInlineScheduleOnJobCreate:
    def test_create_job_with_schedule(self, schedule_client, db_session):
        client, user, _ = schedule_client
        resp = client.post(
            "/api/jobs",
            data={
                "title": "Scheduled Job",
                "schedule_timezone": "UTC",
                "schedule_scheduled_at": FUTURE_DT,
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        schedules = db_session.query(JobSchedule).filter(
            JobSchedule.job_id == job_id
        ).all()
        assert len(schedules) == 1
        assert schedules[0].status == ScheduleStatus.ACTIVE
        assert schedules[0].next_run_time is not None
        assert schedules[0].timezone == "UTC"

    def test_create_job_without_schedule(self, schedule_client, db_session):
        client, user, _ = schedule_client
        resp = client.post(
            "/api/jobs",
            data={"title": "Unscheduled Job"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        schedules = db_session.query(JobSchedule).filter(
            JobSchedule.job_id == job_id
        ).all()
        assert schedules == []


# ---------------------------------------------------------------------------
# GET /api/jobs/schedules/all — list all schedules with filters
# ---------------------------------------------------------------------------

class TestListAllSchedules:
    def test_list_empty(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get("/api/jobs/schedules/all", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_returns_schedule_with_job_info(self, schedule_client):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        resp = client.get("/api/jobs/schedules/all", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["job_title"] == "Test Job"
        assert data["items"][0]["job_status"] is not None
        assert data["total"] == 1

    def test_list_filter_by_schedule_status(self, schedule_client):
        client, user, job = schedule_client
        payload = {**ONE_TIME_PAYLOAD, "status": "inactive"}
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=payload,
            headers=_auth_headers(user),
        )
        resp = client.get(
            "/api/jobs/schedules/all?schedule_status=active",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 0

        resp2 = client.get(
            "/api/jobs/schedules/all?schedule_status=inactive",
            headers=_auth_headers(user),
        )
        assert resp2.status_code == 200
        assert len(resp2.json()["items"]) == 1

    def test_list_pagination_response_shape(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get("/api/jobs/schedules/all", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert data["limit"] == 10  # default
        assert data["offset"] == 0

    def test_list_custom_limit(self, schedule_client):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        resp = client.get(
            "/api/jobs/schedules/all?limit=5",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert data["total"] == 1


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/rerun — rerun a failed job
# ---------------------------------------------------------------------------

class TestRerunJob:
    def test_rerun_failed_job(self, schedule_client, db_session):
        client, user, job = schedule_client
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        _make_step(db_session, job, agent)
        job.status = JobStatus.FAILED
        job.failure_reason = "test failure"
        db_session.commit()

        resp = client.post(
            f"/api/jobs/{job.id}/rerun",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "re-execution started" in data["message"].lower()
        assert data["job_id"] == job.id
        assert data["status"] == "in_progress"

    def test_rerun_non_failed_job_rejected(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/rerun",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 400
        assert "failed or cancelled" in resp.json()["detail"].lower()

    def test_rerun_job_without_steps_rejected(self, schedule_client, db_session):
        client, user, job = schedule_client
        job.status = JobStatus.FAILED
        db_session.commit()

        resp = client.post(
            f"/api/jobs/{job.id}/rerun",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 400
        assert "workflow steps" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/schedule/history — execution history
# ---------------------------------------------------------------------------

class TestScheduleHistory:
    def test_history_empty(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get(
            f"/api/jobs/{job.id}/schedule/history",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_history_not_found_for_missing_job(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.get(
            "/api/jobs/99999/schedule/history",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Response shape validation
# ---------------------------------------------------------------------------

class TestScheduleResponseFields:
    def test_response_has_correct_fields(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        body = resp.json()
        # Wrapper has message + data
        assert "message" in body
        assert "data" in body
        data = body["data"]
        assert "id" in data
        assert "job_id" in data
        assert "status" in data
        assert "timezone" in data
        assert "scheduled_at" in data
        assert "next_run_time" in data
        assert "created_at" in data
        # Recurring fields should NOT exist
        assert "cron_expression" not in data
        assert "is_one_time" not in data
        assert "days_of_week" not in data
        assert "time" not in data


# ---------------------------------------------------------------------------
# GET /api/jobs/filter-options — dynamic filter values for job list
# ---------------------------------------------------------------------------

class TestJobFilterOptions:
    def test_returns_statuses_and_sort_options(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.get("/api/jobs/filter-options", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "statuses" in data
        assert "sort_options" in data
        # Statuses should include all JobStatus enum values
        status_values = [s["value"] for s in data["statuses"]]
        assert "draft" in status_values
        assert "in_queue" in status_values
        assert "completed" in status_values
        assert "failed" in status_values
        # Each option has value + label
        for opt in data["statuses"]:
            assert "value" in opt
            assert "label" in opt
        # Sort options
        sort_values = [s["value"] for s in data["sort_options"]]
        assert "newest" in sort_values
        assert "oldest" in sort_values


# ---------------------------------------------------------------------------
# GET /api/jobs/schedules/filter-options — dynamic filter values for schedule list
# ---------------------------------------------------------------------------

class TestScheduleFilterOptions:
    def test_returns_all_filter_sections(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.get("/api/jobs/schedules/filter-options", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "schedule_statuses" in data
        assert "job_statuses" in data
        assert "sort_options" in data
        assert "jobs" in data

    def test_schedule_statuses_include_active_inactive(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.get("/api/jobs/schedules/filter-options", headers=_auth_headers(user))
        values = [s["value"] for s in resp.json()["schedule_statuses"]]
        assert "active" in values
        assert "inactive" in values

    def test_jobs_list_contains_user_jobs(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get("/api/jobs/schedules/filter-options", headers=_auth_headers(user))
        jobs = resp.json()["jobs"]
        assert len(jobs) >= 1
        assert any(j["id"] == job.id for j in jobs)
        # Each job entry has id + title
        for j in jobs:
            assert "id" in j
            assert "title" in j


# ---------------------------------------------------------------------------
# GET /api/jobs — job list with filtering and sorting
# ---------------------------------------------------------------------------

class TestJobListFiltering:
    def test_list_jobs_default_order_newest_first(self, schedule_client, db_session):
        client, user, job1 = schedule_client
        job2 = _make_job(db_session, user, JobStatus.COMPLETED)
        resp = client.get("/api/jobs", headers=_auth_headers(user))
        assert resp.status_code == 200
        ids = [j["id"] for j in resp.json()]
        # job2 was created after job1, so it should appear first in newest order
        assert ids.index(job2.id) < ids.index(job1.id)

    def test_list_jobs_oldest_first(self, schedule_client, db_session):
        client, user, job1 = schedule_client
        job2 = _make_job(db_session, user, JobStatus.COMPLETED)
        resp = client.get("/api/jobs?sort=oldest", headers=_auth_headers(user))
        assert resp.status_code == 200
        ids = [j["id"] for j in resp.json()]
        assert ids.index(job1.id) < ids.index(job2.id)

    def test_list_jobs_filter_by_status(self, schedule_client, db_session):
        client, user, job1 = schedule_client  # DRAFT
        job2 = _make_job(db_session, user, JobStatus.COMPLETED)
        resp = client.get("/api/jobs?job_status=completed", headers=_auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == job2.id

    def test_list_jobs_filter_returns_empty_for_no_match(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.get("/api/jobs?job_status=failed", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Developer role can access schedule endpoints (role independence)
# ---------------------------------------------------------------------------

class TestDeveloperUserScheduleAccess:
    """Verify schedule endpoints work for any authenticated user, not just BUSINESS."""

    def _dev_client(self, db_session):
        """Create a developer user, a job they own, and a test client."""
        dev = _make_user(db_session, role=UserRole.DEVELOPER)
        job = _make_job(db_session, dev)

        def override():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = override
        return TestClient(app), dev, job

    def test_developer_can_create_schedule(self, db_session):
        client, dev, job = self._dev_client(db_session)
        try:
            resp = client.post(
                f"/api/jobs/{job.id}/schedule",
                json=ONE_TIME_PAYLOAD,
                headers=_auth_headers(dev),
            )
            assert resp.status_code == 201
            assert resp.json()["data"]["job_id"] == job.id
        finally:
            app.dependency_overrides.clear()

    def test_developer_can_get_schedule(self, db_session):
        client, dev, job = self._dev_client(db_session)
        try:
            client.post(
                f"/api/jobs/{job.id}/schedule",
                json=ONE_TIME_PAYLOAD,
                headers=_auth_headers(dev),
            )
            resp = client.get(
                f"/api/jobs/{job.id}/schedule",
                headers=_auth_headers(dev),
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_developer_can_update_schedule(self, db_session):
        client, dev, job = self._dev_client(db_session)
        try:
            client.post(
                f"/api/jobs/{job.id}/schedule",
                json=ONE_TIME_PAYLOAD,
                headers=_auth_headers(dev),
            )
            resp = client.put(
                f"/api/jobs/{job.id}/schedule",
                json={"timezone": "US/Eastern"},
                headers=_auth_headers(dev),
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_developer_can_list_schedules(self, db_session):
        client, dev, job = self._dev_client(db_session)
        try:
            client.post(
                f"/api/jobs/{job.id}/schedule",
                json=ONE_TIME_PAYLOAD,
                headers=_auth_headers(dev),
            )
            resp = client.get(
                "/api/jobs/schedules/all",
                headers=_auth_headers(dev),
            )
            assert resp.status_code == 200
            assert len(resp.json()["items"]) == 1
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Execution state guards (IN_PROGRESS blocks create/update)
# ---------------------------------------------------------------------------

class TestExecutionStateGuards:
    """Verify that create/update are blocked when job is IN_PROGRESS."""

    def _make_client(self, db_session):
        user = _make_user(db_session)
        job = _make_job(db_session, user, status=JobStatus.DRAFT)

        def override():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = override
        return TestClient(app), user, job

    def test_create_blocked_when_in_progress(self, db_session):
        user = _make_user(db_session)
        job = _make_job(db_session, user, status=JobStatus.IN_PROGRESS)

        def override():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = override
        try:
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/jobs/{job.id}/schedule",
                    json=ONE_TIME_PAYLOAD,
                    headers=_auth_headers(user),
                )
                assert resp.status_code == 400
                assert "in progress" in resp.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_create_allowed_when_in_queue(self, db_session):
        user = _make_user(db_session)
        job = _make_job(db_session, user, status=JobStatus.IN_QUEUE)

        def override():
            try:
                yield db_session
            finally:
                pass

        app.dependency_overrides[get_db] = override
        try:
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/jobs/{job.id}/schedule",
                    json=ONE_TIME_PAYLOAD,
                    headers=_auth_headers(user),
                )
                assert resp.status_code == 201
        finally:
            app.dependency_overrides.clear()

    def test_update_blocked_when_in_progress(self, db_session):
        client, user, job = self._make_client(db_session)
        try:
            # Create schedule while job is DRAFT
            client.post(
                f"/api/jobs/{job.id}/schedule",
                json=ONE_TIME_PAYLOAD,
                headers=_auth_headers(user),
            )
            # Simulate job transitioning to IN_PROGRESS
            job.status = JobStatus.IN_PROGRESS
            db_session.commit()

            resp = client.put(
                f"/api/jobs/{job.id}/schedule",
                json={"timezone": "US/Eastern"},
                headers=_auth_headers(user),
            )
            assert resp.status_code == 400
            assert "in progress" in resp.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_update_allowed_when_in_queue(self, db_session):
        client, user, job = self._make_client(db_session)
        try:
            client.post(
                f"/api/jobs/{job.id}/schedule",
                json=ONE_TIME_PAYLOAD,
                headers=_auth_headers(user),
            )
            # Job is now IN_QUEUE (set by create_job_schedule)
            db_session.refresh(job)
            assert job.status == JobStatus.IN_QUEUE

            resp = client.put(
                f"/api/jobs/{job.id}/schedule",
                json={"timezone": "US/Eastern"},
                headers=_auth_headers(user),
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Job status transitions on schedule create/reschedule
# ---------------------------------------------------------------------------

class TestJobStatusTransitions:
    """Verify that creating/rescheduling a schedule transitions job to IN_QUEUE."""

    def test_create_schedule_sets_job_in_queue(self, schedule_client, db_session):
        client, user, job = schedule_client
        assert job.status == JobStatus.DRAFT

        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        db_session.refresh(job)
        assert job.status == JobStatus.IN_QUEUE

    def test_inline_schedule_sets_job_in_queue(self, schedule_client, db_session):
        client, user, _ = schedule_client
        resp = client.post(
            "/api/jobs",
            data={
                "title": "Inline Scheduled Job",
                "schedule_timezone": "UTC",
                "schedule_scheduled_at": FUTURE_DT,
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        job = db_session.query(Job).filter(Job.id == job_id).first()
        assert job.status == JobStatus.IN_QUEUE

    def test_reschedule_failed_job_sets_in_queue(self, schedule_client, db_session):
        client, user, job = schedule_client
        # Create schedule → job goes to IN_QUEUE
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        # Simulate job failure
        job.status = JobStatus.FAILED
        db_session.commit()

        # Reschedule with new time and reactivate
        new_dt = (datetime.utcnow() + timedelta(days=60)).isoformat()
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"scheduled_at": new_dt, "status": "active"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        db_session.refresh(job)
        assert job.status == JobStatus.IN_QUEUE

    def test_reschedule_cancelled_job_sets_in_queue(self, schedule_client, db_session):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedule",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        # Simulate job cancellation
        job.status = JobStatus.CANCELLED
        db_session.commit()

        new_dt = (datetime.utcnow() + timedelta(days=60)).isoformat()
        resp = client.put(
            f"/api/jobs/{job.id}/schedule",
            json={"scheduled_at": new_dt, "status": "active"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        db_session.refresh(job)
        assert job.status == JobStatus.IN_QUEUE


# ---------------------------------------------------------------------------
# Rerun for cancelled jobs
# ---------------------------------------------------------------------------

class TestRerunCancelledJob:
    def test_rerun_cancelled_job(self, schedule_client, db_session):
        client, user, job = schedule_client
        dev = _make_user(db_session, UserRole.DEVELOPER)
        agent = _make_agent(db_session, dev)
        _make_step(db_session, job, agent)
        job.status = JobStatus.CANCELLED
        db_session.commit()

        resp = client.post(
            f"/api/jobs/{job.id}/rerun",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "re-execution started" in data["message"].lower()
        assert data["job_id"] == job.id
        assert data["status"] == "in_progress"

    def test_rerun_completed_job_rejected(self, schedule_client, db_session):
        client, user, job = schedule_client
        job.status = JobStatus.COMPLETED
        db_session.commit()

        resp = client.post(
            f"/api/jobs/{job.id}/rerun",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 400

    def test_rerun_in_progress_job_rejected(self, schedule_client, db_session):
        client, user, job = schedule_client
        job.status = JobStatus.IN_PROGRESS
        db_session.commit()

        resp = client.post(
            f"/api/jobs/{job.id}/rerun",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 400
