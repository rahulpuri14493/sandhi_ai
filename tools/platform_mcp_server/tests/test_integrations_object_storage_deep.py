"""Mock-heavy tests for execution_integrations and execution_object_storage."""
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
        assert "bot_token" in out.lower()

    def test_send_missing_channel(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        out = execute_slack({"bot_token": "mock-slack-bot-token-unit-test"}, {"action": "send", "message": "hi"})
        assert "channel" in out.lower()

    def test_list_channels(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        inst = MagicMock()
        inst.conversations_list.return_value = {"channels": [{"name": "general"}, {"name": "random"}]}

        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack({"bot_token": "mock-slack-bot-token-unit-test"}, {"action": "list_channels"})
        data = json.loads(out)
        assert "general" in data["channels"]

    def test_slack_api_error(self, monkeypatch):
        _ensure_fake_slack_sdk(monkeypatch)
        from slack_sdk.errors import SlackApiError

        inst = MagicMock()
        inst.chat_postMessage.side_effect = SlackApiError("x", response={"error": "channel_not_found"})
        import slack_sdk

        monkeypatch.setattr(slack_sdk, "WebClient", lambda token: inst)
        out = execute_slack(
            {"bot_token": "mock-slack-bot-token-unit-test"},
            {"action": "send", "channel": "#x", "message": "m"},
        )
        assert "channel_not_found" in out or "Error" in out


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


class TestExecuteRestApi:
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
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def request(self, method, url, json=None, headers=None):
                return Resp()

        with patch("httpx.Client", return_value=Ctx()):
            out = execute_rest_api({"base_url": "https://h.com"}, {"path": "api", "method": "get"})
        body = json.loads(out)
        assert body["status"] == 200
        assert body["body"]["ok"] is True


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
