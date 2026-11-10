from collections.abc import Iterator
from dataclasses import field
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

import graphene
import pytest
from django.core.exceptions import ValidationError

from general_manager import GraphQLType
from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_type import (
    _restore_registered_graphql_types,
    get_registered_graphql_types,
)
from general_manager.api.mutation import graph_ql_mutation


@pytest.fixture(autouse=True)
def isolate_mutations(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    declarations = get_registered_graphql_types()
    monkeypatch.setattr(GraphQL, "_mutations", {})
    monkeypatch.setattr(GraphQL, "_value_input_type_cache", {})
    yield
    _restore_registered_graphql_types(declarations)


def _schema() -> graphene.Schema:
    class Query(graphene.ObjectType):
        placeholder = graphene.Boolean()

    mutation = type(
        "Mutation",
        (graphene.ObjectType,),
        {name: registered.Field() for name, registered in GraphQL._mutations.items()},
    )
    return graphene.Schema(query=Query, mutation=mutation)


def test_nested_value_inputs_preserve_nullability_and_dataclass_defaults() -> None:
    class WorkItem(GraphQLType):
        reference: UUID = field(metadata={"is_identifier": True})
        children: "list[WorkItem | None]"
        quantity: Decimal
        note: str | None
        labels: set[str] = field(default_factory=set)
        priorities: tuple[int, ...] = (1,)
        summary: str = field(init=False, default="computed")
        category: ClassVar[str] = "work"

    received: list[WorkItem | None] = []

    @graph_ql_mutation
    def schedule(item: WorkItem | None) -> bool:
        received.append(item)
        return True

    schema = _schema()
    input_type = schema.graphql_schema.get_type("WorkItemInput")
    assert {name: str(field.type) for name, field in input_type.fields.items()} == {
        "reference": "ID!",
        "children": "[WorkItemInput]!",
        "quantity": "Float!",
        "note": "String",
        "labels": "[String!]",
        "priorities": "[Int!]",
    }
    reference = "5b0f19cb-1670-4c76-b8eb-683fc1c020b0"
    result = schema.execute(
        """
        mutation Schedule($ref: ID!) {
            omitted: schedule { success }
            explicitNull: schedule(item: null) { success }
            submitted: schedule(item: {
                reference: $ref, quantity: 3.5, children: [
                    null, {reference: $ref, quantity: 1, children: []}
                ], priorities: [2, 3]
            }) { success }
        }
        """,
        variable_values={"ref": reference},
    )

    assert result.errors is None
    assert received[:2] == [None, None]
    parent = received[2]
    assert parent is not None
    assert parent.reference == UUID(reference)
    assert parent.quantity == Decimal("3.5")
    assert isinstance(parent.quantity, Decimal)
    assert parent.note is None
    assert parent.priorities == (2, 3)
    assert parent.summary == "computed"
    null_item, child = parent.children
    assert null_item is None
    assert child is not None
    assert child.priorities == (1,)
    assert child.labels == parent.labels == set()
    assert child.labels is not parent.labels


def test_invalid_input_does_not_reach_mutation_resolver() -> None:
    class PlanOptions(GraphQLType):
        owner_id: int = field(metadata={"is_identifier": True})
        title: str = "Plan"

        def __post_init__(self) -> None:
            if self.owner_id < 1:
                message = "The owner ID must be positive."
                raise ValidationError(message)

    received: list[PlanOptions] = []

    @graph_ql_mutation
    def submit(options: PlanOptions) -> bool:
        received.append(options)
        return True

    schema = _schema()
    for fields, message in (
        ('ownerId: "abc"', "PlanOptions.owner_id must be a valid int ID."),
        ('ownerId: "1", title: null', "PlanOptions.title cannot be null."),
        ("ownerId: 0", "The owner ID must be positive."),
    ):
        result = schema.execute(
            f"mutation {{ submit(options: {{ {fields} }}) {{ success }} }}"
        )
        assert result.errors
        assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}
        assert message in result.errors[0].message
    assert received == []

    result = schema.execute(
        'mutation { submit(options: {ownerId: "9007199254740993"}) { success } }'
    )
    assert result.errors is None
    assert received == [PlanOptions(owner_id=9007199254740993)]


def test_generated_input_cannot_shadow_the_framework_checksum() -> None:
    class Checksum(GraphQLType):
        value: str

    @graph_ql_mutation
    def verify(checksum: Checksum) -> bool:
        return bool(checksum.value)

    with pytest.raises(TypeError, match=r"ChecksumInput.*reserved for file uploads"):
        _schema()
