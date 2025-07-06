from __future__ import annotations
from typing import Generic, Type, Any, TYPE_CHECKING, TypeVar
from general_manager.manager.meta import GeneralManagerMeta

from general_manager.api.property import GraphQLProperty
from general_manager.cache.cacheTracker import DependencyTracker
from general_manager.cache.signals import dataChange
from general_manager.bucket.baseBucket import Bucket

if TYPE_CHECKING:
    from general_manager.permission.basePermission import BasePermission
    from general_manager.interface.baseInterface import (
        InterfaceBase,
    )
GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
InterfaceType = TypeVar("InterfaceType", bound="InterfaceBase", covariant=True)


class GeneralManager(
    Generic[GeneralManagerType, InterfaceType], metaclass=GeneralManagerMeta
):
    Interface: Type[InterfaceType]
    Permission: Type[BasePermission]
    _attributes: dict[str, Any]

    def __init__(self, *args: Any, **kwargs: Any):
        """
        Initialize the manager by creating an interface instance with the provided arguments and storing its identification.

        The identification is registered with the dependency tracker for tracking purposes.
        """
        self._interface = self.Interface(*args, **kwargs)
        self.__id: dict[str, Any] = self._interface.identification
        DependencyTracker.track(
            self.__class__.__name__, "identification", f"{self.__id}"
        )

    def __str__(self):
        return f"{self.__class__.__name__}(**{self.__id})"

    def __repr__(self):
        return f"{self.__class__.__name__}(**{self.__id})"

    def __reduce__(self) -> str | tuple[Any, ...]:
        """
        Support object serialization by returning a tuple containing the class and identification values for pickling.
        """
        return (self.__class__, tuple(self.__id.values()))

    def __or__(
        self,
        other: (
            GeneralManager[GeneralManagerType, InterfaceType]
            | Bucket[GeneralManagerType]
        ),
    ) -> Bucket[GeneralManagerType]:
        """
        Combine this manager with another manager of the same class or a Bucket using the union operator.

        If combined with a Bucket, returns the union of the Bucket and this manager. If combined with another manager of the same class, returns a Bucket containing both instances. Raises a TypeError for unsupported types.

        Returns:
            Bucket[GeneralManagerType]: A Bucket containing the union of the involved managers.
        """
        if isinstance(other, Bucket):
            return other | self
        elif isinstance(other, GeneralManager) and other.__class__ == self.__class__:
            return self.filter(id__in=[self.__id, other.__id])
        else:
            raise TypeError(f"Unsupported type for union: {type(other)}")

    @property
    def identification(self):
        return self.__id

    def __iter__(self):
        for key, value in self._attributes.items():
            if callable(value):
                yield key, value(self._interface)
                continue
            yield key, value
        for name, value in self.__class__.__dict__.items():
            if isinstance(value, (GraphQLProperty, property)):
                yield name, getattr(self, name)

    @classmethod
    @dataChange
    def create(
        cls,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: dict[str, Any],
    ) -> GeneralManager[GeneralManagerType, InterfaceType]:
        """
        Creates a new managed object using the underlying interface and returns a corresponding manager instance.

        Performs a permission check if a `Permission` class is defined and permission checks are not ignored. Passes all provided arguments to the interface's `create` method.

        Parameters:
            creator_id (int | None): Optional identifier for the creator of the object.
            history_comment (str | None): Optional comment for audit or history tracking.
            ignore_permission (bool): If True, skips the permission check.

        Returns:
            GeneralManager[GeneralManagerType, InterfaceType]: A new manager instance for the created object.
        """
        if not ignore_permission:
            cls.Permission.checkCreatePermission(kwargs, cls, creator_id)
        identification = cls.Interface.create(
            creator_id=creator_id, history_comment=history_comment, **kwargs
        )
        return cls(identification)

    @dataChange
    def update(
        self,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: dict[str, Any],
    ) -> GeneralManager[GeneralManagerType, InterfaceType]:
        """
        Update the underlying interface object with new data and return a new manager instance.

        Parameters:
            creator_id (int | None): Optional identifier for the user performing the update.
            history_comment (str | None): Optional comment describing the update.
            ignore_permission (bool): If True, skips permission checks.
            **kwargs: Additional fields to update on the interface object.

        Returns:
            GeneralManager[GeneralManagerType, InterfaceType]: A new manager instance reflecting the updated object.
        """
        if not ignore_permission:
            self.Permission.checkUpdatePermission(kwargs, self, creator_id)
        self._interface.update(
            creator_id=creator_id,
            history_comment=history_comment,
            **kwargs,
        )
        return self.__class__(**self.identification)

    @dataChange
    def deactivate(
        self,
        creator_id: int | None = None,
        history_comment: str | None = None,
        ignore_permission: bool = False,
    ) -> GeneralManager[GeneralManagerType, InterfaceType]:
        """
        Deactivates the underlying interface object and returns a new manager instance.

        Parameters:
            creator_id (int | None): Optional identifier for the user performing the deactivation.
            history_comment (str | None): Optional comment describing the reason for deactivation.
            ignore_permission (bool): If True, skips permission checks.

        Returns:
            GeneralManager[GeneralManagerType, InterfaceType]: A new instance representing the deactivated object.
        """
        if not ignore_permission:
            self.Permission.checkDeletePermission(self, creator_id)
        self._interface.deactivate(
            creator_id=creator_id, history_comment=history_comment
        )
        return self.__class__(**self.identification)

    @classmethod
    def filter(cls, **kwargs: Any) -> Bucket[GeneralManagerType]:
        DependencyTracker.track(
            cls.__name__, "filter", f"{cls.__parse_identification(kwargs)}"
        )
        return cls.Interface.filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[GeneralManagerType]:
        DependencyTracker.track(
            cls.__name__, "exclude", f"{cls.__parse_identification(kwargs)}"
        )
        return cls.Interface.exclude(**kwargs)

    @classmethod
    def all(cls) -> Bucket[GeneralManagerType]:
        return cls.Interface.filter()

    @staticmethod
    def __parse_identification(kwargs: dict[str, Any]) -> dict[str, Any] | None:
        """
        Return a dictionary with all GeneralManager instances in the input replaced by their identification dictionaries.

        For each key-value pair in the input, any GeneralManager instance is replaced by its identification. Lists and tuples are processed recursively, substituting contained GeneralManager instances with their identifications. Returns None if the resulting dictionary is empty.

        Parameters:
            kwargs (dict[str, Any]): Dictionary to process.

        Returns:
            dict[str, Any] | None: Processed dictionary with identifications, or None if empty.
        """
        output = {}
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
