"""Chat uses configured GraphQL authorization with each actual request context."""

from types import SimpleNamespace

from django.test import override_settings
import graphene
from graphql import GraphQLError
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.chat.graphql_contract import clear_contract_cache
from general_manager.chat.tools import ScopeChatContext, mutate, query


class TenantGate:
    def resolve(self, next_, root, info, **args):
        if info.field_name in {"partList", "renamePart"}:
            context = info.context
            if context.tenant != context.user.tenant or not context.user.allowed:
                message = "permission denied"
                raise GraphQLError(message)
        return next_(root, info, **args)


class NativeRequestGate:
    def resolve(self, next_, root, info, **args):
        if info.context.auth_expected_context is not info.context:
            message = "native request required"
            raise GraphQLError(message)
        return next_(root, info, **args)


class AnonymousContextGate:
    def resolve(self, next_, root, info, **args):
        if info.context is None or info.context.user is not None:
            message = "anonymous Python context required"
            raise GraphQLError(message)
        return next_(root, info, **args)


@pytest.fixture
def execution_schema(monkeypatch):
    writes = []

    class Part(graphene.ObjectType):
        code = graphene.String()

    class Info(graphene.ObjectType):
        total_count = graphene.Int()

    class Page(graphene.ObjectType):
        items = graphene.List(Part)
        page_info = graphene.Field(Info)

    class Query(graphene.ObjectType):
        part_list = graphene.Field(Page, page_size=graphene.Int(), page=graphene.Int())

        def resolve_part_list(self, info, **_args):
            return {
                "items": [{"code": info.context.user.username}],
                "page_info": {"total_count": 1},
            }

    class MutationResult(graphene.ObjectType):
        success = graphene.Boolean()

    class Mutation(graphene.ObjectType):
        rename_part = graphene.Field(MutationResult, name=graphene.String())

        def resolve_rename_part(self, info, name):
            writes.append((info.context.user.username, name))
            return {"success": True}

    monkeypatch.setattr(
        GraphQL, "_schema", graphene.Schema(query=Query, mutation=Mutation)
    )
    monkeypatch.setattr(GraphQL, "graphql_type_registry", {"Part": Part})
    monkeypatch.setattr(
        GraphQL, "manager_registry", {"Part": SimpleNamespace(chat_exposed=True)}
    )
    clear_contract_cache()
    yield writes
    clear_contract_cache()


def test_http_scope_preserves_native_request_context_attributes():
    from general_manager.chat.views import _request_scope

    request = SimpleNamespace(user=object(), tenant="tenant-a", META={})
    scope = _request_scope(request)
    context = ScopeChatContext.from_scope(scope)
    assert context.user is request.user
    assert context.tenant == request.tenant


def test_graphql_middleware_denies_users_without_cached_access_decisions(
    execution_schema,
):
    middleware = "tests.unit.test_chat_execution_context_r29.TenantGate"
    with override_settings(GRAPHENE={"MIDDLEWARE": [middleware]}):
        for username, allowed in [("alice", True), ("bob", False), ("carol", True)]:
            user = SimpleNamespace(
                username=username, allowed=allowed, tenant="tenant-a"
            )
            context = ScopeChatContext.from_scope({"user": user, "tenant": "tenant-a"})
            if allowed:
                result = query(
                    manager="Part", fields=["code"], filters={}, context=context
                )
                assert result["data"] == [{"code": username}]
            else:
                with pytest.raises(ValueError, match="permission denied"):
                    query(manager="Part", fields=["code"], filters={}, context=context)


def test_graphql_middleware_denies_a_different_tenant(execution_schema):
    middleware = "tests.unit.test_chat_execution_context_r29.TenantGate"
    user = SimpleNamespace(username="alice", allowed=True, tenant="tenant-a")
    context = ScopeChatContext.from_scope({"user": user, "tenant": "tenant-b"})
    with override_settings(GRAPHENE={"MIDDLEWARE": [middleware]}):
        with pytest.raises(ValueError, match="permission denied"):
            query(manager="Part", fields=["code"], filters={}, context=context)


def test_http_execution_uses_the_actual_request_object(execution_schema):
    user = SimpleNamespace(username="alice")
    request = SimpleNamespace(user=user)
    request.auth_expected_context = request
    context = ScopeChatContext.from_scope({"user": user, "request": request})
    with override_settings(
        GRAPHENE={
            "MIDDLEWARE": [
                "tests.unit.test_chat_execution_context_r29.NativeRequestGate"
            ]
        }
    ):
        assert query(manager="Part", fields=["code"], filters={}, context=context)[
            "data"
        ] == [{"code": "alice"}]


def test_request_and_scope_cannot_supply_different_users(execution_schema):
    scope_user = SimpleNamespace(username="alice")
    request = SimpleNamespace(user=SimpleNamespace(username="bob"))
    context = ScopeChatContext.from_scope({"user": scope_user, "request": request})
    with pytest.raises(ValueError, match="different user"):
        query(manager="Part", fields=["code"], filters={}, context=context)


def test_python_query_without_context_retains_anonymous_middleware_authorization(
    execution_schema,
):
    schema = GraphQL.get_schema()
    schema.graphql_schema.query_type.fields["partList"].resolve = (
        lambda _root, _info, **_args: {
            "items": [{"code": "anonymous"}],
            "page_info": {"total_count": 1},
        }
    )
    clear_contract_cache()
    with override_settings(
        GRAPHENE={
            "MIDDLEWARE": [
                "graphene_django.debug.DjangoDebugMiddleware",
                "tests.unit.test_chat_execution_context_r29.AnonymousContextGate",
            ]
        }
    ):
        assert query(manager="Part", fields=["code"], filters={})["data"] == [
            {"code": "anonymous"}
        ]


@pytest.mark.parametrize("serialization_raises", [False, True])
@pytest.mark.django_db
def test_debug_sql_instrumentation_ends_with_the_chat_execution(
    execution_schema, serialization_raises
):
    from django.db import connections
    from graphql import GraphQLScalarType

    schema = GraphQL.get_schema()
    connections_before = {
        item.alias: (item.cursor, hasattr(item, "_graphene_cursor"))
        for item in connections.all()
    }
    context = ScopeChatContext(user=SimpleNamespace(username="alice"))
    if serialization_raises:

        def denied(_value):
            message = "serialization failed"
            raise GraphQLError(message)

        denied_type = GraphQLScalarType("DeniedCode", serialize=denied)
        schema.graphql_schema.type_map["DeniedCode"] = denied_type
        schema.graphql_schema.get_type("Part").fields["code"].type = denied_type
        clear_contract_cache()
    with override_settings(
        GRAPHENE={"MIDDLEWARE": ["graphene_django.debug.DjangoDebugMiddleware"]}
    ):
        if serialization_raises:
            with pytest.raises(ValueError, match="serialization failed"):
                query(manager="Part", fields=["code"], filters={}, context=context)
        else:
            assert query(manager="Part", fields=["code"], filters={}, context=context)[
                "data"
            ] == [{"code": "alice"}]
    assert not hasattr(context, "django_debug")
    for item in connections.all():
        cursor, instrumented = connections_before[item.alias]
        assert item.cursor == cursor
        assert hasattr(item, "_graphene_cursor") is instrumented


@pytest.mark.django_db
@pytest.mark.parametrize("same_context", [False, True])
def test_chat_debug_cleanup_preserves_an_outer_instrumentation_owner(
    execution_schema, same_context
):
    from django.db import connections
    from graphene_django.debug.middleware import DjangoDebugContext

    outer = DjangoDebugContext()
    cursors = {item.alias: item.cursor for item in connections.all()}
    context = ScopeChatContext(user=SimpleNamespace(username="alice"))
    if same_context:
        context.django_debug = outer
    try:
        with override_settings(
            GRAPHENE={"MIDDLEWARE": ["graphene_django.debug.DjangoDebugMiddleware"]}
        ):
            assert query(manager="Part", fields=["code"], filters={}, context=context)[
                "data"
            ] == [{"code": "alice"}]
        if same_context:
            assert context.django_debug is outer
        else:
            assert not hasattr(context, "django_debug")
        for item in connections.all():
            assert item.cursor is cursors[item.alias]
            assert hasattr(item, "_graphene_cursor")
    finally:
        outer.disable_instrumentation()


def test_confirmed_mutation_retains_native_request_and_graphql_authorization(
    execution_schema,
):
    configuration = {
        "CHAT": {
            "allowed_mutations": ["renamePart"],
            "confirm_mutations": ["renamePart"],
        }
    }
    middleware = [
        "tests.unit.test_chat_execution_context_r29.TenantGate",
        "tests.unit.test_chat_execution_context_r29.NativeRequestGate",
    ]
    with override_settings(
        GENERAL_MANAGER=configuration, GRAPHENE={"MIDDLEWARE": middleware}
    ):
        for username, allowed in [("alice", True), ("bob", False)]:
            user = SimpleNamespace(
                username=username,
                allowed=allowed,
                tenant="tenant-a",
                is_authenticated=True,
            )
            request = SimpleNamespace(user=user, tenant="tenant-a")
            request.auth_expected_context = request
            context = ScopeChatContext.from_scope({"user": user, "request": request})
            before = list(execution_schema)
            result = mutate(
                mutation="renamePart", input={"name": "Bolt"}, context=context
            )
            assert result["status"] == "confirmation_required"
            assert execution_schema == before
            if allowed:
                result = mutate(
                    mutation="renamePart",
                    input={"name": "Bolt"},
                    confirmed=True,
                    context=context,
                )
                assert result["status"] == "executed"
            else:
                with pytest.raises(ValueError, match="permission denied"):
                    mutate(
                        mutation="renamePart",
                        input={"name": "Bolt"},
                        confirmed=True,
                        context=context,
                    )
                assert execution_schema == before
    assert execution_schema == [("alice", "Bolt")]
