# Codebase layout

Conventions used in this repository so backend and frontend stay easy to navigate and onboard.

## Backend (`backend/`)

| Area | Purpose |
|------|--------|
| `main.py` | FastAPI app entry; mounts routers. |
| `api/` | HTTP route modules (thin handlers → services). |
| `services/` | Business logic, integrations (A2A, MCP, jobs, payments). |
| `models/` | SQLAlchemy ORM models. |
| `schemas/` | Pydantic request/response and wire-format validation. |
| `core/` | Settings (`config.py`), shared security helpers. |
| `db/` | Database session and base metadata. |
| `middleware/` | ASGI/FastAPI middleware. |
| `alembic/` | Database migrations (preferred path for schema changes). |
| `migrations/` | Legacy or auxiliary SQL notes (see `migrations/README.md`). |
| `resources/` | **Packaged, read-only assets** shipped with the app (e.g. default JSON registries under `resources/config/`). Not runtime secrets. |
| `docs/` | Backend-focused technical notes (payloads, storage, workflows). |
| `tests/` | Pytest suite; mirror feature names in test module names. |
| `scripts/` | One-off maintenance or dev utilities. |

**Naming:** Python modules use `snake_case`. Settings and environment variables live in `core/config.py` (pydantic-settings), distinct from packaged files under `resources/`.

## Frontend (`frontend/`)

| Area | Purpose |
|------|--------|
| `src/main.tsx`, `src/App.tsx` | Vite + React bootstrap and root layout. |
| `src/pages/` | Route-level screens (React Router). |
| `src/components/` | Reusable UI (including `components/ui/` for primitives). |
| `src/lib/` | API client, types, stores, domain helpers. **All application TS/TSX for imports lives under `src/`.** |
| `src/assets/` | Static assets referenced from the app. |
| `tests/` | Vitest tests; import production code via `../src/...` or `@/` alias. |
| Config root | `vite.config.ts`, `vitest.config.ts`, `tsconfig.json`, `tailwind.config.js`, `postcss.config.js`, `index.html` |

**Path alias:** `@/*` → `src/*` (see `tsconfig.json` and `vite.config.ts`).

**Do not** add parallel `frontend/components` or `frontend/lib` trees at the repository root—those duplicate `src/` and drift from the build. The TypeScript project `include` is only `src`.

## Cross-cutting docs (`docs/`)

Repo-wide specifications (e.g. [A2A for developers](A2A_DEVELOPERS.md), [A2A task & assignment](A2A_TASK_AND_ASSIGNMENT.md), JSON Schemas under `docs/schemas/`) live at the repository root under `docs/`, separate from `backend/docs/` operational notes.
