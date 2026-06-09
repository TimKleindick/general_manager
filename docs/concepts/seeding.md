# Seeding

GeneralManager seeding builds local, demo, and test data from managers that expose factories. It is dependency-aware enough to order selected managers by interface relationships, but explicit enough that it does not silently create unrelated data.

## Seedable managers

A manager is seedable when it exposes `Factory.create_batch`. Discovery is based on registered GeneralManager classes, then filtered to managers with that factory capability.

## Targets and minimum counts

Seed targets are minimum desired row counts. If a target asks for 10 `Project` rows and 12 already exist, seeding creates nothing for `Project`. If only 4 exist, it creates 6.

Use `--target ManagerName=COUNT` to override the default count for one selected manager.

## Dependency ordering

The seeding planner orders selected managers so required database relations are created first when both sides are selected. It does not automatically add missing dependency managers. Select dependent managers explicitly when a factory expects related rows to exist.

## Batching and failures

`--batch-size` controls how many rows are created per transaction. By default, seeding stops on the first failure and reports the manager and batch size. With `--continue-on-error`, later managers continue and the final result includes partial progress and failures.

## Dry runs

Use `--dry-run` to inspect the selected managers, target counts, and missing dependencies without writing data.

## Related references

- [Bulk factory generation](../howto/factory_bulk_generate.md)
- [Seeding API reference](../api/seeding.md)
