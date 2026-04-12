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

    def test_send_requires_recipients(self):
        out = execute_smtp(
            {"provider": "gmail", "username": "a@b.com", "password": "p"},
            {"action": "send", "subject": "S", "body": "B", "from_address": "a@b.com"},
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

    def test_list_channels_requires_team_id(self):
        out = execute_teams({"access_token": "tok"}, {"action": "list_channels"})
        assert "team_id" in out.lower()

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
        assert data.get("error") == "graph_api_error"

    def test_list_channel_messages_requires_ids(self):
        out = execute_teams({"access_token": "tok"}, {"action": "list_channel_messages"})
        assert "team_id" in out.lower()

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
