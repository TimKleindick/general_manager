from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Annotated, Any

import graphene
import pytest

from general_manager.api.graphql import GraphQL, MeasurementType
from general_manager.api.graphql_output import (
    GraphQLOutputAnnotationError,
    create_output_field_resolver,
    map_graphql_output_annotation,
    resolve_output_type_hints,
)
from general_manager.api.graphql_type import (
    GraphQLType,
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement


class User(GeneralManager):
    pass


class UserType(graphene.ObjectType):
    name = graphene.String()


class Summary(GraphQLType):
    title: str


class SummaryType(graphene.ObjectType):
    title = graphene.String()


@pytest.fixture(autouse=True)
def restore_graphql_type_registry() -> None:
    snapshot = get_registered_graphql_types()
    yield
    _restore_registered_graphql_types(snapshot)


def map_annotation(annotation: object):
    return map_graphql_output_annotation(
        annotation,
        owner_name="Envelope",
        field_name="value",
        manager_registry={"User": User},
        manager_type_registry={"User": UserType},
        output_class_registry={"Summary": Summary},
        output_type_registry={"Summary": SummaryType},
        measurement_type=MeasurementType,
        scalar_mapper=GraphQL._map_field_to_graphene_base_type,
    )


def test_collection_preserves_both_nullability_levels() -> None:
    required = map_annotation(list[User])
    nullable_items = map_annotation(list[User | None])
    nullable_list = map_annotation(list[User] | None)

    assert isinstance(required.field.type, graphene.NonNull)
    assert isinstance(required.field.type.of_type, graphene.List)
    assert isinstance(required.field.type.of_type.of_type, graphene.NonNull)
    assert isinstance(nullable_items.field.type, graphene.NonNull)
    assert not isinstance(nullable_items.field.type.of_type.of_type, graphene.NonNull)
    assert not isinstance(nullable_list.field.type, graphene.NonNull)


@pytest.mark.parametrize(
    ("annotation", "graphene_type"),
    [
        (str, graphene.String),
        (bool, graphene.Boolean),
        (int, graphene.Int),
        (float, graphene.Float),
        (Decimal, graphene.Float),
        (datetime, graphene.DateTime),
        (date, graphene.Date),
    ],
)
def test_supported_scalar_subclasses_use_their_graphene_scalar(
    annotation: type,
    graphene_type: type,
) -> None:
    mapped = map_annotation(annotation)

    assert isinstance(mapped.field.type, graphene.NonNull)
    assert mapped.field.type.of_type is graphene_type
    assert mapped.resolver_type is annotation


def test_direct_manager_uses_live_generated_type_registry() -> None:
    mapped = map_annotation(User)

    assert isinstance(mapped.field.type, graphene.NonNull)
    assert mapped.field.type.of_type is UserType
    assert mapped.resolver_type is User


def test_nested_output_type_uses_output_registry_and_value_resolver_type() -> None:
    mapped = map_annotation(list[Summary | None])

    assert mapped.resolver_type is Summary
    assert isinstance(mapped.field.type, graphene.NonNull)
    assert isinstance(mapped.field.type.of_type, graphene.List)
    assert not isinstance(mapped.field.type.of_type.of_type, graphene.NonNull)
    assert mapped.field.type.of_type.of_type is SummaryType


@pytest.mark.parametrize("annotation", [tuple[User, ...], set[User]])
def test_supported_collection_shapes_map_to_graphene_lists(annotation: object) -> None:
    mapped = map_annotation(annotation)

    assert isinstance(mapped.field.type, graphene.NonNull)
    assert isinstance(mapped.field.type.of_type, graphene.List)
    assert isinstance(mapped.field.type.of_type.of_type, graphene.NonNull)
    assert mapped.field.type.of_type.of_type.of_type is UserType
    assert mapped.resolver_type is User


def test_measurement_maps_to_measurement_object_with_target_unit_argument() -> None:
    mapped = map_annotation(Measurement)

    assert isinstance(mapped.field.type, graphene.NonNull)
    assert mapped.field.type.of_type is MeasurementType
    assert "target_unit" in mapped.field.args
    assert mapped.resolver_type is Measurement


@pytest.mark.parametrize(
    "annotation",
    [
        list,
        set,
        tuple,
        tuple[int, str],
        list[Any],
        list[Annotated[int, "metadata"]],
        int | str,
        int | str | None,
        object,
        Any,
        Annotated[int, "metadata"],
        dict,
    ],
)
def test_unsupported_annotations_name_owner_field_and_annotation(
    annotation: object,
) -> None:
    with pytest.raises(GraphQLOutputAnnotationError) as error:
        map_annotation(annotation)

    message = str(error.value)
    assert "Envelope.value" in message
    assert repr(annotation) in message


def test_registered_names_resolve_in_type_hints() -> None:
    class Envelope:
        value: "Summary"
        users: list["User"]

    hints = resolve_output_type_hints(
        Envelope,
        manager_registry={"User": User},
        output_class_registry={"Summary": Summary},
    )

    assert hints == {"value": Summary, "users": list[User]}


def test_unresolved_forward_reference_names_owner() -> None:
    class Envelope:
        value: "MissingSummary"  # noqa: F821

    with pytest.raises(GraphQLOutputAnnotationError) as error:
        resolve_output_type_hints(
            Envelope,
            manager_registry={"User": User},
            output_class_registry={"Summary": Summary},
        )

    assert "Envelope" in str(error.value)
    assert "MissingSummary" in str(error.value)


def test_output_measurement_resolver_converts_without_manager_access_checks() -> None:
    parent = SimpleNamespace(
        amount=Measurement(1000, "meter"),
        _ensure_as_of_compatible=lambda: (_ for _ in ()).throw(
            AssertionError("output values must not use manager as-of checks")
        ),
    )
    resolver = create_output_field_resolver("amount", Measurement)

    assert resolver(parent, None, target_unit="kilometer") == {
        "value": 1,
        "unit": "kilometer",
    }


def test_output_normal_resolver_reads_dataclass_value_without_info() -> None:
    parent = SimpleNamespace(title="Summary")
    resolver = create_output_field_resolver("title", str)

    assert resolver(parent, None) == "Summary"
