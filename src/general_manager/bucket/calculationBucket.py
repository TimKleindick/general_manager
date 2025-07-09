from __future__ import annotations
from typing import (
    Any,
    Type,
    TYPE_CHECKING,
    Iterable,
    Union,
    Optional,
    Generator,
    List,
)
from general_manager.interface.baseInterface import (
    generalManagerClassName,
    GeneralManagerType,
)
from general_manager.bucket.baseBucket import Bucket
from general_manager.manager.input import Input
from general_manager.utils.filterParser import parse_filters

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.interface.calculationInterface import CalculationInterface


class CalculationBucket(Bucket[GeneralManagerType]):
    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        filter_definitions: Optional[dict[str, dict]] = None,
        exclude_definitions: Optional[dict[str, dict]] = None,
        sort_key: Optional[Union[str, tuple[str]]] = None,
        reverse: bool = False,
    ):
        """
        Initializes a CalculationBucket for managing calculation input combinations.

        Args:
            manager_class: The manager class whose interface must inherit from CalculationInterface.
            filter_definitions: Optional filters to apply to input combinations.
            exclude_definitions: Optional exclusions to remove certain input combinations.
            sort_key: Optional key or tuple of keys to sort the generated combinations.
            reverse: If True, reverses the sorting order.

        Raises:
            TypeError: If the manager class interface does not inherit from CalculationInterface.
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
        self.filters = {} if filter_definitions is None else filter_definitions
        self.excludes = {} if exclude_definitions is None else exclude_definitions
        self._current_combinations = None
        self.sort_key = sort_key
        self.reverse = reverse

    def __reduce__(self) -> generalManagerClassName | tuple[Any, ...]:
        """
        Prepares the CalculationBucket instance for pickling by returning its reconstruction data.

        Returns:
            A tuple containing the class and a tuple of initialization arguments needed to recreate the instance.
        """
        return (
            self.__class__,
            (
                self._manager_class,
                self.filters,
                self.excludes,
                self.sort_key,
                self.reverse,
            ),
            {"current_combinations": self._current_combinations},
        )

    def __setstate__(self, state: dict[str, Any]) -> None:
        """
        Restores the CalculationBucket instance from its pickled state.

        Args:
            state: A dictionary containing the state of the instance, including current combinations.
        """
        self._current_combinations = state.get("current_combinations")

    def __or__(
        self,
        other: (
            Bucket[GeneralManagerType]
            | GeneralManager[GeneralManagerType, CalculationInterface]
        ),
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Combines this CalculationBucket with another bucket or manager of the same type.

        If combined with a manager instance, returns a bucket filtered to that manager's identification.
        If combined with another CalculationBucket of the same manager class, returns a new bucket with filters and excludes that are present and identical in both.

        Raises:
            ValueError: If the other object is not a CalculationBucket or manager of the same class.
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
        """
        Returns a string representation of the bucket, listing up to five calculation manager instances with their input combinations.

        If more than five combinations exist, an ellipsis is appended to indicate additional entries.
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
        Returns a concise string representation of the CalculationBucket, including the manager class name, filters, excludes, sort key, and sort order.
        """
        return f"{self.__class__.__name__}({self._manager_class.__name__}, {self.filters}, {self.excludes}, {self.sort_key}, {self.reverse})"

    def filter(self, **kwargs: Any) -> CalculationBucket:
        """
        Returns a new CalculationBucket with additional filters applied.

        Merges the provided filter criteria with existing filters to further restrict valid input combinations.
        """
        filters = self.filters.copy()
        excludes = self.excludes.copy()
        filters.update(parse_filters(kwargs, self.input_fields))
        return CalculationBucket(self._manager_class, filters, excludes)

    def exclude(self, **kwargs: Any) -> CalculationBucket:
        """
        Returns a new CalculationBucket with additional exclusion criteria applied.

        Keyword arguments specify input values to exclude from the generated combinations.
        """
        filters = self.filters.copy()
        excludes = self.excludes.copy()
        excludes.update(parse_filters(kwargs, self.input_fields))
        return CalculationBucket(self._manager_class, filters, excludes)

    def all(self) -> CalculationBucket:
        """
        Returns the current CalculationBucket instance.

        This method allows for compatibility with interfaces expecting an `all()` method that returns the full set of items.
        """
        return self

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Yields manager instances for each valid combination of input parameters.

        Iterates over all generated input combinations, instantiating the manager class with each set of parameters.
        """
        combinations = self.generate_combinations()
        for combo in combinations:
            yield self._manager_class(**combo)

    def generate_combinations(self) -> List[dict[str, Any]]:
        """
        Generates and caches all valid input combinations based on filters, exclusions, and sorting.

        Returns:
            A list of dictionaries, each representing a unique combination of input values that satisfy the current filters, exclusions, and sorting order.
        """

        def key_func(item: dict[str, Any]) -> tuple:
            return tuple(item[key] for key in sort_key)

        if self._current_combinations is None:
            # Implementierung ähnlich wie im InputManager
            sorted_inputs = self.topological_sort_inputs()
            current_combinations = self._generate_combinations(
                sorted_inputs, self.filters, self.excludes
            )
            if self.sort_key is not None:
                sort_key = self.sort_key
                if isinstance(sort_key, str):
                    sort_key = (sort_key,)
                current_combinations = sorted(
                    current_combinations,
                    key=key_func,
                )
            if self.reverse:
                current_combinations.reverse()
            self._current_combinations = current_combinations

        return self._current_combinations

    def topological_sort_inputs(self) -> List[str]:
        """
        Performs a topological sort of input fields based on their dependencies.

        Returns:
            A list of input field names ordered so that each field appears after its dependencies.

        Raises:
            ValueError: If a cyclic dependency is detected among the input fields.
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

        def visit(node, temp_mark):
            """
            Performs a depth-first traversal to topologically sort nodes, detecting cycles.

            Args:
                node: The current node to visit.
                temp_mark: A set tracking nodes in the current traversal path to detect cycles.

            Raises:
                ValueError: If a cyclic dependency is detected involving the current node.
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
        # Hole mögliche Werte
        """
        Retrieves the possible values for a given input field based on its definition and current dependencies.

        If the input field's `possible_values` is a callable, it is invoked with the current values of its dependencies. If it is an iterable or a `Bucket`, it is returned directly. Raises a `TypeError` if `possible_values` is not a valid type.

        Args:
            key_name: The name of the input field.
            input_field: The input field object whose possible values are to be determined.
            current_combo: The current combination of input values, used to resolve dependencies.

        Returns:
            An iterable or `Bucket` containing the possible values for the input field.

        Raises:
            TypeError: If `possible_values` is neither callable, iterable, nor a `Bucket`.
        """
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
        """
        Recursively generates all valid input combinations for the specified input fields, applying filters and exclusions.

        Args:
            sorted_inputs: Input field names ordered to respect dependency constraints.
            filters: Mapping of input field names to filter definitions.
            excludes: Mapping of input field names to exclusion definitions.

        Returns:
            A list of dictionaries, each representing a valid combination of input values that satisfy all filters and exclusions.
        """

        def helper(index, current_combo):
            """
            Recursively generates all valid input combinations for calculation inputs.

            Yields:
                Dict[str, Any]: A dictionary representing a valid combination of input values, filtered and excluded according to the provided criteria.
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

    def first(self) -> GeneralManagerType | None:
        """
        Returns the first generated manager instance, or None if no combinations exist.
        """
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GeneralManagerType | None:
        """
        Returns the last generated manager instance, or None if no combinations exist.
        """
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        """
        Returns the number of calculation combinations in the bucket.
        """
        return self.__len__()

    def __len__(self) -> int:
        """
        Returns the number of generated calculation combinations in the bucket.
        """
        return len(self.generate_combinations())

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | CalculationBucket[GeneralManagerType]:
        """
        Returns a manager instance or a new bucket for the specified index or slice.

        If an integer index is provided, returns the corresponding manager instance.
        If a slice is provided, returns a new CalculationBucket representing the sliced subset.
        """
        items = self.generate_combinations()
        result = items[item]
        if isinstance(result, list):
            new_bucket = CalculationBucket(
                self._manager_class,
                self.filters.copy(),
                self.excludes.copy(),
                self.sort_key,
                self.reverse,
            )
            new_bucket._current_combinations = result
            return new_bucket
        return self._manager_class(**result)

    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Checks if the specified manager instance is present in the generated combinations.

        Args:
                item: The manager instance to check for membership.

        Returns:
                True if the instance is among the generated combinations, False otherwise.
        """
        return any(item == mgr for mgr in self)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        """
        Retrieves a single calculation manager instance matching the specified filters.

        Args:
            **kwargs: Filter criteria to apply.

        Returns:
            The unique manager instance matching the filters.

        Raises:
            ValueError: If no matching calculation is found or if multiple matches exist.
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
        Returns a new CalculationBucket with updated sorting parameters.

        Args:
            key: The field name or tuple of field names to sort combinations by.
            reverse: If True, sorts in descending order.

        Returns:
            A new CalculationBucket instance with the specified sorting applied.
        """
        return CalculationBucket(
            self._manager_class, self.filters, self.excludes, key, reverse
        )
