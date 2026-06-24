# Seeding API

::: general_manager.seeding.manager_landscape.InvalidSeedTargetError

::: general_manager.seeding.manager_landscape.ManagerSelectionError

::: general_manager.seeding.manager_landscape.SeedableManagerCollisionError

::: general_manager.seeding.manager_landscape.ManagerSeedFailure

::: general_manager.seeding.manager_landscape.SeedTarget

::: general_manager.seeding.manager_landscape.SeedPlanRow

::: general_manager.seeding.manager_landscape.SeedFailure

::: general_manager.seeding.manager_landscape.SeedExecutionResult

::: general_manager.seeding.manager_landscape.parse_target_overrides

::: general_manager.seeding.manager_landscape.discover_seedable_managers

::: general_manager.seeding.manager_landscape.select_seed_targets

::: general_manager.seeding.manager_landscape.order_targets_by_dependencies

::: general_manager.seeding.manager_landscape.build_seed_plan

::: general_manager.seeding.manager_landscape.execute_seed_plan

Manager landscape seeding is a thin planning layer over manager factories. A
manager is seedable only when its nested `Factory` exposes callable
`create_batch(count)`. Discovery returns a class-name keyed mapping in input
order and rejects duplicate seedable class names with
`SeedableManagerCollisionError`; module-qualified names are not selection keys
for this helper.

`parse_target_overrides(raw_targets)` returns `{}` for `None` or empty input.
Otherwise it accepts repeated `NAME=COUNT` strings, trims whitespace, requires
positive integer counts, and rejects duplicate names. `select_seed_targets(...)`
resolves either explicit manager names or `include_all` selection, deduplicates
repeated explicit names by keeping the first occurrence, and applies per-manager
overrides. Override names are validated against all known managers before
selection, so an unknown override raises `ManagerSelectionError.unknown_manager`
before the unselected-override check. Known overrides for managers that are not
selected raise `ManagerSelectionError.unselected_overrides`.

`order_targets_by_dependencies(...)` orders selected targets so required
non-null `ForeignKey` and `OneToOneField` dependencies run first when both sides
are selected. Dependency discovery reads
`manager.Interface._model._meta.get_fields()`, follows each field's
`remote_field.model._general_manager_class`, excludes nullable relations,
self-relations, non-relation fields, and managers without model metadata. It
does not add unselected dependencies. `build_seed_plan(...)` returns ordered
`SeedPlanRow` objects and reports unselected required dependencies by manager
class name.

`execute_seed_plan(...)` dependency-orders targets again, reads
`manager.all().count()` for each ordered target, initializes
`SeedExecutionResult.created[manager_name] = 0` for every ordered target, creates
only the missing rows, and calls `Factory.create_batch(size)` in batches of at
most `batch_size`. Each batch uses its own `transaction.atomic()` block; there is
no cross-manager transaction. Invalid batch sizes raise `ManagerSelectionError`.
Factory failures raise `ManagerSeedFailure` by default, including the manager
name, failed batch size, original error, created count, and remaining count.
With `continue_on_error=True`, the failing manager stops after the first failed
batch, the failure is collected as a `SeedFailure`, and later managers continue.
The returned `SeedExecutionResult.created` value is a concrete immutable
`MappingProxyType`, and `failures` is a tuple.
