from __future__ import annotations

from datetime import date

from django.db.models import CharField, DateField, FloatField

from general_manager.factory import lazy_choice, lazy_date_between
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.rule import Rule


class MissionSchedule(GeneralManager):
    name: str
    window_start: date
    window_end: date
    resupply_eta: date
    backlog_ratio: float
    status: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=120)
        window_start = DateField()
        window_end = DateField()
        resupply_eta = DateField()
        backlog_ratio = FloatField()
        status = CharField(max_length=40)

        class Meta:
            rules = [
                Rule["MissionSchedule"](lambda x: x.window_end >= x.window_start),
                Rule["MissionSchedule"](lambda x: 0 <= x.backlog_ratio <= 2),
            ]

        class Factory:
            name = lazy_choice(
                [
                    "Outer Rim Supply Run",
                    "Hydro Loop Rotation",
                    "Deep Dock Window",
                    "Corellian Relay",
                ]
            )
            window_start = lazy_date_between(date(2222, 6, 1), date(2223, 2, 1))
            window_end = lazy_date_between(date(2223, 2, 2), date(2223, 6, 1))
            resupply_eta = lazy_date_between(date(2222, 6, 1), date(2223, 6, 1))
            backlog_ratio = 0.8
            status = lazy_choice(["planned", "active", "delayed", "closed"])

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander"]
        __update__ = ["isCommander"]
        __delete__ = ["isCommander"]
