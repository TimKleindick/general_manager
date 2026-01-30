from __future__ import annotations

from datetime import date
from typing import Optional

from django.db.models import (
    BooleanField,
    CASCADE,
    CharField,
    DateField,
    FloatField,
    ForeignKey,
    IntegerField,
    SET_NULL,
)

from general_manager import FieldConfig, IndexConfig
from general_manager.factory import (
    lazy_boolean,
    lazy_choice,
    lazy_date_between,
    lazy_decimal,
    lazy_faker_name,
    lazy_integer,
)
from general_manager.interface import DatabaseInterface, ReadOnlyInterface
from general_manager.manager import GeneralManager
from general_manager.permission import ManagerBasedPermission, register_permission
from general_manager.rule import Rule


@register_permission("isCommander")
def _permission_is_commander(_instance, user, _config: list[str]) -> bool:
    return bool(getattr(user, "role", None) == "Commander")


@register_permission("isSafetyOfficer")
def _permission_is_safety_officer(_instance, user, _config: list[str]) -> bool:
    return bool(getattr(user, "role", None) == "SafetyOfficer")


@register_permission("hasClearance")
def _permission_has_clearance(_instance, user, config: list[str]) -> bool:
    if not config:
        return False
    try:
        required = int(config[0])
    except ValueError:
        return False
    return int(getattr(user, "clearance_level", 0)) >= required


class JobRoleCatalog(GeneralManager):
    name: str
    code: str
    clearance_level: int
    department: str

    _data = [
        {
            "name": "Dockmaster",
            "code": "DM",
            "clearance_level": 2,
            "department": "Operations",
        },
        {
            "name": "Quartermaster",
            "code": "QM",
            "clearance_level": 3,
            "department": "Supply",
        },
        {
            "name": "Chief Engineer",
            "code": "CE",
            "clearance_level": 4,
            "department": "Engineering",
        },
        {
            "name": "Safety Officer",
            "code": "SO",
            "clearance_level": 4,
            "department": "Safety",
        },
        {
            "name": "Flight Controller",
            "code": "FC",
            "clearance_level": 3,
            "department": "Mission",
        },
    ]

    class Interface(ReadOnlyInterface):
        name = CharField(max_length=80, unique=True)
        code = CharField(max_length=10, unique=True)
        clearance_level = IntegerField()
        department = CharField(max_length=60)

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=[
                    FieldConfig(name="name", boost=2.0),
                    "code",
                    "department",
                ],
                filters=["department", "clearance_level"],
                sorts=["name", "clearance_level"],
                boost=1.1,
            )
        ]


class CrewMember(GeneralManager):
    name: str
    rank: str
    role: JobRoleCatalog
    clearance_level: int
    ship: "maintenance.Ship"
    assigned_module: Optional["maintenance.Module"]
    on_duty: bool
    medical_hold: bool
    last_medical_check: Optional[date]
    fatigue_index: float

    class Interface(DatabaseInterface):
        name = CharField(max_length=120)
        rank = CharField(max_length=40)
        role = ForeignKey("crew.JobRoleCatalog", on_delete=CASCADE)
        clearance_level = IntegerField()
        ship = ForeignKey("maintenance.Ship", on_delete=CASCADE)
        assigned_module = ForeignKey(
            "maintenance.Module", null=True, blank=True, on_delete=SET_NULL
        )
        on_duty = BooleanField(default=True)
        medical_hold = BooleanField(default=False)
        last_medical_check = DateField(null=True, blank=True)
        fatigue_index = FloatField(default=0.2)

        class Meta:
            rules = [
                Rule["CrewMember"](
                    lambda x: x.clearance_level >= x.role.clearance_level
                ),
                Rule["CrewMember"](
                    lambda x: not (x.medical_hold and x.on_duty)
                ),
                Rule["CrewMember"](
                    lambda x: 0 <= x.fatigue_index <= 1
                ),
                Rule["CrewMember"](
                    lambda x: x.assigned_module is None
                    or x.assigned_module.ship == x.ship
                ),
            ]

        class Factory:
            name = lazy_faker_name()
            rank = lazy_choice(["Lieutenant", "Commander", "Chief", "Specialist"])
            clearance_level = lazy_integer(1, 5)
            on_duty = lazy_boolean(0.8)
            medical_hold = lazy_boolean(0.1)
            last_medical_check = lazy_date_between(
                date(2222, 1, 1), date(2222, 12, 31)
            )
            fatigue_index = lazy_decimal(0, 1, 2)

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander", "isSafetyOfficer"]
        __update__ = ["isCommander", "hasClearance:3"]
        __delete__ = ["isCommander"]

        clearance_level = {"update": ["isCommander"]}

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=[
                    FieldConfig(name="name", boost=2.0),
                    "rank",
                    "role__name",
                    "assigned_module__name",
                ],
                filters=[
                    "rank",
                    "role__code",
                    "clearance_level",
                    "on_duty",
                    "medical_hold",
                    "ship__registry",
                    "ship__name",
                ],
                sorts=["name", "clearance_level"],
                boost=1.3,
            )
        ]
