"""Seed database records for registered GeneralManager classes via their factories."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from graphlib import TopologicalSorter, CycleError
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, models

from general_manager.manager.meta import GeneralManagerMeta


class Command(BaseCommand):
    help = "Populate the database for create-capable managers using their Factory."

    def add_arguments(self, parser) -> None:  # type: ignore[override]
        """
        Add CLI arguments controlling manager selection and batch generation.

        Parameters:
            parser: Django command parser.
        """
        parser.add_argument(
            "--count",
            nargs="+",
            default=["10"],
            help=(
                "Global count and optional per-manager overrides. "
                "Examples: --count 50, --count Project=20 Derivative=200, "
                "--count 50 Project=20."
            ),
        )
        parser.add_argument(
            "--manager",
            nargs="+",
            dest="managers",
            help="Manager class names to seed (space-separated).",
        )
        parser.add_argument(
            "--retries",
            type=int,
            default=2,
            help="Retries per manager class after initial attempt (default: 2).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which managers would be seeded without creating rows.",
        )
        parser.add_argument(
            "--fail-fast",
            action="store_true",
            help="Abort immediately on first seeding error.",
        )

    def handle(self, *_: Any, **options: Any) -> None:
        """
        Seed data for all selected managers that expose a callable factory.

        Parameters:
            options: Parsed command options from Django's management command parser.

        Raises:
            CommandError: If input arguments are invalid or if unresolved managers
                remain after all retry passes.
        """
        raw_count_values = list(options["count"] or ["10"])
        retries = int(options["retries"])
        dry_run = bool(options["dry_run"])
        fail_fast = bool(options["fail_fast"])
        manager_filters = self._parse_manager_values(options.get("managers"))

        if retries < 0:
            raise CommandError("--retries must be >= 0.")  # noqa: TRY003

        all_candidates = self._iter_seedable_manager_classes()
        if manager_filters:
            selected = [m for m in all_candidates if m.__name__ in manager_filters]
            unknown = sorted(
                manager_name
                for manager_name in manager_filters
                if manager_name not in {m.__name__ for m in all_candidates}
            )
            if unknown:
                self.stderr.write(
                    f"Unknown or non-seedable managers ignored: {', '.join(unknown)}"
                )
        else:
            selected = all_candidates

        global_count, count_overrides = self._parse_count_values(
            raw_count_values, {manager_cls.__name__ for manager_cls in all_candidates}
        )
        selected_names = {manager_cls.__name__ for manager_cls in selected}
        ignored_overrides = sorted(
            manager_name
            for manager_name in count_overrides
            if manager_name not in selected_names
        )
        if ignored_overrides:
            self.stderr.write(
                "Ignored count overrides for unselected managers: "
                + ", ".join(ignored_overrides)
            )

        if not selected:
            self.stdout.write("No seedable managers selected.")
            return

        selected_by_name = {
            manager_cls.__name__: manager_cls for manager_cls in selected
        }
        model_to_manager_name = self._build_model_to_manager_name_map(selected)
        ordered, cycle = self._order_managers_by_dependencies(selected)
        created_counts: dict[str, int] = OrderedDict()
        skipped_unique_counts: dict[str, int] = OrderedDict()
        failures: list[SeedFailure] = []

        if cycle is not None:
            if cycle:
                self.stderr.write(
                    "Dependency cycle detected; falling back to deterministic name order. "
                    f"Cycle path: {' -> '.join(cycle)}"
                )
            else:
                self.stderr.write(
                    "Dependency cycle detected; falling back to deterministic name order."
                )

        if dry_run:
            self.stdout.write(f"Dry-run: would seed {len(selected)} manager(s).")
            self.stdout.write("Seed order: " + ", ".join(ordered))
            for manager_name in ordered:
                manager_count = count_overrides.get(manager_name, global_count)
                self.stdout.write(f"- {manager_name}: count={manager_count}")
            return

        self.stdout.write("Seed order: " + ", ".join(ordered))

        for manager_name in ordered:
            manager_cls = selected_by_name[manager_name]
            dependencies = self._manager_dependencies(
                manager_cls, selected_by_name, model_to_manager_name
            )
            max_attempts = retries + 1
            last_error: Exception | None = None
            seeded = False
            skipped_due_unique_conflict = False
            for attempt in range(1, max_attempts + 1):
                try:
                    manager_count = count_overrides.get(manager_name, global_count)
                    manager_cls.Factory.create_batch(manager_count)
                    seeded = True
                    created_counts[manager_name] = (
                        created_counts.get(manager_name, 0) + manager_count
                    )
                    self.stdout.write(f"[ok] {manager_name}: +{manager_count}")
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if self._is_unique_conflict(exc):
                        self.stderr.write(
                            "[retry-unique] "
                            f"{manager_name} attempt {attempt}/{max_attempts}: "
                            f"{exc.__class__.__name__}: {exc}"
                        )
                        if attempt >= max_attempts:
                            skipped_due_unique_conflict = True
                            skipped_unique_counts[manager_name] = (
                                skipped_unique_counts.get(manager_name, 0) + 1
                            )
                            self.stderr.write(
                                "[skip-unique] "
                                f"{manager_name}: unique conflicts persisted after "
                                f"{max_attempts} attempt(s); skipping."
                            )
                            break
                        continue
                    self.stderr.write(
                        "[retry] "
                        f"{manager_name} attempt {attempt}/{max_attempts}: "
                        f"{exc.__class__.__name__}: {exc}"
                    )
                    continue

            if seeded or skipped_due_unique_conflict:
                continue
            if last_error is not None:
                assert last_error is not None
                failure = SeedFailure(
                    manager_name=manager_name,
                    attempts=max_attempts,
                    dependencies=dependencies,
                    error_type=last_error.__class__.__name__,
                    error_message=str(last_error),
                )
                failures.append(failure)
                if fail_fast:
                    raise CommandError(self._format_failures([failure]))

        if failures:
            raise CommandError(
                "Unable to seed all managers.\n" + self._format_failures(failures)
            )

        total_created = sum(created_counts.values())
        total_skipped_unique = sum(skipped_unique_counts.values())
        self.stdout.write(
            f"Seeding complete: {len(created_counts)} manager(s), {total_created} object(s)."
        )
        if total_skipped_unique:
            self.stdout.write(
                "Skipped due to unique conflicts: "
                f"{total_skipped_unique} manager run(s)."
            )

    @staticmethod
    def _iter_seedable_manager_classes() -> list[type[Any]]:
        """
        Return registered manager classes that can be seeded with Factory.create_batch.

        Returns:
            list[type[Any]]: Sorted list of manager classes that are writable
                (database/existing interfaces) and expose a callable Factory.create_batch.
        """
        seedable: list[type[Any]] = []
        for manager_cls in GeneralManagerMeta.all_classes:
            interface = getattr(manager_cls, "Interface", None)
            interface_type = getattr(interface, "_interface_type", None)
            if interface_type not in {"database", "existing"}:
                continue
            factory_cls = getattr(manager_cls, "Factory", None)
            create_batch = getattr(factory_cls, "create_batch", None)
            if not callable(create_batch):
                continue
            seedable.append(manager_cls)
        return sorted(seedable, key=lambda manager_cls: manager_cls.__name__)

    @classmethod
    def _manager_dependencies(
        cls,
        manager_cls: type[Any],
        selected_by_name: dict[str, type[Any]],
        model_to_manager_name: dict[type[models.Model], str],
    ) -> tuple[str, ...]:
        """
        Return selected manager names referenced by ``manager_cls``.

        Parameters:
            manager_cls (type[Any]): Manager class whose inputs are inspected.
            selected_by_name (dict[str, type[Any]]): Name -> manager class for currently
                selected managers.
            model_to_manager_name (dict[type[models.Model], str]): Map from model
                class to selected manager name.

        Returns:
            tuple[str, ...]: Sorted dependency names, limited to selected managers.
        """
        dependencies = set(
            cls._dependencies_from_input_fields(manager_cls, selected_by_name)
        )
        dependencies.update(
            cls._dependencies_from_model_relations(
                manager_cls, selected_by_name, model_to_manager_name
            )
        )
        return tuple(sorted(dependencies))

    @classmethod
    def _order_managers_by_dependencies(
        cls, selected: list[type[Any]]
    ) -> tuple[list[str], tuple[str, ...] | None]:
        """
        Build a dependency-first manager order from manager input relationships.

        Parameters:
            selected (list[type[Any]]): Manager classes selected for seeding.

        Returns:
            tuple[list[str], tuple[str, ...] | None]:
                Ordered manager names and optional cycle path.
        """
        selected_by_name = {
            manager_cls.__name__: manager_cls for manager_cls in selected
        }
        model_to_manager_name = cls._build_model_to_manager_name_map(selected)
        graph = {
            manager_name: set(
                cls._manager_dependencies(
                    selected_by_name[manager_name],
                    selected_by_name,
                    model_to_manager_name,
                )
            )
            for manager_name in sorted(selected_by_name)
        }
        try:
            sorter = TopologicalSorter(graph)
            return list(sorter.static_order()), None
        except CycleError as exc:
            # Fall back deterministically; failures will include dependency context.
            cycle: tuple[str, ...] | None = None
            if len(exc.args) > 1 and isinstance(exc.args[1], tuple):
                cycle = exc.args[1]
            if cycle is None:
                cycle = ()
            return sorted(selected_by_name), cycle

    @staticmethod
    def _dependencies_from_input_fields(
        manager_cls: type[Any], selected_by_name: dict[str, type[Any]]
    ) -> set[str]:
        """
        Collect dependencies from manager-typed ``Interface.input_fields``.
        """
        interface = getattr(manager_cls, "Interface", None)
        input_fields = getattr(interface, "input_fields", {}) or {}
        dependencies: set[str] = set()
        for input_field in input_fields.values():
            if not getattr(input_field, "is_manager", False):
                continue
            candidate_cls = getattr(input_field, "type", None)
            if not isinstance(candidate_cls, type):
                continue
            name = candidate_cls.__name__
            if name in selected_by_name:
                dependencies.add(name)
        return dependencies

    @staticmethod
    def _dependencies_from_model_relations(
        manager_cls: type[Any],
        selected_by_name: dict[str, type[Any]],
        model_to_manager_name: dict[type[models.Model], str],
    ) -> set[str]:
        """
        Collect dependencies from forward Django relations (FK/O2O/M2M).
        """
        model_cls = Command._get_manager_model(manager_cls)
        if model_cls is None:
            return set()

        dependencies: set[str] = set()
        for field in model_cls._meta.get_fields():
            is_forward_relation = isinstance(
                field, (models.ForeignKey, models.OneToOneField, models.ManyToManyField)
            ) and not getattr(field, "auto_created", False)
            if not is_forward_relation:
                continue
            related_model = getattr(field, "related_model", None)
            if not isinstance(related_model, type) or not issubclass(
                related_model, models.Model
            ):
                continue

            related_manager = getattr(related_model, "_general_manager_class", None)
            if isinstance(related_manager, type):
                related_name = related_manager.__name__
                if related_name in selected_by_name:
                    dependencies.add(related_name)
                    continue

            mapped_name = model_to_manager_name.get(related_model)
            if mapped_name and mapped_name in selected_by_name:
                dependencies.add(mapped_name)

        return dependencies

    @staticmethod
    def _get_manager_model(manager_cls: type[Any]) -> type[models.Model] | None:
        """
        Resolve the Django model bound to a manager, when available.
        """
        interface = getattr(manager_cls, "Interface", None)
        model_cls = getattr(interface, "model", None)
        if isinstance(model_cls, type) and issubclass(model_cls, models.Model):
            return model_cls
        factory_cls = getattr(manager_cls, "Factory", None)
        factory_meta = getattr(factory_cls, "_meta", None)
        model_cls = getattr(factory_meta, "model", None)
        if isinstance(model_cls, type) and issubclass(model_cls, models.Model):
            return model_cls
        return None

    @classmethod
    def _build_model_to_manager_name_map(
        cls, selected: list[type[Any]]
    ) -> dict[type[models.Model], str]:
        """
        Build a model -> manager name map for selected managers.
        """
        model_map: dict[type[models.Model], str] = {}
        for manager_cls in selected:
            model_cls = cls._get_manager_model(manager_cls)
            if model_cls is not None:
                model_map[model_cls] = manager_cls.__name__
        return model_map

    @staticmethod
    def _format_failures(failures: list["SeedFailure"]) -> str:
        """
        Format manager seeding failures into a compact, actionable message.

        Parameters:
            failures (list[SeedFailure]): Failures collected during seeding.

        Returns:
            str: Human-readable failure summary lines.
        """
        lines = []
        for failure in failures:
            deps = ", ".join(failure.dependencies) if failure.dependencies else "none"
            lines.append(
                f"- {failure.manager_name}: failed after {failure.attempts} attempt(s); "
                f"depends_on=[{deps}]; last_error={failure.error_type}: "
                f"{failure.error_message}"
            )
        return "\n".join(lines)

    @staticmethod
    def _is_unique_conflict(exc: Exception) -> bool:
        """
        Return True when an exception likely represents a uniqueness conflict.

        Handles both database-level uniqueness errors and Django validation
        errors raised before save.
        """
        if isinstance(exc, IntegrityError):
            message = str(exc).lower()
            return "unique" in message or "duplicate" in message
        if isinstance(exc, ValidationError):
            message = str(exc).lower()
            return "already exists" in message or "unique" in message
        return False

    @staticmethod
    def _parse_count_values(
        raw_values: list[str], known_manager_names: set[str]
    ) -> tuple[int, dict[str, int]]:
        """
        Parse ``--count`` tokens into a global count and per-manager overrides.

        Accepted token forms:
        - ``N`` (global fallback count)
        - ``ManagerName=N`` (per-manager override)

        Parameters:
            raw_values (list[str]): Raw values received from argparse.
            known_manager_names (set[str]): Valid manager names for override keys.

        Returns:
            tuple[int, dict[str, int]]: Global count and manager-specific counts.

        Raises:
            CommandError: If tokens are invalid, counts are < 1, or unknown manager
                names are used.
        """
        global_count = 10
        explicit_global_seen = False
        overrides: dict[str, int] = {}

        for token in raw_values:
            if "=" not in token:
                if explicit_global_seen:
                    raise CommandError(  # noqa: TRY003
                        "Only one global count value is allowed in --count."
                    )
                try:
                    parsed = int(token)
                except ValueError as exc:
                    raise CommandError(  # noqa: TRY003
                        f"Invalid global --count value '{token}'. Expected integer."
                    ) from exc
                if parsed < 1:
                    raise CommandError("--count must be >= 1.")  # noqa: TRY003
                global_count = parsed
                explicit_global_seen = True
                continue

            manager_name, raw_count = token.split("=", 1)
            manager_name = manager_name.strip()
            raw_count = raw_count.strip()
            if not manager_name:
                raise CommandError(  # noqa: TRY003
                    f"Invalid --count override '{token}'. Missing manager name."
                )
            if manager_name not in known_manager_names:
                raise CommandError(  # noqa: TRY003
                    f"Unknown manager in --count override: '{manager_name}'."
                )
            try:
                parsed_override = int(raw_count)
            except ValueError as exc:
                raise CommandError(  # noqa: TRY003
                    f"Invalid --count override '{token}'. Expected Manager=INTEGER."
                ) from exc
            if parsed_override < 1:
                raise CommandError("--count must be >= 1.")  # noqa: TRY003
            overrides[manager_name] = parsed_override

        return global_count, overrides

    @staticmethod
    def _parse_manager_values(raw_values: Any) -> set[str]:
        """
        Parse ``--manager`` values from argparse into a flat set.

        Supports both:
        - ``--manager A B`` (nargs)
        - legacy repeated style from tests/callers, if passed as nested values.
        """
        if not raw_values:
            return set()
        manager_names: set[str] = set()
        for value in raw_values:
            if isinstance(value, str):
                manager_names.add(value)
                continue
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    if isinstance(item, str) and item:
                        manager_names.add(item)
        return manager_names


@dataclass(frozen=True)
class SeedFailure:
    """Failure metadata collected for one manager class."""

    manager_name: str
    attempts: int
    dependencies: tuple[str, ...]
    error_type: str
    error_message: str
