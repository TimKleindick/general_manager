# Seeding

GeneralManager seeding builds local, demo, and test data from managers that expose factories. It is dependency-aware enough to order selected managers by interface relationships, but explicit enough that it does not silently create unrelated data.

## Seedable managers

A manager is seedable when it exposes callable `Factory.create_batch(count)`.
Discovery is based on registered GeneralManager classes, then filtered to
managers with that factory capability. Selection keys are manager class names.
If two seedable managers share the same class name, discovery raises
`SeedableManagerCollisionError` instead of guessing which manager a command-line
name should target. The `seed_manager_landscape` command catches that helper
exception and exposes it to command callers as `CommandError`; direct helper
calls receive the original exception type.

## Targets and minimum counts

Seed targets are minimum desired row counts. If a target asks for 10 `Project` rows and 12 already exist, seeding creates nothing for `Project`. If only 4 exist, it creates 6.

The command requires explicit selection: pass `--manager` at least once or use
`--all`. Omitting both raises `CommandError` before discovery or seeding.

Use `--target ManagerName=COUNT` to override the default count for one selected
manager. Target values are parsed as `NAME=COUNT`, whitespace is ignored, counts
must be positive integers, and duplicate target overrides are rejected. Empty
override input is treated as no overrides. Override names are validated against
all discovered managers before selection, so unknown overrides are reported as
unknown managers before unselected known overrides are reported. Repeated
`--manager` values keep the first occurrence.

## Dependency ordering

The seeding planner orders selected managers so required non-null `ForeignKey`
and `OneToOneField` relations are created first when both sides are selected. It
does not automatically add missing dependency managers. Select dependent
managers explicitly when a factory expects related rows to exist. Dry-run plan
rows list those missing dependencies by manager class name. Dependency discovery
reads the manager interface model metadata, follows
`remote_field.model._general_manager_class`, and ignores nullable relations,
self-relations, non-relation fields, and managers without model metadata.

## Batching and failures

`--batch-size` controls how many rows are created per transaction. Each batch is
wrapped in its own `transaction.atomic()` block, and there is no cross-manager
transaction. By default, seeding stops on the first failure and reports the
manager, failed batch size, original error, rows created before failure, and
remaining count. With `--continue-on-error`, later managers continue and the
failing manager stops after its first failed batch. The final result includes a
created-count entry for every ordered target, even when no rows were needed, plus
partial progress and failures. Batches committed before a failure remain
committed. A run with collected failures still exits with `CommandError` after
printing the summary, even if earlier or later managers were seeded
successfully. The summary includes the failed manager name, original error text,
created count, remaining count, and failed batch size.

## Dry runs

Use `--dry-run` to inspect the selected managers, target counts, and missing
dependencies without writing data. Human dry runs print one line per ordered
manager. `--output-format json` prints a JSON array with `manager_name`,
`target_count`, and `missing_dependencies` for automation.

## Command validation

The command raises Django's `CommandError` for invalid selections, malformed
targets, non-positive counts or batch sizes, unsupported output formats, and
seeding failures. When called through `call_command()`, `--manager` and
`--target` use the public keyword names `manager=` and `target=`. The parser
defines internal destinations named `managers` and `targets`, so code inside the
command sees plural keys after Django normalizes options. Programmatic values
may be passed as `None`, a string, or a sequence of strings; `None` is treated
as omitted. Integer options may be passed as `int` or strings accepted by
Python's `int()` parser, but `bool` is rejected even though it is an `int`
subclass. After parsing, non-positive `count` and `batch_size` values raise
`CommandError`. Boolean switches must be actual booleans. Invalid programmatic
option types are rejected before any manager is seeded.

## Related references

- [Bulk factory generation](../howto/factory_bulk_generate.md)
- [Seeding API reference](../api/seeding.md)
