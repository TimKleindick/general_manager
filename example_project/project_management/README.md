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
- `/dashboard/` advanced dashboard with CRUD and live subscriptions.
- `/projects/` legacy project list UI with live updates enabled.
