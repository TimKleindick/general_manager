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
from general_manager.interface.base_interface import (
    generalManagerClassName,
    GeneralManagerType,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.input import Input
from general_manager.utils.filter_parser import parse_filters

if TYPE_CHECKING:
    from general_manager.api.property import GraphQLProperty


class SortedFilters(TypedDict):
    prop_filters: dict[str, Any]
    input_filters: dict[str, Any]
    prop_excludes: dict[str, Any]
    input_excludes: dict[str, Any]


class InvalidCalculationInterfaceError(TypeError):
    """Raised when a CalculationBucket is initialized with a non-CalculationInterface manager."""

    def __init__(self) -> None:
        """
        Indicates a manager's interface does not inherit from CalculationInterface.

        Initializes the exception with the message "CalculationBucket requires a manager whose interface inherits from CalculationInterface."
        """
        super().__init__(
            "CalculationBucket requires a manager whose interface inherits from CalculationInterface."
        )


class IncompatibleBucketTypeError(TypeError):
    """Raised when attempting to combine buckets of different types."""

    def __init__(self, bucket_type: type, other_type: type) -> None:
        """
        Initialize the error indicating two bucket types cannot be combined.

        Parameters:
            bucket_type (type): The first bucket class involved in the attempted combination.
            other_type (type): The second bucket class involved in the attempted combination.

        Notes:
            The exception message is formatted as "Cannot combine {bucket_type.__name__} with {other_type.__name__}."
        """
        super().__init__(
            f"Cannot combine {bucket_type.__name__} with {other_type.__name__}."
        )


class IncompatibleBucketManagerError(TypeError):
    """Raised when attempting to combine buckets with different manager classes."""

    def __init__(self, first_manager: type, second_manager: type) -> None:
        """
        Indicate that two buckets for different manager classes cannot be combined.

        Parameters:
            first_manager (type): The first manager class involved in the attempted combination.
            second_manager (type): The second manager class involved in the attempted combination.

        Description:
            The exception message will include the class names of both managers.
        """
        super().__init__(
            f"Cannot combine buckets for {first_manager.__name__} and {second_manager.__name__}."
        )


class CyclicDependencyError(ValueError):
    """Raised when a cyclic dependency is detected in calculation sorting."""

    def __init__(self, node: str) -> None:
        """
        Initialize the CyclicDependencyError for a specific node involved in a dependency cycle.

        Parameters:
            node (str): The identifier of the node where a cycle was detected. The exception message will include this node, e.g. "Cyclic dependency detected: {node}."
        """
        super().__init__(f"Cyclic dependency detected: {node}.")


class InvalidPossibleValuesError(TypeError):
    """Raised when an input field provides invalid possible value definitions."""

    def __init__(self, key_name: str) -> None:
        """
        Indicate that an input field defines an invalid `possible_values` configuration.

        Parameters:
            key_name (str): Name of the input field whose `possible_values` configuration is invalid.
        """
        super().__init__(
            f"Invalid possible_values configuration for input '{key_name}'."
        )


class MissingCalculationMatchError(ValueError):
    """Raised when no calculation matches the provided filters."""

    def __init__(self) -> None:
        """
        Exception raised when no calculation matches the provided filters.

        Initializes the exception with the message "No matching calculation found."
        """
        super().__init__("No matching calculation found.")


class MultipleCalculationMatchError(ValueError):
    """Raised when more than one calculation matches the provided filters."""

    def __init__(self) -> None:
        """
        Error raised when more than one calculation matches the provided filters.

        Initializes the exception with the message "Multiple matching calculations found."
        """
        super().__init__("Multiple matching calculations found.")


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
        Initialize a CalculationBucket configured to enumerate all valid input combinations for a manager.

        Parameters:
            manager_class (type[GeneralManagerType]): Manager subclass whose Interface must inherit from CalculationInterface.
            filter_definitions (dict[str, dict] | None): Mapping of input/property filter constraints to apply to generated combinations.
            exclude_definitions (dict[str, dict] | None): Mapping of input/property exclude constraints to remove generated combinations.
            sort_key (str | tuple[str] | None): Key name or tuple of key names used to order generated manager combinations.
            reverse (bool): If True, reverse the ordering defined by `sort_key`.

        Raises:
            InvalidCalculationInterfaceError: If the manager_class.Interface does not inherit from CalculationInterface.
        """
        from general_manager.interface.calculation_interface import (
            CalculationInterface,
        )

        super().__init__(manager_class)

        interface_class = manager_class.Interface
        if not issubclass(interface_class, CalculationInterface):
            raise InvalidCalculationInterfaceError()
        self.input_fields = interface_class.input_fields
        self.filter_definitions = (
            {} if filter_definitions is None else filter_definitions
        )
        self.exclude_definitions = (
            {} if exclude_definitions is None else exclude_definitions
        )

        properties = self._manager_class.Interface.get_graph_ql_properties()
        possible_values = self.transform_properties_to_input_fields(
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
        Combine this bucket with another bucket or intersect it with a single manager instance.

        Parameters:
            other: A CalculationBucket or a GeneralManager instance to merge. If a manager instance of the same manager class is given, it is treated as a filter on that manager's identification.

        Returns:
            A new CalculationBucket representing the constraints common to both operands.

        Raises:
            IncompatibleBucketTypeError: If `other` is neither a CalculationBucket nor a compatible manager instance.
            IncompatibleBucketManagerError: If `other` is a CalculationBucket for a different manager class.
        """
        from general_manager.manager.general_manager import GeneralManager

        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(self.filter(id__in=[other.identification]))
        if not isinstance(other, self.__class__):
            raise IncompatibleBucketTypeError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise IncompatibleBucketManagerError(
                self._manager_class, other._manager_class
            )

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
    def transform_properties_to_input_fields(
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

    def _sort_filters(self, sorted_inputs: List[str]) -> SortedFilters:
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
            sorted_filters = self._sort_filters(sorted_inputs)
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
            list[str]: Input names ordered so each dependency appears before its dependents.

        Raises:
            CyclicDependencyError: If the dependency graph contains a cycle; the exception's `node` identifies a node involved in the cycle.
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
            Depth-first search helper that orders dependency nodes and detects cycles.

            Parameters:
                node (str): The input field being visited.
                temp_mark (set[str]): Nodes on the current DFS path used to detect cycles.

            Raises:
                CyclicDependencyError: If a cyclic dependency is detected involving `node`.
            """
            if node in visited:
                return
            if node in temp_mark:
                raise CyclicDependencyError(node)
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
        Resolve potential values for an input field based on the current partial input combination.

        Parameters:
            key_name (str): Name of the input field used for error context.
            input_field (Input): Input definition that may include `possible_values` and `depends_on`.
            current_combo (dict): Partial mapping of already-selected input values required to evaluate dependencies.

        Returns:
            Iterable[Any] | Bucket[Any]: An iterable of allowed values for the input or a Bucket supplying candidate values.

        Raises:
            InvalidPossibleValuesError: If the input field's `possible_values` is neither callable nor an iterable/Bucket.
        """
        if callable(input_field.possible_values):
            depends_on = input_field.depends_on
            dep_values = [current_combo[dep_name] for dep_name in depends_on]
            possible_values = input_field.possible_values(*dep_values)
        elif isinstance(input_field.possible_values, (Iterable, Bucket)):
            possible_values = input_field.possible_values
        else:
            raise InvalidPossibleValuesError(key_name)
        return possible_values

    def _generate_input_combinations(
        self,
        sorted_inputs: List[str],
        filters: dict[str, dict],
        excludes: dict[str, dict],
    ) -> List[dict[str, Any]]:
        """
        Generate all valid assignments of input fields that satisfy the provided per-field filters and exclusions.

        Parameters:
            sorted_inputs (list[str]): Input names in dependency-respecting order.
            filters (dict[str, dict]): Per-input filter definitions (may include `filter_funcs` or `filter_kwargs`).
            excludes (dict[str, dict]): Per-input exclusion definitions (may include `filter_funcs` or `filter_kwargs`).

        Returns:
            list[dict[str, Any]]: Completed input-to-value mappings that meet the filters and excludes.
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
        Return the single manager instance that matches the provided field filters.

        Parameters:
            **kwargs (Any): Field filters to apply when selecting a calculation (e.g., property or input names mapped to expected values).

        Returns:
            The single manager instance that satisfies the provided filters.

        Raises:
            MissingCalculationMatchError: If no matching manager exists.
            MultipleCalculationMatchError: If more than one matching manager exists.
        """
        filtered_bucket = self.filter(**kwargs)
        items = list(filtered_bucket)
        if len(items) == 1:
            return items[0]
        elif len(items) == 0:
            raise MissingCalculationMatchError()
        else:
            raise MultipleCalculationMatchError()

    def sort(
        self, key: str | tuple[str], reverse: bool = False
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Create a new CalculationBucket configured to order generated combinations by the given attribute key.

        Parameters:
            key: Attribute name or tuple of attribute names to use for ordering generated manager combinations.
            reverse: If True, sort in descending order.

        Returns:
            A new CalculationBucket configured to sort combinations by the provided key and direction.
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
        own.filter_definitions = {}
        own.exclude_definitions = {}
        own._filters = {}
        own._excludes = {}
        return own
