from django.db.models import (
    CharField,
    TextField,
    DateField,
    IntegerField,
    ForeignKey,
    CASCADE,
)
from django.core.validators import RegexValidator
from generalManager.src.manager.generalManager import GeneralManager
from generalManager.src.manager.interface import DatabaseInterface
from generalManager.src.measurement import (
    MeasurementField,
    Measurement,
)
from typing import Optional
from generalManager.src.rule.rule import Rule
from django.db.models.constraints import UniqueConstraint
from generalManager.src.factory.lazy_methods import (
    LazyMeasurement,
    LazyDeltaDate,
    LazyProjectName,
)


class Project(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        number = CharField(max_length=7, validators=[RegexValidator(r"^AP\d{4,5}$")])
        description = TextField(null=True, blank=True)
        start_date = DateField(null=True, blank=True)
        end_date = DateField(null=True, blank=True)
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

        class Meta:
            constraints = [
                UniqueConstraint(fields=["name", "number"], name="unique_booking")
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

    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        estimated_weight = MeasurementField(base_unit="kg", null=True, blank=True)
        estimated_volume = IntegerField(null=True, blank=True)
        project = ForeignKey("Project", on_delete=CASCADE)

    @property
    def estimated_shipment(self):
        if self.estimated_weight is None or self.estimated_volume is None:
            return None
        return self.estimated_weight * self.estimated_volume
