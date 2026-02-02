from __future__ import annotations

from datetime import timedelta
from random import SystemRandom
from typing import Callable, TypeVar

from general_manager.manager import GeneralManager

ManagerT = TypeVar("ManagerT", bound=GeneralManager)

_RNG = SystemRandom()


def _random_manager_instance(
    manager_getter: Callable[[], type[ManagerT]], label: str
) -> ManagerT:
    manager_cls = manager_getter()
    items = list(manager_cls.all())
    if not items:
        raise ValueError(f"No {label} instances available; seed catalogs/data first.")
    return _RNG.choice(items)


def random_ship_class():
    from outer_rim_logistics.maintenance.managers import ShipClassCatalog

    return _random_manager_instance(lambda: ShipClassCatalog, "ShipClassCatalog")


def random_ship_status():
    from outer_rim_logistics.maintenance.managers import ShipStatusCatalog

    return _random_manager_instance(lambda: ShipStatusCatalog, "ShipStatusCatalog")


def random_module_spec():
    from outer_rim_logistics.maintenance.managers import ModuleSpec

    return _random_manager_instance(lambda: ModuleSpec, "ModuleSpec")


def random_ship():
    from outer_rim_logistics.maintenance.managers import Ship

    return _random_manager_instance(lambda: Ship, "Ship")


def random_module():
    from outer_rim_logistics.maintenance.managers import Module

    return _random_manager_instance(lambda: Module, "Module")


def random_module_for_ship(ship):
    from outer_rim_logistics.maintenance.managers import Module

    ship_id = ship.identification.get("id")
    candidates = [
        module
        for module in Module.all()
        if module.ship is not None
        and module.ship.identification.get("id") == ship_id
    ]
    if not candidates:
        return None
    return _RNG.choice(candidates)


def random_optional_module(probability: float = 0.6):
    if probability <= 0:
        return None
    if probability >= 1:
        return random_module()
    return random_module() if _RNG.random() < probability else None


def random_optional_module_for_ship(ship, probability: float = 0.6):
    if probability <= 0:
        return None
    if probability >= 1:
        return random_module_for_ship(ship)
    return (
        random_module_for_ship(ship) if _RNG.random() < probability else None
    )


def random_clearance_level(min_level: int, max_level: int = 5):
    if min_level >= max_level:
        return min_level
    return _RNG.randint(min_level, max_level)


def random_medical_hold(on_duty: bool, probability: float = 0.1) -> bool:
    if on_duty:
        return False
    return _RNG.random() < probability


def random_due_by(opened_on, severity: int):
    max_days = max(1, severity) * 7
    return opened_on + timedelta(days=_RNG.randint(0, max_days))


def random_role():
    from outer_rim_logistics.crew.managers import JobRoleCatalog

    return _random_manager_instance(lambda: JobRoleCatalog, "JobRoleCatalog")


def random_crew_member():
    from outer_rim_logistics.crew.managers import CrewMember

    return _random_manager_instance(lambda: CrewMember, "CrewMember")


def random_part():
    from outer_rim_logistics.supply.managers import PartCatalog

    return _random_manager_instance(lambda: PartCatalog, "PartCatalog")


def random_vendor():
    from outer_rim_logistics.supply.managers import VendorCatalog

    return _random_manager_instance(lambda: VendorCatalog, "VendorCatalog")
