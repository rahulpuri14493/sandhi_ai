# Object Storage (S3-Compatible)

Sandhi AI supports S3-compatible object storage for BRD documents. For production and production-like local deployments, use **Ceph RGW** (or another S3-compatible enterprise endpoint) instead of local filesystem storage.

Local filesystem storage remains available only as a fallback for basic setups.

---

## Enabling S3 Storage (Recommended)

Set these environment variables on the backend (via `.env` or Compose overrides):

| Variable | Required | Example |
|----------|----------|---------|
| `OBJECT_STORAGE_BACKEND` | Yes | `s3` |
| `S3_ENDPOINT_URL` | Yes | `https://rgw.example.com` |
| `S3_ACCESS_KEY_ID` | Yes | *(your key)* |
| `S3_SECRET_ACCESS_KEY` | Yes | *(your secret)* |
| `S3_BUCKET` | Yes | `sandhi-brd-docs` |
| `S3_ADDRESSING_STYLE` | Recommended | `path` (required for most RGW setups) |
| `S3_AUTO_CREATE_BUCKET` | Optional | `true` — auto-creates the bucket on startup |
| `JOB_UPLOAD_MAX_FILE_BYTES` | Optional | Upload size guardrail (default 100 MB) |

Once set, the backend verifies S3 connectivity on startup. Misconfigured settings produce a clear log warning:

```
WARNING  S3 storage check FAILED: S3 authentication failed for bucket 'sandhi-brd-docs'. ...
```

The `/health` endpoint also reports storage status:

```json
{"status": "healthy", "storage": {"ok": true, "detail": "bucket=sandhi-brd-docs reachable"}}
```

A `"degraded"` status is returned when S3 is unreachable — useful for load-balancer health probes.

---

## Production Ceph RGW Deployment

### Option A — External Ceph Cluster (recommended for HA)

Provision Ceph RGW outside Docker and point the backend to it via the S3 environment variables above. No extra Compose file is needed.

### Option B — Single-Box Ceph Cluster via Docker Compose

For a production-grade single-box deployment (works on Windows, Linux, and macOS):

```bash
# 1. Set S3 credentials in .env
#    S3_ACCESS_KEY_ID=<your-key>
#    S3_SECRET_ACCESS_KEY=<your-secret>

# 2. Start the full stack with the Ceph overlay
docker compose -f docker-compose.yml -f docker-compose.ceph.yml up -d
```

The overlay starts a complete Ceph Reef cluster:

| Service | Role |
|---------|------|
| `ceph-mon` | Monitor — bootstraps the cluster, generates FSID and keyrings automatically |
| `ceph-mgr` | Manager — metrics and cluster orchestration |
| `ceph-osd` | Object Storage Daemon — directory-backed, works on all OSes |
| `ceph-rgw` | RADOS Gateway — S3-compatible API at `http://ceph-rgw:7480` |
| `ceph-init` | One-shot — sets pool sizes, applies RGW tuning, creates S3 user |

No manual `ceph.conf` editing or keying generation is required — the monitor bootstraps everything on first boot.

**Production hardening applied by the overlay:**

| Setting | Why |
|---------|-----|
| Bridge networking + dedicated subnet | Service-name DNS (`http://ceph-rgw:7480`) works out of the box |
| Per-service CPU / memory limits | Prevents runaway resource consumption |
| Health checks on MON and RGW | Compose-native readiness gates; services wait for dependencies |
| Single-OSD pool tuning | Replication set to 1 automatically by the init container |
| Idempotent S3 user creation | Safe to re-run `docker compose up` without errors |

> **Scaling up**: To move from single-box to multi-node, switch to Option A with a real Ceph cluster and point `S3_ENDPOINT_URL` at the external RGW. The backend code does not change.

---

## S3 Client Tuning

These optional variables control the backend's S3 connection pool and retry behaviour:

| Env var | Default | Purpose |
|---------|---------|---------|
| `S3_SIGNATURE_VERSION` | `s3v4` | Required by modern Ceph RGW |
| `S3_TCP_KEEPALIVE` | `true` | Prevents idle-connection resets through firewalls/LBs |
| `S3_MAX_POOL_CONNECTIONS` | `100` | Connection pool size for concurrent jobs |
| `S3_CONNECT_TIMEOUT_SECONDS` | `5` | Fast failure on unreachable endpoints |
| `S3_READ_TIMEOUT_SECONDS` | `60` | Allows large-object downloads |
| `S3_MAX_ATTEMPTS` | `5` | Retries with exponential back-off |
| `S3_RETRY_MODE` | `standard` | boto3 standard retry strategy |

See `infra/ceph/.env.ceph.example` for a complete env var template.
