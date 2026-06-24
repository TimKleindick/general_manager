"""Utilities for tracing relationships between GeneralManager classes."""

from __future__ import annotations
from typing import ClassVar, cast, get_args
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.property import GraphQLProperty

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


type PathStart = str
type PathDestination = str
type TraversalValue = GeneralManager | Bucket[GeneralManager]


class MissingStartInstanceError(ValueError):
    """Raised when attempting to traverse a path without a starting instance."""

    def __init__(self) -> None:
        """
        Create the MissingStartInstanceError with its default message.

        This initializer constructs the exception with the message: "Cannot call go_to on a PathMap without a start instance."
        """
        super().__init__("Cannot call go_to on a PathMap without a start instance.")


class InvalidPathTraversalValueError(TypeError):
    """Raised when a traced path attribute does not return a traversable value."""

    def __init__(self) -> None:
        super().__init__(
            "Path traversal attributes must resolve to a GeneralManager or Bucket."
        )


class PathMap:
    """
    Maintain cached traversal paths between GeneralManager classes.

    The class-level mapping stores one PathTracer for each registered
    start/destination class-name pair. A cached tracer may have ``path is None``
    when the classes are known but no route exists.
    """

    instance: PathMap
    mapping: ClassVar[dict[tuple[PathStart, PathDestination], PathTracer]] = {}

    def __new__(cls, *args: object, **kwargs: object) -> PathMap:
        """
        Obtain the singleton PathMap, initializing the path mapping on first instantiation.

        Returns:
            PathMap: The singleton PathMap instance.
        """
        if not hasattr(cls, "instance"):
            cls.instance = super().__new__(cls)
            cls.create_path_mapping()
        return cls.instance

    @classmethod
    def create_path_mapping(cls) -> None:
        """
        Populate the path mapping with tracers for every distinct pair of managed classes.

        The generated tracers capture the attribute sequence needed to navigate from the start class to the destination class and are cached on the singleton instance.

        Returns:
            None
        """
        all_managed_classes = GeneralManagerMeta.all_classes
        for start_class in all_managed_classes:
            for destination_class in all_managed_classes:
                if start_class != destination_class:
                    cls.instance.mapping[
                        (start_class.__name__, destination_class.__name__)
                    ] = PathTracer(start_class, destination_class)

    def __init__(
        self,
        path_start: PathStart | GeneralManager | type[GeneralManager],
    ) -> None:
        """
        Create a new traversal context rooted at the provided manager class or instance.

        Parameters:
            path_start (PathStart | GeneralManager | type[GeneralManager]): Manager class name, manager instance, or manager class that serves as the origin for future path lookups. Only an instance can be traversed with `go_to`.

        Returns:
            None
        """
        self.start_instance: GeneralManager | None
        self.start_class: type[GeneralManager] | None
        self.start_class_name: str
        if isinstance(path_start, GeneralManager):
            self.start_instance = path_start
            self.start_class = path_start.__class__
            self.start_class_name = path_start.__class__.__name__
        elif isinstance(path_start, type):
            self.start_instance = None
            self.start_class = path_start
            self.start_class_name = path_start.__name__
        else:
            self.start_instance = None
            self.start_class = None
            self.start_class_name = path_start

    def to(
        self, path_destination: PathDestination | type[GeneralManager] | str
    ) -> PathTracer | None:
        """
        Retrieve the cached path tracer from the start class to the desired destination.

        ``None`` means no cached key exists for the start/destination names. A
        non-``None`` tracer can still be unreachable; inspect ``tracer.path`` for
        ``None`` before treating it as a usable route.

        Parameters:
            path_destination (PathDestination | type[GeneralManager] | str): Target manager identifier, either as a manager class or string class name. Destination instances are not accepted.

        Returns:
            PathTracer | None: The cached tracer for the registered class-name pair, or None when no mapping key exists.
        """
        if isinstance(path_destination, type):
            path_destination = path_destination.__name__

        tracer = self.mapping.get((self.start_class_name, path_destination), None)
        if not tracer:
            return None
        return tracer

    def go_to(
        self, path_destination: PathDestination | type[GeneralManager] | str
    ) -> TraversalValue | None:
        """
        Traverse the cached path from the configured start to the given destination.

        The lookup first resolves a cached tracer. Missing tracer keys and
        tracers with ``path is None`` return ``None``. A tracer with an empty
        path also returns ``None`` because no traversal is required. If a
        traversable path exists but this PathMap was created from a class or
        string start, MissingStartInstanceError is raised.

        Parameters:
            path_destination (PathDestination | type[GeneralManager] | str): Destination specified as a manager class or string class name. Destination instances are not accepted.

        Returns:
            GeneralManager | Bucket[GeneralManager] | None: The resolved GeneralManager instance, a Bucket of instances reached by the path, or `None` if no cached path exists.

        Raises:
            MissingStartInstanceError: If the cached path requires a concrete start instance but the PathMap was constructed without one.
        """
        if isinstance(path_destination, type):
            path_destination = path_destination.__name__

        tracer = self.mapping.get((self.start_class_name, path_destination), None)
        if not tracer:
            return None
        if self.start_instance is None:
            raise MissingStartInstanceError()
        return tracer.traverse_path(self.start_instance)

    def get_all_connected(self) -> set[str]:
        """
        Return the set of destination class names that are reachable from the configured start.

        Returns:
            set[str]: Destination class names reachable from the current start_class_name.
        """
        connected_classes: set[str] = set()
        for path_tuple, path_obj in self.mapping.items():
            if path_tuple[0] == self.start_class_name:
                destination_class_name = path_tuple[1]
                if path_obj.path is None:
                    continue
                connected_classes.add(destination_class_name)
        return connected_classes


class PathTracer:
    """
    Resolve attribute paths linking one manager class to another.

    The public ``path`` attribute is ``[]`` for the same start/destination class,
    a list of attribute names for the first discovered route, or ``None`` when no
    route exists.
    """

    def __init__(
        self, start_class: type[GeneralManager], destination_class: type[GeneralManager]
    ) -> None:
        """
        Initialise a path tracer between two manager classes.

        Parameters:
            start_class (type[GeneralManager]): Origin manager class where traversal begins.
            destination_class (type[GeneralManager]): Target manager class to reach.

        Returns:
            None
        """
        self.start_class = start_class
        self.destination_class = destination_class
        if self.start_class == self.destination_class:
            self.path: list[str] | None = []
        else:
            self.path = self.create_path(start_class, [])

    def create_path(
        self, current_manager: type[GeneralManager], path: list[str]
    ) -> list[str] | None:
        """
        Recursively compute the traversal path from `current_manager` to the destination class.

        Candidate edges come from `Interface.get_attribute_types()` entries that
        expose a `type` key and from `@GraphQLProperty` return annotations. Only
        GeneralManager subclasses are traversed. The search skips attributes
        already in the current path and skips edges back to the original start
        class to avoid cycles.

        Parameters:
            current_manager (type[GeneralManager]): Manager class used as the current traversal node.
            path (list[str]): Sequence of attribute names accumulated along the traversal.

        Returns:
            list[str] | None: Updated list of attribute names leading to the destination, or None if no route exists.
        """
        current_connections: dict[str, object] = {}
        for (
            attr_name,
            attr_value,
        ) in current_manager.Interface.get_attribute_types().items():
            if isinstance(attr_value, dict):
                current_connections[attr_name] = attr_value.get("type")
        for attr_name, attr_value in current_manager.__dict__.items():
            if not isinstance(attr_value, GraphQLProperty):
                continue
            type_hints = get_args(attr_value.graphql_type_hint)
            field_type = (
                type_hints[0]
                if type_hints
                else cast(type[object], attr_value.graphql_type_hint)
            )
            current_connections[attr_name] = field_type
        for attr, attr_type in current_connections.items():
            if attr in path or attr_type == self.start_class:
                continue
            if attr_type is None or not isinstance(attr_type, type):
                continue
            if not issubclass(attr_type, GeneralManager):
                continue
            if attr_type == self.destination_class:
                return [*path, attr]
            result = self.create_path(attr_type, [*path, attr])
            if result:
                return result

        return None

    def traverse_path(self, start_instance: TraversalValue) -> TraversalValue | None:
        """
        Traverse the stored path starting from the provided manager or bucket instance.

        Manager traversal reads the next path attribute directly. Bucket
        traversal reads the attribute from each bucket entry and unions the
        resulting manager or bucket values. ``None`` is returned for no path,
        same-class empty paths, and empty buckets. Every resolved attribute must
        be a GeneralManager or Bucket.

        Parameters:
            start_instance (GeneralManager | Bucket[GeneralManager]): Object used as the traversal root.

        Returns:
            GeneralManager | Bucket[GeneralManager] | None: The resolved destination object, a merged bucket, or None when no traversal is required.

        Raises:
            InvalidPathTraversalValueError: If a traversed attribute resolves to neither a GeneralManager nor a Bucket.
        """
        current_instance: TraversalValue = start_instance
        if not self.path:
            return None
        for attr in self.path:
            if not isinstance(current_instance, Bucket):
                current_instance = _coerce_traversal_value(
                    getattr(current_instance, attr)
                )
                continue
            new_instance: TraversalValue | None = None
            for entry in current_instance:
                attr_value = _coerce_traversal_value(getattr(entry, attr))
                new_instance = _merge_traversal_values(new_instance, attr_value)
            if new_instance is None:
                return None
            current_instance = new_instance

        return current_instance


def _coerce_traversal_value(value: object) -> TraversalValue:
    """
    Return a traversable manager or bucket value from a resolved path attribute.

    Raises:
        InvalidPathTraversalValueError: If a path attribute resolves to neither a GeneralManager nor a Bucket.
    """
    if isinstance(value, GeneralManager):
        return value
    if isinstance(value, Bucket):
        return cast(Bucket[GeneralManager], value)
    raise InvalidPathTraversalValueError()


def _merge_traversal_values(
    current: TraversalValue | None,
    value: TraversalValue,
) -> TraversalValue:
    """Merge one traversed bucket entry into the accumulated traversal value."""
    if current is None:
        return value
    if isinstance(current, Bucket):
        return current | value
    return current | value
