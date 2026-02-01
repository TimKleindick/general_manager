from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional
from django.core.management.base import BaseCommand
from django.core.management import call_command

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
from supply.managers import CargoManifest, InventoryItem, PartCatalog, VendorCatalog


class Command(BaseCommand):
    help = "Seed the Outer Rim Logistics example with sample data."

    def handle(self, *_args, **_options) -> None:
        self._random = random.Random(42)
        self.stdout.write("Seeding Outer Rim Logistics data...")
        self._seed_ships()
        self._seed_modules()
        self._seed_crew()
        self._seed_inventory()
        self._seed_work_orders()
        self._seed_incidents()
        self._seed_schedule()
        self._seed_manifests()
        call_command("search_index", reindex=True)
        self.stdout.write(self.style.SUCCESS("Seeding complete."))

    def _seed_ships(self) -> None:
        target_ships = 20
        ships = list(Ship.all())
        if len(ships) >= target_ships:
            return
        ship_classes = list(ShipClassCatalog.all())
        ship_statuses = list(ShipStatusCatalog.all())
        if not ship_classes or not ship_statuses:
            return
        existing_names = {ship.name for ship in ships}
        existing_registry = {ship.registry for ship in ships}
        ship_names = [
            "Outer Rim Reliant",
            "Kessel Drift",
            "Nebula Runner",
            "Ryloth Courier",
            "Corellian Wayfarer",
            "Sullust Dawn",
            "Mandalore Meridian",
            "Dathomir Wisp",
            "Tatooine Wind",
            "Bespin Lumen",
            "Alderaan Ember",
            "Endor Trail",
            "Ilum Echo",
            "Ord Mantell Spur",
            "Lothal Horizon",
            "Naboo Crest",
            "Jakku Verge",
            "Mustafar Forge",
            "Hoth Lantern",
            "Rogue Spur",
        ]
        registry_start = 780
        for idx, name in enumerate(ship_names):
            if len(ships) >= target_ships:
                break
            registry = f"ORL-{registry_start + idx}"
            if name in existing_names or registry in existing_registry:
                continue
            ship_class = ship_classes[idx % len(ship_classes)]
            ship_status = ship_statuses[idx % len(ship_statuses)]
            ships.append(
                Ship.create(
                    ignore_permission=True,
                    name=name,
                    registry=registry,
                    ship_class=ship_class,
                    status=ship_status,
                )
            )
            existing_names.add(name)
            existing_registry.add(registry)
        counter = registry_start + len(ship_names)
        while len(ships) < target_ships:
            registry = f"ORL-{counter}"
            if registry in existing_registry:
                counter += 1
                continue
            ship_class = ship_classes[counter % len(ship_classes)]
            ship_status = ship_statuses[counter % len(ship_statuses)]
            ships.append(
                Ship.create(
                    ignore_permission=True,
                    name=f"Outer Rim Freighter {len(ships) + 1}",
                    registry=registry,
                    ship_class=ship_class,
                    status=ship_status,
                )
            )
            existing_registry.add(registry)
            counter += 1

    def _seed_modules(self) -> None:
        ships = list(Ship.all())
        if not ships:
            return
        modules = list(Module.all())
        if modules:
            for idx, module in enumerate(modules):
                if module.ship is None:
                    module.update(ignore_permission=True, ship=ships[idx % len(ships)])
        specs = list(ModuleSpec.all())
        if not specs:
            return
        existing_names = {module.name for module in modules}
        status_cycle = ["operational", "operational", "maintenance", "offline"]
        created = 0
        for ship in ships:
            for spec in specs:
                name = f"{ship.registry}-{spec.module_code}"
                if name in existing_names:
                    continue
                Module.create(
                    ignore_permission=True,
                    name=name,
                    ship=ship,
                    spec=spec,
                    status=status_cycle[created % len(status_cycle)],
                    life_support_uptime=round(self._random.uniform(84, 99), 2),
                    oxygen_reserve=f"{self._random.randint(90, 180)} kg",
                    battery_capacity=f"{self._random.randint(320, 620)} kWh",
                    hazard_limit=spec.hazard_limit,
                    notes="Outer Rim Logistics operational module",
                )
                existing_names.add(name)
                created += 1

    def _seed_crew(self) -> None:
        target_crew = 100
        roles = {role.code: role for role in JobRoleCatalog.all()}
        modules = list(Module.all())
        ships = list(Ship.all())
        if not roles or not ships:
            return
        modules_by_ship: dict[str, list[Module]] = {}
        for module in modules:
            modules_by_ship.setdefault(module.ship.identification["id"], []).append(
                module
            )
        for ship_modules in modules_by_ship.values():
            ship_modules.sort(key=lambda module: module.name)
        def _module_for_ship(ship: Ship, index: int) -> Optional[Module]:
            ship_modules = modules_by_ship.get(ship.identification["id"], [])
            if not ship_modules:
                return None
            return ship_modules[index % len(ship_modules)]
        existing_names = {member.name for member in CrewMember.all()}
        primary_ship = ships[0]
        secondary_ship = ships[1] if len(ships) > 1 else ships[0]
        roster = [
            {
                "name": "Aren Voss",
                "rank": "Commander",
                "role": roles.get("FC"),
                "clearance_level": 5,
                "ship": primary_ship,
                "assigned_module": _module_for_ship(primary_ship, 0),
                "on_duty": True,
                "medical_hold": False,
                "last_medical_check": date(2222, 6, 14),
                "fatigue_index": 0.18,
            },
            {
                "name": "Lira Dane",
                "rank": "Chief",
                "role": roles.get("CE"),
                "clearance_level": 4,
                "ship": primary_ship,
                "assigned_module": _module_for_ship(primary_ship, 1),
                "on_duty": True,
                "medical_hold": False,
                "last_medical_check": date(2222, 5, 2),
                "fatigue_index": 0.32,
            },
            {
                "name": "Tess Kanto",
                "rank": "Lieutenant",
                "role": roles.get("QM"),
                "clearance_level": 3,
                "ship": secondary_ship,
                "assigned_module": _module_for_ship(secondary_ship, 2),
                "on_duty": True,
                "medical_hold": False,
                "last_medical_check": date(2222, 3, 18),
                "fatigue_index": 0.27,
            },
            {
                "name": "Jax Arlenn",
                "rank": "Specialist",
                "role": roles.get("SO"),
                "clearance_level": 4,
                "ship": primary_ship,
                "assigned_module": _module_for_ship(primary_ship, 0),
                "on_duty": False,
                "medical_hold": True,
                "last_medical_check": date(2222, 7, 3),
                "fatigue_index": 0.6,
            },
        ]
        for entry in roster:
            if entry["role"] is None:
                continue
            if entry["name"] in existing_names:
                continue
            CrewMember.create(ignore_permission=True, **entry)
            existing_names.add(entry["name"])
        crew_count = CrewMember.all().count()
        if crew_count >= target_crew:
            return
        crew_names = [
            "Leia",
            "Han",
            "Luke",
            "Rey",
            "Finn",
            "Poe",
            "Jyn",
            "Cassian",
            "Bail",
            "Mon",
            "Hera",
            "Sabine",
            "Ezra",
            "Kanan",
            "Ahsoka",
            "Bo-Katan",
            "Din",
            "Cara",
            "Lando",
            "Wedge",
            "Biggs",
            "Padme",
            "Mace",
            "Qui-Gon",
            "Rose",
            "Holdo",
            "Ackbar",
            "Phasma",
            "Thrawn",
            "Cassio",
        ]
        rank_cycle = ["Lieutenant", "Commander", "Chief", "Specialist", "Ensign"]
        role_list = [role for role in roles.values()]
        for idx in range(target_crew - crew_count):
            ship = ships[idx % len(ships)]
            role = role_list[idx % len(role_list)]
            name = crew_names[idx % len(crew_names)]
            suffix = idx // len(crew_names) + 1
            full_name = f"{name} {suffix}"
            while full_name in existing_names:
                suffix += 1
                full_name = f"{name} {suffix}"
            clearance_level = min(5, role.clearance_level + self._random.randint(0, 2))
            medical_hold = self._random.random() < 0.08
            on_duty = self._random.random() < 0.78
            if medical_hold:
                on_duty = False
            assigned_module = None
            ship_modules = modules_by_ship.get(ship.identification["id"], [])
            if ship_modules and self._random.random() < 0.7:
                assigned_module = ship_modules[idx % len(ship_modules)]
            CrewMember.create(
                ignore_permission=True,
                name=full_name,
                rank=rank_cycle[idx % len(rank_cycle)],
                role=role,
                clearance_level=clearance_level,
                ship=ship,
                assigned_module=assigned_module,
                on_duty=on_duty,
                medical_hold=medical_hold,
                last_medical_check=date(
                    2222, self._random.randint(1, 12), self._random.randint(1, 28)
                ),
                fatigue_index=round(self._random.uniform(0.1, 0.65), 2),
            )
            existing_names.add(full_name)

    def _seed_inventory(self) -> None:
        modules = list(Module.all())
        parts = list(PartCatalog.all())
        if not modules or not parts:
            return
        target_items = len(modules) * len(parts)
        existing_count = InventoryItem.all().count()
        if existing_count >= target_items:
            return
        existing_serials = {item.serial for item in InventoryItem.all()}
        serial_counter = 1000
        for module in modules:
            for part in parts:
                if existing_count >= target_items:
                    return
                serial = f"{module.ship.registry}-{part.part_number}-{serial_counter}"
                while serial in existing_serials:
                    serial_counter += 1
                    serial = (
                        f"{module.ship.registry}-{part.part_number}-{serial_counter}"
                    )
                received_on = date(2222, 3, 1) + timedelta(
                    days=self._random.randint(0, 240)
                )
                expires_on = received_on + timedelta(days=540)
                InventoryItem.create(
                    ignore_permission=True,
                    serial=serial,
                    part=part,
                    quantity=part.reorder_threshold + self._random.randint(0, 18),
                    location=module,
                    received_on=received_on,
                    expires_on=expires_on,
                    reserved=self._random.random() < 0.18,
                )
                existing_serials.add(serial)
                serial_counter += 1
                existing_count += 1

    def _seed_work_orders(self) -> None:
        target_orders = 60
        if WorkOrder.all().count() >= target_orders:
            return
        modules = list(Module.all())
        if not modules:
            return
        crew_by_ship = {}
        for crew_member in CrewMember.all():
            crew_by_ship.setdefault(crew_member.ship.identification["id"], []).append(
                crew_member
            )
        order_titles = [
            "Replace condenser valves",
            "Inspect coolant arrays",
            "Patch microfractures",
            "Calibrate docking clamps",
            "Rewire sensor grid",
            "Align thrust vanes",
            "Service gravity coils",
            "Replace actuator seals",
            "Run diagnostic sweep",
            "Stabilize power conduits",
        ]
        existing_count = WorkOrder.all().count()
        for idx in range(target_orders - existing_count):
            module = modules[idx % len(modules)]
            severity = self._random.randint(1, 5)
            opened_on = date(2222, 5, 1) + timedelta(
                days=self._random.randint(0, 180)
            )
            due_by = opened_on + timedelta(days=severity * 5)
            ship_crew = crew_by_ship.get(module.ship.identification["id"], [])
            assigned_to = None
            if ship_crew and self._random.random() < 0.5:
                assigned_to = ship_crew[idx % len(ship_crew)]
            WorkOrder.create(
                ignore_permission=True,
                module=module,
                assigned_to=assigned_to,
                title=f"{order_titles[idx % len(order_titles)]} ({module.name})",
                severity=severity,
                status=["open", "in_progress", "blocked", "closed"][
                    idx % 4
                ],
                opened_on=opened_on,
                due_by=due_by,
                requires_eva=self._random.random() < 0.2,
            )

    def _seed_incidents(self) -> None:
        target_incidents = 25
        if IncidentReport.all().count() >= target_incidents:
            return
        modules = list(Module.all())
        if not modules:
            return
        reports = [
            "Minor pressure fluctuation during docking lock.",
            "Power bus instability detected during shift change.",
            "Cooling loop feedback spiked beyond tolerance.",
            "Cargo latch misalignment during unload sequence.",
            "Atmospheric scrubber readings drifted off baseline.",
            "Hazard sensor pinged during EVA prep.",
            "Communications relay cut out for 12 seconds.",
            "Thermal shield microfracture found in inspection.",
        ]
        existing = IncidentReport.all().count()
        for idx in range(target_incidents - existing):
            IncidentReport.create(
                ignore_permission=True,
                module=modules[idx % len(modules)],
                severity=self._random.randint(1, 5),
                occurred_on=date(2222, 6, 1)
                + timedelta(days=self._random.randint(0, 180)),
                resolved=self._random.random() < 0.55,
                report=reports[idx % len(reports)],
            )

    def _seed_schedule(self) -> None:
        if MissionSchedule.all().count() > 0:
            return
        MissionSchedule.create(
            ignore_permission=True,
            name="Outer Rim Resupply Window",
            window_start=date(2222, 9, 3),
            window_end=date(2222, 9, 12),
            resupply_eta=date(2222, 9, 9),
            backlog_ratio=0.7,
            status="active",
        )

    def _seed_manifests(self) -> None:
        target_manifests = 30
        if CargoManifest.all().count() >= target_manifests:
            return
        vendors = list(VendorCatalog.all())
        modules = list(Module.all())
        if not vendors:
            return
        existing = CargoManifest.all().count()
        for idx in range(target_manifests - existing):
            CargoManifest.create(
                ignore_permission=True,
                tracking_code=f"ORL-{7800 + idx}",
                vendor=vendors[idx % len(vendors)],
                eta_date=date(2222, 8, 20) + timedelta(days=idx),
                destination_module=modules[idx % len(modules)] if modules else None,
                status=["enroute", "docked", "delayed", "unloading"][idx % 4],
                priority=self._random.random() < 0.25,
                total_mass=f"{self._random.randint(900, 2200)} kg",
                total_volume=f"{self._random.randint(200, 520)} l",
            )
