"""Optional Redis read-through cache for planner artifact raw bytes."""

from services.planner_artifact_cache import get_cached_planner_raw, planner_raw_cache_key


def test_planner_raw_cache_key_stable():
    assert planner_raw_cache_key(12, 34) == "sandhi:planner_raw:v1:12:34"


def test_get_cached_planner_raw_returns_none_when_redis_url_empty():
    assert get_cached_planner_raw(1, 1) is None
