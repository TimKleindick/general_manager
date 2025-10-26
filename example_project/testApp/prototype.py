from __future__ import annotations
import random
import numpy as np
from datetime import date
from typing import Optional, cast, Any
from django.db.models import (
    CharField,
    TextField,
    DateField,
    IntegerField,
    ForeignKey,
    CASCADE,
    constraints,
    BooleanField,
)
from django.core.validators import RegexValidator

from general_manager.interface.calculation_interface import CalculationInterface
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.manager import GeneralManager, graph_ql_property, Input
from general_manager.permission import ManagerBasedPermission
from general_manager.measurement import (
    MeasurementField,
    Measurement,
)
from general_manager.rule import Rule
from general_manager.factory import (
    lazy_measurement,
    lazy_delta_date,
    lazy_project_name,
)
from general_manager.utils import none_to_zero
from general_manager.api.mutation import graph_ql_mutation
from general_manager.interface.read_only_interface import ReadOnlyInterface


class ProjectType(GeneralManager):
    name: str
    description: Optional[str]

    _data = [
        {
            "name": "Aquisition Project",
            "description": "A project that is used to acquire new customers or projects.",
        },
        {
            "name": "Development Project",
            "description": "A project that is used to develop new products or services.",
        },
        {
            "name": "Research Project",
            "description": "A project that is used to research new technologies or methods.",
        },
        {
            "name": "Marketing Project",
            "description": "A project that is used to market products or services.",
        },
        {
            "name": "Sales Project",
            "description": "A project that is used to sell products or services.",
        },
        {
            "name": "Support Project",
            "description": "A project that is used to support customers or projects.",
        },
        {
            "name": "Training Project",
            "description": "A project that is used to train employees or customers.",
        },
        {
            "name": "Consulting Project",
            "description": "A project that is used to consult customers or projects.",
        },
    ]

    class Interface(ReadOnlyInterface):
        name = CharField(max_length=50, unique=True)
        description = TextField(null=True, blank=True)


class Project(GeneralManager):
    name: str
    start_date: Optional[date]
    end_date: Optional[date]
    total_capex: Optional[Measurement]
    derivative_list: DatabaseBucket[Derivative]

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
                Rule["Project"](
                    lambda x: cast(date, x.start_date) < cast(date, x.end_date)
                ),
                Rule["Project"](lambda x: cast(Measurement, x.total_capex) >= "0 EUR"),
            ]

        class Factory:
            name = lazy_project_name()
            end_date = lazy_delta_date(365 * 6, "start_date")
            total_capex = lazy_measurement(75_000, 1_000_000, "EUR")

    class Permission(ManagerBasedPermission):
        __read__ = ["ends_with:name:X-771", "public"]
        __create__ = ["admin", "isMatchingKeyAccount"]
        __update__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]
        __delete__ = ["admin", "isMatchingKeyAccount", "isProjectTeamMember"]

        total_capex = {"update": ["isSalesResponsible", "isProjectManager"]}


class Derivative(GeneralManager):
    name: str
    estimated_weight: Optional[Measurement]
    estimated_volume: Optional[int]
    project: Project
    price: Optional[Measurement]
    derivativevolume_list: DatabaseBucket[DerivativeVolume]

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        estimated_weight = MeasurementField(base_unit="kg", null=True, blank=True)
        estimated_volume = IntegerField(null=True, blank=True)
        project = ForeignKey("Project", on_delete=CASCADE)
        price = MeasurementField(base_unit="EUR", null=True, blank=True)

    @graph_ql_property(sortable=True, filterable=True)
    def estimated_shipment(self) -> Optional[Measurement]:
        if self.estimated_weight is None or self.estimated_volume is None:
            return None
        return self.estimated_weight * self.estimated_volume

    class Permission(ManagerBasedPermission):
        __based_on__ = "project"

        name = {"read": ["ends_with:name:AAA"]}


def generate_volume_distribution(years: int, total_volume: float) -> list[float]:
    """
    Generate a yearly volume distribution with a random peak year and normalize it to a specified total volume.

    Parameters:
        years (int): Number of years over which to distribute the volume.
        total_volume (float): The total volume to be distributed across all years.

    Returns:
        list[float]: A list of yearly volumes summing to total_volume.
    """
    peak_year = random.randint(1, years // 3)
    volumes = np.zeros(years)
    for year in range(peak_year):
        volumes[year] = (year / peak_year) ** 2 + random.uniform(0, 0.05)

    for year in range(peak_year, years):
        volumes[year] = max(0, (years - year) / (years - peak_year)) + random.uniform(
            0, 0.05
        )

    volumes = volumes / np.sum(volumes) * total_volume
    return cast(list[float], volumes.tolist())


def generate_volume(**kwargs: Any) -> list[dict[str, Any]]:
    """
    Generates a list of derivative volume records distributed across the years of a project's duration.

    Returns:
        A list of dictionaries, each containing the provided keyword arguments merged with a specific year (`date`) and the corresponding generated `volume`. Returns an empty list if required data is missing from the derivative or its project.
    """
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
            _adjustmentMethod = generate_volume


def get_possible_dates(project: Project) -> list[date]:
    dates = []
    for derivative in project.derivative_list:
        for volume in derivative.derivativevolume_list:
            if not isinstance(volume.date, date):
                continue
            dates.append(volume.date)

    return sorted(dates)


def get_possible_projects():
    return Project.exclude(derivative__derivativevolume__isnull=True)


class ProjectCommercial(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input(
            Project,
            possible_values=get_possible_projects,
        )
        date = Input(date, possible_values=get_possible_dates)

    @graph_ql_property(sortable=True)
    def total_volume(self) -> int | float | Measurement:
        return sum(
            none_to_zero(volume.volume)
            for derivative in self.project.derivative_list
            for volume in derivative.derivativevolume_list.filter(date=self.date)
        )

    @graph_ql_property(filterable=True, sortable=True)
    def total_shipment(self) -> Optional[Measurement]:
        total = sum(
            none_to_zero(derivative.estimated_weight) * none_to_zero(volume.volume)
            for derivative in self.project.derivative_list
            for volume in derivative.derivativevolume_list.filter(date=self.date)
        )
        if isinstance(total, Measurement):
            return total
        return None

    @graph_ql_property
    def total_revenue(self) -> Optional[Measurement]:
        total = sum(
            none_to_zero(derivative.price) * none_to_zero(volume.volume)
            for derivative in self.project.derivative_list
            for volume in derivative.derivativevolume_list.filter(date=self.date)
        )
        if isinstance(total, Measurement):
            return total
        return None


@graph_ql_mutation
def start_project(
    info,
    project_name: str,
    project_number: str,
    derivative_name: str,
    derivative_weight: Measurement,
    derivative_volume: int,
    start_date: Optional[date],
    end_date: Optional[date],
) -> tuple[Project, Derivative]:
    project = Project.create(
        name=project_name,
        number=project_number,
        start_date=start_date or date.today(),
        end_date=end_date or date.today(),
        creator_id=info.context.user.id,
    )
    derivative = Derivative.create(
        name=derivative_name,
        estimated_weight=derivative_weight,
        estimated_volume=derivative_volume,
        project=project,
        creator_id=info.context.user.id,
    )
    return project, derivative
