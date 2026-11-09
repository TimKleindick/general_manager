# GeneralManager Agent Guidelines

Follow [CONTRIBUTING.md](CONTRIBUTING.md) for repository-specific contribution, validation, and release requirements.

## Commit and Branch Discipline

- Use conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`,
  `refactor:`).
- Keep pull requests focused; open a new branch per topic (e.g.
  `feature/short-description`).

## Environment and Tooling

- Target Python 3.12+ and install dependencies via
  `pip install -r requirements/development.txt` followed by `pip install -e .`.
- Install and respect `pre-commit` hooks; run `pre-commit run --all-files` when
  touching many files.

## Existing Responsibilities and Reuse

- Before any implementation, including features, bug fixes, refactoring,
  optimizations, validations, and checks, inspect the existing GeneralManager
  code and call paths to identify where that responsibility is already handled
  and which layer owns it.
- Reuse or extend the owning implementation instead of duplicating its work.
  Add a separate check only for a demonstrated gap or a distinct responsibility;
  document the gap and why the existing implementation does not cover it.
- For chat operations executed through GraphQL, rely on GraphQL's permission
  enforcement with the correct user and request context. Verify that execution
  path and context before relying on it; do not duplicate permission evaluation
  in chat when GraphQL already performs it.
- Preserve checks with distinct responsibilities, including input validation,
  schema and evidence consistency, and write confirmation. Identify their owner
  and purpose before adding, moving, or removing them.

## Coding Standards

- Prefer clear, explicit code to clever abstractions. When intent is not
  immediately obvious, add concise English comments or docstrings.
- Run `ruff check`, `ruff format`, and `mypy` before opening a PR. Fix warnings
  instead of silencing them unless absolutely necessary.
- Keep new dependencies rare and document why they are required.

## Testing Expectations

- Add or extend tests in `tests/` for every behavioral change.
- Run `python -m pytest` locally and ensure the suite passes before requesting
  review.
- Exercise edge cases, async behavior, and Django integrations when relevant.

## Documentation Duties

- Update `docs/`, README, or changelog entries whenever user-facing behavior
  changes. Ensure examples remain accurate.
- Use clear language and formatting consistent with existing documentation.

## Repository Artifact Hygiene

- Do not add temporary benchmark reports, measurement dumps, verification
  receipts, execution logs, or other task-specific evidence files to the
  repository, regardless of format (including Markdown and JSON).
- Do not add Superpowers artifacts or other agent workflow artifacts, such as
  generated plans, progress notes, review transcripts, or handoff documents.
- Keep these artifacts outside the repository, for example in a task-specific
  directory under `/tmp`. Summarize relevant results in the pull request
  description instead of committing the raw evidence.
- Keep reusable automated tests, intentional test fixtures, and permanent
  product documentation in their existing repository locations. These are not
  temporary evidence or workflow artifacts.
- Before committing or updating a pull request, inspect its file list and
  remove any temporary evidence or workflow artifacts introduced by the change.

## Issue and Release Hygiene

- Reference existing GitHub issues or create one before starting significant
  work to avoid duplication.
- Do not modify version numbers manually; semantic-release handles tagging and
  PyPI publishing based on your commit history.
