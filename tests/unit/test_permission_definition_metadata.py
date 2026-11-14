"""Declarative permission preparation must never cache actor/object decisions."""

from abc import ABCMeta
from contextlib import contextmanager
import sys
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.test import SimpleTestCase

from general_manager.permission import manager_based_permission as policies


@contextmanager
def record_static_probes():
    """Observe real inspection calls without replacing executable authority."""
    code = policies.getattr_static.__code__
    probes = []
    previous = sys.getprofile()

    def observe(frame, event, arg):
        if event == "call" and frame.f_code is code:
            probes.append((frame.f_locals["obj"], frame.f_locals["attr"]))
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(observe)
    try:
        yield probes
    finally:
        sys.setprofile(previous)


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

        with record_static_probes() as probes:
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
        assert [name for _owner, name in probes if name == "__get__"] == []
        # Each classification still attests the inherited runtime authority.
        assert (
            sum(
                owner is Permission and name == "check_permission"
                for owner, name in probes
            )
            == 200
        )

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

        declaration = vars(Permission)["declaration"]
        with record_static_probes() as probes:
            assert (
                policies.additional_local_read_permission(Permission, "name") is False
            )
            with patch.object(Declaration, "__get__", create=True) as descriptor:
                assert (
                    policies.additional_local_read_permission(Permission, "name")
                    is None
                )
                descriptor.assert_not_called()
        assert (
            sum(owner is declaration and name == "__get__" for owner, name in probes)
            == 2
        )

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

    def test_resolved_accounting_uses_effective_row_rules(self) -> None:
        """Use the constructed policy row rules rather than the class declaration."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = []
            name: ClassVar[dict[str, list[str]]] = {"read": ["isAuthenticated"]}

        permission = Permission(SimpleNamespace(), User())
        assert policies.additional_resolved_read_permission(permission, "name") is True
        permission._read_permissions = ["isAuthenticated"]
        assert policies.additional_resolved_read_permission(permission, "name") is False
        permission._read_permissions = ["public"]
        assert policies.additional_resolved_read_permission(permission, "name") is True

    def test_resolved_accounting_rejects_unknown_state_or_cycles(self) -> None:
        """Fail conservatively for malformed or cyclic resolved permission chains."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            name: ClassVar[dict[str, list[str]]] = {"read": ["public"]}

        for key, value in (
            ("_read_permissions", None),
            ("_read_permissions", [object()]),
            ("_ConfiguredManagerPermission__attribute_permissions", {}),
            ("_ConfiguredManagerPermission__based_on_permission", object()),
        ):
            with self.subTest(key=key, value=value):
                permission = Permission(SimpleNamespace(), User())
                vars(permission)[key] = value
                assert (
                    policies.additional_resolved_read_permission(permission, "name")
                    is None
                )
        permission = Permission(SimpleNamespace(), User())
        del vars(permission)["_ConfiguredManagerPermission__based_on_permission"]
        assert policies.additional_resolved_read_permission(permission, "name") is None
        vars(permission)["_ConfiguredManagerPermission__based_on_permission"] = (
            permission
        )
        assert policies.additional_resolved_read_permission(permission, "name") is None

    def test_resolved_accounting_budgets_possible_relation_counterparts(self) -> None:
        """A counterpart declaration cannot be ignored in a resolved read proof."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            basis_id: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

        permission = Permission(SimpleNamespace(), User())
        assert policies.additional_resolved_read_permission(permission, "basis") is True
        assert (
            policies.additional_resolved_read_permission(permission, "basis_id") is True
        )

    def test_resolved_accounting_budgets_nonstandard_alias_declarations(self) -> None:
        """Custom metadata aliases remain bounded without reading metadata again."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]
            alternate_key: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

        permission = Permission(SimpleNamespace(), User())
        assert policies.additional_resolved_read_permission(permission, "basis") is True

    def test_resolved_accounting_depth_remains_bounded(self) -> None:
        """Stop proving long delegated chains at the fixed traversal bound."""

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

        current = Permission(SimpleNamespace(), User())
        root = current
        for _ in range(64):
            delegate = Permission(SimpleNamespace(), User())
            vars(current)["_ConfiguredManagerPermission__based_on_permission"] = (
                delegate
            )
            current = delegate
        assert policies.additional_resolved_read_permission(root, "name") is None

    def test_resolved_accounting_rejects_unknown_captured_rule_containers(self) -> None:
        """Reject executable captured containers without invoking their methods."""

        class UnknownRules(list):
            def __iter__(self):
                """Fail if accounting attempts to iterate this unknown rule container."""
                raise AssertionError

        class UnknownDefinition(dict):
            def __contains__(self, key):
                """Fail if accounting probes this unknown definition container."""
                raise AssertionError

        class Permission(policies.OverrideManagerPermission):
            __read__: ClassVar[list[str]] = ["public"]

        for definition in (
            UnknownDefinition(read=["public"]),
            {"read": UnknownRules(["public"])},
            {"read": [object()]},
        ):
            with self.subTest(definition=type(definition)):
                Permission.name = definition
                permission = Permission(SimpleNamespace(), User())
                Permission.name = {"read": ["public"]}
                assert (
                    policies.additional_resolved_read_permission(permission, "name")
                    is None
                )
