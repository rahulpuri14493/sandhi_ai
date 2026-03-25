# Sandhi AI

**From intent to execution—orchestrate AI agents, track every step, own the outcome.**

Sandhi AI is an **AI agentic platform** that turns business goals into runnable multi-agent workflows. Define a job, assign one or more AI agents, and get full visibility into execution, cost, and results—no black boxes.

Think of it as an AI talent marketplace: you bring the work, agents bring the capabilities, and the platform handles orchestration, payments, and operational control.

---

## Why Sandhi AI

Most AI tools stop at generation. Sandhi AI is built for **execution at scale**.

- **Turn intent into workflows** — Structure business goals into clear steps and assign the right agent to each.
- **Route work intelligently** — Use the best agent for each task by capability, price, or availability instead of locking into a single model.
- **Full accountability** — See what ran, what it cost, and what each agent delivered.
- **Protocol-agnostic** — Native A2A and OpenAI-compatible endpoints run through one platform layer.

## What The Platform Does

- **Discover & compare** — Browse AI agents by capability and pricing.
- **Build workflows** — Auto-split work across agents or assign steps manually.
- **Execute with confidence** — Run jobs on a platform-managed A2A architecture with audit and retry.
- **Track everything** — Inter-agent communication, step status, earnings, and spend in one place.
- **Dual dashboards** — Business view for jobs and cost; developer view for agents and performance.

## Core Use Cases

- **Operations automation** — Break complex tasks into AI-driven steps and run them in sequence with full traceability.
- **Agent marketplace execution** — Pick the best agent per step by skill, price, or availability.
- **Multi-agent collaboration** — Coordinate A2A-enabled agents in a single job with handoffs and tool access.
- **MCP-backed work** — Let agents discover and use approved tools and data (PostgreSQL, vector DBs, files) through the platform.
- **Business oversight** — One place to review jobs, spend, and outputs with clear ownership and audit.

## Architecture At A Glance

Sandhi AI is built so the **platform owns orchestration** and **agents focus on execution**.

- **Backend**: FastAPI application that manages jobs, workflows, payments, MCP, and A2A execution.
- **Frontend**: React application built with Vite and React Router.
- **Database**: PostgreSQL for jobs, workflows, agents, payments, and audit data.
- **A2A support**: Agents can be native A2A or OpenAI-compatible. The platform runs an internal [A2A ↔ OpenAI adapter](tools/a2a_openai_adapter/README.md) so OpenAI-compatible endpoints are still called through the platform’s A2A flow.
- **Platform MCP Server**: A separate platform service that exposes tenant-safe enterprise tools such as PostgreSQL, Vector DB, and file system access.

For implementation details on A2A behavior, see [A2A for developers](docs/A2A_DEVELOPERS.md).

## Product Vision

Sandhi AI is the **AI agentic execution layer** for multi-agent work.

The goal: let any business define a goal, assemble the right agents and tools, and run that work with the same confidence, observability, and control they expect from enterprise SaaS.

We're building toward:

- **Clear orchestration** — Workflows that are easy to design, run, and debug.
- **Predictable economics** — Transparent costing and revenue sharing for agents and platform.
- **Strong tool governance** — MCP and tool access controlled per job and tenant.
- **Secure collaboration** — Multi-agent handoffs and peer calls without leaking credentials.
- **Production-ready deployment** — From local Docker to cloud (e.g. Azure) with one codebase.

## Real-World Business Impact

- **Operations automation** — Businesses that replace manual multi-step processes (procurement, invoice review, onboarding) with agent workflows report 60–90% cost reductions. Sandhi's job + workflow structure is exactly the right abstraction for this.

- **SMBs that can't afford AI teams** — 62% of companies want to partner rather than build. Sandhi can be the platform that lets a 10-person company access enterprise-grade AI agent capabilities without hiring ML engineers — just define the job and pick the agent.

- **AI developer monetization** — Developers currently have no clean way to monetize specialized agents. Sandhi's marketplace + revenue-sharing model mirrors what the App Store did for mobile developers — creating an entirely new economic tier for AI specialists.

- **Multi-agent research & analysis** — Complex knowledge work (market research, due diligence, competitive analysis) can be broken into parallel agent tasks. An orchestration layer with full audit trails makes this trustworthy enough for professional use.

- **India / Asia-Pacific enterprise market** — Asia-Pacific is the fastest-growing region in agentic AI. Over 80% of Indian enterprises are actively exploring agent deployment. Sandhi, built from India, is positioned to win this home market before Western platforms localize for it.

## Technology Stack

- **Backend**: FastAPI, Python
- **Frontend**: React, Vite, React Router
- **Database**: PostgreSQL
- **Authentication**: JWT
- **Deployment**: Docker and Docker Compose

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Node.js 18+ for local frontend development
- Python 3.11+ for local backend development

### Installation (Step-by-Step)

#### Step 1: Clone the repository

```bash
git clone <your-repo-url>
cd sandhi_ai
```

#### Step 2: Create `.env` from template

```bash
python scripts/setup_env.py
```

This creates `.env` from `.env.example` and generates `MCP_INTERNAL_SECRET`.

#### Step 3: Set required values in `.env`

Use the `.env` file at the **repository root** (same directory as `docker-compose.yml`).
Declare `OBJECT_STORAGE_BACKEND` in this file when choosing storage mode.

Required for all Docker setups:

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `SECRET_KEY`
- `MCP_INTERNAL_SECRET`

#### Step 4: Configure S3 storage in `.env` (default mode)

S3 is the default document storage mode. Set these in root `.env`:

```env
OBJECT_STORAGE_BACKEND=s3
S3_ACCESS_KEY_ID=sandhi-access-key
S3_SECRET_ACCESS_KEY=sandhi-secret-key
S3_BUCKET=sandhi-brd-docs
```

Notes:

- With MinIO overlay (`docker-compose.s3.yml`), backend endpoint defaults to `http://minio:9000`.
- For external S3-compatible providers (AWS S3, Ceph RGW, etc.), also set `S3_ENDPOINT_URL=<your-endpoint>`.

#### Step 5: Start services

**Recommended (S3 + MinIO local):**

```bash
docker compose -f docker-compose.yml -f docker-compose.s3.yml up -d --build
```

Use `docker-compose.s3.yml` for local S3/MinIO.

**Optional local filesystem mode (no S3):**

```env
OBJECT_STORAGE_BACKEND=local
```

Then start only core services:

```bash
docker compose up -d --build
```
#### Step 6: Verify services

```bash
docker compose ps
```

You should see `sandhi-backend`, `sandhi-db`, `a2a-openai-adapter`, and `platform-mcp-server`.
If you chose Path B, you should also see `minio`.

#### Step 7: Verify application is up

- Backend API docs: `http://localhost:8000/docs`
- Frontend: `http://localhost:3000`

If using MinIO:

- MinIO Console: `http://localhost:9001`
- Login with `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY`
- Confirm bucket `S3_BUCKET` exists (default: `sandhi-brd-docs`)

Database migrations are applied automatically on backend startup through Alembic. Existing and new databases are both handled without manual migration steps.

### Important Environment Notes

- Set `A2A_ADAPTER_URL=` in the backend environment if you want to bypass the internal adapter and call OpenAI-compatible endpoints directly.
- For MCP in production, set `MCP_ENCRYPTION_KEY` to a long random value so stored credentials are encrypted with a key that is independent from JWT signing.
- Keep `MCP_INTERNAL_SECRET` identical in the backend and platform MCP server so internal API calls remain protected.
- Job document storage supports S3-compatible backends (MinIO locally, external S3 providers in production). See [Object Storage](docs/OBJECT_STORAGE.md).
- Uploading new BRD documents to an existing job replaces older BRD files for that job.
- For Docker with MinIO S3, set `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` in `.env` and run `docker compose -f docker-compose.yml -f docker-compose.s3.yml up -d --build`.

## Local Development

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs at `http://localhost:3000`.

## Testing

- **Backend unit tests**: `cd backend && pytest`
- **Backend coverage gate**: `cd backend && pytest --cov=. --cov-report=term-missing --cov-fail-under=80`
- **Frontend tests**: `cd frontend && npm run test`

Every pull request runs backend tests, frontend tests, and a Docker Compose smoke test in GitHub Actions.

## API Documentation

Once the backend is running, open `http://localhost:8000/docs` for interactive API documentation.

## Project Structure

```text
.
├── backend/                       # FastAPI backend and migrations
├── frontend/                      # React application
├── tools/
│   ├── a2a_openai_adapter/        # Platform-managed A2A ↔ OpenAI adapter
│   └── platform_mcp_server/       # Internal platform MCP server
├── infra/
│   └── object-storage/            # S3 config templates + env examples
├── scripts/
│   └── setup_env.py               # First-time .env setup
├── docs/
│   ├── A2A_DEVELOPERS.md          # Developer-facing A2A guidance
│   └── OBJECT_STORAGE.md          # S3-compatible storage setup and tuning
├── docker-compose.yml             # Core platform services
├── docker-compose.s3.yml          # MinIO S3 overlay
└── README.md
```

## CI And Delivery

The `.github/workflows/` directory contains the CI/CD automation for the platform.

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| **PR Tests** | `workflows/pr-tests.yml` | Every pull request | Runs backend tests, frontend tests, and a Docker Compose smoke test. |
| **Docker Image CI** | `workflows/docker-image.yml` | Push/PR to `main` | Builds images and verifies the Compose stack starts successfully. |
| **Azure Web App** | `workflows/azure-container-webapp.yml` | Push to `main` or manual | Builds and deploys the backend container to Azure App Service. |

### PR Tests

- **docker-compose-stack**: Builds and starts the full stack, waits for backend and frontend readiness, then tears everything down.
- **backend-tests**: Runs unit and integration tests with Python 3.11 and in-memory SQLite.
- **frontend-tests**: Runs unit and integration tests with Node 20.

Backend, frontend, and Docker smoke checks run in parallel.

## Deployment Notes

### Docker Image CI

The Docker workflow builds the images with `docker compose build`, starts the stack with `docker compose up -d`, and confirms that the services come up cleanly.

### Azure Deployment

The Azure workflow builds the backend image, pushes it to GitHub Container Registry, and deploys it to the configured Azure Web App.

Required configuration:

| Location | Name | Required | Description |
|----------|------|----------|-------------|
| App settings | `DATABASE_URL` | Yes* | Full PostgreSQL connection string. |
| App settings | `SECRET_KEY` | Yes | JWT signing secret for production. |
| App settings | `WEBSITES_PORT` | Yes | Set to `8000` so Azure routes traffic correctly. |

*`DATABASE_URL` can also be supplied through Azure connection strings using the `DefaultConnection` name.*

If the app fails to connect to PostgreSQL, check the Web App logs, verify the database URL, and ensure Azure networking allows the container to reach the database.

---

## License

- **Code**: Business Source License 1.1. See [LICENSE](LICENSE).
- **Documentation**: MIT License. See [LICENSE-DOCS](LICENSE-DOCS).
