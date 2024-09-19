from django.db.models import CharField, TextField, DateField
from django.core.validators import RegexValidator
from generalManager.src.manager.generalManager import GeneralManager
from generalManager.src.manager.interface import DatabaseInterface
from generalManager.src.measurement.measurementField import MeasurementField
from django.db.models.constraints import UniqueConstraint


class Project(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=50)
        number = CharField(max_length=7, validators=[RegexValidator(r"^AP\d{5}$")])
        description = TextField()
        start_date = DateField()
        end_date = DateField()
        total_capex = MeasurementField(base_unit="EUR", null=True, blank=True)

        class Meta:
            constraints = [
                UniqueConstraint(fields=["name", "number"], name="unique_booking")
            ]
