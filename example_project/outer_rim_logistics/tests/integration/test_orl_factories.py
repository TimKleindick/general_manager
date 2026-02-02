from __future__ import annotations

from general_manager.interface.capabilities.read_only.management import (
    ReadOnlyManagementCapability,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase

from outer_rim_logistics.crew.managers import CrewMember, JobRoleCatalog
from outer_rim_logistics.maintenance.managers import (
    IncidentReport,
    Module,
    ModuleSpec,
    Ship,
    ShipClassCatalog,
    ShipStatusCatalog,
    WorkOrder,
)
from outer_rim_logistics.mission.managers import (
    CrewFatigue,
    CrewReadiness,
    InventoryHealth,
    MissionReadiness,
    MissionSchedule,
    ModuleHealth,
    OxygenBurnRate,
    ResupplyWindowRisk,
    ScheduleFeasibility,
    ShipCrewLoad,
    ShipInventoryCoverage,
    ShipMaintenanceBacklog,
    ShipOxygenReserve,
    ShipReadiness,
)
from outer_rim_logistics.supply.managers import (
    CargoManifest,
    HazardClass,
    InventoryItem,
    PartCatalog,
    VendorCatalog,
)


class TestORLFactories(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
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
            CrewReadiness,
            InventoryHealth,
            ModuleHealth,
            ScheduleFeasibility,
            MissionReadiness,
            CrewFatigue,
            OxygenBurnRate,
            ResupplyWindowRisk,
            ShipCrewLoad,
            ShipInventoryCoverage,
            ShipMaintenanceBacklog,
            ShipOxygenReserve,
            ShipReadiness,
        ]
        cls._catalogs_synced = False

    def setUp(self) -> None:
        super().setUp()
        self._sync_catalogs()
        self._ensure_base_objects()

    def _sync_catalogs(self) -> None:
        if self.__class__._catalogs_synced:
            return
        capability = ReadOnlyManagementCapability()
        capability.sync_data(HazardClass.Interface)
        capability.sync_data(VendorCatalog.Interface)
        capability.sync_data(PartCatalog.Interface)
        capability.sync_data(JobRoleCatalog.Interface)
        capability.sync_data(ModuleSpec.Interface)
        capability.sync_data(ShipClassCatalog.Interface)
        capability.sync_data(ShipStatusCatalog.Interface)
        self.__class__._catalogs_synced = True

    def _ensure_base_objects(self) -> None:
        ship = Ship.all().first()
        if ship is None:
            ship = Ship.Factory.create()
        if Module.all().count() == 0:
            Module.Factory.create(ship=ship)
        if CrewMember.all().count() == 0:
            CrewMember.Factory.create(ship=ship)

    def test_ship_factory_creates_ship(self) -> None:
        ship = Ship.Factory.create()
        self.assertIsNotNone(ship.identification.get("id"))
        self.assertIsNotNone(ship.ship_class)
        self.assertIsNotNone(ship.status)

    def test_module_factory_creates_module(self) -> None:
        ship = Ship.all().first() or Ship.Factory.create()
        module = Module.Factory.create(ship=ship)
        self.assertEqual(
            module.ship.identification.get("id"),
            ship.identification.get("id"),
        )
        self.assertEqual(module.hazard_limit, module.spec.hazard_limit)

    def test_crew_member_factory_creates_member(self) -> None:
        ship = Ship.all().first() or Ship.Factory.create()
        Module.Factory.create(ship=ship)
        member = CrewMember.Factory.create(ship=ship)
        self.assertGreaterEqual(member.clearance_level, member.role.clearance_level)
        if member.assigned_module is not None:
            self.assertEqual(
                member.assigned_module.ship.identification.get("id"),
                ship.identification.get("id"),
            )

    def test_inventory_item_factory_creates_item(self) -> None:
        item = InventoryItem.Factory.create()
        self.assertIsNotNone(item.part)
        self.assertIsNotNone(item.serial)

    def test_cargo_manifest_factory_creates_manifest(self) -> None:
        manifest = CargoManifest.Factory.create()
        self.assertIsNotNone(manifest.vendor)
        self.assertIsNotNone(manifest.tracking_code)

    def test_work_order_factory_creates_order(self) -> None:
        order = WorkOrder.Factory.create()
        self.assertIsNotNone(order.module)
        self.assertIsNotNone(order.assigned_to)

    def test_incident_report_factory_creates_report(self) -> None:
        report = IncidentReport.Factory.create()
        self.assertIsNotNone(report.module)
        self.assertIsNotNone(report.report)

    def test_mission_schedule_factory_creates_schedule(self) -> None:
        schedule = MissionSchedule.Factory.create()
        self.assertIsNotNone(schedule.name)
        self.assertGreaterEqual(schedule.window_end, schedule.window_start)
