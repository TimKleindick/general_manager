"""Keep mutation, permission, and rule views of relation aliases consistent."""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

from django.db import models

from general_manager.interface import DatabaseInterface, ExistingModelInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.rule import Rule
from general_manager.utils.testing import GeneralManagerTransactionTestCase


_rule_candidates: list[tuple[object, object, object]] = []


def _relation_alias_rule(candidate: Any) -> bool:
    """Record the rule candidate and reject inconsistent relation aliases."""
    _rule_candidates.append((candidate, candidate.basis, candidate.basis_id))
    if candidate.basis_id is None:
        return candidate.basis is None
    return candidate.basis is not None and candidate.basis.id == candidate.basis_id


class _ObserveRelationPermission(ManagerBasedPermission):
    __read__: ClassVar[list[str]] = ["public"]
    __create__: ClassVar[list[str]] = ["public"]
    __update__: ClassVar[list[str]] = ["public"]
    __delete__: ClassVar[list[str]] = ["public"]
    candidates: ClassVar[list[tuple[object, object]]] = []

    def check_permission(self, action: str, attribute: str) -> bool:
        """Observe both candidate aliases before applying ordinary permission checks."""
        if action in {"create", "update"}:
            self.candidates.append((self.instance.basis, self.instance.basis_id))
        return super().check_permission(action, attribute)


class RelationMutationPrecedenceTests(GeneralManagerTransactionTestCase):
    Basis: ClassVar[type[GeneralManager]]
    record_managers: ClassVar[tuple[type[GeneralManager], ...]]

    @classmethod
    def setUpClass(cls) -> None:
        """Build database and existing-model managers sharing relation checks."""

        class MutationAliasBasis(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)

        class MutationAliasRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)
                basis = models.ForeignKey(
                    "general_manager.MutationAliasBasis",
                    on_delete=models.CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )

                class Meta:
                    rules: ClassVar[list[Rule[Any]]] = [
                        Rule(_relation_alias_rule, ignore_if_none=False)
                    ]

            Permission = _ObserveRelationPermission

        class MutationAliasLegacyRecord(models.Model):
            name = models.CharField(max_length=40)
            basis = models.OneToOneField(
                MutationAliasBasis.Interface._model,
                on_delete=models.CASCADE,
                null=True,
                blank=True,
                related_name="+",
            )

            class Meta:
                app_label = "general_manager"

        class MutationAliasExistingRecord(GeneralManager):
            class Interface(ExistingModelInterface):
                model = MutationAliasLegacyRecord

                class Meta:
                    rules: ClassVar[list[Rule[Any]]] = [
                        Rule(_relation_alias_rule, ignore_if_none=False)
                    ]

            Permission = _ObserveRelationPermission

        cls.Basis = MutationAliasBasis
        cls.record_managers = (MutationAliasRecord, MutationAliasExistingRecord)
        cls.general_manager_classes = [MutationAliasBasis, *cls.record_managers]
        super().setUpClass()

    def _write_and_assert_views(
        self,
        manager: type[GeneralManager],
        action: str,
        payload: dict[str, object],
        expected_id: object,
    ) -> None:
        """Compare persisted relations with permission and rule candidates for a write."""
        if action == "update":
            old_basis = self.Basis.create(ignore_permission=True, name="Old basis")
            record = manager.create(
                ignore_permission=True, name="Before", basis=old_basis
            )
        _ObserveRelationPermission.candidates.clear()
        _rule_candidates.clear()
        if action == "create":
            record = manager.create(name="Created", **payload)
        else:
            record.update(**payload)

        row = manager.Interface._model.objects.get(pk=record.id)
        self.assertEqual(row.basis_id, expected_id)
        self.assertEqual(record.basis_id, expected_id)
        self.assertTrue(_ObserveRelationPermission.candidates)
        for basis, basis_id in _ObserveRelationPermission.candidates:
            self.assertEqual(basis_id, expected_id)
            if expected_id is None:
                self.assertIsNone(basis)
            else:
                self.assertIsInstance(basis, self.Basis)
                self.assertEqual(basis.id, expected_id)
        self.assertTrue(_rule_candidates)
        for candidate, basis, basis_id in _rule_candidates:
            self.assertIsInstance(candidate, manager.Interface._model)
            self.assertEqual(basis_id, expected_id)
            if expected_id is None:
                self.assertIsNone(basis)
            else:
                self.assertIsInstance(basis, self.Basis.Interface._model)
                self.assertEqual(basis.pk, expected_id)

    def test_single_relation_or_id_is_available_to_permissions_and_rules(self) -> None:
        """Resolve either supplied alias without stale update values taking precedence."""
        for manager in self.record_managers:
            for action in ("create", "update"):
                for value_kind in (
                    "manager",
                    "model",
                    "raw_id",
                    "none",
                    "id",
                    "id_none",
                ):
                    with self.subTest(
                        manager=manager.__name__, action=action, value=value_kind
                    ):
                        basis = self.Basis.create(
                            ignore_permission=True, name="New basis"
                        )
                        basis_model = self.Basis.Interface._model.objects.get(
                            pk=basis.id
                        )
                        payload, expected_id = {
                            "manager": ({"basis": basis}, basis.id),
                            "model": ({"basis": basis_model}, basis.id),
                            "raw_id": ({"basis": basis.id}, basis.id),
                            "none": ({"basis": None}, None),
                            "id": ({"basis_id": basis.id}, basis.id),
                            "id_none": ({"basis_id": None}, None),
                        }[value_kind]
                        self._write_and_assert_views(
                            manager, action, payload, expected_id
                        )

    def test_explicit_id_wins_in_both_orders_including_null(self) -> None:
        """Prefer an explicitly supplied ID for both input orders and null values."""
        for manager in self.record_managers:
            for action in ("create", "update"):
                for value_kind in ("manager", "model", "raw_id", "none"):
                    for id_is_none in (False, True):
                        if value_kind == "none" and id_is_none:
                            continue
                        for id_first in (False, True):
                            with self.subTest(
                                manager=manager.__name__,
                                action=action,
                                value=value_kind,
                                id_is_none=id_is_none,
                                id_first=id_first,
                            ):
                                losing = self.Basis.create(
                                    ignore_permission=True, name="Losing basis"
                                )
                                winning = self.Basis.create(
                                    ignore_permission=True, name="Winning basis"
                                )
                                relation = {
                                    "manager": losing,
                                    "model": self.Basis.Interface._model.objects.get(
                                        pk=losing.id
                                    ),
                                    "raw_id": losing.id,
                                    "none": None,
                                }[value_kind]
                                expected_id = None if id_is_none else winning.id
                                pairs = [("basis", relation), ("basis_id", expected_id)]
                                if id_first:
                                    pairs.reverse()
                                self._write_and_assert_views(
                                    manager, action, dict(pairs), expected_id
                                )

    def test_name_only_update_keeps_existing_relation_in_both_views(self) -> None:
        """Preserve the existing relation when an update supplies no relation alias."""
        for manager in self.record_managers:
            with self.subTest(manager=manager.__name__):
                basis = self.Basis.create(
                    ignore_permission=True, name="Unchanged basis"
                )
                record = manager.create(
                    ignore_permission=True, name="Before", basis_id=basis.id
                )
                _ObserveRelationPermission.candidates.clear()
                _rule_candidates.clear()
                record.update(name="Renamed")
                row = manager.Interface._model.objects.get(pk=record.id)
                self.assertEqual(row.name, "Renamed")
                self.assertEqual(row.basis_id, basis.id)
                self.assertTrue(_ObserveRelationPermission.candidates)
                for relation, relation_id in _ObserveRelationPermission.candidates:
                    self.assertIsInstance(relation, self.Basis)
                    self.assertEqual(relation.id, basis.id)
                    self.assertEqual(relation_id, basis.id)
                self.assertTrue(_rule_candidates)
                for candidate, relation, relation_id in _rule_candidates:
                    self.assertIsInstance(candidate, manager.Interface._model)
                    self.assertIsInstance(relation, self.Basis.Interface._model)
                    self.assertEqual(relation.pk, basis.id)
                    self.assertEqual(relation_id, basis.id)

    @staticmethod
    def _relation_payload(
        relation: GeneralManager, identifier: object, shape: str
    ) -> dict[str, object]:
        """Build single-alias or conflicting paired input without changing its order."""
        return {
            "relation": {"basis": relation},
            "id": {"basis_id": identifier},
            "relation_first": {"basis": relation, "basis_id": identifier},
            "id_first": {"basis_id": identifier, "basis": relation},
        }[shape]

    def _assert_denied_field_write(
        self,
        manager: type[GeneralManager],
        action: str,
        payload: dict[str, object],
        expected_id: object,
    ) -> None:
        """Reject before persistence while checking the actual winning relation target."""
        if action == "update":
            old_basis = self.Basis.create(ignore_permission=True, name="Original basis")
            record = manager.create(
                ignore_permission=True, name="Before", basis=old_basis
            )
        before_count = manager.Interface._model.objects.count()
        _ObserveRelationPermission.candidates.clear()
        _rule_candidates.clear()
        with self.assertRaises(PermissionError):
            if action == "create":
                manager.create(name="Denied", **payload)
            else:
                record.update(**payload)
        self.assertEqual(manager.Interface._model.objects.count(), before_count)
        if action == "update":
            unchanged = manager.Interface._model.objects.get(pk=record.id)
            self.assertEqual(unchanged.basis_id, old_basis.id)
            self.assertEqual(unchanged.name, "Before")
        self.assertTrue(_ObserveRelationPermission.candidates)
        for relation, relation_id in _ObserveRelationPermission.candidates:
            self.assertIsInstance(relation, self.Basis)
            self.assertEqual(relation.id, expected_id)
            self.assertEqual(relation_id, expected_id)
        self.assertFalse(_rule_candidates)

    def test_field_definition_on_either_alias_gates_every_write_spelling(self) -> None:
        """Apply one declared relation field gate to every FK/O2O mutation spelling."""
        for manager in self.record_managers:
            for action in ("create", "update"):
                for definition_alias in ("basis", "basis_id"):
                    for shape in ("relation", "id", "relation_first", "id_first"):
                        for is_allowed in (False, True):
                            with self.subTest(
                                manager=manager.__name__,
                                action=action,
                                definition=definition_alias,
                                shape=shape,
                                is_allowed=is_allowed,
                            ):
                                allowed = self.Basis.create(
                                    ignore_permission=True, name="Allowed target"
                                )
                                denied = self.Basis.create(
                                    ignore_permission=True, name="Denied target"
                                )
                                winner = allowed if is_allowed else denied
                                loser = denied if is_allowed else allowed
                                relation = winner if shape == "relation" else loser
                                payload = self._relation_payload(
                                    relation, winner.id, shape
                                )
                                definition = {
                                    action: [
                                        "matches:basis_id:-1",
                                        f"matches:basis_id:{allowed.id}",
                                    ]
                                }
                                with patch.object(
                                    _ObserveRelationPermission,
                                    definition_alias,
                                    definition,
                                    create=True,
                                ):
                                    if is_allowed:
                                        self._write_and_assert_views(
                                            manager, action, payload, winner.id
                                        )
                                    else:
                                        self._assert_denied_field_write(
                                            manager, action, payload, winner.id
                                        )

    def test_conflicting_alias_field_definitions_cannot_be_bypassed(self) -> None:
        """Require both explicit alias groups even when only the permissive key is sent."""
        for manager in self.record_managers:
            for action in ("create", "update"):
                for public_alias in ("basis", "basis_id"):
                    for shape in ("relation", "id", "relation_first", "id_first"):
                        with self.subTest(
                            manager=manager.__name__,
                            action=action,
                            public_alias=public_alias,
                            shape=shape,
                        ):
                            winner = self.Basis.create(
                                ignore_permission=True, name="Winning target"
                            )
                            loser = self.Basis.create(
                                ignore_permission=True, name="Losing target"
                            )
                            payload = self._relation_payload(
                                winner if shape == "relation" else loser,
                                winner.id,
                                shape,
                            )
                            with (
                                patch.object(
                                    _ObserveRelationPermission,
                                    "basis",
                                    {
                                        action: ["public"]
                                        if public_alias == "basis"
                                        else ["matches:basis_id:-1"]
                                    },
                                    create=True,
                                ),
                                patch.object(
                                    _ObserveRelationPermission,
                                    "basis_id",
                                    {
                                        action: ["public"]
                                        if public_alias == "basis_id"
                                        else ["matches:basis_id:-1"]
                                    },
                                    create=True,
                                ),
                            ):
                                self._assert_denied_field_write(
                                    manager, action, payload, winner.id
                                )

    def test_identical_alias_field_definitions_allow_the_id_winner(self) -> None:
        """Keep identical field definitions harmless for permitted conflicting inputs."""
        for manager in self.record_managers:
            for action in ("create", "update"):
                for shape in ("relation_first", "id_first"):
                    with self.subTest(
                        manager=manager.__name__, action=action, shape=shape
                    ):
                        winner = self.Basis.create(
                            ignore_permission=True, name="Permitted target"
                        )
                        loser = self.Basis.create(
                            ignore_permission=True, name="Losing target"
                        )
                        definition = {action: [f"matches:basis_id:{winner.id}"]}
                        with (
                            patch.object(
                                _ObserveRelationPermission,
                                "basis",
                                definition,
                                create=True,
                            ),
                            patch.object(
                                _ObserveRelationPermission,
                                "basis_id",
                                definition,
                                create=True,
                            ),
                        ):
                            self._write_and_assert_views(
                                manager,
                                action,
                                self._relation_payload(loser, winner.id, shape),
                                winner.id,
                            )
