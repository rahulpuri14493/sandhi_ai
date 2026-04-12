"""Mock-heavy tests for execution_integrations and execution_object_storage."""
import builtins
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from execution_integrations import _github_host_is_api_github_com, execute_github, execute_notion, execute_rest_api, execute_slack
from execution_object_storage import (
    _normalize_s3_object_key,
    _s3_key_allowed_for_write,
    execute_s3_family,
)


def _ensure_fake_slack_sdk(monkeypatch):
    try:
        import slack_sdk  # noqa: F401

        return
    except ImportError:
        pass
    m = types.ModuleType("slack_sdk")
    err_mod = types.ModuleType("slack_sdk.errors")

    class SlackApiError(Exception):
        def __init__(self, message="", response=None):
            super().__init__(message)
            self.response = response or {}

    err_mod.SlackApiError = SlackApiError
    m.errors = err_mod
    m.WebClient = MagicMock
    monkeypatch.setitem(sys.modules, "slack_sdk", m)
    monkeypatch.setitem(sys.modules, "slack_sdk.errors", err_mod)


def _ensure_fake_github(monkeypatch):
    try:
        import github  # noqa: F401

        return
    except ImportError:
        pass
    m = types.ModuleType("github")
    m.Github = MagicMock
    monkeypatch.setitem(sys.modules, "github", m)


def _ensure_fake_notion(monkeypatch):
    try:
        import notion_client  # noqa: F401

        return
    except ImportError:
        pass
    m = types.ModuleType("notion_client")
    m.Client = MagicMock
    monkeypatch.setitem(sys.modules, "notion_client", m)


class TestGithubHost:
    def test_empty_is_api_github(self):
        assert _github_host_is_api_github_com("") is True

    def test_plain_host(self):
        assert _github_host_is_api_github_com("api.github.com") is True

    def test_with_scheme(self):
        assert _github_host_is_api_github_com("https://api.github.com") is True

    def test_enterprise_not_github_com(self):
        assert _github_host_is_api_github_com("https://git.example.com/api/v3") is False


class TestS3PrefixHelpers:
    def test_allowed_write_key(self, monkeypatch):
        monkeypatch.delenv("MCP_S3_WRITE_KEY_PREFIX", raising=False)
        assert _s3_key_allowed_for_write("a/b", "") is True
        assert _s3_key_allowed_for_write("pre/x", "pre") is True
        assert _s3_key_allowed_for_write("other/x", "pre") is False


class TestExecuteSlack:
    def test_missing_token(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        out = execute_slack({}, {"action": "send", "channel": "c", "message": "m"})
        data = json.loads(out)
        assert data.get("error") == "validation_failed"
        assert "bot_token" in (data.get("message") or "").lower()

    def test_send_missing_channel(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        out = execute_slack(
            {"bot_token": "mock-slack-bot-token-unit-test"},
            {"action": "send", "message": "hi", "idempotency_key": "unit-slack-missing-channel"},
        )
        data = json.loads(out)
        assert data.get("error") == "validation_failed"
        assert "channel" in (data.get("message") or "").lower()

    def test_list_channels(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        inst = MagicMock()
        inst.conversations_list.return_value = {
            "channels": [
                {"id": "C111", "name": "general", "is_private": False},
                {"id": "C222", "name": "random", "is_private": False},
            ]
        }

        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack({"bot_token": "mock-slack-bot-token-unit-test"}, {"action": "list_channels"})
        data = json.loads(out)
        names = [c.get("name") for c in data.get("channels") or []]
        assert "general" in names
        assert data["channels"][0].get("id") == "C111"
        inst.conversations_list.assert_called_once()
        call_kw = inst.conversations_list.call_args[1]
        assert "private_channel" in (call_kw.get("types") or "")

    def test_list_messages_requires_channel(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        out = execute_slack({"bot_token": "mock-slack-bot-token-unit-test"}, {"action": "list_messages"})
        assert "channel" in out.lower()

    def test_list_messages_ok(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        inst = MagicMock()
        inst.conversations_history.return_value = {
            "messages": [{"ts": "1", "user": "U1", "text": "hi"}],
            "response_metadata": {},
        }
        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack(
            {"bot_token": "mock-slack-bot-token-unit-test"},
            {"action": "list_messages", "channel": "C123"},
        )
        data = json.loads(out)
        assert data["messages"][0]["text"] == "hi"
        inst.conversations_history.assert_called_once()

    def test_send_idempotency_single_post(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        inst = MagicMock()
        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        args = {
            "action": "send",
            "channel": "#x",
            "message": "m",
            "idempotency_key": "idem-slack-unit-1",
        }
        cfg = {"bot_token": "mock-slack-bot-token-unit-test"}
        out1 = execute_slack(cfg, args)
        out2 = execute_slack(cfg, args)
        assert out1 == out2
        assert inst.chat_postMessage.call_count == 1

    def test_slack_api_error(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        from slack_sdk.errors import SlackApiError

        inst = MagicMock()
        inst.chat_postMessage.side_effect = SlackApiError("x", response={"error": "channel_not_found"})
        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack(
            {"bot_token": "mock-slack-bot-token-unit-test"},
            {
                "action": "send",
                "channel": "#x",
                "message": "m",
                "idempotency_key": "unit-slack-api-err-1",
            },
        )
        data = json.loads(out)
        assert data.get("error") == "upstream_error"
        assert data.get("upstream_code") == "channel_not_found"

    def test_slack_import_error_surfaces_not_installed(self, monkeypatch):
        real = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "slack_sdk" or name.startswith("slack_sdk."):
                raise ImportError("simulated missing slack_sdk")
            return real(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _guard)
        out = execute_slack({}, {})
        data = json.loads(out)
        assert data.get("error") == "configuration_error"

    def test_slack_generic_exception_maps_safe_error(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        inst = MagicMock()
        inst.chat_postMessage.side_effect = RuntimeError("boom")
        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack(
            {"bot_token": "mock-slack-bot-token-unit-test"},
            {
                "action": "send",
                "channel": "#x",
                "message": "m",
                "idempotency_key": "unit-slack-runtime-err",
            },
        )
        assert "Error" in out

    def test_send_requires_idempotency_key_by_default(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        monkeypatch.delenv("PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY", raising=False)
        inst = MagicMock()
        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack(
            {"bot_token": "mock-slack-bot-token-unit-test"},
            {"action": "send", "channel": "#x", "message": "m"},
        )
        data = json.loads(out)
        assert data.get("error") == "idempotency_required"
        inst.chat_postMessage.assert_not_called()


class TestExecuteGithub:
    def test_missing_token(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        assert "token" in execute_github({}, {"repo": "o/r"}).lower()

    def test_missing_repo(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        assert "repo" in execute_github({"api_key": "mock-github-api-key-unit-test"}, {}).lower()

    def test_get_file_mock(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        import base64

        content_obj = MagicMock()
        content_obj.content = base64.b64encode(b"hello").decode("ascii")
        repo = MagicMock()
        repo.get_contents.return_value = content_obj
        gh = MagicMock()
        gh.get_repo.return_value = repo
        import github as gh_mod

        monkeypatch.setattr(gh_mod, "Github", lambda *a, **k: gh)
        out = execute_github(
            {"api_key": "tok"},
            {"action": "get_file", "repo": "o/r", "path": "README.md"},
        )
        assert out == "hello"

    def test_get_file_listing(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        c1 = MagicMock(path="a", type="file")
        repo = MagicMock()
        repo.get_contents.return_value = [c1]
        gh = MagicMock()
        gh.get_repo.return_value = repo
        import github as gh_mod

        monkeypatch.setattr(gh_mod, "Github", lambda *a, **k: gh)
        out = execute_github(
            {"api_key": "tok"},
            {"action": "get_file", "repo": "o/r", "path": "dir"},
        )
        assert "a" in out

    def test_unknown_action(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        repo = MagicMock()
        gh = MagicMock()
        gh.get_repo.return_value = repo
        import github as gh_mod

        monkeypatch.setattr(gh_mod, "Github", lambda *a, **k: gh)
        out = execute_github(
            {"api_key": "tok"},
            {"action": "nope", "repo": "o/r"},
        )
        assert "unknown action" in out.lower()

    def test_pygithub_import_error(self, monkeypatch):
        real = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "github":
                raise ImportError("simulated missing PyGithub")
            return real(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _guard)
        out = execute_github({}, {"repo": "o/r"})
        assert "not installed" in out.lower()

    def test_get_file_requires_path(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        repo = MagicMock()
        gh = MagicMock()
        gh.get_repo.return_value = repo
        import github as gh_mod

        monkeypatch.setattr(gh_mod, "Github", lambda *a, **k: gh)
        out = execute_github(
            {"api_key": "tok"},
            {"action": "get_file", "repo": "o/r", "path": ""},
        )
        assert "path" in out.lower()

    def test_list_issues_action(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        issue = MagicMock()
        issue.number = 1
        issue.title = "t"
        repo = MagicMock()
        repo.get_issues.return_value = [issue]
        gh = MagicMock()
        gh.get_repo.return_value = repo
        import github as gh_mod

        monkeypatch.setattr(gh_mod, "Github", lambda *a, **k: gh)
        out = execute_github(
            {"api_key": "tok"},
            {"action": "list_issues", "repo": "o/r"},
        )
        data = json.loads(out)
        assert data[0]["number"] == 1

    def test_search_repositories_action(self, monkeypatch):
        _ensure_fake_github(monkeypatch)
        r = MagicMock()
        r.full_name = "a/b"
        gh = MagicMock()
        gh.search_repositories.return_value = [r]
        gh.get_repo.return_value = MagicMock()
        import github as gh_mod

        monkeypatch.setattr(gh_mod, "Github", lambda *a, **k: gh)
        out = execute_github(
            {"api_key": "tok"},
            {"action": "search", "repo": "o/r", "query": "q"},
        )
        data = json.loads(out)
        assert data[0]["full_name"] == "a/b"

    def test_github_enterprise_legacy_login_when_auth_subimport_fails(self, monkeypatch):
        """Non-github.com host + ``from github import Auth`` fails → login_or_token path (lines 75–80)."""
        import base64

        saved = sys.modules.pop("github", None)
        try:
            gh_mod = types.ModuleType("github")

            class FakeGithub:
                def __init__(self, *a, **kw):
                    self.init_kw = kw

                def get_repo(self, _name):
                    r = MagicMock()
                    c = MagicMock()
                    c.content = base64.b64encode(b"z").decode("ascii")
                    r.get_contents.return_value = c
                    return r

            gh_mod.Github = FakeGithub
            sys.modules["github"] = gh_mod

            real_import = builtins.__import__

            def guarded(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "github" and fromlist and "Auth" in fromlist:
                    raise ImportError("no Auth")
                return real_import(name, globals, locals, fromlist, level)

            monkeypatch.setattr(builtins, "__import__", guarded)
            out = execute_github(
                {"api_key": "tok", "base_url": "https://git.example.com/api/v3"},
                {"action": "get_file", "repo": "o/r", "path": "README"},
            )
            assert out == "z"
        finally:
            if saved is not None:
                sys.modules["github"] = saved
            elif "github" in sys.modules:
                del sys.modules["github"]


class TestExecuteNotion:
    def test_missing_key(self, monkeypatch):
        _ensure_fake_notion(monkeypatch)
        assert "api_key" in execute_notion({}, {}).lower()

    def test_search_mock(self, monkeypatch):
        _ensure_fake_notion(monkeypatch)
        client = MagicMock()
        client.search.return_value = {"results": [{"id": "p1"}]}
        import notion_client

        monkeypatch.setattr(notion_client, "Client", lambda auth: client)
        out = execute_notion({"api_key": "mock-notion-api-key-unit-test"}, {"action": "search", "query": "x"})
        assert "p1" in out

    def test_get_page_missing_id(self, monkeypatch):
        _ensure_fake_notion(monkeypatch)
        import notion_client

        monkeypatch.setattr(notion_client, "Client", lambda auth: MagicMock())
        out = execute_notion({"api_key": "mock-notion-api-key-unit-test"}, {"action": "get_page", "query": ""})
        assert "page id" in out.lower()

    def test_notion_import_error(self, monkeypatch):
        real = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "notion_client":
                raise ImportError("simulated missing notion_client")
            return real(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", _guard)
        assert "not installed" in execute_notion({}, {}).lower()

    def test_get_page_retrieve(self, monkeypatch):
        _ensure_fake_notion(monkeypatch)
        client = MagicMock()
        client.pages.retrieve.return_value = {"id": "page-1"}
        import notion_client

        monkeypatch.setattr(notion_client, "Client", lambda auth: client)
        out = execute_notion({"api_key": "k"}, {"action": "get_page", "query": "abc"})
        assert "page-1" in out

    def test_get_database_retrieve(self, monkeypatch):
        _ensure_fake_notion(monkeypatch)
        client = MagicMock()
        client.databases.retrieve.return_value = {"id": "db-1"}
        import notion_client

        monkeypatch.setattr(notion_client, "Client", lambda auth: client)
        out = execute_notion({"api_key": "k"}, {"action": "get_database", "query": "did"})
        assert "db-1" in out


class TestExecuteRestApi:
    def test_blocks_private_base_url(self, monkeypatch):
        monkeypatch.delenv("MCP_HTTP_ALLOW_PRIVATE_URLS", raising=False)
        out = execute_rest_api({"base_url": "http://127.0.0.1:8080"}, {"path": "v1", "method": "GET"})
        data = json.loads(out)
        assert data.get("error") == "base_url_blocked"

    def test_follow_redirects_false_by_default(self, monkeypatch):
        monkeypatch.delenv("MCP_REST_API_FOLLOW_REDIRECTS", raising=False)
        req_kw: dict = {}

        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = "{}"

            def json(self):
                return {}

        class Http:
            def request(self, method, url, json=None, headers=None, **kwargs):
                req_kw.update(kwargs)
                return Resp()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Http()):
                execute_rest_api({"base_url": "https://h.com"}, {"path": "x", "method": "GET"})
        assert req_kw.get("follow_redirects") is False

    def test_follow_redirects_true_when_env_set(self, monkeypatch):
        monkeypatch.setenv("MCP_REST_API_FOLLOW_REDIRECTS", "true")
        req_kw: dict = {}

        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = "{}"

            def json(self):
                return {}

        class Http:
            def request(self, method, url, json=None, headers=None, **kwargs):
                req_kw.update(kwargs)
                return Resp()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Http()):
                execute_rest_api({"base_url": "https://h.com"}, {"path": "x", "method": "GET"})
        assert req_kw.get("follow_redirects") is False

    def test_same_host_redirect_chain_when_env_set(self, monkeypatch):
        monkeypatch.setenv("MCP_REST_API_FOLLOW_REDIRECTS", "true")
        calls: list = []

        class Resp302:
            status_code = 302
            headers = {"location": "/final"}
            text = ""

        class Resp200:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = "{}"

            def json(self):
                return {"ok": True}

        class Http:
            def request(self, method, url, json=None, headers=None, **kwargs):
                calls.append(kwargs.get("follow_redirects"))
                if len(calls) == 1:
                    return Resp302()
                return Resp200()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Http()):
                out = execute_rest_api({"base_url": "https://h.com"}, {"path": "start", "method": "GET"})
        assert calls == [False, False]
        body = json.loads(out)
        assert body.get("status") == 200

    def test_cross_host_redirect_returns_error(self, monkeypatch):
        monkeypatch.setenv("MCP_REST_API_FOLLOW_REDIRECTS", "true")

        class Resp302:
            status_code = 302
            headers = {"location": "https://evil.example/path"}
            text = ""

        class Http:
            def request(self, method, url, json=None, headers=None, **kwargs):
                return Resp302()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Http()):
                out = execute_rest_api({"base_url": "https://h.com"}, {"path": "x", "method": "GET"})
        data = json.loads(out)
        assert data.get("error") == "redirect_host_mismatch"

    def test_subdomain_redirect_succeeds_same_registrable_domain(self, monkeypatch):
        monkeypatch.setenv("MCP_REST_API_FOLLOW_REDIRECTS", "true")
        calls: list = []

        class Resp302:
            status_code = 302
            headers = {"location": "https://cdn.h.com/final"}
            text = ""

        class Resp200:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = "{}"

            def json(self):
                return {"ok": True}

        class Http:
            def request(self, method, url, json=None, headers=None, **kwargs):
                calls.append(url)
                if len(calls) == 1:
                    return Resp302()
                return Resp200()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Http()):
                out = execute_rest_api({"base_url": "https://api.h.com"}, {"path": "start", "method": "GET"})
        body = json.loads(out)
        assert body.get("status") == 200
        assert "cdn.h.com" in calls[1]

    def test_missing_path(self):
        assert "path" in execute_rest_api({"base_url": "https://api.example.com"}, {}).lower()

    def test_invalid_path_full_url(self):
        out = execute_rest_api({"base_url": "https://x.com"}, {"path": "http://evil.com/x"})
        assert "relative" in out.lower()

    def test_missing_base(self):
        out = execute_rest_api({}, {"path": "v1/x"})
        assert "base_url" in out.lower()

    def test_get_json(self):
        class Resp:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = "{}"

            def json(self):
                return {"ok": True}

        class Ctx:
            def request(self, method, url, json=None, headers=None, **kwargs):
                return Resp()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Ctx()):
                out = execute_rest_api({"base_url": "https://h.com"}, {"path": "api", "method": "get"})
        body = json.loads(out)
        assert body["status"] == 200
        assert body["body"]["ok"] is True

    def test_sends_bearer_when_api_key_configured(self):
        captured: dict = {}

        class Resp:
            status_code = 204
            headers = {"content-type": "text/plain"}
            text = "ok"

        class Ctx:
            def request(self, method, url, json=None, headers=None, **kwargs):
                captured["headers"] = dict(headers or {})
                return Resp()

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Ctx()):
                execute_rest_api({"base_url": "https://h.com", "api_key": "tok"}, {"path": "v1/x"})
        assert "Bearer" in captured.get("headers", {}).get("Authorization", "")

    def test_request_exception_maps_safe_error(self):
        class Ctx:
            def request(self, *a, **k):
                raise RuntimeError("network down")

        with patch("execution_integrations.check_url_safe_for_server_fetch", return_value=(True, "")):
            with patch("execution_integrations.get_sync_http_client", return_value=Ctx()):
                out = execute_rest_api({"base_url": "https://h.com"}, {"path": "v1/x"})
        assert "Error" in out


def test_normalize_s3_object_key():
    assert _normalize_s3_object_key("", "buck") == ""
    assert _normalize_s3_object_key("reports/a", "buck") == "reports/a"
    assert _normalize_s3_object_key("/reports/a", "buck") == "reports/a"
    assert _normalize_s3_object_key("buck/reports/a", "buck") == "reports/a"
    assert _normalize_s3_object_key("s3://buck/reports/a", "buck") == "reports/a"
    assert _normalize_s3_object_key("buck", "buck") == ""
    assert _normalize_s3_object_key("s3://other/x", "buck") == "s3://other/x"


class TestS3FamilyMocked:
    def test_list_objects(self, monkeypatch):
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {"Contents": [{"Key": "a/1"}, {"Key": "a/2"}]}
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"action": "list", "key": "a"},
        )
        keys = json.loads(out)["keys"]
        assert "a/1" in keys

    def test_list_bucket_root_empty_key_uses_empty_prefix(self, monkeypatch):
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {"Contents": [{"Key": "root.txt"}]}
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        execute_s3_family(
            "minio",
            {"bucket": "sandhi-brd-docs", "access_key": "a", "secret_key": "s"},
            {"action": "list", "key": ""},
        )
        s3.list_objects_v2.assert_called_once()
        call_kw = s3.list_objects_v2.call_args.kwargs
        assert call_kw["Prefix"] == ""

    def test_list_slash_only_key_uses_empty_prefix(self, monkeypatch):
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {"Contents": []}
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"action": "list", "key": "/"},
        )
        assert s3.list_objects_v2.call_args.kwargs["Prefix"] == ""

    def test_get_object_text(self, monkeypatch):
        body = MagicMock()
        body.read.return_value = b"payload"
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 7}
        s3.get_object.return_value = {"Body": body}
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        out = execute_s3_family(
            "s3",
            {"bucket": "b", "access_key": "a", "secret_key": "s"},
            {"action": "get", "key": "f.txt"},
        )
        assert out == "payload"

    def test_list_includes_truncation_and_continuation(self, monkeypatch):
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {
            "Contents": [{"Key": "a"}],
            "IsTruncated": True,
            "NextContinuationToken": "tok123",
        }
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        out = json.loads(
            execute_s3_family(
                "s3",
                {"bucket": "b", "access_key": "a", "secret_key": "s"},
                {"action": "list", "key": ""},
            )
        )
        assert out["keys"] == ["a"]
        assert out["is_truncated"] is True
        assert out["next_continuation_token"] == "tok123"

    def test_list_passes_continuation_token(self, monkeypatch):
        s3 = MagicMock()
        s3.list_objects_v2.return_value = {"Contents": []}
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        execute_s3_family(
            "ceph",
            {"bucket": "buck", "access_key": "a", "secret_key": "s"},
            {"action": "list", "key": "p", "continuation_token": "abc"},
        )
        assert s3.list_objects_v2.call_args.kwargs["ContinuationToken"] == "abc"

    def test_get_strips_bucket_prefix_and_leading_slash(self, monkeypatch):
        body = MagicMock()
        body.read.return_value = b"payload"
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 7}
        s3.get_object.return_value = {"Body": body}
        monkeypatch.setattr("boto3.client", lambda name, **kw: s3)
        execute_s3_family(
            "s3",
            {"bucket": "myb", "access_key": "a", "secret_key": "s"},
            {"action": "get", "key": "/myb/path/file.txt"},
        )
        assert s3.get_object.call_args.kwargs["Key"] == "path/file.txt"
