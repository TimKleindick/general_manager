"""Abstract bucket primitives for managing GeneralManager collections."""

from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Hashable, Mapping
from typing import (
    Generator,
    TYPE_CHECKING,
    Generic,
    TypeVar,
    cast,
)

from general_manager.bucket.indexing import (
    BucketIndexKeySpec,
    _build_multi_bucket_index_normalized,
    _build_unique_bucket_index_normalized,
    normalize_bucket_index_key_spec,
    validate_bucket_index_max_rows,
)
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import ensure_calculation_run_context

GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
BucketLookup = Mapping[str, object]

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.bucket.group_bucket import GroupBucket


class Bucket(ABC, Generic[GeneralManagerType]):
    """Abstract interface for lazily evaluated GeneralManager collections.

    Concrete buckets provide their own storage type and reconstruction behavior.
    The base class stores only the manager class plus object-shaped lookup
    metadata so shared helpers such as grouping and run-scoped indexes can work
    across database, request, and calculation-backed collections.
    """

    def __init__(self, manager_class: type[GeneralManagerType]) -> None:
        """
        Create a bucket bound to a specific manager class.

        Parameters:
            manager_class (type[GeneralManagerType]): GeneralManager subclass whose instances this bucket represents.

        Returns:
            None
        """
        self._manager_class = manager_class
        self._data: object | None = None
        self.excludes: BucketLookup = {}
        self.filters: BucketLookup = {}

    def __eq__(self, other: object) -> bool:
        """
        Compare two buckets for equality.

        Parameters:
            other (object): Object tested for equality with this bucket.

        Returns:
            bool: True when the buckets share the same class, manager class, and data payload.
        """
        if not isinstance(other, self.__class__):
            return False
        return self._data == other._data and self._manager_class == other._manager_class

    def __reduce__(self) -> str | tuple[object, ...]:
        """
        Provide fallback pickling support by returning constructor arguments.

        Subclasses with additional constructor requirements override this
        method. The fallback shape is kept for older bucket implementations that
        accepted ``(data, manager_class, filters, excludes)``.

        Returns:
            tuple[object, ...]: Reconstruction data for compatible subclasses.
        """
        return (
            self.__class__,
            (None, self._manager_class, self.filters, self.excludes),
        )

    @abstractmethod
    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> Bucket[GeneralManagerType]:
        """
        Return a bucket containing the union of this bucket and another input.

        Parameters:
            other (Bucket[GeneralManagerType] | GeneralManagerType): Bucket or single manager instance to merge.

        Returns:
            Bucket[GeneralManagerType]: New bucket with the combined contents.
        """
        raise NotImplementedError

    @abstractmethod
    def __iter__(
        self,
    ) -> Generator[GeneralManagerType, None, None]:
        """
        Iterate over items in the bucket.

        Yields:
            GeneralManagerType: Items stored in the bucket.
        """
        raise NotImplementedError

    @abstractmethod
    def filter(self, **kwargs: object) -> Bucket[GeneralManagerType]:
        """
        Return a bucket reduced to items matching the provided filters.

        Parameters:
            **kwargs: Field lookups applied to the underlying query.

        Returns:
            Bucket[GeneralManagerType]: Filtered bucket instance.
        """
        raise NotImplementedError

    @abstractmethod
    def exclude(self, **kwargs: object) -> Bucket[GeneralManagerType]:
        """
        Return a bucket that excludes items matching the provided filters.

        Parameters:
            **kwargs: Field lookups specifying records to remove from the result.

        Returns:
            Bucket[GeneralManagerType]: Bucket with the specified records excluded.
        """
        raise NotImplementedError

    @abstractmethod
    def first(self) -> GeneralManagerType | None:
        """
        Return the first item contained in the bucket.

        Returns:
            GeneralManagerType | None: First entry if present, otherwise None.
        """
        raise NotImplementedError

    @abstractmethod
    def last(self) -> GeneralManagerType | None:
        """
        Return the last item contained in the bucket.

        Returns:
            GeneralManagerType | None: Last entry if present, otherwise None.
        """
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        """
        Return the number of items represented by the bucket.

        Returns:
            int: Count of items.
        """
        raise NotImplementedError

    @abstractmethod
    def all(self) -> Bucket[GeneralManagerType]:
        """
        Return a bucket encompassing every item managed by this instance.

        Returns:
            Bucket[GeneralManagerType]: Bucket without filters or exclusions.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, **kwargs: object) -> GeneralManagerType:
        """
        Retrieve a single item matching the provided criteria.

        Parameters:
            **kwargs: Field lookups identifying the target record.

        Returns:
            GeneralManagerType: Matching item.
        """
        raise NotImplementedError

    @abstractmethod
    def __getitem__(
        self, item: int | slice
    ) -> GeneralManagerType | Bucket[GeneralManagerType]:
        """
        Retrieve an item or slice from the bucket.

        Parameters:
            item (int | slice): Index or slice specifying the desired record(s).

        Returns:
            GeneralManagerType | Bucket[GeneralManagerType]: Resulting item or bucket slice.
        """
        raise NotImplementedError

    @abstractmethod
    def __len__(self) -> int:
        """
        Return the number of items contained in the bucket.

        Returns:
            int: Count of elements.
        """
        raise NotImplementedError

    @abstractmethod
    def __contains__(self, item: GeneralManagerType) -> bool:
        """
        Checks whether the specified item is present in the bucket.

        Parameters:
            item (GeneralManagerType): Manager instance evaluated for membership.

        Returns:
            bool: True if the bucket contains the provided instance.
        """
        raise NotImplementedError

    @abstractmethod
    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> Bucket[GeneralManagerType]:
        """
        Return a sorted bucket.

        Parameters:
            key (str | tuple[str, ...]): Attribute name(s) used for sorting.
            reverse (bool): Whether to sort in descending order.

        Returns:
            Bucket[GeneralManagerType]: Sorted bucket instance.
        """
        raise NotImplementedError

    def group_by(self, *group_by_keys: str) -> GroupBucket[GeneralManagerType]:
        """
        Materialise a grouped view of the bucket.

        Parameters:
            *group_by_keys (str): Attribute names used to form groups.

        Returns:
            GroupBucket[GeneralManagerType]: Bucket grouping items by the provided keys.
        """
        from general_manager.bucket.group_bucket import GroupBucket

        return GroupBucket(self._manager_class, group_by_keys, self)

    def _bucket_index_source_signature(self) -> Hashable:
        """Return the conservative run-local source signature for bucket indexes."""
        return (
            self.__class__,
            self._manager_class,
            id(self),
        )

    def index_by(
        self,
        key_spec: BucketIndexKeySpec,
        *,
        max_rows: int | None = 1000,
    ) -> dict[Hashable, GeneralManagerType]:
        """Build or reuse a unique run-scoped index over this bucket.

        Duplicate frozen keys raise an error because each key must identify one
        row. The result is cached only for the active calculation run and is
        separated by bucket object, key spec, unique-vs-many mode, and
        `max_rows`. If no run is active, the method creates a temporary run for
        this call, so there is no reuse across separate calls. Cached hits return
        the same mutable dictionary object stored for the run; treat it as
        read-only because mutations are visible to later same-run hits. Failed
        index construction does not store a cache entry, so later same-run calls
        retry the build. Validation order is `max_rows`, then `key_spec`, then
        bucket iteration. During iteration, the row guardrail is checked before
        key resolution for each row.

        Args:
            key_spec: One field name, or a non-empty tuple of field names for a
                composite key. Empty strings are accepted and passed to
                attribute lookup unchanged; mapping keys are not read unless the
                row also exposes them as attributes.
            max_rows: Maximum number of source rows to read before failing, or
                `None` to disable the guardrail. Non-positive limits are allowed
                but fail as soon as any row is read. Runtime callers must pass
                an integer or `None`; booleans and other values raise
                `TypeError`.

        Returns:
            A dictionary mapping each frozen key to the single matching manager.
            Empty buckets return an empty dictionary.
            Frozen keys preserve already-hashable scalar values and normalize
            managers and containers into hashable identities suitable for lookup
            during the same process.

        Raises:
            BucketIndexTooLargeError: If more than `max_rows` rows are read.
            DuplicateBucketIndexKeyError: If two rows resolve to the same key.
            MissingBucketIndexKeyError: If a row lacks a requested key field.
            UnsupportedBucketIndexKeySpecError: If `key_spec` is not a string or
                a non-empty tuple containing only strings.
            TypeError: If `max_rows` is not an integer or `None`.
            UnhashableBucketIndexKeyError: If a key value cannot be frozen.
            Exception: Exceptions raised while iterating the bucket
                propagates unchanged.
        """
        max_rows = validate_bucket_index_max_rows(max_rows)
        normalized_key_spec = normalize_bucket_index_key_spec(key_spec)
        source_signature = self._bucket_index_source_signature()
        with ensure_calculation_run_context() as context:
            cached = context.get_bucket_index_result(
                source_signature,
                normalized_key_spec,
                False,
                max_rows,
            )
            if cached is not None:
                return cast(dict[Hashable, GeneralManagerType], cached)

            with DependencyTracker() as dependencies:
                index = _build_unique_bucket_index_normalized(
                    self,
                    normalized_key_spec,
                    max_rows=max_rows,
                )
            context.set_bucket_index_result(
                source_signature,
                normalized_key_spec,
                False,
                index,
                dependencies,
                max_rows,
            )
            return index

    def index_many(
        self,
        key_spec: BucketIndexKeySpec,
        *,
        max_rows: int | None = 1000,
    ) -> dict[Hashable, tuple[GeneralManagerType, ...]]:
        """Build or reuse a run-scoped index that groups rows by key.

        Values are tuples preserving source iteration order, and the cached
        result is scoped to the active calculation run. Cache entries are
        separated by bucket object, key spec, unique-vs-many mode, and
        `max_rows`. If no run is active, the method creates a temporary run for
        this call, so there is no reuse across separate calls. Cached hits return
        the same mutable dictionary object stored for the run; treat it as
        read-only because mutations are visible to later same-run hits. Failed
        index construction does not store a cache entry, so later same-run calls
        retry the build. Validation order is `max_rows`, then `key_spec`, then
        bucket iteration. During iteration, the row guardrail is checked before
        key resolution for each row.

        Args:
            key_spec: One field name, or a non-empty tuple of field names for a
                composite key. Empty strings are accepted and passed to
                attribute lookup unchanged; mapping keys are not read unless the
                row also exposes them as attributes.
            max_rows: Maximum number of source rows to read before failing, or
                `None` to disable the guardrail. Non-positive limits are allowed
                but fail as soon as any row is read. Runtime callers must pass
                an integer or `None`; booleans and other values raise
                `TypeError`.

        Returns:
            A dictionary mapping each frozen key to matching managers in source
            order. Empty buckets return an empty dictionary. Frozen keys
            preserve already-hashable scalar values and normalize managers and
            containers into hashable identities suitable for lookup during the
            same process.

        Raises:
            BucketIndexTooLargeError: If more than `max_rows` rows are read.
            MissingBucketIndexKeyError: If a row lacks a requested key field.
            UnsupportedBucketIndexKeySpecError: If `key_spec` is not a string or
                a non-empty tuple containing only strings.
            TypeError: If `max_rows` is not an integer or `None`.
            UnhashableBucketIndexKeyError: If a key value cannot be frozen.
            Exception: Exceptions raised while iterating the bucket
                propagates unchanged.
        """
        max_rows = validate_bucket_index_max_rows(max_rows)
        normalized_key_spec = normalize_bucket_index_key_spec(key_spec)
        source_signature = self._bucket_index_source_signature()
        with ensure_calculation_run_context() as context:
            cached = context.get_bucket_index_result(
                source_signature,
                normalized_key_spec,
                True,
                max_rows,
            )
            if cached is not None:
                return cast(dict[Hashable, tuple[GeneralManagerType, ...]], cached)

            with DependencyTracker() as dependencies:
                index = _build_multi_bucket_index_normalized(
                    self,
                    normalized_key_spec,
                    max_rows=max_rows,
                )
            context.set_bucket_index_result(
                source_signature,
                normalized_key_spec,
                True,
                index,
                dependencies,
                max_rows,
            )
            return index

    def none(self) -> Bucket[GeneralManagerType]:
        """
        Return an empty bucket instance.

        Returns:
            Bucket[GeneralManagerType]: Empty bucket.

        Raises:
            NotImplementedError: Always raised by the base implementation; subclasses must provide a concrete version.
        """
        raise NotImplementedError(
            "The 'none' method is not implemented in the base Bucket class. "
            "Subclasses should implement this method to return an empty bucket."
        )
