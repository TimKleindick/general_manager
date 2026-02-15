from __future__ import annotations

from typing import ClassVar, Optional

from django.db.models import CharField, IntegerField, TextField

from general_manager.interface import ReadOnlyInterface
from general_manager.manager import GeneralManager


class ProjectUserRole(GeneralManager):
    id: int
    name: str

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "program management"},
        {"id": 2, "name": "sales"},
        {"id": 3, "name": "industrialization"},
        {"id": 4, "name": "product development"},
        {"id": 5, "name": "quality"},
        {"id": 6, "name": "procurement"},
        {"id": 7, "name": "logistics"},
        {"id": 8, "name": "welding supervisor"},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)

        class Meta:
            app_label = "core"


class ProjectPhaseType(GeneralManager):
    id: int
    name: str
    description: Optional[str]

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "request for information", "description": None},
        {"id": 2, "name": "request for quotation", "description": None},
        {"id": 3, "name": "nomination", "description": "acquired officially"},
        {"id": 4, "name": "production line release", "description": None},
        {"id": 5, "name": "tool release", "description": None},
        {"id": 6, "name": "series production", "description": None},
        {"id": 7, "name": "spare parts", "description": None},
        {"id": 8, "name": "end of production", "description": None},
        {"id": 9, "name": "internal", "description": None},
        {"id": 10, "name": "lost", "description": None},
        {"id": 11, "name": "prototypes", "description": None},
        {"id": 12, "name": "acquisition", "description": None},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=150, unique=True)
        description = TextField(null=True, blank=True)

        class Meta:
            app_label = "core"


class ProjectType(GeneralManager):
    id: int
    name: str

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "sustaining"},
        {"id": 2, "name": "growth"},
        {"id": 3, "name": "existing"},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)

        class Meta:
            app_label = "core"


class Currency(GeneralManager):
    id: int
    name: str
    abbreviation: str
    symbol: str

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "euro", "abbreviation": "eur", "symbol": "EUR"},
        {"id": 2, "name": "us-dollar", "abbreviation": "usd", "symbol": "USD"},
        {"id": 3, "name": "yuan", "abbreviation": "cny", "symbol": "CNY"},
        {"id": 4, "name": "swiss franc", "abbreviation": "chf", "symbol": "CHF"},
        {"id": 5, "name": "pound", "abbreviation": "gbp", "symbol": "GBP"},
        {"id": 6, "name": "australian dollar", "abbreviation": "aud", "symbol": "AUD"},
        {"id": 7, "name": "japanese yen", "abbreviation": "jpy", "symbol": "JPY"},
        {"id": 8, "name": "czech koruna", "abbreviation": "czk", "symbol": "CZK"},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)
        abbreviation = CharField(max_length=3, unique=True)
        symbol = CharField(max_length=8)

        class Meta:
            app_label = "core"


class DerivativeType(GeneralManager):
    id: int
    name: str
    abbreviation: Optional[str]

    _data: ClassVar[list[dict[str, object]]] = [
        {"id": 1, "name": "crash management system front", "abbreviation": "CMS Front"},
        {"id": 2, "name": "crash management system rear", "abbreviation": "CMS Rear"},
        {"id": 3, "name": "strut", "abbreviation": None},
        {"id": 4, "name": "side impact beam", "abbreviation": None},
        {"id": 5, "name": "body in white", "abbreviation": "BIW"},
        {"id": 6, "name": "sill", "abbreviation": None},
        {"id": 7, "name": "other", "abbreviation": None},
        {"id": 8, "name": "battery box", "abbreviation": None},
    ]

    class Interface(ReadOnlyInterface):
        id = IntegerField(primary_key=True)
        name = CharField(max_length=255, unique=True)
        abbreviation = CharField(max_length=32, null=True, blank=True)

        class Meta:
            app_label = "core"
