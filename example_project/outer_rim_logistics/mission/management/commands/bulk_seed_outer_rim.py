from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django.core.management.base import BaseCommand
from django.db import transaction

from general_manager.interface.capabilities.read_only.management import (
    ReadOnlyManagementCapability,
)

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
from outer_rim_logistics.mission.managers import MissionSchedule
from outer_rim_logistics.supply.managers import (
    CargoManifest,
    HazardClass,
    InventoryItem,
    PartCatalog,
    VendorCatalog,
)


@dataclass(frozen=True)
class SeedTarget:
    name: str
    manager: type
    target: int


class Command(BaseCommand):
    help = "Bulk seed the ORL example using GeneralManager factories."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--ships", type=int, default=1000)
        parser.add_argument("--modules", type=int, default=3000)
        parser.add_argument("--crew", type=int, default=8000)
        parser.add_argument("--inventory", type=int, default=8000)
        parser.add_argument("--manifests", type=int, default=1000)
        parser.add_argument("--work-orders", type=int, default=4000)
        parser.add_argument("--incidents", type=int, default=2000)
        parser.add_argument("--schedules", type=int, default=800)
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *_args, **options) -> None:
        self._sync_catalogs()

        batch_size = max(1, int(options["batch_size"]))

        targets = [
            SeedTarget("ships", Ship, int(options["ships"])),
            SeedTarget("modules", Module, int(options["modules"])),
            SeedTarget("crew", CrewMember, int(options["crew"])),
            SeedTarget("inventory", InventoryItem, int(options["inventory"])),
            SeedTarget("manifests", CargoManifest, int(options["manifests"])),
            SeedTarget("work_orders", WorkOrder, int(options["work_orders"])),
            SeedTarget("incidents", IncidentReport, int(options["incidents"])),
            SeedTarget("schedules", MissionSchedule, int(options["schedules"])),
        ]

        self._ensure_minimums()

        for target in targets:
            self._create_missing(target, batch_size)

        self.stdout.write(self.style.SUCCESS("Bulk seeding complete."))

    def _sync_catalogs(self) -> None:
        capability = ReadOnlyManagementCapability()
        capability.sync_data(HazardClass.Interface)
        capability.sync_data(VendorCatalog.Interface)
        capability.sync_data(PartCatalog.Interface)
        capability.sync_data(JobRoleCatalog.Interface)
        capability.sync_data(ModuleSpec.Interface)
        capability.sync_data(ShipClassCatalog.Interface)
        capability.sync_data(ShipStatusCatalog.Interface)

    def _ensure_minimums(self) -> None:
        if Ship.all().count() == 0:
            Ship.Factory.create()
        if Module.all().count() == 0:
            Module.Factory.create()
        if CrewMember.all().count() == 0:
            CrewMember.Factory.create()

    def _create_missing(self, target: SeedTarget, batch_size: int) -> None:
        existing = target.manager.all().count()
        if existing >= target.target:
            return
        remaining = target.target - existing
        self.stdout.write(
            f"Seeding {target.name}: {remaining} (target {target.target})"
        )
        self._create_in_batches(target.manager.Factory.create_batch, remaining, batch_size)

    def _create_in_batches(
        self,
        create_batch: Callable[[int], list],
        count: int,
        batch_size: int,
    ) -> None:
        remaining = count
        while remaining > 0:
            size = min(batch_size, remaining)
            with transaction.atomic():
                create_batch(size)
            remaining -= size
