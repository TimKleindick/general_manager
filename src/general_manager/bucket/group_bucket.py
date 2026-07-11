"""Grouping bucket implementation for aggregating GeneralManager instances."""

from __future__ import annotations
from collections.abc import Callable, Generator, Hashable, Iterable, Mapping
from typing import Generic, cast
from general_manager.manager.group_manager import GroupManager
from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import Bucket, GeneralManagerType
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import Dependency
from general_manager.cache.run_context import current_calculation_run_context

type GroupLookup = dict[str, object]
type GroupByValue = tuple[tuple[str, object], ...]
type GroupIdentity = tuple[tuple[str, Hashable], ...]


def _freeze_group_value(value: object) -> Hashable:
    """Return a hashable identity for values used to distinguish groups."""
    if isinstance(value, GeneralManager):
        return (
            value.__class__,
            tuple(
                sorted(
                    (
                        key,
                        _freeze_group_value(identifier),
                    )
                    for key, identifier in value.identification.items()
                )
            ),
        )
    if isinstance(value, dict):
        return tuple(
            sorted(
                (
                    (
                        _freeze_group_value(key),
                        _freeze_group_value(item),
                    )
                    for key, item in value.items()
                ),
                key=repr,
            ),
        )
    if isinstance(value, Mapping):
        return tuple(
            sorted(
                (
                    (
                        _freeze_group_value(key),
                        _freeze_group_value(item),
                    )
                    for key, item in value.items()
                ),
                key=repr,
            ),
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_group_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_group_value(item) for item in value)
    return value


def _group_filter_kwargs(
    manager_class: type[GeneralManagerType],
    group_by_value: GroupByValue,
    attributes: Mapping[str, Mapping[str, object]] | None = None,
) -> GroupLookup:
    """Translate grouped manager values into backend-neutral relation lookups."""
    filters: GroupLookup = {}
    if attributes is None:
        get_attribute_types = getattr(
            manager_class.Interface,
            "get_attribute_types",
            None,
        )
        attributes = get_attribute_types() if callable(get_attribute_types) else {}
    for key, value in group_by_value:
        attribute_info = cast(Mapping[str, object], attributes.get(key, {}))
        filter_key = attribute_info.get("filter_lookup", key)
        if isinstance(value, GeneralManager):
            filters.update(
                {
                    f"{filter_key}__{identifier_key}": identifier_value
                    for identifier_key, identifier_value in value.identification.items()
                }
            )
            continue
        filters[str(filter_key)] = value
    return filters


def _restore_materialized_group_bucket(
    manager_class: type[GeneralManagerType],
    values: tuple[GeneralManagerType, ...],
    dependencies: frozenset[Dependency],
    allow_group_materialization: bool = True,
) -> _MaterializedGroupBucket[GeneralManagerType]:
    """Restore a private materialized bucket from its pickle payload."""
    return _MaterializedGroupBucket(
        manager_class,
        values,
        dependencies,
        allow_group_materialization=allow_group_materialization,
    )


def _restore_group_bucket(
    manager_class: type[GeneralManagerType],
    group_by_keys: tuple[str, ...],
    groups: tuple[
        tuple[
            dict[str, object],
            tuple[GeneralManagerType, ...],
            frozenset[Dependency],
        ],
        ...,
    ],
    basis_data: Bucket[GeneralManagerType],
    dependencies: frozenset[Dependency],
    materialized: bool,
) -> GroupBucket[GeneralManagerType]:
    """Restore a grouped view without regrouping its already partitioned data."""
    restored_groups = [
        GroupManager(
            manager_class,
            group_by_value,
            _MaterializedGroupBucket(manager_class, values, group_dependencies),
        )
        for group_by_value, values, group_dependencies in groups
    ]
    return GroupBucket._from_grouped_data(
        manager_class,
        group_by_keys,
        restored_groups,
        basis_data=basis_data,
        captured_dependencies=dependencies,
        materialized=materialized,
    )


def _restore_no_materialization_bucket(
    source: Bucket[GeneralManagerType],
    manager_class: type[GeneralManagerType],
) -> _NoMaterializationBucket[GeneralManagerType]:
    """Restore a live-basis wrapper used by union and nested grouping fallbacks."""
    return _NoMaterializationBucket(source, manager_class)


class _MaterializedGroupBucket(Bucket[GeneralManagerType]):
    """In-memory bucket view for one already-partitioned group."""

    _group_materialization_safe = True

    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        values: Iterable[GeneralManagerType],
        dependencies: Iterable[Dependency] = (),
        fallback_factory: Callable[[], Bucket[GeneralManagerType]] | None = None,
        allow_group_materialization: bool = True,
    ) -> None:
        super().__init__(manager_class)
        self._values = tuple(values)
        self._data = self._values
        self._dependencies = frozenset(dependencies)
        self._fallback_factory = fallback_factory
        self._allow_group_materialization = allow_group_materialization

    def _replay_dependencies(self) -> None:
        DependencyTracker._track_many_validated(self._dependencies)

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        return (
            _restore_materialized_group_bucket,
            (
                self._manager_class,
                self._values,
                self._dependencies,
                self._allow_group_materialization,
            ),
        )

    def _fallback(
        self, operation: str, kwargs: Mapping[str, object]
    ) -> Bucket[GeneralManagerType] | None:
        if self._fallback_factory is None:
            return None
        return cast(
            Bucket[GeneralManagerType],
            getattr(self._fallback_factory(), operation)(**kwargs),
        )

    @staticmethod
    def _supports_local_lookup(kwargs: Mapping[str, object]) -> bool:
        return all("__" not in key for key in kwargs)

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        if isinstance(other, _MaterializedGroupBucket):
            other._replay_dependencies()
            return _MaterializedGroupBucket(
                self._manager_class,
                (*self._values, *other._values),
                self._dependencies | other._dependencies,
                self._fallback_factory,
                False,
            )
        if isinstance(other, self._manager_class):
            return _MaterializedGroupBucket(
                self._manager_class,
                (*self._values, other),
                self._dependencies,
                self._fallback_factory,
                False,
            )
        raise TypeError

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        self._replay_dependencies()
        yield from self._values

    def filter(self, **kwargs: object) -> _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        if not self._supports_local_lookup(kwargs):
            fallback = self._fallback("filter", kwargs)
            if fallback is not None:
                return fallback  # type: ignore[return-value]
        return _MaterializedGroupBucket(
            self._manager_class,
            (
                value
                for value in self._values
                if all(
                    getattr(value, key) == expected for key, expected in kwargs.items()
                )
            ),
            self._dependencies,
            self._fallback_factory,
            self._allow_group_materialization,
        )

    def exclude(self, **kwargs: object) -> _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        if not kwargs:
            return self
        if not self._supports_local_lookup(kwargs):
            fallback = self._fallback("exclude", kwargs)
            if fallback is not None:
                return fallback  # type: ignore[return-value]
        return _MaterializedGroupBucket(
            self._manager_class,
            (
                value
                for value in self._values
                if not all(
                    getattr(value, key) == expected for key, expected in kwargs.items()
                )
            ),
            self._dependencies,
            self._fallback_factory,
            self._allow_group_materialization,
        )

    def first(self) -> GeneralManagerType | None:
        self._replay_dependencies()
        return self._values[0] if self._values else None

    def last(self) -> GeneralManagerType | None:
        self._replay_dependencies()
        return self._values[-1] if self._values else None

    def count(self) -> int:
        self._replay_dependencies()
        return len(self._values)

    def all(self) -> _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        return self

    def get(self, **kwargs: object) -> GeneralManagerType:
        if (
            not self._supports_local_lookup(kwargs)
            and self._fallback_factory is not None
        ):
            return self._fallback_factory().get(**kwargs)
        matches = tuple(self.filter(**kwargs))
        if len(matches) != 1:
            raise LookupError
        return matches[0]

    def __getitem__(
        self,
        item: int | slice,
    ) -> GeneralManagerType | _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        if isinstance(item, slice):
            return _MaterializedGroupBucket(
                self._manager_class,
                self._values[item],
                self._dependencies,
                self._fallback_factory,
                self._allow_group_materialization,
            )
        return self._values[item]

    def __len__(self) -> int:
        self._replay_dependencies()
        return len(self._values)

    def __contains__(self, item: GeneralManagerType) -> bool:
        self._replay_dependencies()
        return item in self._values

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        keys = (key,) if isinstance(key, str) else key
        return _MaterializedGroupBucket(
            self._manager_class,
            sorted(
                self._values,
                key=lambda value: tuple(getattr(value, name) for name in keys),
                reverse=reverse,
            ),
            self._dependencies,
            self._fallback_factory,
            self._allow_group_materialization,
        )

    def none(self) -> _MaterializedGroupBucket[GeneralManagerType]:
        self._replay_dependencies()
        return _MaterializedGroupBucket(
            self._manager_class,
            (),
            self._dependencies,
            self._fallback_factory,
            self._allow_group_materialization,
        )


class _NoMaterializationBucket(Bucket[GeneralManagerType]):
    """Delegate to a live bucket while disabling grouped snapshot construction."""

    _group_materialization_safe = False

    def __init__(
        self,
        source: Bucket[GeneralManagerType],
        manager_class: type[GeneralManagerType] | None = None,
    ) -> None:
        super().__init__(
            manager_class if manager_class is not None else source._manager_class
        )
        self._source = source

    def __or__(
        self, other: Bucket[GeneralManagerType] | GeneralManagerType
    ) -> Bucket[GeneralManagerType]:
        return _NoMaterializationBucket(self._source | other, self._manager_class)

    def __reduce__(self) -> tuple[object, tuple[object, ...]]:
        return (
            _restore_no_materialization_bucket,
            (self._source, self._manager_class),
        )

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        yield from self._source

    def filter(self, **kwargs: object) -> Bucket[GeneralManagerType]:
        return self._source.filter(**kwargs)

    def exclude(self, **kwargs: object) -> Bucket[GeneralManagerType]:
        return self._source.exclude(**kwargs)

    def first(self) -> GeneralManagerType | None:
        return self._source.first()

    def last(self) -> GeneralManagerType | None:
        return self._source.last()

    def count(self) -> int:
        return self._source.count()

    def all(self) -> Bucket[GeneralManagerType]:
        return _NoMaterializationBucket(self._source.all(), self._manager_class)

    def get(self, **kwargs: object) -> GeneralManagerType:
        return self._source.get(**kwargs)

    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | Bucket[GeneralManagerType]:
        value = self._source[item]
        return (
            _NoMaterializationBucket(value, self._manager_class)
            if isinstance(value, Bucket)
            else value
        )

    def __len__(self) -> int:
        return len(self._source)

    def __contains__(self, item: GeneralManagerType) -> bool:
        return item in self._source

    def sort(
        self, key: tuple[str] | str, reverse: bool = False
    ) -> Bucket[GeneralManagerType]:
        return _NoMaterializationBucket(
            self._source.sort(key, reverse), self._manager_class
        )


class InvalidGroupByKeyTypeError(TypeError):
    """Raised when a non-string value is provided as a group-by key."""

    def __init__(self) -> None:
        """
        Error raised when a non-string group-by key is provided.

        Initializes the exception with the message "groupBy() arguments must be strings."
        """
        super().__init__("groupBy() arguments must be strings.")


class UnknownGroupByKeyError(ValueError):
    """Raised when a group-by key does not exist on the manager interface."""

    def __init__(self, manager_name: str) -> None:
        """
        Create an UnknownGroupByKeyError indicating a missing attribute on a manager.

        Parameters:
            manager_name (str): Name of the manager whose attributes were expected; used to format the error message.
        """
        super().__init__(f"groupBy() arguments must be attributes of {manager_name}.")


class GroupBucketTypeMismatchError(TypeError):
    """Raised when attempting to merge grouping buckets of different types."""

    def __init__(self, first_type: type, second_type: type) -> None:
        """
        Initialize the error for attempting to combine two incompatible bucket types.

        Parameters:
            first_type (type): The first type involved in the attempted combination.
            second_type (type): The second type involved in the attempted combination.

        Notes:
            The exception message is formatted as "Cannot combine {first_type.__name__} with {second_type.__name__}."
        """
        super().__init__(
            f"Cannot combine {first_type.__name__} with {second_type.__name__}."
        )


class GroupBucketManagerMismatchError(ValueError):
    """Raised when grouping buckets track different manager classes."""

    def __init__(self, first_manager: type, second_manager: type) -> None:
        """
        Initialize the exception indicating two group buckets track different manager classes.

        Parameters:
            first_manager (type): The first manager class involved in the mismatch.
            second_manager (type): The second manager class involved in the mismatch.
        """
        super().__init__(
            f"Cannot combine buckets for {first_manager.__name__} and {second_manager.__name__}."
        )


class GroupBucketKeysMismatchError(ValueError):
    """Raised when grouping buckets use different grouping keys."""

    def __init__(
        self, first_keys: tuple[str, ...], second_keys: tuple[str, ...]
    ) -> None:
        """Initialize the mismatch error with both grouping key tuples."""
        super().__init__(f"Cannot combine group keys {first_keys} and {second_keys}.")


class GroupItemNotFoundError(ValueError):
    """Raised when a grouped manager matching the provided criteria cannot be found."""

    def __init__(self, manager_name: str, criteria: Mapping[str, object]) -> None:
        """
        Initialize an error indicating a grouped manager matching the provided lookup criteria could not be found.

        Parameters:
            manager_name (str): Name of the manager type searched for.
            criteria: Lookup criteria used to locate the manager; included in the error message.
        """
        super().__init__(f"Cannot find {manager_name} with {criteria}.")


class EmptyGroupBucketSliceError(ValueError):
    """Raised when slicing a group bucket yields no results."""

    def __init__(self) -> None:
        """
        Initialize the EmptyGroupBucketSliceError indicating that slicing a GroupBucket produced no results.

        The exception carries the message "Cannot slice an empty GroupBucket."
        """
        super().__init__("Cannot slice an empty GroupBucket.")


class InvalidGroupBucketIndexError(TypeError):
    """Raised when a group bucket is indexed with an unsupported type."""

    def __init__(self, received_type: type) -> None:
        """
        Initialize the exception for an unsupported GroupBucket index argument type.

        Parameters:
            received_type (type): The actual type that was passed as the index; used to construct the error message.
        """
        super().__init__(
            f"Invalid argument type: {received_type}. Expected int or slice."
        )


def _supports_materialized_group_data(data: object) -> bool:
    """Return whether retaining hydrated group partitions is semantically safe."""
    if getattr(data, "_allow_group_materialization", True) is False:
        return False
    if isinstance(data, (list, tuple)):
        return True
    if bool(getattr(data, "_group_materialization_safe", False)):
        return True
    from general_manager.bucket.calculation_bucket import CalculationBucket
    from general_manager.bucket.database_bucket import DatabaseBucket
    from general_manager.bucket.request_bucket import RequestBucket

    if type(data) is RequestBucket:
        return bool(getattr(data, "_materialized", False))
    if type(data) in (DatabaseBucket, CalculationBucket):
        return current_calculation_run_context() is not None
    return False


class GroupBucket(Generic[GeneralManagerType]):
    """Bucket variant that groups managers by specified attributes."""

    @classmethod
    def _from_grouped_data(
        cls,
        manager_class: type[GeneralManagerType],
        group_by_keys: tuple[str, ...],
        groups: list[GroupManager[GeneralManagerType]],
        *,
        basis_data: Bucket[GeneralManagerType],
        captured_dependencies: frozenset[Dependency] = frozenset(),
        materialized: bool = True,
    ) -> GroupBucket[GeneralManagerType]:
        """Build a grouped view from existing partitions without reading its basis."""
        bucket = object.__new__(cls)
        bucket._manager_class = manager_class
        bucket.filters = {}
        bucket.excludes = {}
        bucket.__check_group_by_arguments(group_by_keys)
        bucket._group_by_keys = group_by_keys
        bucket._materialized = materialized
        bucket._captured_dependencies = captured_dependencies
        bucket._data = groups
        bucket._basis_data = basis_data
        return bucket

    def __init__(
        self,
        manager_class: type[GeneralManagerType],
        group_by_keys: tuple[str, ...],
        data: Bucket[GeneralManagerType],
    ) -> None:
        """
        Build a grouping bucket from the provided base data.

        Parameters:
            manager_class (type[GeneralManagerType]): GeneralManager subclass represented by the bucket.
            group_by_keys (tuple[str, ...]): Attribute names used to define each group.
            data (Bucket[GeneralManagerType]): Source bucket whose entries are grouped.

        Returns:
            None

        Raises:
            TypeError: If a group-by key is not a string.
            ValueError: If a group-by key is not a valid manager attribute.
        """
        self._manager_class = manager_class
        self.filters: GroupLookup = {}
        self.excludes: GroupLookup = {}
        self.__check_group_by_arguments(group_by_keys)
        self._group_by_keys = group_by_keys
        self._materialized = _supports_materialized_group_data(data)
        self._captured_dependencies: frozenset[Dependency] = frozenset()
        self._data: list[GroupManager[GeneralManagerType]] = (
            self.__build_grouped_manager(data)
        )
        self._basis_data: Bucket[GeneralManagerType] = data

    def __eq__(self, other: object) -> bool:
        """
        Compare by unordered group set, manager class, and grouping keys.

        Parameters:
            other (object): Object compared against the current bucket.

        Returns:
            bool: True when both buckets contain the same set of groups and use
                the same manager class and grouping-key tuple. Group order is
                not part of equality.
        """
        if not isinstance(other, self.__class__):
            return False
        return (
            set(self._data) == set(other._data)
            and self._manager_class == other._manager_class
            and self._group_by_keys == other._group_by_keys
        )

    def __check_group_by_arguments(self, group_by_keys: tuple[str, ...]) -> None:
        """
        Validate that each provided group-by key is a string and is exposed by the manager interface.

        Parameters:
            group_by_keys (tuple[str, ...]): Attribute names to use for grouping.

        Raises:
            InvalidGroupByKeyTypeError: If any element of `group_by_keys` is not a string.
            UnknownGroupByKeyError: If any key is not listed in the manager class's interface attributes.
        """
        if not all(isinstance(arg, str) for arg in group_by_keys):
            raise InvalidGroupByKeyTypeError()
        if not all(
            arg in self._manager_class.Interface.get_attributes()
            for arg in group_by_keys
        ):
            raise UnknownGroupByKeyError(self._manager_class.__name__)

    def __build_grouped_manager(
        self,
        data: Bucket[GeneralManagerType],
    ) -> list[GroupManager[GeneralManagerType]]:
        """
        Builds a GroupManager for each distinct combination of configured group-by attribute values.

        Parameters:
            data (Bucket[GeneralManagerType]): Source bucket whose entries are partitioned by the bucket's configured group-by keys.

        Returns:
            list[GroupManager[GeneralManagerType]]: A list of GroupManager objects, one per unique tuple of group-by key values; groups are produced in order sorted by the string representation of their key tuples.
        """
        group_partitions: dict[
            GroupIdentity,
            tuple[GroupByValue, list[GeneralManagerType]],
        ] = {}

        def partition_entries() -> None:
            for entry in data:
                group_by_value: GroupByValue = tuple(
                    (arg, getattr(entry, arg)) for arg in self._group_by_keys
                )
                group_identity: GroupIdentity = tuple(
                    (arg, _freeze_group_value(value)) for arg, value in group_by_value
                )
                partition = group_partitions.get(group_identity)
                if partition is None:
                    partition = (group_by_value, [])
                    group_partitions[group_identity] = partition
                partition[1].append(entry)

        if self._materialized:
            with DependencyTracker() as captured_dependencies:
                partition_entries()
            self._captured_dependencies = frozenset(captured_dependencies)
        else:
            partition_entries()

        group_by_values = {
            identity: partition[0] for identity, partition in group_partitions.items()
        }

        get_attribute_types = getattr(
            self._manager_class.Interface,
            "get_attribute_types",
            None,
        )
        attributes = get_attribute_types() if callable(get_attribute_types) else {}

        groups: list[GroupManager[GeneralManagerType]] = []
        for group_by_value in sorted(group_by_values.values(), key=str):
            group_by_dict = {key: value for key, value in group_by_value}
            group_identity = tuple(
                (arg, _freeze_group_value(value)) for arg, value in group_by_value
            )
            grouped_manager_objects: Bucket[GeneralManagerType]
            if self._materialized:

                def fallback_factory(
                    source: Bucket[GeneralManagerType] = data,
                    value: GroupByValue = group_by_value,
                    attribute_info: Mapping[str, Mapping[str, object]] = attributes,
                ) -> Bucket[GeneralManagerType]:
                    return source.filter(
                        **_group_filter_kwargs(
                            self._manager_class, value, attribute_info
                        )
                    )

                grouped_manager_objects = _MaterializedGroupBucket(
                    self._manager_class,
                    group_partitions[group_identity][1],
                    self._captured_dependencies,
                    fallback_factory,
                )
            else:
                grouped_manager_objects = data.filter(
                    **_group_filter_kwargs(
                        self._manager_class, group_by_value, attributes
                    )
                )
            groups.append(
                GroupManager(
                    self._manager_class, group_by_dict, grouped_manager_objects
                )
            )
        return groups

    def __or__(self, other: object) -> GroupBucket[GeneralManagerType]:
        """
        Return a new GroupBucket representing the union of this bucket and another compatible GroupBucket.

        Parameters:
            other (GroupBucket): The grouping bucket to merge with this one.

        Returns:
            GroupBucket[GeneralManagerType]: A GroupBucket with the same manager class and grouping keys whose basis data is the union of both inputs.

        Raises:
            GroupBucketTypeMismatchError: If `other` is not a GroupBucket of the same class.
            GroupBucketManagerMismatchError: If `other` tracks a different manager class.
            GroupBucketKeysMismatchError: If `other` uses different grouping keys.
        """
        if not isinstance(other, self.__class__):
            raise GroupBucketTypeMismatchError(self.__class__, type(other))
        if self._manager_class != other._manager_class:
            raise GroupBucketManagerMismatchError(
                self._manager_class, other._manager_class
            )
        if self._group_by_keys != other._group_by_keys:
            raise GroupBucketKeysMismatchError(
                self._group_by_keys,
                other._group_by_keys,
            )
        union_basis = self._basis_data | other._basis_data
        if self._materialized or other._materialized:
            union_basis = _NoMaterializationBucket(union_basis, self._manager_class)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            union_basis,
        )

    def __reduce__(self) -> str | tuple[object, ...]:
        """
        Provide pickling support with constructor and current basis data.

        Returns:
            tuple[object, ...]: ``(GroupBucket, (manager_class, group_by_keys,
                basis_data))``, allowing unpickling to rebuild groups from the
                stored basis bucket.
        """
        if not self._materialized:
            return (
                self.__class__,
                (self._manager_class, self._group_by_keys, self._basis_data),
            )
        return (
            _restore_group_bucket,
            (
                self._manager_class,
                self._group_by_keys,
                tuple(
                    (
                        manager._group_by_value,
                        tuple(manager._data),
                        frozenset(
                            getattr(
                                manager._data,
                                "_dependencies",
                                self._captured_dependencies,
                            )
                        ),
                    )
                    for manager in self._data
                ),
                self._basis_data,
                self._captured_dependencies,
                self._materialized,
            ),
        )

    def __iter__(self) -> Generator[GroupManager[GeneralManagerType], None, None]:
        """
        Iterate over the grouped managers produced by this bucket.

        Yields:
            GroupManager[GeneralManagerType]: Individual group manager instances.
        """
        self._replay_dependencies()
        yield from self._data

    def _replay_dependencies(self) -> None:
        DependencyTracker._track_many_validated(self._captured_dependencies)

    def filter(self, **kwargs: object) -> GroupBucket[GeneralManagerType]:
        """
        Return a grouped bucket filtered by the provided lookups.

        Parameters:
            **kwargs: Field lookups evaluated against the underlying bucket.

        Returns:
            GroupBucket[GeneralManagerType]: Grouped bucket containing only matching records.
        """
        new_basis_data = self._basis_data.filter(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def exclude(self, **kwargs: object) -> GroupBucket[GeneralManagerType]:
        """
        Return a grouped bucket that excludes records matching the provided lookups.

        Parameters:
            **kwargs: Field lookups whose matches should be removed from the underlying bucket.

        Returns:
            GroupBucket[GeneralManagerType]: Grouped bucket built from the filtered base data.
        """
        new_basis_data = self._basis_data.exclude(**kwargs)
        return GroupBucket(
            self._manager_class,
            self._group_by_keys,
            new_basis_data,
        )

    def first(self) -> GroupManager[GeneralManagerType] | None:
        """
        Return the first grouped manager in the collection.

        Returns:
            GroupManager[GeneralManagerType] | None: First group when available.
        """
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> GroupManager[GeneralManagerType] | None:
        """
        Return the last grouped manager in the collection.

        Returns:
            GroupManager[GeneralManagerType] | None: Last group when available.
        """
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        """
        Count the number of grouped managers in the bucket.

        Returns:
            int: Number of groups.
        """
        return sum(1 for _ in self)

    def all(self) -> GroupBucket[GeneralManagerType]:
        """
        Return the current grouping bucket.

        Returns:
            GroupBucket[GeneralManagerType]: This instance.
        """
        return self

    def get(self, **kwargs: object) -> GroupManager[GeneralManagerType]:
        """
        Retrieve the first GroupManager matching the provided lookups.

        Parameters:
            **kwargs: Field lookups used to filter the grouped managers.

        Returns:
            The first matching GroupManager.

        Raises:
            GroupItemNotFoundError: If no grouped manager matches the filters.
        """
        first_value = self.filter(**kwargs).first()
        if first_value is None:
            raise GroupItemNotFoundError(self._manager_class.__name__, kwargs)
        return first_value

    def __getitem__(
        self, item: int | slice
    ) -> GroupManager[GeneralManagerType] | GroupBucket[GeneralManagerType]:
        """
        Retrieve a single grouped manager by index or construct a new GroupBucket from a slice of groups.

        Parameters:
            item (int | slice): Integer index to select a single GroupManager, or a slice to select a subsequence of groups.

        Returns:
            GroupManager[GeneralManagerType] if `item` is an int, otherwise a GroupBucket[GeneralManagerType] built from the selected groups.

        Raises:
            EmptyGroupBucketSliceError: If the slice selects no groups.
            InvalidGroupBucketIndexError: If `item` is not an int or slice.
        """
        if isinstance(item, int):
            return self._data[item]
        elif isinstance(item, slice):
            new_data = self._data[item]
            if self._materialized:
                if not new_data:
                    raise EmptyGroupBucketSliceError()
                selected_values = tuple(
                    entry for manager in new_data for entry in manager._data
                )

                def selected_fallback() -> Bucket[GeneralManagerType]:
                    selected_basis: Bucket[GeneralManagerType] | None = None
                    for manager in new_data:
                        group_data = manager._data
                        fallback_factory = getattr(
                            group_data, "_fallback_factory", None
                        )
                        if callable(fallback_factory):
                            group_basis = cast(
                                Bucket[GeneralManagerType], fallback_factory()
                            )
                        else:
                            group_basis = self._basis_data.filter(
                                **_group_filter_kwargs(
                                    self._manager_class,
                                    tuple(manager._group_by_value.items()),
                                )
                            )
                        selected_basis = (
                            group_basis
                            if selected_basis is None
                            else selected_basis | group_basis
                        )
                    return (
                        selected_basis
                        if selected_basis is not None
                        else self._basis_data.none()
                    )

                basis = _MaterializedGroupBucket(
                    self._manager_class,
                    selected_values,
                    self._captured_dependencies,
                    selected_fallback,
                )
                return self._from_grouped_data(
                    self._manager_class,
                    self._group_by_keys,
                    new_data,
                    basis_data=basis,
                    captured_dependencies=self._captured_dependencies,
                )
            new_base_data = None
            for manager in new_data:
                if new_base_data is None:
                    new_base_data = manager._data
                else:
                    new_base_data = new_base_data | manager._data
            if new_base_data is None:
                raise EmptyGroupBucketSliceError()
            return GroupBucket(self._manager_class, self._group_by_keys, new_base_data)
        raise InvalidGroupBucketIndexError(type(item))

    def __len__(self) -> int:
        """
        Return the number of grouped managers.

        Returns:
            int: Number of groups.
        """
        return self.count()

    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Determine whether the given manager instance exists in the underlying data.

        Parameters:
            item (GeneralManagerType): Manager instance checked for membership.

        Returns:
            bool: True if the instance is present in the basis data.
        """
        self._replay_dependencies()
        return item in self._basis_data

    def sort(
        self,
        key: tuple[str, ...] | str,
        reverse: bool = False,
    ) -> GroupBucket[GeneralManagerType]:
        """
        Return a new GroupBucket sorted by the specified attributes.

        Parameters:
            key (str | tuple[str, ...]): Attribute name(s) used for sorting.
            reverse (bool): Whether to apply descending order.

        Returns:
            GroupBucket[GeneralManagerType]: Sorted grouping bucket.
        """
        if isinstance(key, str):
            key = (key,)
        if reverse:
            sorted_data = sorted(
                self._data,
                key=lambda x: tuple(getattr(x, k) for k in key),
                reverse=True,
            )
        else:
            sorted_data = sorted(
                self._data, key=lambda x: tuple(getattr(x, k) for k in key)
            )

        if self._materialized:
            return self._from_grouped_data(
                self._manager_class,
                self._group_by_keys,
                sorted_data,
                basis_data=self._basis_data,
                captured_dependencies=self._captured_dependencies,
            )
        new_bucket = GroupBucket(
            self._manager_class, self._group_by_keys, self._basis_data
        )
        new_bucket._data = sorted_data
        return new_bucket

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        Extend the grouping with additional attribute keys.

        Parameters:
            *group_by_keys (str): Attribute names appended to the current grouping.

        Returns:
            GroupBucket[GeneralManagerType]: New bucket grouped by the combined key set.
        """
        basis_data = self._basis_data
        if self._materialized:
            basis_data = _NoMaterializationBucket(basis_data, self._manager_class)
        return GroupBucket(
            self._manager_class,
            tuple([*self._group_by_keys, *group_by_keys]),
            basis_data,
        )

    def none(self) -> GroupBucket[GeneralManagerType]:
        """
        Produce an empty grouping bucket that preserves the current configuration.

        Returns:
            GroupBucket[GeneralManagerType]: Empty grouping bucket with identical manager class and grouping keys.
        """
        if self._materialized:
            return self._from_grouped_data(
                self._manager_class,
                self._group_by_keys,
                [],
                basis_data=_MaterializedGroupBucket(
                    self._manager_class,
                    (),
                    self._captured_dependencies,
                ),
                captured_dependencies=self._captured_dependencies,
            )
        return GroupBucket(
            self._manager_class, self._group_by_keys, self._basis_data.none()
        )


Bucket.register(GroupBucket)
