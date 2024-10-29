from __future__ import annotations
from typing import Type
from generalManager.src.manager.meta import GeneralManagerMeta
from generalManager.src.manager.interface import InterfaceBase
from generalManager.src.manager.bucket import Bucket


class GeneralManager(metaclass=GeneralManagerMeta):
    Interface: Type[InterfaceBase]

    def __init__(self, id, *args, **kwargs):
        self.__interface = self.Interface(pk=id)
        self.__id = id
        self.__attributes = self.__interface.getAttributes()
        self.__createAtPropertiesForAttributes()

    def __str__(self):
        return f"{self.__class__.__name__}({self.__id})"

    def __repr__(self):
        return f"{self.__class__.__name__}({self.__id})"

    @property
    def id(self):
        return self.__id

    def __iter__(self):
        for key, value in self.__attributes.items():
            if callable(value):
                yield key, value()
                continue
            yield key, value

    def __createAtPropertiesForAttributes(self):

        def propertyMethod(attr_name):
            def getter(self):
                attribute = self.__attributes[attr_name]
                if callable(attribute):
                    return attribute()
                return attribute

            return property(getter)

        for attr_name in self.__attributes.keys():
            setattr(self.__class__, attr_name, propertyMethod(attr_name))

    @classmethod
    def create(
        cls, creator_id: int, history_comment: str | None = None, **kwargs
    ) -> GeneralManager:
        pk = cls.Interface.create(
            creator_id=creator_id, history_comment=history_comment, **kwargs
        )
        return cls(pk)

    def update(
        self, creator_id: int, history_comment: str | None = None, **kwargs
    ) -> GeneralManager:
        self.__interface.update(
            creator_id=creator_id,
            history_comment=history_comment,
            **kwargs,
        )
        return self.__class__(self.__id)

    def deactivate(
        self, creator_id: int, history_comment: str | None = None
    ) -> GeneralManager:
        self.__interface.deactivate(
            creator_id=creator_id, history_comment=history_comment
        )
        return self.__class__(self.__id)

    @classmethod
    def filter(cls, **kwargs) -> Bucket:
        return cls.Interface.filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs) -> Bucket:
        return cls.Interface.exclude(**kwargs)

    @classmethod
    def all(cls) -> Bucket:
        return cls.Interface.filter()
