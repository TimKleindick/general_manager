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


class GeneralManager(Generic[GeneralManagerType], metaclass=GeneralManagerMeta):
    Interface: Type[InterfaceBase]
    _attributes: dict[str, Any]

    def __init__(self, *args: Any, **kwargs: Any):
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
        return (self.__class__, tuple(self.__id.values()))

    def __or__(
        self, other: GeneralManager[GeneralManagerType] | Bucket[GeneralManagerType]
    ) -> Bucket[GeneralManagerType]:
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
        creator_id: int,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: dict[str, Any],
    ) -> GeneralManager[GeneralManagerType]:
        Permission: Type[BasePermission] | None = getattr(cls, "Permission", None)
        if Permission is not None and not ignore_permission:
            Permission.checkCreatePermission(kwargs, cls, creator_id)
        identification = cls.Interface.create(
            creator_id=creator_id, history_comment=history_comment, **kwargs
        )
        return cls(identification)

    @dataChange
    def update(
        self,
        creator_id: int,
        history_comment: str | None = None,
        ignore_permission: bool = False,
        **kwargs: dict[str, Any],
    ) -> GeneralManager[GeneralManagerType]:
        Permission: Type[BasePermission] | None = getattr(self, "Permission", None)
        if Permission is not None and not ignore_permission:
            Permission.checkUpdatePermission(kwargs, self, creator_id)
        self._interface.update(
            creator_id=creator_id,
            history_comment=history_comment,
            **kwargs,
        )
        return self.__class__(**self.identification)

    @dataChange
    def deactivate(
        self,
        creator_id: int,
        history_comment: str | None = None,
        ignore_permission: bool = False,
    ) -> GeneralManager[GeneralManagerType]:
        Permission: Type[BasePermission] | None = getattr(self, "Permission", None)
        if Permission is not None and not ignore_permission:
            Permission.checkDeletePermission(self, creator_id)
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
        Processes a dictionary by replacing GeneralManager instances with their identifications.
        
        For each key-value pair, replaces any GeneralManager instance with its identification. Lists and tuples are processed recursively, substituting contained GeneralManager instances with their identifications. Returns None if the resulting dictionary is empty.
        
        Args:
            kwargs: Dictionary to process.
        
        Returns:
            A new dictionary with GeneralManager instances replaced by their identifications, or None if empty.
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
