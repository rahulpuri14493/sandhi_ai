# Runbook: Queue Overload and Broker Degradation

## Trigger

- **API response:** System returns `HTTP 503 Service Unavailable` with a `Retry-After` header.
- **Logs:** `QueueEnqueueError` or `BACKPRESSURE TRIPPED` entries in backend logs.
- **Health check:** `/health` returns `ok: false` with details regarding broker unreachability.

---

## Diagnosis

### 1. Verify Redis key names (crucial)

Before diagnosing depth, verify which key holds the actual task list:

```bash
redis-cli keys "*interactive*"
```

- If you see `interactive`, use that for `llen`.
- If you see `_kombu.binding.interactive`, use the full prefixed name.

> **Warning:** Checking the wrong key will always report zero depth even while the system is overloaded.

### 2. Check queue stats and breaker state

Hit `/api/jobs/queue/stats`, or use the CLI to check whether the circuit breaker is open:

```bash
# Check if breaker count >= CELERY_CIRCUIT_BREACH_THRESHOLD (default: 10)
redis-cli get circuit_breaker:interactive
redis-cli get circuit_breaker:batch
```

If the value is at or above the threshold, enqueues are being rejected regardless of actual queue depth.



### 3. Identify poison pills

Search logs for:

```
permanently failed after retries exhausted
```

Note the `job_id` from the log line. These jobs are likely causing the repeated worker crashes or timeouts that tripped the circuit breaker.

---

## SLO Thresholds

| SLO | Threshold | Config key |
|---|---|---|
| Max queue age (oldest job waiting) | 30 minutes | `CELERY_SLO_MAX_QUEUE_AGE_SECONDS` |
| Enqueue-to-start p95 | 5 minutes | `CELERY_SLO_ENQUEUE_TO_START_P95_SECONDS` |

### Checking SLO breach status

GET /api/jobs/queue/stats returns `slo_age_breached: true` when the oldest 
job in a queue has been waiting longer than `CELERY_SLO_MAX_QUEUE_AGE_SECONDS`.
Set up an external monitor to alert when this field is true.

---

## Mitigation and Recovery

### 1. Reset circuit breakers

If the underlying issue (e.g. database slowness) is resolved but the API is still rejecting traffic, reset the breaker for a specific lane:

```bash
# Reset only the interactive lane
redis-cli del circuit_breaker:interactive

# Reset all lanes (emergency)
redis-cli keys "circuit_breaker:*" | xargs redis-cli del
```

### 2. Emergency queue purge

> **Warning:** This action is destructive and non-reversible. All pending scheduled jobs in the target queue will be permanently lost. Only use this if those jobs are known to be safe to discard or will be re-enqueued.

If the `batch` queue is starving resources:

```bash
celery -A services.task_queue purge -Q batch
```

### 3. Scale workers

If the queue backlog is legitimate (healthy jobs piling up due to under-provisioning), increase `CELERY_WORKER_AUTOSCALE_MAX` in your environment variables and redeploy worker pods.

---

## Broker Degradation (Redis Down)

### Behavior

| Mode | Behavior |
|---|---|
| `JOB_EXECUTION_STRICT_QUEUE=False` (default) | System fails open. API calls succeed, but jobs run in a local thread on the web server instead of a Celery worker. |
| `JOB_EXECUTION_STRICT_QUEUE=True` | All enqueues fail immediately with a `503`. |

### Verification

1. **Check `/health`** — will return `detail: "celery broker unreachable; local fallback enabled"` when in fallback mode.
2. **Inspect logs** — look for `"falling back to local thread"` to confirm jobs are still being processed locally.
3. **Recovery** — once Redis is restored, the system automatically resumes using Celery for all new enqueues. No manual intervention required.