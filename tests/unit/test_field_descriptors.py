from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from django.apps import apps
from django.db import models

from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    _FieldDescriptorBuilder,
    _general_manager_many_accessor,
    build_field_descriptors,
)
from general_manager.bootstrap import initialize_general_manager_classes


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
