"""Mutations use schema-typed variables without embedding user values in syntax."""

from types import SimpleNamespace
import graphene
import pytest
from django.test.utils import override_settings
from general_manager.api.graphql import GraphQL
from general_manager.chat.tools import mutate


@pytest.fixture
def mutation_schema():
    GraphQL.reset_registry()
    calls = []

    class Status(graphene.Enum):
        OPEN = "OPEN"
        CLOSED = "CLOSED"

    class Details(graphene.InputObjectType):
        long_text = graphene.String()

    class Create(graphene.Mutation):
        class Arguments:
            name = graphene.String(required=True)
            status = Status(required=True)
            details = Details()

        success = graphene.Boolean()

        @staticmethod
        def mutate(root, info, **values):
            calls.append(values)
            return Create(success=True)

    class Mutation(graphene.ObjectType):
        create_part = Create.Field()

    class Query(graphene.ObjectType):
        version = graphene.String()

    GraphQL._schema = graphene.Schema(query=Query, mutation=Mutation)
    yield calls
    GraphQL.reset_registry()


@override_settings(GENERAL_MANAGER={"CHAT": {"allowed_mutations": ["createPart"]}})
@pytest.mark.parametrize(
    "text", ["line one\nline two", 'quote " slash \\ tab\t Unicode ä', "line\r\nnext"]
)
def test_multiline_and_enum_input_execute_with_exact_values(mutation_schema, text):
    result = mutate(
        mutation="createPart",
        input={"name": text, "status": "OPEN", "details": {"long_text": text}},
        context=SimpleNamespace(user=SimpleNamespace(is_authenticated=True)),
    )
    assert result == {"status": "executed", "data": {"success": True}}
    assert mutation_schema[0]["name"] == text
    assert mutation_schema[0]["status"].value == "OPEN"
    assert mutation_schema[0]["details"].long_text == text


@override_settings(GENERAL_MANAGER={"CHAT": {"allowed_mutations": ["createPart"]}})
def test_invalid_enum_is_rejected_before_mutation_resolver(mutation_schema):
    with pytest.raises(ValueError):
        mutate(
            mutation="createPart",
            input={"name": "x", "status": "INVENTED"},
            context=SimpleNamespace(user=SimpleNamespace(is_authenticated=True)),
        )
    assert mutation_schema == []
