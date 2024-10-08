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
from generalManager.src.measurement.measurementField import (
    MeasurementField,
)
from generalManager.src.rule.rule import Rule
from django.db.models.constraints import UniqueConstraint


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


class Derivative(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        estimated_weight = MeasurementField(base_unit="kg", null=True, blank=True)
        estimated_volume = IntegerField(null=True, blank=True)
        project = ForeignKey("Project", on_delete=CASCADE)
