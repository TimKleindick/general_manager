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
from generalManager.src.interface.baseInterface import InterfaceBase
from generalManager.src.manager.bucket import Bucket
from generalManager.src.manager.input import Input

if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.manager.meta import GeneralManagerMeta


class CalculationInterface(InterfaceBase):
    _interface_type = "calculation"
    input_fields: dict[str, Input]

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Calculations are generated, not created directly.")

    def update(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Calculations cannot be updated.")

    def deactivate(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("Calculations cannot be deactivated.")

    def getData(self, search_date: datetime | None = None) -> Any:
        raise NotImplementedError("Calculations do not store data.")

    @classmethod
    def getAttributeTypes(cls) -> dict[str, type]:
        return {name: field.type for name, field in cls.input_fields.items()}

    @classmethod
    def getAttributes(cls) -> dict[str, Any]:
        return {
            name: lambda self, name=name: self.identification.get(name)
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
        name: str, attrs: dict[str, Any], interface: Type[CalculationInterface]
    ) -> tuple[dict[str, Any], Type[CalculationInterface], None]:
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
        mcs: Type[GeneralManagerMeta],
        new_class: Type[GeneralManager],
        interface_class: Type[CalculationInterface],
        model: None,
    ) -> None:
        interface_class._parent_class = new_class

    @classmethod
    def handleInterface(cls) -> tuple[  # type: ignore
        Callable[
            [str, dict[str, Any], Type[CalculationInterface]],
            tuple[dict[str, Any], Type[CalculationInterface], None],
        ],
        Callable[
            [
                Type[GeneralManagerMeta],
                Type[GeneralManager],
                Type[CalculationInterface],
                None,
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
        return cls._preCreate, cls._postCreate


class CalculationBucket(Bucket["GeneralManager"]):
    def __init__(
        self,
        manager_class: Type[GeneralManager],
        filter_definitions: Optional[dict[str, dict]] = None,
        exclude_definitions: Optional[dict[str, dict]] = None,
    ):
        from generalManager.src.interface.calculationInterface import (
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

    def __str__(self) -> str:
        PRINT_MAX = 5
        combinations = self.generate_combinations()
        prefix = f"CalculationBucket ({len(combinations)})["
        main = ""
        sufix = f"]"
        if len(combinations) > PRINT_MAX:
            sufix = f", ...]"

        return f"{prefix}{",".join([f"{self._manager_class.__name__}(**{comb})" for comb in combinations[:PRINT_MAX] ]) }{sufix} "

    def filter(self, **kwargs: Any) -> CalculationBucket:
        filters = self.filters.copy()
        excludes = self.excludes.copy()
        filters.update(self.parse_filters(kwargs))
        return CalculationBucket(self._manager_class, filters, excludes)

    def exclude(self, **kwargs: Any) -> CalculationBucket:
        filters = self.filters.copy()
        excludes = self.excludes.copy()
        excludes.update(self.parse_filters(kwargs))
        return CalculationBucket(self._manager_class, filters, excludes)

    def all(self) -> CalculationBucket:
        return self

    def __iter__(self) -> Generator[GeneralManager, None, None]:
        combinations = self.generate_combinations()
        for combo in combinations:
            yield self._manager_class(**combo)

    def generate_combinations(self) -> List[dict[str, Any]]:
        if self.__current_combinations is None:
            # Implementierung ähnlich wie im InputManager
            sorted_inputs = self.topological_sort_inputs()
            self.__current_combinations = self._generate_combinations(
                sorted_inputs, self.filters, self.excludes
            )
        return self.__current_combinations

    def parse_filters(self, filter_kwargs: dict[str, Any]) -> dict[str, dict]:
        from generalManager.src.manager.generalManager import GeneralManager

        filters = {}
        for kwarg, value in filter_kwargs.items():
            parts = kwarg.split("__")
            field_name = parts[0]
            if field_name not in self.input_fields:
                raise ValueError(f"Unknown input field '{field_name}' in filter")
            input_field = self.input_fields[field_name]

            lookup = "__".join(parts[1:]) if len(parts) > 1 else ""

            if issubclass(input_field.type, GeneralManager):
                # Sammle die Filter-Keyword-Argumente für das InputField
                if lookup == "":
                    lookup = "id"
                    value = value.id
                filters.setdefault(field_name, {}).setdefault("filter_kwargs", {})[
                    lookup
                ] = value
            else:
                # Erstelle Filterfunktionen für Nicht-Bucket-Typen
                filter_func = self.create_filter_function(lookup, value)
                filters.setdefault(field_name, {}).setdefault(
                    "filter_funcs", []
                ).append(filter_func)
        return filters

    def create_filter_function(
        self, lookup_str: str, value: Any
    ) -> Callable[[Any], bool]:
        parts = lookup_str.split("__") if lookup_str else []
        if parts and parts[-1] in [
            "exact",
            "lt",
            "lte",
            "gt",
            "gte",
            "contains",
            "startswith",
            "endswith",
        ]:
            lookup = parts[-1]
            attr_path = parts[:-1]
        else:
            lookup = "exact"
            attr_path = parts

        def filter_func(x):
            for attr in attr_path:
                if hasattr(x, attr):
                    x = getattr(x, attr)
                else:
                    return False
            return self.apply_lookup(x, lookup, value)

        return filter_func

    def apply_lookup(self, x: Any, lookup: str, value: Any) -> bool:
        try:
            if lookup == "exact":
                return x == value
            elif lookup == "lt":
                return x < value
            elif lookup == "lte":
                return x <= value
            elif lookup == "gt":
                return x > value
            elif lookup == "gte":
                return x >= value
            elif lookup == "contains" and isinstance(x, str):
                return value in x
            elif lookup == "startswith" and isinstance(x, str):
                return x.startswith(value)
            elif lookup == "endswith" and isinstance(x, str):
                return x.endswith(value)
            else:
                return False
        except TypeError:
            return False

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

    def first(self) -> GeneralManager | None:
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GeneralManager | None:
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        return sum(1 for _ in self)

    def __len__(self) -> int:
        return self.count()

    def __getitem__(self, item: int | slice) -> GeneralManager | CalculationBucket:
        items = self.generate_combinations()
        result = items[item]
        if isinstance(result, list):
            new_bucket = CalculationBucket(self._manager_class)
            new_bucket.filters = self.filters.copy()
            new_bucket.excludes = self.excludes.copy()
            return new_bucket
        return self._manager_class(**result)

    def __contains__(self, item: GeneralManager) -> bool:
        return item in list(self)

    def get(self, **kwargs: Any) -> GeneralManager:
        filtered_bucket = self.filter(**kwargs)
        items = list(filtered_bucket)
        if len(items) == 1:
            return items[0]
        elif len(items) == 0:
            raise ValueError("No matching calculation found.")
        else:
            raise ValueError("Multiple matching calculations found.")
