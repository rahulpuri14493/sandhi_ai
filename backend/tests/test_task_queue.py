"""Unit tests for the Celery task queue integration and reliability features.

Tests cover:
- execute_platform_job properly delegates to the thread runner with reraise_exceptions=True
- ExecutePlatformJobTask.on_failure correctly marks jobs as FAILED in the DB when retries exhaust
"""

import pytest
from unittest.mock import patch, MagicMock

from services.task_queue import enqueue_execute_platform_job, QueueEnqueueError
from models.job import JobStatus


class TestCeleryTaskReliability:

    @patch("services.job_scheduler.run_job_in_thread")
    def test_execute_platform_job_passes_reraise_flag(self, mock_run_job):
        """
        Verify that the Celery task calls the core execution logic with reraise_exceptions=True.
        This is critical because it allows exceptions to bubble up to Celery,
        which triggers the autoretry_for and retry_backoff policies.
        """
        from services.task_queue import execute_platform_job

        # Call the underlying function directly (simulating a worker executing it)
        execute_platform_job.run(job_id=42, history_id=101, execution_token="token-abc")

        # Verify the flag is strictly set to True
        mock_run_job.assert_called_once_with(
            job_id=42,
            history_id=101,
            execution_token="token-abc",
            reraise_exceptions=True,
        )

    @patch("services.job_scheduler.run_job_in_thread")
    def test_execute_platform_job_exception_bubbles_up(self, mock_run_job):
        """
        Verify that if run_job_in_thread fails (when reraise is true), the exception
        actually bubbles out of the task so Celery can catch it and retry.
        """
        from services.task_queue import execute_platform_job

        mock_run_job.side_effect = ValueError("Simulated worker crash")

        with pytest.raises(ValueError, match="Simulated worker crash"):
            execute_platform_job(job_id=42)

    @patch("db.database.SessionLocal")
    def test_on_failure_hook_marks_job_failed(self, mock_session_local):
        """
        Verify that when Celery exhausts all retries, the custom Task class
        opens a DB session and safely transitions the job status to FAILED.
        """
        from services.task_queue import ExecutePlatformJobTask

        # 1. Setup mock database and job
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_job = MagicMock()
        mock_job.status = JobStatus.IN_PROGRESS

        # Mock the DB query chain: db.query().filter().first() -> mock_job
        mock_db.query.return_value.filter.return_value.first.return_value = mock_job

        # 2. Instantiate our custom Celery Task class
        task = ExecutePlatformJobTask()

        # 3. Manually trigger the on_failure hook (simulating max_retries exceeded)
        exception_instance = RuntimeError("API rate limit exceeded permanently")
        task.on_failure(
            exc=exception_instance,
            task_id="celery-task-uuid-123",
            args=[],
            kwargs={"job_id": 99},  # Simulating kwargs passed to the task
            einfo=None,
        )

        # 4. Verify the DB was updated correctly
        assert mock_job.status == JobStatus.FAILED
        assert "API rate limit exceeded permanently" in mock_job.failure_reason

        # Verify the transaction was committed and closed
        mock_db.commit.assert_called_once()
        mock_db.close.assert_called_once()

    @patch("db.database.SessionLocal")
    def test_on_failure_hook_handles_missing_job_gracefully(self, mock_session_local):
        """
        Verify the failure hook doesn't crash if the job was deleted from the DB mid-flight.
        """
        from services.task_queue import ExecutePlatformJobTask

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        # Mock the DB returning None (job not found)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        task = ExecutePlatformJobTask()

        # This should not raise any exceptions
        task.on_failure(
            exc=Exception("Crash"),
            task_id="celery-task-uuid-123",
            args=[99],  # Simulating job_id passed as an arg instead of kwarg
            kwargs={},
            einfo=None,
        )

        # No commit should happen if the job isn't found
        mock_db.commit.assert_not_called()
        mock_db.close.assert_called_once()

    @patch("db.database.SessionLocal")
    def test_on_failure_hook_updates_db_and_history(self, mock_session_local):
        """Verify the custom Task class marks both Job and History as FAILED."""
        from services.task_queue import ExecutePlatformJobTask

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        # Mock Job and History objects
        mock_job = MagicMock(status=JobStatus.IN_PROGRESS)
        mock_hist = MagicMock(status="started")

        # Setup the mock query to return job first, then history
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_job,
            mock_hist,
        ]

        task = ExecutePlatformJobTask()
        task.on_failure(
            exc=RuntimeError("Final exhaustion"),
            task_id="test-id",
            args=[],
            kwargs={"job_id": 99, "history_id": 101},
            einfo=None,
        )

        # Assertions
        assert mock_job.status == JobStatus.FAILED
        assert mock_hist.status == "failed"
        assert mock_hist.completed_at is not None
        mock_db.commit.assert_called_once()


class TestQueueAdmissionControl:

    @patch("services.task_queue.execute_platform_job.apply_async")
    @patch("services.task_queue.redis.Redis.from_url")
    def test_enqueue_rejects_when_overloaded(
        self, mock_redis_from_url, mock_apply_async
    ):
        """
        Verify that enqueue fails with a QueueEnqueueError when strict=True
        and the Redis queue depth exceeds the maximum threshold.
        """
        mock_client = MagicMock()
        mock_redis_from_url.return_value = mock_client

        # Mock Lua script returning -2 (Overloaded)
        mock_client.eval.return_value = -2

        with pytest.raises(QueueEnqueueError, match="is overloaded"):
            enqueue_execute_platform_job(
                job_id=42, strict=True, queue_name="interactive"
            )
        mock_apply_async.assert_not_called()

    @patch("services.task_queue.execute_platform_job.apply_async")
    @patch("services.task_queue.redis.Redis.from_url")
    def test_circuit_breaker_trips(self, mock_redis_from_url, mock_apply_async):
        """
        Verify that if the circuit breaker key in Redis exceeds the threshold,
        enqueue is rejected immediately.
        """
        mock_client = MagicMock()
        mock_redis_from_url.return_value = mock_client

        # Mock Lua script returning -1 (Circuit Breaker OPEN)
        mock_client.eval.return_value = -1

        with pytest.raises(QueueEnqueueError, match="Circuit breaker OPEN"):
            enqueue_execute_platform_job(
                job_id=42, strict=True, queue_name="interactive"
            )
        mock_apply_async.assert_not_called()

    @patch("services.task_queue.execute_platform_job.apply_async")
    @patch("services.task_queue.redis.Redis.from_url")
    def test_enqueue_succeeds_when_healthy(self, mock_redis_from_url, mock_apply_async):
        """
        Verify that a healthy queue depth and closed circuit breaker
        results in a successful Celery apply_async call.
        """
        mock_client = MagicMock()
        mock_redis_from_url.return_value = mock_client

        # Mock Lua script returning 1 (OK to proceed)
        mock_client.eval.return_value = 1

        result = enqueue_execute_platform_job(
            job_id=42, strict=True, queue_name="interactive"
        )

        assert result is True
        mock_apply_async.assert_called_once_with(
            kwargs={"job_id": 42, "history_id": None, "execution_token": None},
            queue="interactive",
        )
