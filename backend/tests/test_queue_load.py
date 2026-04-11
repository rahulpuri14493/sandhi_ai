import pytest
from unittest.mock import patch, MagicMock
from services.task_queue import enqueue_execute_platform_job, QueueEnqueueError

class TestPriorityIsolationUnderLoad:

    def _mock_lua_result(self, mock_client, result_code: int):
        """Helper: mock the Lua script evaluation result."""
        mock_client.eval.return_value = result_code

    @patch("services.task_queue.execute_platform_job.apply_async")
    @patch("services.task_queue.redis.Redis.from_url")
    def test_interactive_accepts_when_batch_is_full(self, mock_redis, mock_apply_async):
        """
        Batch queue is at max depth.
        Interactive queue is healthy.
        Interactive enqueue must succeed — batch pressure must not spill over.
        """
        mock_client = MagicMock()
        mock_redis.return_value = mock_client

        # Mock Lua script returning 1 (OK)
        self._mock_lua_result(mock_client, 1)

        result = enqueue_execute_platform_job(
            job_id=1,
            strict=True,
            queue_name="interactive",
        )
        assert result is True, "Interactive queue must accept jobs when healthy"


    @patch("services.task_queue.redis.Redis.from_url")
    def test_batch_rejects_when_overloaded(self, mock_redis):
        """
        Batch queue is at max depth.
        Enqueue to batch must be rejected with QueueEnqueueError.
        """
        mock_client = MagicMock()
        mock_redis.return_value = mock_client

        # Mock Lua script returning -2 (Overloaded)
        self._mock_lua_result(mock_client, -2)

        with pytest.raises(QueueEnqueueError, match="is overloaded"):
            enqueue_execute_platform_job(
                job_id=2,
                strict=True,
                queue_name="batch",
            )


    @patch("services.task_queue.execute_platform_job.apply_async")
    @patch("services.task_queue.redis.Redis.from_url")
    def test_interactive_unaffected_by_batch_circuit_breaker(self, mock_redis, mock_apply_async):
        """
        Batch circuit breaker is open.
        Interactive circuit breaker is closed.
        Interactive enqueue must succeed — breakers are per-queue and must not cross.
        """
        mock_client = MagicMock()
        mock_redis.return_value = mock_client

        # Interactive: healthy (Lua returns 1)
        self._mock_lua_result(mock_client, 1)

        result = enqueue_execute_platform_job(
            job_id=3,
            strict=True,
            queue_name="interactive",
        )
        assert result is True, "Interactive queue must be isolated from batch circuit breaker"

    @patch("services.task_queue.redis.Redis.from_url")
    def test_interactive_rejects_when_its_own_breaker_open(self, mock_redis):
        """
        Interactive circuit breaker is open (sustained overload on interactive).
        Must reject with QueueEnqueueError — no fallthrough.
        """
        mock_client = MagicMock()
        mock_redis.return_value = mock_client

        # Interactive: breaker open (Lua returns -1)
        self._mock_lua_result(mock_client, -1)

        with pytest.raises(QueueEnqueueError, match="Circuit breaker OPEN"):
            enqueue_execute_platform_job(
                job_id=4,
                strict=True,
                queue_name="interactive",
            )

    @patch("services.task_queue.execute_platform_job.apply_async")
    @patch("services.task_queue.redis.Redis.from_url")
    def test_mixed_load_interactive_always_wins(self, mock_redis, mock_apply_async):
        """
        Simulate 10 concurrent interactive submissions while batch is overloaded.
        All interactive submissions must succeed.
        """
        mock_client = MagicMock()
        mock_redis.return_value = mock_client

        # Healthy (Lua returns 1)
        self._mock_lua_result(mock_client, 1)

        results = []
        for job_id in range(10):
            result = enqueue_execute_platform_job(
                job_id=job_id,
                strict=True,
                queue_name="interactive",
            )
            results.append(result)

        assert all(results), "All 10 interactive jobs must succeed regardless of batch state"
        assert mock_apply_async.call_count == 10