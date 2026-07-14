"""Mutation-centric ORM capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.db.models import NOT_PROVIDED

from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
    PayloadNormalizer,
)
from general_manager.interface.utils.database_interface_protocols import (
    SupportsActivation,
)
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
    MissingActivationSupportError,
)
from general_manager.interface.utils.models import model_has_field
from general_manager.uploads.finalization import (
    has_upload_candidates,
    lock_upload_claims,
    mark_uploads_finalizing,
    prepare_upload_claims,
    reserve_upload_names,
    run_upload_transaction,
)
from general_manager.uploads.types import UploadCandidate, UploadOperation

from ._compat import call_update_change_reason, call_with_observability
from .support import (
    discard_orm_instance_cache,
    get_support_capability,
    is_soft_delete_enabled,
)

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase

type OrmInterfaceClass = type["OrmInterfaceBase[models.Model]"]
type OrmInterfaceInstance = "OrmInterfaceBase[models.Model]"
type MutationPayload = dict[str, object]
type ManyToManyPayload = dict[str, list[object]]
type MutationResult = dict[str, object]


class OrmMutationCapability(BaseCapability):
    """Common utilities to modify ORM instances."""

    name: ClassVar[CapabilityName] = "orm_mutation"

    def assign_simple_attributes(
        self,
        interface_cls: OrmInterfaceClass,
        instance: models.Model,
        kwargs: MutationPayload,
    ) -> models.Model:
        """
        Apply simple (non-relational) attribute updates to a Django model instance.

        Parameters:
            instance (models.Model): The model instance to modify.
            kwargs: Mapping of field names to values; entries with the sentinel
                `NOT_PROVIDED` are ignored.

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
        interface_cls: OrmInterfaceClass,
        instance: models.Model,
        *,
        creator_id: int | None,
        history_comment: str | None,
        _savepoint: bool = True,
    ) -> object:
        """
        Persist the model instance while recording creator metadata and an optional change reason.

        Performs the save inside an atomic transaction using the interface's configured database alias when available, sets `changed_by_id` on the instance if the attribute exists, runs model validation, and attaches a change reason after a successful save when `history_comment` is provided.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The interface class used to resolve support capabilities and configuration.
            instance (models.Model): The Django model instance to validate and save.
            creator_id (int | None): Identifier of the user or process responsible for the change; assigned to `instance.changed_by_id` when supported.
            history_comment (str | None): Optional comment describing the change; attached as a change reason after save when provided.
            _savepoint (bool): Internal control for avoiding a redundant nested
                savepoint when an encompassing mutation transaction already
                provides the rollback boundary.

        Returns:
            object: The primary key (`pk`) of the saved instance. Django primary
                keys may be non-integer values.

        Raises:
            InvalidFieldValueError: From prior assignment helpers.
            Exception: Database-alias lookup, history actor lookup,
                validation, transaction, save, change-reason, and observability
                errors are not wrapped.
        """
        payload_snapshot = {
            "pk": getattr(instance, "pk", None),
            "creator_id": creator_id,
            "history_comment": history_comment,
        }

        def _perform() -> object:
            """
            Save the model instance within an atomic database transaction, validating it and assigning the creator when available.

            Performs model validation, assigns `changed_by_id` if the attribute exists, saves to the interface's configured database alias when present, and returns the instance primary key.

            Returns:
                The primary key (pk) of the saved instance.
            """
            support = get_support_capability(interface_cls)
            database_alias = support.get_database_alias(interface_cls)
            if database_alias:
                instance._state.db = database_alias
            if database_alias:
                atomic_context = (
                    transaction.atomic(using=database_alias)
                    if _savepoint
                    else transaction.atomic(using=database_alias, savepoint=False)
                )
            else:
                atomic_context = (
                    transaction.atomic()
                    if _savepoint
                    else transaction.atomic(savepoint=False)
                )
            with atomic_context:
                _assign_history_actor(
                    instance,
                    creator_id=creator_id,
                    database_alias=database_alias,
                )
                if model_has_field(instance, "changed_by"):
                    object.__setattr__(instance, "changed_by_id", creator_id)
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
        interface_cls: OrmInterfaceClass,
        instance: models.Model,
        *,
        many_to_many_kwargs: ManyToManyPayload,
        history_comment: str | None,
    ) -> models.Model:
        """
        Apply many-to-many updates to a model instance's related fields.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class owning the model (used for observability).
            instance (models.Model): The model instance whose relationships will be updated.
            many_to_many_kwargs: Mapping of normalized many-to-many payload keys
                to related values. Keys are still expected to use
                `<relation>_id_list`; the suffix is removed to derive the
                relation manager name before calling `.set(values)`.
            history_comment (str | None): Optional change reason to attach to the instance's history after updates.

        Returns:
            models.Model: The same model instance after its many-to-many relations have been updated.

        Raises:
            AttributeError: If the derived relation manager does not exist.
            Exception: Relation `.set()`, change-reason, and observability
                errors are not wrapped.
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
        interface_cls: OrmInterfaceClass,
        *args: object,
        **kwargs: object,
    ) -> MutationResult:
        """
        Create a new ORM model instance from the provided payload and persist it with optional creator and history metadata.

        Args:
            interface_cls: Interface class that defines the target model and
                capabilities.
            *args: Ignored positional arguments kept for manager/capability
                signature compatibility. Passing positional values has no
                effect.
            **kwargs: Field values used to construct the instance. Reserved
                metadata keys are `creator_id` and `history_comment`; all other
                keys are validated as model fields/attributes or many-to-many
                aliases such as `<relation>_id_list`.

        Returns:
            Capability-level result dictionary containing the new instance
            primary key as `{"id": pk}`. The GeneralManager layer consumes this
            result and returns the public manager instance from
            `Manager.create(...)`.

        Raises:
            UnknownFieldError: If the normalized payload contains unknown keys.
            InvalidFieldValueError: If field assignment raises `ValueError`.
            InvalidFieldTypeError: If field assignment raises `TypeError`.
            Exception: Validation, transaction, save, many-to-many,
                change-reason, history actor, and observability errors are not
                wrapped.
        """
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> MutationResult:
            """
            Create a new model instance from the given kwargs, persist it with optional creator and history metadata, and apply many-to-many relations.

            Pops `creator_id` and `history_comment` from the local payload, normalizes the remaining payload, assigns simple attributes to a new model instance, saves the instance (recording creator/history when provided), and updates many-to-many relationships.

            Returns:
                MutationResult: A mapping with key `"id"` set to the primary
                    key of the created instance.
            """
            local_kwargs = dict(kwargs)
            creator_id = cast(int | None, local_kwargs.pop("creator_id", None))
            history_comment = cast(
                str | None, local_kwargs.pop("history_comment", None)
            )
            normalized_simple, normalized_many = _normalize_payload(
                interface_cls, local_kwargs
            )
            mutation = _mutation_capability_for(interface_cls)
            if has_upload_candidates(normalized_simple):
                prepared = prepare_upload_claims(
                    interface_cls,
                    normalized_simple,
                    operation=UploadOperation.CREATE,
                    actor_id=creator_id,
                    target_pk=None,
                )

                def _create_claimed() -> MutationResult:
                    locked = lock_upload_claims(prepared)
                    instance = interface_cls._model()
                    ordinary_values = {
                        key: value
                        for key, value in normalized_simple.items()
                        if not isinstance(value, UploadCandidate)
                    }
                    instance = mutation.assign_simple_attributes(
                        interface_cls,
                        instance,
                        ordinary_values,
                    )
                    reserved_values = reserve_upload_names(
                        locked,
                        instance,
                        normalized_simple,
                    )
                    claimed_instance = mutation.assign_simple_attributes(
                        interface_cls,
                        instance,
                        reserved_values,
                    )
                    claimed_pk = mutation.save_with_history(
                        interface_cls,
                        claimed_instance,
                        creator_id=creator_id,
                        history_comment=history_comment,
                    )
                    mutation.apply_many_to_many(
                        interface_cls,
                        claimed_instance,
                        many_to_many_kwargs=normalized_many,
                        history_comment=history_comment,
                    )
                    mark_uploads_finalizing(locked, target_pk=claimed_pk)
                    return {"id": claimed_pk}

                return run_upload_transaction(prepared, _create_claimed)
            instance = mutation.assign_simple_attributes(
                interface_cls, interface_cls._model(), normalized_simple
            )
            support = get_support_capability(interface_cls)
            database_alias = support.get_database_alias(interface_cls)
            atomic_context = (
                transaction.atomic(using=database_alias)
                if database_alias
                else transaction.atomic()
            )
            with atomic_context:
                pk = mutation.save_with_history(
                    interface_cls,
                    instance,
                    creator_id=creator_id,
                    history_comment=history_comment,
                    _savepoint=False,
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
        interface_instance: OrmInterfaceInstance,
        *args: object,
        **kwargs: object,
    ) -> MutationResult:
        """
        Update the model instance referenced by the given interface instance with the provided payload, persisting changes and recording history and many-to-many updates.

        Parameters:
            interface_instance (OrmInterfaceBase): Interface wrapper whose `pk` identifies the target model instance to update.
            *args: Ignored positional arguments kept for manager/capability
                signature compatibility. Passing positional values has no
                effect.
            **kwargs: Field values to apply; reserved metadata keys are
                `creator_id` and `history_comment`. Other keys are validated as
                model fields/attributes or many-to-many aliases such as
                `<relation>_id_list`.

        Returns:
            MutationResult: Capability-level mapping containing the saved
                primary key as `{"id": pk}`. The GeneralManager layer consumes
                this result, refreshes the public manager state, and returns the
                same manager instance from `manager.update(...)`.

        Raises:
            UnknownFieldError: If the normalized payload contains unknown keys.
            InvalidFieldValueError: If field assignment raises `ValueError`.
            InvalidFieldTypeError: If field assignment raises `TypeError`.
            Exception: Row lookup, validation, transaction, save, many-to-many,
                cache invalidation, change-reason, history actor, and
                observability errors are not wrapped.
        """
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> MutationResult:
            """
            Update an existing model instance from a normalized payload, persist the changes with history, and return the instance id.

            Performs simple-field updates and many-to-many relation updates, saves the instance while recording creator and history comment when provided, and returns a dict with the resulting primary key.

            Returns:
                MutationResult: A mapping `{"id": pk}` where `pk` is the primary
                    key of the updated instance.
            """
            local_kwargs = dict(kwargs)
            creator_id = cast(int | None, local_kwargs.pop("creator_id", None))
            history_comment = cast(
                str | None, local_kwargs.pop("history_comment", None)
            )
            normalized_simple, normalized_many = _normalize_payload(
                interface_instance.__class__, local_kwargs
            )
            support = get_support_capability(interface_instance.__class__)
            manager = support.get_manager(
                interface_instance.__class__,
                only_active=False,
            )
            mutation = _mutation_capability_for(interface_instance.__class__)
            if has_upload_candidates(normalized_simple):
                prepared = prepare_upload_claims(
                    interface_instance.__class__,
                    normalized_simple,
                    operation=UploadOperation.UPDATE,
                    actor_id=creator_id,
                    target_pk=interface_instance.pk,
                )

                def _update_claimed() -> MutationResult:
                    locked = lock_upload_claims(prepared)
                    instance = manager.select_for_update().get(pk=interface_instance.pk)
                    ordinary_values = {
                        key: value
                        for key, value in normalized_simple.items()
                        if not isinstance(value, UploadCandidate)
                    }
                    instance = mutation.assign_simple_attributes(
                        interface_instance.__class__,
                        instance,
                        ordinary_values,
                    )
                    reserved_values = reserve_upload_names(
                        locked,
                        instance,
                        normalized_simple,
                    )
                    claimed_instance = mutation.assign_simple_attributes(
                        interface_instance.__class__,
                        instance,
                        reserved_values,
                    )
                    claimed_pk = mutation.save_with_history(
                        interface_instance.__class__,
                        claimed_instance,
                        creator_id=creator_id,
                        history_comment=history_comment,
                    )
                    mutation.apply_many_to_many(
                        interface_instance.__class__,
                        claimed_instance,
                        many_to_many_kwargs=normalized_many,
                        history_comment=history_comment,
                    )
                    mark_uploads_finalizing(locked, target_pk=claimed_pk)
                    return {"id": claimed_pk}

                result = run_upload_transaction(prepared, _update_claimed)
                discard_orm_instance_cache(interface_instance.__class__, result["id"])
                return result
            instance = manager.get(pk=interface_instance.pk)
            instance = mutation.assign_simple_attributes(
                interface_instance.__class__, instance, normalized_simple
            )
            database_alias = support.get_database_alias(interface_instance.__class__)
            atomic_context = (
                transaction.atomic(using=database_alias)
                if database_alias
                else transaction.atomic()
            )
            with atomic_context:
                pk = mutation.save_with_history(
                    interface_instance.__class__,
                    instance,
                    creator_id=creator_id,
                    history_comment=history_comment,
                    _savepoint=False,
                )
                mutation.apply_many_to_many(
                    interface_instance.__class__,
                    instance,
                    many_to_many_kwargs=normalized_many,
                    history_comment=history_comment,
                )
            discard_orm_instance_cache(interface_instance.__class__, pk)
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
        interface_instance: OrmInterfaceInstance,
        *args: object,
        **kwargs: object,
    ) -> MutationResult:
        """
        Delete or deactivate the provided ORM interface instance according to the interface's deletion policy.

        If soft-delete is enabled for the interface, the instance is marked inactive (requires the instance to implement SupportsActivation) and saved with an optional history comment indicating deactivation. If soft-delete is not enabled, the instance is hard-deleted inside a database transaction using the support-provided database alias when available; the function attempts to set `changed_by_id` from `creator_id` and attaches the provided history comment as the change reason before deletion.

        Parameters:
            interface_instance: The interface-wrapped model instance to remove.
            *args: Ignored positional arguments kept for manager/capability
                signature compatibility. Passing positional values has no
                effect.
            **kwargs: Reserved metadata keys are `creator_id` and
                `history_comment`; other keys are currently ignored by this
                capability.

        Returns:
            MutationResult: Capability-level dictionary containing the primary
                key under the key `"id"`. The GeneralManager layer consumes this
                result, invalidates the public manager instance for later field
                reads, and returns according to the manager-level delete
                contract.

        Raises:
            MissingActivationSupportError: If soft-delete is enabled but the instance does not implement activation support.
            Exception: Row lookup, history actor lookup, transaction, delete,
                cache invalidation, change-reason, save, and observability
                errors are not wrapped.
        """
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> MutationResult:
            """
            Delete or deactivate the target ORM instance referenced by the surrounding interface_instance, recording creator and history metadata as appropriate.

            Performs a soft deactivation when soft-delete is enabled for the model (requiring activation support) or a hard delete otherwise. When soft-deactivating, saves the instance with a deactivation history comment; when hard-deleting, sets the change reason and performs the deletion inside a database transaction.

            Returns:
                result: A dictionary of the form `{"id": pk}` where `pk` is the
                    primary key of the affected instance.

            Raises:
                MissingActivationSupportError: If soft-delete is enabled but the instance does not implement activation support.
            """
            local_kwargs = dict(kwargs)
            creator_id = cast(int | None, local_kwargs.pop("creator_id", None))
            history_comment = cast(
                str | None, local_kwargs.pop("history_comment", None)
            )
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
                discard_orm_instance_cache(interface_instance.__class__, pk)
                return {"id": pk}

            history_comment_local = (
                f"{history_comment} (deleted)" if history_comment else "Deleted"
            )
            database_alias = support.get_database_alias(interface_instance.__class__)
            _assign_history_actor(
                instance,
                creator_id=creator_id,
                database_alias=database_alias,
            )
            if model_has_field(instance, "changed_by"):
                object.__setattr__(instance, "changed_by_id", creator_id)
            call_update_change_reason(instance, history_comment_local)
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
            discard_orm_instance_cache(
                interface_instance.__class__, interface_instance.pk
            )
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
        interface_cls: OrmInterfaceClass,
        *,
        payload: MutationPayload,
    ) -> tuple[MutationPayload, ManyToManyPayload]:
        """
        Normalize and split a mutation payload into simple field values and many-to-many relation values.

        Parameters:
            interface_cls (type): The interface class whose schema/normalizer should be used.
            payload: Raw input payload to validate and normalize.

        Returns:
            tuple:
                - MutationPayload: Normalized simple field values suitable for direct assignment.
                - ManyToManyPayload: Normalized many-to-many relation values
                  keyed by `<relation>_id_list`.

        Raises:
            UnknownFieldError: If a payload key is not a known model field or
                attribute.
            Exception: Payload-normalizer and observability errors are not
                wrapped.
        """
        payload_snapshot = {"keys": sorted(payload.keys())}

        def _perform() -> tuple[MutationPayload, ManyToManyPayload]:
            """
            Normalize and validate the provided payload into simple field values and many-to-many lists.

            Performs key validation, splits the payload into simple and many-to-many parts, and returns the normalized results.

            Returns:
                tuple:
                    - normalized_simple: Mapping of simple field names to their normalized values.
                    - normalized_many: Mapping of `<relation>_id_list` keys to lists of normalized values.
            """
            support = get_support_capability(interface_cls)
            normalizer = support.get_payload_normalizer(interface_cls)
            simple_kwargs, many_to_many_kwargs = _validate_and_split_payload(
                normalizer, payload
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
    interface_cls: OrmInterfaceClass,
    payload: MutationPayload,
) -> tuple[MutationPayload, ManyToManyPayload]:
    """
    Normalize a raw mutation payload into simple attributes and many-to-many mappings for the given ORM interface.

    If the interface provides a validation capability with `normalize_payload`, that handler is used; otherwise the support-provided payload normalizer is used to validate keys, split the payload, and normalize values.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The interface class whose normalization rules should be applied.
        payload: The raw input payload to validate and normalize.

    Returns:
        normalized_simple: Simple attribute names mapped to normalized values.
        normalized_many: `<relation>_id_list` keys mapped to lists of normalized related IDs or values.

    Raises:
        UnknownFieldError: If a payload key is not a known model field or
            attribute.
        Exception: Custom validation-capability and payload-normalizer errors
            are not wrapped.
    """
    handler = interface_cls.get_capability_handler("validation")
    if handler is not None and hasattr(handler, "normalize_payload"):
        normalizing_handler = cast(OrmValidationCapability, handler)
        return normalizing_handler.normalize_payload(
            interface_cls, payload=dict(payload)
        )
    support = get_support_capability(interface_cls)
    normalizer = support.get_payload_normalizer(interface_cls)
    simple_kwargs, many_to_many_kwargs = _validate_and_split_payload(
        normalizer, payload
    )
    normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
    normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
    return dict(normalized_simple), {
        key: list(value) for key, value in normalized_many.items()
    }


def _validate_and_split_payload(
    normalizer: Any,
    payload: MutationPayload,
) -> tuple[MutationPayload, MutationPayload]:
    """Validate and split payloads without exposing caller mappings to mutation."""
    normalizer_payload = (
        payload if type(normalizer) is PayloadNormalizer else dict(payload)
    )
    normalizer.validate_keys(normalizer_payload)
    return _split_many_to_many_payload(normalizer, normalizer_payload)


def _split_many_to_many_payload(
    normalizer: Any,
    payload: MutationPayload,
) -> tuple[MutationPayload, MutationPayload]:
    """Split concrete normalizer payloads without mutation; copy for custom normalizers."""
    if type(normalizer) is PayloadNormalizer:
        return normalizer.split_many_to_many_non_mutating(payload)
    return cast(
        tuple[MutationPayload, MutationPayload],
        normalizer.split_many_to_many(payload),
    )


def _assign_history_actor(
    instance: models.Model,
    *,
    creator_id: int | None,
    database_alias: str | None,
) -> None:
    """Assign the current history actor for simple-history-backed writes."""
    if model_has_field(instance, "changed_by"):
        return

    if creator_id is None:
        object.__setattr__(instance, "_history_user", None)
        return

    user_model = get_user_model()
    manager = user_model._default_manager
    if database_alias:
        manager = manager.db_manager(database_alias)
    object.__setattr__(instance, "_history_user", manager.get(pk=creator_id))


def _mutation_capability_for(
    interface_cls: OrmInterfaceClass,
) -> OrmMutationCapability:
    """
    Retrieve the ORM mutation capability associated with the given interface class.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The interface class to query for the orm_mutation capability.

    Returns:
        OrmMutationCapability: The required mutation capability instance for the interface class.
    """
    return cast(
        OrmMutationCapability,
        interface_cls.require_capability(
            "orm_mutation",
            expected_type=OrmMutationCapability,
        ),
    )
