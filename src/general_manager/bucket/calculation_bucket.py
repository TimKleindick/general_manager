"""Bucket implementation that enumerates calculation interface combinations."""

from __future__ import annotations
from collections.abc import Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
import struct
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
    Protocol,
)
from uuid import UUID
from operator import attrgetter
from copy import deepcopy
from general_manager.interface.base_interface import (
    generalManagerClassName,
    GeneralManagerType,
)
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.indexing import freeze_bucket_index_value
from general_manager.manager.input import (
    DateRangeDomain,
    Input,
    InputDomain,
    NumericRangeDomain,
)
from general_manager.utils.filter_parser import (
    FilterFunction,
    ParsedFilters,
    parse_filters,
)

if TYPE_CHECKING:
    from general_manager.api.property import GraphQLProperty
    from general_manager.manager.general_manager import GeneralManager


type Combination = dict[str, object]
type RawFilterDefinitions = dict[str, object]


@dataclass(frozen=True, slots=True)
class _TrustedToken:
    """Comparison-safe token for one exact immutable scalar value."""

    kind: str
    payload: object


class _EnumerationWitness(Protocol):
    """Validate that an enumerated candidate remains backed by its source."""

    def authorizes(self) -> bool: ...

    def track_membership_dependency(self) -> None: ...


class _StaticEnumerationWitness:
    """Base witness for sources with no external cache dependency to track."""

    __slots__ = ()

    def track_membership_dependency(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _SequenceEnumerationWitness(_StaticEnumerationWitness):
    """Prove that the exact sequence slot still contains the emitted object."""

    source: list[object] | tuple[object, ...]
    source_index: int
    candidate: object
    candidate_token: _TrustedToken

    def authorizes(self) -> bool:
        try:
            current = self.source[self.source_index]
        except IndexError:
            return False
        return (
            current is self.candidate
            and _trusted_candidate_token(current) == self.candidate_token
        )


@dataclass(frozen=True, slots=True)
class _SetEnumerationWitness(_StaticEnumerationWitness):
    """Prove that an emitted safe scalar remains in the exact set source."""

    source: set[object] | frozenset[object]
    candidate: object
    candidate_token: _TrustedToken

    def authorizes(self) -> bool:
        if _trusted_candidate_token(self.candidate) != self.candidate_token:
            return False
        try:
            return self.candidate in self.source
        except Exception:  # noqa: BLE001 - mutated sets must only revoke evidence
            return False


@dataclass(frozen=True, slots=True)
class _NumericRangeEnumerationWitness(_StaticEnumerationWitness):
    """Prove that an exact numeric range still has its immutable configuration."""

    source: NumericRangeDomain
    configuration: tuple[_TrustedToken, _TrustedToken, _TrustedToken]
    candidate: int | float

    def authorizes(self) -> bool:
        configuration = _numeric_range_configuration(self.source)
        return configuration == self.configuration


@dataclass(frozen=True, slots=True)
class _DateRangeEnumerationWitness(_StaticEnumerationWitness):
    """Prove that an exact date range still has its immutable configuration."""

    source: DateRangeDomain
    configuration: tuple[
        _TrustedToken,
        _TrustedToken,
        _TrustedToken,
        _TrustedToken,
    ]
    candidate: date

    def authorizes(self) -> bool:
        configuration = _date_range_configuration(self.source)
        return configuration == self.configuration


@dataclass(frozen=True, slots=True)
class _EnumerationEvidence:
    """Conservative proof that one candidate came from one static input source."""

    input_field: Input[type[object]]
    provider: object
    dependency_names: tuple[str, ...]
    dependency_tokens: tuple[_TrustedToken, ...]
    candidate_token: _TrustedToken
    witness: _EnumerationWitness

    def authorizes(
        self,
        input_field: Input[type[object]],
        value: object,
        identification: Mapping[str, object],
    ) -> bool:
        if input_field is not self.input_field:
            return False
        if input_field.possible_values is not self.provider:
            return False
        if type(input_field.depends_on) is not list:
            return False
        if not _trusted_dependency_names_match(
            input_field.depends_on,
            self.dependency_names,
        ):
            return False
        dependency_snapshot = _trusted_dependency_snapshot(
            self.dependency_names, identification
        )
        if dependency_snapshot != self.dependency_tokens:
            return False
        if _trusted_candidate_token(value) != self.candidate_token:
            return False
        return self.witness.authorizes()

    def track_membership_dependency(self) -> None:
        self.witness.track_membership_dependency()


def _trusted_candidate_token(value: object) -> _TrustedToken | None:
    """Return a comparison-safe token for an eligible exact scalar."""
    value_type = type(value)
    if value_type is bool:
        return _TrustedToken("bool", value)
    if value_type is int:
        return _TrustedToken("int", value)
    if value_type is float:
        return _TrustedToken("float", struct.pack("!d", value))
    if value_type is str:
        return _TrustedToken("str", value)
    if value_type is bytes:
        return _TrustedToken("bytes", value)
    if value_type is date:
        date_value = cast(date, value)
        return _TrustedToken("date", date_value.toordinal())
    if value_type is datetime:
        datetime_value = cast(datetime, value)
        if datetime_value.tzinfo is not None:
            return None
        return _TrustedToken(
            "datetime",
            (
                datetime_value.year,
                datetime_value.month,
                datetime_value.day,
                datetime_value.hour,
                datetime_value.minute,
                datetime_value.second,
                datetime_value.microsecond,
                datetime_value.fold,
            ),
        )
    if value_type is UUID:
        return _TrustedToken("uuid", cast(UUID, value).int)
    return None


def _trusted_dependency_snapshot(
    dependency_names: tuple[str, ...],
    identification: Mapping[str, object],
) -> tuple[_TrustedToken, ...] | None:
    """Tokenize declared dependency values without comparing arbitrary objects."""
    if type(identification) is not dict:
        return None
    tokens: list[_TrustedToken] = []
    for dependency_name in dependency_names:
        if type(dependency_name) is not str:
            return None
        try:
            dependency_value = identification[dependency_name]
        except KeyError:
            return None
        token = _trusted_candidate_token(dependency_value)
        if token is None:
            return None
        tokens.append(token)
    return tuple(tokens)


def _trusted_dependency_names_match(
    current_names: list[str],
    expected_names: tuple[str, ...],
) -> bool:
    """Compare dependency names only after proving every current value is a str."""
    if len(current_names) != len(expected_names):
        return False
    for current_name, expected_name in zip(current_names, expected_names, strict=True):
        if type(current_name) is not str or current_name != expected_name:
            return False
    return True


def _numeric_range_configuration(
    source: NumericRangeDomain,
) -> tuple[_TrustedToken, _TrustedToken, _TrustedToken] | None:
    """Return safe exact built-in configuration for a numeric range."""
    values = (source.min_value, source.max_value, source.step)
    if any(type(value) not in {int, float} for value in values):
        return None
    tokens = tuple(_trusted_candidate_token(value) for value in values)
    if any(token is None for token in tokens):
        return None
    return cast(
        tuple[_TrustedToken, _TrustedToken, _TrustedToken],
        tokens,
    )


def _date_range_configuration(
    source: DateRangeDomain,
) -> tuple[_TrustedToken, _TrustedToken, _TrustedToken, _TrustedToken] | None:
    """Return safe exact built-in configuration for a date range."""
    if (
        type(source.start) is not date
        or type(source.end) is not date
        or type(source.frequency) is not str
        or type(source.step) is not int
    ):
        return None
    tokens = tuple(
        _trusted_candidate_token(value)
        for value in (source.start, source.end, source.frequency, source.step)
    )
    if any(token is None for token in tokens):
        return None
    return cast(
        tuple[_TrustedToken, _TrustedToken, _TrustedToken, _TrustedToken],
        tokens,
    )


def _sequence_enumeration_witness(
    source: list[object] | tuple[object, ...],
    candidate: object,
    candidate_token: _TrustedToken,
    source_index: int | None,
) -> _SequenceEnumerationWitness | None:
    """Build an identity-and-position witness for an exact sequence."""
    if type(source_index) is not int or source_index < 0:
        return None
    try:
        source_candidate = source[source_index]
    except IndexError:
        return None
    if source_candidate is not candidate:
        return None
    if _trusted_candidate_token(source_candidate) != candidate_token:
        return None
    return _SequenceEnumerationWitness(
        source,
        source_index,
        candidate,
        candidate_token,
    )


def _set_enumeration_witness(
    source: set[object] | frozenset[object],
    candidate: object,
    candidate_token: _TrustedToken,
) -> _SetEnumerationWitness | None:
    """Build a constant-time membership witness for a safe exact set."""
    try:
        candidate_is_member = candidate in source
    except Exception:  # noqa: BLE001 - unsafe set contents make evidence ineligible
        return None
    if not candidate_is_member:
        return None
    return _SetEnumerationWitness(source, candidate, candidate_token)


def _numeric_range_enumeration_witness(
    source: NumericRangeDomain,
    candidate: object,
) -> _NumericRangeEnumerationWitness | None:
    """Build a witness for a safe candidate emitted by an exact numeric range."""
    configuration = _numeric_range_configuration(source)
    if configuration is None:
        return None
    expected_type = (
        float
        if any(
            type(value) is float
            for value in (source.min_value, source.max_value, source.step)
        )
        else int
    )
    if type(candidate) is not expected_type:
        return None
    numeric_candidate = cast(int | float, candidate)
    return _NumericRangeEnumerationWitness(
        source,
        configuration,
        numeric_candidate,
    )


def _date_range_enumeration_witness(
    source: DateRangeDomain,
    candidate: object,
) -> _DateRangeEnumerationWitness | None:
    """Build a witness for a safe candidate emitted by an exact date range."""
    configuration = _date_range_configuration(source)
    if configuration is None or type(candidate) is not date:
        return None
    date_candidate = candidate
    return _DateRangeEnumerationWitness(source, configuration, date_candidate)


def _trusted_enumeration_evidence(
    input_field: Input[type[object]],
    resolved_source: object,
    candidate: object,
    identification: dict[str, object],
    *,
    source_index: int | None = None,
) -> _EnumerationEvidence | None:
    """Build static-source evidence without resolving callable providers."""
    provider = input_field.possible_values
    if callable(provider):
        return None
    if type(input_field) is not Input:
        return None
    if any(
        override_name in input_field.__dict__
        for override_name in (
            "resolve_possible_values",
            "normalize",
            "cast",
            "_build_dependency_values",
        )
    ):
        return None
    if provider is not resolved_source:
        return None
    if type(input_field.depends_on) is not list:
        return None
    dependency_names = tuple(input_field.depends_on)
    dependency_tokens = _trusted_dependency_snapshot(
        dependency_names,
        identification,
    )
    if dependency_tokens is None:
        return None
    candidate_token = _trusted_candidate_token(candidate)
    if candidate_token is None:
        return None

    witness: _EnumerationWitness | None
    source_type = type(resolved_source)
    if source_type is list or source_type is tuple:
        witness = _sequence_enumeration_witness(
            cast(list[object] | tuple[object, ...], resolved_source),
            candidate,
            candidate_token,
            source_index,
        )
    elif source_type is set or source_type is frozenset:
        witness = _set_enumeration_witness(
            cast(set[object] | frozenset[object], resolved_source),
            candidate,
            candidate_token,
        )
    elif source_type is NumericRangeDomain:
        witness = _numeric_range_enumeration_witness(
            cast(NumericRangeDomain, resolved_source),
            candidate,
        )
    elif source_type is DateRangeDomain:
        witness = _date_range_enumeration_witness(
            cast(DateRangeDomain, resolved_source),
            candidate,
        )
    else:
        witness = None
    if witness is None:
        return None
    return _EnumerationEvidence(
        input_field=input_field,
        provider=provider,
        dependency_names=dependency_names,
        dependency_tokens=dependency_tokens,
        candidate_token=candidate_token,
        witness=witness,
    )


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

        properties = self._manager_class.Interface.get_graph_ql_properties()
        possible_values = self.transform_properties_to_input_fields(
            properties, self.input_fields
        )

        self._filters = parse_filters(self.filter_definitions, possible_values)
        self._excludes = parse_filters(self.exclude_definitions, possible_values)

        self._data: list[Combination] | None = None
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

    def __reduce__(self) -> generalManagerClassName | tuple[object, ...]:
        """
        Provide pickling support for calculation buckets.

        Returns:
            tuple[object, ...]: Reconstruction data representing the class, arguments, and state.
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

    def __setstate__(self, state: dict[str, object]) -> None:
        """
        Restore the bucket after unpickling.

        Parameters:
            state: Pickled state containing cached combination data.

        Returns:
            None
        """
        self._data = cast(list[Combination] | None, state.get("data"))

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Build a bucket from constraints common to this bucket and another operand.

        Parameters:
            other: A CalculationBucket or a GeneralManager instance to combine.
                If a same-class manager instance is given, it is first converted
                into an ``id__in=[identification]`` filter bucket.

        Returns:
            A new CalculationBucket containing only filter and exclude
            definitions that are present with equal values on both bucket
            operands. This is a compatibility-preserving common-constraint
            merge, not a set union of materialized calculation results.

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
        Return a compact preview of generated combinations.

        Cached buckets include the exact combination count. Uncached buckets avoid
        materializing all combinations for string formatting; when more than the
        preview limit exists, the count is reported as a lower-bound label.

        Returns:
            str: Human-readable summary of up to five combinations.
        """
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

        Sorted or reversed buckets use normal materialization so the preview
        reflects the final global ordering. Unsorted uncached buckets read at
        most ``limit + 1`` matching combinations and leave ``_data`` untouched.
        """
        if self._data is not None:
            return self._data[:limit], str(len(self._data)), len(self._data) > limit

        if self._normalized_sort_key() is not None or self.reverse:
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
            if sorted_filters["prop_filters"] or sorted_filters["prop_excludes"]:
                preview_iterator = self._iter_prop_filtered_identifications(
                    preview_iterator,
                    sorted_filters["prop_filters"],
                    sorted_filters["prop_excludes"],
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
            str: Debug string listing filters, excludes, sort key, and ordering.
        """
        return f"{self.__class__.__name__}({self._manager_class.__name__}, {self.filter_definitions}, {self.exclude_definitions}, {self.sort_key}, {self.reverse})"

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
        return CalculationBucket(
            manager_class=self._manager_class,
            filter_definitions={
                **self.filter_definitions.copy(),
                **kwargs,
            },
            exclude_definitions=self.exclude_definitions.copy(),
        )

    def exclude(self, **kwargs: object) -> CalculationBucket[GeneralManagerType]:
        """
        Add additional exclusion rules and return a new calculation bucket.

        Exclusion keys use the same lookup grammar and error behavior as
        :meth:`filter`; matching combinations are removed rather than kept.

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
        return CalculationBucket(
            manager_class=self._manager_class,
            filter_definitions=self.filter_definitions.copy(),
            exclude_definitions={
                **self.exclude_definitions.copy(),
                **kwargs,
            },
        )

    def all(self) -> CalculationBucket[GeneralManagerType]:
        """
        Return a deep copy of this calculation bucket.

        Returns:
            CalculationBucket[GeneralManagerType]: Independent copy that can be mutated without affecting the original.
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

        return {
            "prop_filters": prop_filters,
            "input_filters": input_filters,
            "prop_excludes": prop_excludes,
            "input_excludes": input_excludes,
        }

    def _normalized_sort_key(self) -> tuple[str, ...] | None:
        """Return the configured sort key as a tuple, or None when unsorted."""
        if self.sort_key is None:
            return None
        if isinstance(self.sort_key, str):
            return (self.sort_key,)
        return self.sort_key

    def _bucket_index_source_signature(self) -> Hashable:
        """Return a stable signature for equivalent calculation bucket plans."""
        return (
            "calculation",
            self._manager_class,
            freeze_bucket_index_value(self.filter_definitions),
            freeze_bucket_index_value(self.exclude_definitions),
            self._normalized_sort_key(),
            self.reverse,
        )

    def _sort_uses_only_inputs(self, sort_key: tuple[str, ...] | None) -> bool:
        """Return whether a sort can be applied to raw input dictionaries."""
        if sort_key is None:
            return True
        return all(key in self.input_fields for key in sort_key)

    def _sort_dict_combinations(
        self,
        combinations: list[Combination],
        sort_key: tuple[str, ...],
    ) -> list[Combination]:
        """
        Sort input dictionaries while tolerating missing optional inputs.

        Present values sort before missing values in ascending order. Missing
        keys use None as the explicit placeholder, guarded by a presence flag so
        they are not compared directly with concrete input values.
        """
        return sorted(
            combinations,
            key=lambda combo: tuple(
                (key not in combo, combo.get(key, None)) for key in sort_key
            ),
        )

    def _manager_combinations(
        self,
        combinations: list[Combination],
    ) -> list[GeneralManagerType]:
        """Instantiate managers for each raw input-combination dictionary."""
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
                sort_key = self._normalized_sort_key()
                needs_manager_access = (
                    bool(sorted_filters["prop_filters"])
                    or bool(sorted_filters["prop_excludes"])
                    or not self._sort_uses_only_inputs(sort_key)
                )

                if needs_manager_access:
                    manager_combinations = self._manager_combinations(
                        current_combinations
                    )
                    manager_combinations = self._filter_prop_combinations(
                        manager_combinations,
                        sorted_filters["prop_filters"],
                        sorted_filters["prop_excludes"],
                    )
                    if sort_key is not None:
                        getters = [attrgetter(key) for key in sort_key]
                        manager_combinations = sorted(
                            manager_combinations,
                            key=lambda manager_obj: tuple(
                                getter(manager_obj) for getter in getters
                            ),
                        )
                    identifications = self._manager_identifications(
                        manager_combinations
                    )
                else:
                    identifications = current_combinations
                    if sort_key is not None:
                        identifications = self._sort_dict_combinations(
                            identifications,
                            sort_key,
                        )

                if self.reverse:
                    identifications.reverse()
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
    ) -> Generator[Combination, None, None]:
        """
        Lazily apply property filters and yield manager identifications.

        This mirrors the property-filter materialization path used by
        :meth:`generate_combinations`, but lets ``__str__`` stop after enough
        matching combinations have been found.
        """
        for combo in combinations:
            manager = self._manager_class(**combo)
            if self._filter_prop_combinations([manager], prop_filters, prop_excludes):
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

    def sort(
        self, key: str | tuple[str], reverse: bool = False
    ) -> CalculationBucket[GeneralManagerType]:
        """
        Create a new CalculationBucket configured to order generated combinations by the given attribute key.

        Sorting by raw input keys happens before managers are built. Sorting by
        computed properties builds manager instances and reads the named
        attributes. Missing attributes raise ``AttributeError`` when the bucket
        materializes; incomparable values raise ``TypeError`` from Python's
        sort. Exceptions raised by computed properties propagate unchanged.

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
        Return an empty calculation bucket for the same manager class.

        The returned bucket starts from an ``all()`` copy, then clears cached
        data and raw/parsed filter and exclude definitions. It preserves the
        manager class, sort key, and reverse flag.

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
        return own
