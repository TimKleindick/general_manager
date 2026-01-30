from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase

from maintenance.managers import Module, ModuleSpec, Ship, WorkOrder
from general_manager.utils.testing import run_registered_startup_hooks


class RuleValidationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        run_registered_startup_hooks(managers=[ModuleSpec])
        spec = ModuleSpec.all().first()
        if spec is None:
            raise AssertionError("ModuleSpec data missing")
        ship = Ship.create(
            ignore_permission=True,
            name="Test Runner",
            registry="ORL-TST",
            ship_class="Test Frame",
            status="active",
        )
        self.module = Module.create(
            ignore_permission=True,
            name="Habitat Ring Alpha",
            ship=ship,
            spec=spec,
            status="operational",
            life_support_uptime=92.0,
            oxygen_reserve="120 kg",
            battery_capacity="420 kWh",
            hazard_limit=spec.hazard_limit,
            notes="Test module",
        )

    def test_work_order_due_by_rule(self) -> None:
        with self.assertRaises(ValidationError):
            WorkOrder.create(
                ignore_permission=True,
                title="Invalid schedule",
                module=self.module,
                assigned_to=None,
                severity=4,
                status="open",
                opened_on=date(2222, 8, 10),
                due_by=date(2222, 8, 1),
                requires_eva=False,
            )
