from __future__ import annotations

from typing import assert_type

from django.db import models

from general_manager.measurement import Measurement, MeasurementField


class MeasurementConsumer(models.Model):
    length = MeasurementField(base_unit="meter")

    class Meta:
        app_label = "typing_consumer"


field = MeasurementField(base_unit="meter")
assert_type(
    field.__get__(None, MeasurementConsumer),
    MeasurementField | Measurement | None,
)
