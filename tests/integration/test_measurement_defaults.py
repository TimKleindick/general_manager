"""Measurement declarations remain valid through generated GraphQL defaults."""

from decimal import Decimal
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db import models
from graphql import get_introspection_query

from general_manager.api.graphql import GraphQL
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement, MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class MeasurementDefaultsTest(GeneralManagerTransactionTestCase):
    default_calls: ClassVar[list[int]] = []

    @classmethod
    def setUpClass(cls):
        cls.default_calls.clear()

        def next_length():
            cls.default_calls.append(1)
            return f"{len(cls.default_calls)} mm"

        class MeasurementDefaults(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                length = MeasurementField(base_unit="mm", default="100 mm")
                ratio = MeasurementField(
                    base_unit="dimensionless", default="0.01 dimensionless"
                )
                object_length = MeasurementField(
                    base_unit="mm", default=Measurement(2, "cm")
                )
                optional_length = MeasurementField(
                    base_unit="mm", default=None, null=True, blank=True
                )
                dynamic_length = MeasurementField(
                    base_unit="mm", default=next_length, null=True, blank=True
                )
                unset_length = MeasurementField(base_unit="mm", null=True, blank=True)

                class Meta:
                    app_label = "general_manager"

        cls.manager = MeasurementDefaults
        cls.general_manager_classes = [MeasurementDefaults]

    def setUp(self):
        # The first setup also checks manager/schema registration did not call it.
        assert self.default_calls == []
        self.addCleanup(self.default_calls.clear)
        self.user = get_user_model().objects.create_user(
            username="measurement-defaults"
        )
        self.client.force_login(self.user)

    def test_schema_sdl_serializes_static_defaults_without_evaluating_callable(self):
        schema = GraphQL.get_schema()
        sdl = str(schema)
        assert 'length: MeasurementScalar! = "100 millimeter"' in sdl
        assert 'ratio: MeasurementScalar! = "0.01"' in sdl
        assert 'objectLength: MeasurementScalar! = "2 centimeter"' in sdl
        assert "optionalLength: MeasurementScalar = null" in sdl
        assert self.default_calls == []
        field = self.manager.Interface._model._meta.get_field("length")
        assert field.default == "100 mm"
        assert field.deconstruct()[3]["default"] == "100 mm"

    def test_schema_introspection_exposes_only_static_create_defaults(self):
        result = GraphQL.get_schema().execute(get_introspection_query())
        assert result.errors is None
        mutation = next(
            t for t in result.data["__schema"]["types"] if t["name"] == "Mutation"
        )
        create = next(
            f for f in mutation["fields"] if f["name"] == "createMeasurementDefaults"
        )
        defaults = {arg["name"]: arg["defaultValue"] for arg in create["args"]}
        assert defaults["length"] == '"100 millimeter"'
        assert defaults["ratio"] == '"0.01"'
        assert defaults["objectLength"] == '"2 centimeter"'
        assert defaults["optionalLength"] == "null"
        assert defaults["dynamicLength"] is None
        assert defaults["unsetLength"] is None
        update = next(
            f for f in mutation["fields"] if f["name"] == "updateMeasurementDefaults"
        )
        assert all(arg["defaultValue"] is None for arg in update["args"])
        assert self.default_calls == []

    def test_omitted_create_defaults_and_callable_lifecycle(self):
        for index in (1, 2):
            response = self.query(
                'mutation { createMeasurementDefaults(name: "Defaults") '
                "{ success MeasurementDefaults { id } } }"
            )
            self.assertResponseNoErrors(response)
            payload = response.json()["data"]["createMeasurementDefaults"]
            assert payload["success"] is True
            stored = self.manager.Interface._model.objects.get(
                pk=payload["MeasurementDefaults"]["id"]
            )
            assert stored.length == Measurement(100, "mm")
            assert stored.ratio == Measurement(Decimal("0.01"), "dimensionless")
            assert stored.object_length == Measurement(2, "cm")
            assert stored.dynamic_length == Measurement(index, "mm")
            assert stored.optional_length is None
            assert stored.unset_length is None
            assert len(self.default_calls) == index

    def test_explicit_null_is_stored_instead_of_callable_default(self):
        response = self.query(
            'mutation { createMeasurementDefaults(name: "Null", dynamicLength: null) '
            "{ success MeasurementDefaults { id } } }"
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["createMeasurementDefaults"]
        assert payload["success"] is True
        stored = self.manager.Interface._model.objects.get(
            pk=payload["MeasurementDefaults"]["id"]
        )
        assert stored.dynamic_length is None
        # ORM creation initializes a blank model before assigning explicit values.
        assert self.default_calls == [1]

    def test_partial_update_preserves_omission_and_accepts_explicit_null(self):
        instance = self.manager.create(
            name="Before",
            length="250 mm",
            dynamic_length="3 mm",
            optional_length="4 mm",
            creator_id=self.user.pk,
        )
        calls_after_create = list(self.default_calls)
        for mutation in (
            'mutation($id: ID!) { updateMeasurementDefaults(id: $id, name: "After") { success } }',
            "mutation($id: ID!, $length: MeasurementScalar) { updateMeasurementDefaults(id: $id, length: $length) { success } }",
        ):
            response = self.query(mutation, variables={"id": instance.id})
            self.assertResponseNoErrors(response)
            assert (
                response.json()["data"]["updateMeasurementDefaults"]["success"] is True
            )
            stored = self.manager.Interface._model.objects.get(pk=instance.id)
            assert stored.length == Measurement(250, "mm")
            assert stored.dynamic_length == Measurement(3, "mm")
            assert stored.optional_length == Measurement(4, "mm")
        response = self.query(
            "mutation($id: ID!) { updateMeasurementDefaults(id: $id, optionalLength: null) { success } }",
            variables={"id": instance.id},
        )
        self.assertResponseNoErrors(response)
        assert response.json()["data"]["updateMeasurementDefaults"]["success"] is True
        assert (
            self.manager.Interface._model.objects.get(pk=instance.id).optional_length
            is None
        )
        assert self.default_calls == calls_after_create
