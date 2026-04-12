"""Unit tests for SMTP and Microsoft Teams platform MCP executors."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from execution_smtp import execute_smtp
from execution_teams import execute_teams


class TestExecuteSmtp:
    def test_parse_attachments_only_message(self):
        from execution_smtp import _parse_attachments

        atts, err = _parse_attachments(
            {
                "attachments": [
                    {
                        "filename": "note.txt",
                        "content_base64": __import__("base64").b64encode(b"hello").decode("ascii"),
                    }
                ]
            }
        )
        assert err is None
        assert len(atts) == 1
        assert atts[0][0] == "note.txt"
        assert atts[0][1] == b"hello"

    def test_validate_custom_missing_host(self):
        out = execute_smtp({"provider": "custom"}, {"action": "validate"})
        assert "smtp_host" in out.lower()

    def test_validate_gmail_password_ok(self):
        fake = MagicMock()

        class _Ctx:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return fake

            def __exit__(self, *a):
                return False

        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.login = MagicMock()
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {"provider": "gmail", "username": "a@b.com", "password": "secret"},
                    {"action": "validate"},
                )
        data = json.loads(out)
        assert data["status"] == "ok"

    def test_validate_oauth2_requires_smtp_235(self):
        fake = MagicMock()
        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.docmd = MagicMock(return_value=(535, b"5.7.3 Authentication unsuccessful"))
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {
                        "provider": "outlook",
                        "auth_mode": "oauth2",
                        "username": "u@contoso.com",
                        "access_token": "fake.jwt.token",
                    },
                    {"action": "validate"},
                )
        assert "535" in out
        assert "Error" in out or "rejected" in out.lower()

    def test_smtp_send_requires_idempotency_key(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY", raising=False)
        out = execute_smtp(
            {"provider": "gmail", "username": "a@b.com", "password": "p"},
            {"action": "send", "to": "b@b.com", "subject": "S", "body": "B", "from_address": "a@b.com"},
        )
        data = json.loads(out)
        assert data.get("error") == "idempotency_required"

    def test_send_requires_recipients(self):
        out = execute_smtp(
            {"provider": "gmail", "username": "a@b.com", "password": "p"},
            {
                "action": "send",
                "subject": "S",
                "body": "B",
                "from_address": "a@b.com",
                "idempotency_key": "unit-smtp-send-no-recipients",
            },
        )
        assert "to" in out.lower()

    def test_unknown_action(self):
        out = execute_smtp({"provider": "gmail"}, {"action": "nope"})
        assert "unknown_action" in out

    def test_list_mail_non_gmail_returns_hint(self):
        out = execute_smtp(
            {"provider": "outlook", "access_token": "x"},
            {"action": "list_mail_messages"},
        )
        data = json.loads(out)
        assert data.get("error") == "gmail_api_only"

    def test_gmail_list_requires_token(self):
        out = execute_smtp({"provider": "gmail"}, {"action": "list_mail_messages"})
        assert "access_token" in out.lower()


class TestExecuteTeams:
    def test_send_message_requires_idempotency_key(self, monkeypatch):
        monkeypatch.delenv("PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY", raising=False)
        out = execute_teams(
            {"access_token": "tok"},
            {
                "action": "send_message",
                "team_id": "t1",
                "channel_id": "c1",
                "body": "hi",
            },
        )
        data = json.loads(out)
        assert data.get("error") == "idempotency_required"

    def test_missing_token_list(self):
        out = execute_teams({}, {"action": "list_joined_teams"})
        assert "access_token" in out.lower()

    def test_list_joined_teams_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": [{"id": "t1", "displayName": "Team A"}]}
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({"access_token": "tok"}, {"action": "list_joined_teams"})
        data = json.loads(out)
        assert any(t.get("displayName") == "Team A" for t in data.get("teams", []))

    def test_graph_explorer_base_url_normalized_to_v1(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": []}
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        bad_base = "https://developer.microsoft.com/en-us/graph/graph-explorer/me"
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            execute_teams(
                {"access_token": "tok-graph-explorer-url", "graph_base_url": bad_base},
                {"action": "list_joined_teams"},
            )
        url = mock_http.request.call_args[0][1]
        assert url == "https://graph.microsoft.com/v1.0/me/joinedTeams"

    def test_graph_base_trailing_me_stripped_for_graph_host(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": []}
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            execute_teams(
                {"access_token": "tok-trailing-me", "graph_base_url": "https://graph.microsoft.com/v1.0/me"},
                {"action": "list_joined_teams"},
            )
        url = mock_http.request.call_args[0][1]
        assert url == "https://graph.microsoft.com/v1.0/me/joinedTeams"

    def test_login_host_base_url_reset_to_v1(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"value": []}
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            execute_teams(
                {
                    "access_token": "tok-login-host",
                    "graph_base_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                },
                {"action": "list_joined_teams"},
            )
        url = mock_http.request.call_args[0][1]
        assert url == "https://graph.microsoft.com/v1.0/me/joinedTeams"

    def test_list_channels_requires_team_id(self):
        out = execute_teams({"access_token": "tok"}, {"action": "list_channels"})
        data = json.loads(out)
        assert data.get("error") == "validation_failed"
        assert "team_id" in (data.get("message") or "").lower()

    def test_graph_error_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.reason_phrase = "Unauthorized"
        mock_resp.json.return_value = {"error": {"code": "InvalidAuthenticationToken", "message": "bad"}}
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({"access_token": "bad"}, {"action": "list_joined_teams"})
        data = json.loads(out)
        assert data.get("error") == "auth_failed"

    def test_graph_error_403_no_authorization_info_hint(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.reason_phrase = "Forbidden"
        mock_resp.json.return_value = {
            "error": {
                "code": "Forbidden",
                "message": "No authorization information present on the request.",
            }
        }
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                {"access_token": "tok-403-noauth-msg"},
                {"action": "list_joined_teams"},
            )
        data = json.loads(out)
        assert data.get("error") == "permission_denied"
        assert data.get("status") == 403
        hint = (data.get("hint") or "").lower()
        assert "msa" in hint or "personal microsoft" in hint
        assert "jwt.ms" in hint

    def test_list_channel_messages_requires_ids(self):
        out = execute_teams({"access_token": "tok"}, {"action": "list_channel_messages"})
        data = json.loads(out)
        assert data.get("error") == "validation_failed"

    def test_list_channel_messages_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "value": [
                {
                    "id": "m1",
                    "createdDateTime": "2025-01-01T00:00:00Z",
                    "from": {"user": {"displayName": "A"}},
                    "body": {"content": "hello"},
                }
            ]
        }
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                {"access_token": "tok"},
                {"action": "list_channel_messages", "team_id": "t1", "channel_id": "c1"},
            )
        data = json.loads(out)
        assert data["messages"][0]["id"] == "m1"
