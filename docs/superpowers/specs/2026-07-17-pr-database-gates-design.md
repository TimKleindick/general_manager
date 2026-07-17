# Pull Request Database Gates Design

## Goal

Make PostgreSQL and MariaDB compatibility merge-blocking on every pull request
without running the complete six-job release matrix on every change.

## Current State

The Quality workflow runs the SQLite-backed functional suite on Python 3.12,
3.13, and 3.14 for pull requests. Its PostgreSQL 18 and MariaDB 11.8 LTS
matrix runs only when the reusable Quality workflow is called from the
push-triggered release workflow. The active default-branch ruleset requires
the three Python test contexts and `lint-and-mypy`, but no server-database
context.

## CI Policy

Every pull request must run the complete non-performance functional suite on:

- PostgreSQL 18 with Python 3.14.
- MariaDB 11.8 LTS with Python 3.14.

The two jobs use `fail-fast: false`. A failure in either job blocks merging,
and both jobs finish so one backend cannot hide a failure in the other.

The release workflow retains the complete matrix:

- PostgreSQL 18 with Python 3.12, 3.13, and 3.14.
- MariaDB 11.8 LTS with Python 3.12, 3.13, and 3.14.

Manual Quality dispatches retain their current behavior and do not start
server-database jobs. No path filters are introduced; every pull request gets
the same backend-compatibility guarantee.

## Workflow Architecture

Add a reusable workflow dedicated to one backend/Python test execution. Its
inputs provide the Python version and database-specific configuration:

- display name;
- test-settings selector;
- service image and port;
- database user;
- Python database driver; and
- service health command.

The reusable workflow owns the database and Meilisearch services, Python
setup, package and driver installation, database environment variables, and
the `python -m pytest -m "not perf"` command. This keeps the runner behavior in
one place.

`quality.yml` supplies two separate matrix callers:

1. A pull-request caller guarded by `github.event_name == 'pull_request'`.
   Its matrix contains PostgreSQL and MariaDB and fixes Python at 3.14.
2. A release caller guarded by `github.event_name == 'push'`. Its matrix
   contains the same databases crossed with Python 3.12, 3.13, and 3.14.

Both callers invoke the same reusable workflow. Their job names explicitly
distinguish PR gates from release coverage and remain stable so GitHub can use
the PR names as required status-check contexts.

## Failure Behavior

The following conditions fail the corresponding backend check:

- the database or Meilisearch service does not become healthy;
- Python, package, or database-driver installation fails;
- Django migration or setup fails; or
- any selected functional test fails.

The reusable runner does not suppress or downgrade these failures. Release
artifacts continue to depend on the Quality workflow, so a release backend
failure prevents publication.

## Repository Ruleset Rollout

The active default-branch ruleset, `Don't push directly to main`, already
requires the Python 3.12-3.14 test checks and `lint-and-mypy`. After the new
workflow is pushed to PR #408:

1. Wait for both PR backend jobs to appear and complete successfully.
2. Read their exact emitted GitHub check-context names.
3. Add those two contexts to the ruleset's existing required-status-check
   list.
4. Preserve strict status checks, the existing required contexts, review
   requirements, code-scanning policy, bypass actors, and merge method.
5. Confirm the PR is blocked while either new context is pending or failing
   and is eligible after both pass.

The ruleset is not updated using predicted names. Observing the emitted names
first avoids creating an impossible required check because reusable workflows
compose caller and called-job names.

## Tests

Workflow contract tests must verify:

- the reusable workflow exposes the intended inputs;
- its database and Meilisearch services preserve the tested images, health
  checks, ports, and credentials;
- it installs the selected driver and runs the complete non-performance test
  suite with the selected Django database environment;
- the pull-request caller contains exactly PostgreSQL and MariaDB on Python
  3.14 and is restricted to pull-request events;
- the release caller contains both databases across Python 3.12-3.14 and is
  restricted to push events; and
- manual dispatch does not start either server-database caller.

Relevant workflow unit tests, Ruff lint/format, strict mypy, and the local
functional suite must remain green. PR #408 must then pass both new backend
jobs, the existing PR checks, and the repository ruleset verification.

## Acceptance Criteria

- Every pull request reports two stable server-database checks: PostgreSQL 18
  / Python 3.14 and MariaDB 11.8 LTS / Python 3.14.
- Both checks run `pytest -m "not perf"` and are required by the active
  default-branch ruleset.
- The release workflow still runs all six backend/Python combinations before
  artifact publication.
- Workflow implementation is shared rather than duplicated between PR and
  release jobs.
- Manual Quality dispatch and the existing non-database PR gates retain their
  current behavior.
