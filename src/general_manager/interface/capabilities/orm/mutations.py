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
        """
        Apply simple (non-relational) attribute updates to a Django model instance.

        Parameters:
            instance (models.Model): The model instance to modify.
            kwargs (dict[str, Any]): Mapping of field names to values; entries with the sentinel `NOT_PROVIDED` are ignored.

        Returns:
            models.Model: The same instance after attribute assignment.

        Raises:
            InvalidFieldValueError: If assigning a value raises a `ValueError` for a field.
            InvalidFieldTypeError: If assigning a value raises a `TypeError` for a field.
        """
        payload_snapshot = {"keys": sorted(kwargs.keys())}

        def _perform() -> models.Model:
            """
            Apply the provided simple attribute values to the captured model instance, ignoring keys marked NOT_PROVIDED.

            The function sets each key on the enclosed `instance` to its corresponding value and returns the mutated model instance. Keys whose value is `NOT_PROVIDED` are skipped.

            Returns:
                models.Model: The same model instance after attribute assignment.

            Raises:
                InvalidFieldValueError: If assigning a value raises ValueError for a field.
                InvalidFieldTypeError: If assigning a value raises TypeError for a field.
            """
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
        """
        Persist the model instance while recording creator metadata and an optional change reason.

        Performs the save inside an atomic transaction using the interface's configured database alias when available, sets `changed_by_id` on the instance if the attribute exists, runs model validation, and attaches a change reason after a successful save when `history_comment` is provided.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The interface class used to resolve support capabilities and configuration.
            instance (models.Model): The Django model instance to validate and save.
            creator_id (int | None): Identifier of the user or process responsible for the change; assigned to `instance.changed_by_id` when supported.
            history_comment (str | None): Optional comment describing the change; attached as a change reason after save when provided.

        Returns:
            int: The primary key (`pk`) of the saved instance.
        """
        payload_snapshot = {
            "pk": getattr(instance, "pk", None),
            "creator_id": creator_id,
            "history_comment": history_comment,
        }

        def _perform() -> int:
            """
            Save the model instance within an atomic database transaction, validating it and assigning the creator when available.

            Performs model validation, assigns `changed_by_id` if the attribute exists, saves to the interface's configured database alias when present, and returns the instance primary key.

            Returns:
                The primary key (pk) of the saved instance.
            """
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
        """
        Apply many-to-many updates to a model instance's related fields.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class owning the model (used for observability).
            instance (models.Model): The model instance whose relationships will be updated.
            many_to_many_kwargs (dict[str, list[int]]): Mapping of relation keys to lists of related object IDs.
                Each key is expected to end with the suffix `_id_list`; the suffix is removed to derive the relation manager name.
            history_comment (str | None): Optional change reason to attach to the instance's history after updates.

        Returns:
            models.Model: The same model instance after its many-to-many relations have been updated.
        """
        payload_snapshot = {
            "pk": getattr(instance, "pk", None),
            "relations": sorted(many_to_many_kwargs.keys()),
            "history_comment": history_comment,
        }

        def _perform() -> models.Model:
            """
            Apply the provided many-to-many id lists to the instance's related managers.

            This sets each many-to-many relation on `instance` using entries from `many_to_many_kwargs`,
            where each key expectedly ends with the suffix `_id_list` and maps to the corresponding
            relation name after removing that suffix.

            Returns:
                models.Model: The same `instance` after its many-to-many relations have been updated.
            """
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
        """
        Create a new ORM model instance from the provided payload and persist it with optional creator and history metadata.

        Parameters:
                interface_cls (type[OrmInterfaceBase]): Interface class that defines the target model and capabilities.
                *args: Ignored positional arguments (kept for signature compatibility).
                **kwargs: Field values used to construct the instance. Recognized special keys:
                        creator_id (int, optional): Identifier to record as the creator/changed_by of the saved instance.
                        history_comment (str, optional): Change reason to attach to the created instance's history.

        Returns:
                result (dict[str, Any]): A dictionary containing the new instance primary key as {"id": <pk>}.
        """
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> dict[str, Any]:
            """
            Create a new model instance from the given kwargs, persist it with optional creator and history metadata, and apply many-to-many relations.

            Pops `creator_id` and `history_comment` from the local payload, normalizes the remaining payload, assigns simple attributes to a new model instance, saves the instance (recording creator/history when provided), and updates many-to-many relationships.

            Returns:
                dict[str, Any]: A mapping with key `"id"` set to the primary key of the created instance.
            """
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
        """
        Update the model instance referenced by the given interface instance with the provided payload, persisting changes and recording history and many-to-many updates.

        Parameters:
            interface_instance (OrmInterfaceBase): Interface wrapper whose `pk` identifies the target model instance to update.
            *args: Ignored.
            **kwargs: Field values to apply; may include `creator_id` (int) and `history_comment` (str) to record who made the change and why. Payload will be normalized and split into simple fields and many-to-many relations before applying.

        Returns:
            dict[str, Any]: A mapping containing the saved instance id as {"id": <pk>}.
        """
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> dict[str, Any]:
            """
            Update an existing model instance from a normalized payload, persist the changes with history, and return the instance id.

            Performs simple-field updates and many-to-many relation updates, saves the instance while recording creator and history comment when provided, and returns a dict with the resulting primary key.

            Returns:
                dict[str, int]: A mapping {"id": pk} where `pk` is the primary key of the updated instance.
            """
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
        """
        Delete or deactivate the provided ORM interface instance according to the interface's deletion policy.

        If soft-delete is enabled for the interface, the instance is marked inactive (requires the instance to implement SupportsActivation) and saved with an optional history comment indicating deactivation. If soft-delete is not enabled, the instance is hard-deleted inside a database transaction using the support-provided database alias when available; the function attempts to set `changed_by_id` from `creator_id` and attaches the provided history comment as the change reason before deletion.

        Parameters:
            interface_instance: The interface-wrapped model instance to remove.

        Returns:
            result (dict[str, Any]): A dict containing the primary key under the key `"id"`.

        Raises:
            MissingActivationSupportError: If soft-delete is enabled but the instance does not implement activation support.
        """
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> dict[str, Any]:
            """
            Delete or deactivate the target ORM instance referenced by the surrounding interface_instance, recording creator and history metadata as appropriate.

            Performs a soft deactivation when soft-delete is enabled for the model (requiring activation support) or a hard delete otherwise. When soft-deactivating, saves the instance with a deactivation history comment; when hard-deleting, sets the change reason and performs the deletion inside a database transaction.

            Returns:
                result (dict[str, Any]): A dictionary of the form `{"id": pk}` where `pk` is the primary key of the affected instance.

            Raises:
                MissingActivationSupportError: If soft-delete is enabled but the instance does not implement activation support.
            """
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
        """
        Normalize and split a mutation payload into simple field values and many-to-many relation values.

        Parameters:
            interface_cls (type): The interface class whose schema/normalizer should be used.
            payload (dict[str, Any]): Raw input payload to validate and normalize.

        Returns:
            tuple:
                - dict[str, Any]: Normalized simple field values suitable for direct assignment.
                - dict[str, list[Any]]: Normalized many-to-many relation values keyed by relation name.
        """
        payload_snapshot = {"keys": sorted(payload.keys())}

        def _perform() -> tuple[dict[str, Any], dict[str, list[Any]]]:
            """
            Normalize and validate the provided payload into simple field values and many-to-many lists.

            Performs key validation, splits the payload into simple and many-to-many parts, and returns the normalized results.

            Returns:
                tuple:
                    - normalized_simple (dict[str, Any]): Mapping of simple field names to their normalized values.
                    - normalized_many (dict[str, list[Any]]): Mapping of many-to-many relation names to lists of normalized values.
            """
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
    """
    Normalize a raw mutation payload into simple attributes and many-to-many mappings for the given ORM interface.

    If the interface provides a validation capability with `normalize_payload`, that handler is used; otherwise the support-provided payload normalizer is used to validate keys, split the payload, and normalize values.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The interface class whose normalization rules should be applied.
        payload (dict[str, Any]): The raw input payload to validate and normalize.

    Returns:
        normalized_simple (dict[str, Any]): Simple attribute names mapped to normalized values.
        normalized_many (dict[str, list[Any]]): Many-to-many field names mapped to lists of normalized related IDs or values.
    """
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
    """
    Retrieve the ORM mutation capability associated with the given interface class.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The interface class to query for the orm_mutation capability.

    Returns:
        OrmMutationCapability: The required mutation capability instance for the interface class.
    """
    return interface_cls.require_capability(  # type: ignore[return-value]
        "orm_mutation",
        expected_type=OrmMutationCapability,
    )
