from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from django.apps import apps
from django.db import models

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.cache.signals import data_change
from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    _FieldDescriptorBuilder,
    _general_manager_accessor,
    _general_manager_many_accessor,
    build_field_descriptors,
)
from general_manager.bootstrap import initialize_general_manager_classes
from general_manager.manager.general_manager import GeneralManager


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
