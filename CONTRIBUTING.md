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
- Run `mypy` to maintain type safety.
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
- `python -m pytest`, `ruff check`, and `mypy` all pass locally.
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
