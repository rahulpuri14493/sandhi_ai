# MCP Guardrails Runbook

This runbook covers production operations for MCP invocation guardrails under heavy multi-agent traffic.

## Scope

- Quota saturation (`mcp_quota_exceeded`)
- Circuit open storms (`mcp_circuit_open`)
- Redis degradation fallback (distributed -> process-local)
- Recommended dashboards and alert thresholds

## Key Signals

- Endpoint: `GET /metrics`
- Counter: `mcp_guardrail_events_total{event,code,operation_class,target_key}`
- Histogram: `mcp_guardrail_call_duration_seconds{operation_class,target_key,outcome}`
- Logs: `mcp_guardrail_event ...`

## Playbook: Quota Saturation

Symptoms:

- Rising `mcp_guardrail_events_total{event="rejected",code="mcp_quota_exceeded"}`
- Increased `mcp_guardrail_call_duration_seconds` p95/p99 with `outcome="guardrail_error"`

Actions:

1. Check target hotspots: top `target_key` with quota rejections.
2. Raise `MCP_TARGET_MAX_CONCURRENT_CALLS` for specific tools via `MCP_TOOL_POLICY_JSON`.
3. If tenant-wide congestion, increase tier concurrency via `MCP_TENANT_TIER_POLICY_JSON`.
4. Reduce retry amplification:
   - keep write-like retries low
   - enforce idempotency keys for write retries.
5. Verify worker capacity/CPU and upstream MCP throughput.

Alert suggestion:

- Warning: rejection ratio > 2% for 5m
- Critical: rejection ratio > 8% for 10m

## Playbook: Circuit Open Storm

Symptoms:

- Burst of `mcp_circuit_open`
- error ratio spikes for same `target_key`

Actions:

1. Validate upstream MCP health for affected target.
2. Inspect rolling-window breaker settings:
   - `MCP_CIRCUIT_BREAKER_WINDOW_SECONDS`
   - `MCP_CIRCUIT_BREAKER_MIN_SAMPLES`
   - `MCP_CIRCUIT_BREAKER_ERROR_RATE_THRESHOLD`
3. If false positives during brief spikes, increase `MIN_SAMPLES` or slightly raise `ERROR_RATE_THRESHOLD`.
4. If real upstream instability, keep breaker aggressive and scale/recover upstream first.

Alert suggestion:

- Warning: `mcp_circuit_open` > 10 events / 5m for same target
- Critical: `mcp_circuit_open` > 50 events / 5m across 3+ targets

## Playbook: Redis Degraded Fallback

Symptoms:

- Log line: `disabling distributed mode after redis error`
- Cross-worker admission consistency drops

Actions:

1. Restore Redis connectivity/latency.
2. Confirm `MCP_GUARDRAILS_DISTRIBUTED_ENABLED=true` and `MCP_GUARDRAILS_REDIS_URL` are valid.
3. Restart app workers after Redis recovery to re-enable distributed mode cleanly.
4. During fallback window, temporarily lower concurrency to reduce over-admission risk.

Alert suggestion:

- Warning on first fallback log event
- Critical if fallback persists > 10 minutes

## Dashboard Panels

- Guardrail rejects by code (stacked rate)
- Retry volume by operation class
- Success vs reject ratio by target key
- Latency p50/p95/p99 per target (`outcome="success"`)
- Circuit-open event rate per target
- Distributed-fallback incidents (log-derived)

## Baseline Thresholds (500 concurrency tenant profile)

- `mcp_quota_exceeded` < 3% sustained
- `mcp_circuit_open` near zero in healthy state
- success latency p95 under tool SLO (define per tool category)
- retry rate:
  - read-like < 15%
  - write-like < 5%

## Notes

- Use tenant tiers (`bronze/silver/gold`) for controlled capacity rollout.
- Per-tool policies should be the first tuning lever for persistent hotspots.
