from __future__ import annotations
from abc import ABC, abstractmethod
from typing import (
    Type,
    Generator,
    TYPE_CHECKING,
    Any,
    TypeVar,
    Generic,
    Iterable,
    ClassVar,
    Callable,
    TypedDict,
)
from datetime import datetime
from django.conf import settings
from django.db.models import Model
from general_manager.auxiliary import args_to_kwargs

if TYPE_CHECKING:
    from general_manager.manager.input import Input
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta
    from general_manager.manager.groupManager import GroupedManager, GroupBucket


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
type generalManagerClassName = str
type attributes = dict[str, Any]
type interfaceBaseClass = Type[InterfaceBase]
type newlyCreatedInterfaceClass = Type[InterfaceBase]
type relatedClass = Type[Model] | None
type newlyCreatedGeneralManagerClass = GeneralManagerMeta

type classPreCreationMethod = Callable[
    [generalManagerClassName, attributes, interfaceBaseClass],
    tuple[attributes, interfaceBaseClass, relatedClass],
]

type classPostCreationMethod = Callable[
    [newlyCreatedGeneralManagerClass, newlyCreatedInterfaceClass, relatedClass],
    None,
]


class AttributeTypedDict(TypedDict):
    """
    This class is used to define the type of the attributes dictionary.
    It is used to define the type of the attributes dictionary in the
    GeneralManager class.
    """

    type: type
    default: Any
    is_required: bool
    is_editable: bool


class InterfaceBase(ABC):
    _parent_class: ClassVar[Type[Any]]
    _interface_type: ClassVar[str]
    input_fields: dict[str, Input]

    def __init__(self, *args: Any, **kwargs: Any):
        self.identification = self.parseInputFieldsToIdentification(*args, **kwargs)
        self.formatIdentification()

    def parseInputFieldsToIdentification(
        self,
        *args: Any,
        **kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        identification = {}
        kwargs = args_to_kwargs(args, self.input_fields.keys(), kwargs)
        # Prüfe auf fehlende oder unerwartete Argumente
        extra_args = set(kwargs.keys()) - set(self.input_fields.keys())
        if extra_args:
            for extra_arg in extra_args:
                if extra_arg.replace("_id", "") in self.input_fields.keys():
                    kwargs[extra_arg.replace("_id", "")] = kwargs.pop(extra_arg)
                else:
                    raise TypeError(f"Unexpected arguments: {', '.join(extra_args)}")

        missing_args = set(self.input_fields.keys()) - set(kwargs.keys())
        if missing_args:
            raise TypeError(f"Missing required arguments: {', '.join(missing_args)}")

        # Verarbeite Felder unter Berücksichtigung von Abhängigkeiten
        processed = set()
        while len(processed) < len(self.input_fields):
            progress_made = False
            for name, input_field in self.input_fields.items():
                if name in processed:
                    continue
                depends_on = input_field.depends_on
                if all(dep in processed for dep in depends_on):
                    value = self.input_fields[name].cast(kwargs[name])
                    self._process_input(name, value, identification)
                    identification[name] = value
                    processed.add(name)
                    progress_made = True
            if not progress_made:
                # Zirkuläre Abhängigkeit erkannt
                unresolved = set(self.input_fields.keys()) - processed
                raise ValueError(
                    f"Circular dependency detected among inputs: {', '.join(unresolved)}"
                )
        return identification

    def formatIdentification(self) -> dict[str, Any]:
        from general_manager.manager.generalManager import GeneralManager

        for key, value in self.identification.items():
            if isinstance(value, GeneralManager):
                self.identification[key] = value.identification
            elif isinstance(value, (list, tuple)):
                self.identification[key] = [
                    v.identification if isinstance(v, GeneralManager) else v
                    for v in value
                ]
        return self.identification

    def _process_input(
        self, name: str, value: Any, identification: dict[str, Any]
    ) -> None:
        input_field = self.input_fields[name]
        if not isinstance(value, input_field.type):
            raise TypeError(
                f"Invalid type for {name}: {type(value)}, expected: {input_field.type}"
            )
        if settings.DEBUG:
            # Prüfe mögliche Werte
            possible_values = input_field.possible_values
            if possible_values is not None:
                if callable(possible_values):
                    depends_on = input_field.depends_on
                    dep_values = {
                        dep_name: identification.get(dep_name)
                        for dep_name in depends_on
                    }
                    allowed_values = possible_values(**dep_values)
                elif isinstance(possible_values, Iterable):
                    allowed_values = possible_values
                else:
                    raise TypeError(f"Invalid type for possible_values of input {name}")

                if value not in allowed_values:
                    raise ValueError(
                        f"Invalid value for {name}: {value}, allowed: {allowed_values}"
                    )

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    @abstractmethod
    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
        raise NotImplementedError

    @classmethod
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
        classPreCreationMethod,
        classPostCreationMethod,
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

    @classmethod
    @abstractmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Returns the type of the field with the given name.
        """
        raise NotImplementedError


class Bucket(ABC, Generic[GeneralManagerType]):

    def __init__(self, manager_class: Type[GeneralManagerType]):
        self._manager_class = manager_class
        self._data = None
        self.excludes = {}
        self.filters = {}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __reduce__(self) -> str | tuple[Any, ...]:
        return (
            self.__class__,
            (None, self._manager_class, self.filters, self.excludes),
        )

    @abstractmethod
    def __or__(
        self, other: Bucket[GeneralManagerType] | GeneralManager[GeneralManagerType]
    ) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def __iter__(
        self,
    ) -> Generator[GeneralManagerType | GroupedManager[GeneralManagerType]]:
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: Any) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def first(self) -> GeneralManagerType | GroupedManager[GeneralManagerType] | None:
        raise NotImplementedError

    @abstractmethod
    def last(self) -> GeneralManagerType | GroupedManager[GeneralManagerType] | None:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def get(
        self, **kwargs: Any
    ) -> GeneralManagerType | GroupedManager[GeneralManagerType]:
        raise NotImplementedError

    @abstractmethod
    def __getitem__(
        self, item: int | slice
    ) -> (
        GeneralManagerType
        | GroupedManager[GeneralManagerType]
        | Bucket[GeneralManagerType]
    ):
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: GeneralManagerType) -> bool:
        raise NotImplementedError

    @abstractmethod
    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        raise NotImplementedError

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        This method groups the data by the given arguments.
        It returns a GroupBucket with the grouped data.
        """
        from general_manager.manager.groupManager import GroupBucket

        return GroupBucket(self._manager_class, group_by_keys, self)
