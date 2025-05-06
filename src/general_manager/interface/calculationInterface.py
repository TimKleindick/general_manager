from __future__ import annotations
from datetime import datetime
from typing import (
    Any,
    Type,
    TYPE_CHECKING,
    Callable,
    Iterable,
    Union,
    Optional,
    Generator,
    List,
)
from general_manager.interface.baseInterface import (
    InterfaceBase,
    Bucket,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    attributes,
    interfaceBaseClass,
    newlyCreatedGeneralManagerClass,
    newlyCreatedInterfaceClass,
    relatedClass,
    GeneralManagerType,
    AttributeTypedDict,
)
from general_manager.manager.input import Input
from general_manager.auxiliary.filterParser import parse_filters

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta


class CalculationInterface(InterfaceBase):
    _interface_type = "calculation"
    input_fields: dict[str, Input]

    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError("Calculations do not store data.")

    @classmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
        return {
            name: {
                "type": field.type,
                "default": None,
                "is_editable": False,
                "is_required": True,
            }
            for name, field in cls.input_fields.items()
        }

    @classmethod
    def getAttributes(cls) -> dict[str, Any]:
        return {
            name: lambda self, name=name: cls.input_fields[name].cast(
                self.identification.get(name)
            )
            for name in cls.input_fields.keys()
        }

    @classmethod
    def filter(cls, **kwargs: Any) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).filter(**kwargs)

    @classmethod
    def exclude(cls, **kwargs: Any) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).exclude(**kwargs)

    @classmethod
    def all(cls) -> CalculationBucket:
        return CalculationBucket(cls._parent_class).all()

    @staticmethod
    def _preCreate(
        name: generalManagerClassName, attrs: attributes, interface: interfaceBaseClass
    ) -> tuple[attributes, interfaceBaseClass, None]:
        # Felder aus der Interface-Klasse sammeln
        input_fields: dict[str, Input[Any]] = {}
        for key, value in vars(interface).items():
            if key.startswith("__"):
                continue
            if isinstance(value, Input):
                input_fields[key] = value

        # Interface-Typ bestimmen
        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(
            interface.__name__, (interface,), {"input_fields": input_fields}
        )
        attrs["Interface"] = interface_cls

        return attrs, interface_cls, None

    @staticmethod
    def _postCreate(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        interface_class._parent_class = new_class

    @classmethod
    def handleInterface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        return cls._preCreate, cls._postCreate

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        This method returns the field type for the given field name.
        """
        input = cls.input_fields.get(field_name)
        if input is None:
            raise ValueError(f"Field '{field_name}' not found in input fields.")
        return input.type


class CalculationBucket(Bucket[GeneralManagerType]):
    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        filter_definitions: Optional[dict[str, dict]] = None,
        exclude_definitions: Optional[dict[str, dict]] = None,
        sort_key: Optional[Union[str, tuple[str]]] = None,
        reverse: bool = False,
    ):
        from general_manager.interface.calculationInterface import (
            CalculationInterface,
        )

        super().__init__(manager_class)

        interface_class = manager_class.Interface
        if not issubclass(interface_class, CalculationInterface):
            raise TypeError(
                "CalculationBucket can only be used with CalculationInterface subclasses"
            )
        self.input_fields = interface_class.input_fields
        self.filters = {} if filter_definitions is None else filter_definitions
        self.excludes = {} if exclude_definitions is None else exclude_definitions
        self.__current_combinations = None
        self.sort_key = sort_key
        self.reverse = reverse

    def __reduce__(self) -> generalManagerClassName | tuple[Any, ...]:
        return (
            self.__class__,
            (
                self._manager_class,
                self.filters,
                self.excludes,
                self.sort_key,
                self.reverse,
            ),
        )

    def __or__(
        self, other: Bucket[GeneralManagerType] | GeneralManager[GeneralManagerType]
    ) -> CalculationBucket[GeneralManagerType]:
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(self.filter(id__in=[other.identification]))
        if not isinstance(other, self.__class__):
            raise ValueError("Cannot combine different bucket types")
        if self._manager_class != other._manager_class:
            raise ValueError("Cannot combine different manager classes")

        combined_filters = {
            key: value
            for key, value in self.filters.items()
            if key in other.filters and value == other.filters[key]
        }

        combined_excludes = {
            key: value
            for key, value in self.excludes.items()
            if key in other.excludes and value == other.excludes[key]
        }

        return CalculationBucket(
            self._manager_class,
            combined_filters,
            combined_excludes,
        )

    def __str__(self) -> str:
        PRINT_MAX = 5
        combinations = self.generate_combinations()
        prefix = f"CalculationBucket ({len(combinations)})["
        main = ",".join(
            [
                f"{self._manager_class.__name__}(**{comb})"
                for comb in combinations[:PRINT_MAX]
            ]
        )
        sufix = f"]"
        if len(combinations) > PRINT_MAX:
            sufix = f", ...]"

        return f"{prefix}{main}{sufix} "

    def __repr__(self) -> str:
        return self.__str__()

    def filter(self, **kwargs: Any) -> CalculationBucket:
        filters = self.filters.copy()
        excludes = self.excludes.copy()
        filters.update(parse_filters(kwargs, self.input_fields))
        return CalculationBucket(self._manager_class, filters, excludes)

    def exclude(self, **kwargs: Any) -> CalculationBucket:
        filters = self.filters.copy()
        excludes = self.excludes.copy()
        excludes.update(parse_filters(kwargs, self.input_fields))
        return CalculationBucket(self._manager_class, filters, excludes)

    def all(self) -> CalculationBucket:
        return self

    def __iter__(self) -> Generator[GeneralManagerType]:
        combinations = self.generate_combinations()
        for combo in combinations:
            yield self._manager_class(**combo)

    def generate_combinations(self) -> List[dict[str, Any]]:
        if self.__current_combinations is None:
            # Implementierung ähnlich wie im InputManager
            sorted_inputs = self.topological_sort_inputs()
            current_combinations = self._generate_combinations(
                sorted_inputs, self.filters, self.excludes
            )
            if self.sort_key is not None:
                sort_key = self.sort_key
                if isinstance(sort_key, str):
                    sort_key = (sort_key,)
                key_func = lambda x: (tuple(x[key] for key in sort_key))
                current_combinations = sorted(
                    current_combinations,
                    key=key_func,
                )
            if self.reverse:
                current_combinations.reverse()
            self.__current_combinations = current_combinations

        return self.__current_combinations

    def topological_sort_inputs(self) -> List[str]:
        from collections import defaultdict

        dependencies = {
            name: field.depends_on for name, field in self.input_fields.items()
        }
        graph = defaultdict(set)
        for key, deps in dependencies.items():
            for dep in deps:
                graph[dep].add(key)

        visited = set()
        sorted_inputs = []

        def visit(node, temp_mark):
            if node in visited:
                return
            if node in temp_mark:
                raise ValueError(f"Cyclic dependency detected: {node}")
            temp_mark.add(node)
            for m in graph.get(node, []):
                visit(m, temp_mark)
            temp_mark.remove(node)
            visited.add(node)
            sorted_inputs.append(node)

        for node in self.input_fields.keys():
            if node not in visited:
                visit(node, set())

        sorted_inputs.reverse()
        return sorted_inputs

    def get_possible_values(
        self, key_name: str, input_field: Input, current_combo: dict
    ) -> Union[Iterable[Any], Bucket[Any]]:
        # Hole mögliche Werte
        if callable(input_field.possible_values):
            depends_on = input_field.depends_on
            dep_values = {dep_name: current_combo[dep_name] for dep_name in depends_on}
            possible_values = input_field.possible_values(**dep_values)
        elif isinstance(input_field.possible_values, (Iterable, Bucket)):
            possible_values = input_field.possible_values
        else:
            raise TypeError(f"Invalid possible_values for input '{key_name}'")
        return possible_values

    def _generate_combinations(
        self,
        sorted_inputs: List[str],
        filters: dict[str, dict],
        excludes: dict[str, dict],
    ) -> List[dict[str, Any]]:
        def helper(index, current_combo):
            if index == len(sorted_inputs):
                yield current_combo.copy()
                return
            input_name: str = sorted_inputs[index]
            input_field = self.input_fields[input_name]

            # Hole mögliche Werte
            possible_values = self.get_possible_values(
                input_name, input_field, current_combo
            )

            # Wende die Filter an
            field_filters = filters.get(input_name, {})
            field_excludes = excludes.get(input_name, {})

            if isinstance(possible_values, Bucket):
                # Wende die Filter- und Exklusionsargumente direkt an
                filter_kwargs = field_filters.get("filter_kwargs", {})
                exclude_kwargs = field_excludes.get("filter_kwargs", {})
                possible_values = possible_values.filter(**filter_kwargs).exclude(
                    **exclude_kwargs
                )
            else:
                # Wende die Filterfunktionen an
                filter_funcs = field_filters.get("filter_funcs", [])
                for filter_func in filter_funcs:
                    possible_values = filter(filter_func, possible_values)

                exclude_funcs = field_excludes.get("filter_funcs", [])
                for exclude_func in exclude_funcs:
                    possible_values = filter(
                        lambda x: not exclude_func(x), possible_values
                    )

                possible_values = list(possible_values)

            for value in possible_values:
                if not isinstance(value, input_field.type):
                    continue
                current_combo[input_name] = value
                yield from helper(index + 1, current_combo)
                del current_combo[input_name]

        return list(helper(0, {}))

    def first(self) -> GeneralManagerType | None:
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GeneralManagerType | None:
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        return sum(1 for _ in self)

    def __len__(self) -> int:
        return self.count()

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | CalculationBucket[GeneralManagerType]:
        items = self.generate_combinations()
        result = items[item]
        if isinstance(result, list):
            new_bucket = CalculationBucket(self._manager_class)
            new_bucket.filters = self.filters.copy()
            new_bucket.excludes = self.excludes.copy()
            return new_bucket
        return self._manager_class(**result)

    def __contains__(self, item: GeneralManagerType) -> bool:
        return item in list(self)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        filtered_bucket = self.filter(**kwargs)
        items = list(filtered_bucket)
        if len(items) == 1:
            return items[0]
        elif len(items) == 0:
            raise ValueError("No matching calculation found.")
        else:
            raise ValueError("Multiple matching calculations found.")

    def sort(
        self, key: str | tuple[str], reverse: bool = False
    ) -> CalculationBucket[GeneralManagerType]:
        return CalculationBucket(
            self._manager_class, self.filters, self.excludes, key, reverse
        )
