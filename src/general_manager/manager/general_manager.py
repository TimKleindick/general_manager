from __future__ import annotations
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Iterator, Self, Type

from general_manager.api.property import GraphQLProperty
from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.cache.signals import data_change
from general_manager.logging import get_logger
from general_manager.manager.meta import GeneralManagerMeta, InvalidManagerStateError


class UnsupportedUnionOperandError(TypeError):
    """Raised when attempting to union a manager with an incompatible operand."""

    def __init__(self, operand_type: type) -> None:
        """
        Exception raised when attempting to perform a union with an unsupported operand type.

        Parameters:
            operand_type (type): The operand type that is not supported for the union; its representation is included in the exception message.
        """
        super().__init__(f"Unsupported type for union: {operand_type}.")


if TYPE_CHECKING:
    from general_manager.permission.base_permission import BasePermission
    from general_manager.interface.base_interface import InterfaceBase


logger = get_logger("manager.general")


class GeneralManager(metaclass=GeneralManagerMeta):
    Permission: Type[BasePermission]
    _attributes: dict[str, Any]
    Interface: Type["InterfaceBase"]
    _old_values: dict[str, Any]
    _manager_state_valid: bool
    _manager_state_reason: str | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Create a manager by constructing its Interface and record the resulting identification.

        Parameters:
            *args: Positional arguments forwarded to the Interface constructor.
            **kwargs: Keyword arguments forwarded to the Interface constructor.
        """
        self._interface = self.Interface(*args, **kwargs)
        self.__id: dict[str, Any] = self._interface.identification
        self._manager_state_valid = True
        self._manager_state_reason = None
        DependencyTracker.track(
            self.__class__.__name__,
            "identification",
            serialize_dependency_identifier(self.__id),
        )
        logger.debug(
            "instantiated manager",
            context={
                "manager": self.__class__.__name__,
                "identification": self.__id,
            },
        )

    def __str__(self) -> str:
        """Return a user-friendly representation showing the identification."""
        return f"{self.__class__.__name__}(**{self.__id})"

    def __repr__(self) -> str:
        """Return a detailed representation of the manager instance."""
        return f"{self.__class__.__name__}(**{self.__id})"

    def __reduce__(self) -> str | tuple[Any, ...]:
        """
        Provide pickling support for the manager instance.

        Returns:
            tuple[Any, ...]: Reconstruction data consisting of the class and identification tuple.
        """
        return (self.__class__, tuple(self.__id.values()))

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
    def identification(self) -> dict[str, Any]:
        """Return the identification dictionary used to fetch the managed object."""
        return self.__id

    def _reload_interface_state(self) -> None:
        """Rebuild the backing interface so field access reflects the latest persisted state."""
        self._interface = self.Interface(**self.__id)
        self._manager_state_valid = True
        self._manager_state_reason = None

    def _invalidate_manager_state(self, reason: str) -> None:
        """Mark the manager as invalid for subsequent attribute reads."""
        self._manager_state_valid = False
        self._manager_state_reason = reason

    def _ensure_manager_state_valid(self, attribute_name: str | None = None) -> None:
        """Raise a dedicated error when callers read fields from an invalidated manager."""
        if self._manager_state_valid:
            return
        raise InvalidManagerStateError(
            self.__class__.__name__,
            self._manager_state_reason or "manager state is invalid",
            attribute_name,
        )

    def _ensure_manager_not_invalidated(self) -> None:
        """Raise when a caller attempts to mutate an invalidated manager."""
        self._ensure_manager_state_valid()

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        """Iterate over attribute names and resolved values for the managed object."""
        self._ensure_manager_state_valid()
        for key, value in self._attributes.items():
            if callable(value):
                yield key, value(self._interface)
                continue
            yield key, value
        for name, value in self.__class__.__dict__.items():
            if isinstance(value, (GraphQLProperty, property)):
                yield name, getattr(self, name)

    @classmethod
    @data_change
    def create(
        cls,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: Any,
    ) -> Self:
        """
        Create a new managed object through the interface.

        Parameters:
            creator_id (int | None): Optional identifier of the creating user.
            history_comment (str | None): Audit comment stored with the change.
            ignore_permission (bool): When True, skip permission validation.
            **kwargs (Any): Additional fields forwarded to the interface `create` method.

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
        **kwargs: Any,
    ) -> Self:
        """
        Update the managed object, refresh this manager in place, and return it.

        Parameters:
            creator_id (int | None): Optional identifier of the user performing the update.
            history_comment (str | None): Optional audit comment recorded with the update.
            ignore_permission (bool): If True, skip permission validation.
            **kwargs (Any): Field updates forwarded to the interface.

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
    def filter(cls, **kwargs: Any) -> Bucket[Self]:
        """
        Get a Bucket of managers matching the provided lookup expressions.

        Lookup expressions may include GeneralManager instances (or iterables of them), which are substituted with their identification mappings before being forwarded to the Interface for filtering.

        Parameters:
            **kwargs (Any): Lookup expressions (Django-style) used to filter managers.

        Returns:
            Bucket[Self]: Bucket containing manager instances that match the lookups.
        """
        identifier_map = cls.__parse_identification(kwargs) or kwargs
        if getattr(cls.Interface, "_interface_type", None) != "request":
            DependencyTracker.track(
                cls.__name__, "filter", serialize_dependency_identifier(identifier_map)
            )
        logger.debug(
            "manager filter",
            context={
                "manager": cls.__name__,
                "filters": identifier_map,
            },
        )
        return cls.Interface.filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Self]:
        """
        Return a bucket excluding managers that match the provided lookups.

        Parameters:
            **kwargs (Any): Django-style exclusion expressions forwarded to the interface.

        Returns:
            Bucket[Self]: Bucket of manager instances that do not satisfy the lookups.
        """
        identifier_map = cls.__parse_identification(kwargs) or kwargs
        if getattr(cls.Interface, "_interface_type", None) != "request":
            DependencyTracker.track(
                cls.__name__,
                "exclude",
                serialize_dependency_identifier(identifier_map),
            )
        logger.debug(
            "manager exclude",
            context={
                "manager": cls.__name__,
                "filters": identifier_map,
            },
        )
        return cls.Interface.exclude(**kwargs)

    @classmethod
    def all(cls) -> Bucket[Self]:
        """Return a bucket containing every managed object of this class."""
        logger.debug(
            "manager all",
            context={
                "manager": cls.__name__,
            },
        )
        return cls.Interface.filter()

    @staticmethod
    def __parse_identification(kwargs: dict[str, Any]) -> dict[str, Any] | None:
        """
        Replace manager instances within a filter mapping by their identifications.

        Parameters:
            kwargs (dict[str, Any]): Mapping containing potential manager instances.

        Returns:
            dict[str, Any] | None: Mapping with managers substituted by identification dictionaries, or None if no substitutions occurred.
        """
        output: dict[str, Any] = {}
        for key, value in kwargs.items():
            if isinstance(value, GeneralManager):
                output[key] = value.identification
            elif isinstance(value, list):
                output[key] = [
                    v.identification if isinstance(v, GeneralManager) else v
                    for v in value
                ]
            elif isinstance(value, tuple):
                output[key] = tuple(
                    v.identification if isinstance(v, GeneralManager) else v
                    for v in value
                )
            else:
                output[key] = value
        return output if output else None
