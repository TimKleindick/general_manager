# new_structure

Django project scaffold for migrating selected Knowledge Hub managers to the
new GeneralManager interface model.

## Location

`example_project/knowledege_hub/new_structure`

## Included managers

- `Project` (`DatabaseInterface`)
- `Customer` (`DatabaseInterface`)
- `AccountNumber` (`DatabaseInterface`)
- `ProjectTeam` (`DatabaseInterface`)
- `User` (`DatabaseInterface`)
- `Derivative` (`DatabaseInterface`)
- `Plant` (`DatabaseInterface`)
- `CustomerVolume` (`DatabaseInterface`)
- `ProjectUserRole` (`ReadOnlyInterface`)
- `ProjectPhaseType` (`ReadOnlyInterface`)

Additional read-only support managers are included for FK completeness:

- `ProjectType`
- `Currency`
- `DerivativeType`

Managers are split across `core/managers/` domain modules.

UI routes:
- `/projects/` project selector with infinite scroll and live updates.
- `/dashboard/?projectId=<id>` project dashboard with grouped cards, modals, and live updates.
- `/dashboard/` serves the SPA shell; React routing guards redirect to `/projects/` when `projectId` is missing.

Frontend stack:
- React 18 + TypeScript + Redux Toolkit
- Vite build pipeline (compiled assets served by Django static files)
- Tailwind + shadcn-style UI components
- Recharts for project and derivative volume diagrams

Frontend workspace:
- `frontend/` (source code, build config, and npm scripts)

Compiled frontend assets:
- `core/static/core/dashboard_app/assets/app.css`
- `core/static/core/dashboard_app/assets/app.js`

Django template mount points:
- `core/templates/core/spa_entry.html` renders `<div id="app-root">` and is used by both `/projects/` and `/dashboard/`.

Build commands (run in `example_project/project_management/frontend`):
- `npm install`
- `npm run typecheck`
- `npm run build`

## Docker Compose (ORL-style full stack)

`project_management` includes a full Docker stack strongly aligned with the ORL setup:

- `web` (Django + Daphne, scale target)
- `celery` (background tasks)
- `nginx` (reverse proxy + load balancing)
- `db` (PostgreSQL)
- `redis` (cache + Celery broker/result backend)
- `meilisearch` (search backend)
- `django-init` (migrations, collectstatic, search reindex, optional seeding)

Files:

- `example_project/project_management/docker-compose.yml`
- `example_project/project_management/Dockerfile`
- `example_project/project_management/docker/entrypoint.sh`
- `example_project/project_management/docker/init-entrypoint.sh`
- `example_project/project_management/docker/entrypoint-celery.sh`
- `example_project/project_management/docker/nginx.conf.template`
- `example_project/project_management/docker/nginx-entrypoint.sh`
- `example_project/project_management/.env.example`

Run:

```bash
cd example_project/project_management
cp .env.example .env
docker compose up --build
```

Scale web workers behind nginx:

```bash
docker compose up --build --scale web=3
```

Common environment variables (`.env`):

- `PM_HTTP_PORT` (default `8000`)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `MEILISEARCH_API_KEY` (used by web and meilisearch)
- `GEMINI_API_KEY` (required for `/ai/chat` when using Gemini backend)
- `GM_MCP_UNANSWERED_LOGGER` (default `general_manager.mcp.unanswered.log_to_file`)
- `GM_MCP_UNANSWERED_LOG_FILE` (optional JSONL path for fast setup logging)
- `GM_MCP_UNANSWERED_LOG_MODEL` (used with `general_manager.mcp.unanswered.log_to_model`)
- `PM_SEED_ON_START=true` to run `generate_test_data` once in `django-init`

Notes:

- Static assets are collected to shared volume `pm-static` and served by nginx.
- Search indexes are rebuilt during `django-init` (`python manage.py search_index --reindex`).
- Redis is used for both Django cache and Celery broker/result backend.
