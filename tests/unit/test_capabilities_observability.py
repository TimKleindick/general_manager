from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from general_manager.interface.capabilities.calculation import (
    CalculationQueryCapability,
)
from general_manager.interface.capabilities.existing_model import (
    ExistingModelResolutionCapability,
)
from general_manager.interface.capabilities.orm import (
    OrmCreateCapability,
    OrmDeleteCapability,
    OrmMutationCapability,
    OrmQueryCapability,
    OrmReadCapability,
    OrmUpdateCapability,
    OrmValidationCapability,
)
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)


def _assert_observability(
    *,
    patch_target: str,
    target_callable,
    expected_target,
    expected_operation: str,
    expected_payload: dict[str, object],
) -> None:
    sentinel = object()
    with mock.patch(patch_target, return_value=sentinel) as wrapper:
        result = target_callable()
        assert result is sentinel
        wrapper.assert_called_once()
        args, kwargs = wrapper.call_args
        assert args[0] is expected_target
        assert kwargs["operation"] == expected_operation
        assert kwargs["payload"] == expected_payload
        assert callable(kwargs["func"])


def test_orm_read_capability_uses_observability():
    interface_instance = SimpleNamespace(pk=7, _search_date=None)
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmReadCapability().get_data(interface_instance),
        expected_target=interface_instance,
        expected_operation="read",
        expected_payload={"pk": 7},
    )


def test_orm_create_capability_uses_observability():
    interface_cls = type("WritableInterface", (), {})
    payload = {"name": "alpha"}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmCreateCapability().create(interface_cls, **payload),
        expected_target=interface_cls,
        expected_operation="create",
        expected_payload={"kwargs": payload},
    )


def test_orm_update_capability_uses_observability():
    instance = SimpleNamespace(pk=11)
    payload = {"name": "bravo"}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmUpdateCapability().update(instance, **payload),
        expected_target=instance,
        expected_operation="update",
        expected_payload={"kwargs": payload, "pk": 11},
    )


def test_orm_delete_capability_uses_observability():
    instance = SimpleNamespace(pk=21)
    payload = {"reason": "cleanup"}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmDeleteCapability().delete(instance, **payload),
        expected_target=instance,
        expected_operation="delete",
        expected_payload={"kwargs": payload, "pk": 21},
    )


def test_orm_validation_capability_uses_observability():
    interface_cls = type("ValidationInterface", (), {})
    payload = {"foo": "bar", "baz": "qux"}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmValidationCapability().normalize_payload(
            interface_cls, payload=payload
        ),
        expected_target=interface_cls,
        expected_operation="validation.normalize",
        expected_payload={"keys": sorted(payload.keys())},
    )


def test_orm_mutation_capability_uses_observability():
    interface_cls = type(
        "MutationInterface",
        (),
        {
            "_update_change_reason": classmethod(lambda *_: None),
            "_get_database_alias": classmethod(lambda *_: None),
        },
    )
    instance = SimpleNamespace(pk=42)
    payload = {"foo": 1}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmMutationCapability().assign_simple_attributes(
            interface_cls, instance, payload
        ),
        expected_target=interface_cls,
        expected_operation="mutation.assign_simple",
        expected_payload={"keys": sorted(payload.keys())},
    )

    relations = {"roles": [1, 2], "tags": [3]}
    with mock.patch(
        "general_manager.interface.capabilities.orm.update_change_reason",
        return_value=None,
    ):
        _assert_observability(
            patch_target="general_manager.interface.capabilities.orm.with_observability",
            target_callable=lambda: OrmMutationCapability().save_with_history(
                interface_cls, instance, creator_id=5, history_comment="note"
            ),
            expected_target=interface_cls,
            expected_operation="mutation.save_with_history",
            expected_payload={"pk": 42, "creator_id": 5, "history_comment": "note"},
        )

        _assert_observability(
            patch_target="general_manager.interface.capabilities.orm.with_observability",
            target_callable=lambda: OrmMutationCapability().apply_many_to_many(
                interface_cls,
                instance,
                many_to_many_kwargs=relations,
                history_comment="sync",
            ),
            expected_target=interface_cls,
            expected_operation="mutation.apply_many_to_many",
            expected_payload={
                "pk": 42,
                "relations": sorted(relations.keys()),
                "history_comment": "sync",
            },
        )


def test_orm_query_capability_uses_observability():
    interface_cls = type("OrmInterface", (), {})
    payload = {"status": "active"}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmQueryCapability().filter(interface_cls, **payload),
        expected_target=interface_cls,
        expected_operation="query.filter",
        expected_payload={"kwargs": payload},
    )
    _assert_observability(
        patch_target="general_manager.interface.capabilities.orm.with_observability",
        target_callable=lambda: OrmQueryCapability().exclude(interface_cls, **payload),
        expected_target=interface_cls,
        expected_operation="query.exclude",
        expected_payload={"kwargs": payload},
    )


def test_calculation_query_capability_uses_observability():
    interface_cls = type("CalculationInterface", (), {})
    payload = {"foo": 1}
    _assert_observability(
        patch_target="general_manager.interface.capabilities.calculation.with_observability",
        target_callable=lambda: CalculationQueryCapability().filter(
            interface_cls, **payload
        ),
        expected_target=interface_cls,
        expected_operation="calculation.query.filter",
        expected_payload={"kwargs": payload},
    )
    _assert_observability(
        patch_target="general_manager.interface.capabilities.calculation.with_observability",
        target_callable=lambda: CalculationQueryCapability().exclude(
            interface_cls, **payload
        ),
        expected_target=interface_cls,
        expected_operation="calculation.query.exclude",
        expected_payload={"kwargs": payload},
    )
    _assert_observability(
        patch_target="general_manager.interface.capabilities.calculation.with_observability",
        target_callable=lambda: CalculationQueryCapability().all(interface_cls),
        expected_target=interface_cls,
        expected_operation="calculation.query.all",
        expected_payload={},
    )


def test_read_only_capability_uses_observability():
    parent_cls = type("Parent", (), {"__name__": "Parent"})
    model_cls = type("Model", (), {"__name__": "Model"})
    interface_cls = type(
        "ReadOnlyInterface",
        (),
        {"_parent_class": parent_cls, "_model": model_cls},
    )

    _assert_observability(
        patch_target="general_manager.interface.capabilities.read_only.with_observability",
        target_callable=lambda: ReadOnlyManagementCapability().ensure_schema_is_up_to_date(
            interface_cls,
            parent_cls,
            model_cls,
        ),
        expected_target=interface_cls,
        expected_operation="read_only.ensure_schema",
        expected_payload={"manager": "Parent", "model": "Model"},
    )

    _assert_observability(
        patch_target="general_manager.interface.capabilities.read_only.with_observability",
        target_callable=lambda: ReadOnlyManagementCapability().sync_data(
            interface_cls,
            connection=None,
            transaction=None,
            integrity_error=None,
            json_module=None,
        ),
        expected_target=interface_cls,
        expected_operation="read_only.sync_data",
        expected_payload={
            "manager": "Parent",
            "model": "Model",
            "schema_validated": False,
        },
    )


def test_existing_model_capability_uses_observability():
    interface_cls = type("ExistingInterface", (), {})
    model_cls = type("Model", (), {"__name__": "Model", "_meta": SimpleNamespace()})

    _assert_observability(
        patch_target="general_manager.interface.capabilities.existing_model.with_observability",
        target_callable=lambda: ExistingModelResolutionCapability().resolve_model(
            interface_cls
        ),
        expected_target=interface_cls,
        expected_operation="existing_model.resolve",
        expected_payload={"interface": "ExistingInterface"},
    )

    _assert_observability(
        patch_target="general_manager.interface.capabilities.existing_model.with_observability",
        target_callable=lambda: ExistingModelResolutionCapability().ensure_history(
            model_cls, interface_cls
        ),
        expected_target=interface_cls,
        expected_operation="existing_model.ensure_history",
        expected_payload={"interface": "ExistingInterface", "model": "Model"},
    )

    _assert_observability(
        patch_target="general_manager.interface.capabilities.existing_model.with_observability",
        target_callable=lambda: ExistingModelResolutionCapability().apply_rules(
            interface_cls, model_cls
        ),
        expected_target=interface_cls,
        expected_operation="existing_model.apply_rules",
        expected_payload={"interface": "ExistingInterface", "model": "Model"},
    )
