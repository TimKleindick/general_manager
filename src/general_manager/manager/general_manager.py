from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from datetime import date, datetime
from json.encoder import encode_basestring_ascii
from typing import TYPE_CHECKING, ClassVar, Iterator, Protocol, Self, Type, cast

from general_manager.api.property import GraphQLProperty
from general_manager.as_of import (
    HistoricalContextConflictError,
    _represents_same_instant,
    as_of,
    current_as_of_date,
    ensure_as_of_read_supported,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.cache.signals import data_change
from general_manager.logging import get_logger
from general_manager.manager.meta import GeneralManagerMeta, InvalidManagerStateError


class UnsupportedUnionOperandError(TypeError):
    """
    Raised when attempting to union a manager with an incompatible operand.

    The public message is ``"Unsupported type for union: {operand_type}."``.
    """

    def __init__(self, operand_type: type) -> None:
        """
        Exception raised when attempting to perform a union with an unsupported operand type.

        Parameters:
            operand_type (type): The operand type that is not supported for the union; its representation is included in the exception message.
        """
        super().__init__(f"Unsupported type for union: {operand_type}.")


class TrustedOrmHydrationNotSupportedError(TypeError):
    """
    Raised when trusted ORM hydration is requested for a non-ORM interface.

    The public message is
    ``"{interface_name} does not support trusted ORM hydration."``.
    """

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} does not support trusted ORM hydration.")


if TYPE_CHECKING:
    from general_manager.permission.base_permission import BasePermission
    from general_manager.interface.base_interface import InterfaceBase


logger = get_logger("manager.general")


def _debug_logging_enabled() -> bool:
    is_enabled_for = getattr(logger, "isEnabledFor", None)
    if callable(is_enabled_for):
        return bool(is_enabled_for(logging.DEBUG))
    return True


def _serialize_simple_id_identification(
    identification: dict[str, object],
) -> str | None:
    if len(identification) != 1 or "id" not in identification:
        return None
    value = identification["id"]
    if value is True:
        serialized_value = "true"
    elif value is False:
        serialized_value = "false"
    elif value is None:
        serialized_value = "null"
    elif isinstance(value, int):
        serialized_value = str(value)
    elif isinstance(value, str):
        serialized_value = encode_basestring_ascii(value)
    elif isinstance(value, float):
        serialized_value = json.dumps(value)
    elif isinstance(value, datetime):
        serialized_value = encode_basestring_ascii(value.isoformat())
    elif isinstance(value, date):
        serialized_value = encode_basestring_ascii(value.isoformat())
    else:
        return None
    return f'{{"id": {serialized_value}}}'


def _track_identification_dependency_active_default(
    cls: type[object],
    identification: dict[str, object],
) -> None:
    identifier = _serialize_simple_id_identification(identification)
    if identifier is None:
        identifier = serialize_dependency_identifier(identification)
    DependencyTracker._track_validated(
        type.__getattribute__(cls, "__name__"),
        "identification",
        identifier,
    )


class TrustedOrmRow(Protocol):
    """Protocol for ORM rows accepted by trusted hydration."""

    pk: object


type _IdentificationDependencyCache = tuple[type[object], object, str]
_EFFECTIVE_SEARCH_DATE_MISSING = object()


def _effective_search_date_for_interface(interface: object) -> datetime | None:
    """Return the snapshot instant represented by an interface instance."""
    behavior = getattr(interface.__class__, "_as_of_behavior", "unsupported")
    if behavior == "historical":
        search_date = getattr(interface, "_search_date", None)
        return search_date if isinstance(search_date, datetime) else None
    if behavior == "transparent":
        return current_as_of_date()
    return None


def _reconstruct_general_manager(
    manager_class: type["GeneralManager"],
    identification_values: tuple[object, ...],
    effective_search_date: datetime | None,
) -> "GeneralManager":
    """Reconstruct a manager with the backing snapshot encoded by its pickle."""
    behavior = getattr(manager_class.Interface, "_as_of_behavior", "unsupported")
    if effective_search_date is not None and behavior == "historical":
        return manager_class(
            *identification_values,
            search_date=effective_search_date,
        )
    if effective_search_date is not None and behavior == "transparent":
        with as_of(effective_search_date):
            return manager_class(*identification_values)
    return manager_class(*identification_values)


class GeneralManager(metaclass=GeneralManagerMeta):
    chat_exposed: bool = False
    _gm_uses_default_identification_dependency_active: ClassVar[bool] = True
    Permission: Type[BasePermission]
    _attributes: dict[str, object]
    Interface: Type["InterfaceBase"]
    _old_values: dict[str, object]
    _attribute_value_cache: dict[str, object]
    _identification_dependency_cache: _IdentificationDependencyCache | None
    _manager_state_valid: bool
    _manager_state_reason: str | None
    _effective_search_date: datetime | None

    @classmethod
    def _track_identification_dependency(
        cls,
        identification: dict[str, object],
    ) -> None:
        """Track an identification dependency only when a tracker is active."""

        if DependencyTracker.is_active():
            if type.__getattribute__(
                cls,
                "_gm_uses_default_identification_dependency_active",
            ):
                _track_identification_dependency_active_default(cls, identification)
            else:
                cls._track_identification_dependency_active(identification)

    @classmethod
    def _track_identification_dependency_active(
        cls,
        identification: dict[str, object],
    ) -> None:
        """Record an identification dependency for the default tracking path."""

        _track_identification_dependency_active_default(cls, identification)

    def _identification_dependency_identifier(self) -> str:
        identification = self.__id
        if len(identification) == 1 and "id" in identification:
            value = identification["id"]
            value_class = value.__class__
            cache = self._identification_dependency_cache
            if cache is not None and cache[0] is value_class and cache[1] == value:
                return cache[2]
            identifier = _serialize_simple_id_identification(identification)
            if identifier is not None:
                self._identification_dependency_cache = (
                    value_class,
                    value,
                    identifier,
                )
                return identifier
        self._identification_dependency_cache = None
        return serialize_dependency_identifier(identification)

    def _track_own_identification_dependency_active(self) -> None:
        """Replay this instance's identification dependency on cached reads."""

        manager_class = self.__class__
        if type.__getattribute__(
            manager_class,
            "_gm_uses_default_identification_dependency_active",
        ):
            DependencyTracker._track_validated(
                type.__getattribute__(manager_class, "__name__"),
                "identification",
                self._identification_dependency_identifier(),
            )
            return
        manager_class._track_identification_dependency_active(self.__id)

    def __init__(self, *args: object, **kwargs: object) -> None:
        """
        Create a manager by constructing its Interface and record the resulting identification.

        Parameters:
            *args: Positional arguments forwarded to the Interface constructor.
            **kwargs: Keyword arguments forwarded to the Interface constructor.
        """
        ensure_as_of_read_supported(self.Interface)
        self._interface = self.Interface(*args, **kwargs)
        self._effective_search_date = _effective_search_date_for_interface(
            self._interface
        )
        self.__id: dict[str, object] = self._interface.identification
        self._attribute_value_cache = {}
        self._identification_dependency_cache = None
        self._manager_state_valid = True
        self._manager_state_reason = None
        self._track_identification_dependency(self.__id)
        if _debug_logging_enabled():
            logger.debug(
                "instantiated manager",
                context={
                    "manager": self.__class__.__name__,
                    "identification": self.__id,
                },
            )

    @classmethod
    def _from_trusted_orm_instance(
        cls,
        instance: TrustedOrmRow,
        *,
        search_date: object | None = None,
    ) -> Self:
        """
        Build a manager around an ORM-loaded row without public input validation.

        This private path is only for framework-owned Django ORM rows. It must
        not be used for GraphQL, mutation, import, factory, or other external
        payloads. Managers that use the base constructor hydrate through the
        interface's trusted ORM hook and bypass public Interface input
        validation. Managers with a custom ``__init__`` are reconstructed with
        ``cls(instance.pk)`` or ``cls(instance.pk, search_date=search_date)`` so
        their custom construction contract still runs. ORM interfaces normalize
        ``search_date`` while building the trusted interface or public
        constructor path.

        Raises:
            TrustedOrmHydrationNotSupportedError: If the Interface does not
                expose a callable trusted ORM hydration hook.
        """
        ensure_as_of_read_supported(cls.Interface)
        hydrate = getattr(cls.Interface, "_from_trusted_orm_instance", None)
        if not callable(hydrate):
            raise TrustedOrmHydrationNotSupportedError(cls.Interface.__name__)

        if cls.__init__ is not GeneralManager.__init__:
            pk = instance.pk
            if search_date is None:
                return cls(pk)
            return cls(pk, search_date=search_date)

        from general_manager.cache.run_context import (
            TRUSTED_ORM_MANAGER_PREFIX,
            current_calculation_run_context,
        )

        context = current_calculation_run_context()
        cache_key = (
            TRUSTED_ORM_MANAGER_PREFIX,
            cls,
            id(instance),
            instance.pk,
            search_date,
        )
        if context is not None:
            cached = context.get(cache_key)
            if isinstance(cached, cls):
                cls._track_identification_dependency(cached.identification)
                return cached

        manager = cls.__new__(cls)
        manager._interface = hydrate(instance, search_date=search_date)
        manager._effective_search_date = _effective_search_date_for_interface(
            manager._interface
        )
        manager.__id = manager._interface.identification
        manager._attribute_value_cache = {}
        manager._identification_dependency_cache = None
        manager._manager_state_valid = True
        manager._manager_state_reason = None
        if context is not None:
            context.set(cache_key, manager)
        cls._track_identification_dependency(manager.__id)
        if _debug_logging_enabled():
            logger.debug(
                "trusted orm manager hydrated",
                context={
                    "manager": cls.__name__,
                    "identification": manager.__id,
                },
            )
        return manager

    def __str__(self) -> str:
        """Return a user-friendly representation showing the identification."""
        return f"{self.__class__.__name__}(**{self.__id})"

    @classmethod
    def _is_request_interface(cls) -> bool:
        return getattr(cls.Interface, "_interface_type", None) == "request"

    def __repr__(self) -> str:
        """Return a detailed representation of the manager instance."""
        return f"{self.__class__.__name__}(**{self.__id})"

    def __getattr__(self, attribute_name: str) -> object:
        """
        Lazily install descriptors for declared fields on late-imported managers.

        Manager classes imported after Django app startup are registered but have
        not yet had descriptor properties attached. Falling back here lets
        interactive and test-only managers behave like startup-loaded managers
        while preserving normal ``AttributeError`` behavior for unknown names.
        The fallback calls
        ``GeneralManagerMeta.ensure_attributes_initialized(self.__class__, attribute_name)``;
        a truthy result means descriptors were installed and the attribute is
        returned through ``object.__getattribute__``. A falsey result raises
        ``AttributeError(attribute_name)``.
        """
        if GeneralManagerMeta.ensure_attributes_initialized(
            self.__class__, attribute_name
        ):
            return object.__getattribute__(self, attribute_name)
        raise AttributeError(attribute_name)

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        """Provide snapshot-preserving pickling support for this manager."""
        effective = self.__dict__.get("_effective_search_date")
        if not isinstance(effective, datetime):
            interface = self.__dict__.get("_interface")
            if (
                interface is not None
                and getattr(interface.__class__, "_as_of_behavior", "unsupported")
                == "historical"
            ):
                candidate = getattr(interface, "_search_date", None)
                effective = candidate if isinstance(candidate, datetime) else None
            else:
                effective = None
        return (
            _reconstruct_general_manager,
            (self.__class__, tuple(self.__id.values()), effective),
        )

    def __or__(
        self,
        other: Self | Bucket[Self],
    ) -> Bucket[Self]:
        """
        Combine this manager with another manager or a Bucket into a Bucket representing their union.

        Parameters:
            other (Self | Bucket[Self]): A manager of the same class or a Bucket to union with.

        Returns:
            Bucket[Self]: A Bucket containing the union of the managed objects represented by this manager and `other`.

        Raises:
            UnsupportedUnionOperandError: If `other` is not a Bucket and not a GeneralManager instance of the same class.
        """
        if isinstance(other, Bucket):
            return other | self
        elif isinstance(other, GeneralManager) and other.__class__ == self.__class__:
            return self.filter(id__in=[self.__id, other.__id])
        else:
            raise UnsupportedUnionOperandError(type(other))

    def __eq__(
        self,
        other: object,
    ) -> bool:
        """
        Determine whether another object represents the same managed entity.

        Returns:
            `true` if `other` is a `GeneralManager` whose identification equals this manager's, `false` otherwise.
        """
        if not isinstance(other, GeneralManager):
            return False
        return self.identification == other.identification

    @property
    def identification(self) -> dict[str, object]:
        """Return the identification dictionary used to fetch the managed object."""
        return self.__id

    @property
    def history(self) -> object:
        """Return the history queryset scoped to this manager instance."""
        from general_manager.interface.capabilities.orm import HistoryNotSupportedError

        history_handler = self.Interface.get_capability_handler("history")
        if history_handler is None:
            raise HistoryNotSupportedError(self.Interface.__name__)
        get_history_queryset = getattr(
            history_handler,
            "get_history_queryset_for_manager",
            None,
        )
        if not callable(get_history_queryset):
            raise HistoryNotSupportedError(self.Interface.__name__)
        return get_history_queryset(self.Interface, self)

    def _reload_interface_state(self) -> None:
        """
        Rebuild the backing interface and mark this manager state as valid.

        The interface is reconstructed as ``self.Interface(**identification)``;
        ``_manager_state_valid`` is set to ``True`` and
        ``_manager_state_reason`` is cleared.
        """
        ensure_as_of_read_supported(self.Interface)
        behavior = getattr(self.Interface, "_as_of_behavior", "unsupported")
        if behavior == "historical":
            self._ensure_as_of_compatible()

        previous_effective = self.__dict__.get(
            "_effective_search_date", _EFFECTIVE_SEARCH_DATE_MISSING
        )
        if (
            behavior == "historical"
            and previous_effective is _EFFECTIVE_SEARCH_DATE_MISSING
        ):
            legacy_search_date = getattr(self._interface, "_search_date", None)
            previous_effective = (
                legacy_search_date if isinstance(legacy_search_date, datetime) else None
            )
        interface_kwargs = dict(self.__id)
        if behavior == "historical" and isinstance(previous_effective, datetime):
            interface_kwargs["search_date"] = previous_effective
        self._interface = self.Interface(**interface_kwargs)
        self._effective_search_date = _effective_search_date_for_interface(
            self._interface
        )
        self._attribute_value_cache = {}
        self._manager_state_valid = True
        self._manager_state_reason = None

    def _invalidate_manager_state(self, reason: str) -> None:
        """
        Mark the manager as invalid for subsequent attribute reads.

        ``_manager_state_valid`` becomes ``False`` and
        ``_manager_state_reason`` stores ``reason``.
        """
        self._manager_state_valid = False
        self._manager_state_reason = reason
        self._attribute_value_cache = {}

    def _ensure_manager_state_valid(self, attribute_name: str | None = None) -> None:
        """
        Raise a dedicated error when callers read fields from an invalidated manager.

        Valid managers return without side effects. Invalid managers raise
        ``InvalidManagerStateError`` with this class name, the stored invalidation
        reason or ``"manager state is invalid"``, and the optional attribute name.
        """
        self._ensure_as_of_compatible()
        if self._manager_state_valid:
            return
        raise InvalidManagerStateError(
            self.__class__.__name__,
            self._manager_state_reason or "manager state is invalid",
            attribute_name,
        )

    def _ensure_as_of_compatible(self) -> None:
        """Reject reads when this manager is bound to another snapshot."""
        active = current_as_of_date()
        if active is None:
            return

        effective = self.__dict__.get(
            "_effective_search_date", _EFFECTIVE_SEARCH_DATE_MISSING
        )
        if effective is _EFFECTIVE_SEARCH_DATE_MISSING:
            interface = self.__dict__.get("_interface")
            if (
                interface is not None
                and getattr(interface.__class__, "_as_of_behavior", "unsupported")
                == "historical"
            ):
                candidate = getattr(interface, "_search_date", None)
                effective = candidate if isinstance(candidate, datetime) else None
            else:
                effective = None
            self._effective_search_date = cast(datetime | None, effective)

        if not isinstance(effective, datetime) or not _represents_same_instant(
            effective, active
        ):
            raise HistoricalContextConflictError

    def _ensure_manager_not_invalidated(self) -> None:
        """Raise when a caller attempts to mutate an invalidated manager."""
        self._ensure_manager_state_valid()

    def __iter__(self) -> Iterator[tuple[str, object]]:
        """
        Iterate over attribute names and resolved values for the managed object.

        Callable entries in ``_attributes`` are invoked with ``self._interface``.
        The synthetic ``history`` property and generated relation descriptors are
        skipped. GraphQLProperty and normal property class attributes are yielded
        by reading them through ``getattr(self, name)``.
        """
        self._ensure_manager_state_valid()
        for key, value in self._attributes.items():
            if callable(value):
                yield key, value(self._interface)
                continue
            yield key, value
        for name, value in self.__class__.__dict__.items():
            if name == "history":
                continue
            if getattr(value, "_general_manager_generated_relation", False):
                continue
            if isinstance(value, (GraphQLProperty, property)):
                yield name, getattr(self, name)

    @classmethod
    @data_change
    def create(
        cls,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: object,
    ) -> Self:
        """
        Create a new managed object through the interface.

        Parameters:
            creator_id (int | None): Optional identifier of the creating user.
            history_comment (str | None): Audit comment stored with the change.
            ignore_permission (bool): When True, skip permission validation.
            **kwargs: Additional fields forwarded to the interface `create` method.

        Returns:
            Self: Manager instance representing the created object.

        Raises:
            PermissionError: Propagated if the permission check fails.
        """
        if not ignore_permission:
            cls.Permission.check_create_permission(kwargs, cls, creator_id)
        identification = cls.Interface.create(
            creator_id=creator_id, history_comment=history_comment, **kwargs
        )
        logger.info(
            "manager created",
            context={
                "manager": cls.__name__,
                "creator_id": creator_id,
                "ignore_permission": ignore_permission,
                "fields": sorted(kwargs.keys()),
                "identification": identification,
            },
        )
        return cls(**identification)

    @data_change
    def update(
        self,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: object,
    ) -> Self:
        """
        Update the managed object, refresh this manager in place, and return it.

        Parameters:
            creator_id (int | None): Optional identifier of the user performing the update.
            history_comment (str | None): Optional audit comment recorded with the update.
            ignore_permission (bool): If True, skip permission validation.
            **kwargs: Field updates forwarded to the interface.

        Returns:
            Self: This manager instance after reloading its backing interface state.

        Raises:
            PermissionError: If the permission check fails when `ignore_permission` is False.
        """
        self._ensure_manager_not_invalidated()
        if not ignore_permission:
            self.Permission.check_update_permission(kwargs, self, creator_id)
        self._interface.update(
            creator_id=creator_id,
            history_comment=history_comment,
            **kwargs,
        )
        payload_cache = getattr(self._interface, "_request_payload_cache", None)
        self._reload_interface_state()
        if isinstance(payload_cache, Mapping) and hasattr(
            self._interface, "set_request_payload_cache"
        ):
            self._interface.set_request_payload_cache(payload_cache)
        logger.info(
            "manager updated",
            context={
                "manager": self.__class__.__name__,
                "creator_id": creator_id,
                "ignore_permission": ignore_permission,
                "fields": sorted(kwargs.keys()),
                "identification": self.identification,
            },
        )
        return self

    @data_change
    def delete(
        self,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
    ) -> None:
        """
        Delete the managed object; performs a soft delete when the underlying interface is configured accordingly.

        Parameters:
            creator_id (int | None): Optional identifier of the user performing the action.
            history_comment (str | None): Audit comment recorded with the deletion.
            ignore_permission (bool): When True, skip permission validation.

        Raises:
            PermissionError: If permission validation fails.
        """
        self._ensure_manager_not_invalidated()
        if not ignore_permission:
            self.Permission.check_delete_permission(self, creator_id)
        self._interface.delete(creator_id=creator_id, history_comment=history_comment)
        self._invalidate_manager_state("manager was deleted")
        logger.info(
            "manager deleted",
            context={
                "manager": self.__class__.__name__,
                "creator_id": creator_id,
                "ignore_permission": ignore_permission,
                "identification": self.identification,
            },
        )

    @classmethod
    def filter(cls, **kwargs: object) -> Bucket[Self]:
        """
        Get a Bucket of managers matching the provided lookup expressions.

        Lookup expressions may include GeneralManager instances, or lists/tuples
        containing them. Single-field manager identifications are replaced with
        their ``id`` value only when the identification mapping has exactly the
        one key ``"id"``. Empty mappings, single-key non-``"id"`` mappings, and
        multi-key mappings are replaced with a copied identification mapping
        before the lookups are forwarded to the interface.
        Normalization is shallow: only top-level lookup values and direct
        list/tuple items are inspected. Non-manager values, including nested
        containers, are forwarded unchanged. When no manager values are present,
        the original ``kwargs`` mapping is delegated. The method returns the
        interface result typed as ``Bucket[Self]`` and does not wrap
        identification, mapping-copy, interface, or bucket errors.

        Parameters:
            **kwargs: Lookup expressions used to filter managers.

        Returns:
            Bucket[Self]: Bucket containing manager instances that match the lookups.
        """
        identifier_map = cls.__parse_identification(kwargs) or kwargs
        if _debug_logging_enabled():
            logger.debug(
                "manager filter",
                context={
                    "manager": type.__getattribute__(cls, "__name__"),
                    "filters": identifier_map,
                },
            )
        return cast(Bucket[Self], cls.Interface.filter(**identifier_map))

    @classmethod
    def get(cls, **kwargs: object) -> Self:
        """
        Return the single manager matching the provided lookup expressions.

        This is a convenience wrapper around ``filter(...).get()`` and preserves
        the underlying bucket's single-item exception behavior.
        """
        return cls.filter(**kwargs).get()

    @classmethod
    def exclude(cls, **kwargs: object) -> Bucket[Self]:
        """
        Return a bucket excluding managers that match the provided lookups.

        Lookup normalization, return typing, and error propagation match
        ``filter(...)``.

        Parameters:
            **kwargs: Lookup expressions forwarded to the interface as exclusions.

        Returns:
            Bucket[Self]: Bucket of manager instances that do not satisfy the lookups.
        """
        identifier_map = cls.__parse_identification(kwargs) or kwargs
        if _debug_logging_enabled():
            logger.debug(
                "manager exclude",
                context={
                    "manager": type.__getattribute__(cls, "__name__"),
                    "filters": identifier_map,
                },
            )
        return cast(Bucket[Self], cls.Interface.exclude(**identifier_map))

    @classmethod
    def all(cls) -> Bucket[Self]:
        """Return a bucket containing every managed object of this class."""
        if _debug_logging_enabled():
            logger.debug(
                "manager all",
                context={
                    "manager": type.__getattribute__(cls, "__name__"),
                },
            )
        return cast(Bucket[Self], cls.Interface.filter())

    @staticmethod
    def __parse_identification(
        kwargs: dict[str, object],
    ) -> dict[str, object] | None:
        """
        Replace manager instances within a filter mapping by lookup identifiers.

        Parameters:
            kwargs: Mapping containing potential manager instances.

        Returns:
            Mapping with managers substituted by lookup identifiers, or
            ``None`` if no substitutions occurred.
        """
        needs_normalization = False
        for value in kwargs.values():
            if isinstance(value, GeneralManager):
                needs_normalization = True
                break
            if isinstance(value, (list, tuple)):
                if any(isinstance(item, GeneralManager) for item in value):
                    needs_normalization = True
                    break
        if not needs_normalization:
            return None

        output: dict[str, object] = {}
        changed = False
        for key, value in kwargs.items():
            if isinstance(value, GeneralManager):
                output[key] = GeneralManager.__lookup_identifier(value)
                changed = True
            elif isinstance(value, list):
                normalized = [
                    GeneralManager.__lookup_identifier(v)
                    if isinstance(v, GeneralManager)
                    else v
                    for v in value
                ]
                output[key] = normalized
                changed = changed or normalized != value
            elif isinstance(value, tuple):
                normalized_tuple = tuple(
                    GeneralManager.__lookup_identifier(v)
                    if isinstance(v, GeneralManager)
                    else v
                    for v in value
                )
                output[key] = normalized_tuple
                changed = changed or normalized_tuple != value
            else:
                output[key] = value
        return output if changed else None

    @staticmethod
    def __lookup_identifier(manager: GeneralManager) -> object:
        """Return the scalar id for normal managers, or a copy for composite ids."""
        identification = manager.identification
        if set(identification) == {"id"}:
            return identification["id"]
        return dict(identification)
