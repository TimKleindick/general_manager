"""Bucket implementation that enumerates calculation interface combinations."""

from __future__ import annotations
from collections.abc import Hashable, Iterable, Iterator
from datetime import datetime
from types import UnionType
from itertools import islice
from typing import (
    Type,
    TYPE_CHECKING,
    Union,
    Optional,
    Generator,
    List,
    TypedDict,
    get_origin,
    get_args,
    cast,
)
from copy import deepcopy
from general_manager.as_of import (
    HistoricalContextConflictError,
    _represents_same_instant,
    current_as_of_date,
)
from general_manager.interface.base_interface import (
    generalManagerClassName,
    GeneralManagerType,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.bucket._ordering import (
    SortTerm,
    normalize_ordering,
    sort_items,
    validate_ordering_fields,
)
from general_manager.bucket.projection import ProjectionRows
from general_manager.manager.input import Input, InputDomain
from general_manager.utils.filter_parser import (
    FilterFunction,
    ParsedFilters,
    create_filter_function,
    parse_filters,
)

if TYPE_CHECKING:
    from general_manager.api.property import GraphQLProperty
    from general_manager.bucket.group_bucket import GroupBucket
    from general_manager.interface.interfaces.calculation import CalculationInterface
    from general_manager.manager.general_manager import GeneralManager


type Combination = dict[str, object]
type RawFilterDefinitions = dict[str, object]
type FilterCallGroups = tuple[RawFilterDefinitions, ...]


class SortedFilters(TypedDict):
    """Internal parsed-filter partition used while generating combinations."""

    prop_filters: ParsedFilters
    input_filters: ParsedFilters
    prop_excludes: ParsedFilters
    input_excludes: ParsedFilters


def _build_exclude_filter(exclude_func: FilterFunction) -> FilterFunction:
    """Create a lazy predicate that keeps values an exclude does not reject."""

    def includes_value(value: object) -> bool:
        return not exclude_func(value)

    return includes_value


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
        filter_definitions: Optional[RawFilterDefinitions] = None,
        exclude_definitions: Optional[RawFilterDefinitions] = None,
        filter_call_groups: FilterCallGroups | None = None,
        exclude_call_groups: FilterCallGroups | None = None,
    ) -> None:
        """
        Initialize a CalculationBucket configured to enumerate all valid input combinations for a manager.

        Parameters:
            manager_class (type[GeneralManagerType]): Manager subclass whose Interface must inherit from CalculationInterface.
            filter_definitions (dict[str, dict] | None): Mapping of input/property filter constraints to apply to generated combinations.
            exclude_definitions (dict[str, dict] | None): Mapping of input/property exclude constraints to remove generated combinations.
        Raises:
            InvalidCalculationInterfaceError: If the manager_class.Interface does not inherit from CalculationInterface.
        """
        from general_manager.interface.interfaces.calculation import (
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
        self._filter_call_groups = (
            filter_call_groups
            if filter_call_groups is not None
            else ((self.filter_definitions,) if self.filter_definitions else ())
        )
        self._exclude_call_groups = (
            exclude_call_groups
            if exclude_call_groups is not None
            else ((self.exclude_definitions,) if self.exclude_definitions else ())
        )

        properties = self._manager_class.Interface.get_graph_ql_properties()
        possible_values = self.transform_properties_to_input_fields(
            properties, self.input_fields
        )

        self._filters = parse_filters(self.filter_definitions, possible_values)
        self._excludes = parse_filters(self.exclude_definitions, possible_values)
        self._parsed_filter_call_groups = tuple(
            parse_filters(group, possible_values) for group in self._filter_call_groups
        )
        self._parsed_exclude_call_groups = tuple(
            parse_filters(group, possible_values) for group in self._exclude_call_groups
        )

        self._data: list[Combination] | None = None
        self._allowed_identifications: list[Combination] | None = None
        self._sort_fields: tuple[str, ...] = ()
        self._effective_search_date = current_as_of_date()

    def _ensure_as_of_compatible(self) -> None:
        """Reject use in a historical context other than the bound snapshot."""
        active = current_as_of_date()
        effective = self._effective_search_date
        if active is None:
            if effective is not None:
                raise HistoricalContextConflictError
            return
        if effective is None or not _represents_same_instant(effective, active):
            raise HistoricalContextConflictError

    def _derive(
        self,
        *,
        filter_definitions: RawFilterDefinitions,
        exclude_definitions: RawFilterDefinitions,
        sort_fields: tuple[str, ...],
        filter_call_groups: FilterCallGroups | None = None,
        exclude_call_groups: FilterCallGroups | None = None,
    ) -> CalculationBucket[GeneralManagerType]:
        """Build a derived bucket while preserving this bucket's snapshot."""
        bucket = self.__class__(
            self._manager_class,
            filter_definitions,
            exclude_definitions,
            filter_call_groups,
            exclude_call_groups,
        )
        bucket._sort_fields = sort_fields
        bucket._effective_search_date = self._effective_search_date
        if self._allowed_identifications is not None:
            bucket._allowed_identifications = [
                dict(identification) for identification in self._allowed_identifications
            ]
        return bucket

    def __eq__(self, other: object) -> bool:
        """
        Compare two calculation buckets for structural equality.

        Parameters:
            other (object): Candidate bucket.

        Returns:
            bool: True when both buckets share the same manager class and identical filter/exclude state.
        """
        self._ensure_as_of_compatible()
        if not isinstance(other, self.__class__):
            return False
        other._ensure_as_of_compatible()
        return (
            self.filter_definitions == other.filter_definitions
            and self.exclude_definitions == other.exclude_definitions
            and self._filter_call_groups == other._filter_call_groups
            and self._exclude_call_groups == other._exclude_call_groups
            and self._manager_class == other._manager_class
        )

    def __reduce__(self) -> generalManagerClassName | tuple[object, ...]:
        """
        Provide pickling support for calculation buckets.

        Returns:
            tuple[object, ...]: Reconstruction data representing the class, arguments, and state.
        """
        self._ensure_as_of_compatible()
        return (
            self.__class__,
            (
                self._manager_class,
                self.filter_definitions,
                self.exclude_definitions,
                self._filter_call_groups,
                self._exclude_call_groups,
            ),
            {
                "data": self._data,
                "allowed_identifications": self._allowed_identifications,
                "effective_search_date": self._effective_search_date,
                "sort_fields": self._sort_fields,
            },
        )

    def __setstate__(self, state: dict[str, object]) -> None:
        """
        Restore the bucket after unpickling.

        Parameters:
            state: Pickled state containing cached combination data.

        Returns:
            None
        """
        self._data = cast(list[Combination] | None, state.get("data"))
        self._allowed_identifications = cast(
            list[Combination] | None,
            state.get("allowed_identifications"),
        )
        self._effective_search_date = cast(
            "datetime | None", state.get("effective_search_date")
        )
        self._sort_fields = cast(tuple[str, ...], state.get("sort_fields", ()))
        possible_values = self.transform_properties_to_input_fields(
            self._manager_class.Interface.get_graph_ql_properties(), self.input_fields
        )
        self._parsed_filter_call_groups = tuple(
            parse_filters(group, possible_values) for group in self._filter_call_groups
        )
        self._parsed_exclude_call_groups = tuple(
            parse_filters(group, possible_values) for group in self._exclude_call_groups
        )

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> Bucket[GeneralManagerType]:
        """
        Materialize a left-first deduplicated union of represented combinations.

        Parameters:
            other: A CalculationBucket or a GeneralManager instance to combine.
                If a same-class manager instance is given, it is first converted
                into an ``id__in=[identification]`` filter bucket.

        Returns:
            A private exact-subset bucket containing each represented manager
            once, in left-first order.

        Raises:
            IncompatibleBucketTypeError: If `other` is neither a CalculationBucket nor a compatible manager instance.
            IncompatibleBucketManagerError: If `other` is a CalculationBucket for a different manager class.
        """
        self._ensure_as_of_compatible()
        from general_manager.bucket._materialized_bucket import MaterializedBucket

        if isinstance(other, self._manager_class):
            return (
                MaterializedBucket(
                    self._manager_class,
                    tuple(self),
                    snapshot=self._effective_search_date,
                )
                | other
            )
        if isinstance(other, MaterializedBucket):
            if other._manager_class != self._manager_class:
                raise IncompatibleBucketManagerError(
                    self._manager_class, other._manager_class
                )
            return (
                MaterializedBucket(
                    self._manager_class,
                    tuple(self),
                    snapshot=self._effective_search_date,
                )
                | other
            )
        if not isinstance(other, self.__class__):
            raise IncompatibleBucketTypeError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise IncompatibleBucketManagerError(
                self._manager_class, other._manager_class
            )

        other._ensure_as_of_compatible()
        return MaterializedBucket(
            self._manager_class,
            tuple(self),
            snapshot=self._effective_search_date,
        ) | MaterializedBucket(
            self._manager_class,
            tuple(other),
            snapshot=other._effective_search_date,
        )

    def __str__(self) -> str:
        """
        Return a compact preview of generated combinations.

        Cached buckets include the exact combination count. Uncached buckets avoid
        materializing all combinations for string formatting; when more than the
        preview limit exists, the count is reported as a lower-bound label.

        Returns:
            str: Human-readable summary of up to five combinations.
        """
        self._ensure_as_of_compatible()
        PRINT_MAX = 5
        combinations, count_label, has_more = self._str_combinations_preview(PRINT_MAX)
        prefix = f"CalculationBucket ({count_label})["
        main = ",".join(
            [f"{self._manager_class.__name__}(**{comb})" for comb in combinations]
        )
        suffix = "]"
        if has_more:
            suffix = ", ...]"

        return f"{prefix}{main}{suffix}"

    def _str_combinations_preview(
        self, limit: int
    ) -> tuple[list[Combination], str, bool]:
        """
        Return combinations, count label, and overflow flag for ``__str__``.

        Sorted buckets use normal materialization so the preview
        reflects the final global ordering. Unsorted uncached buckets read at
        most ``limit + 1`` matching combinations and leave ``_data`` untouched.
        """
        if self._data is not None:
            return self._data[:limit], str(len(self._data)), len(self._data) > limit

        if self._normalized_sort_key() is not None:
            combinations = self.generate_combinations()
            return (
                combinations[:limit],
                str(len(combinations)),
                len(combinations) > limit,
            )

        from general_manager.cache.run_context import ensure_calculation_run_context

        with ensure_calculation_run_context():
            sorted_inputs = self.topological_sort_inputs()
            sorted_filters = self._sort_filters(sorted_inputs)
            if self._uses_static_iterator_possible_values(sorted_inputs):
                combinations = self.generate_combinations()
                return (
                    combinations[:limit],
                    str(len(combinations)),
                    len(combinations) > limit,
                )
            snapshot_iterables = self._uses_dependent_possible_values(sorted_inputs)
            preview_iterator = self._iter_input_combinations(
                sorted_inputs,
                sorted_filters["input_filters"],
                sorted_filters["input_excludes"],
                snapshot_iterables=snapshot_iterables,
            )
            allowed_identification_keys = None
            if self._allowed_identifications is not None:
                allowed_identification_keys = {
                    freeze_bucket_index_value(identification)
                    for identification in self._allowed_identifications
                }
            if (
                self._requires_grouped_call_evaluation()
                or sorted_filters["prop_filters"]
                or sorted_filters["prop_excludes"]
                or allowed_identification_keys is not None
            ):
                preview_iterator = self._iter_prop_filtered_identifications(
                    preview_iterator,
                    sorted_filters["prop_filters"],
                    sorted_filters["prop_excludes"],
                    allowed_identification_keys,
                )
            preview = list(islice(preview_iterator, limit + 1))

        has_more = len(preview) > limit
        if has_more:
            preview = preview[:limit]
        count_label = f"{limit}+" if has_more else str(len(preview))
        return preview, count_label, has_more

    def _uses_static_iterator_possible_values(self, sorted_inputs: list[str]) -> bool:
        """Return whether previewing would consume a one-shot static iterator."""
        return any(
            isinstance(self.input_fields[input_name].possible_values, Iterator)
            for input_name in sorted_inputs
        )

    def _uses_dependent_possible_values(self, sorted_inputs: list[str]) -> bool:
        """Return whether previewing should snapshot values before dependencies."""
        return any(
            bool(self.input_fields[input_name].depends_on)
            and self.input_fields[input_name].possible_values is not None
            for input_name in sorted_inputs
        )

    def __repr__(self) -> str:
        """
        Return a detailed representation of the bucket configuration.

        Returns:
            str: Debug string listing filters, excludes, and signed ordering.
        """
        self._ensure_as_of_compatible()
        rendered = (
            f"{self.__class__.__name__}({self._manager_class.__name__}, "
            f"{self.filter_definitions}, {self.exclude_definitions})"
        )
        if not self._sort_fields:
            return rendered
        fields = ", ".join(repr(field) for field in self._sort_fields)
        return f"{rendered}.sort({fields})"

    @staticmethod
    def transform_properties_to_input_fields(
        properties: dict[str, GraphQLProperty],
        input_fields: dict[str, Input[type[object]]],
    ) -> dict[str, Input[type[object]]]:
        """
        Derive input-field definitions for GraphQL properties without explicit inputs.

        This helper is a framework hook used by calculation filtering and
        sorting. It treats list, tuple, set, union, and optional property type
        hints as their concrete element/member type when possible and falls back
        to ``object`` when the hint cannot be resolved to a class.

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

    def filter(self, **kwargs: object) -> CalculationBucket[GeneralManagerType]:
        """
        Add additional filters and return a new calculation bucket.

        Lookup keys use the shared calculation filter grammar: ``field`` or
        ``field__lookup`` for input and property values. Supported Python
        lookup operators are ``exact``, ``lt``, ``lte``, ``gt``, ``gte``,
        ``contains``, ``startswith``, ``endswith``, and ``in``. For
        manager-typed inputs, ``field=value`` filters by the manager id,
        ``field_id`` is an id alias, and suffixes such as
        ``field__name__startswith`` are forwarded to the nested manager bucket.
        Unknown fields raise ``UnknownInputFieldError`` from the filter parser.
        Calling without keyword arguments returns an independent equivalent
        bucket without altering the query groups.

        Parameters:
            **kwargs: Filter expressions applied to generated combinations.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket reflecting the updated filter definitions.

        Raises:
            UnknownInputFieldError: If a filter key references no input or
                derived GraphQL property.
            TypeError: Propagated from invalid input casts or downstream
                manager-bucket filtering.
            ValueError: Propagated from input parsing or normalization.
        """
        self._ensure_as_of_compatible()
        if not kwargs:
            return self.all()
        return self._derive(
            filter_definitions={
                **self.filter_definitions.copy(),
                **kwargs,
            },
            exclude_definitions=self.exclude_definitions.copy(),
            sort_fields=self._sort_fields,
            filter_call_groups=(*self._filter_call_groups, dict(kwargs)),
            exclude_call_groups=self._exclude_call_groups,
        )

    def exclude(self, **kwargs: object) -> CalculationBucket[GeneralManagerType]:
        """
        Add additional exclusion rules and return a new calculation bucket.

        Exclusion keys use the same lookup grammar and error behavior as
        :meth:`filter`; matching combinations are removed rather than kept.
        Calling without keyword arguments returns an independent equivalent
        bucket without altering the query groups.

        Parameters:
            **kwargs: Exclusion expressions removing combinations from the result.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket reflecting the updated exclusion definitions.

        Raises:
            UnknownInputFieldError: If an exclude key references no input or
                derived GraphQL property.
            TypeError: Propagated from invalid input casts or downstream
                manager-bucket filtering.
            ValueError: Propagated from input parsing or normalization.
        """
        self._ensure_as_of_compatible()
        if not kwargs:
            return self.all()
        return self._derive(
            filter_definitions=self.filter_definitions.copy(),
            exclude_definitions={
                **self.exclude_definitions.copy(),
                **kwargs,
            },
            sort_fields=self._sort_fields,
            filter_call_groups=self._filter_call_groups,
            exclude_call_groups=(*self._exclude_call_groups, dict(kwargs)),
        )

    def all(self) -> CalculationBucket[GeneralManagerType]:
        """
        Return a deep copy of this calculation bucket.

        Returns:
            CalculationBucket[GeneralManagerType]: Independent copy that can be mutated without affecting the original.
        """
        self._ensure_as_of_compatible()
        return deepcopy(self)

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        """
        Iterate over every generated combination as a manager instance.

        Yields:
            GeneralManagerType: Manager constructed from each valid set of inputs.
        """
        self._ensure_as_of_compatible()
        combinations = self.generate_combinations()
        for combo in combinations:
            yield self._manager_class(**combo)

    def _project_rows(self, fields: tuple[str, ...]) -> ProjectionRows:
        """Project normalized calculation inputs without manager instances."""
        self._ensure_as_of_compatible()
        if not all(field in self.input_fields for field in fields):
            return super()._project_rows(fields)
        if self._projection_requires_manager_access():
            return super()._project_rows(fields)

        from general_manager.interface.capabilities.calculation.input_resolution import (
            resolve_calculation_input_value,
        )

        interface_class = cast(
            "type[CalculationInterface]",
            self._manager_class.Interface,
        )
        rows: list[tuple[object, ...]] = []
        for identification in self.generate_combinations():
            resolved_values: dict[str, object] = {}
            rows.append(
                tuple(
                    resolve_calculation_input_value(
                        interface_class,
                        identification,
                        field,
                        resolved_values,
                    )
                    for field in fields
                )
            )
        return tuple(rows)

    def _projection_requires_manager_access(self) -> bool:
        """Return whether this plan needs portable manager evaluation."""
        if self._allowed_identifications is not None:
            return True

        sorted_filters = self._sort_filters(self.topological_sort_inputs())
        return self._filters_or_sort_require_manager_access(sorted_filters)

    def _filters_or_sort_require_manager_access(
        self,
        sorted_filters: SortedFilters,
    ) -> bool:
        """Return whether property filters or sorting need manager values."""
        if (
            self._requires_grouped_call_evaluation()
            or sorted_filters["prop_filters"]
            or sorted_filters["prop_excludes"]
        ):
            return True
        return not self._sort_uses_only_inputs(self._normalized_sort_key())

    def _sort_filters(self, sorted_inputs: List[str]) -> SortedFilters:
        """
        Partition filters into input- and property-based buckets.

        Parameters:
            sorted_inputs (list[str]): Input names ordered by dependency.

        Returns:
            SortedFilters: Mapping that separates filters/excludes for inputs and properties.
        """
        input_filters: ParsedFilters = {}
        prop_filters: ParsedFilters = {}
        input_excludes: ParsedFilters = {}
        prop_excludes: ParsedFilters = {}

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

        if self._requires_grouped_call_evaluation():
            # Flattened excludes implement NOT(a) AND NOT(b), which is not the
            # public NOT(a AND b) contract. The grouped pass below evaluates
            # them after manager construction instead.
            input_excludes = {}
            prop_excludes = {}

        return {
            "prop_filters": prop_filters,
            "input_filters": input_filters,
            "prop_excludes": prop_excludes,
            "input_excludes": input_excludes,
        }

    def _requires_grouped_call_evaluation(self) -> bool:
        """Return whether flattened definitions lose a public call boundary."""
        filter_keys = [key for group in self._filter_call_groups for key in group]
        exclude_keys = [key for group in self._exclude_call_groups for key in group]
        manager_lookup_keys = [
            (field_name, lookup)
            for groups in (
                self._parsed_filter_call_groups,
                self._parsed_exclude_call_groups,
            )
            for group in groups
            for field_name, definitions in group.items()
            for lookup in definitions.get("filter_kwargs", {})
        ]
        return (
            len(filter_keys) != len(set(filter_keys))
            or len(exclude_keys) != len(set(exclude_keys))
            or len(manager_lookup_keys) != len(set(manager_lookup_keys))
            or any(len(group) > 1 for group in self._exclude_call_groups)
        )

    @staticmethod
    def _matches_call_group(
        manager: GeneralManagerType,
        group: ParsedFilters,
    ) -> bool:
        """Apply one parsed Django-style call group to a generated manager."""
        for field_name, definitions in group.items():
            try:
                value = getattr(manager, field_name)
            except AttributeError:
                return False
            for filter_func in definitions.get("filter_funcs", []):
                if not filter_func(value):
                    return False
            for lookup, expected in definitions.get("filter_kwargs", {}).items():
                identification = getattr(value, "identification", None)
                lookup_parts = lookup.split("__") if lookup else []
                if (
                    isinstance(identification, dict)
                    and lookup_parts
                    and lookup_parts[0] in identification
                ):
                    candidate = identification[lookup_parts[0]]
                    matcher = create_filter_function(
                        "__".join(lookup_parts[1:]), expected
                    )
                else:
                    candidate = value
                    matcher = create_filter_function(lookup, expected)
                if not matcher(candidate):
                    return False
        return True

    def _apply_call_groups(
        self,
        managers: list[GeneralManagerType],
    ) -> list[GeneralManagerType]:
        """Preserve AND call groups and NOT(AND) exclude groups exactly."""
        if not self._requires_grouped_call_evaluation():
            return managers
        return [
            manager
            for manager in managers
            if all(
                self._matches_call_group(manager, group)
                for group in self._parsed_filter_call_groups
            )
            and not any(
                self._matches_call_group(manager, group)
                for group in self._parsed_exclude_call_groups
            )
        ]

    def _normalized_sort_key(self) -> tuple[str, ...] | None:
        """Return the configured sort key as a tuple, or None when unsorted."""
        if not self._sort_fields:
            return None
        return self._sort_fields

    def _bucket_index_source_signature(self) -> Hashable:
        """Return a stable signature for equivalent calculation bucket plans."""
        self._ensure_as_of_compatible()
        return (
            "calculation",
            self._manager_class,
            freeze_bucket_index_value(self.filter_definitions),
            freeze_bucket_index_value(self.exclude_definitions),
            freeze_bucket_index_value(self._filter_call_groups),
            freeze_bucket_index_value(self._exclude_call_groups),
            freeze_bucket_index_value(self._allowed_identifications),
            self._normalized_sort_key(),
        )

    def _sort_uses_only_inputs(self, sort_key: tuple[str, ...] | None) -> bool:
        """Return whether a sort can be applied to raw input dictionaries."""
        if sort_key is None:
            return True
        return all(
            key.removeprefix("-").removeprefix("+") in self.input_fields
            for key in sort_key
        )

    def _sort_dict_combinations(
        self,
        combinations: list[Combination],
        terms: tuple[SortTerm, ...],
    ) -> list[Combination]:
        """
        Sort input dictionaries while tolerating missing optional inputs.

        Present values sort before missing values in ascending order. Missing
        keys use None as the explicit placeholder, guarded by a presence flag so
        they are not compared directly with concrete input values.
        """
        return sort_items(
            combinations,
            terms,
            value_for=lambda combo, field: combo.get(field),
            identity_for=lambda combo: combo,
        )

    def _manager_combinations(
        self,
        combinations: list[Combination],
    ) -> list[GeneralManagerType]:
        """Instantiate managers for each raw input-combination dictionary."""
        self._ensure_as_of_compatible()
        return [self._manager_class(**combo) for combo in combinations]

    @staticmethod
    def _manager_identifications(
        managers: list[GeneralManagerType],
    ) -> list[Combination]:
        """Return the identification dictionaries from manager instances."""
        return [manager.identification for manager in managers]

    def generate_combinations(self) -> List[Combination]:
        """
        Compute (and cache) the list of valid input combinations.

        This framework helper materializes the bucket. It orders inputs by
        dependency, applies input-level filters/excludes while enumerating
        candidate values, then applies property-level filters/excludes and
        sorting when manager access is required. The returned list is the
        bucket's cached mutable list; callers should treat it as read-only.

        Returns:
            list[Combination]: Cached list of input dictionaries satisfying filters, excludes, and ordering.

        Raises:
            CyclicDependencyError: If input dependencies contain a cycle.
            InvalidPossibleValuesError: If a required input cannot provide
                iterable or bucket-backed possible values.
            UnknownInputFieldError: If stored filter definitions reference an
                unknown input or property.
            AttributeError: Propagated from missing computed properties during
                property filtering or sorting.
            TypeError: Propagated from invalid casts, downstream bucket
                filtering, or incomparable sort values.
            ValueError: Propagated from input parsing or normalization.
        """

        self._ensure_as_of_compatible()
        if self._data is None:
            from general_manager.cache.run_context import ensure_calculation_run_context

            with ensure_calculation_run_context():
                sorted_inputs = self.topological_sort_inputs()
                sorted_filters = self._sort_filters(sorted_inputs)
                current_combinations = self._generate_input_combinations(
                    sorted_inputs,
                    sorted_filters["input_filters"],
                    sorted_filters["input_excludes"],
                )
                manager_combinations: list[GeneralManagerType] | None = None
                if self._allowed_identifications is not None:
                    allowed_identification_keys = {
                        freeze_bucket_index_value(identification)
                        for identification in self._allowed_identifications
                    }
                    manager_combinations = [
                        manager
                        for manager in self._manager_combinations(current_combinations)
                        if freeze_bucket_index_value(manager.identification)
                        in allowed_identification_keys
                    ]
                    current_combinations = self._manager_identifications(
                        manager_combinations
                    )
                sort_key = self._normalized_sort_key()
                sort_terms = (
                    normalize_ordering(sort_key) if sort_key is not None else ()
                )
                needs_manager_access = self._filters_or_sort_require_manager_access(
                    sorted_filters
                )

                if needs_manager_access:
                    if manager_combinations is None:
                        manager_combinations = self._manager_combinations(
                            current_combinations
                        )
                    manager_combinations = self._filter_prop_combinations(
                        manager_combinations,
                        sorted_filters["prop_filters"],
                        sorted_filters["prop_excludes"],
                    )
                    manager_combinations = self._apply_call_groups(manager_combinations)
                    if sort_terms:
                        manager_combinations = sort_items(
                            manager_combinations, sort_terms
                        )
                    identifications = self._manager_identifications(
                        manager_combinations
                    )
                else:
                    identifications = current_combinations
                    if sort_terms:
                        identifications = self._sort_dict_combinations(
                            identifications,
                            sort_terms,
                        )

                self._data = identifications

        return self._data

    def topological_sort_inputs(self) -> List[str]:
        """
        Produce a dependency-respecting order of input fields.

        This framework helper includes every configured input name and orders
        dependencies before the inputs that depend on them.

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
        self,
        key_name: str,
        input_field: Input[type[object]],
        current_combo: Combination,
    ) -> Union[Iterable[object], Bucket["GeneralManager"], None]:
        # Retrieve possible values
        """
        Resolve potential values for an input field based on the current partial input combination.

        This framework helper resolves static, callable, domain, iterable, or
        bucket-backed ``possible_values`` for one input. Optional inputs with no
        possible-values source return ``None``; required inputs without a valid
        iterable, domain, or bucket source raise ``InvalidPossibleValuesError``.

        Parameters:
            key_name (str): Name of the input field used for error context.
            input_field (Input): Input definition that may include `possible_values` and `depends_on`.
            current_combo (dict): Partial mapping of already-selected input values required to evaluate dependencies.

        Returns:
            Iterable[object] | Bucket[GeneralManager] | None: An iterable of allowed values for the input, a Bucket supplying candidate values, or ``None`` when an optional input has no explicit domain.

        Raises:
            InvalidPossibleValuesError: If the input field's `possible_values` is neither callable nor an iterable/Bucket.
        """
        self._ensure_as_of_compatible()
        possible_values = input_field.resolve_possible_values(
            current_combo,
            cache_context=(self._manager_class, key_name),
        )
        if possible_values is None:
            if input_field.required:
                raise InvalidPossibleValuesError(key_name)
            return None
        if isinstance(possible_values, InputDomain):
            possible_values = possible_values
        elif not isinstance(possible_values, (Iterable, Bucket)):
            raise InvalidPossibleValuesError(key_name)
        return possible_values

    def _iter_input_combinations(
        self,
        sorted_inputs: List[str],
        filters: ParsedFilters,
        excludes: ParsedFilters,
        *,
        snapshot_iterables: bool,
    ) -> Generator[Combination, None, None]:
        """
        Yield valid assignments of input fields satisfying filters and excludes.

        Parameters:
            sorted_inputs (list[str]): Input names in dependency-respecting order.
            filters (dict[str, dict]): Per-input filter definitions (may include `filter_funcs` or `filter_kwargs`).
            excludes (dict[str, dict]): Per-input exclusion definitions (may include `filter_funcs` or `filter_kwargs`).

        Yields:
            Combination: Completed input-to-value mappings that meet the
                filters and excludes.
        """

        def input_passes_filters(
            input_name: str,
            current_combo: Combination,
        ) -> bool:
            """Return whether the current input state satisfies input-level filters."""

            field_filters = filters.get(input_name, {})
            field_excludes = excludes.get(input_name, {})
            current_value = current_combo.get(input_name)

            for filter_func in field_filters.get("filter_funcs", []):
                if not filter_func(current_value):
                    return False
            for exclude_func in field_excludes.get("filter_funcs", []):
                if exclude_func(current_value):
                    return False
            return True

        def helper(
            index: int,
            current_combo: Combination,
        ) -> Generator[Combination, None, None]:
            """
            Recursively emit input combinations that satisfy filters and excludes.

            Parameters:
                index (int): Position within `sorted_inputs` currently being assigned.
                current_combo: Partial assignment of inputs built so far.

            Yields:
                Combination: Completed combination of input values.
            """
            if index == len(sorted_inputs):
                yield current_combo.copy()
                return
            input_name: str = sorted_inputs[index]
            input_field = self.input_fields[input_name]

            possible_values = self.get_possible_values(
                input_name, input_field, current_combo
            )
            if possible_values is None:
                if input_passes_filters(input_name, current_combo):
                    yield from helper(index + 1, current_combo)
                return

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
                        _build_exclude_filter(exclude_func), possible_values
                    )
                if snapshot_iterables:
                    possible_values = list(possible_values)

            for value in possible_values:
                if not isinstance(value, input_field.type):
                    continue
                current_combo[input_name] = value
                yield from helper(index + 1, current_combo)
                del current_combo[input_name]

        yield from helper(0, {})

    def _generate_input_combinations(
        self,
        sorted_inputs: List[str],
        filters: ParsedFilters,
        excludes: ParsedFilters,
    ) -> List[Combination]:
        """
        Generate all valid assignments of input fields that satisfy filters.

        Parameters:
            sorted_inputs (list[str]): Input names in dependency-respecting order.
            filters (dict[str, dict]): Per-input filter definitions.
            excludes (dict[str, dict]): Per-input exclusion definitions.

        Returns:
            list[Combination]: Completed input-to-value mappings that meet the
                filters and excludes.
        """
        return list(
            self._iter_input_combinations(
                sorted_inputs,
                filters,
                excludes,
                snapshot_iterables=True,
            )
        )

    def _iter_prop_filtered_identifications(
        self,
        combinations: Iterable[Combination],
        prop_filters: ParsedFilters,
        prop_excludes: ParsedFilters,
        allowed_identification_keys: set[Hashable] | None = None,
    ) -> Generator[Combination, None, None]:
        """
        Lazily apply property filters and yield manager identifications.

        This mirrors the property-filter materialization path used by
        :meth:`generate_combinations`, but lets ``__str__`` stop after enough
        matching combinations have been found.
        """
        for combo in combinations:
            manager = self._manager_class(**combo)
            if (
                allowed_identification_keys is not None
                and freeze_bucket_index_value(manager.identification)
                not in allowed_identification_keys
            ):
                continue
            if self._apply_call_groups(
                self._filter_prop_combinations([manager], prop_filters, prop_excludes)
            ):
                yield manager.identification

    def _filter_prop_combinations(
        self,
        manager_combinations: list[GeneralManagerType],
        prop_filters: ParsedFilters,
        prop_excludes: ParsedFilters,
    ) -> list[GeneralManagerType]:
        """
        Apply property-level filters and excludes to manager combinations.

        Parameters:
            manager_combinations (list[GeneralManagerType]): Managers built from
                input combinations already passing input filters.
            prop_filters: Filter definitions keyed by property name.
            prop_excludes: Exclude definitions keyed by property name.

        Returns:
            list[GeneralManagerType]: Manager instances that satisfy property
            constraints.
        """

        prop_filter_needed = set(prop_filters.keys()) | set(prop_excludes.keys())
        if not prop_filter_needed:
            return manager_combinations

        # Apply property filters and exclusions
        filtered_combos: list[GeneralManagerType] = []
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
    ) -> GeneralManagerType | Bucket[GeneralManagerType]:
        """
        Retrieve a manager instance or subset of combinations.

        Parameters:
            item (int | slice): Index or slice specifying which combinations to return.

        Returns:
            GeneralManagerType | CalculationBucket[GeneralManagerType]:
                Manager instance for single indices or bucket wrapping the sliced combinations.
        """
        self._ensure_as_of_compatible()
        items = self.generate_combinations()
        result = items[item]
        if isinstance(result, list):
            from general_manager.bucket._materialized_bucket import MaterializedBucket

            return MaterializedBucket(
                self._manager_class,
                tuple(self._manager_class(**combination) for combination in result),
                snapshot=self._effective_search_date,
            )
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

    def get(self, **kwargs: object) -> GeneralManagerType:
        """
        Return the single manager instance that matches the provided field filters.

        Parameters:
            **kwargs: Field filters to apply when selecting a calculation (e.g., property or input names mapped to expected values).

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

    def sort(self, *fields: str) -> CalculationBucket[GeneralManagerType]:
        """
        Create a new CalculationBucket configured to order generated combinations.

        Sorting by raw input keys happens before managers are built. Sorting by
        computed properties builds manager instances and reads the named
        attributes. Each signed field controls its own direction, and null
        values remain last in either direction. Declared paths are validated
        before materialization.

        Parameters:
            fields: Signed attribute names; prefix a field with ``-`` for
                descending order.

        Returns:
            A new CalculationBucket configured with the supplied ordering.
        """
        self._ensure_as_of_compatible()
        terms = normalize_ordering(fields)
        if not terms:
            return self.all()
        validate_ordering_fields(self._manager_class, terms)
        return self._derive(
            filter_definitions=self.filter_definitions,
            exclude_definitions=self.exclude_definitions,
            sort_fields=tuple(term.signed_field for term in terms),
            filter_call_groups=self._filter_call_groups,
            exclude_call_groups=self._exclude_call_groups,
        )

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """Group this bucket only when its snapshot matches the active context."""
        self._ensure_as_of_compatible()
        return super().group_by(*group_by_keys)

    def none(self) -> CalculationBucket[GeneralManagerType]:
        """
        Return an empty calculation bucket for the same manager class.

        The returned bucket starts from an ``all()`` copy, then clears cached
        data and raw/parsed filter and exclude definitions. It preserves the
        manager class and private signed ordering state.

        Returns:
            CalculationBucket[GeneralManagerType]: Bucket with no combinations
            and cleared filter/exclude state.
        """
        own = self.all()
        own._data = []
        own.filter_definitions = {}
        own.exclude_definitions = {}
        own._filters = {}
        own._excludes = {}
        own._filter_call_groups = ()
        own._exclude_call_groups = ()
        own._parsed_filter_call_groups = ()
        own._parsed_exclude_call_groups = ()
        own._allowed_identifications = []
        return own

    def with_instances(
        self,
        instances: Iterable[GeneralManagerType],
    ) -> Bucket[GeneralManagerType]:
        """Return the exact supplied instances without reconstruction."""
        self._ensure_as_of_compatible()
        from general_manager.bucket._materialized_bucket import MaterializedBucket

        selected = tuple(instances)
        for instance in selected:
            if instance.__class__ != self._manager_class:
                raise IncompatibleBucketTypeError(self.__class__, type(instance))
        return MaterializedBucket(
            self._manager_class, selected, snapshot=self._effective_search_date
        )
