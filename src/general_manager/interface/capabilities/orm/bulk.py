"""Conservative SQL bulk creation for explicitly opted-in ORM managers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from inspect import getattr_static
from typing import TYPE_CHECKING, Any, cast

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import EMPTY_VALUES
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import connections, models, router
from django.db.models import UniqueConstraint
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)
from django.utils import timezone
from simple_history.models import HistoricalRecords
from simple_history.signals import (
    post_create_historical_record,
    pre_create_historical_record,
)

from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.orm.mutations import (
    OrmCreateCapability,
    OrmMutationCapability,
    OrmValidationCapability,
)
from general_manager.interface.capabilities.orm.support import (
    OrmPersistenceSupportCapability,
    get_support_capability,
)
from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
    PayloadNormalizer,
)
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.interface.utils.models import (
    GeneralManagerModel,
    get_full_clean_methode,
    model_has_field,
)
from general_manager.interface.utils.history import DatabaseAwareHistoricalRecords
from general_manager.manager.bulk_create import CreateManyUnsupportedError

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.manager.general_manager import GeneralManager


@dataclass(frozen=True)
class BulkCreateEligibility:
    """A stable diagnostic result for the opt-in SQL creation path."""

    eligible: bool
    reasons: tuple[str, ...]


class BulkCreateRecordError(Exception):
    """Keep the input position when local batch validation rejects a row."""

    def __init__(self, index: int, cause: BaseException) -> None:
        self.index = index
        self.cause = cause
        super().__init__(str(cause))


class BulkCreateManyToManyError(ValueError):
    """A normalized input attempted a relation that SQL creation cannot apply."""


class BulkCreateReturningUnsupportedError(RuntimeError):
    """The selected Django backend cannot return generated bulk primary keys."""


class BulkCreateInputUnsupportedError(CreateManyUnsupportedError):
    """An input targets a canonical extension SQL creation cannot reproduce."""

    def __init__(self, keys: Sequence[str]) -> None:
        super().__init__(
            "BulkCreate SQL creation cannot assign non-concrete model input "
            f"key(s): {', '.join(keys)}. Disable BulkCreate.enabled to use "
            "canonical creation."
        )


_UNIQUE_DISJUNCTION_CHUNK_SIZE = 500
_DEFAULT_HISTORY_USER = getattr_static(GeneralManagerModel, "_history_user")


def _query_parameter_chunk_size(alias: str, value_width: int) -> int:
    """Bound set-based validation queries by the selected backend's limit."""
    max_params = getattr(connections[alias].features, "max_query_params", None)
    if not isinstance(max_params, int) or max_params <= 0:
        return _UNIQUE_DISJUNCTION_CHUNK_SIZE
    return max(
        1,
        min(_UNIQUE_DISJUNCTION_CHUNK_SIZE, max_params // max(value_width, 1)),
    )


def _bulk_config(manager: type["GeneralManager"]) -> object | None:
    return getattr(manager, "BulkCreate", None)


def _is_generated_full_clean(model: type[models.Model]) -> bool:
    clean = model.full_clean
    expected = get_full_clean_methode(model)
    return getattr(clean, "__code__", None) is expected.__code__


def _signal_receivers_are_framework_owned(model: type[models.Model]) -> bool:
    """Allow only simple-history's exact bound ``post_save`` method.

    The comparison deliberately uses the underlying function identity.  Module
    names are never treated as an allowlist because applications can place
    arbitrary receiver code in a framework-looking module.
    """
    for signal in (pre_save, post_save, pre_delete, post_delete, m2m_changed):
        sync_receivers, async_receivers = signal._live_receivers(model)
        for receiver in (*sync_receivers, *async_receivers):
            target = getattr(receiver, "__func__", receiver)
            if signal is post_save and target is HistoricalRecords.post_save:
                tracker = getattr(receiver, "__self__", None)
                if type(tracker) is not DatabaseAwareHistoricalRecords:
                    return False
                continue
            if signal is pre_delete and target is HistoricalRecords.pre_delete:
                continue
            if signal is post_delete and target is HistoricalRecords.post_delete:
                continue
            return False
    return True


def _has_default_history_tracker(model: type[models.Model]) -> bool:
    """Allow only the generated history tracker configuration we reproduce.

    ``HistoricalRecords`` exposes several callbacks and model-shape options.
    Bulk insertion does not invoke those callbacks, so an otherwise exact
    ``DatabaseAwareHistoricalRecords`` subclass is insufficient evidence that
    a customized tracker remains equivalent.
    """
    sync_receivers, async_receivers = post_save._live_receivers(model)
    trackers = [
        getattr(receiver, "__self__", None)
        for receiver in (*sync_receivers, *async_receivers)
        if getattr(receiver, "__func__", receiver) is HistoricalRecords.post_save
    ]
    if len(trackers) != 1 or type(trackers[0]) is not DatabaseAwareHistoricalRecords:
        return False
    tracker = trackers[0]
    expected = DatabaseAwareHistoricalRecords(inherit=True)
    return all(
        getattr(tracker, name, object()) == value
        for name, value in vars(expected).items()
    )


def _manager_receivers_are_framework_owned(manager: type["GeneralManager"]) -> bool:
    """Reject arbitrary manager lifecycle observers before bypassing ``create``."""
    from general_manager.api.graphql import GraphQL
    from general_manager.api.remote_invalidation import emit_remote_invalidation
    from general_manager.cache.dependency_index import generic_cache_invalidation
    from general_manager.cache.batch_refresh import is_batch_refresh_receiver
    from general_manager.cache.dependency_index import capture_old_values
    from general_manager.cache.signals import (
        data_change_transaction_finished,
        data_change_transaction_finishing,
        data_change_transaction_started,
        post_data_change,
        pre_data_change,
    )
    from general_manager.search.invalidation import (
        _handle_search_post_change,
        _handle_search_pre_change,
    )
    from general_manager.workflow.signal_bridge import _handle_post_data_change

    allowed_pre = {capture_old_values, _handle_search_pre_change}
    allowed_post = {
        generic_cache_invalidation,
        getattr(GraphQL._handle_data_change, "__func__", GraphQL._handle_data_change),
        emit_remote_invalidation,
        _handle_search_post_change,
        _handle_post_data_change,
    }
    for signal, allowed in (
        (pre_data_change, allowed_pre),
        (post_data_change, allowed_post),
    ):
        sync_receivers, async_receivers = signal._live_receivers(manager)
        for receiver in (*sync_receivers, *async_receivers):
            target = getattr(receiver, "__func__", receiver)
            if any(target is allowed_receiver for allowed_receiver in allowed) or (
                is_batch_refresh_receiver(target)
            ):
                continue
            return False
    for signal in (
        data_change_transaction_started,
        data_change_transaction_finishing,
        data_change_transaction_finished,
    ):
        sync_receivers, async_receivers = signal._live_receivers(manager)
        if sync_receivers or async_receivers:
            return False
    return True


def _requires_local_search(manager: type["GeneralManager"]) -> bool:
    from general_manager.cache.signals import post_data_change
    from general_manager.search.invalidation import _handle_search_post_change

    # Search is registered globally, but inspecting the concrete manager also
    # catches a sender-scoped registration.
    sync_receivers, async_receivers = post_data_change._live_receivers(manager)
    return any(
        getattr(receiver, "__func__", receiver) is _handle_search_post_change
        for receiver in (*sync_receivers, *async_receivers)
    )


def bulk_create_eligibility(manager: type["GeneralManager"]) -> BulkCreateEligibility:
    """Explain whether a manager can use the true SQL path for ``create_many``."""
    reasons: list[str] = []
    config = _bulk_config(manager)
    if getattr(config, "enabled", None) is not True:
        reasons.append("BulkCreate.enabled must be exactly True")

    interface = getattr(manager, "Interface", None)
    if not isinstance(interface, type) or not issubclass(interface, OrmInterfaceBase):
        reasons.append("manager does not use a writable ORM interface")
        return BulkCreateEligibility(False, tuple(reasons))
    model = interface._model
    if getattr_static(model, "_history_user", None) is not _DEFAULT_HISTORY_USER or any(
        name in base.__dict__
        for base in model.__mro__
        for name in ("_history_date", "_change_reason")
    ):
        reasons.append("custom history attribution or timestamp hook")
    if router.routers:
        reasons.append("database routers require canonical creation")
    if getattr(interface, "database", None) not in (None, "default"):
        reasons.append("non-default interface database requires canonical creation")
    features = connections["default"].features
    if not getattr(
        features,
        "can_return_rows_from_bulk_insert",
        getattr(features, "can_return_ids_from_bulk_insert", False),
    ):
        reasons.append("database does not return bulk primary keys")
    if interface.__init__ is not OrmInterfaceBase.__init__:
        reasons.append("custom Interface constructor")
    from general_manager.manager.general_manager import GeneralManager

    if manager.__init__ is not GeneralManager.__init__:
        reasons.append("custom manager constructor")
    expected_handlers = {
        "create": OrmCreateCapability,
        "orm_mutation": OrmMutationCapability,
        "validation": OrmValidationCapability,
        "orm_support": OrmPersistenceSupportCapability,
        "observability": LoggingObservabilityCapability,
    }
    for name, expected in expected_handlers.items():
        if type(interface.get_capability_handler(cast(Any, name))) is not expected:
            reasons.append(f"custom {name} capability")

    has_rules = bool(getattr(model._meta, "rules", ())) or bool(
        getattr(interface, "rules", ())
    )
    if has_rules and getattr(config, "local_rules", None) is not True:
        reasons.append("BulkCreate.local_rules must be exactly True when rules exist")
    if getattr(config, "local_permissions", None) is not True:
        reasons.append("BulkCreate.local_permissions must be exactly True")
    if (
        _requires_local_search(manager)
        and getattr(config, "local_search", None) is not True
    ):
        reasons.append(
            "BulkCreate.local_search must be exactly True when search is enabled"
        )
    if model.save is not models.Model.save:
        reasons.append("custom model save")
    if model.save_base is not models.Model.save_base:
        reasons.append("custom model save_base")
    if model.clean is not models.Model.clean:
        reasons.append("custom model clean")
    if model.full_clean is not models.Model.full_clean and not _is_generated_full_clean(
        model
    ):
        reasons.append("custom model full_clean")
    for method_name in ("clean_fields", "validate_unique", "validate_constraints"):
        if getattr(model, method_name) is not getattr(models.Model, method_name):
            reasons.append(f"custom model {method_name}")
    if model._meta.proxy or model._meta.parents:
        reasons.append("proxy or multi-table model")
    if (
        type(cast(Any, model)._base_manager) is not models.Manager
        or type(cast(Any, model)._default_manager) is not models.Manager
    ):
        reasons.append("custom model manager")
    if model._meta.many_to_many:
        reasons.append("many-to-many fields are unsupported")
    if any(
        isinstance(field, (models.FileField, models.ImageField))
        for field in model._meta.fields
    ):
        reasons.append("file fields are unsupported")
    supported_scalar_fields = (
        models.AutoField,
        models.BigAutoField,
        models.SmallAutoField,
        models.IntegerField,
        models.BigIntegerField,
        models.SmallIntegerField,
        models.PositiveIntegerField,
        models.PositiveSmallIntegerField,
        models.FloatField,
        models.DecimalField,
        models.BooleanField,
        models.CharField,
        models.TextField,
        models.DateField,
        models.DateTimeField,
        models.TimeField,
        models.DurationField,
        models.UUIDField,
        models.EmailField,
        models.URLField,
        models.SlugField,
        models.GenericIPAddressField,
    )
    for field in model._meta.fields:
        if isinstance(field, (models.ForeignKey, models.OneToOneField)):
            if field.target_field is not field.remote_field.model._meta.pk:
                reasons.append("foreign keys using to_field are unsupported")
            if getattr(field.remote_field, "limit_choices_to", None):
                reasons.append("limit_choices_to relations are unsupported")
        if (
            getattr(field, "unique_for_date", "")
            or getattr(field, "unique_for_month", "")
            or getattr(field, "unique_for_year", "")
        ):
            reasons.append("date-scoped uniqueness is unsupported")
        if type(field) not in (
            *supported_scalar_fields,
            models.ForeignKey,
            models.OneToOneField,
        ):
            reasons.append("custom or unsupported model field")
        if getattr(field, "db_collation", None):
            reasons.append("database collation field is unsupported")
        if getattr(field, "generated", False) or field.has_db_default():
            reasons.append("database-generated field defaults are unsupported")
    for constraint in model._meta.constraints:
        if (
            type(constraint) is not UniqueConstraint
            or constraint.condition
            or constraint.expressions
            or constraint.nulls_distinct is not None
            or constraint.deferrable is not None
            or getattr(constraint, "include", ())
            or getattr(constraint, "opclasses", ())
        ):
            reasons.append("complex model constraint")
            break
    if not _signal_receivers_are_framework_owned(model):
        reasons.append("custom Django model signal receiver")
    elif not _has_default_history_tracker(model):
        reasons.append("custom history tracker configuration")
    history_model = getattr(getattr(model, "history", None), "model", None)
    if not isinstance(history_model, type) or not hasattr(
        history_model, "tracked_fields"
    ):
        reasons.append("custom history model")
    elif any(
        (
            *signal._live_receivers(history_model)[0],
            *signal._live_receivers(history_model)[1],
        )
        for signal in (pre_create_historical_record, post_create_historical_record)
    ):
        reasons.append("custom history signal receiver")
    elif (
        cast(Any, history_model).save is not models.Model.save
        or cast(Any, history_model).save_base is not models.Model.save_base
        or not _signal_receivers_are_framework_owned(history_model)
        or hasattr(history_model, "history_relation")
    ):
        reasons.append("custom history model persistence")
    if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
        reasons.append("simple history is disabled")
    if not _manager_receivers_are_framework_owned(manager):
        reasons.append("custom manager lifecycle signal receiver")
    return BulkCreateEligibility(not reasons, tuple(reasons))


def _normalize_record(
    normalizer: PayloadNormalizer,
    record: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, list[object]]]:
    payload = dict(record)
    normalizer.validate_keys(payload)
    simple, many = normalizer.split_many_to_many(payload)
    return normalizer.normalize_simple_values(simple), normalizer.normalize_many_values(
        many
    )


def _assert_concrete_assignment_keys(
    model: type[models.Model], values: Mapping[str, object]
) -> None:
    """Reject model methods and descriptors accepted by generic normalization.

    Canonical mutation can intentionally assign supported extension attributes.
    SQL bulk creation has no equivalent hook, so accepting such a payload would
    silently lose data or bypass behaviour.  ``NOT_PROVIDED`` is preserved for
    concrete fields because the mutation capability handles it before insert.
    """
    concrete_names = {
        name for field in model._meta.fields for name in (field.name, field.attname)
    }
    unsupported = sorted(set(values) - concrete_names)
    if unsupported:
        raise BulkCreateInputUnsupportedError(unsupported)


def _assert_no_many_to_many(values: Mapping[str, object]) -> None:
    if values:
        raise BulkCreateManyToManyError


def _validate_foreign_keys(
    model: type[models.Model], instances: Sequence[models.Model], alias: str
) -> None:
    for field in model._meta.fields:
        if not isinstance(field, (models.ForeignKey, models.OneToOneField)):
            continue
        values = {getattr(instance, field.attname) for instance in instances}
        values.discard(None)
        if not values:
            continue
        related_manager = cast(Any, field.remote_field.model)._base_manager
        target_field = field.target_field
        target_name = target_field.attname
        found_objects: dict[object, models.Model] = {}
        ordered_values = tuple(values)
        chunk_size = _query_parameter_chunk_size(alias, 1)
        for start in range(0, len(ordered_values), chunk_size):
            found_objects.update(
                {
                    getattr(related, target_name): related
                    for related in related_manager.using(alias).filter(
                        **{
                            f"{target_name}__in": ordered_values[
                                start : start + chunk_size
                            ]
                        }
                    )
                }
            )
        found = set(found_objects)
        for instance in instances:
            value = getattr(instance, field.attname)
            if value in found_objects:
                field.set_cached_value(instance, found_objects[value])
        missing = values - found
        if missing:
            index = next(
                index
                for index, instance in enumerate(instances)
                if getattr(instance, field.attname) in missing
            )
            raise BulkCreateRecordError(
                index,
                ValidationError(
                    {field.name: ["Selected related object does not exist."]}
                ),
            )


def _unique_groups(model: type[models.Model]) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = [
        (field.name,)
        for field in model._meta.fields
        if field.unique or field.primary_key
    ]
    groups.extend(tuple(group) for group in model._meta.unique_together)
    groups.extend(
        tuple(constraint.fields)
        for constraint in model._meta.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.fields
    )
    return tuple(dict.fromkeys(groups))


def _validate_unique(
    model: type[models.Model], instances: Sequence[models.Model], alias: str
) -> None:
    candidates: list[BulkCreateRecordError] = []
    for fields in _unique_groups(model):
        field_objects = tuple(model._meta.get_field(name) for name in fields)
        attnames = tuple(field.attname for field in field_objects)
        error_key = fields[0] if len(fields) == 1 else NON_FIELD_ERRORS
        error_message = (
            "This value must be unique."
            if len(fields) == 1
            else "This value combination must be unique."
        )
        values_by_index = [
            tuple(getattr(instance, name) for name in attnames)
            for instance in instances
        ]
        seen: set[tuple[object, ...]] = set()
        for index, values in enumerate(values_by_index):
            if any(value is None for value in values):
                continue
            if values in seen:
                candidates.append(
                    BulkCreateRecordError(
                        index,
                        ValidationError({error_key: [error_message]}),
                    )
                )
                continue
            seen.add(values)
        if len(fields) == 1 and seen:
            existing: set[object] = set()
            ordered_unique_values = tuple(values[0] for values in seen)
            chunk_size = _query_parameter_chunk_size(alias, 1)
            for start in range(0, len(ordered_unique_values), chunk_size):
                existing.update(
                    cast(Any, model)
                    ._base_manager.using(alias)
                    .filter(
                        **{
                            f"{attnames[0]}__in": ordered_unique_values[
                                start : start + chunk_size
                            ]
                        }
                    )
                    .values_list(attnames[0], flat=True)
                )
            if existing:
                index = next(
                    index
                    for index, values in enumerate(values_by_index)
                    if values[0] in existing
                )
                candidates.append(
                    BulkCreateRecordError(
                        index,
                        ValidationError({error_key: [error_message]}),
                    )
                )
        elif len(fields) > 1 and seen:
            composite_existing: set[tuple[object, ...]] = set()
            ordered_combinations: tuple[tuple[object, ...], ...] = tuple(seen)
            chunk_size = _query_parameter_chunk_size(alias, len(fields))
            for start in range(0, len(ordered_combinations), chunk_size):
                predicate = models.Q(pk__in=())
                for combination in ordered_combinations[start : start + chunk_size]:
                    predicate |= models.Q(
                        **dict(zip(attnames, combination, strict=True))
                    )
                composite_existing.update(
                    cast(Any, model)
                    ._base_manager.using(alias)
                    .filter(predicate)
                    .values_list(*attnames)
                )
            if composite_existing:
                index = next(
                    index
                    for index, values in enumerate(values_by_index)
                    if values in composite_existing
                )
                candidates.append(
                    BulkCreateRecordError(
                        index,
                        ValidationError({error_key: [error_message]}),
                    )
                )
    if candidates:
        raise min(candidates, key=lambda candidate: candidate.index)


def _validate_scalar_values(instance: models.Model) -> None:
    """Run Django scalar validation without row-by-row related-object lookups."""
    errors: dict[str, list[str]] = defaultdict(list)
    relations = tuple(
        field
        for field in instance._meta.fields
        if isinstance(field, (models.ForeignKey, models.OneToOneField))
    )
    try:
        instance.clean_fields(exclude=[field.name for field in relations])
    except ValidationError as error:
        for name, messages in error.message_dict.items():
            errors[name].extend(messages)
    for field in relations:
        try:
            value = getattr(instance, field.attname)
            if field.blank and value in EMPTY_VALUES:
                continue
            value = field.to_python(value)
            models.Field.validate(field, value, instance)
            field.run_validators(value)
            setattr(instance, field.attname, value)
        except ValidationError as error:
            errors[field.name].extend(error.messages)
        except (TypeError, ValueError):
            errors[field.name].append("Enter a valid value.")
    if errors:
        raise ValidationError(cast(Any, errors))


def _validate_local_rules(instance: models.Model) -> None:
    errors: dict[str, list[str]] = defaultdict(list)
    for rule in getattr(instance._meta, "rules", ()):
        if rule.evaluate(instance) is False:
            for field_name, messages in rule.get_error_message().items():
                errors[field_name].extend(
                    [messages] if isinstance(messages, str) else messages
                )
    if errors:
        raise ValidationError(cast(Any, errors))


def _history_rows(
    model: type[models.Model],
    instances: Sequence[models.Model],
    *,
    creator_id: int | None,
    history_comment: str | None,
    history_user_id: object | None,
) -> tuple[type[models.Model], list[models.Model]]:
    history = cast(Any, model).history
    history_model = cast(type[models.Model], history.model)
    rows: list[models.Model] = []
    included = cast(Sequence[Any], cast(Any, history_model).tracked_fields)
    for instance in instances:
        attrs = {field.attname: getattr(instance, field.attname) for field in included}
        rows.append(
            history_model(
                history_date=timezone.now(),
                history_type="+",
                history_user_id=history_user_id,
                history_change_reason=history_comment,
                **attrs,
            )
        )
    return history_model, rows


def create_many_with_bulk_sql(
    manager: type["GeneralManager"],
    records: Sequence[Mapping[str, object]],
    *,
    creator_id: int | None,
    history_comment: str | None,
    ignore_permission: bool,
    database_alias: str,
) -> tuple[models.Model, ...]:
    """Validate and persist one fully eligible batch without calling ``cls.create``."""
    interface = cast(type[OrmInterfaceBase[models.Model]], manager.Interface)
    model = interface._model
    normalizer = get_support_capability(interface).get_payload_normalizer(interface)
    mutation = cast(
        OrmMutationCapability, interface.get_capability_handler("orm_mutation")
    )
    instances: list[models.Model] = []
    for index, record in enumerate(records):
        try:
            if not ignore_permission:
                manager.Permission.check_create_permission(
                    dict(record), manager, creator_id
                )
            simple, many = _normalize_record(normalizer, record)
            _assert_no_many_to_many(many)
            _assert_concrete_assignment_keys(model, simple)
            instance = mutation.assign_simple_attributes(interface, model(), simple)
            if model_has_field(instance, "changed_by"):
                cast(Any, instance).changed_by_id = creator_id
            _validate_scalar_values(instance)
            instances.append(instance)
        except BaseException as error:
            raise BulkCreateRecordError(index, error) from error
    _validate_foreign_keys(model, instances, database_alias)
    for index, instance in enumerate(instances):
        try:
            _validate_local_rules(instance)
        except BaseException as error:
            raise BulkCreateRecordError(index, error) from error
    _validate_unique(model, instances, database_alias)
    features = connections[database_alias].features
    if not getattr(
        features,
        "can_return_rows_from_bulk_insert",
        getattr(features, "can_return_ids_from_bulk_insert", False),
    ):
        raise BulkCreateReturningUnsupportedError
    cast(Any, model)._base_manager.using(database_alias).bulk_create(
        instances, batch_size=len(instances)
    )
    history_user_id: object | None = None
    if creator_id is not None:
        history_user_id = (
            get_user_model()
            ._default_manager.using(database_alias)
            .get(pk=creator_id)
            .pk
        )
    history_model, history_rows = _history_rows(
        model,
        instances,
        creator_id=creator_id,
        history_comment=history_comment,
        history_user_id=history_user_id,
    )
    cast(Any, history_model)._base_manager.using(database_alias).bulk_create(
        history_rows, batch_size=len(history_rows)
    )
    return tuple(instances)


def publish_bulk_created_rows(
    manager: type["GeneralManager"],
    instances: Sequence[models.Model],
    records: Sequence[Mapping[str, object]],
    creator_id: int | None,
    history_comment: str | None,
    ignore_permission: bool,
    *,
    database_alias: str,
) -> None:
    """Publish persisted rows through the standard manager observers.

    Dependency caches are invalidated once before any observer can read the
    new rows. The batch context suppresses redundant root invalidation while
    retaining ordinary invalidation for nested mutations.
    """
    from general_manager.cache.batch_refresh import is_batch_refresh_receiver
    from general_manager.cache.dependency_index import (
        capture_old_values,
        invalidate_manager_cache,
        record_invalidated_cache_keys_for_graphql_rewarm,
    )
    from general_manager.cache.signals import post_data_change, pre_data_change

    def needs_manager_instances() -> bool:
        from general_manager.api.graphql import GraphQL
        from general_manager.api.remote_invalidation import emit_remote_invalidation
        from general_manager.search.invalidation import (
            _handle_search_pre_change,
            _handle_search_post_change,
            create_search_requires_instance,
        )

        pre_sync, pre_async = pre_data_change._live_receivers(manager)
        post_sync, post_async = post_data_change._live_receivers(manager)
        for receiver in (*pre_sync, *pre_async):
            target = getattr(receiver, "__func__", receiver)
            if target not in {
                capture_old_values,
                _handle_search_pre_change,
            } and not is_batch_refresh_receiver(target):
                return True
        from general_manager.cache.dependency_index import generic_cache_invalidation

        for receiver in (*post_sync, *post_async):
            target = getattr(receiver, "__func__", receiver)
            if target is emit_remote_invalidation:
                # Remote refresh uses the explicit identification payload.
                continue
            if target is getattr(GraphQL._handle_data_change, "__func__", None):
                if manager.__name__ not in GraphQL.manager_registry:
                    continue
            if target is _handle_search_post_change:
                if not create_search_requires_instance(manager, database_alias):
                    continue
            if (
                target is not generic_cache_invalidation
                and not is_batch_refresh_receiver(target)
            ):
                return True
        return False

    invalidated = invalidate_manager_cache(manager.__name__)
    if invalidated:
        record_invalidated_cache_keys_for_graphql_rewarm(invalidated)
    hydrate_managers = needs_manager_instances()
    for row, record in zip(instances, records, strict=True):
        created = manager._from_trusted_orm_instance(row) if hydrate_managers else None
        change_context: dict[str, object] = {}
        pre_data_change.send(
            sender=manager,
            instance=None,
            action="create",
            creator_id=creator_id,
            history_comment=history_comment,
            ignore_permission=ignore_permission,
            change_context=change_context,
            database_alias=database_alias,
        )
        post_data_change.send(
            sender=manager,
            instance=created,
            previous_instance=None,
            identification=(
                dict(created.identification) if created is not None else {"id": row.pk}
            ),
            action="create",
            old_relevant_values={},
            creator_id=creator_id,
            history_comment=history_comment,
            ignore_permission=ignore_permission,
            change_context=change_context,
            database_alias=database_alias,
            **{
                key: value
                for key, value in record.items()
                if key
                not in {
                    "sender",
                    "signal",
                    "instance",
                    "previous_instance",
                    "identification",
                    "action",
                    "old_relevant_values",
                    "change_context",
                    "database_alias",
                }
            },
        )
