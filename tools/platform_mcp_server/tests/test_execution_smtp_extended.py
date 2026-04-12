"""Extended mocked coverage for execution_smtp (Gmail API + auth branches)."""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from execution_smtp import config_timeout, execute_smtp


def _resp(status: int, json_data=None, text: str = ""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if json_data is not None:
        r.json.return_value = json_data
    else:
        r.json.side_effect = ValueError("no json")
    return r


GMAIL_CFG = {"provider": "gmail", "username": "u@gmail.com", "access_token": "ya29.gmail-test"}


class TestGmailApiPaths:
    def test_list_mail_invalid_max_results_uses_default(self):
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(200, {"messages": []})
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            execute_smtp(
                GMAIL_CFG,
                {"action": "list_mail_messages", "max_results": "nope"},
            )
        assert mock_http.get.called

    def test_list_mail_messages_ok(self):
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(
            200,
            {"messages": [{"id": "m1", "threadId": "t1"}], "resultSizeEstimate": 1},
        )
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(
                GMAIL_CFG,
                {"action": "list_mail_messages", "query": "is:unread", "max_results": 5},
            )
        d = json.loads(out)
        assert d["messages"][0]["id"] == "m1"
        mock_http.get.assert_called_once()
        assert mock_http.get.call_args[1]["params"]["q"] == "is:unread"

    def test_list_mail_messages_api_error(self):
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(403, {"error": "denied"})
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(GMAIL_CFG, {"action": "list_mail_messages"})
        d = json.loads(out)
        assert d.get("error") == "gmail_api_error"

    def test_gmail_api_error_non_json_body(self):
        mock_http = MagicMock()
        r = MagicMock()
        r.status_code = 500
        r.json.side_effect = ValueError("x")
        r.text = "plain"
        mock_http.get.return_value = r
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(GMAIL_CFG, {"action": "list_mail_messages"})
        d = json.loads(out)
        assert d.get("error") == "gmail_api_error"
        assert "plain" in str(d.get("body"))

    def test_list_mail_requires_token(self):
        out = execute_smtp({"provider": "gmail"}, {"action": "list_mail_messages"})
        assert "access_token" in out.lower()

    def test_get_mail_message_walks_payload(self):
        b64 = base64.urlsafe_b64encode(b"hello gmail body").decode("ascii").rstrip("=")
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64},
                },
                {
                    "filename": "a.txt",
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "att1", "size": 3},
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": "!!!badbase64!!!"},
                },
            ],
        }
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(
            200,
            {
                "id": "mid",
                "threadId": "tid",
                "snippet": "sn",
                "labelIds": ["INBOX"],
                "payload": payload,
            },
        )
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(
                GMAIL_CFG,
                {"action": "get_mail_message", "message_id": "mid"},
            )
        d = json.loads(out)["message"]
        assert "hello gmail body" in d["body_text"]
        assert d["headers"] is not None
        assert len(d.get("attachments") or []) >= 1

    def test_get_mail_non_dict_message(self):
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(200, ["x"])
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(
                GMAIL_CFG,
                {"action": "get_mail_message", "message_id": "m"},
            )
        assert "message" in json.loads(out)

    def test_get_mail_message_requires_id(self):
        out = execute_smtp(GMAIL_CFG, {"action": "get_mail_message"})
        assert "message_id" in out.lower()

    def test_get_attachment_requires_ids_and_token(self):
        out = execute_smtp({"provider": "gmail"}, {"action": "get_mail_attachment", "message_id": "m"})
        assert "access_token" in out.lower() or "attachment" in out.lower()

    def test_get_mail_attachment_graph_error(self):
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(404, {"error": "not found"})
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(
                GMAIL_CFG,
                {"action": "get_mail_attachment", "message_id": "m", "attachment_id": "a"},
            )
        assert json.loads(out).get("error") == "gmail_api_error"

    def test_get_mail_attachment_ok(self):
        raw_b64 = base64.urlsafe_b64encode(b"xyz").decode().rstrip("=")
        mock_http = MagicMock()
        mock_http.get.return_value = _resp(200, {"data": raw_b64, "size": 3})
        with patch("execution_smtp.get_sync_http_client", return_value=mock_http):
            out = execute_smtp(
                GMAIL_CFG,
                {"action": "get_mail_attachment", "message_id": "m1", "attachment_id": "a1"},
            )
        d = json.loads(out)
        assert "content_base64" in d

    def test_gmail_api_only_for_outlook_list(self):
        out = execute_smtp(
            {"provider": "outlook", "access_token": "x"},
            {"action": "list_mail_messages"},
        )
        assert json.loads(out).get("error") == "gmail_api_only"


class TestSmtpValidateAndConfig:
    def test_config_timeout_invalid_uses_default(self):
        assert config_timeout({"timeout_seconds": "bad"}) == 30.0
        assert config_timeout({"timeout_seconds": 200}) == 120.0

    def test_validate_oauth2_success(self):
        fake = MagicMock()
        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.docmd = MagicMock(return_value=(235, b"OK"))
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {
                        "provider": "outlook",
                        "auth_mode": "oauth2",
                        "username": "u@outlook.com",
                        "access_token": "tok",
                    },
                    {"action": "validate"},
                )
        assert json.loads(out).get("status") == "ok"

    def test_validate_password_login_success(self):
        fake = MagicMock()
        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.login = MagicMock()
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {
                        "provider": "gmail",
                        "username": "a@b.com",
                        "password": "pw",
                    },
                    {"action": "validate"},
                )
        assert json.loads(out).get("status") == "ok"

    def test_validate_oauth2_535_hint(self):
        fake = MagicMock()
        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.docmd = MagicMock(return_value=(535, b"auth failed"))
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {
                        "provider": "outlook",
                        "auth_mode": "oauth2",
                        "username": "u@outlook.com",
                        "access_token": "tok",
                    },
                    {"action": "validate"},
                )
        assert "535" in out
        assert "outlook.office.com" in out.lower() or "Hint" in out

    def test_validate_password_login_fails(self):
        import smtplib

        fake = MagicMock()
        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.login = MagicMock(side_effect=smtplib.SMTPException("bad"))
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {
                        "provider": "gmail",
                        "username": "a@b.com",
                        "password": "pw",
                    },
                    {"action": "validate"},
                )
        assert "SMTP login failed" in out or "failed" in out.lower()


class TestSmtpSendIdempotent:
    def test_send_success_with_idempotency(self):
        fake = MagicMock()
        fake.ehlo = MagicMock()
        fake.starttls = MagicMock()
        fake.login = MagicMock()
        fake.sendmail = MagicMock()
        fake.quit = MagicMock()
        with patch("execution_smtp.ssl.create_default_context", return_value=MagicMock()):
            with patch("execution_smtp.smtplib.SMTP", return_value=fake):
                out = execute_smtp(
                    {
                        "provider": "gmail",
                        "username": "a@b.com",
                        "password": "pw",
                    },
                    {
                        "action": "send",
                        "to": "dest@b.com",
                        "subject": "S",
                        "body": "B",
                        "from_address": "a@b.com",
                        "idempotency_key": "unit-smtp-send-ok-1",
                    },
                )
        d = json.loads(out)
        assert d.get("status") == "ok"
        fake.sendmail.assert_called_once()
