# Database Backend CI Coverage Design

## Purpose

GeneralManager currently runs its test suite against SQLite only, so its README
cannot substantiate full support for PostgreSQL or MariaDB. Add release-gated CI
coverage across every supported Python version so the project can explicitly
claim SQLite, PostgreSQL, and MariaDB as supported backends.

## Supported Matrix

The existing pull-request matrix remains unchanged:

| Backend | Python 3.12 | Python 3.13 | Python 3.14 |
| --- | --- | --- | --- |
| SQLite | Yes | Yes | Yes |

The push-triggered release workflow adds this compatibility matrix:

| Backend | Python 3.12 | Python 3.13 | Python 3.14 |
| --- | --- | --- | --- |
| PostgreSQL 18 | Yes | Yes | Yes |
| MariaDB 11.8 LTS | Yes | Yes | Yes |

PostgreSQL 18 is a currently supported PostgreSQL release. MariaDB 11.8 is a
stable long-term-support release maintained through June 2028. Major-version
container tags allow CI to receive compatible patch and security updates while
keeping the tested database feature set stable.

## Workflow Architecture

Add a separate database-backend job to `.github/workflows/quality.yml`. Its
matrix contains PostgreSQL and MariaDB entries and the three supported Python
versions. Each matrix leg starts the selected database service, installs the
matching Django driver, and runs the test suite against that service.

The backend job uses `if: github.event_name == 'push'`. Pull requests therefore
continue to run only the existing SQLite matrix. The reusable quality workflow
is invoked by `.github/workflows/publish.yml` for pushes to `main`; its caller
retains the `push` event context, so all six backend legs run before the
artifact and release jobs. Because the artifact job already depends on the
reusable quality job, any backend failure blocks publication.

The workflow's manual trigger does not run the release-only backend matrix.
This keeps the condition tied specifically to the release pipeline rather than
to every invocation of the quality workflow.

## Test Database Configuration

Update `tests/test_settings.py` so `GENERAL_MANAGER_TEST_DATABASE` selects one
of three explicit configurations:

- An unset value or `sqlite` keeps the current in-memory SQLite database.
- `postgresql` selects `django.db.backends.postgresql`.
- `mariadb` selects `django.db.backends.mysql`, which is Django's MariaDB
  backend.

Connection values for server databases come from
`GENERAL_MANAGER_TEST_DATABASE_NAME`, `GENERAL_MANAGER_TEST_DATABASE_USER`,
`GENERAL_MANAGER_TEST_DATABASE_PASSWORD`,
`GENERAL_MANAGER_TEST_DATABASE_HOST`, and
`GENERAL_MANAGER_TEST_DATABASE_PORT`. The CI workflow sets every value
explicitly. Unsupported selector values raise `ValueError` during settings
import so configuration errors fail early instead of silently falling back to
SQLite.

The PostgreSQL matrix installs Psycopg 3 with its binary extra. The MariaDB
matrix installs `mysqlclient`. These remain CI dependencies rather than project
runtime dependencies because Django applications choose and manage their own
database driver.

## Test Scope

Each PostgreSQL and MariaDB leg runs the complete pytest suite with the `perf`
marker excluded. Functional unit and integration tests therefore execute on
each backend. The existing Meilisearch service remains available so unrelated
integration coverage does not silently disappear from these runs.

Performance tests stay on SQLite. Their deterministic query-count budgets are
documented as SQLite-calibrated and cannot be reused as backend-independent
budgets without separate measurement and maintenance work.

Add regression tests that verify:

- the default SQLite and explicit PostgreSQL/MariaDB settings;
- rejection of an unknown database selector;
- the two-backend by three-Python-version workflow matrix;
- the push-only condition and service/driver/test-command contract;
- the release dependency continues to block artifacts on all quality jobs.

Configuration and workflow regression tests are written before their
corresponding changes and observed failing for the missing behavior.

## Documentation Contract

Replace the README's claim that GeneralManager works with any Django-supported
database with a dedicated backend-support statement:

> GeneralManager builds on Django's database layer, but this project only
> claims backend support covered by its tests or maintained examples. SQLite,
> PostgreSQL, and MariaDB are exercised by CI and are fully supported. Other
> Django-supported backends may work, but are not currently claimed as
> supported by GeneralManager.

This wording distinguishes Django's capabilities from GeneralManager's own
maintenance commitment and directly connects the support claim to automated
coverage.

## Validation

Run the new settings and workflow regression tests first, followed by the
existing SQLite suite, Ruff, formatting checks, and mypy. The PostgreSQL and
MariaDB service matrix is ultimately verified in GitHub Actions because the
release-only job is defined for GitHub-hosted service containers. If compatible
local containers are available, matching focused runs may supplement but do not
replace the workflow validation.

## Out of Scope

- Running PostgreSQL or MariaDB jobs on pull requests.
- Claiming support for MySQL, Oracle, or third-party Django backends.
- Adding database drivers to GeneralManager's runtime dependencies or public
  optional extras.
- Creating backend-specific performance budgets.
- Testing every maintained major release of PostgreSQL or MariaDB.
