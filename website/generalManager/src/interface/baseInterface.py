from abc import ABC, abstractmethod
from typing import Any, Type, ClassVar, Callable
from datetime import datetime
from generalManager.src.manager.bucket import Bucket


class InterfaceBase(ABC):
    _parent_class: ClassVar[Type]
    _interface_type: ClassVar[str]

    def __init__(self, pk: Any, *args, **kwargs):
        self.pk = pk

    @classmethod
    @abstractmethod
    def create(cls, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def update(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def deactivate(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def getData(self, search_date: datetime | None = None):
        raise NotImplementedError

    @abstractmethod
    def getAttributeTypes(self) -> dict[str, type]:
        raise NotImplementedError

    @abstractmethod
    def getAttributes(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def filter(cls, **kwargs) -> Bucket:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def exclude(cls, **kwargs) -> Bucket:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def handleInterface(cls) -> tuple[Callable, Callable]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        raise NotImplementedError
