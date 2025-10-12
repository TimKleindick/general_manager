"""Bucket implementation that enumerates calculation interface combinations."""

from __future__ import annotations
from types import UnionType
from typing import (
    Any,
    Type,
    TYPE_CHECKING,
    Iterable,
    Union,
    Optional,
    Generator,
    List,
    TypedDict,
    get_origin,
    get_args,
)
from operator import attrgetter
from copy import deepcopy
from general_manager.interface.baseInterface import (
    generalManagerClassName,
    GeneralManagerType,
)
from general_manager.bucket.baseBucket import Bucket
from general_manager.manager.input import Input
from general_manager.utils.filterParser import parse_filters

if TYPE_CHECKING:
    from general_manager.api.property import GraphQLProperty


class SortedFilters(TypedDict):
    prop_filters: dict[str, Any]
    input_filters: dict[str, Any]
    prop_excludes: dict[str, Any]
    input_excludes: dict[str, Any]


class CalculationBucket(Bucket[GeneralManagerType]):
    """Bucket that builds cartesian products of calculation input fields."""

    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        filter_definitions: Optional[dict[str, dict]] = None,
        exclude_definitions: Optional[dict[str, dict]] = None,
        sort_key: Optional[Union[str, tuple[str]]] = None,
        reverse: bool = False,
    ) -> None:
        """
        Prepare a calculation bucket that enumerates all input combinations.

        Parameters:
            manager_class (type[GeneralManagerType]): Manager subclass whose interface derives from `CalculationInterface`.
            filter_definitions (dict[str, dict] | None): Optional filter constraints applied to generated combinations.
            exclude_definitions (dict[str, dict] | None): Optional exclude constraints removing combinations.
            sort_key (str | tuple[str, ...] | None): Key(s) used to order generated combinations.
            reverse (bool): When True, reverse the ordering defined by `sort_key`.

        Returns:
            None

        Raises:
            TypeError: If the interface does not inherit from `CalculationInterface`.
        """
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
        self.filter_definitions = (
            {} if filter_definitions is None else filter_definitions
        )
        self.exclude_definitions = (
            {} if exclude_definitions is None else exclude_definitions
        )

        properties = self._manager_class.Interface.getGraphQLProperties()
        possible_values = self.transformPropertiesToInputFields(
            properties, self.input_fields
        )

        self._filters = parse_filters(self.filter_definitions, possible_values)
        self._excludes = parse_filters(self.exclude_definitions, possible_values)

        self._data = None
        self.sort_key = sort_key
        self.reverse = reverse

    def __eq__(self, other: object) -> bool:
        """
        Compare two calculation buckets for structural equality.

        Parameters:
            other (object): Candidate bucket.

        Returns:
            bool: True when both buckets share the same manager class and identical filter/exclude state.
        """
        if not isinstance(other, self.__class__):
            return False
        return (
            self.filter_definitions == other.filter_definitions
            and self.exclude_definitions == other.exclude_definitions
            and self._manager_class == other._manager_class
        )

    def __reduce__(self) -> generalManagerClassName | tuple[Any, ...]:
        """
        Provide pickling support for calculation buckets.

        Returns:
            tuple[Any, ...]: Reconstruction data representing the class, arguments, and state.
        """
        return (
            self.__class__,
            (
                self._manager_class,
                self.filter_definitions,
                self.exclude_definitions,
                self.sort_key,
                self.reverse,
            ),
            {"data": self._data},
        )

    def __setstate__(self, state: dict[str, Any]) -> None:
        """
        Restore the bucket after unpickling.

        Parameters:
            state (dict[str, Any]): Pickled state containing cached combination data.

        Returns:
            None
        """
        self._data = state.get("data")

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Merge two calculation buckets or intersect with a single manager instance.

        Parameters:
            other (Bucket[GeneralManagerType] | GeneralManagerType): Calculation bucket or manager instance to merge.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket reflecting the combined constraints.

        Raises:
            ValueError: If `other` is incompatible or uses a different manager class.
        """
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(self.filter(id__in=[other.identification]))
        if not isinstance(other, self.__class__):
            raise ValueError("Cannot combine different bucket types")
        if self._manager_class != other._manager_class:
            raise ValueError("Cannot combine different manager classes")

        combined_filters = {
            key: value
            for key, value in self.filter_definitions.items()
            if key in other.filter_definitions
            and value == other.filter_definitions[key]
        }

        combined_excludes = {
            key: value
            for key, value in self.exclude_definitions.items()
            if key in other.exclude_definitions
            and value == other.exclude_definitions[key]
        }

        return CalculationBucket(
            self._manager_class,
            combined_filters,
            combined_excludes,
        )

    def __str__(self) -> str:
        """
        Return a compact preview of the generated combinations.

        Returns:
            str: Human-readable summary of up to five combinations.
        """
        PRINT_MAX = 5
        combinations = self.generate_combinations()
        prefix = f"CalculationBucket ({len(combinations)})["
        main = ",".join(
            [
                f"{self._manager_class.__name__}(**{comb})"
                for comb in combinations[:PRINT_MAX]
            ]
        )
        sufix = "]"
        if len(combinations) > PRINT_MAX:
            sufix = ", ...]"

        return f"{prefix}{main}{sufix}"

    def __repr__(self) -> str:
        """
        Return a detailed representation of the bucket configuration.

        Returns:
            str: Debug string listing filters, excludes, sort key, and ordering.
        """
        return f"{self.__class__.__name__}({self._manager_class.__name__}, {self.filter_definitions}, {self.exclude_definitions}, {self.sort_key}, {self.reverse})"

    @staticmethod
    def transformPropertiesToInputFields(
        properties: dict[str, GraphQLProperty], input_fields: dict[str, Input]
    ) -> dict[str, Input]:
        """
        Derive input-field definitions for GraphQL properties without explicit inputs.

        Parameters:
            properties (dict[str, GraphQLProperty]): GraphQL properties declared on the manager.
            input_fields (dict[str, Input]): Existing input field definitions.

        Returns:
            dict[str, Input]: Combined mapping of input field names to `Input` definitions.
        """
        parsed_inputs = {**input_fields}
        for prop_name, prop in properties.items():
            current_hint = prop.graphql_type_hint
            origin = get_origin(current_hint)
            args = list(get_args(current_hint))

            if origin in (Union, UnionType):
                non_none_args = [arg for arg in args if arg is not type(None)]
                current_hint = non_none_args[0] if non_none_args else object
                origin = get_origin(current_hint)
                args = list(get_args(current_hint))

            if origin in (list, tuple, set):
                inner = args[0] if args else object
                resolved_type = inner if isinstance(inner, type) else object
            elif isinstance(current_hint, type):
                resolved_type = current_hint
            else:
                resolved_type = object

            prop_input = Input(
                type=resolved_type, possible_values=None, depends_on=None
            )
            parsed_inputs[prop_name] = prop_input

        return parsed_inputs

    def filter(self, **kwargs: Any) -> CalculationBucket:
        """
        Add additional filters and return a new calculation bucket.

        Parameters:
            **kwargs (Any): Filter expressions applied to generated combinations.

        Returns:
            CalculationBucket: Bucket reflecting the updated filter definitions.
        """
        return CalculationBucket(
            manager_class=self._manager_class,
            filter_definitions={
                **self.filter_definitions.copy(),
                **kwargs,
            },
            exclude_definitions=self.exclude_definitions.copy(),
        )

    def exclude(self, **kwargs: Any) -> CalculationBucket:
        """
        Add additional exclusion rules and return a new calculation bucket.

        Parameters:
            **kwargs (Any): Exclusion expressions removing combinations from the result.

        Returns:
            CalculationBucket: Bucket reflecting the updated exclusion definitions.
        """
        return CalculationBucket(
            manager_class=self._manager_class,
            filter_definitions=self.filter_definitions.copy(),
            exclude_definitions={
                **self.exclude_definitions.copy(),
                **kwargs,
            },
        )

    def all(self) -> CalculationBucket:
        """
        Return a deep copy of this calculation bucket.

        Returns:
            CalculationBucket: Independent copy that can be mutated without affecting the original.
        """
        return deepcopy(self)

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Iterate over every generated combination as a manager instance.

        Yields:
            GeneralManagerType: Manager constructed from each valid set of inputs.
        """
        combinations = self.generate_combinations()
        for combo in combinations:
            yield self._manager_class(**combo)

    def _sortFilters(self, sorted_inputs: List[str]) -> SortedFilters:
        """
        Partition filters into input- and property-based buckets.

        Parameters:
            sorted_inputs (list[str]): Input names ordered by dependency.

        Returns:
            SortedFilters: Mapping that separates filters/excludes for inputs and properties.
        """
        input_filters: dict[str, dict] = {}
        prop_filters: dict[str, dict] = {}
        input_excludes: dict[str, dict] = {}
        prop_excludes: dict[str, dict] = {}

        for filter_name, filter_def in self._filters.items():
            if filter_name in sorted_inputs:
                input_filters[filter_name] = filter_def
            else:
                prop_filters[filter_name] = filter_def
        for exclude_name, exclude_def in self._excludes.items():
            if exclude_name in sorted_inputs:
                input_excludes[exclude_name] = exclude_def
            else:
                prop_excludes[exclude_name] = exclude_def

        return {
            "prop_filters": prop_filters,
            "input_filters": input_filters,
            "prop_excludes": prop_excludes,
            "input_excludes": input_excludes,
        }

    def generate_combinations(self) -> List[dict[str, Any]]:
        """
        Compute (and cache) the list of valid input combinations.

        Returns:
            list[dict[str, Any]]: Cached list of input dictionaries satisfying filters, excludes, and ordering.
        """

        def key_func(manager_obj: GeneralManagerType) -> tuple:
            getters = [attrgetter(key) for key in sort_key]
            return tuple(getter(manager_obj) for getter in getters)

        if self._data is None:
            sorted_inputs = self.topological_sort_inputs()
            sorted_filters = self._sortFilters(sorted_inputs)
            current_combinations = self._generate_input_combinations(
                sorted_inputs,
                sorted_filters["input_filters"],
                sorted_filters["input_excludes"],
            )
            manager_combinations = self._generate_prop_combinations(
                current_combinations,
                sorted_filters["prop_filters"],
                sorted_filters["prop_excludes"],
            )

            if self.sort_key is not None:
                sort_key = self.sort_key
                if isinstance(sort_key, str):
                    sort_key = (sort_key,)
                manager_combinations = sorted(
                    manager_combinations,
                    key=key_func,
                )
            if self.reverse:
                manager_combinations.reverse()
            self._data = [manager.identification for manager in manager_combinations]

        return self._data

    def topological_sort_inputs(self) -> List[str]:
        """
        Produce a dependency-respecting order of input fields.

        Returns:
            list[str]: Input names ordered so each dependency appears before dependants.

        Raises:
            ValueError: If the dependency graph contains a cycle.
        """
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

        def visit(node: str, temp_mark: set[str]) -> None:
            """
            Perform DFS while detecting cycles in the dependency graph.

            Parameters:
                node (str): Input field currently being processed.
                temp_mark (set[str]): Nodes visited along the current path.

            Returns:
                None

            Raises:
                ValueError: If a cyclic dependency involves `node`.
            """
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

        for node in self.input_fields:
            if node not in visited:
                visit(node, set())

        sorted_inputs.reverse()
        return sorted_inputs

    def get_possible_values(
        self, key_name: str, input_field: Input, current_combo: dict
    ) -> Union[Iterable[Any], Bucket[Any]]:
        # Retrieve possible values
        """
        Resolve the potential values for an input field given the current combination.

        Parameters:
            key_name (str): Name of the input field.
            input_field (Input): Input definition describing type and dependencies.
            current_combo (dict): Current partial assignment of input values.

        Returns:
            Iterable[Any] | Bucket[Any]: Collection of permissible values.

        Raises:
            TypeError: If the configured `possible_values` cannot be evaluated.
        """
        if callable(input_field.possible_values):
            depends_on = input_field.depends_on
            dep_values = [current_combo[dep_name] for dep_name in depends_on]
            possible_values = input_field.possible_values(*dep_values)
        elif isinstance(input_field.possible_values, (Iterable, Bucket)):
            possible_values = input_field.possible_values
        else:
            raise TypeError(f"Invalid possible_values for input '{key_name}'")
        return possible_values

    def _generate_input_combinations(
        self,
        sorted_inputs: List[str],
        filters: dict[str, dict],
        excludes: dict[str, dict],
    ) -> List[dict[str, Any]]:
        """
        Generate all valid input combinations while honouring filters and excludes.

        Parameters:
            sorted_inputs (list[str]): Input names ordered by dependency.
            filters (dict[str, dict]): Filter definitions keyed by input name.
            excludes (dict[str, dict]): Exclusion definitions keyed by input name.

        Returns:
            list[dict[str, Any]]: Valid input combinations.
        """

        def helper(
            index: int,
            current_combo: dict[str, Any],
        ) -> Generator[dict[str, Any], None, None]:
            """
            Recursively emit input combinations that satisfy filters and excludes.

            Parameters:
                index (int): Position within `sorted_inputs` currently being assigned.
                current_combo (dict[str, Any]): Partial assignment of inputs built so far.

            Yields:
                dict[str, Any]: Completed combination of input values.
            """
            if index == len(sorted_inputs):
                yield current_combo.copy()
                return
            input_name: str = sorted_inputs[index]
            input_field = self.input_fields[input_name]

            possible_values = self.get_possible_values(
                input_name, input_field, current_combo
            )

            field_filters = filters.get(input_name, {})
            field_excludes = excludes.get(input_name, {})

            # use filter_funcs and exclude_funcs to filter possible values
            if isinstance(possible_values, Bucket):
                filter_kwargs = field_filters.get("filter_kwargs", {})
                exclude_kwargs = field_excludes.get("filter_kwargs", {})
                possible_values = possible_values.filter(**filter_kwargs).exclude(
                    **exclude_kwargs
                )
            else:
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

    def _generate_prop_combinations(
        self,
        current_combos: list[dict[str, Any]],
        prop_filters: dict[str, Any],
        prop_excludes: dict[str, Any],
    ) -> list[GeneralManagerType]:
        """
        Apply property-level filters and excludes to manager combinations.

        Parameters:
            current_combos (list[dict[str, Any]]): Input combinations already passing input filters.
            prop_filters (dict[str, Any]): Filter definitions keyed by property name.
            prop_excludes (dict[str, Any]): Exclude definitions keyed by property name.

        Returns:
            list[GeneralManagerType]: Manager instances that satisfy property constraints.
        """

        prop_filter_needed = set(prop_filters.keys()) | set(prop_excludes.keys())
        manager_combinations = [
            self._manager_class(**combo) for combo in current_combos
        ]
        if not prop_filter_needed:
            return manager_combinations

        # Apply property filters and exclusions
        filtered_combos = []
        for manager in manager_combinations:
            keep = True
            # include filters
            for prop_name, defs in prop_filters.items():
                for func in defs.get("filter_funcs", []):
                    if not func(getattr(manager, prop_name)):
                        keep = False
                        break
                if not keep:
                    break
            # excludes
            if keep:
                for prop_name, defs in prop_excludes.items():
                    for func in defs.get("filter_funcs", []):
                        if func(getattr(manager, prop_name)):
                            keep = False
                            break
                    if not keep:
                        break
            if keep:
                filtered_combos.append(manager)
        return filtered_combos

    def first(self) -> GeneralManagerType | None:
        """
        Return the first generated manager instance.

        Returns:
            GeneralManagerType | None: First instance or None when no combinations exist.
        """
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GeneralManagerType | None:
        """
        Return the last generated manager instance.

        Returns:
            GeneralManagerType | None: Last instance or None when no combinations exist.
        """
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        """
        Return the number of calculation combinations.

        Returns:
            int: Number of generated combinations.
        """
        return self.__len__()

    def __len__(self) -> int:
        """
        Return the number of generated combinations.

        Returns:
            int: Cached number of combinations.
        """
        return len(self.generate_combinations())

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | CalculationBucket[GeneralManagerType]:
        """
        Retrieve a manager instance or subset of combinations.

        Parameters:
            item (int | slice): Index or slice specifying which combinations to return.

        Returns:
            GeneralManagerType | CalculationBucket[GeneralManagerType]:
                Manager instance for single indices or bucket wrapping the sliced combinations.
        """
        items = self.generate_combinations()
        result = items[item]
        if isinstance(result, list):
            new_bucket = CalculationBucket(
                self._manager_class,
                self.filter_definitions.copy(),
                self.exclude_definitions.copy(),
                self.sort_key,
                self.reverse,
            )
            new_bucket._data = result
            return new_bucket
        return self._manager_class(**result)

    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Determine whether the provided manager instance exists among generated combinations.

        Parameters:
            item (GeneralManagerType): Manager instance to test for membership.

        Returns:
            bool: True when the instance matches one of the generated combinations.
        """
        return any(item == mgr for mgr in self)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        """
        Retrieve a single manager instance that satisfies the given filters.

        Parameters:
            **kwargs (Any): Filter expressions narrowing the calculation results.

        Returns:
            GeneralManagerType: Matching manager instance.

        Raises:
            ValueError: If zero or multiple calculations match the filters.
        """
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
        """
        Return a new bucket with updated sorting preferences.

        Parameters:
            key (str | tuple[str, ...]): Attribute name(s) used for ordering combinations.
            reverse (bool): Whether to apply descending order.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket configured with the provided sorting options.
        """
        return CalculationBucket(
            self._manager_class,
            self.filter_definitions,
            self.exclude_definitions,
            key,
            reverse,
        )

    def none(self) -> CalculationBucket[GeneralManagerType]:
        """
        Return an empty calculation bucket with the same configuration.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket with no combinations and cleared cached data.
        """
        own = self.all()
        own._data = []
        own.filters = {}
        own.excludes = {}
        return own
