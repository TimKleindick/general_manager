from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.tools import mutate


class _Result:
    def __init__(self, data=None, errors=None) -> None:
        self.data = data
        self.errors = errors


class _RecordingSchema:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def execute(self, query_text: str, context_value=None):  # type: ignore[no-untyped-def]
        self.calls.append({"query": query_text, "context": context_value})
        return self.result


class _User:
    is_authenticated = True
    id = 7


class ChatMutationToolTests(SimpleTestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()

    def tearDown(self) -> None:
        GraphQL.reset_registry()
        super().tearDown()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_mutations": ["createPart"],
            }
        }
    )
    def test_mutate_executes_allowed_mutation(self) -> None:
        schema = _RecordingSchema(
            _Result(
                data={
                    "createPart": {
                        "success": True,
                        "Part": {"id": "123", "name": "Bolt"},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]
        context = SimpleNamespace(user=_User())

        result = mutate(
            mutation="createPart",
            input={"name": "Bolt", "active": True},
            context=context,
        )

        assert result == {
            "status": "executed",
            "data": {"success": True, "Part": {"id": "123", "name": "Bolt"}},
        }
        assert schema.calls[0]["context"] is context
        query_text = str(schema.calls[0]["query"])
        assert "mutation ChatMutation" in query_text
        assert 'createPart(name: "Bolt", active: true)' in query_text
        assert "{ success }" in query_text

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_mutations": ["createPart"],
            }
        }
    )
    def test_mutate_rejects_non_allowlisted_mutation(self) -> None:
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(ValueError, match=r"Mutation 'deletePart' is not allowed\."):
            mutate(
                mutation="deletePart",
                input={"id": "1"},
                context=SimpleNamespace(user=_User()),
            )

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_mutations": ["createPart"],
            }
        }
    )
    def test_mutate_requires_authenticated_user(self) -> None:
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(
            ValueError, match=r"Chat mutations require an authenticated user\."
        ):
            mutate(
                mutation="createPart",
                input={"name": "Bolt"},
                context=SimpleNamespace(user=AnonymousUser()),
            )

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_mutations": ["createPart"],
                "confirm_mutations": ["createPart"],
            }
        }
    )
    def test_mutate_returns_confirmation_required_for_confirmed_mutations(self) -> None:
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        result = mutate(
            mutation="createPart",
            input={"name": "Bolt"},
            context=SimpleNamespace(user=_User()),
        )

        assert result == {
            "status": "confirmation_required",
            "mutation": "createPart",
            "input": {"name": "Bolt"},
        }

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_mutations": ["createPart"],
                "confirm_mutations": ["createPart"],
            }
        }
    )
    def test_mutate_executes_confirmed_mutation_after_user_confirmation(self) -> None:
        schema = _RecordingSchema(_Result(data={"createPart": {"success": True}}))
        GraphQL._schema = schema  # type: ignore[assignment]

        result = mutate(
            mutation="createPart",
            input={"name": "Bolt"},
            context=SimpleNamespace(user=_User()),
            confirmed=True,
        )

        assert result == {"status": "executed", "data": {"success": True}}
        assert 'createPart(name: "Bolt")' in str(schema.calls[0]["query"])

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_mutations": ["createPart"],
            }
        }
    )
    def test_mutate_surfaces_graphql_errors(self) -> None:
        GraphQL._schema = _RecordingSchema(  # type: ignore[assignment]
            _Result(errors=[SimpleNamespace(message="permission denied")])
        )

        with pytest.raises(ValueError, match="permission denied"):
            mutate(
                mutation="createPart",
                input={"name": "Bolt"},
                context=SimpleNamespace(user=_User()),
            )
