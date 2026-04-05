# Sandhi AI — frontend

Vite + React + TypeScript SPA. API calls go through `/api` (proxied to the backend in dev; see `vite.config.ts`).

## Layout

All application code lives under **`src/`**:

| Path | Role |
|------|------|
| `src/pages/` | Route-level screens (React Router) |
| `src/components/` | Shared UI, including `components/ui/` primitives |
| `src/lib/` | API client (`api.ts`), types, Zustand store, helpers |
| `src/assets/` | Static assets |

Path alias **`@/*`** maps to **`src/*`** (`tsconfig.json`, `vite.config.ts`, `vitest.config.ts`).

Do not add a second `components/` or `lib/` tree next to `src/` at the frontend root—those duplicate `src/` and are not part of the build.

Full repo conventions (backend + frontend): **[`docs/CODEBASE_LAYOUT.md`](../docs/CODEBASE_LAYOUT.md)**.

A2A task envelope, tool registry, assignment, and validation (platform → agent JSON): **[`docs/A2A_TASK_AND_ASSIGNMENT.md`](../docs/A2A_TASK_AND_ASSIGNMENT.md)**.

## Scripts

| Command | Purpose |
|---------|--------|
| `npm run dev` | Dev server (port 3000) |
| `npm run build` | `tsc` + production bundle |
| `npm run test` | Vitest (see `vitest.config.ts`) |
| `npm run lint` | ESLint |

Node version: see `package.json` `engines` and `.nvmrc`.
