# Seed Data From Manager Landscape

Use the `seed_manager_landscape` management command to populate database-backed managers using each manager's `Factory`.

This command:

1. Discovers seedable managers from `GeneralManagerMeta.all_classes`.
2. Keeps only writable interface types (`database`, `existing`).
3. Builds a dependency-aware seed order from:
   - manager-typed `Interface.input_fields`
   - Django model forward relations (`ForeignKey`, `OneToOneField`, `ManyToManyField`)
4. Calls `Factory.create_batch(count)` for each manager.
5. Retries each manager up to `--retries` times (in addition to the first attempt).

## Basic usage

```bash
python manage.py seed_manager_landscape
```

Default behavior creates `10` rows per selected manager with `2` retries per manager.

## Common options

```bash
python manage.py seed_manager_landscape --count 25
python manage.py seed_manager_landscape --count 50 Project=20 Derivative=200
python manage.py seed_manager_landscape --manager Project Derivative
python manage.py seed_manager_landscape --retries 2
python manage.py seed_manager_landscape --dry-run
python manage.py seed_manager_landscape --fail-fast
```

- `--count`: global rows per manager and optional overrides (`>= 1`).
  - `--count 50` -> all selected managers use `50`
  - `--count 50 Project=20` -> `Project` uses `20`, others use `50`
  - `--count Project=20 Derivative=200` -> unspecified managers use default `10`
  - if `--manager` narrows selection, overrides for unselected managers are ignored with a warning
- `--manager`: restrict seeding to specific manager class names (space-separated).
- `--retries`: retries per manager after the first failure (`>= 0`).
- `--dry-run`: prints the seed plan only; does not create rows.
  - includes seed order and resolved per-manager counts
- `--fail-fast`: stop immediately when a manager exhausts its attempts.

## Dependency ordering

The command inspects manager inputs and seeds dependencies first.

Example:

- `Derivative.Interface` has an input typed as `Project`.
- `Project` is seeded before `Derivative`.

If the dependency graph contains cycles, the command falls back to deterministic name ordering and still reports unresolved failures clearly.
It also emits a warning with the detected cycle path.

## Failure output

If a manager still fails after all attempts, the command raises `CommandError` with:

- manager name
- number of attempts
- discovered dependencies
- exception type and message

Example format:

```text
- Derivative: failed after 3 attempt(s); depends_on=[Project]; last_error=ValidationError: ...
```

## Unique conflicts

If a manager repeatedly fails with uniqueness conflicts, the command treats this as
retry/skip behavior:

1. retry up to the configured attempts (`1 + --retries`)
2. skip that manager run if uniqueness conflicts persist
3. continue with remaining managers

This keeps seeding generic and avoids manager-specific special cases in the command.
