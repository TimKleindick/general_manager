from __future__ import annotations
from typing import Generic, Type, Any
from generalManager.src.manager.meta import GeneralManagerMeta
from generalManager.src.interface.baseInterface import (
    InterfaceBase,
    Bucket,
    GeneralManagerType,
)
from generalManager.src.api.property import GraphQLProperty


class GeneralManager(Generic[GeneralManagerType], metaclass=GeneralManagerMeta):
    Interface: Type[InterfaceBase]
    _attributes: dict[str, Any]

    def __init__(self, *args: Any, **kwargs: Any):
        self._interface = self.Interface(*args, **kwargs)
        self.__id: dict[str, Any] = self._interface.identification

    def __str__(self):
        return f"{self.__class__.__name__}({self.__id})"

    def __repr__(self):
        return f"{self.__class__.__name__}({self.__id})"

    def __or__(
        self, other: GeneralManager[GeneralManagerType] | Bucket[GeneralManagerType]
    ) -> Bucket[GeneralManagerType]:
        if isinstance(other, Bucket):
            return other | self
        elif isinstance(other, GeneralManager) and other.__class__ == self.__class__:
            return self.filter(id__in=[self.id, other.id])
        else:
            raise TypeError(f"Unsupported type for union: {type(other)}")

    @property
    def id(self):
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
    def create(
        cls, creator_id: int, history_comment: str | None = None, **kwargs: Any
    ) -> GeneralManager:
        identification = cls.Interface.create(
            creator_id=creator_id, history_comment=history_comment, **kwargs
        )
        return cls(identification)

    def update(
        self, creator_id: int, history_comment: str | None = None, **kwargs: Any
    ) -> GeneralManager:
        self._interface.update(
            creator_id=creator_id,
            history_comment=history_comment,
            **kwargs,
        )
        return self.__class__(self.__id)

    def deactivate(
        self, creator_id: int, history_comment: str | None = None
    ) -> GeneralManager:
        self._interface.deactivate(
            creator_id=creator_id, history_comment=history_comment
        )
        return self.__class__(self.__id)

    @classmethod
    def filter(cls, **kwargs: Any) -> Bucket[GeneralManagerType]:
        return cls.Interface.filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> Bucket[GeneralManagerType]:
        return cls.Interface.exclude(**kwargs)

    @classmethod
    def all(cls) -> Bucket[GeneralManagerType]:
        return cls.Interface.filter()
