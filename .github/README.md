# GitHub Actions workflows

This folder contains CI/CD workflows for the Sandhi AI platform.

## Workflows

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| **PR Tests** | `workflows/pr-tests.yml` | Every pull request (all branches) | Run backend + frontend unit and integration tests; smoke-test Docker Compose stack. |
| **Docker Image CI** | `workflows/docker-image.yml` | Push/PR to `main` | Build Docker Compose images and bring up the stack to verify it starts. |
| **Azure Web App** | `workflows/azure-container-webapp.yml` | Push to `main` or manual | Build backend image and deploy to Azure App Service. |

## PR Tests (pr-tests.yml)

- **docker-compose-stack:** Builds and starts the full stack (backend, frontend, DB, etc.), waits for backend and frontend to be ready, then tears down. Ensures the stack builds and runs.
- **backend-tests:** Runs in `backend/` with Python 3.11. Uses in-memory SQLite (see `backend/tests/conftest.py`). No `A2A_ADAPTER_URL` or real DB required.
  - `pytest -v` — unit tests
  - `pytest tests/integration/ -v` — integration tests (job flows, BRD, workflow, etc.)
- **frontend-tests:** Runs in `frontend/` with Node 20.
  - `npm run test -- --run` — unit tests
  - `npm run test -- --run tests/integration` — integration tests (marketplace, job flow, dashboard, etc.)

Backend and frontend jobs run in parallel; they do not depend on the Docker stack. The Docker job runs in parallel as well and only validates that the stack comes up.

## Docker Image CI (docker-image.yml)

Builds images with `docker compose build`, starts the stack with `docker compose up -d`, and waits for backend and frontend. Used to validate the Compose setup on `main` (and PRs to `main`). Does not run pytest or npm test; for full tests use PR Tests.

## Azure deployment (azure-container-webapp.yml)

Builds the **backend** image (from `backend/Dockerfile`), pushes to GitHub Container Registry, and deploys to the configured Azure Web App. Configure via repo secrets and Azure app settings (see comments in the file).

## Environment and configuration

- **PR Tests / Docker CI:** `.env` is created in the job with `POSTGRES_*`, `SECRET_KEY`, and `POSTGRES_DB` so Compose and backend can start. Backend pytest uses in-memory SQLite and does not need PostgreSQL or `A2A_ADAPTER_URL`.
- **A2A and scale:** The platform runs without a message bus or separate registry; A2A is protocol + optional adapter. See [docs/A2A_DEVELOPERS.md](../docs/A2A_DEVELOPERS.md) for architecture and scale (e.g. 200+ agents with optional list pagination).

## Recent changes reflected here

- **Integration tests:** Backend `tests/integration/` and frontend `tests/integration/` are run in PR Tests.
- **New features covered by tests:** Job flows (create, analyze documents, workflow, execute), BRD workflow, workflow clarification (generate-workflow-questions), agent list with pagination (`limit`/`offset`, `X-Total-Count`), A2A and sequential workflows.
