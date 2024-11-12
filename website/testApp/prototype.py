from __future__ import annotations
from django.db.models import (
    CharField,
    TextField,
    DateField,
    IntegerField,
    ForeignKey,
    CASCADE,
    constraints,
)
from django.core.validators import RegexValidator
from generalManager.src.manager.generalManager import GeneralManager
from generalManager.src.manager.bucket import Bucket
from generalManager.src.interface import DatabaseInterface
from generalManager.src.measurement import (
    MeasurementField,
    Measurement,
)
from typing import Optional, Any
from generalManager.src.rule.rule import Rule
from generalManager.src.factory.lazy_methods import (
    LazyMeasurement,
    LazyDeltaDate,
    LazyProjectName,
)
from generalManager.src.api.graphql import graphQlProperty
import random
import numpy as np
from datetime import date

# from generalManager.src.intermediate.generalIntermediate import Intermediate, Input


class Project(GeneralManager):
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    total_capex: Optional[Measurement]
    derivative_list: list[Derivative]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        number = CharField(max_length=7, validators=[RegexValidator(r"^AP\d{4,5}$")])
        description = TextField(null=True, blank=True)
        start_date = DateField(null=True, blank=True)
        end_date = DateField(null=True, blank=True)
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

        class Meta:
            constraints = [
                constraints.UniqueConstraint(
                    fields=["name", "number"], name="unique_booking"
                )
            ]

            rules = [
                Rule(lambda x: x.start_date < x.end_date),
                Rule(lambda x: x.total_capex >= "0 EUR"),
            ]

        class Factory:
            name = LazyProjectName()
            end_date = LazyDeltaDate(365 * 6, "start_date")
            total_capex = LazyMeasurement(75_000, 1_000_000, "EUR")


class Derivative(GeneralManager):
    name: str
    estimated_weight: Optional[Measurement]
    estimated_volume: Optional[int]
    project: Project
    price: Optional[Measurement]
    derivative_volume_list: Bucket[DerivativeVolume]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        estimated_weight = MeasurementField(base_unit="kg", null=True, blank=True)
        estimated_volume = IntegerField(null=True, blank=True)
        project = ForeignKey("Project", on_delete=CASCADE)  # type: ignore
        price = MeasurementField(base_unit="EUR", null=True, blank=True)

    @graphQlProperty
    def estimated_shipment(self) -> Optional[Measurement]:
        if self.estimated_weight is None or self.estimated_volume is None:
            return None
        return self.estimated_weight * self.estimated_volume


def generate_volume_distribution(years: int, total_volume: float) -> list[float]:
    peak_year = random.randint(1, years // 3)
    volumes = np.zeros(years)
    for year in range(peak_year):
        volumes[year] = (year / peak_year) ** 2 + random.uniform(0, 0.05)

    for year in range(peak_year, years):
        volumes[year] = max(0, (years - year) / (years - peak_year)) + random.uniform(
            0, 0.05
        )

    volumes = volumes / np.sum(volumes) * total_volume
    return volumes.tolist()


def generateVolume(**kwargs: dict[str, Any]) -> list[dict[str, Any]]:

    derivative = kwargs["derivative"]
    total_volume = derivative.get("estimated_volume")
    start_date: date | None = derivative.get("project", {}).get("start_date")
    end_date: date | None = derivative.get("project", {}).get("end_date")
    if total_volume is None or start_date is None or end_date is None:
        return []
    total_years = start_date.year - end_date.year
    volumes = generate_volume_distribution(total_years, total_volume)
    records: list[dict[str, Any]] = []
    for year, volume in enumerate(volumes, start=start_date.year):
        records.append(
            {
                **kwargs,
                "date": date.fromisoformat(f"{year}-01-01"),
                "volume": volume,
            }
        )
    return records


class DerivativeVolume(GeneralManager):
    derivative: Derivative
    date: date
    volume: Measurement

    class Interface(DatabaseInterface):
        derivative = ForeignKey("Derivative", on_delete=CASCADE)  # type: ignore
        date = DateField()
        volume = IntegerField()

        class Meta:
            constraints = [
                constraints.UniqueConstraint(
                    fields=["derivative", "date"], name="unique_volume"
                )
            ]

            Rule(lambda x: x.volume >= 0)

        class Factory:
            _adjustmentMethod = generateVolume
