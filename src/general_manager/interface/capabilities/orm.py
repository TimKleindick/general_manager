"""ORM-backed capability implementations."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, Type, cast, ClassVar

from datetime import datetime, timedelta

from django.db import models, transaction
from django.db.models import NOT_PROVIDED
from django.utils import timezone

from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.database_interface_protocols import (
    SupportsActivation,
    SupportsHistory,
)
from general_manager.interface.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
    SoftDeleteGeneralManagerModel,
    SoftDeleteMixin,
    get_full_clean_methode,
)
from general_manager.interface.utils.django_manager_utils import DjangoManagerSelector
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
    MissingActivationSupportError,
)
from general_manager.interface.utils.field_descriptors import (
    FieldDescriptor,
    build_field_descriptors,
)
from general_manager.interface.utils.payload_normalizer import PayloadNormalizer
from general_manager.rule import Rule
from simple_history.utils import update_change_reason

from .builtin import BaseCapability
from .base import CapabilityName
from .utils import with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.backends.database.database_based_interface import (
        OrmPersistenceInterface,
        OrmWritableInterface,
    )


class OrmPersistenceSupportCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "orm_support"

    def get_database_alias(
        self, interface_cls: type["OrmPersistenceInterface"]
    ) -> str | None:
        return getattr(interface_cls, "database", None)

    def get_manager(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        *,
        only_active: bool = True,
    ) -> models.Manager:
        selector = DjangoManagerSelector(
            model=interface_cls._model,
            database_alias=self.get_database_alias(interface_cls),
            use_soft_delete=getattr(interface_cls, "_use_soft_delete", False),
            cached_active=getattr(interface_cls, "_active_manager", None),
        )
        manager = selector.active_manager() if only_active else selector.all_manager()
        interface_cls._active_manager = selector.cached_active  # type: ignore[attr-defined]
        return manager

    def get_queryset(
        self, interface_cls: type["OrmPersistenceInterface"]
    ) -> models.QuerySet:
        manager = self.get_manager(interface_cls, only_active=True)
        queryset: models.QuerySet = manager.all()  # type: ignore[assignment]
        return queryset

    def get_payload_normalizer(
        self, interface_cls: type["OrmPersistenceInterface"]
    ) -> PayloadNormalizer:
        return PayloadNormalizer(cast(Type[models.Model], interface_cls._model))

    def get_field_descriptors(
        self, interface_cls: type["OrmPersistenceInterface"]
    ) -> dict[str, FieldDescriptor]:
        descriptors = getattr(interface_cls, "_field_descriptors", None)
        if descriptors is None:
            descriptors = build_field_descriptors(interface_cls)
            interface_cls._field_descriptors = descriptors  # type: ignore[attr-defined]
        return descriptors


class OrmLifecycleCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "orm_lifecycle"

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["OrmPersistenceInterface"],
        base_model_class: type[GeneralManagerBasisModel],
    ) -> tuple[
        dict[str, Any], type["OrmPersistenceInterface"], type[GeneralManagerBasisModel]
    ]:
        model_fields, meta_class = self._collect_model_fields(interface)
        model_fields["__module__"] = attrs.get("__module__")
        meta_class, use_soft_delete, rules = self._apply_meta_configuration(meta_class)
        if meta_class:
            model_fields["Meta"] = meta_class
        base_classes = self._determine_model_bases(base_model_class, use_soft_delete)
        model = cast(
            type[GeneralManagerBasisModel],
            type(name, base_classes, model_fields),
        )
        self._finalize_model_class(
            model,
            meta_class=meta_class,
            use_soft_delete=use_soft_delete,
            rules=rules,
        )
        attrs["_interface_type"] = interface._interface_type
        interface_cls = self._build_interface_class(interface, model, use_soft_delete)
        attrs["Interface"] = interface_cls

        manager_factory = cast(type | None, attrs.pop("Factory", None))
        factory_definition = manager_factory or getattr(interface, "Factory", None)
        attrs["Factory"] = self._build_factory_class(
            name=name,
            factory_definition=factory_definition,
            interface_cls=interface_cls,
            model=model,
        )

        return attrs, interface_cls, model

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: type["OrmPersistenceInterface"],
        model: type[GeneralManagerBasisModel] | None,
    ) -> None:
        if model is None:
            return
        interface_class._parent_class = new_class  # type: ignore[attr-defined]
        model._general_manager_class = new_class  # type: ignore[attr-defined]
        try:
            new_class.objects = interface_class._get_manager()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        if getattr(interface_class, "_use_soft_delete", False) and hasattr(
            model, "all_objects"
        ):
            new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                only_active=False
            )

    def _collect_model_fields(
        self,
        interface: type["OrmPersistenceInterface"],
    ) -> tuple[dict[str, Any], type | None]:
        custom_fields, ignore_fields = self._handle_custom_fields(interface)
        model_fields: dict[str, Any] = {}
        meta_class: type | None = None
        for attr_name, attr_value in interface.__dict__.items():
            if attr_name.startswith("__"):
                continue
            if attr_name == "Meta" and isinstance(attr_value, type):
                meta_class = attr_value
            elif attr_name == "Factory":
                continue
            elif attr_name in ignore_fields:
                continue
            else:
                model_fields[attr_name] = attr_value
        model_fields.update(custom_fields)
        return model_fields, meta_class

    def _handle_custom_fields(
        self,
        interface: type["OrmPersistenceInterface"],
    ) -> tuple[dict[str, Any], list[str]]:
        model = getattr(interface, "_model", None) or interface
        field_names: dict[str, models.Field] = {}
        ignore: list[str] = []
        for attr_name, attr_value in model.__dict__.items():
            if isinstance(attr_value, models.Field):
                ignore.append(f"{attr_value.name}_value")
                ignore.append(f"{attr_value.name}_unit")
                field_names[attr_name] = attr_value
        return field_names, ignore

    def _apply_meta_configuration(
        self,
        meta_class: type | None,
    ) -> tuple[type | None, bool, list[Any] | None]:
        use_soft_delete = False
        rules: list[Any] | None = None
        if meta_class is None:
            return None, use_soft_delete, rules
        if hasattr(meta_class, "use_soft_delete"):
            use_soft_delete = meta_class.use_soft_delete
            delattr(meta_class, "use_soft_delete")
        if hasattr(meta_class, "rules"):
            rules = cast(list[Rule], meta_class.rules)
            delattr(meta_class, "rules")
        return meta_class, use_soft_delete, rules

    def _determine_model_bases(
        self,
        base_model_class: type[GeneralManagerBasisModel],
        use_soft_delete: bool,
    ) -> tuple[type[models.Model], ...]:
        if not use_soft_delete:
            return (base_model_class,)
        if (
            base_model_class is GeneralManagerModel
            or base_model_class is GeneralManagerBasisModel
        ) and issubclass(SoftDeleteGeneralManagerModel, base_model_class):
            return (SoftDeleteGeneralManagerModel,)
        if issubclass(base_model_class, SoftDeleteMixin):
            return (base_model_class,)
        return (cast(type[models.Model], SoftDeleteMixin), base_model_class)

    def _finalize_model_class(
        self,
        model: type[GeneralManagerBasisModel],
        *,
        meta_class: type | None,
        use_soft_delete: bool,
        rules: list[Any] | None,
    ) -> None:
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore[attr-defined]
            model.full_clean = get_full_clean_methode(model)  # type: ignore[assignment]
        if meta_class and use_soft_delete:
            model._meta.use_soft_delete = use_soft_delete  # type: ignore[attr-defined]

    def _build_interface_class(
        self,
        interface: type["OrmPersistenceInterface"],
        model: type[GeneralManagerBasisModel],
        use_soft_delete: bool,
    ) -> type["OrmPersistenceInterface"]:
        interface_cls = type(interface.__name__, (interface,), {})
        interface_cls._model = model  # type: ignore[attr-defined]
        interface_cls._use_soft_delete = use_soft_delete  # type: ignore[attr-defined]
        interface_cls._field_descriptors = None  # type: ignore[attr-defined]
        return interface_cls

    def _build_factory_class(
        self,
        *,
        name: str,
        factory_definition: type | None,
        interface_cls: type["OrmPersistenceInterface"],
        model: type[GeneralManagerBasisModel],
    ) -> type[AutoFactory]:
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)


class OrmMutationCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "orm_mutation"

    def assign_simple_attributes(
        self,
        interface_cls: type["OrmWritableInterface"],
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

        return with_observability(
            interface_cls,
            operation="mutation.assign_simple",
            payload=payload_snapshot,
            func=_perform,
        )

    def save_with_history(
        self,
        interface_cls: type["OrmWritableInterface"],
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
            database_alias = interface_cls._get_database_alias()
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

        result = with_observability(
            interface_cls,
            operation="mutation.save_with_history",
            payload=payload_snapshot,
            func=_perform,
        )
        if history_comment:
            update_change_reason(instance, history_comment)
        return result

    def apply_many_to_many(
        self,
        interface_cls: type["OrmWritableInterface"],
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

        result = with_observability(
            interface_cls,
            operation="mutation.apply_many_to_many",
            payload=payload_snapshot,
            func=_perform,
        )
        if history_comment:
            update_change_reason(instance, history_comment)
        return result


class OrmReadCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "OrmPersistenceInterface") -> Any:
        def _perform() -> Any:
            interface_cls = interface_instance.__class__
            manager = interface_cls._get_manager(
                only_active=not getattr(interface_cls, "_use_soft_delete", False)
            )
            model_cls = interface_cls._model
            pk = interface_instance.pk
            instance: Any | None
            missing_error: Exception | None = None
            try:
                instance = manager.get(pk=pk)
            except model_cls.DoesNotExist as error:  # type: ignore[attr-defined]
                instance = None
                missing_error = error
            search_date = interface_instance._search_date
            if search_date is not None:
                if search_date <= timezone.now() - timedelta(
                    seconds=interface_cls.historical_lookup_buffer_seconds
                ):
                    historical: Any | None
                    if instance is not None:
                        historical = interface_cls.get_historical_record(
                            instance, search_date
                        )
                    else:
                        historical = interface_cls._get_historical_record_by_pk(
                            pk, search_date
                        )
                    if historical is not None:
                        return historical
            if instance is not None:
                return instance
            if missing_error is not None:
                raise missing_error
            raise model_cls.DoesNotExist  # type: ignore[attr-defined]

        return with_observability(
            interface_instance,
            operation="read",
            payload={"pk": interface_instance.pk},
            func=_perform,
        )


class OrmCreateCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "create"
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def create(
        self,
        interface_cls: type["OrmWritableInterface"],
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

        return with_observability(
            interface_cls,
            operation="create",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmUpdateCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "update"
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def update(
        self,
        interface_instance: "OrmWritableInterface",
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
            manager = interface_instance.__class__._get_manager(only_active=False)
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

        return with_observability(
            interface_instance,
            operation="update",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmDeleteCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "delete"
    required_attributes: ClassVar[tuple[str, ...]] = ()

    def delete(
        self,
        interface_instance: "OrmWritableInterface",
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        _ = args
        payload_snapshot = {"kwargs": dict(kwargs), "pk": interface_instance.pk}

        def _perform() -> dict[str, Any]:
            local_kwargs = dict(kwargs)
            creator_id = local_kwargs.pop("creator_id", None)
            history_comment = local_kwargs.pop("history_comment", None)
            manager = interface_instance.__class__._get_manager(only_active=False)
            instance = manager.get(pk=interface_instance.pk)
            mutation = _mutation_capability_for(interface_instance.__class__)
            if getattr(interface_instance.__class__, "_use_soft_delete", False):
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
            update_change_reason(instance, history_comment_local)
            database_alias = interface_instance.__class__._get_database_alias()
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

        return with_observability(
            interface_instance,
            operation="delete",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmHistoryCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "history"

    def get_historical_record(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        instance: Any,
        search_date: datetime | None = None,
    ) -> Any | None:
        if not isinstance(instance, SupportsHistory):
            return None
        history_manager = cast(SupportsHistory, instance).history
        database_alias = interface_cls._get_database_alias()
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            cast(models.QuerySet, history_manager.filter(history_date__lte=search_date))
            .order_by("history_date")
            .last()
        )
        return historical

    def get_historical_record_by_pk(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        pk: Any,
        search_date: datetime | None,
    ) -> Any | None:
        if search_date is None or not hasattr(interface_cls._model, "history"):
            return None
        history_manager = interface_cls._model.history  # type: ignore[attr-defined]
        database_alias = interface_cls._get_database_alias()
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            history_manager.filter(id=pk, history_date__lte=search_date)
            .order_by("history_date")
            .last()
        )
        return historical


class OrmValidationCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "validation"
    required_attributes: ClassVar[tuple[str, ...]] = ("_payload_normalizer",)

    def normalize_payload(
        self,
        interface_cls: type["OrmWritableInterface"],
        *,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        payload_snapshot = {"keys": sorted(payload.keys())}

        def _perform() -> tuple[dict[str, Any], dict[str, list[Any]]]:
            normalizer = interface_cls._payload_normalizer()
            payload_copy = dict(payload)
            normalizer.validate_keys(payload_copy)
            simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(
                payload_copy
            )
            normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
            normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
            return normalized_simple, normalized_many

        return with_observability(
            interface_cls,
            operation="validation.normalize",
            payload=payload_snapshot,
            func=_perform,
        )


class OrmQueryCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "query"
    required_attributes: ClassVar[tuple[str, ...]] = (
        "_payload_normalizer",
        "_get_queryset",
    )

    def filter(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        **kwargs: Any,
    ) -> DatabaseBucket:
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket:
            include_flag, normalized = self._normalize_kwargs(interface_cls, kwargs)
            return self._build_bucket(
                interface_cls,
                include_inactive=include_flag,
                normalized_kwargs=normalized,
            )

        return with_observability(
            interface_cls,
            operation="query.filter",
            payload=payload_snapshot,
            func=_perform,
        )

    def exclude(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        **kwargs: Any,
    ) -> DatabaseBucket:
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket:
            include_flag, normalized = self._normalize_kwargs(interface_cls, kwargs)
            return self._build_bucket(
                interface_cls,
                include_inactive=include_flag,
                normalized_kwargs=normalized,
                exclude=True,
            )

        return with_observability(
            interface_cls,
            operation="query.exclude",
            payload=payload_snapshot,
            func=_perform,
        )

    def _normalize_kwargs(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        kwargs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        payload = dict(kwargs)
        include_inactive = bool(payload.pop("include_inactive", False))
        normalizer = interface_cls._payload_normalizer()
        normalized_kwargs = normalizer.normalize_filter_kwargs(payload)
        return include_inactive, normalized_kwargs

    def _build_bucket(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        *,
        include_inactive: bool,
        normalized_kwargs: dict[str, Any],
        exclude: bool = False,
    ) -> DatabaseBucket:
        queryset_base = interface_cls._get_queryset()
        if include_inactive:
            queryset_base = interface_cls._get_manager(only_active=False).all()
        queryset = (
            queryset_base.exclude(**normalized_kwargs)
            if exclude
            else queryset_base.filter(**normalized_kwargs)
        )
        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            interface_cls._parent_class,
            dict(normalized_kwargs),
        )


def _normalize_payload(
    interface_cls: type["OrmWritableInterface"],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    handler = interface_cls.get_capability_handler("validation")
    if handler is not None and hasattr(handler, "normalize_payload"):
        return handler.normalize_payload(interface_cls, payload=dict(payload))
    normalizer = interface_cls._payload_normalizer()
    payload_copy = dict(payload)
    normalizer.validate_keys(payload_copy)
    simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(payload_copy)
    normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
    normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
    return normalized_simple, normalized_many


def _mutation_capability_for(
    interface_cls: type["OrmWritableInterface"],
) -> OrmMutationCapability:
    handler = interface_cls.get_capability_handler("orm_mutation")
    if isinstance(handler, OrmMutationCapability):
        return handler
    return OrmMutationCapability()
