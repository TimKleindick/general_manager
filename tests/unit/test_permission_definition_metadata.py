"""Declarative permission preparation must never cache actor/object decisions."""

from abc import ABCMeta
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.test import SimpleTestCase

from general_manager.permission import manager_based_permission as policies


class TestPermissionDefinitionMetadata(SimpleTestCase):
    def test_structure_probe_never_compares_an_unknown_container_metaclass(
        self,
    ) -> None:
        class ExecutableType(type):
            def __eq__(cls, other: object) -> bool:
                raise AssertionError

        class Rules(metaclass=ExecutableType):
            pass

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

        with patch.object(Permission, "__read__", Rules()):
            assert policies._can_prepare_local_read_definitions(Permission) is False

    def test_structure_probe_does_not_normalize_dynamic_default_settings(self) -> None:
        """Structural eligibility checks must leave dynamic default settings unevaluated."""
        with patch.object(
            policies, "_get_default_permissions", side_effect=AssertionError("not lazy")
        ) as defaults:
            for permission in (
                policies.AdditiveManagerPermission,
                policies.OverrideManagerPermission,
                policies.ManagerBasedPermission,
            ):
                assert policies._can_prepare_local_read_definitions(permission) is False
        defaults.assert_not_called()

    def test_builtin_declarations_do_not_repeat_descriptor_discovery(self) -> None:
        """Repeated builtin declarations must avoid repeated descriptor discovery."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            secret: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

        with patch.object(
            policies, "getattr_static", wraps=policies.getattr_static
        ) as inspect_descriptor:
            for _ in range(100):
                assert (
                    policies.additional_local_read_permission(Permission, "name")
                    is False
                )
                assert (
                    policies.additional_local_read_permission(Permission, "secret")
                    is True
                )

        # Exact builtin values never need dynamic descriptor discovery.
        assert inspect_descriptor.call_count < 30

    def test_ordinary_mixin_changes_remain_live(self) -> None:
        class Rules:
            secret: ClassVar[dict[str, list[str]]] = {"read": ["public"]}

        class Permission(Rules, policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

        actor = AnonymousUser()
        instance = SimpleNamespace()
        previous = Permission(instance, actor)
        Rules.secret = {"read": ["isAdmin"]}
        assert previous.check_permission("read", "secret") is True
        assert Permission(instance, actor).check_permission("read", "secret") is False
        assert (
            policies.additional_local_read_permission(Permission, "secret") is not False
        )
        del Rules.secret
        assert Permission(instance, actor).check_permission("read", "secret") is True
        Rules.secret = {"read": ["isAdmin"]}
        assert Permission(instance, actor).check_permission("read", "secret") is False

    def test_independent_custom_metaclass_remains_supported(self) -> None:
        class CustomMeta(ABCMeta):
            pass

        class Permission(policies.OverrideManagerPermission, metaclass=CustomMeta):
            __read__: ClassVar[list[str]] = ["public"]

        assert policies.additional_local_read_permission(Permission, "name") is None
        assert (
            Permission(SimpleNamespace(), AnonymousUser()).check_permission(
                "read", "name"
            )
            is True
        )

    def test_declaration_value_gaining_descriptor_behavior_is_unknown(self) -> None:
        class Declaration:
            pass

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            declaration = Declaration()

        assert policies.additional_local_read_permission(Permission, "name") is False
        with patch.object(Declaration, "__get__", create=True):
            assert policies.additional_local_read_permission(Permission, "name") is None

    def test_inherited_and_mutated_definitions_remain_live(self) -> None:
        class Parent(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            secret: ClassVar[dict[str, list[str]]] = {"read": ["public"]}

        class Child(Parent):
            pass

        instance = SimpleNamespace()
        actor = AnonymousUser()

        def check(allowed: bool) -> None:
            assert policies.additional_local_read_permission(Child, "secret") is (
                not allowed
            )
            assert Child(instance, actor).check_permission("read", "secret") is allowed

        check(True)
        Parent.secret["read"][:] = ["isAdmin"]
        check(False)
        Child.secret = {"read": ["public"]}
        check(True)
        del Child.secret
        check(False)
        Parent.secret = {"read": ["public"]}
        check(True)
        del Parent.secret
        check(True)

    def test_runtime_callable_policy_is_not_proven_declarative(self) -> None:
        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

        assert policies.additional_local_read_permission(Permission, "name") is False
        with patch.object(Permission, "check_permission", return_value=False):
            assert policies.additional_local_read_permission(Permission, "name") is None
        assert policies.additional_local_read_permission(Permission, "name") is False

    def test_preparation_does_not_reuse_another_actors_result(self) -> None:
        """Prepared rule metadata must never reuse a permission decision across actors."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            secret: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

        instance = SimpleNamespace()
        for actor, allowed in (
            (User(is_superuser=True), True),
            (AnonymousUser(), False),
            (User(is_superuser=True), True),
        ):
            assert (
                policies.additional_local_read_permission(Permission, "secret") is True
            )
            assert (
                Permission(instance, actor).check_permission("read", "secret")
                is allowed
            )
