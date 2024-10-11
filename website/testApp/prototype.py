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
from generalManager.src.measurement.measurement import Measurement
from generalManager.src.rule.rule import Rule
from django.db.models.constraints import UniqueConstraint
import factory
import random
from datetime import timedelta, date
from faker import Faker

fake = Faker()


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
            name = factory.LazyAttribute(
                lambda _: (
                    f"{fake.word().capitalize()} "
                    f"{fake.word().capitalize()} "
                    f"{fake.random_element(elements=('X', 'Z', 'G'))}"
                    f"-{fake.random_int(min=1, max=1000)}"
                )
            )
            end_date = factory.LazyAttribute(
                lambda obj: (obj.start_date or date.today())
                + timedelta(days=random.randint(1, 365))
            )
            total_capex = factory.LazyAttribute(
                lambda _: Measurement(
                    str(random.uniform(0, 1_000_000))[:10], unit="EUR"
                )
            )


class Derivative(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        estimated_weight = MeasurementField(base_unit="kg", null=True, blank=True)
        estimated_volume = IntegerField(null=True, blank=True)
        project = ForeignKey("Project", on_delete=CASCADE)
