from __future__ import annotations

from django.core.management import call_command

from general_manager.utils.testing import GeneralManagerTransactionTestCase

from crew.managers import CrewMember, JobRoleCatalog
from maintenance.managers import (
    IncidentReport,
    Module,
    ModuleSpec,
    Ship,
    ShipClassCatalog,
    ShipStatusCatalog,
    WorkOrder,
)
from mission.managers import MissionSchedule
from supply.managers import (
    CargoManifest,
    HazardClass,
    InventoryItem,
    PartCatalog,
    VendorCatalog,
)


class TestORLBulkSeed(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.general_manager_classes = [
            HazardClass,
            VendorCatalog,
            PartCatalog,
            JobRoleCatalog,
            ModuleSpec,
            ShipClassCatalog,
            ShipStatusCatalog,
            Ship,
            Module,
            CrewMember,
            InventoryItem,
            CargoManifest,
            WorkOrder,
            IncidentReport,
            MissionSchedule,
        ]

    def test_bulk_seed_command_creates_targets(self) -> None:
        call_command(
            "bulk_seed_outer_rim",
            ships=2,
            modules=4,
            crew=5,
            inventory=3,
            manifests=2,
            work_orders=2,
            incidents=2,
            schedules=1,
            batch_size=2,
        )

        assert Ship.all().count() >= 2
        assert Module.all().count() >= 4
        assert CrewMember.all().count() >= 5
        assert InventoryItem.all().count() >= 3
        assert CargoManifest.all().count() >= 2
        assert WorkOrder.all().count() >= 2
        assert IncidentReport.all().count() >= 2
        assert MissionSchedule.all().count() >= 1
