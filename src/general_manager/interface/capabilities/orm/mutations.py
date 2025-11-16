"""Mutation-centric ORM capabilities."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, ClassVar, cast

from django.db import models, transaction
from django.db.models import NOT_PROVIDED

from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.utils.database_interface_protocols import (
    SupportsActivation,
)
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
    MissingActivationSupportError,
)

from ._compat import call_update_change_reason, call_with_observability
from .support import get_support_capability, is_soft_delete_enabled

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase


class OrmMutationCapability(BaseCapability):
    """Common utilities to modify ORM instances."""

    name: ClassVar[CapabilityName] = "orm_mutation"

    def assign_simple_attributes(
        self,
        interface_cls: type["OrmInterfaceBase"],
        instance: models.Model,
        kwargs: dict[str, Any],
    ) -> models.Model:
        payload_snapshot = {"keys": sorted(kwargs.keys())}

        def _perform() -> models.Model:
            for key, value in kwargs.items():
                if value is NOT_PROVIDED:
                    continue
                try:
                    setattr(instance, key, value)
                except ValueError as error:
                    raise InvalidFieldValueError(key, value) from error
                except TypeError as error:
                    raise InvalidFieldTypeError(key, error) from error
            return instance

        return call_with_observability(
            interface_cls,
            operation="mutation.assign_simple",
            payload=payload_snapshot,
            func=_perform,
        )

    def save_with_history(
        self,
        interface_cls: type["OrmInterfaceBase"],
        instance: models.Model,
        *,
        creator_id: int | None,
        history_comment: str | None,
    ) -> int:
        payload_snapshot = {
            "pk": getattr(instance, "pk", None),
            "creator_id": creator_id,
            "history_comment": history_comment,
        }

        def _perform() -> int:
            support = get_support_capability(interface_cls)
            database_alias = support.get_database_alias(interface_cls)
            if database_alias:
                instance._state.db = database_alias  # type: ignore[attr-defined]
            atomic_context = (
                transaction.atomic(using=database_alias)
                if database_alias
                else transaction.atomic()
            )
            with atomic_context:
                try:
                    instance.changed_by_id = creator_id  # type: ignore[attr-defined]
                except AttributeError:
                    pass
                instance.full_clean()
                if database_alias:
                    instance.save(using=database_alias)
                else:
                    instance.save()
            return instance.pk

        result = call_with_observability(
            interface_cls,
            operation="mutation.save_with_history",
            payload=payload_snapshot,
            func=_perform,
        )
        if history_comment:
            call_update_change_reason(instance, history_comment)
        return result

    def apply_many_to_many(
        self,
        interface_cls: type["OrmInterfaceBase"],
        instance: models.Model,
        *,
        many_to_many_kwargs: dict[str, list[int]],
        history_comment: str | None,
    ) -> models.Model:
        payload_snapshot = {
            "pk": getattr(instance, "pk", None),
            "relations": sorted(many_to_many_kwargs.keys()),
            "history_comment": history_comment,
        }

        def _perform() -> models.Model:
            for key, value in many_to_many_kwargs.items():
                field_name = key.removesuffix("_id_list")
                getattr(instance, field_name).set(value)
            return instance

        result = call_with_observability(
            interface_cls,
            operation="mutation.apply_many_to_many",
            payload=payload_snapshot,
            func=_perform,
        )
        if history_comment:
            call_update_change_reason(instance, history_comment)
        return result


class OrmCreateCapability(BaseCapability):
    """Create new ORM instances using capability-driven configuration."""

    name: ClassVar[CapabilityName] = "create"
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def create(
        self,
        interface_cls: type["OrmInterfaceBase"],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> dict[str, Any]:
            local_kwargs = dict(kwargs)
            creator_id = local_kwargs.pop("creator_id", None)
            history_comment = local_kwargs.pop("history_comment", None)
            normalized_simple, normalized_many = _normalize_payload(
                interface_cls, local_kwargs
            )
            mutation = _mutation_capability_for(interface_cls)
            instance = mutation.assign_simple_attributes(
                interface_cls, interface_cls._model(), normalized_simple
            )
            pk = mutation.save_with_history(
                interface_cls,
                instance,
                creator_id=creator_id,
                history_comment=history_comment,
            )
            mutation.apply_many_to_many(
                interface_cls,
                instance,
                many_to_many_kwargs=normalized_many,
                history_comment=history_comment,
            )
            return {"id": pk}

        return call_with_observability(
            interface_cls,
            operation="create",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmUpdateCapability(BaseCapability):
    """Update existing ORM instances."""

    name: ClassVar[CapabilityName] = "update"
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def update(
        self,
        interface_instance: "OrmInterfaceBase",
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> dict[str, Any]:
            local_kwargs = dict(kwargs)
            creator_id = local_kwargs.pop("creator_id", None)
            history_comment = local_kwargs.pop("history_comment", None)
            normalized_simple, normalized_many = _normalize_payload(
                interface_instance.__class__, local_kwargs
            )
            support = get_support_capability(interface_instance.__class__)
            manager = support.get_manager(
                interface_instance.__class__,
                only_active=False,
            )
            instance = manager.get(pk=interface_instance.pk)
            mutation = _mutation_capability_for(interface_instance.__class__)
            instance = mutation.assign_simple_attributes(
                interface_instance.__class__, instance, normalized_simple
            )
            pk = mutation.save_with_history(
                interface_instance.__class__,
                instance,
                creator_id=creator_id,
                history_comment=history_comment,
            )
            mutation.apply_many_to_many(
                interface_instance.__class__,
                instance,
                many_to_many_kwargs=normalized_many,
                history_comment=history_comment,
            )
            return {"id": pk}

        return call_with_observability(
            interface_instance,
            operation="update",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmDeleteCapability(BaseCapability):
    """Delete (or deactivate) ORM instances."""

    name: ClassVar[CapabilityName] = "delete"
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def delete(
        self,
        interface_instance: "OrmInterfaceBase",
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> dict[str, Any]:
            local_kwargs = dict(kwargs)
            creator_id = local_kwargs.pop("creator_id", None)
            history_comment = local_kwargs.pop("history_comment", None)
            support = get_support_capability(interface_instance.__class__)
            manager = support.get_manager(
                interface_instance.__class__,
                only_active=False,
            )
            instance = manager.get(pk=interface_instance.pk)
            mutation = _mutation_capability_for(interface_instance.__class__)
            if is_soft_delete_enabled(interface_instance.__class__):
                if not isinstance(instance, SupportsActivation):
                    raise MissingActivationSupportError(instance.__class__.__name__)
                instance.is_active = False
                history_comment_local = (
                    f"{history_comment} (deactivated)"
                    if history_comment
                    else "Deactivated"
                )
                model_instance = cast(models.Model, instance)
                pk = mutation.save_with_history(
                    interface_instance.__class__,
                    model_instance,
                    creator_id=creator_id,
                    history_comment=history_comment_local,
                )
                return {"id": pk}

            history_comment_local = (
                f"{history_comment} (deleted)" if history_comment else "Deleted"
            )
            try:
                instance.changed_by_id = creator_id  # type: ignore[attr-defined]
            except AttributeError:
                pass
            call_update_change_reason(instance, history_comment_local)
            database_alias = support.get_database_alias(interface_instance.__class__)
            atomic_context = (
                transaction.atomic(using=database_alias)
                if database_alias
                else transaction.atomic()
            )
            with atomic_context:
                if database_alias:
                    instance.delete(using=database_alias)
                else:
                    instance.delete()
            return {"id": interface_instance.pk}

        return call_with_observability(
            interface_instance,
            operation="delete",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmValidationCapability(BaseCapability):
    """Validate and normalize payloads used by mutation capabilities."""

    name: ClassVar[CapabilityName] = "validation"

    def normalize_payload(
        self,
        interface_cls: type["OrmInterfaceBase"],
        *,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        payload_snapshot = {"keys": sorted(payload.keys())}

        def _perform() -> tuple[dict[str, Any], dict[str, list[Any]]]:
            support = get_support_capability(interface_cls)
            normalizer = support.get_payload_normalizer(interface_cls)
            payload_copy = dict(payload)
            normalizer.validate_keys(payload_copy)
            simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(
                payload_copy
            )
            normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
            normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
            return normalized_simple, normalized_many

        return call_with_observability(
            interface_cls,
            operation="validation.normalize",
            payload=payload_snapshot,
            func=_perform,
        )


def _normalize_payload(
    interface_cls: type["OrmInterfaceBase"],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    handler = interface_cls.get_capability_handler("validation")
    if handler is not None and hasattr(handler, "normalize_payload"):
        return handler.normalize_payload(interface_cls, payload=dict(payload))
    support = get_support_capability(interface_cls)
    normalizer = support.get_payload_normalizer(interface_cls)
    payload_copy = dict(payload)
    normalizer.validate_keys(payload_copy)
    simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(payload_copy)
    normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
    normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
    return normalized_simple, normalized_many


def _mutation_capability_for(
    interface_cls: type["OrmInterfaceBase"],
) -> OrmMutationCapability:
    return interface_cls.require_capability(  # type: ignore[return-value]
        "orm_mutation",
        expected_type=OrmMutationCapability,
    )
