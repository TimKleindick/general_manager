# type: ignore

"""Isolated real-transaction probe for synchronous GraphQL mutation handling."""

import os
import sys
from inspect import CORO_CLOSED, getcoroutinestate
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

import django

django.setup()

import graphene
from django.db import transaction

from general_manager.api.graphql_view import GeneralManagerGraphQLView


def build_view(mutation: type[graphene.ObjectType]) -> GeneralManagerGraphQLView:
    class Query(graphene.ObjectType):
        ping = graphene.String()

    view = GeneralManagerGraphQLView(
        schema=graphene.Schema(query=Query, mutation=mutation)
    )
    view.json_encode = lambda _request, payload, **_kwargs: payload
    return view


def probe_dynamic_rollback() -> None:
    events: list[str] = []
    created: list[object] = []

    class DynamicMutation(graphene.Mutation):
        Output = graphene.String

        @staticmethod
        def mutate(_root: object, _info: object) -> object:
            transaction.on_commit(lambda: events.append("committed"))

            async def mutation_body() -> str:
                events.append("mutation body")
                return "changed"

            result = mutation_body()
            created.append(result)
            return result

    class Mutation(graphene.ObjectType):
        dynamic_mutation = DynamicMutation.Field()

    with patch("graphene_django.views.graphene_settings.ATOMIC_MUTATIONS", True):
        payload, status = build_view(Mutation).get_response(
            SimpleNamespace(GET={}, method="POST"),
            {"query": "mutation Q { dynamicMutation }"},
        )

    assert status == 400
    assert payload["errors"][0]["extensions"]["code"] == "GRAPHQL_VALIDATION_FAILED"
    assert len(created) == 1
    assert getcoroutinestate(created[0]) == CORO_CLOSED
    assert events == []


def probe_sync_commit() -> None:
    events: list[str] = []

    class SyncMutation(graphene.Mutation):
        Output = graphene.String

        @staticmethod
        def mutate(_root: object, _info: object) -> str:
            events.append("mutation body")
            transaction.on_commit(lambda: events.append("committed"))
            return "changed"

    class Mutation(graphene.ObjectType):
        sync_mutation = SyncMutation.Field()

    with patch("graphene_django.views.graphene_settings.ATOMIC_MUTATIONS", True):
        payload, status = build_view(Mutation).get_response(
            SimpleNamespace(GET={}, method="POST"),
            {"query": "mutation Q { syncMutation }"},
        )

    assert status == 200
    assert payload["data"] == {"syncMutation": "changed"}
    assert events == ["mutation body", "committed"]


if __name__ == "__main__":
    probe_dynamic_rollback()
    probe_sync_commit()
