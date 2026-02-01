from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from django.db.models import (
    BooleanField,
    CASCADE,
    CharField,
    DateField,
    ForeignKey,
    IntegerField,
    SET_NULL,
)

from factory.declarations import LazyAttribute, LazyFunction
from general_manager.factory import (
    lazy_boolean,
    lazy_choice,
    lazy_date_between,
    lazy_faker_sentence,
    lazy_integer,
)
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.rule import Rule
from orl.factory_utils import random_crew_member, random_due_by, random_module


def _work_order_due_ok(order: "WorkOrder") -> bool:
    if order.due_by is None:
        return True
    if order.opened_on is None:
        return True
    severity_days = max(1, order.severity) * 7
    return order.due_by <= order.opened_on + timedelta(days=severity_days)


class WorkOrder(GeneralManager):
    title: str
    module: "maintenance.Module"
    assigned_to: Optional["crew.CrewMember"]
    severity: int
    status: str
    opened_on: date
    due_by: Optional[date]
    requires_eva: bool

    class Interface(DatabaseInterface):
        title = CharField(max_length=140)
        module = ForeignKey("maintenance.Module", on_delete=CASCADE)
        assigned_to = ForeignKey(
            "crew.CrewMember", null=True, blank=True, on_delete=SET_NULL
        )
        severity = IntegerField()
        status = CharField(max_length=30)
        opened_on = DateField()
        due_by = DateField(null=True, blank=True)
        requires_eva = BooleanField(default=False)

        class Meta:
            rules = [
                Rule["WorkOrder"](
                    lambda x: x.due_by is None
                    or x.opened_on is None
                    or x.due_by >= x.opened_on
                ),
                Rule["WorkOrder"](_work_order_due_ok),
            ]

        class Factory:
            title = lazy_faker_sentence(5)
            module = LazyFunction(random_module)
            assigned_to = LazyFunction(lambda: random_crew_member())
            severity = lazy_integer(1, 5)
            status = lazy_choice(["open", "in_progress", "blocked", "closed"])
            opened_on = lazy_date_between(date(2222, 1, 1), date(2222, 12, 31))
            due_by = LazyAttribute(lambda obj: random_due_by(obj.opened_on, obj.severity))
            requires_eva = lazy_boolean(0.15)

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander", "isSafetyOfficer"]
        __update__ = ["isCommander", "isSafetyOfficer"]
        __delete__ = ["isCommander"]

        status = {"update": ["isCommander", "isSafetyOfficer"]}


class IncidentReport(GeneralManager):
    module: "maintenance.Module"
    severity: int
    occurred_on: date
    resolved: bool
    report: str

    class Interface(DatabaseInterface):
        module = ForeignKey("maintenance.Module", on_delete=CASCADE)
        severity = IntegerField()
        occurred_on = DateField()
        resolved = BooleanField(default=False)
        report = CharField(max_length=255)

        class Meta:
            rules = [
                Rule["IncidentReport"](lambda x: 1 <= x.severity <= 5),
            ]

        class Factory:
            module = LazyFunction(random_module)
            severity = lazy_integer(1, 5)
            occurred_on = lazy_date_between(date(2222, 1, 1), date(2222, 12, 31))
            resolved = lazy_boolean(0.4)
            report = lazy_faker_sentence(8)

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander", "isSafetyOfficer"]
        __update__ = ["isCommander", "isSafetyOfficer"]
        __delete__ = ["isCommander"]
