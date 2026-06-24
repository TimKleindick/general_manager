"""Helpers that derive cache dependency metadata from GeneralManager objects."""

from collections.abc import Iterator, Mapping

from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.dependency_index import (
    Dependency,
    serialize_dependency_identifier,
)


class ModelDependencyCollector:
    """Collect dependency tuples from cached arguments."""

    @staticmethod
    def collect(
        obj: object,
    ) -> Iterator[Dependency]:
        """
        Traverse arbitrary objects and yield cache dependency tuples.

        Parameters:
            obj: Object that may contain GeneralManager instances, buckets,
                `collections.abc.Mapping` instances, or objects satisfying
                `isinstance(obj, (list, tuple, set))`. Mapping/list/tuple/set
                subclasses are included; `frozenset` and arbitrary iterable
                objects are not traversed. Custom object attributes are not
                inspected by this method; GeneralManager instances yield only
                their own identification dependency here.
                Unsupported non-container objects yield nothing.

        Yields:
            `Dependency` is imported from `general_manager.cache.dependency_index`;
            this helper treats it as the tuple shape
            `tuple[str, Literal["identification", "filter", "exclude"], str]`.
            The fields are `(manager_name, action, identifier)`, where `action`
            is one of `"identification"`, `"filter"`, or `"exclude"` and
            `identifier` is the serialized dependency string. Identifier
            serialization, accepted input shapes, and stable output formatting
            are delegated to `serialize_dependency_identifier()`.
            GeneralManager instances are recognized with
            `isinstance(obj, GeneralManager)` and yield an `"identification"`
            dependency. Recognition checks GeneralManager before Bucket before
            `collections.abc.Mapping` before list/tuple/set containers; the
            first matching category controls behavior and later categories are
            not inspected.
            The identifier is `serialize_dependency_identifier(obj.identification)`.
            The identifier is always the serialized dependency string returned
            by `serialize_dependency_identifier()`, so yielded tuples are
            hashable and can be stored in a `set[Dependency]`; the serializer's
            canonical format and supported input normalization define the
            identifier contract. Bucket
            instances are recognized with `isinstance(obj, Bucket)` and use
            the direct `obj._manager_class.__name__` lookup as `manager_name`;
            exceptions from missing `_manager_class` or missing `__name__`
            propagate. Buckets yield exactly one `"filter"` dependency first with
            tuple `(obj._manager_class.__name__, "filter",
            serialize_dependency_identifier(obj.filters))` and exactly one
            `"exclude"` dependency second with tuple
            `(obj._manager_class.__name__, "exclude",
            serialize_dependency_identifier(obj.excludes))`, even when either
            mapping is empty. Mapping keys are ignored; only mapping values are
            traversed in the mapping's normal value iteration order, including
            keyword-argument mappings. Lists and tuples are traversed in
            sequence order; sets use Python set iteration order and are not
            deterministic. Traversed containers means only
            `collections.abc.Mapping` instances and objects satisfying
            `isinstance(obj, (list, tuple, set))`; these containers are iterated
            live rather than snapshotted, so Python's normal
            mutation-during-iteration behavior applies. Direct repeated
            GeneralManager or Bucket references can yield duplicate tuples.
            Cyclic or repeated traversed container
            objects are tracked by object identity and visited once per
            `collect()` call. A container is marked before its children are
            traversed, so the first encounter is traversed and later encounters
            in the same call are skipped, including self-references. Equal but
            distinct containers are traversed separately. Exceptions raised by
            dependency identifier serialization propagate to the caller.
        """
        yield from ModelDependencyCollector._collect(obj, seen=set())

    @staticmethod
    def _collect(
        obj: object,
        *,
        seen: set[int],
    ) -> Iterator[Dependency]:
        """Collect dependencies while guarding against cyclic containers."""
        if isinstance(obj, GeneralManager):
            yield (
                obj.__class__.__name__,
                "identification",
                serialize_dependency_identifier(obj.identification),
            )
        elif isinstance(obj, Bucket):
            yield (
                obj._manager_class.__name__,
                "filter",
                serialize_dependency_identifier(obj.filters),
            )
            yield (
                obj._manager_class.__name__,
                "exclude",
                serialize_dependency_identifier(obj.excludes),
            )
        elif isinstance(obj, Mapping):
            obj_id = id(obj)
            if obj_id in seen:
                return
            seen.add(obj_id)
            for v in obj.values():
                yield from ModelDependencyCollector._collect(v, seen=seen)
        elif isinstance(obj, (list, tuple, set)):
            obj_id = id(obj)
            if obj_id in seen:
                return
            seen.add(obj_id)
            for item in obj:
                yield from ModelDependencyCollector._collect(item, seen=seen)

    @staticmethod
    def add_args(
        dependencies: set[Dependency],
        args: tuple[object, ...],
        kwargs: Mapping[str, object],
    ) -> None:
        """
        Enrich the dependency set with values discovered in positional and keyword arguments.

        Mutates the provided `dependencies` set in place. If the first
        positional argument satisfies `isinstance(args[0], GeneralManager)`, this
        method first scans current values from `args[0].__dict__.values()` in
        that mapping's iteration order, passing each attribute value to a
        separate `collect()` call; if that manager has no `__dict__`,
        `AttributeError` propagates. When `args` is empty, this first-argument
        attribute scan is skipped. It does
        not inspect slots, properties, class attributes, or attribute names. It
        then calls `collect(args)` once for the complete positional-argument
        tuple and `collect(kwargs)` once for the complete keyword mapping.
        Each of these calls uses the same traversal rules, duplicate behavior,
        and serialization exception propagation documented on `collect()`.
        Container cycle tracking is scoped to each `collect()` call, not shared
        across the whole `add_args()` call. Because `dependencies` is a set,
        duplicate tuples collapse; the documented scan order matters only for
        side effects and exceptions raised during collection or serialization.
        Each yielded dependency is added to `dependencies` immediately during the
        current scan step; dependencies are not buffered for a later bulk update.
        The first positional manager, when present, is also collected again as
        part of `collect(args)`, so its own `"identification"` dependency is
        added after its attributes are scanned.

        Parameters:
            dependencies: Target collection that accumulates dependency tuples.
            args: Positional arguments from the cached function.
            kwargs: Keyword arguments from the cached function.

        Returns:
            None
        """
        if args and isinstance(args[0], GeneralManager):
            inner_self = args[0]
            for attr_val in inner_self.__dict__.values():
                for dependency_tuple in ModelDependencyCollector.collect(attr_val):
                    dependencies.add(dependency_tuple)

        for dependency_tuple in ModelDependencyCollector.collect(args):
            dependencies.add(dependency_tuple)
        for dependency_tuple in ModelDependencyCollector.collect(kwargs):
            dependencies.add(dependency_tuple)
