"""Unit tests for job schedule CRUD endpoints with structured date/time fields.

Covers:
- Happy path: create, list, get, update, delete (one-time + recurring)
- Structured field validation (missing required fields, invalid days/time/timezone)
- Authorization: wrong user cannot access another user's schedules
- 404 cases: job not found, schedule not found
- Status transitions: active → inactive clears next_run_time
- next_run_time is computed on create and recomputed on update
- Cascade: deleting a job removes its schedules
- Creating a job with inline schedule fields via POST /api/jobs
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from core.security import create_access_token, get_password_hash
from db.database import get_db
from main import app
from models.job import Job, JobSchedule, JobStatus, ScheduleStatus
from models.user import User, UserRole


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_job(db_session, user):
    job = Job(
        business_id=user.id,
        title="Test Job",
        description="desc",
        status=JobStatus.DRAFT,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _auth_headers(user):
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


RECURRING_PAYLOAD = {
    "is_one_time": False,
    "timezone": "UTC",
    "days_of_week": [1, 3, 5],
    "time": "09:00",
}

ONE_TIME_PAYLOAD = {
    "is_one_time": True,
    "timezone": "UTC",
    "scheduled_at": "2027-06-15T14:30:00",
}


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
    def test_recurring_schedule_valid(self):
        from schemas.job import JobScheduleCreate
        s = JobScheduleCreate(**RECURRING_PAYLOAD)
        assert s.is_one_time is False
        assert s.days_of_week == [1, 3, 5]
        assert s.time == "09:00"

    def test_one_time_schedule_valid(self):
        from schemas.job import JobScheduleCreate
        s = JobScheduleCreate(**ONE_TIME_PAYLOAD)
        assert s.is_one_time is True
        assert s.scheduled_at is not None

    def test_recurring_missing_days_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(is_one_time=False, time="09:00")

    def test_recurring_missing_time_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(is_one_time=False, days_of_week=[1, 3])

    def test_one_time_missing_scheduled_at_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(is_one_time=True)

    def test_invalid_day_of_week_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(is_one_time=False, days_of_week=[7], time="09:00")

    def test_invalid_time_format_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(is_one_time=False, days_of_week=[1], time="9am")

    def test_invalid_timezone_rejected(self):
        from pydantic import ValidationError
        from schemas.job import JobScheduleCreate
        with pytest.raises(ValidationError):
            JobScheduleCreate(is_one_time=False, days_of_week=[1], time="09:00", timezone="Fake/Zone")

    def test_update_all_none_allowed(self):
        from schemas.job import JobScheduleUpdate
        s = JobScheduleUpdate()
        assert s.is_one_time is None


# ---------------------------------------------------------------------------
# Cron builder helpers
# ---------------------------------------------------------------------------

class TestCronBuilders:
    def test_build_cron_from_schedule(self):
        from schemas.job import build_cron_from_schedule
        assert build_cron_from_schedule([1, 3, 5], "09:30") == "30 9 * * 1,3,5"

    def test_build_cron_from_schedule_all_days(self):
        from schemas.job import build_cron_from_schedule
        assert build_cron_from_schedule([], "14:00") == "0 14 * * *"

    def test_build_cron_from_datetime(self):
        from datetime import datetime
        from schemas.job import build_cron_from_datetime
        dt = datetime(2027, 3, 20, 14, 30)
        assert build_cron_from_datetime(dt) == "30 14 20 3 *"


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/schedules — create
# ---------------------------------------------------------------------------

class TestCreateSchedule:
    def test_create_recurring_schedule(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["job_id"] == job.id
        assert data["is_one_time"] is False
        assert data["days_of_week"] == [1, 3, 5]
        assert data["time"] == "09:00"
        assert data["timezone"] == "UTC"
        assert data["status"] == "active"
        assert data["next_run_time"] is not None

    def test_create_one_time_schedule(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_one_time"] is True
        assert data["scheduled_at"] is not None
        assert data["timezone"] == "UTC"

    def test_create_schedule_inactive_has_no_next_run(self, schedule_client):
        client, user, job = schedule_client
        payload = {**RECURRING_PAYLOAD, "status": "inactive"}
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=payload,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        assert resp.json()["next_run_time"] is None

    def test_create_schedule_missing_fields_rejected(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json={"is_one_time": False, "time": "09:00"},  # missing days_of_week
            headers=_auth_headers(user),
        )
        assert resp.status_code == 422

    def test_create_schedule_job_not_found(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.post(
            "/api/jobs/99999/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 404

    def test_create_schedule_wrong_user(self, schedule_client, db_session):
        client, _, job = schedule_client
        other_user = _make_user(db_session)
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(other_user),
        )
        assert resp.status_code == 404

    def test_create_schedule_unauthenticated(self, schedule_client):
        client, _, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/schedules — list
# ---------------------------------------------------------------------------

class TestListSchedules:
    def test_list_empty(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get(f"/api/jobs/{job.id}/schedules", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_multiple_schedules(self, schedule_client):
        client, user, job = schedule_client
        client.post(f"/api/jobs/{job.id}/schedules", json=RECURRING_PAYLOAD, headers=_auth_headers(user))
        client.post(f"/api/jobs/{job.id}/schedules", json=ONE_TIME_PAYLOAD, headers=_auth_headers(user))
        resp = client.get(f"/api/jobs/{job.id}/schedules", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_wrong_user_forbidden(self, schedule_client, db_session):
        client, _, job = schedule_client
        other_user = _make_user(db_session)
        resp = client.get(f"/api/jobs/{job.id}/schedules", headers=_auth_headers(other_user))
        assert resp.status_code == 403

    def test_list_job_not_found(self, schedule_client):
        client, user, _ = schedule_client
        resp = client.get("/api/jobs/99999/schedules", headers=_auth_headers(user))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}/schedules/{schedule_id} — single get
# ---------------------------------------------------------------------------

class TestGetSchedule:
    def test_get_existing_schedule(self, schedule_client):
        client, user, job = schedule_client
        create_resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        )
        sid = create_resp.json()["id"]
        resp = client.get(f"/api/jobs/{job.id}/schedules/{sid}", headers=_auth_headers(user))
        assert resp.status_code == 200
        assert resp.json()["id"] == sid
        assert resp.json()["days_of_week"] == [1, 3, 5]

    def test_get_schedule_not_found(self, schedule_client):
        client, user, job = schedule_client
        resp = client.get(f"/api/jobs/{job.id}/schedules/99999", headers=_auth_headers(user))
        assert resp.status_code == 404

    def test_get_schedule_wrong_user(self, schedule_client, db_session):
        client, user, job = schedule_client
        create_resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        )
        sid = create_resp.json()["id"]
        other_user = _make_user(db_session)
        resp = client.get(f"/api/jobs/{job.id}/schedules/{sid}", headers=_auth_headers(other_user))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/jobs/{job_id}/schedules/{schedule_id} — update
# ---------------------------------------------------------------------------

class TestUpdateSchedule:
    def test_update_days_and_time(self, schedule_client):
        client, user, job = schedule_client
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        ).json()["id"]

        resp = client.put(
            f"/api/jobs/{job.id}/schedules/{sid}",
            json={"days_of_week": [0, 6], "time": "18:00"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["days_of_week"] == [0, 6]
        assert data["time"] == "18:00"
        assert data["next_run_time"] is not None

    def test_disable_schedule_clears_next_run_time(self, schedule_client):
        client, user, job = schedule_client
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        ).json()["id"]

        resp = client.put(
            f"/api/jobs/{job.id}/schedules/{sid}",
            json={"status": "inactive"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "inactive"
        assert resp.json()["next_run_time"] is None

    def test_re_enable_schedule_sets_next_run_time(self, schedule_client):
        client, user, job = schedule_client
        payload = {**RECURRING_PAYLOAD, "status": "inactive"}
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=payload,
            headers=_auth_headers(user),
        ).json()["id"]

        resp = client.put(
            f"/api/jobs/{job.id}/schedules/{sid}",
            json={"status": "active"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        assert resp.json()["next_run_time"] is not None

    def test_update_timezone(self, schedule_client):
        client, user, job = schedule_client
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        ).json()["id"]

        resp = client.put(
            f"/api/jobs/{job.id}/schedules/{sid}",
            json={"timezone": "Asia/Kolkata"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["timezone"] == "Asia/Kolkata"

    def test_update_schedule_not_found(self, schedule_client):
        client, user, job = schedule_client
        resp = client.put(
            f"/api/jobs/{job.id}/schedules/99999",
            json={"status": "inactive"},
            headers=_auth_headers(user),
        )
        assert resp.status_code == 404

    def test_update_wrong_user_forbidden(self, schedule_client, db_session):
        client, user, job = schedule_client
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        ).json()["id"]

        other_user = _make_user(db_session)
        resp = client.put(
            f"/api/jobs/{job.id}/schedules/{sid}",
            json={"status": "inactive"},
            headers=_auth_headers(other_user),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/jobs/{job_id}/schedules/{schedule_id}
# ---------------------------------------------------------------------------

class TestDeleteSchedule:
    def test_delete_schedule_success(self, schedule_client):
        client, user, job = schedule_client
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        ).json()["id"]

        resp = client.delete(
            f"/api/jobs/{job.id}/schedules/{sid}",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 204

        get_resp = client.get(
            f"/api/jobs/{job.id}/schedules/{sid}",
            headers=_auth_headers(user),
        )
        assert get_resp.status_code == 404

    def test_delete_schedule_not_found(self, schedule_client):
        client, user, job = schedule_client
        resp = client.delete(
            f"/api/jobs/{job.id}/schedules/99999",
            headers=_auth_headers(user),
        )
        assert resp.status_code == 404

    def test_delete_wrong_user_forbidden(self, schedule_client, db_session):
        client, user, job = schedule_client
        sid = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        ).json()["id"]

        other_user = _make_user(db_session)
        resp = client.delete(
            f"/api/jobs/{job.id}/schedules/{sid}",
            headers=_auth_headers(other_user),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Cascade: deleting a job should remove its schedules
# ---------------------------------------------------------------------------

class TestCascadeDelete:
    def test_schedules_deleted_with_job(self, schedule_client, db_session):
        client, user, job = schedule_client
        client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
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
    def test_create_job_with_recurring_schedule(self, schedule_client, db_session):
        client, user, _ = schedule_client
        resp = client.post(
            "/api/jobs",
            data={
                "title": "Scheduled Job",
                "schedule_is_one_time": "false",
                "schedule_timezone": "UTC",
                "schedule_days_of_week": "[1,3,5]",
                "schedule_time": "09:00",
            },
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        job_id = resp.json()["id"]

        schedules = db_session.query(JobSchedule).filter(
            JobSchedule.job_id == job_id
        ).all()
        assert len(schedules) == 1
        assert schedules[0].is_one_time is False
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
# One-time and recurring response fields
# ---------------------------------------------------------------------------

class TestScheduleResponseFields:
    def test_recurring_response_has_structured_fields(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=RECURRING_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "days_of_week" in data
        assert "time" in data
        assert "timezone" in data
        assert "scheduled_at" in data
        # cron_expression should NOT be in response
        assert "cron_expression" not in data

    def test_one_time_response_has_structured_fields(self, schedule_client):
        client, user, job = schedule_client
        resp = client.post(
            f"/api/jobs/{job.id}/schedules",
            json=ONE_TIME_PAYLOAD,
            headers=_auth_headers(user),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_one_time"] is True
        assert data["scheduled_at"] is not None
        assert "cron_expression" not in data
