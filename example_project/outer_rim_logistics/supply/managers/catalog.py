from __future__ import annotations

from django.db.models import BooleanField, CASCADE, CharField, ForeignKey, IntegerField

from general_manager import FieldConfig, IndexConfig
from general_manager.interface import ReadOnlyInterface
from general_manager.manager import GeneralManager
from general_manager.measurement import Measurement, MeasurementField


class HazardClass(GeneralManager):
    code: str
    name: str
    storage_limit_per_module: int
    requires_eva: bool

    _data = [
        {
            "code": "HX-1",
            "name": "Volatile Propellant",
            "storage_limit_per_module": 12,
            "requires_eva": True,
        },
        {
            "code": "CR-2",
            "name": "Cryogenic Materials",
            "storage_limit_per_module": 8,
            "requires_eva": True,
        },
        {
            "code": "BX-3",
            "name": "Bioactive Samples",
            "storage_limit_per_module": 6,
            "requires_eva": False,
        },
    ]

    class Interface(ReadOnlyInterface):
        code = CharField(max_length=10, unique=True)
        name = CharField(max_length=80)
        storage_limit_per_module = IntegerField()
        requires_eva = BooleanField(default=False)


class VendorCatalog(GeneralManager):
    name: str
    vendor_code: str
    sector: str
    rating: int
    preferred: bool

    _data = [
        {
            "name": "Corellia Freight Guild",
            "vendor_code": "CFG",
            "sector": "Corellian Run",
            "rating": 4,
            "preferred": True,
        },
        {
            "name": "Kessel Deephaul",
            "vendor_code": "KDH",
            "sector": "Kessel",
            "rating": 3,
            "preferred": False,
        },
        {
            "name": "Bespin Cloud Yards",
            "vendor_code": "BCY",
            "sector": "Bespin",
            "rating": 5,
            "preferred": True,
        },
    ]

    class Interface(ReadOnlyInterface):
        name = CharField(max_length=120)
        vendor_code = CharField(max_length=12, unique=True)
        sector = CharField(max_length=120)
        rating = IntegerField()
        preferred = BooleanField(default=False)

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="orderable",
                fields=["name", "vendor_code", "sector"],
                filters=["preferred", "rating"],
                sorts=["name", "rating"],
            )
        ]


class PartCatalog(GeneralManager):
    part_number: str
    name: str
    description: str
    hazard_class: HazardClass
    mass: Measurement
    volume: Measurement
    unit_cost: Measurement
    reorder_threshold: int
    in_stock: bool
    preferred_vendor: VendorCatalog

    _data = [
        {
            "part_number": "CR-AL-1138",
            "name": "Corellian Alloy Plating",
            "description": "Hull reinforcement panels for deep-space runs.",
            "hazard_class": {"code": "HX-1"},
            "mass": "24 kg",
            "volume": "0.12 l",
            "unit_cost": "450 EUR",
            "reorder_threshold": 8,
            "in_stock": True,
            "preferred_vendor": {"vendor_code": "CFG"},
        },
        {
            "part_number": "BS-CO-77",
            "name": "Bespin Cloud Condenser",
            "description": "Atmospheric recycler for life-support loops.",
            "hazard_class": {"code": "CR-2"},
            "mass": "18 kg",
            "volume": "0.08 l",
            "unit_cost": "620 EUR",
            "reorder_threshold": 4,
            "in_stock": False,
            "preferred_vendor": {"vendor_code": "BCY"},
        },
        {
            "part_number": "KE-MIN-204",
            "name": "Kessel Mining Servo",
            "description": "Actuator replacement for cargo loaders.",
            "hazard_class": {"code": "BX-3"},
            "mass": "12 kg",
            "volume": "0.05 l",
            "unit_cost": "310 EUR",
            "reorder_threshold": 10,
            "in_stock": True,
            "preferred_vendor": {"vendor_code": "KDH"},
        },
    ]

    class Interface(ReadOnlyInterface):
        part_number = CharField(max_length=30, unique=True)
        name = CharField(max_length=120)
        description = CharField(max_length=255)
        hazard_class = ForeignKey("supply.HazardClass", on_delete=CASCADE)
        mass = MeasurementField(base_unit="kg")
        volume = MeasurementField(base_unit="l")
        unit_cost = MeasurementField(base_unit="EUR")
        reorder_threshold = IntegerField()
        in_stock = BooleanField(default=False)
        preferred_vendor = ForeignKey("supply.VendorCatalog", on_delete=CASCADE)

    class SearchConfig:
        indexes = [
            IndexConfig(
                name="global",
                fields=[
                    FieldConfig(name="name", boost=2.0),
                    FieldConfig(name="part_number", boost=1.6),
                    "description",
                    "hazard_class__code",
                ],
                filters=["hazard_class__code", "in_stock", "reorder_threshold"],
                sorts=["name", "part_number"],
                boost=1.2,
            ),
            IndexConfig(
                name="orderable",
                fields=["name", "part_number"],
                filters=["hazard_class__code"],
                sorts=["name"],
            ),
        ]
