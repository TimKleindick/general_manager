"""Request relation checks and adapter payloads select the same target."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import replace
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from general_manager.interface import RequestInterface
from general_manager.interface.capabilities.request.relation_inputs import (
    RequestRuleCandidate,
)
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
from general_manager.permission.permission_data_manager import PermissionDataManager
from general_manager.rule import Rule
from general_manager.rule.rule import NonexistentAttributeError


class RelationMemoryTransport(SharedRequestTransport):
    def __init__(self) -> None:
        """Start an isolated in-memory service state for each test transport."""
        self.reset()

    def reset(self) -> None:
        """Restore representative remote payloads and clear recorded mutation bodies."""
        self.payload = {
            "id": 10,
            "wire": {
                "owner": {"identifier": 1},
                "account": {"tenant": "original", "code": "one"},
            },
            "name": "Before",
            "external_id": 9,
        }
        self.bodies = []

    def send(self, request, *, interface_cls, operation, plan, identification):
        """Apply submitted values as the fake upstream and return its resulting payload."""
        if plan.action in {"create", "update"}:
            body = dict(request.body or {})
            self.bodies.append(body)
            for key, value in body.items():
                if key in {"owner", "owner_id", "ownerWire"}:
                    if isinstance(value, GeneralManager):
                        value = value.identification["id"]
                    elif isinstance(value, dict):
                        value = value["identifier"]
                    self.payload["wire"]["owner"] = (
                        None if value is None else {"identifier": value}
                    )
                elif key in {"account", "account_id"}:
                    if isinstance(value, GeneralManager):
                        value = dict(value.identification)
                    self.payload["wire"]["account"] = value
                else:
                    self.payload[key] = value
        return RequestTransportResponse(payload=dict(self.payload), status_code=200)


def serialize_owner(payload):
    """Model an adapter that writes through the canonical manager object."""
    owner = payload["owner"]
    return {"ownerWire": None if owner is None else owner.id}


def serialize_owner_id(payload):
    """Model an adapter that writes through the explicit identifier alias."""
    return {"ownerWire": payload["owner_id"]}


class TestRequestRelationMutations(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """Declare request managers with simple, composite, and optional relationships."""
        super().setUpClass()

        class RequestRelationTarget(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                label = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation(
                            name="detail", path="/targets/{id}"
                        )
                    }

        class RequestRelationAccount(GeneralManager):
            class Interface(RequestInterface):
                tenant = Input(type=str)
                code = Input(type=str)
                identification_fields = ("tenant", "code")
                label = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation(
                            name="detail", path="/accounts/{tenant}/{code}"
                        )
                    }

        def decode_owner(value):
            """Translate the nested remote owner representation into its manager reference."""
            return (
                None if value is None else RequestRelationTarget(id=value["identifier"])
            )

        def decode_account(value):
            """Translate a remote composite identity without assuming a scalar ID."""
            return None if value is None else RequestRelationAccount(**value)

        cls.permission_candidates = []
        cls.rule_candidates = []

        def snapshot(candidate):
            """Read check-object aliases through the same attribute interface as permissions."""
            return {
                name: getattr(candidate, name, None)
                for name in (
                    "owner",
                    "owner_id",
                    "account",
                    "account_id",
                    "external_id",
                )
            }

        def capture_rule(candidate):
            """Record the actual Rule candidate before allowing the mutation to proceed."""
            cls.rule_candidates.append((snapshot(candidate), dict(vars(candidate))))
            return True

        class RequestRelationRecord(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                owner = RequestField(
                    RequestRelationTarget | None,
                    source=("wire", "owner"),
                    normalizer=decode_owner,
                    is_editable=True,
                    is_required=False,
                )
                account = RequestField(
                    RequestRelationAccount,
                    source=("wire", "account"),
                    normalizer=decode_account,
                    is_editable=True,
                    is_required=False,
                )
                name = RequestField(str, is_editable=True, is_required=False)
                external_id = RequestField(int, is_editable=True, is_required=False)

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
                    transport_config = RequestTransportConfig(
                        base_url="https://relations.example.test"
                    )
                    rules: ClassVar[list[Rule]] = [
                        Rule(capture_rule, ignore_if_none=False)
                    ]

            class Permission(ManagerBasedPermission):
                def check_permission(self, action, attribute):
                    """Capture permission attributes while exercising the normal permission wrapper."""
                    cls.permission_candidates.append(snapshot(self.instance))
                    return True

        cls.Target = RequestRelationTarget
        cls.Account = RequestRelationAccount
        cls.Record = RequestRelationRecord
        for manager in (cls.Target, cls.Account, cls.Record):
            manager._attributes = manager.Interface.get_attributes()
            GeneralManagerMeta.create_at_properties_for_attributes(
                manager._attributes.keys(), manager
            )

    def setUp(self) -> None:
        """Create fresh transport and capture state for the next test."""
        super().setUp()
        self._reset_state()

    def _reset_state(self) -> None:
        """Reset mutable adapter configuration between independent matrix cases."""
        self.transport = RelationMemoryTransport()
        self.Record.Interface.transport = self.transport
        self.Record.Interface.create_serializer = None
        self.Record.Interface.update_serializer = None
        self.permission_candidates.clear()
        self.rule_candidates.clear()
        self.user = AnonymousUser()

    def mutate(self, action, values):
        """Exercise the public create or update method with a verified anonymous user."""
        if action == "create":
            return self.Record.create(creator_id=self.user, **values)
        return self.Record(id=10).update(creator_id=self.user, **values)

    def assert_owner_candidates(self, expected_id) -> None:
        """Verify permission and Rule views identify the same effective owner."""
        self.assertTrue(self.permission_candidates)
        self.assertTrue(self.rule_candidates)
        for candidate in [
            *self.permission_candidates,
            *(values for values, _raw in self.rule_candidates),
        ]:
            self.assertEqual(candidate["owner_id"], expected_id)
            if expected_id is None:
                self.assertIsNone(candidate["owner"])
            else:
                self.assertIsInstance(candidate["owner"], self.Target)
                self.assertEqual(candidate["owner"].id, expected_id)

    def test_explicit_id_wins_for_both_adapter_styles_and_keyword_orders(self) -> None:
        """Keep actual writes and checks consistent across adapters, ordering, and nulls."""
        for action in ("create", "update"):
            for serializer in (serialize_owner, serialize_owner_id):
                for reverse in (False, True):
                    for winner in (2, None):
                        with self.subTest(
                            action=action,
                            serializer=serializer.__name__,
                            reverse=reverse,
                            winner=winner,
                        ):
                            self._reset_state()
                            setattr(
                                self.Record.Interface,
                                f"{action}_serializer",
                                staticmethod(serializer),
                            )
                            pairs = [("owner", self.Target(id=1)), ("owner_id", winner)]
                            values = dict(reversed(pairs) if reverse else pairs)
                            record = self.mutate(action, values)
                            self.assertEqual(
                                self.transport.bodies, [{"ownerWire": winner}]
                            )
                            self.assert_owner_candidates(winner)
                            self.assertEqual(
                                None if record.owner is None else record.owner.id,
                                winner,
                            )

    def test_single_relation_key_preserves_wire_value_and_synthesizes_check_alias(
        self,
    ) -> None:
        """Preserve lone wire values while exposing their counterpart only to checks."""
        cases = (
            ("owner", self.Target(id=2), 2),
            ("owner", "2", 2),
            ("owner_id", 2, 2),
            ("owner_id", "2", 2),
            ("owner", None, None),
            ("owner_id", None, None),
        )
        for action in ("create", "update"):
            for key, value, expected_id in cases:
                with self.subTest(action=action, key=key, value=value):
                    self._reset_state()
                    record = self.mutate(action, {key: value})
                    body = self.transport.bodies[-1]
                    self.assertEqual(set(body), {key})
                    self.assertIs(body[key], value)
                    self.assert_owner_candidates(expected_id)
                    self.assertEqual(
                        None if record.owner is None else record.owner.id, expected_id
                    )

    def test_explicit_relation_overrides_old_raw_alias_in_update_candidate(
        self,
    ) -> None:
        """Prevent a previous identifier from overriding a new lone relation value."""
        self.transport.payload["owner_id"] = 1
        owner = self.Target(id=2)
        record = self.mutate("update", {"owner": owner})
        self.assertEqual(set(self.transport.bodies[-1]), {"owner"})
        self.assertIs(self.transport.bodies[-1]["owner"], owner)
        self.assert_owner_candidates(2)
        self.assertEqual(record.owner.id, 2)

    def test_omitted_relation_uses_resolved_source_without_adding_wire_keys(
        self,
    ) -> None:
        """Resolve unchanged check state without submitting that relationship again."""
        record = self.mutate("update", {"name": "Renamed"})
        self.assertEqual(self.transport.bodies, [{"name": "Renamed"}])
        self.assert_owner_candidates(1)
        self.assertEqual(record.owner.id, 1)
        self.assertEqual(record.name, "Renamed")

    def test_composite_identifications_work_without_guessing_a_scalar_id(self) -> None:
        """Keep complete composite targets consistent in checks and serialized payloads."""
        identification = {"tenant": "chosen", "code": "two"}
        for action in ("create", "update"):
            for as_manager in (False, True):
                with self.subTest(action=action, as_manager=as_manager):
                    self._reset_state()
                    winner = (
                        self.Account(**identification) if as_manager else identification
                    )
                    record = self.mutate(
                        action,
                        {
                            "account": self.Account(tenant="original", code="one"),
                            "account_id": winner,
                        },
                    )
                    body = self.transport.bodies[-1]
                    self.assertEqual(set(body), {"account", "account_id"})
                    self.assertIs(body["account_id"], winner)
                    self.assertEqual(body["account"].identification, identification)
                    for candidate in [
                        *self.permission_candidates,
                        *(values for values, _raw in self.rule_candidates),
                    ]:
                        self.assertEqual(candidate["account_id"], identification)
                        self.assertEqual(
                            candidate["account"].identification, identification
                        )
                    self.assertEqual(record.account.identification, identification)

    def test_ambiguous_composite_scalar_is_rejected_before_transport(self) -> None:
        """Reject a consumed ambiguous composite reference before a mutation is sent."""
        for action in ("create", "update"):
            with self.subTest(action=action):
                self._reset_state()
                with self.assertRaises(ValueError) as error:
                    self.mutate(action, {"account_id": 2})
                self.assertIn("account", str(error.exception))
                self.assertIn("identification", str(error.exception))
                self.assertEqual(self.transport.bodies, [])

    def test_ordinary_scalar_id_does_not_gain_a_relation_alias(self) -> None:
        """Leave ordinary scalar ID fields outside relationship alias normalization."""
        for action in ("create", "update"):
            with self.subTest(action=action):
                self._reset_state()
                record = self.mutate(action, {"external_id": 42})
                self.assertEqual(self.transport.bodies, [{"external_id": 42}])
                self.assertEqual(record.external_id, 42)
                self.assertNotIn("external", self.rule_candidates[-1][1])
                for candidate in self.permission_candidates:
                    self.assertEqual(candidate["external_id"], 42)

    def test_unused_required_relation_is_not_read_for_partial_update(self) -> None:
        """Avoid fetching an unused required relationship during a partial update."""
        name_rule = Rule(lambda candidate: candidate.name == "After")
        fields = {
            **self.Record.Interface.fields,
            "owner": replace(self.Record.Interface.fields["owner"], is_required=True),
        }
        for rules in ([], [name_rule]):
            with self.subTest(has_rules=bool(rules)):
                self.setUp()
                with (
                    patch.object(self.Record.Interface, "fields", fields),
                    patch.object(self.Record.Interface, "rules", rules),
                ):
                    record = self.Record(id=10)
                    record._interface.set_request_payload_cache(
                        {"id": 10, "name": "Before"}
                    )
                    record.update(ignore_permission=True, name="After")
                    self.assertEqual(self.transport.bodies, [{"name": "After"}])
                    self.assertEqual(record.name, "After")

    def test_permission_attribute_does_not_resolve_unrelated_relation(self) -> None:
        """Resolve only the relationship consumed by a permission attribute access."""
        candidate = PermissionDataManager(
            {"owner_id": 2, "account": "ambiguous composite scalar"}, self.Record
        )
        self.assertEqual(candidate.owner.id, 2)
        self.assertEqual(candidate.owner_id, 2)
        self.assertIsNone(candidate.unknown_scalar)
        with self.assertRaises(ValueError):
            _ = candidate.account

    def test_unknown_rule_scalar_still_raises(self) -> None:
        """Preserve strict Rule failures instead of treating unknown scalars as null."""
        rule = Rule(lambda candidate: candidate.unknown_scalar == 1)
        with patch.object(self.Record.Interface, "rules", [rule]):
            with self.assertRaises(NonexistentAttributeError):
                self.Record(id=10).update(ignore_permission=True, name="After")
        self.assertEqual(self.transport.bodies, [])

    def test_input_identification_is_not_a_request_field_relation(self) -> None:
        """Keep constructor identification inputs outside mutable request relationships."""

        class InputIdentityRecord(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                parent = Input(type=self.Target, required=False)
                name = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict] = {
                        "detail": RequestQueryOperation(
                            name="detail", path="/records/{id}"
                        )
                    }
                    create_operation = RequestMutationOperation(
                        name="create", method="POST", path="/records"
                    )
                    transport_config = RequestTransportConfig(
                        base_url="https://relations.example.test"
                    )

        InputIdentityRecord.Interface.transport = self.transport
        parent = self.Target(id=1)
        InputIdentityRecord.create(
            ignore_permission=True, parent=parent, parent_id=2, name="Created"
        )
        self.assertIs(self.transport.bodies[-1]["parent"], parent)
        self.assertEqual(self.transport.bodies[-1]["parent_id"], 2)

    def test_raw_scalar_pair_keeps_a_json_safe_winning_representation(self) -> None:
        """Keep default request bodies JSON-safe when resolving conflicting raw IDs."""
        for action in ("create", "update"):
            for winner in (2, "2"):
                with self.subTest(action=action, winner=winner):
                    self.setUp()
                    record = self.mutate(action, {"owner": 1, "owner_id": winner})
                    self.assertEqual(
                        self.transport.bodies, [{"owner": winner, "owner_id": winner}]
                    )
                    self.assertIsInstance(
                        self.transport.bodies[-1]["owner"], type(winner)
                    )
                    self.assert_owner_candidates(2)
                    self.assertEqual(record.owner.id, 2)

    def test_unconsumed_lone_wire_value_is_not_interpreted(self) -> None:
        """Leave an opaque lone wire value to its adapter when no check consumes it."""
        for action in ("create", "update"):
            with self.subTest(action=action):
                self.setUp()
                value = {"identifier": 2}
                with patch.object(self.Record.Interface, "rules", []):
                    if action == "create":
                        record = self.Record.create(ignore_permission=True, owner=value)
                    else:
                        record = self.Record(id=10).update(
                            ignore_permission=True, owner=value
                        )
                self.assertEqual(set(self.transport.bodies[-1]), {"owner"})
                self.assertIs(self.transport.bodies[-1]["owner"], value)
                self.assertEqual(record.owner.id, 2)

    def test_scalar_relation_keeps_scalar_shape_for_identification_mapping(
        self,
    ) -> None:
        """Unwrap a supported single-ID mapping without changing canonical scalar shape."""
        from general_manager.interface.capabilities.request.relation_inputs import (
            reconcile_request_relation_kwargs,
        )

        payload = {"owner": 1, "owner_id": {"id": 2}}
        resolved = reconcile_request_relation_kwargs(self.Record.Interface, payload)

        self.assertEqual(resolved, {"owner": 2, "owner_id": {"id": 2}})
        self.assertEqual(payload, {"owner": 1, "owner_id": {"id": 2}})

    def test_uninitialized_rule_candidate_keeps_normal_attribute_errors(self) -> None:
        """Raise AttributeError rather than recursively reading unset relation metadata."""
        candidate = RequestRuleCandidate.__new__(RequestRuleCandidate)
        for name in ("owner", "unknown_scalar", "_gm_relations"):
            with self.subTest(name=name):
                with self.assertRaises(AttributeError):
                    getattr(candidate, name)

    def test_uninitialized_rule_candidate_can_be_copied(self) -> None:
        """Allow normal copying before lazy metadata slots have been initialized."""
        candidate = RequestRuleCandidate.__new__(RequestRuleCandidate)
        for copier in (copy, deepcopy):
            with self.subTest(copier=copier.__name__):
                copied = copier(candidate)
                self.assertIsInstance(copied, RequestRuleCandidate)
                self.assertIsNot(copied, candidate)
                self.assertEqual(vars(copied), {})
                with self.assertRaises(AttributeError):
                    _ = copied.unknown_scalar

    def test_rule_candidate_copy_preserves_lazy_explicit_aliases(self) -> None:
        """Retain deferred aliases and normal shallow versus deep namespace semantics."""
        for copier in (copy, deepcopy):
            with self.subTest(copier=copier.__name__):
                notes = ["original"]
                values = {"owner_id": 2, "name": "Before", "notes": notes}
                candidate = RequestRuleCandidate(
                    self.Record.Interface,
                    values,
                    explicit_values=values,
                    existing_payload=None,
                )
                copied = copier(candidate)
                self.assertIsNot(copied, candidate)
                self.assertNotIn("owner", vars(candidate))
                self.assertNotIn("owner", vars(copied))
                self.assertEqual(copied.owner.id, 2)
                self.assertEqual(copied.owner_id, 2)
                self.assertNotIn("owner", vars(candidate))
                self.assertEqual(copied.name, "Before")
                self.assertEqual(copied.notes, notes)
                if copier is copy:
                    self.assertIs(copied.notes, notes)
                else:
                    self.assertIsNot(copied.notes, notes)
                with self.assertRaises(AttributeError):
                    _ = copied.unknown_scalar

    def test_rule_candidate_copy_preserves_lazy_source_snapshot(self) -> None:
        """Retain deferred source reads with the expected shared or isolated snapshot."""
        for copier in (copy, deepcopy):
            with self.subTest(copier=copier.__name__):
                self._reset_state()
                existing = self.transport.payload
                candidate = RequestRuleCandidate(
                    self.Record.Interface,
                    {**existing, "name": "After"},
                    explicit_values={"name": "After"},
                    existing_payload=existing,
                )
                copied = copier(candidate)
                existing["wire"]["owner"]["identifier"] = 2
                self.assertNotIn("owner", vars(copied))
                self.assertEqual(copied.owner.id, 2 if copier is copy else 1)
                self.assertEqual(copied.owner_id, 2 if copier is copy else 1)
                self.assertEqual(copied.name, "After")

    def test_rule_candidate_copy_preserves_cached_aliases(self) -> None:
        """Copy already resolved aliases with normal manager-object sharing semantics."""
        for copier in (copy, deepcopy):
            with self.subTest(copier=copier.__name__):
                candidate = RequestRuleCandidate(
                    self.Record.Interface,
                    {"owner_id": 2},
                    explicit_values={"owner_id": 2},
                    existing_payload=None,
                )
                owner = candidate.owner
                copied = copier(candidate)
                self.assertEqual(copied.owner.id, 2)
                self.assertEqual(copied.owner_id, 2)
                if copier is copy:
                    self.assertIs(copied.owner, owner)
                else:
                    self.assertIsNot(copied.owner, owner)
