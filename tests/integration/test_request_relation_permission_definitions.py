"""Enforce request relation field gates through either submitted alias."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestMutationOperation,
    RequestQueryOperation,
    RequestTransportConfig,
    RequestTransportResponse,
    SharedRequestTransport,
)
from general_manager.manager import GeneralManager, Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission import ManagerBasedPermission
from general_manager.permission.base_permission import PermissionCheckError


class PermissionDefinitionTransport(SharedRequestTransport):
    """Record actual writes separately from permitted detail reads."""

    def __init__(self) -> None:
        """Start each permission scenario with an unchanged remote owner."""
        self.payload = {"id": 10, "ownerId": 1}
        self.mutation_bodies = []

    def send(self, request, *, interface_cls, operation, plan, identification):
        """Apply only mutation bodies and return the current remote representation."""
        if plan.action in {"create", "update"}:
            body = dict(request.body or {})
            self.mutation_bodies.append(body)
            self.payload.update(body)
        return RequestTransportResponse(payload=dict(self.payload), status_code=200)


def serialize_permission_owner(values):
    """Prefer the canonical object so conflicting aliases must be reconciled first."""
    if "owner" in values:
        owner = values["owner"]
        return {"ownerId": None if owner is None else owner.id}
    return {"ownerId": values["owner_id"]}


class TestRequestRelationPermissionDefinitions(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Register actual request managers without replacing their permission engine."""
        super().setUpClass()
        cls.managers = []
        cls.addClassCleanup(cls._unregister_managers)

        class RequestFieldPermissionOwner(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                label = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation(
                            name="detail", path="/owners/{id}"
                        )
                    }

        cls.managers.append(RequestFieldPermissionOwner)

        def decode_owner(value):
            """Resolve the remote owner ID through the declared optional relation."""
            return None if value is None else RequestFieldPermissionOwner(id=value)

        class RequestFieldPermissionRecord(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                owner = RequestField(
                    RequestFieldPermissionOwner | None,
                    source="ownerId",
                    normalizer=decode_owner,
                    is_editable=True,
                    is_required=False,
                )

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation(
                            name="detail", path="/records/{id}"
                        )
                    }
                    create_operation = RequestMutationOperation(
                        name="create", method="POST", path="/records"
                    )
                    update_operation = RequestMutationOperation(
                        name="update", method="PATCH", path="/records/{id}"
                    )
                    create_serializer = serialize_permission_owner
                    update_serializer = serialize_permission_owner
                    transport_config = RequestTransportConfig(
                        base_url="https://permission-definitions.example.test"
                    )

            class Permission(ManagerBasedPermission):
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]

        cls.managers.append(RequestFieldPermissionRecord)
        cls.Owner = RequestFieldPermissionOwner
        cls.Record = RequestFieldPermissionRecord
        cls.original_permission = cls.Record.Permission
        for manager in cls.managers:
            manager._attributes = manager.Interface.get_attributes()
            GeneralManagerMeta.create_at_properties_for_attributes(
                manager._attributes.keys(), manager
            )

    @classmethod
    def _unregister_managers(cls) -> None:
        """Remove only these fixtures from process-wide manager registration queues."""
        for registry_name in (
            "all_classes",
            "pending_graphql_interfaces",
            "pending_attribute_initialization",
        ):
            registry = getattr(GeneralManagerMeta, registry_name)
            registry[:] = [
                manager for manager in registry if manager not in cls.managers
            ]

    def setUp(self) -> None:
        """Restore the fixture permission class even if a matrix assertion fails."""
        super().setUp()
        self.addCleanup(setattr, self.Record, "Permission", self.original_permission)
        self.user = AnonymousUser()

    def _permission_class(self, definitions):
        """Use real field definitions with permissive independent operation gates."""
        return type(
            "RequestRelationFieldPermission",
            (ManagerBasedPermission,),
            {"__create__": ["public"], "__update__": ["public"], **definitions},
        )

    def _payload(self, spelling, winner):
        """Give a losing canonical target to paired writes to test effective-value gates."""
        if spelling == "owner":
            return {"owner": self.Owner(id=winner)}
        if spelling == "owner_id":
            return {"owner_id": winner}
        other = 3 if winner == 2 else 2
        pairs = [("owner", self.Owner(id=other)), ("owner_id", winner)]
        return dict(reversed(pairs) if spelling == "id_first" else pairs)

    def _assert_mutation(self, action, spelling, winner, *, allowed):
        """Assert authorization against the selected target and the real upstream write."""
        transport = PermissionDefinitionTransport()
        self.Record.Interface.transport = transport
        payload = self._payload(spelling, winner)
        if allowed:
            if action == "create":
                record = self.Record.create(creator_id=self.user, **payload)
            else:
                record = self.Record(id=10).update(creator_id=self.user, **payload)
            self.assertEqual(transport.mutation_bodies, [{"ownerId": winner}])
            self.assertEqual(record.owner.id, winner)
        else:
            with self.assertRaises(PermissionCheckError):
                if action == "create":
                    self.Record.create(creator_id=self.user, **payload)
                else:
                    self.Record(id=10).update(creator_id=self.user, **payload)
            self.assertEqual(transport.mutation_bodies, [])
            self.assertEqual(transport.payload["ownerId"], 1)

    def test_single_alias_definition_gates_every_write_spelling(self) -> None:
        """A field gate declared on either alias follows both names and keeps list OR."""
        expressions = ["matches:owner_id:99", "matches:owner_id:2"]
        for declared_alias in ("owner", "owner_id"):
            self.Record.Permission = self._permission_class(
                {declared_alias: {"create": expressions, "update": expressions}}
            )
            for action in ("create", "update"):
                for spelling in ("owner", "owner_id", "owner_first", "id_first"):
                    for winner in (2, 3):
                        with self.subTest(
                            defined=declared_alias,
                            action=action,
                            spelling=spelling,
                            winner=winner,
                        ):
                            self._assert_mutation(
                                action, spelling, winner, allowed=winner == 2
                            )

    def test_opposing_alias_definitions_are_both_required(self) -> None:
        """Submitting only one alias cannot bypass the other alias's same-action gate."""
        self.Record.Permission = self._permission_class(
            {
                "owner": {
                    "create": ["matches:owner_id:99", "matches:owner_id:2"],
                    "update": ["matches:owner_id:99", "matches:owner_id:2"],
                },
                "owner_id": {
                    "create": ["matches:owner_id:3", "matches:owner_id:98"],
                    "update": ["matches:owner_id:3", "matches:owner_id:98"],
                },
            }
        )
        for action in ("create", "update"):
            for spelling in ("owner", "owner_id", "owner_first", "id_first"):
                for winner in (2, 3):
                    with self.subTest(action=action, spelling=spelling, winner=winner):
                        self._assert_mutation(action, spelling, winner, allowed=False)

    def test_identical_alias_definitions_do_not_change_allowed_outcome(self) -> None:
        """The same alternative list on both aliases still permits its matching branch."""
        expressions = ["matches:owner_id:99", "matches:owner_id:2"]
        self.Record.Permission = self._permission_class(
            {
                "owner": {"create": expressions, "update": expressions},
                "owner_id": {"create": expressions, "update": expressions},
            }
        )
        for action in ("create", "update"):
            for spelling in ("owner", "owner_id"):
                with self.subTest(action=action, spelling=spelling):
                    self._assert_mutation(action, spelling, 2, allowed=True)
