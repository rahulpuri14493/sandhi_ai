# Slack, Microsoft Teams, and SMTP — platform MCP runbook

This runbook covers Sandhi’s **platform MCP** integrations for **Slack**, **Microsoft Teams (Graph)**, and **SMTP** (including Gmail OAuth read paths). It aligns with [Issue #71](https://github.com/rahulpuri14493/sandhi_ai/issues/71) (read/write contract, validation, idempotency).

## Unified tool errors (JSON)

Successful reads/writes return JSON (e.g. `{"channels": [...]}`, `{"status": "ok", ...}`). Failures use a stable top-level `error` string where possible:

| `error` | Typical cause |
|--------|----------------|
| `validation_failed` | Missing/invalid arguments before calling the provider |
| `idempotency_required` | Write attempted without `idempotency_key` (see below) |
| `auth_failed` | Invalid or rejected token (Graph 401, Slack `not_authed`, etc.) |
| `permission_denied` | Graph 403, wrong account type, or missing delegated scopes |
| `upstream_error` | Provider 4xx (except auth/permission), Slack API errors |
| `upstream_unavailable` | 5xx, 429, or connectivity issues |
| `configuration_error` | Missing SDK or misconfiguration |
| `output_validation_failed` | Optional shape check failed (see `PLATFORM_MCP_VALIDATE_TOOL_OUTPUT`) |

Graph errors include `provider: "graph"`, HTTP `status`, and `upstream_code` (Graph’s `error.code`). Slack errors include `provider: "slack"` and `upstream_code` when applicable.

## Idempotency on writes (default: strict)

These actions **require** a non-empty `idempotency_key` unless you opt out:

- Slack: `action: send`
- Teams: `send_message`, `reply_message`
- SMTP: `action: send`

**Opt-out (dev/legacy only):** set `PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY=true`.

**Behavior:** With a key, successful responses are cached for `PLATFORM_MCP_IDEMPOTENCY_TTL_SECONDS` (default 3600s), using **Redis** when `PLATFORM_MCP_IDEMPOTENCY_REDIS_URL` / `REDIS_URL` / `MCP_GUARDRAILS_REDIS_URL` is set; otherwise an in-process cache (single worker only).

## Optional output validation

Set `PLATFORM_MCP_VALIDATE_TOOL_OUTPUT=true` to assert basic JSON shapes for a few read paths (e.g. `list_channels` must return a `channels` array). Intended for debugging or contract hardening; adds latency only on those paths.

## Example payloads

### Slack — list channels (read)

```json
{ "action": "list_channels" }
```

### Slack — list messages (read)

```json
{ "action": "list_messages", "channel": "C01234567", "limit": 20 }
```

### Slack — send (write)

```json
{
  "action": "send",
  "channel": "C01234567",
  "message": "Hello from Sandhi",
  "idempotency_key": "job-42-step-7-slack-1"
}
```

### Teams — list joined teams (read)

```json
{ "action": "list_joined_teams" }
```

Requires Graph **access token** with appropriate delegated scopes (work/school account for most Teams APIs; personal Microsoft accounts are often blocked for `me/joinedTeams`).

### Teams — Graph base URL

Set **Graph base URL** to `https://graph.microsoft.com/v1.0` (default). Do **not** paste Graph Explorer page URLs.

### Teams — send channel message (write)

```json
{
  "action": "send_message",
  "team_id": "<team-id>",
  "channel_id": "<channel-id>",
  "body": "Hello",
  "idempotency_key": "job-42-step-7-teams-send-1"
}
```

### SMTP — send (write)

```json
{
  "action": "send",
  "to": "user@example.com",
  "subject": "Test",
  "body": "Body",
  "from_address": "bot@example.com",
  "idempotency_key": "job-42-step-7-smtp-1"
}
```

## Troubleshooting

| Symptom | Checks |
|--------|--------|
| `idempotency_required` | Pass `idempotency_key` on every send/post, or set `PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY=true` for local dev only. |
| Graph `permission_denied` / 403 “No authorization information…” | Often **MSA vs work/school** for Teams roster APIs; verify **jwt.ms**: `aud` = Graph, `scp` includes `Team.ReadBasic.All` (and related) after Entra admin consent; **Re-authorize** Microsoft on the tool. |
| Graph `auth_failed` / 401 | Refresh token; use **Connect Microsoft** in MCP settings. |
| Slack `auth_failed` | Regenerate bot token; ensure `chat:write` / `channels:read` / `channels:history` as needed. |
| Duplicate messages on retry | Ensure the **same** `idempotency_key` is reused for retries of the same logical send; use a **new** key for a new message. |

## Live E2E (optional, not CI)

1. Create a Slack app with a bot token that can list channels.
2. Export `SLACK_LIVE_BOT_TOKEN=xoxb-...`
3. Run:

```bash
cd tools/platform_mcp_server
pytest -m messaging_live tests/test_live_messaging_e2e.py -v
```

Tests skip automatically if the token is unset. Extend the same pattern for Graph with a dedicated token env if you add live Teams tests later.

## Test coverage (target ≥ 80% on messaging modules)

With the full `tools/platform_mcp_server` pytest suite, local runs show **≥ 80%** line coverage on:

- `execution_contract.py` (unified errors / idempotency / optional output validation)
- `execution_teams.py` (Microsoft Graph Teams + mail paths, mocked HTTP)
- `execution_integrations.py` (Slack, mocked)
- `execution_smtp.py` (SMTP validate/send + Gmail REST, mocked)

Backend helpers:

- `services/mcp_config_merge.py`, `services/job_completion_notify_email.py` — covered to **100%** via `tests/test_mcp_config_merge.py` and `tests/test_job_completion_notify_email.py`.

`app.py` is not measured as a whole (large FastAPI surface); `_tool_result_is_error` is covered in `tests/test_execution_helpers_unit.py::TestToolResultIsError`.

## Related environment variables

See `.env.example` for:

- `PLATFORM_MCP_IDEMPOTENCY_*`, `PLATFORM_MCP_ALLOW_WRITES_WITHOUT_IDEMPOTENCY_KEY`
- `PLATFORM_MCP_VALIDATE_TOOL_OUTPUT`
- `MCP_OAUTH_*` for Microsoft / Google connect flows
