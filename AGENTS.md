# GeneralManager Agent Guidelines

Follow the published CONTRIBUTING.md so that every change looks and feels the
same as work done by maintainers. The highlights below are the minimum bar for
agents executing tasks in this repository.

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

## Issue and Release Hygiene

- Reference existing GitHub issues or create one before starting significant
  work to avoid duplication.
- Do not modify version numbers manually; semantic-release handles tagging and
  PyPI publishing based on your commit history.

By following these rules every contribution stays aligned with the official
CONTRIBUTING policy and is easy for maintainers to review.
