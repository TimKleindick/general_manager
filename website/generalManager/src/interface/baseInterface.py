from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Type, ClassVar, Callable, TYPE_CHECKING
from datetime import datetime
from generalManager.src.manager.bucket import Bucket

if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.manager.meta import GeneralManagerMeta


class InterfaceBase(ABC):
    _parent_class: ClassVar[Type[Any]]
    _interface_type: ClassVar[str]

    def __init__(self, *args: Any, **kwargs: Any):
        self.identification = self.parseInputFieldsToIdentification(*args, **kwargs)

    @abstractmethod
    def parseInputFieldsToIdentification(
        self, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def getAttributeTypes(cls) -> dict[str, type]:
        raise NotImplementedError

    @abstractmethod
    def getAttributes(cls) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def filter(cls, **kwargs: Any) -> Bucket[Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def exclude(cls, **kwargs: Any) -> Bucket[Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def handleInterface(
        cls,
    ) -> tuple[
        Callable[
            [str, dict[str, Any], Type[InterfaceBase]],
            tuple[dict[str, Any], Type[InterfaceBase], Type[Any]],
        ],
        Callable[
            [
                Type[GeneralManagerMeta],
                Type[GeneralManager],
                Type[InterfaceBase],
                Type[Any],
            ],
            None,
        ],
    ]:
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
