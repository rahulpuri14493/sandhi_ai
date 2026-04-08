"""Unit tests for the Celery task queue integration and reliability features.

Tests cover:
- execute_platform_job properly delegates to the thread runner with reraise_exceptions=True
- ExecutePlatformJobTask.on_failure correctly marks jobs as FAILED in the DB when retries exhaust
"""

import pytest
from unittest.mock import patch, MagicMock

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
        execute_platform_job(job_id=42, history_id=101, execution_token="token-abc")

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
            kwargs={"job_id": 99}, # Simulating kwargs passed to the task
            einfo=None
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
            args=[99], # Simulating job_id passed as an arg instead of kwarg
            kwargs={},
            einfo=None
        )
        
        # No commit should happen if the job isn't found
        mock_db.commit.assert_not_called()
        mock_db.close.assert_called_once()