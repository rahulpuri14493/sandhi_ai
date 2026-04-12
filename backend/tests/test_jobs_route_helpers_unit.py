"""Unit tests for small helpers in api.routes.jobs (no HTTP)."""


def test_zip_extract_backoff_caps_at_max(monkeypatch):
    import api.routes.jobs as jm

    class _S:
        ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS = 1.0
        ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS = 2.0
        ZIP_EXTRACT_RETRY_JITTER_SECONDS = 0.0

    monkeypatch.setattr(jm, "settings", _S())
    monkeypatch.setattr(jm.random, "uniform", lambda _a, _b: 0.0)
    assert jm._zip_extract_backoff(0) == 1.0
    assert jm._zip_extract_backoff(10) == 2.0


def test_zip_extract_backoff_includes_jitter(monkeypatch):
    import api.routes.jobs as jm

    class _S:
        ZIP_EXTRACT_RETRY_BASE_DELAY_SECONDS = 0.1
        ZIP_EXTRACT_RETRY_MAX_DELAY_SECONDS = 10.0
        ZIP_EXTRACT_RETRY_JITTER_SECONDS = 0.2

    monkeypatch.setattr(jm, "settings", _S())
    monkeypatch.setattr(jm.random, "uniform", lambda a, b: (a + b) / 2)
    v = jm._zip_extract_backoff(0)
    assert 0.2 <= v <= 0.3
