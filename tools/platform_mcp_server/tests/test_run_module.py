"""Tests for run.py entry (uvicorn wiring)."""
from unittest.mock import MagicMock


def test_env_bool_defaults():
    import run as run_mod

    assert run_mod._env_bool("RUN_TEST_UNSET_XYZ", False) is False
    assert run_mod._env_bool("RUN_TEST_UNSET_XYZ", True) is True


def test_env_bool_truthy(monkeypatch):
    import run as run_mod

    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("RUN_BOOL_T", v)
        assert run_mod._env_bool("RUN_BOOL_T") is True
        monkeypatch.delenv("RUN_BOOL_T", raising=False)


def test_env_bool_falsey(monkeypatch):
    import run as run_mod

    monkeypatch.setenv("RUN_BOOL_F", "0")
    assert run_mod._env_bool("RUN_BOOL_F") is False


def test_main_invokes_uvicorn(monkeypatch):
    import run as run_mod

    mock_run = MagicMock()
    monkeypatch.setattr(run_mod.uvicorn, "run", mock_run)
    monkeypatch.setenv("PORT", "9099")
    monkeypatch.setenv("UVICORN_RELOAD", "true")
    run_mod.main()
    mock_run.assert_called_once()
    args, kw = mock_run.call_args
    assert args[0] == "app:app"
    assert kw["host"] == "0.0.0.0"
    assert kw["port"] == 9099
    assert kw["reload"] is True
    assert kw["log_level"] == "info"
    assert kw["access_log"] is True
