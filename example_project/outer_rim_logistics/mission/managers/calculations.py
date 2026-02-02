from __future__ import annotations

from datetime import date, timedelta

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

from outer_rim_logistics.crew.managers import CrewMember, JobRoleCatalog
from outer_rim_logistics.maintenance.managers import IncidentReport, Module, Ship, WorkOrder
from outer_rim_logistics.supply.managers import CargoManifest, InventoryItem, PartCatalog
from .schedule import MissionSchedule
from general_manager.measurement import Measurement


def _mission_as_of_values(ship: Ship | None = None) -> list[date]:
    schedules = list(MissionSchedule.all())
    if schedules:
        return [schedule.window_start for schedule in schedules]
    return [date.today()]


def _module_values() -> list[Module]:
    return list(Module.all())


def _ship_values() -> list[Ship]:
    return list(Ship.all())


def _select_schedule(schedules: list[MissionSchedule]) -> MissionSchedule:
    schedules_sorted = sorted(schedules, key=lambda schedule: schedule.window_start)
    today = date.today()
    for schedule in schedules_sorted:
        if schedule.window_start >= today:
            return schedule
    return schedules_sorted[0]


class CrewReadiness(GeneralManager):
    as_of: date
    ship: Ship | None

    class Interface(CalculationInterface):
        as_of = Input(date, possible_values=_mission_as_of_values)
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def score(self) -> Measurement:
        crew_query = CrewMember.all()
        if self.ship is not None:
            crew_query = crew_query.filter(ship_id=self.ship.identification["id"])
        crew = list(crew_query)
        if not crew:
            return Measurement(0, "percent")
        on_duty = [
            member for member in crew if member.on_duty and not member.medical_hold
        ]
        availability_ratio = len(on_duty) / len(crew)
        fatigue_avg = sum(member.fatigue_index for member in crew) / len(crew)
        roles = {role.code for role in JobRoleCatalog.all()}
        staffed_roles = {member.role.code for member in on_duty}
        clearance_coverage = len(staffed_roles) / max(1, len(roles))
        score = 100 * availability_ratio
        score -= fatigue_avg * 20
        score -= (1 - clearance_coverage) * 20
        return Measurement(max(0.0, min(100.0, score)), "percent")


class InventoryHealth(GeneralManager):
    as_of: date
    ship: Ship | None

    class Interface(CalculationInterface):
        as_of = Input(date, possible_values=_mission_as_of_values)
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def health(self) -> Measurement:
        parts = list(PartCatalog.all())
        if not parts:
            return Measurement(0, "percent")
        coverage = 0
        expiring = 0
        for part in parts:
            inventory_query = InventoryItem.filter(part=part)
            if self.ship is not None:
                inventory_query = inventory_query.filter(
                    location__ship_id=self.ship.identification["id"]
                )
            total_qty = sum(item.quantity for item in inventory_query)
            if total_qty >= part.reorder_threshold:
                coverage += 1
            for item in inventory_query:
                if item.expires_on and item.expires_on <= self.as_of + timedelta(
                    days=14
                ):
                    expiring += 1
        coverage_ratio = coverage / len(parts)
        expiring_penalty = min(15, expiring * 2)
        score = coverage_ratio * 100 - expiring_penalty
        return Measurement(max(0.0, min(100.0, score)), "percent")


class ModuleHealth(GeneralManager):
    ship: Ship | None

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def score(self) -> Measurement:
        modules_query = Module.all()
        if self.ship is not None:
            modules_query = modules_query.filter(ship_id=self.ship.identification["id"])
        modules = list(modules_query)
        if not modules:
            return Measurement(0, "percent")
        orders_query = WorkOrder.all()
        if self.ship is not None:
            orders_query = orders_query.filter(
                module__ship_id=self.ship.identification["id"]
            )
        open_orders = [order for order in orders_query if order.status != "closed"]
        order_penalty = min(30, sum(order.severity for order in open_orders))
        incidents_query = IncidentReport.all()
        if self.ship is not None:
            incidents_query = incidents_query.filter(
                module__ship_id=self.ship.identification["id"]
            )
        recent_incidents = [
            incident
            for incident in incidents_query
            if incident.occurred_on >= date.today() - timedelta(days=30)
        ]
        incident_penalty = min(
            20, sum(incident.severity for incident in recent_incidents)
        )
        uptime_bonus = sum(module.life_support_uptime for module in modules) / len(
            modules
        )
        score = 60 + uptime_bonus * 0.2 - order_penalty - incident_penalty
        return Measurement(max(0.0, min(100.0, score)), "percent")


class ScheduleFeasibility(GeneralManager):
    class Interface(CalculationInterface):
        pass

    @graph_ql_property
    def score(self) -> Measurement:
        schedules = list(MissionSchedule.all())
        if not schedules:
            return Measurement(0, "percent")
        schedule = _select_schedule(schedules)
        window_days = (schedule.window_start - date.today()).days
        window_penalty = min(20, max(0, -window_days))
        resupply_risk = 0
        if schedule.resupply_eta > schedule.window_end:
            resupply_risk = 20
        backlog_penalty = min(20, int(schedule.backlog_ratio * 10))
        return Measurement(
            max(0.0, 100 - window_penalty - resupply_risk - backlog_penalty),
            "percent",
        )


class MissionReadiness(GeneralManager):
    as_of: date
    ship: Ship | None

    class Interface(CalculationInterface):
        as_of = Input(date, possible_values=_mission_as_of_values)
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def readiness(self) -> Measurement:
        crew_score = CrewReadiness(as_of=self.as_of, ship=self.ship).score
        inventory_score = InventoryHealth(as_of=self.as_of, ship=self.ship).health
        module_score = ModuleHealth(ship=self.ship).score
        schedule_score = ScheduleFeasibility().score
        weighted = (
            0.35 * crew_score
            + 0.30 * inventory_score
            + 0.20 * module_score
            + 0.15 * schedule_score
        )
        return weighted.to("percent")


class CrewFatigue(GeneralManager):
    ship: Ship | None

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def average_fatigue(self) -> Measurement:
        crew_query = CrewMember.all()
        if self.ship is not None:
            crew_query = crew_query.filter(ship_id=self.ship.identification["id"])
        crew = list(crew_query)
        if not crew:
            return Measurement(0, "percent")
        avg = sum(member.fatigue_index for member in crew) / len(crew)
        return Measurement(avg * 100, "percent")


class OxygenBurnRate(GeneralManager):
    module: Module

    class Interface(CalculationInterface):
        module = Input(Module, possible_values=_module_values)

    @graph_ql_property
    def oxygen_burn(self) -> Measurement:
        crew_count = CrewMember.filter(assigned_module=self.module).count()
        base = 3.5
        return Measurement(base * max(1, crew_count), "kg/day")


class ResupplyWindowRisk(GeneralManager):
    ship: Ship | None

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def risk(self) -> Measurement:
        schedules = list(MissionSchedule.all())
        manifests_query = CargoManifest.all()
        if self.ship is not None:
            manifests_query = manifests_query.filter(
                destination_module__ship_id=self.ship.identification["id"]
            )
        manifests = list(manifests_query)
        if not schedules or not manifests:
            return Measurement(0, "percent")
        schedule = _select_schedule(schedules)
        lateness = sum(
            1 for manifest in manifests if manifest.eta_date > schedule.window_end
        )
        risk = min(100, lateness * 25)
        return Measurement(float(risk), "percent")


class ShipReadiness(GeneralManager):
    ship: Ship
    as_of: date

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)
        as_of = Input(date, possible_values=_mission_as_of_values)

    @graph_ql_property
    def readiness(self) -> Measurement:
        return MissionReadiness(as_of=self.as_of, ship=self.ship).readiness


class ShipInventoryCoverage(GeneralManager):
    ship: Ship
    as_of: date

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)
        as_of = Input(date, possible_values=_mission_as_of_values)

    @graph_ql_property
    def coverage(self) -> Measurement:
        return InventoryHealth(as_of=self.as_of, ship=self.ship).health


class ShipCrewLoad(GeneralManager):
    ship: Ship

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def load(self) -> Measurement:
        crew = list(CrewMember.filter(ship_id=self.ship.identification["id"]))
        if not crew:
            return Measurement(0, "percent")
        staffed_roles = {member.role.code for member in crew}
        required_roles = {role.code for role in JobRoleCatalog.all()}
        coverage_ratio = len(staffed_roles) / max(1, len(required_roles))
        return Measurement(coverage_ratio * 100, "percent")


class ShipOxygenReserve(GeneralManager):
    ship: Ship

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def reserve(self) -> Measurement:
        modules = list(Module.filter(ship_id=self.ship.identification["id"]))
        if not modules:
            return Measurement(0, "kg")
        total = sum((module.oxygen_reserve for module in modules), Measurement(0, "kg"))
        return total


class ShipMaintenanceBacklog(GeneralManager):
    ship: Ship

    class Interface(CalculationInterface):
        ship = Input(Ship, possible_values=_ship_values)

    @graph_ql_property
    def backlog(self) -> Measurement:
        open_orders = WorkOrder.filter(
            module__ship_id=self.ship.identification["id"]
        ).exclude(status="closed")
        return Measurement(open_orders.count(), "dimensionless")
