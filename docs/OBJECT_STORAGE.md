# Object Storage (S3-Compatible)

Sandhi AI stores BRD/job documents in S3-compatible storage.

## Current default behavior

- Default backend storage is `s3`.
- Local Docker onboarding uses **MinIO** (S3-compatible) via `docker-compose.s3.yml`.
- Local filesystem storage is still supported for testing/basic setups by setting `OBJECT_STORAGE_BACKEND=local`.

---

## Recommended local setup (Docker + MinIO)

1. Configure root `.env`:

```env
OBJECT_STORAGE_BACKEND=s3
S3_ACCESS_KEY_ID=sandhi-access-key
S3_SECRET_ACCESS_KEY=sandhi-secret-key
S3_BUCKET=sandhi-brd-docs
```

`S3_ENDPOINT_URL` is optional for local MinIO overlay because `docker-compose.s3.yml`
already injects `http://minio:9000` for backend.

2. Start stack with MinIO overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.s3.yml up -d --build
```

3. Verify services:

```bash
docker compose ps
```

Expected storage services:

- `minio` (S3 API + console)
- `minio-init` (one-shot bucket creation)

MinIO endpoints:

- API: `http://localhost:9000`
- Console: `http://localhost:9001`

---

## Using external S3 in production

Use any S3-compatible endpoint (AWS S3, Ceph RGW, etc.) by setting:

| Variable | Required | Example |
|----------|----------|---------|
| `OBJECT_STORAGE_BACKEND` | Yes | `s3` |
| `S3_ENDPOINT_URL` | Yes | `https://rgw.example.com` |
| `S3_ACCESS_KEY_ID` | Yes | *(your key)* |
| `S3_SECRET_ACCESS_KEY` | Yes | *(your secret)* |
| `S3_BUCKET` | Yes | `sandhi-brd-docs` |
| `S3_REGION` | Optional | `us-east-1` |
| `S3_ADDRESSING_STYLE` | Recommended | `path` |
| `S3_AUTO_CREATE_BUCKET` | Optional | `true` |
| `JOB_UPLOAD_MAX_FILE_BYTES` | Optional | `104857600` (100MB) |

For external production S3, skip the overlay compose file and run only `docker-compose.yml`.

---

## Optional local filesystem mode

If you need to bypass S3 entirely:

```env
OBJECT_STORAGE_BACKEND=local
```

Then run:

```bash
docker compose up -d --build
```

---

## Health and failure behavior

- Backend checks storage connectivity and bucket access.
- `/health` includes storage status:

```json
{"status":"healthy","storage":{"ok":true,"detail":"bucket=sandhi-brd-docs reachable"}}
```

If storage config is missing or endpoint is unavailable, health can return `"degraded"` with details.

---

## Reliability hardening in storage layer

The S3 path includes protections for transient failures:

- boto3 retry settings (`S3_RETRY_MODE`, `S3_MAX_ATTEMPTS`)
- app-level exponential backoff + jitter retries for upload/download/head operations
- post-upload object visibility wait (`head_object`) before returning metadata

This reduces race conditions where uploads succeed but immediate reads fail.

---

## S3 tuning variables

| Env var | Default | Purpose |
|---------|---------|---------|
| `S3_SIGNATURE_VERSION` | `s3v4` | Signature algorithm |
| `S3_TCP_KEEPALIVE` | `true` | Better idle connection stability |
| `S3_MAX_POOL_CONNECTIONS` | `100` | Connection pool size |
| `S3_CONNECT_TIMEOUT_SECONDS` | `5` | Connect timeout |
| `S3_READ_TIMEOUT_SECONDS` | `60` | Read timeout |
| `S3_MAX_ATTEMPTS` | `5` | boto3 retry attempts |
| `S3_RETRY_MODE` | `standard` | boto3 retry mode |
| `S3_OPERATION_RETRY_ATTEMPTS` | `4` | App-level retry attempts |
| `S3_OPERATION_RETRY_BASE_DELAY_SECONDS` | `0.2` | Backoff base delay |
| `S3_OPERATION_RETRY_MAX_DELAY_SECONDS` | `2.0` | Backoff cap |
| `S3_OPERATION_RETRY_JITTER_SECONDS` | `0.1` | Jitter to avoid thundering herd |

See `infra/object-storage/.env.s3.example` for complete examples.
