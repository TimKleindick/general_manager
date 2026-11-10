"""Wrapper for accessing permission-relevant data across manager operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Generic, Protocol, TypeVar, cast

from django.db import models, router
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.base import ModelState

from general_manager.manager.general_manager import GeneralManager

if TYPE_CHECKING:
    from general_manager.interface.interfaces.request import RequestInterface
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from general_manager.interface.capabilities.orm.mutations import (
        _RoutedCreatePermission,
    )


class InvalidPermissionDataError(TypeError):
    """Raised when the permission data manager receives unsupported input."""

    def __init__(self) -> None:
        """Build the error for unsupported permission payload types.

        The public message is stable:
        ``permission_data must be either a dict or an instance of GeneralManager.``
        """
        super().__init__(
            "permission_data must be either a dict or an instance of GeneralManager."
        )


class PermissionRelationResolutionError(PermissionError):
    """A target manager reconstructed a different row than the routed ORM lookup."""

    def __init__(self, field_name: str) -> None:
        """Reject unsupported reconstruction before evaluating relation permissions."""
        super().__init__(
            f"Cannot preserve the resolved ORM relation '{field_name}' "
            "while constructing its manager."
        )


GeneralManagerData = TypeVar("GeneralManagerData", bound=GeneralManager)


class _RelatedModelType(Protocol):
    _base_manager: models.Manager[models.Model]


class PermissionDataManager(Generic[GeneralManagerData]):
    """Adapter that exposes permission-related data as a unified interface."""

    def __init__(
        self,
        permission_data: dict[str, object] | GeneralManagerData,
        manager: type[GeneralManagerData] | None = None,
    ) -> None:
        """Wrap a permission payload and expose its fields through attributes.

        ``permission_data`` accepts ``dict`` instances and ``dict`` subclasses,
        not arbitrary ``Mapping`` implementations. Dictionary payloads are used
        for create, update, and mutation checks. Ordinary attribute access uses
        ``dict.get(name)``, so missing keys resolve to ``None`` instead of
        raising ``AttributeError``. Declared ORM and manager-typed request
        relations expose both the related object and its raw identifier. An explicitly supplied raw ID
        wins over a simultaneously supplied relation, including ``None``.
        ``manager`` records the manager class
        associated with a dictionary payload so delegated permission checks can
        resolve related manager values. ``manager=None`` is valid for
        dictionary payloads that do not need delegated manager resolution.

        Manager instance payloads are used for read/delete checks. Attribute
        access delegates to ``getattr(instance, name)`` and propagates that
        lookup's result or exception. For instance payloads, ``manager`` is
        ignored and inferred from ``type(permission_data)``.

        Wrapper attributes and properties take precedence over payload keys with
        the same name. For example, a dictionary key named ``"manager"`` does
        not shadow the :attr:`manager` property; access the original dictionary
        through :attr:`permission_data` when such keys must be read.

        Args:
            permission_data: Dictionary of field names to permission values or
                a ``GeneralManager`` instance whose attributes provide values.
            manager: Manager class associated with a dictionary payload.

        Raises:
            InvalidPermissionDataError: If ``permission_data`` is neither a
                dictionary nor a ``GeneralManager`` instance.
        """
        self.get_data: Callable[[str], object]
        self._permission_data = permission_data
        self._manager: type[GeneralManagerData] | None
        self._relation_normalizer: PayloadNormalizer | None = None
        self._relation_inputs: dict[str, object] = {}
        self._orm_relation_values: dict[str, object] = {}
        self._source_instance: models.Model | None = None
        self._request_interface: type[RequestInterface] | None = None
        self._request_relation_names: set[str] = set()
        self._request_candidate_values: dict[str, object] | None = None
        if isinstance(permission_data, GeneralManager):
            gm_instance = permission_data

            def manager_getter(name: str) -> object:
                """Delegate read/delete attributes to the original manager instance."""
                return getattr(gm_instance, name)

            self.get_data = manager_getter
            self._manager = type(permission_data)
        elif isinstance(permission_data, dict):
            data_mapping = permission_data
            self._manager = manager
            self._relation_normalizer = self._get_relation_normalizer()
            self._set_request_interface()
            self._set_relation_inputs(data_mapping)

            def dict_getter(name: str) -> object:
                """Resolve declared aliases and ordinary permission attributes lazily."""
                if name in self._request_relation_names:
                    return self._get_request_relation_value(data_mapping, name)
                field = self._relation_field(name)
                if field is not None:
                    if name not in self._orm_relation_values:
                        self._orm_relation_values[name] = self._get_relation_value(
                            data_mapping, field, name
                        )
                    return self._orm_relation_values[name]
                return data_mapping.get(name)

            self.get_data = dict_getter
        else:
            raise InvalidPermissionDataError()

    @classmethod
    def for_update(
        cls,
        base_data: GeneralManagerData,
        update_data: dict[str, object],
    ) -> PermissionDataManager[GeneralManagerData]:
        """Create a wrapper representing ``base_data`` with updates applied.

        ``base_data`` must support ``dict(base_data)``. It is converted with
        that operation and then shallowly overlaid with ``update_data``. Values
        from ``update_data`` win on key conflicts; nested dictionaries or other
        mutable values are not deep-merged. The returned wrapper stores
        ``type(base_data)`` as its ``manager`` and exposes only the merged final
        state through dictionary-style missing-key semantics and consistent
        relation aliases. Old aliases do not compete with an explicitly supplied
        update. The original
        object remains available to callers outside this wrapper if they need a
        separate before/after comparison.

        Args:
            base_data: Existing manager instance whose iterable key/value data
                provides the base permission state.
            update_data: Field values to add or override for the permission
                check.

        Returns:
            Wrapper exposing the merged permission state.
        """
        # Keep explicit updates after existing state, including relation aliases.
        merged_data: dict[str, object] = {
            key: value for key, value in base_data if key not in update_data
        }
        merged_data.update(update_data)
        candidate = cls(merged_data, base_data.__class__)
        # Only this call's kwargs compete; snapshot aliases are fallback state.
        candidate._set_relation_inputs(update_data)
        if candidate._relation_normalizer is not None:
            source_instance = getattr(base_data._interface, "_instance", None)
            if isinstance(source_instance, models.Model):
                candidate._source_instance = source_instance
        return candidate

    def _get_relation_normalizer(self) -> PayloadNormalizer | None:
        """Reuse ORM write metadata without guessing aliases for scalar fields."""
        from general_manager.permission.relation_aliases import orm_relation_normalizer

        return orm_relation_normalizer(self._manager)

    def _set_relation_inputs(self, payload: dict[str, object]) -> None:
        """Replace explicit inputs and invalidate cached alias values."""
        self._orm_relation_values.clear()
        normalizer = self._relation_normalizer
        self._relation_inputs = (
            normalizer.resolve_relation_aliases(payload) if normalizer else payload
        )
        self._request_candidate_values = None

    def _set_request_interface(self) -> None:
        """Recognize explicit manager-typed request fields, not scalar suffixes."""
        interface = getattr(self._manager, "Interface", None)
        if self._relation_normalizer is not None or not isinstance(interface, type):
            return
        from general_manager.interface.interfaces.request import RequestInterface
        from general_manager.interface.capabilities.request.relation_inputs import (
            request_relations,
        )

        if not issubclass(interface, RequestInterface):
            return
        self._request_interface = interface
        self._request_relation_names = {
            name
            for relation in request_relations(interface)
            for name in (relation.name, relation.id_name)
        }

    def _get_request_relation_value(self, data: dict[str, object], name: str) -> object:
        """Resolve and cache the requested declared request-relation alias."""
        from general_manager.interface.capabilities.request.relation_inputs import (
            request_relation_values,
        )

        interface = self._request_interface
        assert interface is not None
        if self._request_candidate_values is None:
            self._request_candidate_values = {}
        if name not in self._request_candidate_values:
            self._request_candidate_values.update(
                request_relation_values(
                    interface,
                    data,
                    explicit_values=self._relation_inputs,
                    attribute=name,
                )
            )
        return self._request_candidate_values.get(name)

    def _resolves_relation(self, name: str) -> bool:
        """Whether this check view supplies a consistent declared alias pair."""
        return (
            name in self._request_relation_names
            or self._relation_field(name) is not None
        )

    def _relation_field(self, name: str) -> models.ForeignKey[models.Model] | None:
        """Find the ORM field owning a declared relation alias."""
        normalizer = self._relation_normalizer
        return normalizer.get_relation_field(name) if normalizer else None

    def _get_relation_value(
        self,
        data: dict[str, object],
        field: models.ForeignKey[models.Model],
        name: str,
    ) -> object:
        """Expose both aliases of the relation intended by this operation."""
        from general_manager.interface.capabilities.orm.mutations import (
            _CreatePermissionTargetChangedError,
            _routed_create_check,
        )

        create = _routed_create_check.get()
        if create is not None and (
            create.manager is not self._manager or self._source_instance is not None
        ):
            create = None
        normalizer = self._relation_normalizer
        assert normalizer is not None

        def get_identifier(value: object) -> object:
            """Read the effective key for an explicit input or Django's default."""
            if create is not None and value is models.NOT_PROVIDED:
                value = getattr(create.get_source(), field.attname)
            return (
                None
                if value is None
                else normalizer.get_relation_identifier(field, value)
            )

        inputs = self._relation_inputs
        if field.name in inputs or field.attname in inputs:
            value = inputs.get(field.attname, inputs.get(field.name))
        else:
            # An unchanged update uses the relation from the original snapshot.
            value = (
                data.get(field.name, data.get(field.attname))
                if create is None
                else None
            )
        identifier = get_identifier(value)
        if create is not None:
            payload = create.payload
            if payload is not data and identifier != get_identifier(
                payload.get(field.attname, payload.get(field.name))
            ):
                # A copied payload can reuse the route, but cannot authorize a
                # different relation from the one the caller intends to write.
                raise _CreatePermissionTargetChangedError
            if field.name in payload or field.attname in payload:
                create.checked_relations[field.attname] = (field, identifier)
        if identifier is None or name == field.attname:
            return identifier

        related_type = field.related_model
        manager_type = getattr(related_type, "_general_manager_class", None)
        # A write check needs the target on the write database even if a read
        # router would select a replica. Standalone checks retain Django hints.
        if create is not None:
            if create.routing_hint is None:
                create.routing_hint = self._routing_hint(create)
            source = create.routing_hint
            database = create.database_alias
        else:
            source = self._routing_hint()
            database = None
        related_manager = cast(
            _RelatedModelType, related_type
        )._base_manager.db_manager(using=database, hints={"instance": source})
        # A supplied object may have stale or edited attributes even when its
        # PK and database match. Authorize the row currently selected by the key.
        related = related_manager.get(**{field.target_field.name: identifier})
        if manager_type is None:
            return related
        return self._manager_for_relation(field, related, manager_type)

    @staticmethod
    def _manager_for_relation(
        field: models.ForeignKey[models.Model],
        related: models.Model,
        manager_type: type[GeneralManager],
    ) -> GeneralManager:
        """Hydrate through the owner and fail closed if custom construction changes rows.

        Custom constructors retain their normal contract. They are supported
        here only when the resulting identification and cached ORM row preserve
        the selected PK, database, and target key; no constructor is bypassed.
        """
        try:
            manager = manager_type._from_trusted_orm_instance(related)
        except ObjectDoesNotExist as error:
            raise PermissionRelationResolutionError(field.name) from error
        if not isinstance(manager, manager_type):
            raise PermissionRelationResolutionError(field.name)
        backing = getattr(getattr(manager, "_interface", None), "_instance", None)
        if (
            manager.identification.get("id") != related.pk
            or not isinstance(backing, field.related_model)
            or backing.pk != related.pk
            or backing._state.db != related._state.db
            or getattr(backing, field.target_field.attname)
            != getattr(related, field.target_field.attname)
        ):
            raise PermissionRelationResolutionError(field.name)
        return manager

    def _routing_hint(
        self, create: _RoutedCreatePermission | None = None
    ) -> models.Model:
        """Build a routing hint with the proposed field values.

        Copy the existing model for an update or the lazily initialized model
        for an actual routed create. Standalone checks use literal defaults.
        """
        from general_manager.interface.capabilities.orm.mutations import (
            _copy_create_source,
            _UnboundRoutedCreatePermissionError,
        )
        from general_manager.interface.capabilities.orm.support import (
            get_support_capability,
        )

        normalizer = self._relation_normalizer
        assert normalizer is not None
        model = normalizer.model
        database = (
            get_support_capability(create.interface).get_database_alias(
                create.interface
            )
            if create is not None
            else getattr(getattr(self._manager, "Interface", None), "database", None)
        )
        source = self._source_instance
        if create is not None:
            if create.shares_source:
                source = create.get_source()
            elif database is None:
                raise _UnboundRoutedCreatePermissionError
        if source is None:
            hint = object.__new__(model)
            hint._state = ModelState()
            primary_key = model._meta.pk
            assert primary_key is not None
            hint.__dict__[primary_key.attname] = None
        else:
            hint = _copy_create_source(source)
        fields_cache = cast(dict[str, object], hint._state.fields_cache)
        values = normalizer.normalize_simple_values(
            create.payload if create is not None else self._relation_inputs
        )
        for field in model._meta.concrete_fields:
            value = values.get(field.attname, models.NOT_PROVIDED)
            if value is not models.NOT_PROVIDED:
                hint.__dict__[field.attname] = field.to_python(value)
                if field.is_relation:
                    fields_cache.pop(field.name, None)
            elif field.name in values and isinstance(field, models.ForeignKey):
                related = values[field.name]
                if related is None:
                    hint.__dict__[field.attname] = None
                    fields_cache.pop(field.name, None)
                elif isinstance(related, models.Model):
                    hint.__dict__[field.attname] = getattr(
                        related, field.target_field.attname
                    )
                    fields_cache[field.name] = related
                    if hint._state.db is None:
                        hint._state.db = router.db_for_write(model, instance=related)
            elif create is not None and create.shares_source:
                default = hint.__dict__.get(field.attname, models.NOT_PROVIDED)
                if default is not models.NOT_PROVIDED and not hasattr(
                    default, "resolve_expression"
                ):
                    hint.__dict__[field.attname] = field.to_python(default)
            elif source is None and field.has_default() and not callable(field.default):
                hint.__dict__[field.attname] = (
                    normalizer.get_relation_identifier(field, field.default)
                    if isinstance(field, models.ForeignKey)
                    else field.to_python(field.default)
                )
        hint._state.db = database or router.db_for_write(model, instance=hint)
        return hint

    @property
    def permission_data(self) -> dict[str, object] | GeneralManagerData:
        """Return the original mapping or manager instance payload."""
        return self._permission_data

    @property
    def manager(self) -> type[GeneralManagerData] | None:
        """Return the manager class associated with the permission data."""
        return self._manager

    def __getattr__(self, name: str) -> object:
        """Return the named value from the wrapped permission payload."""
        return self.get_data(name)
