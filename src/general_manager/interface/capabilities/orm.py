"""ORM-backed capability implementations."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, Type, Callable, cast, ClassVar

from datetime import datetime, timedelta

from django.db import models, transaction
from django.db.models import NOT_PROVIDED, Subquery
from django.utils import timezone

from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.base_interface import InterfaceBase
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
from simple_history.models import HistoricalChanges
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
        soft_delete = _is_soft_delete_enabled(interface_cls)
        selector = DjangoManagerSelector(
            model=interface_cls._model,
            database_alias=self.get_database_alias(interface_cls),
            use_soft_delete=soft_delete,
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
            descriptors = build_field_descriptors(
                interface_cls,
                resolve_many=self.resolve_many_to_many,
            )
            interface_cls._field_descriptors = descriptors  # type: ignore[attr-defined]
        return descriptors

    def resolve_many_to_many(
        self,
        interface_instance: "OrmPersistenceInterface",
        field_call: str,
        field_name: str,
    ) -> models.QuerySet[Any]:
        manager = getattr(interface_instance._instance, field_call)
        queryset = manager.all()
        model_cls = getattr(queryset, "model", None)
        interface_cls = interface_instance.__class__
        if isinstance(model_cls, type) and issubclass(model_cls, HistoricalChanges):
            target_field = interface_cls._model._meta.get_field(field_name)  # type: ignore[attr-defined]
            target_model = getattr(target_field, "related_model", None)
            if target_model is None:
                return manager.none()
            target_model = cast(Type[models.Model], target_model)
            related_attr = None
            for rel_field in model_cls._meta.get_fields():  # type: ignore[attr-defined]
                related_model = getattr(rel_field, "related_model", None)
                if related_model == target_model:
                    related_attr = rel_field.name
                    break
            if related_attr is None:
                return target_model._default_manager.none()
            related_id_field = f"{related_attr}_id"
            related_ids_query = queryset.values_list(related_id_field, flat=True)
            if (
                not hasattr(target_model, "history")
                or interface_instance._search_date is None  # type: ignore[attr-defined]
            ):
                return target_model._default_manager.filter(
                    pk__in=Subquery(related_ids_query)
                )
            target_model = cast(Type[SupportsHistory], target_model)

            related_ids = list(related_ids_query)
            if not related_ids:
                return target_model._default_manager.none()  # type: ignore[return-value]
            return cast(
                models.QuerySet[Any],
                target_model.history.as_of(interface_instance._search_date).filter(  # type: ignore[attr-defined]
                    pk__in=related_ids
                ),
            )

        return queryset


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
        support = _support_capability_for(interface_class)
        new_class.objects = support.get_manager(interface_class)  # type: ignore[attr-defined]
        if _is_soft_delete_enabled(interface_class):
            new_class.all_objects = support.get_manager(  # type: ignore[attr-defined]
                interface_class,
                only_active=False,
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

    def describe_custom_fields(
        self,
        model: type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        field_names: list[str] = []
        ignore: list[str] = []
        for attr_name, attr_value in model.__dict__.items():
            if isinstance(attr_value, models.Field):
                recorded_name = getattr(attr_value, "name", attr_name)
                field_names.append(recorded_name)
                ignore.append(f"{recorded_name}_value")
                ignore.append(f"{recorded_name}_unit")
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
        interface_cls._soft_delete_default = use_soft_delete  # type: ignore[attr-defined]
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
            support = _support_capability_for(interface_cls)
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
            support = _support_capability_for(interface_cls)
            only_active = not _is_soft_delete_enabled(interface_cls)
            manager = support.get_manager(
                interface_cls,
                only_active=only_active,
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
                        history_handler = _history_capability_for(interface_cls)
                        historical = history_handler.get_historical_record(
                            interface_cls,
                            instance,
                            search_date,
                        )
                    else:
                        history_handler = _history_capability_for(interface_cls)
                        historical = history_handler.get_historical_record_by_pk(
                            interface_cls,
                            pk,
                            search_date,
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

    def get_attribute_types(
        self,
        interface_cls: type["OrmPersistenceInterface"],
    ) -> dict[str, dict[str, Any]]:
        descriptors = _support_capability_for(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {
            name: dict(descriptor.metadata) for name, descriptor in descriptors.items()
        }

    def get_attributes(
        self,
        interface_cls: type["OrmPersistenceInterface"],
    ) -> dict[str, Callable[[Any], Any]]:
        descriptors = _support_capability_for(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {name: descriptor.accessor for name, descriptor in descriptors.items()}

    def get_field_type(
        self,
        interface_cls: type["OrmPersistenceInterface"],
        field_name: str,
    ) -> type:
        field = interface_cls._model._meta.get_field(field_name)
        if (
            field.is_relation
            and field.related_model
            and hasattr(field.related_model, "_general_manager_class")
        ):
            return field.related_model._general_manager_class  # type: ignore[attr-defined]
        return type(field)


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
            support = _support_capability_for(interface_instance.__class__)
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
            support = _support_capability_for(interface_instance.__class__)
            manager = support.get_manager(
                interface_instance.__class__,
                only_active=False,
            )
            instance = manager.get(pk=interface_instance.pk)
            mutation = _mutation_capability_for(interface_instance.__class__)
            if _is_soft_delete_enabled(interface_instance.__class__):
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
        database_alias = _support_capability_for(interface_cls).get_database_alias(
            interface_cls
        )
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
        database_alias = _support_capability_for(interface_cls).get_database_alias(
            interface_cls
        )
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

    def normalize_payload(
        self,
        interface_cls: type["OrmWritableInterface"],
        *,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        payload_snapshot = {"keys": sorted(payload.keys())}

        def _perform() -> tuple[dict[str, Any], dict[str, list[Any]]]:
            support = _support_capability_for(interface_cls)
            normalizer = support.get_payload_normalizer(interface_cls)
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
        support = _support_capability_for(interface_cls)
        normalizer = support.get_payload_normalizer(interface_cls)
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
        support = _support_capability_for(interface_cls)
        queryset_base = support.get_queryset(interface_cls)
        if include_inactive:
            queryset_base = support.get_manager(
                interface_cls,
                only_active=False,
            ).all()
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
    support = _support_capability_for(interface_cls)
    normalizer = support.get_payload_normalizer(interface_cls)
    payload_copy = dict(payload)
    normalizer.validate_keys(payload_copy)
    simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(payload_copy)
    normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
    normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
    return normalized_simple, normalized_many


def _mutation_capability_for(
    interface_cls: type["OrmWritableInterface"],
) -> OrmMutationCapability:
    return interface_cls.require_capability(  # type: ignore[return-value]
        "orm_mutation",
        expected_type=OrmMutationCapability,
    )


def _soft_delete_capability_for(
    interface_cls: type["OrmPersistenceInterface"],
) -> SoftDeleteCapability:
    return interface_cls.require_capability(  # type: ignore[return-value]
        "soft_delete",
        expected_type=SoftDeleteCapability,
    )


def _is_soft_delete_enabled(interface_cls: type["OrmPersistenceInterface"]) -> bool:
    handler = interface_cls.get_capability_handler("soft_delete")
    if isinstance(handler, SoftDeleteCapability):
        return handler.is_enabled()
    model = getattr(interface_cls, "_model", None)
    if model is not None:
        meta = getattr(model, "_meta", None)
        if meta is not None:
            return bool(getattr(meta, "use_soft_delete", False))
    return bool(getattr(interface_cls, "_soft_delete_default", False))


def _support_capability_for(
    interface_cls: type["OrmPersistenceInterface"],
) -> OrmPersistenceSupportCapability:
    return interface_cls.require_capability(  # type: ignore[return-value]
        "orm_support",
        expected_type=OrmPersistenceSupportCapability,
    )


def _history_capability_for(
    interface_cls: type["OrmPersistenceInterface"],
) -> OrmHistoryCapability:
    return interface_cls.require_capability(  # type: ignore[return-value]
        "history",
        expected_type=OrmHistoryCapability,
    )


class SoftDeleteCapability(BaseCapability):
    """Track whether soft delete behavior should be applied."""

    name: ClassVar[CapabilityName] = "soft_delete"

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
        default_marker = object()
        default = getattr(interface_cls, "_soft_delete_default", default_marker)
        if default is default_marker:
            model = getattr(interface_cls, "_model", None)
            meta = getattr(model, "_meta", None) if model is not None else None
            default = (
                getattr(meta, "use_soft_delete", self.enabled) if meta else self.enabled
            )
        self.enabled = bool(default)
        super().setup(interface_cls)

    def is_enabled(self) -> bool:
        return self.enabled

    def set_state(self, enabled: bool) -> None:
        self.enabled = enabled
