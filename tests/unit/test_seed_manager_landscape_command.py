from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import models
from django.test import SimpleTestCase


class _ManagerInput:
    """Tiny input stub mirroring manager-typed Input fields."""

    def __init__(self, manager_type: type):
        self.is_manager = True
        self.type = manager_type


def _mock_manager(
    name: str,
    interface_type: str = "database",
    side_effect: object | None = None,
    deps: list[type] | None = None,
    model: type[models.Model] | None = None,
) -> type:
    """Create a manager-like class mock compatible with seed command filtering."""
    input_fields = {
        f"dep_{index}": _ManagerInput(dep_cls)
        for index, dep_cls in enumerate(deps or [])
    }
    interface_attrs = {"_interface_type": interface_type, "input_fields": input_fields}
    if model is not None:
        interface_attrs["model"] = model
    interface = type("Interface", (), interface_attrs)
    factory = type("Factory", (), {})()
    create_batch = MagicMock()
    if side_effect is not None:
        create_batch.side_effect = side_effect
    factory.create_batch = create_batch
    return type(name, (), {"Interface": interface, "Factory": factory})


class _DepModel(models.Model):
    class Meta:
        app_label = "general_manager"


class _MainModel(models.Model):
    dep_fk = models.ForeignKey(_DepModel, on_delete=models.CASCADE)
    dep_o2o = models.OneToOneField(
        _DepModel, on_delete=models.CASCADE, related_name="main_o2o"
    )
    dep_m2m = models.ManyToManyField(_DepModel)

    class Meta:
        app_label = "general_manager"


class SeedManagerLandscapeCommandTests(SimpleTestCase):
    def test_seeds_only_writable_seedable_managers(self):
        writable = _mock_manager("WritableManager", "database")
        readonly = _mock_manager("ReadOnlyManager", "readonly")
        no_factory = type(
            "NoFactoryManager",
            (),
            {"Interface": type("Interface", (), {"_interface_type": "database"})},
        )
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [writable, readonly, no_factory],
        ):
            stdout = StringIO()
            call_command("seed_manager_landscape", "--count", "2", stdout=stdout)

            writable.Factory.create_batch.assert_called_once_with(2)
            self.assertIn("Seeding complete", stdout.getvalue())
            self.assertNotIn("ReadOnlyManager", stdout.getvalue())

    def test_filters_manager_names(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "existing")
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b],
        ):
            call_command(
                "seed_manager_landscape", "--manager", "ManagerB", "--count", "1"
            )

            manager_a.Factory.create_batch.assert_not_called()
            manager_b.Factory.create_batch.assert_called_once_with(1)

    def test_filters_manager_names_space_separated(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "database")
        manager_c = _mock_manager("ManagerC", "database")
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b, manager_c],
        ):
            call_command(
                "seed_manager_landscape",
                "--manager",
                "ManagerA",
                "ManagerC",
                "--count",
                "1",
            )

            manager_a.Factory.create_batch.assert_called_once_with(1)
            manager_b.Factory.create_batch.assert_not_called()
            manager_c.Factory.create_batch.assert_called_once_with(1)

    def test_count_overrides_per_manager(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "database")
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b],
        ):
            call_command(
                "seed_manager_landscape",
                "--count",
                "50",
                "ManagerA=20",
                "ManagerB=200",
            )

            manager_a.Factory.create_batch.assert_called_once_with(20)
            manager_b.Factory.create_batch.assert_called_once_with(200)

    def test_count_overrides_with_global_fallback(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "database")
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b],
        ):
            call_command(
                "seed_manager_landscape",
                "--count",
                "50",
                "ManagerA=20",
            )

            manager_a.Factory.create_batch.assert_called_once_with(20)
            manager_b.Factory.create_batch.assert_called_once_with(50)

    def test_unknown_manager_in_count_override_raises(self):
        manager_a = _mock_manager("ManagerA", "database")
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a],
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("seed_manager_landscape", "--count", "ManagerX=3")

        self.assertIn("Unknown manager", str(ctx.exception))

    def test_ignores_count_overrides_for_unselected_managers_with_warning(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "database")
        stderr = StringIO()
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b],
        ):
            call_command(
                "seed_manager_landscape",
                "--manager",
                "ManagerA",
                "--count",
                "ManagerA=50",
                "ManagerB=200",
                stderr=stderr,
            )

        manager_a.Factory.create_batch.assert_called_once_with(50)
        manager_b.Factory.create_batch.assert_not_called()
        self.assertIn(
            "Ignored count overrides for unselected managers: ManagerB",
            stderr.getvalue(),
        )

    def test_dry_run_prints_resolved_seed_plan(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "database")
        stdout = StringIO()
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b],
        ):
            call_command(
                "seed_manager_landscape",
                "--dry-run",
                "--count",
                "50",
                "ManagerA=20",
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertIn("Dry-run: would seed 2 manager(s).", output)
        self.assertIn("- ManagerA: count=20", output)
        self.assertIn("- ManagerB: count=50", output)
        manager_a.Factory.create_batch.assert_not_called()
        manager_b.Factory.create_batch.assert_not_called()

    def test_cycle_warning_is_emitted(self):
        manager_a = _mock_manager("ManagerA", "database")
        manager_b = _mock_manager("ManagerB", "database")
        manager_a.Interface.input_fields = {"dep": _ManagerInput(manager_b)}
        manager_b.Interface.input_fields = {"dep": _ManagerInput(manager_a)}
        stderr = StringIO()

        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager_a, manager_b],
        ):
            call_command("seed_manager_landscape", "--count", "1", stderr=stderr)

        self.assertIn("Dependency cycle detected", stderr.getvalue())

    def test_unique_conflicts_are_retried_then_skipped(self):
        unique_error = ValidationError(
            {"__all__": ["Item with this key already exists."]}
        )
        conflict_manager = _mock_manager(
            "ConflictManager",
            side_effect=[unique_error, unique_error, unique_error],
        )
        healthy_manager = _mock_manager("HealthyManager")
        stdout = StringIO()
        stderr = StringIO()
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [conflict_manager, healthy_manager],
        ):
            call_command(
                "seed_manager_landscape",
                "--count",
                "1",
                "--retries",
                "2",
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(conflict_manager.Factory.create_batch.call_count, 3)
        healthy_manager.Factory.create_batch.assert_called_once_with(1)
        self.assertIn("[skip-unique] ConflictManager", stderr.getvalue())
        self.assertIn(
            "Skipped due to unique conflicts: 1 manager run(s).", stdout.getvalue()
        )

    def test_retries_until_dependencies_resolve(self):
        transient_error = RuntimeError("waiting for dependency")
        manager = _mock_manager(
            "RetryManager",
            side_effect=[transient_error, None],
        )
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager],
        ):
            call_command("seed_manager_landscape", "--count", "1", "--retries", "2")

            self.assertEqual(manager.Factory.create_batch.call_count, 2)

    def test_raises_when_unresolved_after_all_passes(self):
        manager = _mock_manager(
            "AlwaysFailManager",
            side_effect=RuntimeError("hard failure"),
        )
        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [manager],
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("seed_manager_landscape", "--count", "1", "--retries", "2")

        self.assertIn("AlwaysFailManager", str(ctx.exception))
        self.assertIn("failed after 3 attempt(s)", str(ctx.exception))

    def test_orders_by_discovered_manager_dependencies(self):
        call_order: list[str] = []

        dep = _mock_manager("DependencyManager", "database", model=_DepModel)
        main = _mock_manager("MainManager", "database", model=_MainModel)
        _DepModel._general_manager_class = dep  # type: ignore[attr-defined]
        dep.Factory.create_batch.side_effect = lambda _count: call_order.append(
            "DependencyManager"
        )
        main.Factory.create_batch.side_effect = lambda _count: call_order.append(
            "MainManager"
        )

        with patch(
            "general_manager.management.commands.seed_manager_landscape.GeneralManagerMeta.all_classes",
            [main, dep],
        ):
            call_command("seed_manager_landscape", "--count", "1")

        self.assertEqual(call_order, ["DependencyManager", "MainManager"])
