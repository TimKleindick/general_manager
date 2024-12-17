from __future__ import annotations
import random
import numpy as np
from datetime import date
from typing import Optional, Any
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
from generalManager.src.interface import (
    Bucket,
    DatabaseInterface,
    CalculationInterface,
)
from generalManager.src.manager import GeneralManager, graphQlProperty, Input
from generalManager.src.measurement import (
    MeasurementField,
    Measurement,
)
from generalManager.src.rule import Rule
from generalManager.src.factory import (
    LazyMeasurement,
    LazyDeltaDate,
    LazyProjectName,
)
from generalManager.src.auxiliary import noneToZero


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
                Rule["Project"](lambda x: x.start_date < x.end_date),
                Rule["Project"](lambda x: x.total_capex >= "0 EUR"),
            ]

        class Factory:
            name = LazyProjectName()
            end_date = LazyDeltaDate(365 * 6, "start_date")
            total_capex = LazyMeasurement(75_000, 1_000_000, "EUR")

    # class Permission(ManagerBasedPermission):
    #     __read__ = ["public"]
    #     __create__ = ["admin", "isMatchingKeyAccount"]
    #     __update__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]
    #     __delete__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]

    #     total_capex = {"update": ["isSalesResponsible", "isProjectManager"]}


class Derivative(GeneralManager):
    name: str
    estimated_weight: Optional[Measurement]
    estimated_volume: Optional[int]
    project: Project
    price: Optional[Measurement]
    derivativevolume_list: Bucket[DerivativeVolume]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        estimated_weight = MeasurementField(base_unit="kg", null=True, blank=True)
        estimated_volume = IntegerField(null=True, blank=True)
        project = ForeignKey("Project", on_delete=CASCADE)
        price = MeasurementField(base_unit="EUR", null=True, blank=True)

    @graphQlProperty
    def estimated_shipment(self) -> Optional[Measurement]:
        if self.estimated_weight is None or self.estimated_volume is None:
            return None
        return self.estimated_weight * self.estimated_volume

    # class Permission(ManagerBasedPermission):
    #     __based_on__ = project


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
    total_volume = getattr(derivative, "estimated_volume")
    project = getattr(derivative, "project")
    if project is None:
        return []
    start_date: date | None = project.start_date
    end_date: date | None = project.end_date
    if total_volume is None or start_date is None or end_date is None:
        return []
    total_years = end_date.year - start_date.year
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
    volume: int

    class Interface(DatabaseInterface):
        derivative = ForeignKey("Derivative", on_delete=CASCADE)
        date = DateField()
        volume = IntegerField()

        class Meta:
            constraints = [
                constraints.UniqueConstraint(
                    fields=["derivative", "date"], name="unique_volume"
                )
            ]
            rules = [Rule["DerivativeVolume"](lambda x: x.volume >= 0)]

        class Factory:
            _adjustmentMethod = generateVolume


def getPossibleDates(project: Project):
    dates = []
    for derivative in project.derivative_list:
        for volume in derivative.derivativevolume_list:
            volume: DerivativeVolume
            dates.append(volume.date)

    return sorted(dates)


class ProjectCommercial(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input(
            Project,
            possible_values=lambda: Project.exclude(
                derivative__derivativevolume__isnull=True
            ),
        )
        date = Input(date, possible_values=getPossibleDates)

    @graphQlProperty
    def total_volume(self) -> int | float | Measurement:
        return sum(
            noneToZero(volume.volume)
            for derivative in self.project.derivative_list
            for volume in derivative.derivativevolume_list.filter(date=self.date)
        )

    @graphQlProperty
    def total_shipment(self) -> Optional[Measurement]:
        total = sum(
            noneToZero(derivative.estimated_weight) * noneToZero(volume.volume)
            for derivative in self.project.derivative_list
            for volume in derivative.derivativevolume_list.filter(date=self.date)
        )
        if isinstance(total, Measurement):
            return total
        return None

    @graphQlProperty
    def total_revenue(self) -> Optional[Measurement]:
        total = sum(
            noneToZero(derivative.price) * noneToZero(volume.volume)
            for derivative in self.project.derivative_list
            for volume in derivative.derivativevolume_list.filter(date=self.date)
        )
        if isinstance(total, Measurement):
            return total
        return None
