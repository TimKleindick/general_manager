"""ORM measurement default normalization and validation contracts."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import models
from django.test.utils import isolate_apps

from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    build_field_descriptors,
)
from general_manager.measurement import Measurement, MeasurementField


@pytest.mark.parametrize(
    ("default", "base_unit", "expected"),
    [
        ("100 mm", "mm", "100 millimeter"),
        ("0 mm", "mm", "0 millimeter"),
        ("10 cm", "mm", "10 centimeter"),
        ("0.01 dimensionless", "dimensionless", "0.01"),
    ],
)
@pytest.mark.parametrize("inherited", [False, True])
def test_measurement_metadata_normalizes_string_defaults(
    default, base_unit, expected, inherited
):
    with isolate_apps():

        class DefaultModel(models.Model):
            amount = MeasurementField(base_unit=base_unit, default=default)

            class Meta:
                app_label = "field_descriptor_tests"

        model = DefaultModel
        if inherited:

            class InheritedModel(DefaultModel):
                class Meta:
                    app_label = "field_descriptor_tests"

            model = InheritedModel
        descriptors = build_field_descriptors(SimpleNamespace(_model=model))
        normalized = descriptors["amount"].metadata["default"]
        assert isinstance(normalized, Measurement)
        assert str(normalized) == expected
        assert DefaultModel.amount.default == default
        assert DefaultModel.amount.deconstruct()[3]["default"] == default


@pytest.mark.parametrize(
    "kind", ["measurement", "none", "unset", "callable", "invalid_callable"]
)
def test_measurement_metadata_preserves_default_identity_and_lifecycle(kind):
    calls = []

    def next_default():
        calls.append(1)
        return "100 seconds" if kind == "invalid_callable" else "100 mm"

    default = {
        "measurement": Measurement(2, "cm"),
        "none": None,
        "unset": models.NOT_PROVIDED,
        "callable": next_default,
        "invalid_callable": next_default,
    }[kind]
    with isolate_apps():

        class DefaultModel(models.Model):
            amount = MeasurementField(base_unit="mm", default=default, null=True)

            class Meta:
                app_label = "field_descriptor_tests"

        descriptors = build_field_descriptors(SimpleNamespace(_model=DefaultModel))
        assert descriptors["amount"].metadata["default"] is default
        assert calls == []
        if kind == "invalid_callable":
            with pytest.raises(ValidationError, match="Unit must be compatible"):
                DefaultModel()
            assert calls == [1]
        if kind == "callable":
            assert DefaultModel().amount == Measurement(100, "mm")
            assert calls == [1]
            assert DefaultModel(amount=None).amount is None
            assert DefaultModel(amount="2 mm").amount == Measurement(2, "mm")
            assert calls == [1]


@pytest.mark.parametrize(
    ("default", "base_unit", "message"),
    [
        ("", "mm", "Value must be a Measurement"),
        ("not-a-measurement", "mm", "Value must be a Measurement"),
        ("1 not_a_real_unit", "mm", "Value must be a Measurement"),
        (12, "mm", "Value must be a Measurement"),
        ("1 second", "mm", "Unit must be compatible"),
        ("1 EUR", "USD", "Unit must be compatible"),
        ("1 EUR", "mm", "Unit cannot be a currency"),
        ("1 mm", "EUR", "Unit must be a currency"),
    ],
)
def test_measurement_metadata_rejects_invalid_defaults(default, base_unit, message):
    with isolate_apps():

        class DefaultModel(models.Model):
            amount = MeasurementField(base_unit=base_unit, default=default)

            class Meta:
                app_label = "field_descriptor_tests"

        with pytest.raises(ValidationError, match=message):
            build_field_descriptors(SimpleNamespace(_model=DefaultModel))
        with pytest.raises(ValidationError, match=message):
            DefaultModel()


def test_measurement_metadata_rejects_incompatible_measurement_default():
    with isolate_apps():

        class DefaultModel(models.Model):
            amount = MeasurementField(base_unit="mm", default=Measurement(1, "second"))

            class Meta:
                app_label = "field_descriptor_tests"

        with pytest.raises(ValidationError, match="Unit must be compatible"):
            build_field_descriptors(SimpleNamespace(_model=DefaultModel))
