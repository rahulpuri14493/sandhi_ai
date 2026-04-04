"""Corner cases for planner_artifact_cache (mock Redis client)."""

from unittest.mock import MagicMock

import pytest

import services.planner_artifact_cache as pac


@pytest.fixture(autouse=True)
def reset_planner_cache_client(monkeypatch):
    """Avoid leaking mock client between tests."""
    pac._client = None
    yield
    pac._client = None


def test_get_cached_planner_raw_returns_bytes_when_redis_hits(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.get.return_value = b'{"hit":true}'

    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)

    from services.planner_artifact_cache import get_cached_planner_raw

    assert get_cached_planner_raw(10, 20) == b'{"hit":true}'
    mock_redis.get.assert_called_once_with("sandhi:planner_raw:v1:10:20")


def test_get_cached_planner_raw_returns_none_for_unexpected_type(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.get.return_value = "not-bytes"

    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)

    from services.planner_artifact_cache import get_cached_planner_raw

    assert get_cached_planner_raw(1, 1) is None


def test_get_cached_planner_raw_memoryview_converted(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.get.return_value = memoryview(b"abc")

    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)

    from services.planner_artifact_cache import get_cached_planner_raw

    assert get_cached_planner_raw(1, 1) == b"abc"


def test_get_cached_planner_raw_returns_none_on_redis_error(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.get.side_effect = ConnectionError("refused")

    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)

    from services.planner_artifact_cache import get_cached_planner_raw

    assert get_cached_planner_raw(1, 1) is None


def test_set_cached_planner_raw_skips_empty_data(monkeypatch):
    mock_redis = MagicMock()
    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)

    from services.planner_artifact_cache import set_cached_planner_raw

    set_cached_planner_raw(1, 1, b"")
    mock_redis.setex.assert_not_called()


def test_set_cached_planner_raw_skips_when_no_client(monkeypatch):
    monkeypatch.setattr(pac, "_get_client", lambda: None)

    from services.planner_artifact_cache import set_cached_planner_raw

    # Should not raise
    set_cached_planner_raw(1, 1, b"x")


def test_set_cached_planner_raw_skips_when_ttl_non_positive(monkeypatch):
    mock_redis = MagicMock()
    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)
    from core.config import settings

    monkeypatch.setattr(settings, "PLANNER_ARTIFACT_CACHE_TTL_SECONDS", 0)

    from services.planner_artifact_cache import set_cached_planner_raw

    set_cached_planner_raw(1, 1, b"data")
    mock_redis.setex.assert_not_called()


def test_set_cached_planner_raw_calls_setex(monkeypatch):
    mock_redis = MagicMock()
    monkeypatch.setattr(pac, "_get_client", lambda: mock_redis)

    from core.config import settings

    monkeypatch.setattr(settings, "PLANNER_ARTIFACT_CACHE_TTL_SECONDS", 60)

    from services.planner_artifact_cache import set_cached_planner_raw

    set_cached_planner_raw(5, 7, b'{"z":1}')
    mock_redis.setex.assert_called_once()
    args, kwargs = mock_redis.setex.call_args
    assert args[0] == "sandhi:planner_raw:v1:5:7"
    assert args[1] == 60
    assert args[2] == b'{"z":1}'
