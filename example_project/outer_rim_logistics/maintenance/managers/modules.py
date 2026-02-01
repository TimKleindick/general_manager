from __future__ import annotations

from typing import Optional

from django.db.models import (
    CASCADE,
    CharField,
    FloatField,
    ForeignKey,
    IntegerField,
    SET_NULL,
)

from factory.declarations import LazyAttribute, LazyAttributeSequence, LazyFunction
from general_manager import FieldConfig, IndexConfig
from general_manager.factory import (
    lazy_choice,
    lazy_integer,
    lazy_measurement,
    lazy_project_name,
)
from general_manager.interface import DatabaseInterface, ReadOnlyInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement, MeasurementField
from general_manager.permission import ManagerBasedPermission
from general_manager.rule import Rule
from orl.factory_utils import (
    random_module_spec,
    random_ship,
    random_ship_class,
    random_ship_status,
)


class ModuleSpec(GeneralManager):
    module_code: str
    name: str
    base_capacity: Measurement
    life_support_rating: int
    hazard_limit: int

    _data = [
        {
            "module_code": "ORL-A",
            "name": "Habitat Ring",
            "base_capacity": "1200 l",
            "life_support_rating": 95,
            "hazard_limit": 6,
        },
        {
            "module_code": "ORL-B",
            "name": "Engineering Bay",
            "base_capacity": "900 l",
            "life_support_rating": 90,
            "hazard_limit": 8,
        },
        {
            "module_code": "ORL-C",
            "name": "Cargo Spine",
            "base_capacity": "1800 l",
            "life_support_rating": 85,
            "hazard_limit": 10,
        },
    ]

    class Interface(ReadOnlyInterface):
        module_code = CharField(max_length=12, unique=True)
        name = CharField(max_length=120)
        base_capacity = MeasurementField(base_unit="l")
        life_support_rating = IntegerField()
        hazard_limit = IntegerField()


class ShipClassCatalog(GeneralManager):
    name: str
    code: str
    description: str

    _data = [
        {
            "name": "Courier Cruiser",
            "code": "CC",
            "description": "Fast resupply platform for outer rim routes.",
        },
        {
            "name": "Cargo Hauler",
            "code": "CH",
            "description": "Heavy cargo transport with reinforced bays.",
        },
        {
            "name": "Escort Frigate",
            "code": "EF",
            "description": "Patrol and escort vessel with expanded life support.",
        },
    ]

    class Interface(ReadOnlyInterface):
        name = CharField(max_length=120, unique=True)
        code = CharField(max_length=20, unique=True)
        description = CharField(max_length=255)


class ShipStatusCatalog(GeneralManager):
    name: str
    code: str

    _data = [
        {"name": "Active", "code": "active"},
        {"name": "Docked", "code": "docked"},
        {"name": "Maintenance", "code": "maintenance"},
    ]

    class Interface(ReadOnlyInterface):
        name = CharField(max_length=80, unique=True)
        code = CharField(max_length=20, unique=True)


class Ship(GeneralManager):
    name: str
    registry: str
    ship_class: ShipClassCatalog
    status: ShipStatusCatalog

    class Interface(DatabaseInterface):
        name = CharField(max_length=120, unique=True)
        registry = CharField(max_length=40, unique=True)
        ship_class = ForeignKey("maintenance.ShipClassCatalog", on_delete=CASCADE)
        status = ForeignKey("maintenance.ShipStatusCatalog", on_delete=CASCADE)

        class Factory:
            name = lazy_project_name()
            registry = LazyAttributeSequence(lambda _obj, idx: f"ORL-{9000 + idx}")
            ship_class = LazyFunction(random_ship_class)
            status = LazyFunction(random_ship_status)

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander"]
        __update__ = ["isCommander", "isSafetyOfficer"]
        __delete__ = ["isCommander"]

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=[
                    FieldConfig(name="name", boost=2.0),
                    "registry",
                    "ship_class__name",
                    "status__name",
                ],
                filters=["ship_class__code", "status__code"],
                sorts=["name", "registry"],
                boost=1.1,
            )
        ]


class Module(GeneralManager):
    name: str
    ship: Ship
    spec: ModuleSpec
    status: str
    life_support_uptime: float
    oxygen_reserve: Measurement
    battery_capacity: Measurement
    hazard_limit: int
    notes: Optional[str]

    class Interface(DatabaseInterface):
        name = CharField(max_length=120, unique=True)
        ship = ForeignKey("maintenance.Ship", on_delete=CASCADE)
        spec = ForeignKey("maintenance.ModuleSpec", on_delete=CASCADE)
        status = CharField(max_length=40)
        life_support_uptime = FloatField()
        oxygen_reserve = MeasurementField(base_unit="kg")
        battery_capacity = MeasurementField(base_unit="kWh")
        hazard_limit = IntegerField()
        notes = CharField(max_length=255, null=True, blank=True)

        class Meta:
            rules = [
                Rule["Module"](
                    lambda x: 0 <= x.life_support_uptime <= 100
                ),
                Rule["Module"](
                    lambda x: x.oxygen_reserve >= "50 kg"
                ),
            ]

        class Factory:
            name = LazyAttributeSequence(lambda _obj, idx: f"ORL-MOD-{1000 + idx}")
            ship = LazyFunction(random_ship)
            spec = LazyFunction(random_module_spec)
            status = lazy_choice(["operational", "maintenance", "offline"])
            life_support_uptime = lazy_integer(80, 99)
            oxygen_reserve = lazy_measurement(60, 180, "kg")
            battery_capacity = lazy_measurement(320, 680, "kWh")
            hazard_limit = LazyAttribute(lambda obj: obj.spec.hazard_limit)
            notes = lazy_choice(
                [
                    "Outer Rim Logistics operational module",
                    "Routine inspection pending",
                    "Emergency systems verified",
                ]
            )

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander"]
        __update__ = ["isCommander", "isSafetyOfficer"]
        __delete__ = ["isCommander"]
