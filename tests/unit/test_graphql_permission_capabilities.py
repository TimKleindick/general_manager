from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from collections.abc import Sequence
from typing import Any, ClassVar, cast
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings

from general_manager.api.graphql import GraphQL
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.graphql_capabilities import (
    CapabilityEvaluationContext,
    GraphQLPermissionCapability,
    clear_capability_context,
    get_capability_context,
    get_graphql_capabilities,
    mutation_capability,
    object_capability,
    permission_capability,
)


@dataclass
class DummyManager:
    identification: dict[str, Any]

    def __hash__(self) -> int:
        """Return a stable hash derived from the test identification code."""
        return hash(self.identification["code"])


class PolicyBackendUnavailable(RuntimeError):
    pass


class DeniedPermission(PermissionError):
    pass


class TestCurrentUserCapabilityProvider:
    graphql_fields: ClassVar[dict[str, type]] = {"email": str}
    graphql_capabilities: ClassVar[tuple[GraphQLPermissionCapability, ...]] = (
        object_capability(
            "canViewProfile", lambda user, request_user: user is request_user
        ),
    )


class GraphQLPermissionCapabilityTests(SimpleTestCase):
    def test_object_capability_is_public_permission_api(self) -> None:
        """
        Verify the public permission package re-exports the object capability helper.
        """
        from general_manager.permission import object_capability as public_helper

        self.assertIs(public_helper, object_capability)

    def test_object_capability_evaluates_once_per_operation_cache_key(self) -> None:
        """
        Verify object capability results are cached per operation identity.

        The evaluator records each invocation so the assertion can prove repeated
        evaluations for the same declaration and instance reuse the cached result.
        """
        calls: list[str] = []

        def can_rename(instance: DummyManager, user: Any) -> bool:
            """Record the evaluated manager code and allow the capability."""
            calls.append(instance.identification["code"])
            return True

        declaration = object_capability("canRename", can_rename)
        instance = DummyManager({"code": "ALPHA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        self.assertTrue(context.evaluate(declaration, instance))
        self.assertTrue(context.evaluate(declaration, instance))

        self.assertEqual(calls, ["ALPHA"])

    def test_object_capability_denies_and_caches_evaluator_errors(self) -> None:
        """
        Verify evaluator exceptions deny the capability and are cached.

        The broken evaluator increments a counter before raising so repeated
        evaluations can prove the deny-on-error result is stored.
        """
        calls = 0

        def broken_evaluator(instance: DummyManager, user: Any) -> bool:
            """Count the evaluation attempt and simulate a policy backend failure."""
            nonlocal calls
            calls += 1
            raise PolicyBackendUnavailable

        declaration = object_capability("canRename", broken_evaluator)
        instance = DummyManager({"code": "ALPHA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        self.assertFalse(context.evaluate(declaration, instance))
        self.assertFalse(context.evaluate(declaration, instance))

        self.assertEqual(calls, 1)

    def test_object_capability_batch_evaluator_warms_cache(self) -> None:
        """
        Verify batch evaluators can populate cached object capability results.

        The per-object evaluator returns false, so true results after warmup show
        that the batch output was used instead of falling back.
        """
        calls: list[list[str]] = []

        def can_rename_batch(
            instances: Sequence[Any],
            user: Any,
        ) -> dict[DummyManager, bool]:
            """Record the warmed manager codes and allow each provided instance."""
            managers = cast(Sequence[DummyManager], instances)
            calls.append([instance.identification["code"] for instance in managers])
            return {instance: True for instance in managers}

        declaration = object_capability(
            "canRename",
            lambda _instance, _user: False,
            batch_evaluator=can_rename_batch,
        )
        first = DummyManager({"code": "ALPHA"})
        second = DummyManager({"code": "BETA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        context.warm([declaration], [first, second])

        self.assertTrue(context.evaluate(declaration, first))
        self.assertTrue(context.evaluate(declaration, second))
        self.assertEqual(calls, [["ALPHA", "BETA"]])

    def test_permission_capability_allows_manager_without_permission(self) -> None:
        """
        Verify permission-backed capabilities allow managers without Permission classes.
        """

        class UnprotectedManager:
            """Manager stub with no nested Permission class."""

            pass

        declaration = permission_capability(UnprotectedManager, "create")

        self.assertEqual(declaration.name, "createUnprotectedManager")
        self.assertTrue(
            declaration.evaluator(DummyManager({"code": "ALPHA"}), AnonymousUser())
        )

    def test_permission_capability_delegates_create_update_and_delete(self) -> None:
        """
        Verify CRUD permission capabilities call the matching permission entrypoint.

        The test manager records create, update, and delete calls to prove payload
        resolution and action dispatch are wired through the real helper.
        """
        calls: list[tuple[str, dict[str, Any]]] = []

        class ProtectedManager:
            """Manager stub with a recording Permission class."""

            class Permission:
                """Permission stub that records CRUD checks for assertions."""

                @staticmethod
                def check_create_permission(
                    payload: dict[str, Any],
                    target: type[Any],
                    user: Any,
                ) -> None:
                    """Record create permission payloads."""
                    del target, user
                    calls.append(("create", payload))

                @staticmethod
                def check_update_permission(
                    payload: dict[str, Any],
                    instance: Any,
                    user: Any,
                ) -> None:
                    """Record update permission payloads."""
                    del instance, user
                    calls.append(("update", payload))

                @staticmethod
                def check_delete_permission(instance: Any, user: Any) -> None:
                    """Record delete permission checks."""
                    del instance, user
                    calls.append(("delete", {}))

        instance = DummyManager({"code": "ALPHA"})
        user = AnonymousUser()

        create_capability = permission_capability(
            ProtectedManager,
            "create",
            payload={"name": "Apollo"},
        )
        update_capability = permission_capability(
            ProtectedManager,
            "update",
            payload=lambda target, _user: {"code": target.identification["code"]},
        )
        delete_capability = permission_capability(ProtectedManager, "delete")

        self.assertTrue(create_capability.evaluator(instance, user))
        self.assertTrue(update_capability.evaluator(instance, user))
        self.assertTrue(delete_capability.evaluator(instance, user))
        self.assertEqual(
            calls,
            [
                ("create", {"name": "Apollo"}),
                ("update", {"code": "ALPHA"}),
                ("delete", {}),
            ],
        )

    def test_permission_capability_denies_permission_errors(self) -> None:
        """
        Verify permission-backed capabilities return false when permissions fail.
        """

        class ProtectedManager:
            """Manager stub with a denying Permission class."""

            class Permission:
                """Permission stub that denies delete checks."""

                @staticmethod
                def check_delete_permission(instance: Any, user: Any) -> None:
                    """Raise a permission denial for delete operations."""
                    del instance, user
                    raise DeniedPermission

        declaration = permission_capability(ProtectedManager, "delete")

        self.assertFalse(
            declaration.evaluator(DummyManager({"code": "ALPHA"}), AnonymousUser())
        )

    def test_mutation_capability_allows_unguarded_mutation(self) -> None:
        """
        Verify mutation-backed capabilities allow mutations with no permission class.
        """

        def archive_project() -> None:
            """Mutation stub used to carry GraphQL mutation metadata."""
            return None

        archive_project._general_manager_mutation_permission = None  # type: ignore[attr-defined]
        declaration = mutation_capability(cast(type[Any], archive_project))

        self.assertEqual(declaration.name, "archiveProject")
        self.assertTrue(
            declaration.evaluator(DummyManager({"code": "ALPHA"}), AnonymousUser())
        )

    def test_mutation_capability_denies_permission_errors(self) -> None:
        """
        Verify mutation-backed capabilities return false on permission errors.
        """

        class ArchivePermission:
            """Mutation permission stub that always denies."""

            @staticmethod
            def check(payload: dict[str, Any], user: Any) -> None:
                """Raise a permission denial for the provided payload."""
                del payload, user
                raise DeniedPermission

        def archive_project() -> None:
            """Mutation stub used to carry GraphQL mutation metadata."""
            return None

        archive_project._general_manager_mutation_permission = ArchivePermission  # type: ignore[attr-defined]
        declaration = mutation_capability(
            cast(type[Any], archive_project),
            payload={"status": "locked"},
        )

        self.assertFalse(
            declaration.evaluator(DummyManager({"code": "ALPHA"}), AnonymousUser())
        )

    def test_batch_warm_skips_empty_uncacheable_and_cached_inputs(self) -> None:
        """
        Verify warmup skips empty inputs, unbatchable declarations, and cached rows.
        """
        batch_calls = 0

        def can_rename_batch(
            instances: Sequence[Any],
            user: Any,
        ) -> list[bool]:
            """Count batch evaluations and return an allow result."""
            nonlocal batch_calls
            del instances, user
            batch_calls += 1
            return [True]

        no_batch = object_capability("canView", lambda _instance, _user: True)
        with_batch = object_capability(
            "canRename",
            lambda _instance, _user: False,
            batch_evaluator=can_rename_batch,
        )
        instance = DummyManager({"code": "ALPHA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        context.warm([with_batch], [])
        context.warm([no_batch], [instance])
        context.warm([with_batch], [instance])
        context.warm([with_batch], [instance])

        self.assertEqual(batch_calls, 1)
        self.assertTrue(context.evaluate(with_batch, instance))

    def test_batch_warm_logs_and_falls_back_after_batch_errors(self) -> None:
        """
        Verify batch warmup logs failures and leaves per-object fallback available.
        """
        calls = 0

        def broken_batch(instances: Sequence[Any], user: Any) -> list[bool]:
            """Simulate a failing batch policy backend."""
            del instances, user
            raise PolicyBackendUnavailable

        def evaluator(instance: DummyManager, user: Any) -> bool:
            """Count fallback object evaluations and allow the capability."""
            nonlocal calls
            del instance, user
            calls += 1
            return True

        declaration = object_capability(
            "canRename",
            evaluator,
            batch_evaluator=broken_batch,
        )
        instance = DummyManager({"code": "ALPHA"})
        context = CapabilityEvaluationContext(user=AnonymousUser())

        with mock.patch(
            "general_manager.permission.graphql_capabilities.logger"
        ) as logger_mock:
            context.warm([declaration], [instance])

        logger_mock.warning.assert_called_once()
        self.assertTrue(context.evaluate(declaration, instance))
        self.assertEqual(calls, 1)

    def test_get_graphql_capabilities_filters_invalid_declarations(self) -> None:
        """
        Verify capability discovery returns only valid GraphQL capability declarations.
        """
        valid = object_capability("canRename", lambda _instance, _user: True)

        class Project:
            """Manager stub with mixed valid and invalid capability declarations."""

            class Permission:
                """Permission stub exposing GraphQL capability declarations."""

                graphql_capabilities = (valid, object())

        self.assertEqual(get_graphql_capabilities(Project), (valid,))

    def test_get_and_clear_capability_context_share_operation_storage(self) -> None:
        """
        Verify operation contexts are reused until explicitly cleared.
        """
        info = SimpleNamespace(
            context=SimpleNamespace(user=AnonymousUser()),
            operation=object(),
        )

        first = get_capability_context(info)
        second = get_capability_context(info)
        clear_capability_context(info)
        third = get_capability_context(info)

        self.assertIs(first, second)
        self.assertIsNot(first, third)

    def test_get_capability_context_returns_uncached_context_when_storage_fails(
        self,
    ) -> None:
        """
        Verify context creation falls back when request context storage is immutable.
        """

        class FrozenContext:
            """Context stub that rejects dynamic attribute storage."""

            user = AnonymousUser()

            def __setattr__(self, name: str, value: Any) -> None:
                """Reject attempts to store operation-scoped cache state."""
                del name, value
                raise RuntimeError("frozen")

        info = SimpleNamespace(context=FrozenContext(), operation=object())

        first = get_capability_context(info)
        second = get_capability_context(info)

        self.assertIsInstance(first, CapabilityEvaluationContext)
        self.assertIsInstance(second, CapabilityEvaluationContext)
        self.assertIsNot(first, second)

    def test_clear_capability_context_ignores_missing_storage(self) -> None:
        """
        Verify clearing capability context is a no-op when storage is absent.
        """
        info = SimpleNamespace(context=SimpleNamespace(), operation=object())

        clear_capability_context(info)

    def test_anonymous_context_default_user_identity(self) -> None:
        """
        Verify an omitted user defaults to an anonymous capability user.
        """
        context = CapabilityEvaluationContext()
        declaration = GraphQLPermissionCapability(
            "canView",
            lambda _instance, user: not user.is_authenticated,
        )

        self.assertTrue(context.evaluate(declaration, DummyManager({"code": "ALPHA"})))

    def test_graphql_capability_type_is_cached_and_resolves_declaration(self) -> None:
        """
        Verify generated object capability GraphQL types are cached and resolvable.
        """

        class Project(GeneralManager):
            """Manager stub used for generated object capability type naming."""

            pass

        declaration = object_capability("canView", lambda _instance, _user: True)
        GraphQL.reset_registry()

        capability_type = GraphQL._get_or_create_capability_type(
            Project,
            (declaration,),
        )
        cached_type = GraphQL._get_or_create_capability_type(Project, (declaration,))
        resolver = capability_type.resolve_canView
        info = SimpleNamespace(
            context=SimpleNamespace(user=AnonymousUser()),
            operation=object(),
        )

        self.assertIs(capability_type, cached_type)
        self.assertTrue(resolver({"instance": DummyManager({"code": "ALPHA"})}, info))

    def test_current_user_capability_type_is_cached_and_resolves_declaration(
        self,
    ) -> None:
        """
        Verify generated current-user capability types are cached and resolvable.
        """
        user = AnonymousUser()
        declaration = object_capability(
            "canViewProfile",
            lambda current_user, request_user: current_user is request_user,
        )
        GraphQL.reset_registry()

        capability_type = GraphQL._get_or_create_current_user_capability_type(
            (declaration,)
        )
        cached_type = GraphQL._get_or_create_current_user_capability_type(
            (declaration,)
        )
        resolver = capability_type.resolve_canViewProfile
        info = SimpleNamespace(context=SimpleNamespace(user=user), operation=object())

        self.assertIs(capability_type, cached_type)
        self.assertTrue(resolver({"instance": user}, info))

    @override_settings(GENERAL_MANAGER={})
    def test_current_user_capability_provider_is_optional(self) -> None:
        """
        Verify current-user capability provider lookup returns none when unset.
        """
        self.assertIsNone(GraphQL._get_current_user_capability_provider())

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER": (
                "tests.unit.test_graphql_permission_capabilities."
                "TestCurrentUserCapabilityProvider"
            )
        }
    )
    def test_register_current_user_capabilities_uses_provider_fields(self) -> None:
        """
        Verify provider-backed `me` fields resolve through generated query fields.
        """
        user = SimpleNamespace(email="apollo@example.com")
        GraphQL.reset_registry()

        GraphQL.register_current_user_capabilities()
        me_resolver = GraphQL._query_fields["resolve_me"]
        me_field_resolver = GraphQL._query_fields["me"].type.resolve_email

        self.assertIs(
            me_resolver(None, SimpleNamespace(context=SimpleNamespace(user=user))), user
        )
        self.assertEqual(
            me_field_resolver(
                user, SimpleNamespace(context=SimpleNamespace(user=user))
            ),
            "apollo@example.com",
        )
