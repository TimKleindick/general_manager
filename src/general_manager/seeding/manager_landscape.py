"""Dependency-aware manager seeding helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from django.db import models, transaction

from general_manager.interface.infrastructure.startup_hooks import (
    order_interfaces_by_dependency,
)


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


class ManagerSeedFailure(RuntimeError):
    """Raised when seeding a manager fails."""

    def __init__(
        self, manager_name: str, batch_size: int, error: BaseException
    ) -> None:
        self.manager_name = manager_name
        self.batch_size = batch_size
        self.error = error
        super().__init__(
            f"Failed seeding {manager_name} with batch size {batch_size}: {error}"
        )


@dataclass(frozen=True)
class SeedTarget:
    """Desired minimum row count for a manager."""

    manager_name: str
    count: int


@dataclass(frozen=True)
class SeedPlanRow:
    """Dry-run description for one seed target."""

    manager_name: str
    target_count: int
    missing_dependencies: list[str]


@dataclass(frozen=True)
class SeedFailure:
    """Failure captured while seeding a manager."""

    manager_name: str
    error: str


@dataclass(frozen=True)
class SeedExecutionResult:
    """Summary of a seeding run."""

    created: dict[str, int]
    failures: list[SeedFailure]


def parse_target_overrides(
    raw_targets: list[str] | tuple[str, ...] | None,
) -> dict[str, int]:
    """Parse repeated NAME=COUNT command arguments into a mapping."""

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
        parsed[name] = count
    return parsed


def discover_seedable_managers(managers: Iterable[type[Any]]) -> dict[str, type[Any]]:
    """Return managers that expose a factory with create_batch."""

    seedable: dict[str, type[Any]] = {}
    for manager in managers:
        factory = getattr(manager, "Factory", None)
        create_batch = getattr(factory, "create_batch", None)
        if callable(create_batch):
            seedable[manager.__name__] = manager
    return seedable


def select_seed_targets(
    *,
    managers_by_name: dict[str, type[Any]],
    selected_names: list[str] | tuple[str, ...] | None,
    include_all: bool,
    default_count: int,
    overrides: dict[str, int],
) -> list[SeedTarget]:
    """Resolve selected managers and desired target counts."""

    if default_count < 1:
        raise ManagerSelectionError.invalid_count()

    selected_names = list(selected_names or [])
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
    managers_by_name: dict[str, type[Any]],
) -> list[SeedTarget]:
    """Order seed targets so selected required relation dependencies run first."""

    target_by_manager = {
        managers_by_name[target.manager_name]: target for target in targets
    }
    manager_order = [managers_by_name[target.manager_name] for target in targets]
    selected_managers = set(manager_order)
    ordered_managers = order_interfaces_by_dependency(
        manager_order,
        lambda manager: {
            dependency
            for dependency in _required_manager_dependencies(manager)
            if dependency in selected_managers
        },
    )
    return [target_by_manager[manager] for manager in ordered_managers]


def build_seed_plan(
    targets: list[SeedTarget],
    managers_by_name: dict[str, type[Any]],
) -> list[SeedPlanRow]:
    """Build dry-run plan rows for ordered seed targets."""

    ordered_targets = order_targets_by_dependencies(targets, managers_by_name)
    selected = {target.manager_name for target in ordered_targets}
    return [
        SeedPlanRow(
            manager_name=target.manager_name,
            target_count=target.count,
            missing_dependencies=[
                dependency.__name__
                for dependency in _required_manager_dependencies(
                    managers_by_name[target.manager_name]
                )
                if dependency.__name__ not in selected
            ],
        )
        for target in ordered_targets
    ]


def execute_seed_plan(
    *,
    targets: list[SeedTarget],
    managers_by_name: dict[str, type[Any]],
    batch_size: int,
    continue_on_error: bool,
) -> SeedExecutionResult:
    """Create missing rows for each seed target."""

    if batch_size < 1:
        raise ManagerSelectionError.invalid_batch_size()

    ordered_targets = order_targets_by_dependencies(targets, managers_by_name)
    created: dict[str, int] = {}
    failures: list[SeedFailure] = []

    for target in ordered_targets:
        manager = managers_by_name[target.manager_name]
        existing = int(manager.all().count())
        remaining = max(target.count - existing, 0)
        created[target.manager_name] = 0
        while remaining > 0:
            size = min(batch_size, remaining)
            try:
                with transaction.atomic():
                    manager.Factory.create_batch(size)
            except Exception as exc:
                failure = ManagerSeedFailure(target.manager_name, size, exc)
                if not continue_on_error:
                    raise failure from exc
                failures.append(
                    SeedFailure(manager_name=target.manager_name, error=str(exc))
                )
                break
            created[target.manager_name] += size
            remaining -= size

    return SeedExecutionResult(created=created, failures=failures)


def _required_manager_dependencies(manager: type[Any]) -> set[type[Any]]:
    """Return managers required by non-null FK/O2O relations on manager's model."""

    model = getattr(getattr(manager, "Interface", None), "_model", None)
    opts = getattr(model, "_meta", None)
    if opts is None:
        return set()

    dependencies: set[type[Any]] = set()
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
