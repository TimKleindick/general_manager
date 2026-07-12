from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from django.apps import apps
from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.cache.signals import data_change
from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    _FieldDescriptorBuilder,
    _can_prefetch_direct_relation,
    _direct_relation_prefetch_source_rows,
    _general_manager_accessor,
    _general_manager_many_accessor,
    _prefetch_direct_relation_managers,
    build_field_descriptors,
)
from general_manager.bootstrap import initialize_general_manager_classes
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.orm_interface import OrmInterfaceBase


def test_general_manager_many_accessor_uses_explicit_relation_field_name() -> None:
    manager_class = Mock()
    filter_result = object()
    manager_class.filter.return_value = filter_result

    related_model = Mock()
    relation_field = Mock(spec=models.Field)
    relation_field.name = "reviewer"
    related_model._meta.get_field.return_value = relation_field

    accessor = _general_manager_many_accessor(
        accessor_name="reviewassignment_set",
        related_model=related_model,
        general_manager_class=manager_class,
        source_model=cast(type[models.Model], models.Model),
        relation_field_name="reviewer",
    )

    interface_instance = type("InterfaceInstance", (), {"pk": 42})()
    result = accessor(interface_instance)

    related_model._meta.get_field.assert_called_once_with("reviewer")
    manager_class.filter.assert_called_once_with(reviewer=42)
    assert result is filter_result


def test_general_manager_many_accessor_skips_row_scan_for_prefetched_source() -> None:
    class PrefetchSourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class PrefetchRelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class RelatedManager:
        class Interface:
            _model = PrefetchRelatedModel
            database = None
            _soft_delete_default = False

        @classmethod
        def filter(cls, **_kwargs: object) -> object:
            raise AssertionError

    class RunContext:
        def get_orm_model_row(
            self,
            model: type[object],
            primary_key: object,
            database_alias: object,
        ) -> object:
            assert model is PrefetchSourceModel
            assert primary_key == 7
            assert database_alias is None
            return source_row

        def get_orm_model_relation_prefetched_keys(
            self,
            model: type[object],
            database_alias: object,
            accessor_name: str,
        ) -> frozenset[tuple[int, None]]:
            assert model is PrefetchSourceModel
            assert database_alias is None
            assert accessor_name == "members"
            return frozenset({(7, None)})

        def get_orm_model_row_items(
            self,
            _model: type[object],
        ) -> tuple[object, ...]:
            raise AssertionError

    related_queryset = Mock()
    relation_manager = SimpleNamespace(all=Mock(return_value=related_queryset))
    source_row = PrefetchSourceModel(id=7)
    source_row.members = relation_manager
    interface_instance = SimpleNamespace(_instance=source_row, pk=7)
    accessor = _general_manager_many_accessor(
        accessor_name="members",
        related_model=PrefetchRelatedModel,
        general_manager_class=RelatedManager,
        source_model=PrefetchSourceModel,
        relation_filter_name="sources",
    )

    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=RunContext(),
    ):
        result = accessor(interface_instance)

    assert result._data is related_queryset
    assert result.filters == {"sources": [7]}
    relation_manager.all.assert_called_once_with()


def test_general_manager_many_accessor_falls_back_when_source_row_is_not_indexed() -> (
    None
):
    class UnindexedPrefetchSourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class UnindexedPrefetchRelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class RelatedManager:
        filter = Mock(return_value="fallback-bucket")

        class Interface:
            _model = UnindexedPrefetchRelatedModel
            database = None
            _soft_delete_default = False

    class RunContext:
        def get_orm_model_row(
            self,
            model: type[object],
            primary_key: object,
            database_alias: object,
        ) -> object | None:
            assert model is UnindexedPrefetchSourceModel
            assert primary_key == 7
            assert database_alias is None
            return None

    source_row = UnindexedPrefetchSourceModel(id=7)
    interface_instance = SimpleNamespace(_instance=source_row, pk=7)
    accessor = _general_manager_many_accessor(
        accessor_name="members",
        related_model=UnindexedPrefetchRelatedModel,
        general_manager_class=RelatedManager,
        source_model=UnindexedPrefetchSourceModel,
        relation_filter_name="sources",
    )

    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=RunContext(),
    ):
        result = accessor(interface_instance)

    assert result == "fallback-bucket"
    RelatedManager.filter.assert_called_once_with(sources=7)


def test_general_manager_many_accessor_falls_back_for_soft_delete_managers() -> None:
    class SoftDeletePrefetchSourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class SoftDeletePrefetchRelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class RelatedManager:
        filter = Mock(return_value="fallback-bucket")

        class Interface:
            _model = SoftDeletePrefetchRelatedModel
            database = None
            _soft_delete_default = True

    source_row = SoftDeletePrefetchSourceModel(id=7)
    interface_instance = SimpleNamespace(_instance=source_row, pk=7)
    accessor = _general_manager_many_accessor(
        accessor_name="members",
        related_model=SoftDeletePrefetchRelatedModel,
        general_manager_class=RelatedManager,
        source_model=SoftDeletePrefetchSourceModel,
        relation_filter_name="sources",
    )

    result = accessor(interface_instance)

    assert result == "fallback-bucket"
    RelatedManager.filter.assert_called_once_with(sources=7)


def test_general_manager_many_accessor_falls_back_for_database_mismatch() -> None:
    class DatabasePrefetchSourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class DatabasePrefetchRelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class RelatedManager:
        filter = Mock(return_value="fallback-bucket")

        class Interface:
            _model = DatabasePrefetchRelatedModel
            database = "default"
            _soft_delete_default = False

    source_row = DatabasePrefetchSourceModel(id=7)
    source_row._state.db = "replica"
    interface_instance = SimpleNamespace(_instance=source_row, pk=7)
    accessor = _general_manager_many_accessor(
        accessor_name="members",
        related_model=DatabasePrefetchRelatedModel,
        general_manager_class=RelatedManager,
        source_model=DatabasePrefetchSourceModel,
        relation_filter_name="sources",
    )

    result = accessor(interface_instance)

    assert result == "fallback-bucket"
    RelatedManager.filter.assert_called_once_with(sources=7)


def test_general_manager_fk_accessor_uses_trusted_hydration_for_loaded_row() -> None:
    related = SimpleNamespace(pk=7)
    constructor_calls: list[object] = []
    calls: list[tuple[object, object | None]] = []
    trusted_result = object()

    class RelatedManager:
        def __init__(self, pk: object) -> None:
            constructor_calls.append(pk)

        @classmethod
        def _from_trusted_orm_instance(
            cls,
            instance: object,
            *,
            search_date: object | None = None,
        ) -> object:
            calls.append((instance, search_date))
            return trusted_result

    accessor = _general_manager_accessor("owner", RelatedManager)
    interface_instance = SimpleNamespace(_instance=SimpleNamespace(owner=related))

    assert accessor(interface_instance) is trusted_result
    assert constructor_calls == []
    assert calls == [(related, None)]


def test_general_manager_fk_accessor_uses_raw_id_without_loading_relation() -> None:
    calls: list[object] = []

    class RelatedManager:
        def __init__(self, pk: object) -> None:
            calls.append(pk)

    class SourceInstance:
        owner_id = 7
        _state = SimpleNamespace(fields_cache={})
        loaded_relation = False

        @property
        def owner(self) -> object:
            self.loaded_relation = True
            return object()

    accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    interface_instance = SimpleNamespace(_instance=SourceInstance())

    result = accessor(interface_instance)

    assert isinstance(result, RelatedManager)
    assert calls == [7]
    assert not interface_instance._instance.loaded_relation


def test_general_manager_fk_accessor_reuses_raw_id_manager_in_run_context() -> None:
    calls: list[object] = []

    class RelatedManager:
        def __init__(self, pk: object) -> None:
            calls.append(pk)

    source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(fields_cache={}),
    )
    accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    interface_instance = SimpleNamespace(_instance=source)

    with CalculationRunContext():
        first = accessor(interface_instance)
        second = accessor(interface_instance)

    assert first is second
    assert calls == [7]


def test_general_manager_fk_accessor_batches_indexed_related_rows() -> None:
    class DirectSourceModel(models.Model):
        owner_id = models.IntegerField(null=True)

        class Meta:
            app_label = "field_descriptor_tests"

    class DirectRelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_tests"

    class DirectRelatedInterface:
        __init__ = OrmInterfaceBase.__init__
        _model = DirectRelatedModel
        database = None
        _soft_delete_default = False

    class DirectRelatedManager:
        __init__ = GeneralManager.__init__
        Interface = DirectRelatedInterface

    trusted_managers: list[object] = []

    def trusted_hydrate(
        cls: type[DirectRelatedManager],
        instance: DirectRelatedModel,
        *,
        search_date: object | None = None,
    ) -> object:
        del search_date
        manager = cls.__new__(cls)
        trusted_managers.append((instance.pk, manager))
        return manager

    DirectRelatedManager._from_trusted_orm_instance = classmethod(trusted_hydrate)  # type: ignore[method-assign]
    source_rows = (
        DirectSourceModel(id=1, owner_id=7),
        DirectSourceModel(id=2, owner_id=7),
    )
    related_row = DirectRelatedModel(id=7)

    class RelatedObjects:
        def __init__(self) -> None:
            self.calls: list[set[object]] = []

        def in_bulk(self, ids: set[object]) -> dict[object, DirectRelatedModel]:
            self.calls.append(ids)
            return {7: related_row}

    related_objects = RelatedObjects()

    class RunContext:
        def __init__(self) -> None:
            self.relation_managers: dict[object, object] = {}
            self.prefetched: frozenset[tuple[object, object | None]] = frozenset()

        def get_orm_model_row_items(
            self,
            model: type[object],
        ) -> tuple[tuple[tuple[object, object | None], object], ...]:
            assert model is DirectSourceModel
            return tuple(((row.pk, None), row) for row in source_rows)

        def get_orm_direct_relation_prefetched_keys(
            self,
            _model: type[object],
            _database_alias: object | None,
            _accessor_name: str,
        ) -> frozenset[tuple[object, object | None]]:
            return self.prefetched

        def add_orm_direct_relation_prefetched_keys(
            self,
            _model: type[object],
            _database_alias: object | None,
            _accessor_name: str,
            row_keys: list[tuple[object, object | None]],
        ) -> None:
            self.prefetched = frozenset(row_keys)

        def get_orm_relation_manager(self, key: object) -> object:
            return self.relation_managers.get(key)

        def set_orm_relation_manager(self, key: object, value: object) -> None:
            self.relation_managers[key] = value

    run_context = RunContext()
    accessor = _general_manager_accessor(
        "owner",
        DirectRelatedManager,
        raw_id_name="owner_id",
        related_model=DirectRelatedModel,
    )

    with (
        patch(
            "general_manager.cache.run_context.current_calculation_run_context",
            return_value=run_context,
        ),
        patch.object(DirectRelatedModel._meta, "default_manager", related_objects),
    ):
        first = accessor(SimpleNamespace(_instance=source_rows[0]))
        second = accessor(SimpleNamespace(_instance=source_rows[1]))

    assert first is second
    assert related_objects.calls == [{7}]
    assert len(trusted_managers) == 1

    for row in source_rows:
        row.profile = related_row
    run_context.prefetched = frozenset()

    reverse_accessor = _general_manager_accessor(
        "profile",
        DirectRelatedManager,
        related_model=DirectRelatedModel,
    )

    def fake_prefetch(rows: list[models.Model], _accessor_name: str) -> None:
        for row in rows:
            row.profile = related_row

    with (
        patch(
            "general_manager.cache.run_context.current_calculation_run_context",
            return_value=run_context,
        ),
        patch(
            "general_manager.interface.capabilities.orm_utils.field_descriptors._prefetch_relation_in_chunks",
            side_effect=fake_prefetch,
        ) as prefetch,
    ):
        reverse_first = reverse_accessor(SimpleNamespace(_instance=source_rows[0]))
        reverse_second = reverse_accessor(SimpleNamespace(_instance=source_rows[1]))

    assert reverse_first is reverse_second
    prefetch.assert_called_once()


def test_direct_relation_prefetch_helpers_fail_closed_for_unsafe_rows() -> None:
    class SourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_guard_tests"

    class RelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_guard_tests"

    source = SourceModel(id=1)

    class NoRowIndex:
        pass

    assert _direct_relation_prefetch_source_rows(NoRowIndex(), source, None) is None

    class NonCallableRowIndex:
        get_orm_model_row_items = 1

    assert (
        _direct_relation_prefetch_source_rows(NonCallableRowIndex(), source, None)
        is None
    )

    class EmptyRowIndex:
        def get_orm_model_row_items(self, _model: type[object]) -> tuple[object, ...]:
            return ()

    assert _direct_relation_prefetch_source_rows(EmptyRowIndex(), source, None) is None

    class MismatchedRowIndex:
        def get_orm_model_row_items(
            self, _model: type[object]
        ) -> tuple[tuple[tuple[object, object], object], ...]:
            return (((1, "replica"), source), ((2, None), object()))

    assert (
        _direct_relation_prefetch_source_rows(MismatchedRowIndex(), source, None)
        is None
    )

    deferred = SourceModel(id=2)
    deferred.get_deferred_fields = lambda: {"name"}  # type: ignore[method-assign]

    class DeferredRowIndex:
        def get_orm_model_row_items(
            self, _model: type[object]
        ) -> tuple[tuple[tuple[object, object], object], ...]:
            return (((2, None), deferred),)

    assert (
        _direct_relation_prefetch_source_rows(DeferredRowIndex(), source, None) is None
    )

    class RelatedInterface:
        __init__ = OrmInterfaceBase.__init__
        _model = RelatedModel
        database = None
        _soft_delete_default = False

    class RelatedManager:
        __init__ = GeneralManager.__init__
        Interface = RelatedInterface

    RelatedManager._from_trusted_orm_instance = staticmethod(lambda row: row)  # type: ignore[attr-defined]
    interface_instance = SimpleNamespace(_search_date=None)

    assert not _can_prefetch_direct_relation(
        interface_instance,
        RelatedManager,
        None,
        None,
    )
    interface_instance._search_date = object()
    assert not _can_prefetch_direct_relation(
        interface_instance,
        RelatedManager,
        RelatedModel,
        None,
    )
    interface_instance._search_date = None

    RelatedManager.Interface._model = SourceModel
    assert not _can_prefetch_direct_relation(
        interface_instance,
        RelatedManager,
        RelatedModel,
        None,
    )
    RelatedManager.Interface._model = RelatedModel
    RelatedManager.Interface.database = "replica"
    assert not _can_prefetch_direct_relation(
        interface_instance,
        RelatedManager,
        RelatedModel,
        "default",
    )
    RelatedManager.Interface.database = None

    with patch.object(RelatedModel._meta, "use_soft_delete", True, create=True):
        assert not _can_prefetch_direct_relation(
            interface_instance,
            RelatedManager,
            RelatedModel,
            None,
        )
    RelatedManager.Interface._soft_delete_default = True
    assert not _can_prefetch_direct_relation(
        interface_instance,
        RelatedManager,
        RelatedModel,
        None,
    )
    RelatedManager.Interface._soft_delete_default = False

    class CustomInitManager:
        Interface = RelatedInterface

        def __init__(self, _pk: object) -> None:
            pass

    CustomInitManager._from_trusted_orm_instance = staticmethod(lambda row: row)  # type: ignore[attr-defined]
    assert not _can_prefetch_direct_relation(
        interface_instance,
        CustomInitManager,
        RelatedModel,
        None,
    )

    class CustomInterface:
        def __init__(self) -> None:
            pass

        _model = RelatedModel
        database = None
        _soft_delete_default = False

    class CustomInterfaceManager:
        __init__ = GeneralManager.__init__
        Interface = CustomInterface

    CustomInterfaceManager._from_trusted_orm_instance = staticmethod(lambda row: row)  # type: ignore[attr-defined]
    assert not _can_prefetch_direct_relation(
        interface_instance,
        CustomInterfaceManager,
        RelatedModel,
        None,
    )

    class NoTrustedManager:
        __init__ = GeneralManager.__init__
        Interface = RelatedInterface

    assert not _can_prefetch_direct_relation(
        interface_instance,
        NoTrustedManager,
        RelatedModel,
        None,
    )

    assert _can_prefetch_direct_relation(
        interface_instance,
        RelatedManager,
        RelatedModel,
        None,
    )


def test_direct_relation_prefetch_handles_cached_and_missing_related_rows() -> None:
    class SourceModel(models.Model):
        target_id = models.IntegerField(null=True)

        class Meta:
            app_label = "field_descriptor_branch_tests"

    class RelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_branch_tests"

    class RelatedInterface:
        __init__ = OrmInterfaceBase.__init__
        _model = RelatedModel
        database = None
        _soft_delete_default = False

    class RelatedManager:
        __init__ = GeneralManager.__init__
        Interface = RelatedInterface

    hydrated: list[object] = []

    def trusted_hydrate(row: RelatedModel) -> object:
        hydrated.append(row)
        return object()

    RelatedManager._from_trusted_orm_instance = staticmethod(trusted_hydrate)  # type: ignore[attr-defined]
    source_rows = (
        SourceModel(id=1, target_id=7),
        SourceModel(id=2, target_id=None),
        SourceModel(id=3, target_id=8),
    )
    for row in source_rows:
        row._state.db = "replica"
    good_related = RelatedModel(id=7)
    invalid_related = RelatedModel(id=8)
    invalid_related.pk = []

    class RelatedObjects:
        def __init__(self) -> None:
            self.aliases: list[str] = []
            self.ids: list[set[object]] = []

        def using(self, alias: str) -> "RelatedObjects":
            self.aliases.append(alias)
            return self

        def in_bulk(self, ids: set[object]) -> dict[object, RelatedModel]:
            self.ids.append(ids)
            return {7: good_related, 8: invalid_related}

    related_objects = RelatedObjects()

    class RunContext:
        def __init__(self) -> None:
            self.prefetched: frozenset[tuple[object, object | None]] = frozenset()
            self.relation_managers: dict[object, object] = {}

        def get_orm_model_row_items(
            self, _model: type[object]
        ) -> tuple[tuple[tuple[object, object | None], SourceModel], ...]:
            return tuple(((row.pk, row._state.db), row) for row in source_rows)

        def get_orm_direct_relation_prefetched_keys(
            self,
            _model: type[object],
            _database_alias: object | None,
            _accessor_name: str,
        ) -> frozenset[tuple[object, object | None]]:
            return self.prefetched

        def add_orm_direct_relation_prefetched_keys(
            self,
            _model: type[object],
            _database_alias: object | None,
            _accessor_name: str,
            row_keys: list[tuple[object, object | None]],
        ) -> None:
            self.prefetched = frozenset(row_keys)

        def get_orm_relation_manager(self, key: object) -> object:
            return self.relation_managers.get(key)

        def set_orm_relation_manager(self, key: object, value: object) -> None:
            self.relation_managers[key] = value

    context = RunContext()
    source_interface = SimpleNamespace(_instance=source_rows[0])
    with (
        patch(
            "general_manager.cache.run_context.current_calculation_run_context",
            return_value=context,
        ),
        patch.object(RelatedModel._meta, "default_manager", related_objects),
    ):
        assert _prefetch_direct_relation_managers(
            source_interface,
            accessor_name="target",
            manager_type=RelatedManager,
            related_model=RelatedModel,
            raw_id_name="target_id",
        )
        assert _prefetch_direct_relation_managers(
            source_interface,
            accessor_name="target",
            manager_type=RelatedManager,
            related_model=RelatedModel,
            raw_id_name="target_id",
        )

    assert related_objects.aliases == ["replica"]
    assert related_objects.ids == [{7, 8}]
    assert hydrated == [good_related, invalid_related]

    class ReverseSourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_branch_tests"

        @property
        def profile(self) -> object:
            state = getattr(self, "_profile_state", "missing")
            if state == "missing":
                raise ObjectDoesNotExist
            return getattr(self, "_profile_value", None)

    reverse_rows = (
        ReverseSourceModel(id=1),
        ReverseSourceModel(id=2),
        ReverseSourceModel(id=3),
        ReverseSourceModel(id=4),
    )
    for row in reverse_rows:
        row._state.db = "replica"
    reverse_rows[1]._profile_state = "empty"
    reverse_rows[2]._profile_state = "invalid"
    invalid_profile = RelatedModel(id=9)
    invalid_profile.pk = []
    reverse_rows[2]._profile_value = invalid_profile
    reverse_rows[3]._profile_state = "good"
    reverse_rows[3]._profile_value = good_related

    class ReverseContext(RunContext):
        def get_orm_model_row_items(
            self, _model: type[object]
        ) -> tuple[tuple[tuple[object, object | None], ReverseSourceModel], ...]:
            return tuple(((row.pk, row._state.db), row) for row in reverse_rows)

    reverse_context = ReverseContext()
    with (
        patch(
            "general_manager.cache.run_context.current_calculation_run_context",
            return_value=reverse_context,
        ),
        patch(
            "general_manager.interface.capabilities.orm_utils.field_descriptors._prefetch_relation_in_chunks"
        ) as prefetch,
    ):
        assert _prefetch_direct_relation_managers(
            SimpleNamespace(_instance=reverse_rows[0]),
            accessor_name="profile",
            manager_type=RelatedManager,
            related_model=RelatedModel,
            raw_id_name=None,
        )

    prefetch.assert_called_once()


def test_direct_relation_accessor_preserves_loaded_and_fallback_paths() -> None:
    class RelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_accessor_tests"

    related = RelatedModel(id=7)
    tracked: list[bool] = []

    class RelatedManager:
        def __init__(self, pk: object) -> None:
            self.pk = pk

        def _track_own_identification_dependency_active(self) -> None:
            tracked.append(True)

    class Context:
        def __init__(self) -> None:
            self.values: dict[object, object] = {}

        def get_orm_relation_manager(self, key: object) -> object:
            return self.values.get(key)

        def set_orm_relation_manager(self, key: object, value: object) -> None:
            self.values[key] = value

    context = Context()
    loaded_accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    loaded_source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(db=None, fields_cache={"owner": related}),
    )
    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=context,
    ):
        first = loaded_accessor(SimpleNamespace(_instance=loaded_source))
        with DependencyTracker():
            second = loaded_accessor(SimpleNamespace(_instance=loaded_source))

    assert isinstance(first, RelatedManager)
    assert second is first
    assert first.pk == 7
    assert tracked == [True]

    class UnhashableContext(Context):
        def get_orm_relation_manager(self, _key: object) -> object:
            raise TypeError

    fallback_accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    unhashable_source = SimpleNamespace(
        owner_id=[],
        _state=SimpleNamespace(db=None, fields_cache={}),
    )
    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=UnhashableContext(),
    ):
        fallback = fallback_accessor(SimpleNamespace(_instance=unhashable_source))

    assert isinstance(fallback, RelatedManager)
    assert fallback.pk == []

    assert (
        loaded_accessor(
            SimpleNamespace(
                _instance=SimpleNamespace(
                    owner_id=None,
                    _state=SimpleNamespace(fields_cache={}),
                )
            )
        )
        is None
    )
    assert (
        loaded_accessor(
            SimpleNamespace(
                _instance=SimpleNamespace(
                    owner_id=7,
                    _state=SimpleNamespace(fields_cache={"owner": None}),
                )
            )
        )
        is None
    )

    class MissingReverse:
        @property
        def profile(self) -> object:
            raise ObjectDoesNotExist

    reverse_accessor = _general_manager_accessor("profile", RelatedManager)
    assert reverse_accessor(SimpleNamespace(_instance=MissingReverse())) is None
    assert (
        reverse_accessor(SimpleNamespace(_instance=SimpleNamespace(profile=None)))
        is None
    )


def test_direct_relation_prefetch_returns_false_without_safe_context_or_admission() -> (
    None
):
    class SourceModel(models.Model):
        class Meta:
            app_label = "field_descriptor_context_tests"

    class RelatedModel(models.Model):
        class Meta:
            app_label = "field_descriptor_context_tests"

    class RelatedManager:
        def __init__(self, _pk: object) -> None:
            pass

    interface_instance = SimpleNamespace(_instance=SourceModel(id=1))
    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=None,
    ):
        assert not _prefetch_direct_relation_managers(
            interface_instance,
            accessor_name="owner",
            manager_type=RelatedManager,
            related_model=RelatedModel,
            raw_id_name="owner_id",
        )

    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=object(),
    ):
        assert not _prefetch_direct_relation_managers(
            SimpleNamespace(_instance=object()),
            accessor_name="owner",
            manager_type=RelatedManager,
            related_model=RelatedModel,
            raw_id_name="owner_id",
        )

    class EmptyContext:
        def get_orm_model_row_items(self, _model: type[object]) -> tuple[object, ...]:
            return ()

    class SafeInterface:
        __init__ = OrmInterfaceBase.__init__
        _model = RelatedModel
        database = None
        _soft_delete_default = False

    class SafeManager:
        __init__ = GeneralManager.__init__
        Interface = SafeInterface

    SafeManager._from_trusted_orm_instance = staticmethod(lambda row: row)  # type: ignore[attr-defined]
    with patch(
        "general_manager.cache.run_context.current_calculation_run_context",
        return_value=EmptyContext(),
    ):
        assert not _prefetch_direct_relation_managers(
            SimpleNamespace(_instance=SourceModel(id=1)),
            accessor_name="owner",
            manager_type=SafeManager,
            related_model=RelatedModel,
            raw_id_name="owner_id",
        )


def test_general_manager_fk_accessor_clears_raw_id_cache_on_data_change() -> None:
    calls: list[object] = []

    class RelatedManager:
        def __init__(self, pk: object) -> None:
            calls.append(pk)

    source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(fields_cache={}),
    )
    accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    interface_instance = SimpleNamespace(_instance=source)

    @data_change
    def mutate(instance: object) -> object:
        return instance

    with CalculationRunContext():
        first = accessor(interface_instance)
        mutate(SimpleNamespace(identification={"id": 1}))
        second = accessor(interface_instance)

    assert first is not second
    assert calls == [7, 7]


def test_general_manager_fk_accessor_scopes_raw_id_cache_by_database_alias() -> None:
    calls: list[object] = []

    class RelatedManager:
        def __init__(self, pk: object) -> None:
            calls.append(pk)

    accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    default_source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(db="default", fields_cache={}),
    )
    replica_source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(db="replica", fields_cache={}),
    )

    with CalculationRunContext():
        default_manager = accessor(SimpleNamespace(_instance=default_source))
        replica_manager = accessor(SimpleNamespace(_instance=replica_source))

    assert default_manager is not replica_manager
    assert calls == [7, 7]


def test_general_manager_fk_accessor_replays_dependency_for_cached_raw_id_manager() -> (
    None
):
    calls: list[object] = []

    class RelatedInterface:
        def __init__(self, manager_id: object) -> None:
            calls.append(manager_id)
            self.identification = {"id": manager_id}

    class RelatedManager(GeneralManager):
        pass

    RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]

    source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(fields_cache={}),
    )
    accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    interface_instance = SimpleNamespace(_instance=source)

    with CalculationRunContext():
        accessor(interface_instance)
        with DependencyTracker() as dependencies:
            accessor(interface_instance)

    assert (
        "RelatedManager",
        "identification",
        '{"id": 7}',
    ) in dependencies
    assert calls == [7]


def test_general_manager_fk_accessor_replays_custom_tracking_for_cached_raw_id_manager() -> (
    None
):
    calls: list[object] = []
    tracked: list[dict[str, object]] = []

    class RelatedInterface:
        def __init__(self, manager_id: object) -> None:
            calls.append(manager_id)
            self.identification = {"id": manager_id}

    class RelatedManager(GeneralManager):
        @classmethod
        def _track_identification_dependency_active(
            cls,
            identification: dict[str, object],
        ) -> None:
            tracked.append(dict(identification))
            DependencyTracker.track("CustomRelatedManager", "all", "")

    RelatedManager.Interface = RelatedInterface  # type: ignore[assignment]

    source = SimpleNamespace(
        owner_id=7,
        _state=SimpleNamespace(fields_cache={}),
    )
    accessor = _general_manager_accessor(
        "owner",
        RelatedManager,
        raw_id_name="owner_id",
    )
    interface_instance = SimpleNamespace(_instance=source)

    with CalculationRunContext():
        accessor(interface_instance)
        with DependencyTracker() as dependencies:
            accessor(interface_instance)

    assert tracked == [{"id": 7}]
    assert ("CustomRelatedManager", "all", "") in dependencies
    assert (
        "RelatedManager",
        "identification",
        '{"id": 7}',
    ) not in dependencies
    assert calls == [7]


def test_build_field_descriptors_disambiguates_duplicate_reverse_relations() -> None:
    class SourceModel(models.Model):
        class Meta:
            app_label = "general_manager"

    class RelatedModel(models.Model):
        fk_a: Any = models.ForeignKey(SourceModel, on_delete=models.CASCADE)
        fk_b: Any = models.ForeignKey(SourceModel, on_delete=models.CASCADE)

        class Meta:
            app_label = "general_manager"

    for model in (SourceModel, RelatedModel):
        model_key = model._meta.model_name
        if model_key not in apps.all_models["general_manager"]:
            apps.register_model("general_manager", model)
    apps.clear_cache()

    interface_cls = type("InterfaceUnderTest", (), {"_model": SourceModel})

    register_calls: list[dict[str, Any]] = []
    resolve_calls: list[str | None] = []
    original_register = _FieldDescriptorBuilder._register_collection_field
    original_resolve = _FieldDescriptorBuilder._resolve_collection_base_name

    def record_register(self: _FieldDescriptorBuilder, **kwargs: Any) -> None:
        register_calls.append(dict(kwargs))
        return original_register(self, **kwargs)

    def record_resolve(self: _FieldDescriptorBuilder, **kwargs: Any) -> str:
        resolve_calls.append(kwargs.get("relation_field_name"))
        return original_resolve(self, **kwargs)

    with (
        patch.object(
            _FieldDescriptorBuilder,
            "_register_collection_field",
            autospec=True,
            side_effect=record_register,
        ),
        patch.object(
            _FieldDescriptorBuilder,
            "_resolve_collection_base_name",
            autospec=True,
            side_effect=record_resolve,
        ),
    ):
        descriptors = build_field_descriptors(
            interface_cls,
            resolve_many=lambda *_args: None,
        )

    assert "related_model_list" in descriptors
    assert "relatedmodel_set_list" in descriptors

    relation_field_names = {
        call["relation_field_name"]
        for call in register_calls
        if call["relation_field_name"] is not None
    }
    assert relation_field_names == {"fk_a", "fk_b"}
    assert resolve_calls.count("fk_a") == 1
    assert resolve_calls.count("fk_b") == 1


def test_build_field_descriptors_resolves_string_relation_targets() -> None:
    class OwnerModel(models.Model):
        class Meta:
            app_label = "accounts"

    model = type("ModelUnderTest", (), {})
    interface_cls = type("InterfaceUnderTest", (), {"_model": model})
    owner_manager = type("OwnerManager", (), {})
    OwnerModel._general_manager_class = owner_manager  # type: ignore[attr-defined]
    owner_field = Mock(spec=models.Field)
    owner_field.name = "owner"
    owner_field.related_model = "accounts.OwnerModel"
    owner_field.null = False
    owner_field.editable = True
    owner_field.default = None
    members_field = Mock(spec=models.Field)
    members_field.name = "members"
    members_field.related_model = "accounts.OwnerModel"
    members_field.many_to_many = True
    members_field.editable = True

    field_descriptors_module = (
        "general_manager.interface.capabilities.orm_utils.field_descriptors"
    )
    with (
        patch("django.apps.apps.get_model", return_value=OwnerModel) as get_model,
        patch(f"{field_descriptors_module}._iter_model_fields", return_value=[]),
        patch(
            f"{field_descriptors_module}._iter_many_to_many_fields",
            return_value=[members_field],
        ),
        patch(f"{field_descriptors_module}._iter_reverse_relations", return_value=[]),
        patch(
            f"{field_descriptors_module}._iter_foreign_key_fields",
            return_value=[owner_field],
        ),
    ):
        descriptors = build_field_descriptors(interface_cls)

    assert descriptors["owner"].metadata["type"] is owner_manager
    assert descriptors["members_list"].metadata["type"] is owner_manager
    assert get_model.call_count == 2
    get_model.assert_any_call("accounts", "OwnerModel")


def test_build_field_descriptors_adds_raw_foreign_key_id_accessor() -> None:
    class RawIdOwnerModel(models.Model):
        class Meta:
            app_label = "general_manager"

    class RawIdChildModel(models.Model):
        owner = models.ForeignKey(
            RawIdOwnerModel,
            on_delete=models.CASCADE,
            null=True,
        )

        class Meta:
            app_label = "general_manager"

    interface_cls = type("InterfaceUnderTest", (), {"_model": RawIdChildModel})

    descriptors = build_field_descriptors(
        interface_cls,
        resolve_many=lambda *_args: None,
    )

    assert "owner" in descriptors
    assert "owner_id" in descriptors
    assert descriptors["owner_id"].metadata["type"] is int

    interface_instance = SimpleNamespace(_instance=RawIdChildModel(owner_id=42))

    assert descriptors["owner_id"].accessor(interface_instance) == 42


def test_build_field_descriptors_resolves_same_app_string_relation_targets() -> None:
    class SourceMeta:
        app_label = "billing"

    model = type("ModelUnderTest", (), {"_meta": SourceMeta})
    interface_cls = type("InterfaceUnderTest", (), {"_model": model})

    class BillingOwnerModel(models.Model):
        class Meta:
            app_label = "billing"

    owner_manager = type("OwnerManager", (), {})
    BillingOwnerModel._general_manager_class = owner_manager  # type: ignore[attr-defined]
    owner_field = Mock(spec=models.Field)
    owner_field.name = "owner"
    owner_field.related_model = "BillingOwnerModel"
    owner_field.null = False
    owner_field.editable = True
    owner_field.default = None

    field_descriptors_module = (
        "general_manager.interface.capabilities.orm_utils.field_descriptors"
    )
    with (
        patch(
            "django.apps.apps.get_model", return_value=BillingOwnerModel
        ) as get_model,
        patch(f"{field_descriptors_module}._iter_model_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_many_to_many_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_reverse_relations", return_value=[]),
        patch(
            f"{field_descriptors_module}._iter_foreign_key_fields",
            return_value=[owner_field],
        ),
    ):
        descriptors = build_field_descriptors(interface_cls)

    assert descriptors["owner"].metadata["type"] is owner_manager
    get_model.assert_called_once_with("billing", "BillingOwnerModel")


def test_build_field_descriptors_skips_invalid_string_relation_targets() -> None:
    class SourceMeta:
        app_label = "accounts"

    model = type("ModelUnderTest", (), {"_meta": SourceMeta})
    interface_cls = type("InterfaceUnderTest", (), {"_model": model})
    dotted_field = Mock(spec=models.Field)
    dotted_field.name = "owner"
    dotted_field.related_model = "accounts.OwnerModel"
    same_app_field = Mock(spec=models.Field)
    same_app_field.name = "reviewer"
    same_app_field.related_model = "ReviewerModel"
    no_app_model = type("NoAppModelUnderTest", (), {})
    no_app_interface_cls = type("NoAppInterfaceUnderTest", (), {"_model": no_app_model})
    no_app_field = Mock(spec=models.Field)
    no_app_field.name = "creator"
    no_app_field.related_model = "CreatorModel"

    field_descriptors_module = (
        "general_manager.interface.capabilities.orm_utils.field_descriptors"
    )
    with (
        patch("django.apps.apps.get_model", side_effect=LookupError),
        patch(f"{field_descriptors_module}._iter_model_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_many_to_many_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_reverse_relations", return_value=[]),
        patch(
            f"{field_descriptors_module}._iter_foreign_key_fields",
            return_value=[dotted_field, same_app_field],
        ),
    ):
        descriptors = build_field_descriptors(interface_cls)

    with (
        patch("django.apps.apps.get_model") as get_model,
        patch(f"{field_descriptors_module}._iter_model_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_many_to_many_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_reverse_relations", return_value=[]),
        patch(
            f"{field_descriptors_module}._iter_foreign_key_fields",
            return_value=[no_app_field],
        ),
    ):
        no_app_descriptors = build_field_descriptors(no_app_interface_cls)

    assert descriptors == {}
    assert no_app_descriptors == {}
    get_model.assert_not_called()


def test_build_field_descriptors_skips_non_model_app_registry_results() -> None:
    model = type("ModelUnderTest", (), {})
    interface_cls = type("InterfaceUnderTest", (), {"_model": model})
    owner_field = Mock(spec=models.Field)
    owner_field.name = "owner"
    owner_field.related_model = "accounts.OwnerModel"

    field_descriptors_module = (
        "general_manager.interface.capabilities.orm_utils.field_descriptors"
    )
    with (
        patch("django.apps.apps.get_model", return_value=object()),
        patch(f"{field_descriptors_module}._iter_model_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_many_to_many_fields", return_value=[]),
        patch(f"{field_descriptors_module}._iter_reverse_relations", return_value=[]),
        patch(
            f"{field_descriptors_module}._iter_foreign_key_fields",
            return_value=[owner_field],
        ),
    ):
        descriptors = build_field_descriptors(interface_cls)

    assert descriptors == {}


def test_initialize_general_manager_classes_clears_stale_field_descriptors() -> None:
    stale_descriptors = {"owner": object()}
    observed_cache_values: list[object | None] = []

    class Interface:
        _field_descriptors = stale_descriptors

        @classmethod
        def get_attributes(cls) -> dict[str, object]:
            observed_cache_values.append(cls._field_descriptors)
            return {}

    manager_cls = type("ManagerUnderTest", (), {"Interface": Interface})

    initialize_general_manager_classes([manager_cls], [manager_cls])

    assert observed_cache_values == [None]


def test_build_field_descriptors_preserves_file_field_metadata() -> None:
    class FileMetadataModel(models.Model):
        required_file = models.FileField(upload_to="uploads")
        optional_file = models.FileField(upload_to="uploads", blank=True)
        default_file = models.FileField(
            upload_to="uploads",
            default="uploads/default.txt",
        )
        nullable_required_file = models.FileField(
            upload_to="uploads",
            null=True,
            blank=False,
        )
        image = models.ImageField(upload_to="images", blank=True)

        class Meta:
            app_label = "field_descriptor_tests"

    interface_cls = type("InterfaceUnderTest", (), {"_model": FileMetadataModel})

    descriptors = build_field_descriptors(interface_cls)

    required_file = descriptors["required_file"].metadata
    assert required_file["type"] is str
    assert required_file["orm_field_kind"] == "file"
    assert required_file["is_required"] is True
    assert required_file["file_clearable"] is False

    optional_file = descriptors["optional_file"].metadata
    assert optional_file["type"] is str
    assert optional_file["orm_field_kind"] == "file"
    assert optional_file["is_required"] is False
    assert optional_file["file_clearable"] is True

    default_file = descriptors["default_file"].metadata
    assert default_file["is_required"] is False
    assert default_file["file_clearable"] is False

    nullable_required_file = descriptors["nullable_required_file"].metadata
    assert nullable_required_file["is_required"] is True
    assert nullable_required_file["file_clearable"] is False

    image = descriptors["image"].metadata
    assert image["type"] is str
    assert image["orm_field_kind"] == "image"
    assert image["file_clearable"] is True


def test_build_field_descriptors_does_not_add_file_metadata_to_string_fields() -> None:
    class StringMetadataModel(models.Model):
        char = models.CharField(max_length=100)
        url = models.URLField()
        path = models.FilePathField(path="/var")

        class Meta:
            app_label = "field_descriptor_tests"

    interface_cls = type("InterfaceUnderTest", (), {"_model": StringMetadataModel})

    descriptors = build_field_descriptors(interface_cls)

    for name in ("char", "url", "path"):
        metadata = descriptors[name].metadata
        assert metadata["type"] is str
        assert "orm_field_kind" not in metadata
        assert "file_clearable" not in metadata
