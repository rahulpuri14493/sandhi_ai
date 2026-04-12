"""Extended mocked coverage for execution_teams (Graph mail, channel APIs, error mapping)."""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

pytestmark = pytest.mark.unit

from execution_teams import execute_teams


def _mock_response(status: int, json_data=None, *, text: str | None = None, reason: str = "OK"):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.reason_phrase = reason
    if json_data is not None:
        r.json.return_value = json_data
    else:
        def _raise():
            raise json.JSONDecodeError("x", "doc", 0)

        r.json.side_effect = _raise
    r.text = text or ""
    return r


CFG = {"access_token": "tok-extended-tests"}


class TestTeamsChannelAndMail:
    def test_list_channel_messages_ok_with_next_link(self):
        mock_resp = _mock_response(
            200,
            {
                "value": [
                    {
                        "id": "m1",
                        "createdDateTime": "2025-01-01T00:00:00Z",
                        "from": {"user": {"id": "u1"}},
                        "body": {"content": "hello world " * 2000},
                        "replyToId": None,
                    },
                    "skip-non-dict",
                ],
                "@odata.nextLink": "https://graph.microsoft.com/next",
            },
        )
        mock_http = MagicMock()
        mock_http.request.return_value = mock_resp
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                CFG,
                {"action": "list_channel_messages", "team_id": "t1", "channel_id": "c1", "top": "not-int"},
            )
        data = json.loads(out)
        assert len(data["messages"]) == 1
        assert "…" in data["messages"][0]["body_preview"]
        assert data.get("next_link")

    def test_list_channel_messages_graph_error(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            404,
            {"error": {"code": "NotFound", "message": "no"}},
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(CFG, {"action": "list_channel_messages", "team_id": "t", "channel_id": "c"})
        d = json.loads(out)
        assert d.get("error") == "upstream_error"
        assert d.get("status") == 404

    def test_get_channel_message_ok_truncates_body(self):
        long_body = "x" * 50_000
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            200,
            {
                "id": "mid",
                "body": {"contentType": "text", "content": long_body},
                "hasAttachments": False,
            },
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                CFG,
                {"action": "get_channel_message", "team_id": "t", "channel_id": "c", "message_id": "mid"},
            )
        data = json.loads(out)["message"]
        assert len(data["body"]["content"]) <= 32001
        assert "…" in data["body"]["content"]

    def test_get_channel_message_non_dict_json(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(200, ["not", "dict"])
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                CFG,
                {"action": "get_channel_message", "team_id": "t", "channel_id": "c", "message_id": "x"},
            )
        assert "message" in json.loads(out)

    def test_list_mail_messages_ok(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            200,
            {
                "value": [
                    {
                        "id": "mail1",
                        "subject": "S",
                        "bodyPreview": "p",
                        "isRead": True,
                    }
                ],
                "@odata.nextLink": "https://graph.microsoft.com/mailnext",
            },
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(CFG, {"action": "list_mail_messages", "folder": "inbox", "top": 5})
        d = json.loads(out)
        assert d["messages"][0]["id"] == "mail1"
        assert "next_link" in d

    def test_get_mail_message_preview_vs_full(self):
        mock_http = MagicMock()

        def _req(*_a, **_k):
            return _mock_response(
                200,
                {
                    "id": "m1",
                    "subject": "Sub",
                    "bodyPreview": "short preview",
                    "body": {"contentType": "text", "content": "full body hidden"},
                    "from": None,
                },
            )

        mock_http.request.side_effect = _req
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            prev = json.loads(
                execute_teams(CFG, {"action": "get_mail_message", "message_id": "m1"}),
            )["message"]
            assert "preview" in (prev["body"]["content"] or "").lower() or prev["body"]["content"] == "short preview"

        mock_http.request.side_effect = _req
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            full = json.loads(
                execute_teams(
                    CFG,
                    {"action": "get_mail_message", "message_id": "m1", "include_full_body": True},
                ),
            )["message"]
            assert "full body" in full["body"]["content"]

    def test_get_mail_attachment_metadata_only(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            200,
            {"@odata.type": "#file", "name": "f.bin", "contentType": "application/octet-stream"},
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                CFG,
                {"action": "get_mail_attachment", "message_id": "m1", "attachment_id": "a1"},
            )
        d = json.loads(out)
        assert d.get("error") == "attachment_not_downloadable"

    def test_get_mail_attachment_invalid_base64(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            200,
            {
                "@odata.type": "fileAttachment",
                "name": "x",
                "contentType": "text/plain",
                "contentBytes": "@@@not-valid-b64@@@",
            },
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                CFG,
                {"action": "get_mail_attachment", "message_id": "m1", "attachment_id": "a1"},
            )
        assert json.loads(out).get("error") == "invalid_attachment_base64"

    def test_get_mail_attachment_ok_and_truncates(self):
        raw = b"x" * (5 * 1024 * 1024)
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            200,
            {
                "@odata.type": "fileAttachment",
                "name": "big.bin",
                "contentType": "application/octet-stream",
                "contentBytes": base64.b64encode(raw).decode("ascii"),
            },
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams(
                CFG,
                {"action": "get_mail_attachment", "message_id": "m1", "attachment_id": "a1"},
            )
        d = json.loads(out)
        assert d.get("truncated") is True
        assert "content_base64" in d

    def test_send_and_reply_success(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(201, {"id": "newmsg"})
        args_base = {
            "team_id": "t1",
            "channel_id": "c1",
            "body": "hi",
            "idempotency_key": "idem-send-ext-1",
        }
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = json.loads(execute_teams(CFG, {"action": "send_message", **args_base}))
            assert out.get("status") == "ok"
        mock_http.request.return_value = _mock_response(201, {"id": "reply"})
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out2 = json.loads(
                execute_teams(
                    CFG,
                    {
                        "action": "reply_message",
                        "team_id": "t1",
                        "channel_id": "c1",
                        "message_id": "parent",
                        "body": "reply",
                        "idempotency_key": "idem-reply-ext-1",
                    },
                ),
            )
            assert out2.get("status") == "ok"


class TestGraphErrorMapping:
    def test_graph_502_upstream_unavailable(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(502, {"error": {"code": "x", "message": "gw"}})
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({**CFG, "access_token": "tok-502"}, {"action": "list_joined_teams"})
        d = json.loads(out)
        assert d.get("error") == "upstream_unavailable"

    def test_graph_429_upstream_unavailable(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(429, {"error": {"code": "throttled", "message": "slow"}})
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({**CFG, "access_token": "tok-429"}, {"action": "list_joined_teams"})
        assert json.loads(out).get("error") == "upstream_unavailable"

    def test_graph_error_non_json_body(self):
        mock_http = MagicMock()
        r = MagicMock()
        r.status_code = 500
        r.reason_phrase = "Server Error"
        r.json.side_effect = ValueError("not json")
        r.text = "plain error"
        mock_http.request.return_value = r
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({**CFG, "access_token": "tok-500-plain"}, {"action": "list_joined_teams"})
        d = json.loads(out)
        assert d.get("error") == "upstream_unavailable"
        assert d.get("status") == 500

    def test_graph_403_mail_hint(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            403,
            {"error": {"code": "ErrorAccessDenied", "message": "mailbox not available"}},
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({**CFG, "access_token": "tok-403-mail"}, {"action": "list_joined_teams"})
        d = json.loads(out)
        assert d.get("error") == "permission_denied"
        assert "Mail.Read" in (d.get("hint") or "")

    def test_graph_403_channel_message_hint(self):
        mock_http = MagicMock()
        mock_http.request.return_value = _mock_response(
            403,
            {"error": {"code": "x", "message": "cannot read channel messages"}},
        )
        with patch("execution_teams.get_sync_http_client", return_value=mock_http):
            out = execute_teams({**CFG, "access_token": "tok-403-ch"}, {"action": "list_joined_teams"})
        d = json.loads(out)
        assert "ChannelMessage" in (d.get("hint") or "") or "channel" in (d.get("hint") or "").lower()
