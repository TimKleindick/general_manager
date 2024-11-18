from __future__ import annotations
from abc import ABC, abstractmethod
from django.db import models
from typing import (
    Type,
    Generator,
    TYPE_CHECKING,
    Any,
    TypeVar,
    Generic,
    List,
    Callable,
    Iterable,
    Optional,
    Union,
)


if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.calculation.input import Input


T = TypeVar("T")


class Bucket(ABC, Generic[T]):

    def __init__(self, manager_class: Type[GeneralManager]):
        self._manager_class = manager_class
        self._data = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __iter__(self) -> Generator[GeneralManager]:
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: Any) -> Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: Any) -> Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def first(self) -> T | None:
        raise NotImplementedError

    @abstractmethod
    def last(self) -> T | None:
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def get(self, **kwargs: Any) -> T:
        raise NotImplementedError

    @abstractmethod
    def __getitem__(self, item: int | slice) -> T | Bucket[T]:
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: T) -> bool:
        raise NotImplementedError


T1 = TypeVar("T1", bound=models.Model)


class DatabaseBucket(Bucket["GeneralManager"]):

    def __init__(
        self,
        data: models.QuerySet[T1],
        manager_class: Type[GeneralManager],
        filter_definitions: dict[str, list[Any]] = {},
    ):
        self._data = data
        self._manager_class = manager_class
        self._filter_definitions = {**filter_definitions}

    def __iter__(self) -> Generator[GeneralManager]:
        for item in self._data:
            yield self._manager_class(item.pk)

    def __mergeFilterDefinitions(self, **kwargs: Any) -> dict[str, list[Any]]:
        kwarg_filter: dict[str, list[Any]] = {}
        for key, value in self._filter_definitions.items():
            kwarg_filter[key] = value
        for key, value in kwargs.items():
            if key not in kwarg_filter:
                kwarg_filter[key] = []
            kwarg_filter[key].append(value)
        return kwarg_filter

    def filter(self, **kwargs: Any) -> DatabaseBucket:
        merged_filter = self.__mergeFilterDefinitions(**kwargs)
        return self.__class__(
            self._data.filter(**kwargs), self._manager_class, merged_filter
        )

    def exclude(self, **kwargs: Any) -> DatabaseBucket:
        merged_filter = self.__mergeFilterDefinitions(**kwargs)
        return self.__class__(
            self._data.exclude(**kwargs), self._manager_class, merged_filter
        )

    def first(self) -> GeneralManager | None:
        first_element = self._data.first()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def last(self) -> GeneralManager | None:
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def count(self) -> int:
        return self._data.count()

    def all(self) -> DatabaseBucket:
        return self.__class__(self._data.all(), self._manager_class)

    def get(self, **kwargs: Any) -> GeneralManager:
        element = self._data.get(**kwargs)
        return self._manager_class(element.pk)

    def __getitem__(self, item: int | slice) -> GeneralManager | DatabaseBucket:
        if isinstance(item, slice):
            return self.__class__(self._data[item], self._manager_class)
        return self._manager_class(self._data[item].pk)

    def __len__(self) -> int:
        return self._data.count()

    def __repr__(self) -> str:
        return f"{self._manager_class.__name__}Bucket ({self._data})"

    def __contains__(self, item: GeneralManager | models.Model) -> bool:
        from generalManager.src.manager.generalManager import GeneralManager

        if isinstance(item, GeneralManager):
            return item.id in self._data.values_list("pk", flat=True)
        return item in self._data


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
        # Implementierung ähnlich wie im InputManager
        sorted_inputs = self.topological_sort_inputs()
        combinations = self._generate_combinations(
            sorted_inputs, self.filters, self.excludes
        )
        return combinations

    def parse_filters(self, filter_kwargs: dict[str, Any]) -> dict[str, dict]:
        filters = {}
        for kwarg, value in filter_kwargs.items():
            parts = kwarg.split("__")
            field_name = parts[0]
            if field_name not in self.input_fields:
                raise ValueError(f"Unknown input field '{field_name}' in filter")
            input_field = self.input_fields[field_name]

            lookup = "__".join(parts[1:]) if len(parts) > 1 else ""

            if isinstance(input_field.possible_values, Bucket):
                # Sammle die Filter-Keyword-Argumente für das InputField
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

            # Konvertiere mögliche Werte in eine Liste
            if isinstance(possible_values, Bucket):
                possible_values = list(possible_values)
            else:
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
        return len(list(self))

    def __len__(self) -> int:
        return self.count()

    def __getitem__(self, item: int | slice) -> GeneralManager | CalculationBucket:
        items = list(self)
        result = items[item]
        if isinstance(result, list):
            new_bucket = CalculationBucket(self._manager_class)
            new_bucket.filters = self.filters.copy()
            new_bucket.excludes = self.excludes.copy()
            return new_bucket
        return result

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
