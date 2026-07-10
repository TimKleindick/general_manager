# Contributing to GeneralManager

Thank you for helping make GeneralManager better. This document explains the
expectations for contributors so you can spend your time building features
instead of guessing the workflow.

## Ways to Contribute

- Improve documentation, tutorials, or examples.
- Triage and reproduce bugs filed through GitHub issues.
- Add new tests or harden existing code paths.
- Propose new features or API improvements via issues or discussion threads.

Please open an issue before working on significant changes so we can align on
scope and avoid duplicated effort.

## Local Development

1. **Clone and branch**
   ```bash
   git clone https://github.com/TimKleindick/general_manager.git
   cd general_manager
   git checkout -b feature/short-description
   ```
2. **Create a virtual environment** using Python 3.12 or newer, then activate it.
3. **Install dependencies**
   ```bash
   pip install -r requirements/development.txt
   pip install -e .
   pre-commit install
   ```

The `pre-commit` hooks run Ruff, formatting, and other sanity checks before each
commit. You can run them manually with `pre-commit run --all-files`.

## Coding Standards

- Follow type-hinted, readable Python. Prefer simple, explicit code over clever
  one-liners.
- Run `ruff check` and `ruff format` to keep lint and style consistent.
- Run `mypy --strict` to maintain type safety.
- Keep dependencies minimal and document any new third-party package you add.
- Use docstrings and inline comments sparingly but clearly when the intent is
  not obvious from the code itself.

## Testing

- Add or update tests for every behavior change. Focus on edge cases that guard
  against regressions.
- Run the full suite locally before opening a pull request:
  ```bash
  python -m pytest
  ```

### Performance regression tests

Performance tests remain enabled as regression gates in the normal test suite.
To run only those tests, select the `perf` marker:

```bash
python -m pytest -m perf
```

For a focused database run or a complete calibration recording, use:

```bash
python -m pytest tests/perf/test_database_bucket_perf.py -vv
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf -q -s
```

The focused command's `-vv` enables diagnostic collection, but pytest still
captures output from passing tests. Display the diagnostic lines by disabling
capture:

```bash
python -m pytest tests/perf/test_database_bucket_perf.py -vv -s
```

Deterministic integer ceilings for queries, callbacks, source yields,
manager/group constructions, and cache work are CI pass/fail gates. Elapsed time
and peak allocations are diagnostics only: representative cases collect them
with `-vv`, but they never determine whether a test passes. Setup and fixture
work must stay outside measured blocks, and functional correctness assertions
must accompany the metrics.

Record mode prints the deterministic observations and bypasses ceiling
enforcement. Functional assertions, budget validity and uniqueness, and the
full-manifest safeguards remain active. A successful recording is therefore not
proof that the regression gates pass; follow it with a normal enforced run such
as `python -m pytest -m perf`.

CI covers Python 3.12, 3.13, and 3.14. Calibrate a ceiling only after three
complete recording runs in each relevant supported environment produce
identical ordered name/value output within that environment. Compare only the
ordered `PERF_OBSERVATION ` entries, not pytest progress or duration; from a
saved log, extract the matches with
`rg -o 'PERF_OBSERVATION [A-Z0-9_]+=[0-9]+' run.log`. Record exact current
counts without padding.
If stable counts differ by Python version, use the maximum stable count as the
ceiling and add an adjacent comment in `tests/perf/budgets.py` with the
per-version values and reason.

Every budget name must be unique and observed exactly once. Full-manifest
validation runs at pytest session finish only when all three budget workload
modules (calculation, database, and group) were selected, no `-k` expression was
used, and the session otherwise succeeded. A budget increase requires an
adjacent explanatory comment in `tests/perf/budgets.py` and
performance-regression review. Lower a ceiling only after stable before/after
evidence supports the change.

The current deterministic counts describe the SQLite CI baseline. Add separate
coverage if a backend needs its own budgets; do not assume these counts apply to
unsupported databases. Issue #337 establishes measurement infrastructure and
intentionally makes no claim of a direct runtime speedup. Later optimizations
should use these baselines and lower them when stable evidence supports it.

## Documentation

Documentation lives in the `docs/` directory and is published with MkDocs. To
preview docs locally:

```bash
pip install -r requirements/development.txt
mkdocs serve
```

Update tutorials, API references, or changelog entries whenever your change
impacts users. Screenshots or diagrams should live under `docs/assets/`.

## Pull Request Checklist

- Changes are rebased on the latest `main` branch.
- Commits follow the Conventional Commits format (`feat:`, `fix:`, `docs:` …).
- `python -m pytest`, `ruff check`, and `mypy --strict` all pass locally.
- New or updated documentation is included when behavior changes.
- The PR description explains the problem, the chosen solution, and any
  follow-up work.

PRs that are small and well-tested are easier to review. Drafts are welcome if
you need early feedback.

## Issue Reporting

- Use the existing templates in `.github/ISSUE_TEMPLATE/`.
- Include reproduction steps, relevant stack traces, and environment details.
- Tag issues with `bug`, `enhancement`, or `question` when possible.

## Release Management

The repository uses semantic-release to publish PyPI packages and changelog
entries automatically. Avoid editing version numbers manually; instead focus on
clear commit messages and updated tests so release automation can classify your
changes correctly.

Thanks again for investing your time into GeneralManager!
