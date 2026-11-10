"""Resolve permission relations in the same database and visibility context as ORM writes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar
from unittest.mock import patch

from django.db import connections, models
from django.test import override_settings

from general_manager.interface import ExistingModelInterface
from general_manager.interface.capabilities.orm import OrmCreateCapability
from general_manager.interface.utils.errors import InvalidFieldValueError
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.base_permission import PermissionCheckError
from general_manager.permission.permission_data_manager import (
    PermissionDataManager,
    PermissionRelationResolutionError,
)
from general_manager.rule import Rule
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from tests.utils.database import create_test_models, drop_test_models


_callable_default_evaluations: list[bool] = []


def _callable_context_default() -> str:
    """Record whether permission resolution accidentally executes a default."""
    _callable_default_evaluations.append(True)
    return "materialized"


class _AllowedTargetPermission(OverrideManagerPermission):
    __read__: ClassVar[list[str]] = ["public"]
    candidates: ClassVar[list[tuple[Any, object]]] = []

    def check_permission(self, action: str, attribute: str) -> bool:
        """Authorize writes against, and record, the actual resolved target."""
        if action in {"create", "update"}:
            target = self.instance.basis
            self.candidates.append((target, self.instance.basis_id))
            return target.allowed
        return super().check_permission(action, attribute)


class _RelationContextRouter:
    @staticmethod
    def _source_database(source: Any) -> str:
        """Exercise routing that depends on literal and callable model defaults."""
        if source.shard == "numeric":
            return "secondary" if source.shard_number == 2 else "default"
        if source.shard == "fk_numeric":
            return "secondary" if source.routing_basis_id == 2 else "default"
        if source.shard == "fk_object":
            return "secondary" if source.routing_model_basis_id == 2 else "default"
        if source.shard == "callable":
            return (
                "secondary"
                if source.__dict__.get("nonce") == "materialized"
                else "default"
            )
        return source.shard

    def db_for_read(self, model: type[models.Model], **hints: Any) -> str | None:
        """Resolve relation reads using the proposed source instance context."""
        if model.__name__ == "PermissionContextSource":
            return "secondary"
        if model.__name__ == "PermissionContextTarget":
            source = hints.get("instance")
            if source is not None:
                # Unsaved Django instances have no database until save(); the
                # supplied shard still identifies their intended target.
                return source._state.db or self._source_database(source)
            return "default"
        return None

    def db_for_write(self, model: type[models.Model], **hints: Any) -> str | None:
        """Choose the source shard from its instance rather than class-only hints."""
        if model.__name__ == "PermissionContextSource":
            source = hints.get("instance")
            return self._source_database(source) if source is not None else "default"
        return None


@override_settings(DATABASE_ROUTERS=[_RelationContextRouter()])
class RoutedRelationPermissionContextTests(GeneralManagerTransactionTestCase):
    databases: ClassVar[set[str]] = {"default", "secondary"}
    Target: ClassVar[type[models.Model]]
    Source: ClassVar[type[GeneralManager]]
    _default_created_models: ClassVar[list[type[models.Model]]] = []
    _secondary_created_models: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        """Build raw FK fixtures on default and secondary test databases."""

        class PermissionContextTarget(models.Model):
            allowed = models.BooleanField()

            class Meta:
                app_label = "general_manager"

        class PermissionContextSource(models.Model):
            name = models.CharField(max_length=40)
            details = models.JSONField(default=dict, blank=True)
            shard = models.CharField(max_length=20, default="secondary")
            nonce = models.CharField(max_length=30, default=_callable_context_default)
            shard_number = models.IntegerField(default="2")
            routing_basis = models.ForeignKey(
                PermissionContextTarget,
                on_delete=models.CASCADE,
                related_name="+",
                default="2",
            )
            routing_model_basis = models.ForeignKey(
                PermissionContextTarget,
                on_delete=models.CASCADE,
                related_name="+",
                default=PermissionContextTarget(pk="2"),
            )
            basis = models.ForeignKey(PermissionContextTarget, on_delete=models.CASCADE)

            class Meta:
                app_label = "general_manager"

        class RoutedSource(GeneralManager):
            class Interface(ExistingModelInterface):
                model = PermissionContextSource

            Permission = _AllowedTargetPermission

        cls.Target = PermissionContextTarget
        cls.Source = RoutedSource
        cls.general_manager_classes = [RoutedSource]
        super().setUpClass()
        cls._default_created_models = create_test_models(
            connections["default"], (PermissionContextTarget,)
        )
        cls._secondary_created_models = create_test_models(
            connections["secondary"],
            (
                PermissionContextTarget,
                PermissionContextSource,
                PermissionContextSource.history.model,
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Drop managed FK sources before separately owned raw target tables."""
        try:
            cls._drop_created_test_models()
        finally:
            try:
                with connections["secondary"].schema_editor() as editor:
                    drop_test_models(editor, reversed(cls._secondary_created_models))
            finally:
                try:
                    with connections["default"].schema_editor() as editor:
                        drop_test_models(editor, reversed(cls._default_created_models))
                finally:
                    cls._default_created_models = []
                    cls._secondary_created_models = []
                    super().tearDownClass()

    def setUp(self) -> None:
        """Seed contradictory same-PK targets across both databases."""
        super().setUp()
        for alias, allowed in (("default", True), ("secondary", False)):
            self.Target.objects.using(alias).create(pk=1, allowed=allowed)
            self.Target.objects.using(alias).create(pk=2, allowed=not allowed)
        _AllowedTargetPermission.candidates.clear()

    def _assert_secondary_candidate(self, expected_id: int, allowed: bool) -> None:
        """Require every permission lookup to inspect the routed target row."""
        self.assertTrue(_AllowedTargetPermission.candidates)
        for target, identifier in _AllowedTargetPermission.candidates:
            self.assertIsInstance(target, self.Target)
            self.assertEqual(target._state.db, "secondary")
            self.assertEqual(target.pk, expected_id)
            self.assertEqual(identifier, expected_id)
            self.assertEqual(target.allowed, allowed)

    def test_create_uses_source_instance_routing_and_rejects_denied_target(
        self,
    ) -> None:
        """Deny the routed target even when the same default-database PK is allowed."""
        with self.assertRaises(PermissionCheckError):
            self.Source.create(name="Denied", shard="secondary", basis_id=1)
        self._assert_secondary_candidate(1, False)
        for alias in ("default", "secondary"):
            self.assertFalse(self.Source.Interface._model.objects.using(alias).exists())

        _AllowedTargetPermission.candidates.clear()
        created = self.Source.create(name="Allowed", shard="secondary", basis_id=2)
        self._assert_secondary_candidate(2, True)
        row = self.Source.Interface._model.objects.using("secondary").get(pk=created.id)
        self.assertEqual(row.basis_id, 2)
        self.assertTrue(row.basis.allowed)
        self.assertEqual(row.basis._state.db, "secondary")
        self.assertFalse(self.Source.Interface._model.objects.using("default").exists())

    def test_omitted_plain_default_routes_candidate_to_secondary(self) -> None:
        """Apply the literal shard default before evaluating relation permissions."""
        with self.assertRaises(PermissionCheckError):
            self.Source.create(name="Denied default", basis_id=1)
        self._assert_secondary_candidate(1, False)
        _AllowedTargetPermission.candidates.clear()
        created = self.Source.create(name="Allowed default", basis_id=2)
        self._assert_secondary_candidate(2, True)
        row = self.Source.Interface._model.objects.using("secondary").get(pk=created.id)
        self.assertEqual((row.shard, row.basis_id), ("secondary", 2))
        self.assertFalse(self.Source.Interface._model.objects.using("default").exists())

    def test_converted_literal_defaults_authorize_the_actual_write_database(
        self,
    ) -> None:
        """Convert numeric and FK defaults before deciding which target permits a write."""
        for mode in ("numeric", "fk_numeric", "fk_object"):
            with self.subTest(mode=mode):
                counts = {
                    alias: self.Source.Interface._model.objects.using(alias).count()
                    for alias in ("default", "secondary")
                }
                _AllowedTargetPermission.candidates.clear()
                with self.assertRaises(PermissionCheckError):
                    self.Source.create(name="Denied converted", shard=mode, basis_id=1)
                self._assert_secondary_candidate(1, False)
                for alias, count in counts.items():
                    self.assertEqual(
                        self.Source.Interface._model.objects.using(alias).count(), count
                    )

                _AllowedTargetPermission.candidates.clear()
                created = self.Source.create(
                    name="Allowed converted", shard=mode, basis_id=2
                )
                self._assert_secondary_candidate(2, True)
                row = self.Source.Interface._model.objects.using("secondary").get(
                    pk=created.id
                )
                self.assertEqual(
                    (
                        row.shard_number,
                        row.routing_basis_id,
                        row.routing_model_basis_id,
                        row.basis_id,
                    ),
                    (2, 2, 2, 2),
                )
                self.assertIsInstance(row.shard_number, int)
                self.assertTrue(row.basis.allowed)
                self.assertEqual(
                    self.Source.Interface._model.objects.using("default").count(),
                    counts["default"],
                )

    def test_permission_resolution_does_not_execute_callable_defaults(self) -> None:
        """Resolve relation permissions without materializing unrelated callable defaults."""
        candidate = PermissionDataManager(
            {"name": "No initialization", "shard": "secondary", "basis_id": 2},
            self.Source,
        )
        _callable_default_evaluations.clear()
        self.assertEqual(candidate.basis._state.db, "secondary")
        self.assertEqual(candidate.basis_id, 2)
        self.assertEqual(_callable_default_evaluations, [])

    def test_callable_default_routes_create_permission_and_write_together(self) -> None:
        """Check the permitted target on the database selected by the real default."""
        _callable_default_evaluations.clear()
        created = self.Source.create(
            name="Allowed callable default", shard="callable", basis_id=2
        )
        self._assert_secondary_candidate(2, True)
        self.assertEqual(_callable_default_evaluations, [True])
        row = self.Source.Interface._model.objects.using("secondary").get(pk=created.id)
        self.assertEqual((row.nonce, row.basis_id), ("materialized", 2))
        self.assertFalse(self.Source.Interface._model.objects.using("default").exists())

    def test_callable_default_denies_create_without_writing(self) -> None:
        """A denied routed target leaves both databases unchanged."""
        _callable_default_evaluations.clear()
        with self.assertRaises(PermissionCheckError):
            self.Source.create(
                name="Denied callable default", shard="callable", basis_id=1
            )
        self._assert_secondary_candidate(1, False)
        self.assertEqual(_callable_default_evaluations, [True])
        for alias in ("default", "secondary"):
            self.assertFalse(self.Source.Interface._model.objects.using(alias).exists())

    def test_denial_without_routing_check_leaves_defaults_uninitialized(self) -> None:
        """An early permission denial does not prepare a model for an unused write."""
        _callable_default_evaluations.clear()
        with (
            patch.object(
                self.Source.Permission,
                "check_create_permission",
                side_effect=PermissionCheckError(None, ["Denied"]),
            ),
            self.assertRaises(PermissionCheckError),
        ):
            self.Source.create(name="Denied before routing", basis_id=2)
        self.assertEqual(_callable_default_evaluations, [])

    def test_callable_default_with_inherited_create_and_copied_payload(self) -> None:
        """Bind the real write when permission checks copy nested payload values."""

        class InheritedCreate(OrmCreateCapability):
            """Reuse the standard ORM implementation through a capability subclass."""

        interface = self.Source.Interface
        get_handler = interface.get_capability_handler
        check_permission = self.Source.Permission.check_create_permission

        def check_copied(data: dict[str, object], manager: Any, user: Any) -> None:
            check_permission(deepcopy(data), manager, user)

        with (
            patch.object(
                interface,
                "get_capability_handler",
                side_effect=lambda name: (
                    InheritedCreate() if name == "create" else get_handler(name)
                ),
            ),
            patch.object(
                self.Source.Permission, "check_create_permission", check_copied
            ),
        ):
            created = self.Source.create(
                name="Copied permission data",
                shard="callable",
                basis_id=2,
                details={"labels": ["one"]},
            )
        self._assert_secondary_candidate(2, True)
        self.assertEqual(created.details, {"labels": ["one"]})

    def test_custom_create_requires_fixed_database_for_related_object_check(
        self,
    ) -> None:
        """Reject a dynamic route that the built-in persistence path cannot bind."""
        with (
            patch.object(self.Source.Interface, "create") as custom_create,
            self.assertRaisesRegex(PermissionError, "requires a fixed database alias"),
        ):
            self.Source.create(name="Custom writer", shard="callable", basis_id=2)
        custom_create.assert_not_called()

    def test_custom_permission_cannot_substitute_a_different_relation(self) -> None:
        """A separate check cannot silently read the real create's permitted target."""
        check_permission = self.Source.Permission.check_create_permission

        def check_other_relation(
            data: dict[str, object], manager: Any, user: Any
        ) -> None:
            check_permission({**data, "basis_id": 1}, manager, user)

        with (
            patch.object(
                self.Source.Permission,
                "check_create_permission",
                check_other_relation,
            ),
            self.assertRaisesRegex(PermissionError, "relation target changed"),
        ):
            self.Source.create(name="Mismatched check", shard="callable", basis_id=2)
        for alias in ("default", "secondary"):
            self.assertFalse(self.Source.Interface._model.objects.using(alias).exists())

    def test_validation_cannot_change_the_authorized_create_target(self) -> None:
        """Abort before saving if cleaning changes the write database or key."""
        model = self.Source.Interface._model
        for name, value in (("shard", "default"), ("basis_id", 1)):
            with self.subTest(field=name):

                def change_target(
                    instance: Any, field_name=name, new_value=value
                ) -> None:
                    setattr(instance, field_name, new_value)

                with (
                    patch.object(model, "clean", change_target),
                    self.assertRaisesRegex(PermissionError, "relation target changed"),
                ):
                    self.Source.create(
                        name="Changed target", shard="callable", basis_id=2
                    )
                self._assert_secondary_candidate(2, True)
                for alias in ("default", "secondary"):
                    self.assertFalse(model.objects.using(alias).exists())

    def test_update_uses_existing_source_routing_and_does_not_persist_denied_id(
        self,
    ) -> None:
        """Preserve routed source state when the replacement target is denied."""
        record = self.Source.create(
            ignore_permission=True, name="Before", shard="secondary", basis_id=2
        )
        _AllowedTargetPermission.candidates.clear()
        with self.assertRaises(PermissionCheckError):
            record.update(name="Denied", basis_id=1)
        self._assert_secondary_candidate(1, False)
        row = self.Source.Interface._model.objects.using("secondary").get(pk=record.id)
        self.assertEqual((row.name, row.basis_id), ("Before", 2))

        _AllowedTargetPermission.candidates.clear()
        record.update(name="Renamed")
        self._assert_secondary_candidate(2, True)
        row.refresh_from_db(using="secondary")
        self.assertEqual((row.name, row.basis_id), ("Renamed", 2))


class FilteredRelationPermissionContextTests(GeneralManagerTransactionTestCase):
    Target: ClassVar[type[models.Model]]
    Source: ClassVar[type[GeneralManager]]
    _created_targets: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        """Build a raw target with distinct filtered default and unfiltered base managers."""

        class VisibleTargets(models.Manager):
            def get_queryset(self):
                """Hide non-visible targets from the default, but not base, manager."""
                return super().get_queryset().filter(visible=True)

        class PermissionFilteredTarget(models.Model):
            allowed = models.BooleanField(default=True)
            visible = models.BooleanField()
            objects = VisibleTargets()
            all_objects = models.Manager()

            class Meta:
                app_label = "general_manager"
                base_manager_name = "all_objects"

        class PermissionFilteredSource(models.Model):
            name = models.CharField(max_length=40)
            basis = models.ForeignKey(
                PermissionFilteredTarget, on_delete=models.CASCADE
            )

            class Meta:
                app_label = "general_manager"

        class FilteredSource(GeneralManager):
            class Interface(ExistingModelInterface):
                model = PermissionFilteredSource

            Permission = _AllowedTargetPermission

        cls.Target = PermissionFilteredTarget
        cls.Source = FilteredSource
        cls.general_manager_classes = [FilteredSource]
        super().setUpClass()
        cls._created_targets = create_test_models(
            connections["default"], (PermissionFilteredTarget,)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove managed sources before their separately owned filtered target."""
        try:
            cls._drop_created_test_models()
        finally:
            try:
                with connections["default"].schema_editor() as editor:
                    drop_test_models(editor, reversed(cls._created_targets))
            finally:
                cls._created_targets = []
                super().tearDownClass()

    def _assert_hidden_candidate(self, expected_id: object) -> None:
        """Require the hidden target to remain accessible through relation lookup."""
        self.assertTrue(_AllowedTargetPermission.candidates)
        for target, identifier in _AllowedTargetPermission.candidates:
            self.assertIsInstance(target, self.Target)
            self.assertEqual(target.pk, expected_id)
            self.assertEqual(identifier, expected_id)
            self.assertFalse(target.visible)

    def test_relation_reads_are_cached_per_candidate_and_update_state(self) -> None:
        """Use one relation query per candidate without leaking old targets into updates."""
        visible = self.Target._base_manager.create(visible=True)
        hidden = self.Target._base_manager.create(visible=False)
        candidate = PermissionDataManager({"basis_id": hidden.pk}, self.Source)
        with self.assertNumQueries(1):
            first = candidate.basis
            self.assertIs(candidate.basis, first)
            self.assertIs(candidate.basis, first)
            self.assertEqual(candidate.basis_id, hidden.pk)
            self.assertEqual(candidate.basis_id, hidden.pk)

        record = self.Source.create(
            ignore_permission=True, name="Before", basis=visible
        )
        _AllowedTargetPermission.candidates.clear()
        record.update(name="New target", basis_id=hidden.pk)
        self._assert_hidden_candidate(hidden.pk)
        self.assertIs(
            _AllowedTargetPermission.candidates[0][0],
            _AllowedTargetPermission.candidates[-1][0],
        )
        row = self.Source.Interface._model.objects.get(pk=record.id)
        self.assertEqual(row.basis_id, hidden.pk)

    def test_for_update_invalidates_alias_values_read_during_initialization(
        self,
    ) -> None:
        """Discard constructor-time alias values when explicit update inputs replace them."""
        visible = self.Target._base_manager.create(visible=True)
        hidden = self.Target._base_manager.create(visible=False)
        record = self.Source.create(
            ignore_permission=True, name="Before", basis=visible
        )

        class PrimedCandidate(PermissionDataManager):
            def __init__(self, permission_data, manager=None):
                """Prime both aliases before the inherited update path installs explicit inputs."""
                super().__init__(permission_data, manager)
                self.initial_basis = self.basis
                self.initial_id = self.basis_id

        candidate = PrimedCandidate.for_update(record, {"basis": hidden})
        self.assertEqual(candidate.initial_basis.pk, visible.pk)
        self.assertEqual(candidate.initial_id, visible.pk)
        self.assertEqual(candidate.basis.pk, hidden.pk)
        self.assertEqual(candidate.basis_id, hidden.pk)

    def test_name_only_update_keeps_relation_hidden_by_default_manager(self) -> None:
        """Retain an unchanged FK target excluded only by its default manager."""
        hidden = self.Target._base_manager.create(visible=False)
        self.assertFalse(self.Target._default_manager.filter(pk=hidden.pk).exists())
        record = self.Source.create(ignore_permission=True, name="Before", basis=hidden)
        _AllowedTargetPermission.candidates.clear()
        record.update(name="Renamed")
        self._assert_hidden_candidate(hidden.pk)
        row = self.Source.Interface._model.objects.get(pk=record.id)
        self.assertEqual((row.name, row.basis_id), ("Renamed", hidden.pk))

    def test_explicit_hidden_id_uses_base_manager_for_create_and_update(self) -> None:
        """Resolve submitted IDs through the same base manager as Django relations."""
        visible = self.Target._base_manager.create(visible=True)
        hidden = self.Target._base_manager.create(visible=False)
        for action in ("create", "update"):
            with self.subTest(action=action):
                if action == "update":
                    record = self.Source.create(
                        ignore_permission=True, name="Before", basis=visible
                    )
                _AllowedTargetPermission.candidates.clear()
                if action == "create":
                    record = self.Source.create(name="Created", basis_id=hidden.pk)
                else:
                    record.update(basis_id=hidden.pk)
                self._assert_hidden_candidate(hidden.pk)
                row = self.Source.Interface._model.objects.get(pk=record.id)
                self.assertEqual(row.basis_id, hidden.pk)

    def test_supplied_model_properties_are_not_trusted_for_authorization(self) -> None:
        """Refresh selected primary-key targets instead of trusting stale supplied flags."""
        for state in ("stale", "changed"):
            for operation in ("create", "update"):
                with self.subTest(state=state, operation=operation):
                    supplied = self.Target._base_manager.create(
                        visible=True, allowed=state == "stale"
                    )
                    if state == "stale":
                        self.Target._base_manager.filter(pk=supplied.pk).update(
                            allowed=False
                        )
                    else:
                        supplied.allowed = True
                    retained = self.Target._base_manager.create(
                        visible=True, allowed=True
                    )
                    child = self.Source.create(
                        ignore_permission=True, name="Before", basis=retained
                    )
                    mutate = (
                        self.Source.create if operation == "create" else child.update
                    )
                    before = self.Source.Interface._model.objects.count()
                    _AllowedTargetPermission.candidates.clear()
                    with self.assertRaises(PermissionCheckError):
                        mutate(name="Denied", basis=supplied)
                    self.assertEqual(
                        self.Source.Interface._model.objects.count(), before
                    )
                    row = self.Source.Interface._model.objects.get(pk=child.id)
                    self.assertEqual((row.name, row.basis_id), ("Before", retained.pk))
                    self.assertTrue(_AllowedTargetPermission.candidates)
                    self.assertTrue(
                        all(
                            not target.allowed
                            for target, _ in _AllowedTargetPermission.candidates
                        )
                    )


_nullable_rule_candidates: list[tuple[object, object]] = []


def _nullable_key_alias_rule(candidate: Any) -> bool:
    """Require both relation aliases to describe the same nullable target key."""
    _nullable_rule_candidates.append((candidate.basis, candidate.basis_id))
    if candidate.basis_id is None:
        return candidate.basis is None
    return candidate.basis is not None and candidate.basis.code == candidate.basis_id


class _ObserveNullableRelationPermission(OverrideManagerPermission):
    __read__: ClassVar[list[str]] = ["public"]
    __create__: ClassVar[list[str]] = ["public"]
    __update__: ClassVar[list[str]] = ["public"]
    candidates: ClassVar[list[tuple[object, object]]] = []

    def check_permission(self, action: str, attribute: str) -> bool:
        """Record both aliases without authorizing against a target with a null key."""
        if action in {"create", "update"}:
            self.candidates.append((self.instance.basis, self.instance.basis_id))
        return super().check_permission(action, attribute)


class NullableTargetKeyPermissionContextTests(GeneralManagerTransactionTestCase):
    Target: ClassVar[type[models.Model]]
    Source: ClassVar[type[GeneralManager]]
    _created_targets: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        """Build a nullable alternate-key FK with real permission and rule observers."""

        class PermissionNullableTarget(models.Model):
            code = models.CharField(max_length=20, unique=True, null=True, blank=True)

            class Meta:
                app_label = "general_manager"

        class PermissionNullableSource(models.Model):
            name = models.CharField(max_length=40)
            basis = models.ForeignKey(
                PermissionNullableTarget,
                to_field="code",
                null=True,
                blank=True,
                on_delete=models.CASCADE,
            )

            class Meta:
                app_label = "general_manager"

        class NullableSource(GeneralManager):
            class Interface(ExistingModelInterface):
                model = PermissionNullableSource

                class Meta:
                    rules: ClassVar[list[Rule[Any]]] = [
                        Rule(_nullable_key_alias_rule, ignore_if_none=False)
                    ]

            Permission = _ObserveNullableRelationPermission

        cls.Target = PermissionNullableTarget
        cls.Source = NullableSource
        cls.general_manager_classes = [NullableSource]
        super().setUpClass()
        cls._created_targets = create_test_models(
            connections["default"], (PermissionNullableTarget,)
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove managed sources before their separately owned nullable-key target."""
        try:
            cls._drop_created_test_models()
        finally:
            try:
                with connections["default"].schema_editor() as editor:
                    drop_test_models(editor, reversed(cls._created_targets))
            finally:
                cls._created_targets = []
                super().tearDownClass()

    def test_saved_target_with_null_alternate_key_is_a_null_relation(self) -> None:
        """Expose null aliases to rules and permissions for a saved null-key target."""
        target = self.Target.objects.create(code=None)
        old_target = self.Target.objects.create(code="old")
        for action in ("create", "update"):
            with self.subTest(action=action):
                if action == "update":
                    record = self.Source.create(
                        ignore_permission=True, name="Before", basis=old_target
                    )
                _ObserveNullableRelationPermission.candidates.clear()
                _nullable_rule_candidates.clear()
                if action == "create":
                    record = self.Source.create(name="Null relation", basis=target)
                else:
                    record.update(basis=target)
                row = self.Source.Interface._model.objects.get(pk=record.id)
                self.assertIsNone(row.basis_id)
                self.assertIsNone(row.basis)
                self.assertTrue(_ObserveNullableRelationPermission.candidates)
                for (
                    relation,
                    identifier,
                ) in _ObserveNullableRelationPermission.candidates:
                    self.assertIsNone(relation)
                    self.assertIsNone(identifier)
                self.assertTrue(_nullable_rule_candidates)
                for relation, identifier in _nullable_rule_candidates:
                    self.assertIsNone(relation)
                    self.assertIsNone(identifier)

    def test_stale_saved_model_rules_resolve_the_current_alternate_key_target(
        self,
    ) -> None:
        """Rules and permission views must identify the row selected by the persisted key."""
        for operation in ("create", "update"):
            with self.subTest(operation=operation):
                supplied = self.Target.objects.create(code=f"allow-{operation}")
                other = self.Target.objects.create(code=f"deny-{operation}")
                retained = self.Target.objects.create(code=f"retain-{operation}")
                child = self.Source.create(
                    ignore_permission=True, name="Before", basis=retained
                )
                self.Target.objects.filter(pk=supplied.pk).update(
                    code=f"moved-{operation}"
                )
                self.Target.objects.filter(pk=other.pk).update(code=supplied.code)
                _nullable_rule_candidates.clear()
                _ObserveNullableRelationPermission.candidates.clear()
                record = (
                    self.Source.create(name="Stale target", basis=supplied)
                    if operation == "create"
                    else child.update(basis=supplied)
                )
                row = self.Source.Interface._model.objects.get(pk=record.id)
                self.assertEqual(row.basis.pk, other.pk)
                self.assertTrue(_nullable_rule_candidates)
                for target, key in _nullable_rule_candidates:
                    self.assertEqual((target.pk, key), (other.pk, supplied.code))
                for target, key in _ObserveNullableRelationPermission.candidates:
                    self.assertEqual((target.pk, key), (other.pk, supplied.code))

    def test_unsaved_model_without_pk_keeps_django_rejection(self) -> None:
        """Do not bypass Django's unsaved-object guard by extracting an existing key."""
        self.Target.objects.create(code="existing-key")
        unsaved = self.Target(code="existing-key")
        with self.assertRaisesRegex(ValueError, "unsaved related object"):
            self.Source.create(ignore_permission=True, name="Unsaved", basis=unsaved)
        self.assertFalse(self.Source.Interface._model.objects.exists())

    def test_alternate_key_model_assignment_keeps_django_validation(self) -> None:
        """Django still validates a supplied model's type and router compatibility."""
        valid = self.Target.objects.create(code="valid")
        child = self.Source.create(ignore_permission=True, name="Before", basis=valid)
        model = self.Source.Interface._model
        wrong_type = model(pk=1)
        wrong_type.code = valid.code

        for mutate in (self.Source.create, child.update):
            with self.subTest(operation=mutate.__name__):
                with self.assertRaises(InvalidFieldValueError):
                    mutate(ignore_permission=True, name="Wrong type", basis=wrong_type)
                with (
                    patch("django.db.router.allow_relation", return_value=False),
                    self.assertRaises(InvalidFieldValueError),
                ):
                    mutate(ignore_permission=True, name="Router rejected", basis=valid)
                self.assertEqual(
                    list(model.objects.values_list("name", "basis_id")),
                    [("Before", "valid")],
                )

    def test_explicit_pk_models_resolve_current_target_or_sql_null(self) -> None:
        """PK-bearing unsaved instances follow the actual key, including SQL nulls."""
        for operation in ("create", "update"):
            for null_key in (False, True):
                with self.subTest(operation=operation, null_key=null_key):
                    suffix = f"{operation}-{int(null_key)}"
                    original = self.Target.objects.create(code=f"old-{suffix}")
                    current = self.Target.objects.create(code=f"key-{suffix}")
                    retained = self.Target.objects.create(code=f"r-{suffix}")
                    reference = self.Target(
                        pk=original.pk, code=None if null_key else current.code
                    )
                    self.assertTrue(reference._state.adding)
                    child = self.Source.create(
                        ignore_permission=True, name="Before", basis=retained
                    )
                    _nullable_rule_candidates.clear()
                    _ObserveNullableRelationPermission.candidates.clear()
                    record = (
                        self.Source.create(name="Explicit PK", basis=reference)
                        if operation == "create"
                        else child.update(basis=reference)
                    )
                    row = self.Source.Interface._model.objects.get(pk=record.id)
                    expected_pk = None if null_key else current.pk
                    self.assertEqual(
                        None if row.basis is None else row.basis.pk, expected_pk
                    )
                    for observed, key in (
                        *_nullable_rule_candidates,
                        *_ObserveNullableRelationPermission.candidates,
                    ):
                        self.assertEqual(
                            None if observed is None else observed.pk, expected_pk
                        )
                        self.assertEqual(key, None if null_key else current.code)


@override_settings(DATABASE_ROUTERS=[])
class CustomConstructorRelationPermissionContextTests(
    GeneralManagerTransactionTestCase
):
    databases: ClassVar[set[str]] = {"default", "secondary"}
    _secondary_created_models: ClassVar[list[type[models.Model]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        """Use isolated wrapped-target fixtures so constructor metadata cannot leak."""

        class PermissionConstructorTarget(models.Model):
            allowed = models.BooleanField()

            class Meta:
                app_label = "general_manager"

        class ConstructorTarget(GeneralManager):
            calls: ClassVar[int] = 0

            def __init__(self, *args, **kwargs):
                """Retain the supported custom constructor's ordinary public loading path."""
                type(self).calls += 1
                super().__init__(*args, **kwargs)

            class Interface(ExistingModelInterface):
                model = PermissionConstructorTarget

        class PermissionConstructorSource(models.Model):
            name = models.CharField(max_length=40)
            basis = models.ForeignKey(
                PermissionConstructorTarget, on_delete=models.CASCADE
            )

            class Meta:
                app_label = "general_manager"

        class SecondarySource(GeneralManager):
            class Interface(ExistingModelInterface):
                model = PermissionConstructorSource
                database = "secondary"

            Permission = _AllowedTargetPermission

        class DefaultSource(GeneralManager):
            class Interface(ExistingModelInterface):
                model = PermissionConstructorSource
                database = "default"

            Permission = _AllowedTargetPermission

        cls.Target, cls.WrappedTarget = PermissionConstructorTarget, ConstructorTarget
        cls.Source, cls.DefaultSource = SecondarySource, DefaultSource
        cls.general_manager_classes = [
            ConstructorTarget,
            SecondarySource,
            DefaultSource,
        ]
        super().setUpClass()
        cls._secondary_created_models = create_test_models(
            connections["secondary"],
            (
                PermissionConstructorTarget,
                PermissionConstructorTarget.history.model,
                PermissionConstructorSource,
                PermissionConstructorSource.history.model,
            ),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove owned source/target tables before restoring all manager registries."""
        try:
            cls._drop_created_test_models()
        finally:
            try:
                with connections["secondary"].schema_editor() as editor:
                    drop_test_models(editor, reversed(cls._secondary_created_models))
            finally:
                cls._secondary_created_models = []
                super().tearDownClass()

    def setUp(self) -> None:
        """Create conflicting same-primary-key target rows on the two databases."""
        super().setUp()
        for alias, allowed in (("default", True), ("secondary", False)):
            self.Target.objects.using(alias).create(pk=1, allowed=allowed)
            self.Target.objects.using(alias).create(pk=2, allowed=not allowed)
        self.WrappedTarget.calls = 0
        _AllowedTargetPermission.candidates.clear()

    def test_custom_constructor_cannot_authorize_a_differently_routed_row(self) -> None:
        """Reject reconstruction mismatch before permission predicates or source writes."""
        self.Target.objects.using("secondary").create(pk=3, allowed=False)
        child = self.Source.create(ignore_permission=True, name="Before", basis_id=2)
        for operation in ("create", "update"):
            for identifier in (1, 3):
                with self.subTest(operation=operation, identifier=identifier):
                    _AllowedTargetPermission.candidates.clear()
                    mutate = (
                        self.Source.create if operation == "create" else child.update
                    )
                    with self.assertRaisesRegex(
                        PermissionError, "resolved ORM relation"
                    ):
                        mutate(name="Denied", basis_id=identifier)
                    self.assertEqual(_AllowedTargetPermission.candidates, [])
                    row = self.Source.Interface._model.objects.using("secondary").get(
                        pk=child.id
                    )
                    self.assertEqual((row.name, row.basis_id), ("Before", 2))
                    self.assertEqual(
                        self.Source.Interface._model.objects.using("secondary").count(),
                        1,
                    )
                    self.assertFalse(
                        self.Source.Interface._model.objects.using("default").exists()
                    )

    def test_custom_interface_constructor_must_preserve_the_resolved_database(
        self,
    ) -> None:
        """The related interface initializer runs and must preserve the chosen row."""
        interface = self.WrappedTarget.Interface
        original_init = interface.__init__
        loaded: list[str | None] = []

        def initialize(instance: Any, *args: Any, **kwargs: Any) -> None:
            original_init(instance, *args, **kwargs)
            loaded.append(instance._instance._state.db)

        with (
            patch.object(self.WrappedTarget, "__init__", GeneralManager.__init__),
            patch.object(interface, "__init__", initialize),
        ):
            with self.assertRaises(PermissionRelationResolutionError):
                self.Source.create(name="Wrong database", basis_id=2)
            created = self.DefaultSource.create(name="Matching database", basis_id=1)
        self.assertTrue(loaded)
        self.assertEqual(set(loaded), {"default"})
        self.assertFalse(
            self.Source.Interface._model.objects.using("secondary").exists()
        )
        self.assertTrue(
            self.DefaultSource.Interface._model.objects.using("default")
            .filter(pk=created.id, basis_id=1)
            .exists()
        )

    def test_custom_constructor_on_matching_database_still_runs_and_allows(
        self,
    ) -> None:
        """Preserve ordinary custom construction when it reconstructs the selected row."""
        created = self.DefaultSource.create(name="Allowed", basis_id=1)
        created.update(name="Updated")
        self.assertGreater(self.WrappedTarget.calls, 0)
        self.assertTrue(_AllowedTargetPermission.candidates)
        for target, identifier in _AllowedTargetPermission.candidates:
            self.assertIsInstance(target, self.WrappedTarget)
            self.assertEqual(
                (target._interface._instance._state.db, identifier), ("default", 1)
            )
        row = self.Source.Interface._model.objects.using("default").get(pk=created.id)
        self.assertEqual((row.name, row.basis_id), ("Updated", 1))
        self.assertFalse(
            self.Source.Interface._model.objects.using("secondary").exists()
        )
