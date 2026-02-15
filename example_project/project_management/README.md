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
