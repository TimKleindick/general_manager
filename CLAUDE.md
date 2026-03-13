# General Manager — Claude Code Operating Contract

## Instruction Precedence

- System/safety constraints first, then this file, then task-specific preferences.
- If instructions conflict, obey higher-priority instructions and note the conflict briefly.

## Task Complexity Classification

- `trivial`: touches <=2 files, local behavior change only, no API/DB/auth/security/migration impact.
- `standard`: bounded multi-file task with no architecture pivot.
- `hard`: architecture decisions, cross-module contracts, migrations, auth/security-sensitive flows, or major refactors.

## Workflow Orchestration

### 1. Plan-First Default

- Use `EnterPlanMode` for non-trivial tasks (3+ steps, architectural choices, migrations, or cross-file behavior changes).
- If implementation diverges from plan, stop and re-plan before continuing.
- Define explicit success criteria and verification criteria before coding.

### 2. Subagent Strategy

- For non-trivial tasks, use the Task tool with specialized agents to reduce context pressure.
- Use `Explore` agents for discovery and codebase mapping.
- Use `Plan` agents for architecture evaluation.
- Use `Bash` agents for test runs and build verification.
- For tasks with 2+ independent exploration tracks, parallelize via multiple agents.
- Avoid unnecessary fan-out for small, local, low-uncertainty changes.

### 3. Lessons-First Execution

- Before planning or editing on non-trivial tasks, check `tasks/lessons.md` for relevant lessons.
- Extract applicable lessons and convert them into concrete execution constraints for the current task.
- After implementation, append new lessons in format: **trigger → action → verification**.
- Before writing a new lesson, scan for duplicates; update/merge existing lessons instead of appending near-duplicates.
- Avoid vague lessons ("be careful", "test more"). Keep them specific and actionable.

### 4. Task Tracking

- Use TodoWrite to maintain a live checklist for non-trivial work.
- Update checklist state as soon as work status changes.
- If scope changes, add new items before continuing.

### 5. Verification Before Completion

- Prove behavior with the smallest sufficient verification set (targeted tests first, broader checks as needed).
- Compare behavior before/after when regression risk is non-trivial.
- Record verification commands and outcomes.

### 6. Elegance and Scope Control

- Prefer simple, high-leverage fixes over broad rewrites.
- If a solution feels hacky, replace it with the cleanest minimal alternative before finalizing.
- Limit changes to files required for the task.

### 7. Autonomous Execution

- When a bug is reported, reproduce, isolate root cause, implement fix, and verify without hand-holding.
- Use logs, failing tests, and concrete evidence rather than speculation.

## Quality Bar

- **Clarity**: Keep instructions concrete and enforceable.
- **Rigor**: Verify claims with commands, tests, or logs.
- **Minimal Impact**: Change only what is necessary.

## Tech Stack

- Python (Django backend), with pytest for testing
- Ruff for linting/formatting, mypy for type checking
- Docker Compose for services
- pre-commit hooks enabled

## Common Commands

```bash
# Tests
python -m pytest                    # full suite
python manage.py test               # Django tests

# Linting / Formatting
ruff check .
ruff format .
mypy .

# Django
python manage.py migrate
python manage.py makemigrations
python manage.py check

# Docker
docker compose up
docker compose ps
docker compose logs
```
