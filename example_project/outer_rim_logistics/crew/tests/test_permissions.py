from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from crew.managers import CrewMember, JobRoleCatalog
from maintenance.managers import Ship, ShipClassCatalog, ShipStatusCatalog
from general_manager.permission.base_permission import PermissionCheckError
from general_manager.utils.testing import run_registered_startup_hooks


class CrewPermissionTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        run_registered_startup_hooks(
            managers=[JobRoleCatalog, ShipClassCatalog, ShipStatusCatalog]
        )
        self.role = JobRoleCatalog.filter(code="DM").first()
        if self.role is None:
            raise AssertionError("JobRoleCatalog data missing for DM")
        ship_class = ShipClassCatalog.filter(code="CC").first()
        ship_status = ShipStatusCatalog.filter(code="active").first()
        if ship_class is None or ship_status is None:
            raise AssertionError("Ship catalogs missing")
        self.ship = Ship.create(
            ignore_permission=True,
            name="Test Ship",
            registry="ORL-TST",
            ship_class=ship_class,
            status=ship_status,
        )

    def test_commander_can_create(self) -> None:
        user = get_user_model().objects.create_user(
            username="commander", password="testpass123"
        )
        user.role = "Commander"
        user.clearance_level = 5
        data = {
            "name": "Kara Synd",
            "rank": "Commander",
            "role": self.role,
            "clearance_level": 5,
            "ship": self.ship,
            "assigned_module": None,
            "on_duty": True,
            "medical_hold": False,
            "last_medical_check": date(2222, 8, 12),
            "fatigue_index": 0.1,
        }
        CrewMember.Permission.check_create_permission(data, CrewMember, user)

    def test_unprivileged_user_denied(self) -> None:
        user = get_user_model().objects.create_user(
            username="technician", password="testpass123"
        )
        user.role = "Technician"
        user.clearance_level = 1
        data = {
            "name": "Nico Renn",
            "rank": "Specialist",
            "role": self.role,
            "clearance_level": 2,
            "ship": self.ship,
            "assigned_module": None,
            "on_duty": True,
            "medical_hold": False,
            "last_medical_check": date(2222, 8, 12),
            "fatigue_index": 0.2,
        }
        with self.assertRaises(PermissionCheckError):
            CrewMember.Permission.check_create_permission(data, CrewMember, user)
