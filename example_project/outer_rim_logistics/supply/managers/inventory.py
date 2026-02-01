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

from general_manager import FieldConfig, IndexConfig
from factory.declarations import LazyFunction
from general_manager.factory import (
    lazy_boolean,
    lazy_choice,
    lazy_date_between,
    lazy_integer,
    lazy_measurement,
    lazy_sequence,
)
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement, MeasurementField
from general_manager.permission import ManagerBasedPermission
from general_manager.rule import Rule
from orl.factory_utils import (
    random_module,
    random_part,
    random_vendor,
)


class InventoryItem(GeneralManager):
    serial: str
    part: "supply.PartCatalog"
    quantity: int
    location: Optional["maintenance.Module"]
    received_on: Optional[date]
    expires_on: Optional[date]
    reserved: bool

    class Interface(DatabaseInterface):
        serial = CharField(max_length=40, unique=True)
        part = ForeignKey("supply.PartCatalog", on_delete=CASCADE)
        quantity = IntegerField()
        location = ForeignKey(
            "maintenance.Module", null=True, blank=True, on_delete=SET_NULL
        )
        received_on = DateField(null=True, blank=True)
        expires_on = DateField(null=True, blank=True)
        reserved = BooleanField(default=False)

        class Meta:
            rules = [
                Rule["InventoryItem"](lambda x: x.quantity >= 0),
                Rule["InventoryItem"](
                    lambda x: x.expires_on is None
                    or x.received_on is None
                    or x.expires_on >= x.received_on
                ),
            ]

        class Factory:
            serial = lazy_sequence(start=1000, step=7)
            part = LazyFunction(random_part)
            quantity = lazy_integer(0, 200)
            location = LazyFunction(lambda: random_module())
            received_on = lazy_date_between(date(2222, 1, 1), date(2222, 12, 31))
            expires_on = lazy_date_between(date(2223, 1, 1), date(2224, 12, 31))
            reserved = lazy_boolean(0.2)

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander", "isSafetyOfficer"]
        __update__ = ["isCommander", "isSafetyOfficer"]
        __delete__ = ["isCommander"]

        quantity = {"update": ["isCommander", "isSafetyOfficer"]}

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=[
                    FieldConfig(name="serial", boost=1.8),
                    "part__name",
                    "part__part_number",
                    "location__name",
                ],
                filters=["reserved", "location__name"],
                sorts=["serial"],
                boost=1.1,
            )
        ]


class CargoManifest(GeneralManager):
    tracking_code: str
    vendor: "supply.VendorCatalog"
    eta_date: date
    destination_module: Optional["maintenance.Module"]
    status: str
    priority: bool
    total_mass: Measurement
    total_volume: Measurement

    class Interface(DatabaseInterface):
        tracking_code = CharField(max_length=40, unique=True)
        vendor = ForeignKey("supply.VendorCatalog", on_delete=CASCADE)
        eta_date = DateField()
        destination_module = ForeignKey(
            "maintenance.Module", null=True, blank=True, on_delete=SET_NULL
        )
        status = CharField(max_length=30)
        priority = BooleanField(default=False)
        total_mass = MeasurementField(base_unit="kg")
        total_volume = MeasurementField(base_unit="l")

        class Meta:
            rules = [
                Rule["CargoManifest"](
                    lambda x: x.eta_date >= date.today() - timedelta(days=30)
                ),
            ]

        class Factory:
            tracking_code = lazy_sequence(start=5000, step=11)
            vendor = LazyFunction(random_vendor)
            eta_date = lazy_date_between(date(2222, 1, 1), date(2223, 12, 31))
            destination_module = LazyFunction(lambda: random_module())
            status = lazy_choice(["enroute", "docked", "delayed", "unloading"])
            priority = lazy_boolean(0.25)
            total_mass = lazy_measurement(800, 2400, "kg")
            total_volume = lazy_measurement(120, 600, "l")

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["isCommander"]
        __update__ = ["isCommander", "isSafetyOfficer"]
        __delete__ = ["isCommander"]
