from __future__ import annotations
from typing import Type, Any, TYPE_CHECKING, Self
from general_manager.manager.meta import GeneralManagerMeta

from general_manager.api.property import GraphQLProperty
from general_manager.cache.cacheTracker import DependencyTracker
from general_manager.cache.signals import dataChange
from general_manager.bucket.baseBucket import Bucket

if TYPE_CHECKING:
    from general_manager.permission.basePermission import BasePermission


class GeneralManager(metaclass=GeneralManagerMeta):
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
        other: Self | Bucket[Self],
    ) -> Bucket[Self]:
        """
        Returns a Bucket containing the union of this manager and another manager of the same class or a Bucket.

        If `other` is a Bucket, the result is the union of the Bucket and this manager. If `other` is a manager of the same class, the result is a Bucket containing both managers. Raises a TypeError if `other` is not a supported type.

        Returns:
            Bucket[Self]: A Bucket containing the combined managers.
        """
        if isinstance(other, Bucket):
            return other | self
        elif isinstance(other, GeneralManager) and other.__class__ == self.__class__:
            return self.filter(id__in=[self.__id, other.__id])
        else:
            raise TypeError(f"Unsupported type for union: {type(other)}")

    def __eq__(
        self,
        other: object,
    ) -> bool:
        """
        Check equality based on the identification dictionary.

        Returns True if the other object is a GeneralManager with the same identification, otherwise False.
        """
        if not isinstance(other, GeneralManager):
            return False
        return self.identification == other.identification

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
        **kwargs: Any,
    ) -> Self:
        """
        Create a new managed object via the underlying interface and return a manager instance representing it.

        Performs a permission check unless `ignore_permission` is True. All additional keyword arguments are passed to the interface's `create` method.

        Parameters:
            creator_id (int | None): Optional ID of the user creating the object.
            history_comment (str | None): Optional comment for audit or history purposes.
            ignore_permission (bool): If True, bypasses the permission check.

        Returns:
            Self: Manager instance for the newly created object.
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
        **kwargs: Any,
    ) -> Self:
        """
        Update the managed object with new data and return a new manager instance reflecting the changes.

        Parameters:
            creator_id (int | None): Identifier of the user performing the update, if applicable.
            history_comment (str | None): Optional comment describing the update.
            ignore_permission (bool): If True, bypasses permission checks.
            **kwargs: Fields and values to update on the managed object.

        Returns:
            Self: A new manager instance representing the updated object.
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
    ) -> Self:
        """
        Deactivate the managed object and return a new manager instance representing its deactivated state.

        Parameters:
            creator_id (int | None): Optional ID of the user performing the deactivation.
            history_comment (str | None): Optional comment explaining the deactivation.
            ignore_permission (bool): If True, bypasses permission checks.

        Returns:
            Self: A new manager instance for the deactivated object.
        """
        if not ignore_permission:
            self.Permission.checkDeletePermission(self, creator_id)
        self._interface.deactivate(
            creator_id=creator_id, history_comment=history_comment
        )
        return self.__class__(**self.identification)

    @classmethod
    def filter(cls, **kwargs: Any) -> Bucket[Self]:
        """
        Return a bucket of managed objects matching the specified filter criteria.

        Parameters:
            kwargs: Field lookups used to filter the managed objects.

        Returns:
            Bucket[Self]: A collection of manager instances matching the filter conditions.
        """
        DependencyTracker.track(
            cls.__name__, "filter", f"{cls.__parse_identification(kwargs)}"
        )
        return cls.Interface.filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Self]:
        """
        Return a bucket of managed objects excluding those that match the specified criteria.

        Parameters:
            kwargs: Field-value pairs used to determine which objects to exclude.

        Returns:
            Bucket[Self]: A collection of managed objects not matching the exclusion criteria.
        """
        DependencyTracker.track(
            cls.__name__, "exclude", f"{cls.__parse_identification(kwargs)}"
        )
        return cls.Interface.exclude(**kwargs)

    @classmethod
    def all(cls) -> Bucket[Self]:
        """
        Return a bucket containing all managed objects of this class.
        """
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
