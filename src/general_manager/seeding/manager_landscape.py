"""Dependency-aware manager seeding helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast

from django.db import models, transaction

from general_manager.interface.infrastructure.startup_hooks import (
    order_interfaces_by_dependency,
)


class CountableSeedResult(Protocol):
    """Object returned by manager ``all()`` during seeding."""

    def count(self) -> int:
        """Return the current row count for the manager."""
        ...


class SeedBatchFactory(Protocol):
    """Factory capability used by landscape seeding."""

    def create_batch(self, count: int) -> object:
        """Create one seed batch and return the factory-specific result."""
        ...


class SeedableManagerRuntime(Protocol):
    """Manager class shape required while executing a seed plan."""

    Factory: SeedBatchFactory

    def all(self) -> CountableSeedResult:
        """Return a bucket-like object with the current row count."""
        ...


class InvalidSeedTargetError(ValueError):
    """Raised when a seed target argument cannot be parsed."""

    @classmethod
    def invalid_format(cls, raw: str) -> InvalidSeedTargetError:
        return cls(f"Target override must use NAME=COUNT format: {raw!r}")

    @classmethod
    def invalid_integer(
        cls,
        manager_name: str,
    ) -> InvalidSeedTargetError:
        return cls(f"Target count for {manager_name!r} must be an integer.")

    @classmethod
    def not_positive(cls, manager_name: str) -> InvalidSeedTargetError:
        return cls(f"Target count for {manager_name!r} must be greater than zero.")

    @classmethod
    def duplicate(cls, manager_name: str) -> InvalidSeedTargetError:
        return cls(f"Duplicate target override for {manager_name!r}.")


class ManagerSelectionError(ValueError):
    """Raised when manager selection arguments are invalid."""

    @classmethod
    def invalid_count(cls) -> ManagerSelectionError:
        return cls("--count must be greater than zero.")

    @classmethod
    def missing_selection(cls) -> ManagerSelectionError:
        return cls("Pass --manager at least once or use --all.")

    @classmethod
    def unknown_manager(cls, manager_name: str) -> ManagerSelectionError:
        return cls(f"Unknown manager: {manager_name}")

    @classmethod
    def unselected_overrides(cls, manager_names: str) -> ManagerSelectionError:
        return cls(
            f"Target override provided for unselected manager(s): {manager_names}"
        )

    @classmethod
    def invalid_batch_size(cls) -> ManagerSelectionError:
        return cls("--batch-size must be greater than zero.")

    @classmethod
    def conflicting_selection(cls) -> ManagerSelectionError:
        return cls("--all and --manager are mutually exclusive.")


class SeedableManagerCollisionError(ValueError):
    """Raised when seedable manager discovery finds ambiguous class names."""

    @classmethod
    def duplicate_name(
        cls,
        name: str,
        existing: type[object],
        new: type[object],
    ) -> SeedableManagerCollisionError:
        existing_name = f"{existing.__module__}.{existing.__name__}"
        new_name = f"{new.__module__}.{new.__name__}"
        return cls(
            "Multiple seedable managers share the class name "
            f"{name!r}: {existing_name} and {new_name}. Use the "
            "explicit manager list to avoid ambiguous class names."
        )


class ManagerSeedFailure(RuntimeError):
    """Raised when seeding a manager fails."""

    def __init__(
        self,
        manager_name: str,
        batch_size: int,
        error: BaseException,
        *,
        created_count: int = 0,
        remaining_count: int | None = None,
    ) -> None:
        self.manager_name = manager_name
        self.batch_size = batch_size
        self.error = error
        self.created_count = created_count
        self.remaining_count = remaining_count
        super().__init__(
            f"Failed seeding {manager_name} with batch size {batch_size}: {error}"
        )


@dataclass(frozen=True)
class SeedTarget:
    """Desired minimum row count for one selected manager."""

    manager_name: str
    count: int


@dataclass(frozen=True)
class SeedPlanRow:
    """Dry-run description for one seed target after dependency ordering."""

    manager_name: str
    target_count: int
    missing_dependencies: tuple[str, ...]


@dataclass(frozen=True)
class SeedFailure:
    """Failure captured while seeding a manager with continue-on-error enabled."""

    manager_name: str
    error: str
    created_count: int
    remaining_count: int
    batch_size: int


@dataclass(frozen=True)
class SeedExecutionResult:
    """Summary of created rows and collected failures for a seeding run."""

    created: Mapping[str, int]
    failures: tuple[SeedFailure, ...]


def parse_target_overrides(
    raw_targets: list[str] | tuple[str, ...] | None,
) -> dict[str, int]:
    """Parse repeated ``NAME=COUNT`` target override arguments.

    ``None`` and empty inputs return an empty mapping. Otherwise, each raw
    target must contain ``=``.
    Whitespace around the manager name and count is ignored. Counts must parse
    as positive integers, and each manager name may appear at most once.

    Args:
        raw_targets: Repeated raw command-line values, or ``None``.

    Returns:
        Mapping from manager name to target minimum count.

    Raises:
        InvalidSeedTargetError: If a value is not ``NAME=COUNT``, either side is
            blank, the count is not an integer, the count is below one, or the
            manager name is duplicated.
    """

    parsed: dict[str, int] = {}
    for raw in raw_targets or ():
        if "=" not in raw:
            raise InvalidSeedTargetError.invalid_format(raw)
        name, raw_count = raw.split("=", 1)
        name = name.strip()
        raw_count = raw_count.strip()
        if not name or not raw_count:
            raise InvalidSeedTargetError.invalid_format(raw)
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise InvalidSeedTargetError.invalid_integer(name) from exc
        if count < 1:
            raise InvalidSeedTargetError.not_positive(name)
        if name in parsed:
            raise InvalidSeedTargetError.duplicate(name)
        parsed[name] = count
    return parsed


def discover_seedable_managers(
    managers: Iterable[type[object]],
) -> dict[str, type[object]]:
    """Return managers whose nested ``Factory`` exposes callable ``create_batch``.

    The returned mapping is keyed by the manager class name in input order.
    Managers without a factory or without callable ``Factory.create_batch`` are
    ignored. Duplicate class names are rejected because the command-line
    selection surface accepts class-name keys.

    Raises:
        SeedableManagerCollisionError: If two different seedable managers share
            the same class name.
    """

    seedable: dict[str, type[object]] = {}
    for manager in managers:
        factory = getattr(manager, "Factory", None)
        create_batch = getattr(factory, "create_batch", None)
        if callable(create_batch):
            name = manager.__name__
            existing = seedable.get(name)
            if existing is not None and existing is not manager:
                raise SeedableManagerCollisionError.duplicate_name(
                    name,
                    existing,
                    manager,
                )
            seedable[name] = manager
    return seedable


def select_seed_targets(
    *,
    managers_by_name: dict[str, type[object]],
    selected_names: list[str] | tuple[str, ...] | None,
    include_all: bool,
    default_count: int,
    overrides: dict[str, int],
) -> list[SeedTarget]:
    """Resolve selected manager names and per-manager target counts.

    ``--all``-style selection preserves discovery order. Explicit selection
    preserves first occurrence order and deduplicates repeated manager names.
    Target overrides are checked against all known manager names before
    selection, so unknown overrides raise ``unknown_manager`` before unselected
    override checks. Known overrides must then refer to selected managers.

    Raises:
        ManagerSelectionError: If ``default_count`` is below one, no manager is
            selected without ``include_all``, a selected or overridden manager is
            unknown, or an override targets an unselected manager.
    """

    if default_count < 1:
        raise ManagerSelectionError.invalid_count()

    selected_names = list(selected_names or [])
    unknown_managers = set(overrides) - set(managers_by_name)
    if unknown_managers:
        names = ", ".join(sorted(unknown_managers))
        raise ManagerSelectionError.unknown_manager(names)

    if include_all:
        ordered_names = list(managers_by_name)
    else:
        if not selected_names:
            raise ManagerSelectionError.missing_selection()
        ordered_names = []
        for name in selected_names:
            if name not in managers_by_name:
                raise ManagerSelectionError.unknown_manager(name)
            if name not in ordered_names:
                ordered_names.append(name)

    unselected_overrides = set(overrides) - set(ordered_names)
    if unselected_overrides:
        names = ", ".join(sorted(unselected_overrides))
        raise ManagerSelectionError.unselected_overrides(names)

    return [
        SeedTarget(manager_name=name, count=overrides.get(name, default_count))
        for name in ordered_names
    ]


def order_targets_by_dependencies(
    targets: list[SeedTarget],
    managers_by_name: dict[str, type[object]],
) -> list[SeedTarget]:
    """Order seed targets so selected required relation dependencies run first.

    Duplicate target names keep their first target. Only dependencies that are
    also selected affect ordering; missing dependencies are reported by
    ``build_seed_plan`` rather than inserted automatically.
    """

    ordered_targets, _dependencies_by_manager = _order_targets_by_dependencies(
        targets,
        managers_by_name,
    )
    return ordered_targets


def _order_targets_by_dependencies(
    targets: list[SeedTarget],
    managers_by_name: dict[str, type[object]],
) -> tuple[list[SeedTarget], dict[type[object], set[type[object]]]]:
    """Order seed targets and return the dependency map used for ordering."""

    target_by_manager_name: dict[str, SeedTarget] = {}
    manager_order_by_name: dict[str, type[object]] = {}
    for target in targets:
        if target.manager_name in target_by_manager_name:
            continue
        target_by_manager_name[target.manager_name] = target
        manager_order_by_name[target.manager_name] = managers_by_name[
            target.manager_name
        ]

    target_by_manager = {
        manager: target_by_manager_name[manager_name]
        for manager_name, manager in manager_order_by_name.items()
    }
    manager_order = list(manager_order_by_name.values())
    selected_managers = set(manager_order)
    dependencies_by_manager: dict[type[object], set[type[object]]] = {}

    def selected_dependencies(manager: type[object]) -> set[type[object]]:
        if manager not in dependencies_by_manager:
            dependencies_by_manager[manager] = _required_manager_dependencies(manager)
        dependencies = dependencies_by_manager[manager]
        return {
            dependency for dependency in dependencies if dependency in selected_managers
        }

    ordered_managers = order_interfaces_by_dependency(
        manager_order,
        selected_dependencies,
    )
    return (
        [target_by_manager[manager] for manager in ordered_managers],
        dependencies_by_manager,
    )


def build_seed_plan(
    targets: list[SeedTarget],
    managers_by_name: dict[str, type[object]],
) -> list[SeedPlanRow]:
    """Build dry-run plan rows for ordered seed targets.

    Rows are returned in dependency order. ``missing_dependencies`` lists
    required non-null relation managers discovered for each target that are not
    also selected, by manager class name. The planner does not create or add
    those dependencies.
    """

    ordered_targets, dependencies_by_manager = _order_targets_by_dependencies(
        targets,
        managers_by_name,
    )
    selected = {target.manager_name for target in ordered_targets}
    return [
        SeedPlanRow(
            manager_name=target.manager_name,
            target_count=target.count,
            missing_dependencies=tuple(
                dependency.__name__
                for dependency in dependencies_by_manager[
                    managers_by_name[target.manager_name]
                ]
                if dependency.__name__ not in selected
            ),
        )
        for target in ordered_targets
    ]


def execute_seed_plan(
    *,
    targets: list[SeedTarget],
    managers_by_name: dict[str, type[object]],
    batch_size: int,
    continue_on_error: bool,
) -> SeedExecutionResult:
    """Create missing rows for each seed target.

    Targets are dependency-ordered again before execution. The current count is
    read from ``manager.all().count()`` before each ordered target. Every ordered
    target receives a ``created`` entry initialized to zero. If the existing
    count already meets the target, no factory call is made for that manager.
    Missing rows are created with ``Factory.create_batch(size)`` in batches of at
    most ``batch_size``.

    Each batch runs in its own ``transaction.atomic()`` block with no
    cross-manager atomicity, so prior manager batches and earlier batches for a
    failing manager can remain committed regardless of ``continue_on_error``.
    With ``continue_on_error=True``, the failing manager stops after its first
    failed batch, later managers continue, and partial progress is reported in
    the returned ``SeedExecutionResult``. The ``created`` result is a
    ``MappingProxyType`` and ``failures`` is a tuple.

    Raises:
        ManagerSelectionError: If ``batch_size`` is below one.
        ManagerSeedFailure: If a batch raises and ``continue_on_error`` is
            false. The exception stores manager name, failed batch size,
            original error, rows created before failure, and remaining count.
    """

    if batch_size < 1:
        raise ManagerSelectionError.invalid_batch_size()

    ordered_targets = order_targets_by_dependencies(targets, managers_by_name)
    created: dict[str, int] = {}
    failures: list[SeedFailure] = []

    for target in ordered_targets:
        manager = cast(SeedableManagerRuntime, managers_by_name[target.manager_name])
        existing = int(manager.all().count())
        remaining = max(target.count - existing, 0)
        created[target.manager_name] = 0
        while remaining > 0:
            size = min(batch_size, remaining)
            try:
                with transaction.atomic():
                    manager.Factory.create_batch(size)
            except Exception as exc:
                failure = ManagerSeedFailure(
                    target.manager_name,
                    size,
                    exc,
                    created_count=created[target.manager_name],
                    remaining_count=remaining,
                )
                if not continue_on_error:
                    raise failure from exc
                failures.append(
                    SeedFailure(
                        manager_name=target.manager_name,
                        error=str(exc),
                        created_count=created[target.manager_name],
                        remaining_count=remaining,
                        batch_size=size,
                    )
                )
                break
            created[target.manager_name] += size
            remaining -= size

    return SeedExecutionResult(
        created=MappingProxyType(dict(created)),
        failures=tuple(failures),
    )


def _required_manager_dependencies(manager: type[object]) -> set[type[object]]:
    """Return managers required by non-null FK/O2O relations on manager's model.

    Dependencies are discovered from ``manager.Interface._model._meta.get_fields()``.
    Non-null ``ForeignKey`` and ``OneToOneField`` relations contribute
    ``field.remote_field.model._general_manager_class`` when that value is a
    manager class different from ``manager``. Nullable relations, self
    relations, non-relation fields, and managers without model metadata are
    ignored.
    """

    model = getattr(getattr(manager, "Interface", None), "_model", None)
    opts = getattr(model, "_meta", None)
    if opts is None:
        return set()

    dependencies: set[type[object]] = set()
    for field in opts.get_fields():
        if not isinstance(field, (models.ForeignKey, models.OneToOneField)):
            continue
        if getattr(field, "null", False):
            continue
        related_model = getattr(field.remote_field, "model", None)
        related_manager = getattr(related_model, "_general_manager_class", None)
        if isinstance(related_manager, type) and related_manager is not manager:
            dependencies.add(related_manager)

    return dependencies
