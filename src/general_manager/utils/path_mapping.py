"""Utilities for tracing relationships between GeneralManager classes."""

from __future__ import annotations
from collections import deque
from collections.abc import Mapping
from typing import ClassVar, cast, get_args
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.property import GraphQLProperty

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


type PathStart = str
type PathDestination = str
type PathEdge = tuple[str, PathDestination]
type PathClassEdge = tuple[str, type[GeneralManager]]
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


def _as_manager_class(value: object) -> type[GeneralManager] | None:
    """Return value when it is a GeneralManager subclass."""
    if value is None or not isinstance(value, type):
        return None
    if not issubclass(value, GeneralManager):
        return None
    return value


def _iter_manager_connections(
    manager_class: type[GeneralManager],
) -> list[tuple[str, type[GeneralManager]]]:
    """Return traversable manager edges declared by interface fields and GraphQL properties."""
    current_connections: dict[str, object] = {}
    for attr_name, attr_value in manager_class.Interface.get_attribute_types().items():
        if isinstance(attr_value, Mapping):
            current_connections[attr_name] = attr_value.get("type")
    for attr_name, attr_value in manager_class.__dict__.items():
        if not isinstance(attr_value, GraphQLProperty):
            continue
        type_hints = get_args(attr_value.graphql_type_hint)
        field_type = (
            type_hints[0]
            if type_hints
            else cast(type[object], attr_value.graphql_type_hint)
        )
        current_connections[attr_name] = field_type

    connections: list[tuple[str, type[GeneralManager]]] = []
    for attr_name, attr_type in current_connections.items():
        manager_type = _as_manager_class(attr_type)
        if manager_type is not None:
            connections.append((attr_name, manager_type))
    return connections


class PathMap:
    """
    Maintain cached traversal paths between GeneralManager classes.

    The class-level mapping stores lazily resolved PathTracer objects for
    requested start/destination class-name pairs. A cached tracer may have
    ``path is None`` when the classes are known but no route exists.
    """

    instance: PathMap
    mapping: ClassVar[dict[tuple[PathStart, PathDestination], PathTracer]] = {}
    _registry_signature: ClassVar[tuple[type[GeneralManager], ...]] = ()
    _classes_by_name: ClassVar[dict[str, type[GeneralManager]]] = {}
    _adjacency: ClassVar[dict[PathStart, tuple[PathEdge, ...]]] = {}
    _class_adjacency: ClassVar[
        dict[type[GeneralManager], tuple[PathClassEdge, ...]]
    ] = {}

    def __new__(cls, *args: object, **kwargs: object) -> PathMap:
        """
        Obtain the singleton PathMap, refreshing graph metadata on each construction.

        Returns:
            PathMap: The singleton PathMap instance.
        """
        force_refresh = False
        if not hasattr(cls, "instance"):
            cls.instance = super().__new__(cls)
            force_refresh = not cls.mapping
        cls._ensure_graph_current(force=force_refresh)
        return cls.instance

    @classmethod
    def create_path_mapping(cls) -> None:
        """
        Refresh path graph metadata without eagerly creating all pair tracers.

        Returns:
            None
        """
        cls._ensure_graph_current(force=True)

    @classmethod
    def _ensure_graph_current(cls, *, force: bool = False) -> None:
        """Refresh class and adjacency caches when the manager registry changes."""
        registry_signature = tuple(GeneralManagerMeta.all_classes)
        if not force and cls._registry_signature == registry_signature:
            return

        cls._registry_signature = registry_signature
        cls._classes_by_name = {
            manager_class.__name__: manager_class
            for manager_class in registry_signature
        }
        cls._class_adjacency = {
            manager_class: tuple(_iter_manager_connections(manager_class))
            for manager_class in registry_signature
        }
        cls._adjacency = {
            manager_class.__name__: tuple(
                (attr, target_class.__name__)
                for attr, target_class in cls._class_adjacency[manager_class]
            )
            for manager_class in registry_signature
        }
        cls.mapping.clear()

    @classmethod
    def _edges_for_class(
        cls,
        manager_class: type[GeneralManager],
    ) -> tuple[PathClassEdge, ...]:
        """Return cached outgoing edges for registered or discovered manager classes."""
        edges = cls._class_adjacency.get(manager_class)
        if edges is not None:
            return edges

        edges = tuple(_iter_manager_connections(manager_class))
        cls._class_adjacency[manager_class] = edges
        cls._adjacency.setdefault(
            manager_class.__name__,
            tuple((attr, target_class.__name__) for attr, target_class in edges),
        )
        return edges

    @classmethod
    def _find_path(
        cls,
        start_class_name: PathStart,
        destination_class_name: PathDestination,
    ) -> list[str] | None:
        """Find the shortest attribute path between two registered managers."""
        if start_class_name == destination_class_name:
            return []

        start_class = cls._classes_by_name.get(start_class_name)
        destination_class = cls._classes_by_name.get(destination_class_name)
        if start_class is None or destination_class is None:
            return None

        visited = {start_class}
        queue: deque[tuple[type[GeneralManager], list[str]]] = deque(
            [(start_class, [])]
        )

        while queue:
            current_class, current_path = queue.popleft()
            for attr, next_class in cls._edges_for_class(current_class):
                if next_class in visited:
                    continue
                next_path = [*current_path, attr]
                if next_class == destination_class:
                    return next_path
                visited.add(next_class)
                queue.append((next_class, next_path))

        return None

    @staticmethod
    def _destination_name(
        path_destination: PathDestination | type[GeneralManager] | str,
    ) -> PathDestination:
        """Return the class-name key for a path destination input."""
        if isinstance(path_destination, type):
            return path_destination.__name__
        return path_destination

    def _get_or_create_tracer(
        self,
        destination_class_name: PathDestination,
    ) -> PathTracer | None:
        """Return the cached tracer for one pair, computing only that pair when needed."""
        if self.start_class_name == destination_class_name:
            return None

        key = (self.start_class_name, destination_class_name)
        tracer = self.mapping.get(key)
        if tracer is not None:
            return tracer

        start_class = self._classes_by_name.get(self.start_class_name)
        destination_class = self._classes_by_name.get(destination_class_name)
        if start_class is None or destination_class is None:
            return None

        path = self._find_path(self.start_class_name, destination_class_name)
        tracer = PathTracer(start_class, destination_class, path, search=False)
        self.mapping[key] = tracer
        return tracer

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

        ``None`` means either the start or destination class name is not
        registered, or the requested pair uses the same start and destination
        class. A non-``None`` tracer can still be unreachable; inspect
        ``tracer.path`` for ``None`` before treating it as a usable route.

        Parameters:
            path_destination (PathDestination | type[GeneralManager] | str): Target manager identifier, either as a manager class or string class name. Destination instances are not accepted.

        Returns:
            PathTracer | None: The cached tracer for the registered class-name pair, or None when no mapping key can be created.
        """
        return self._get_or_create_tracer(self._destination_name(path_destination))

    def go_to(
        self, path_destination: PathDestination | type[GeneralManager] | str
    ) -> TraversalValue | None:
        """
        Traverse the cached path from the configured start to the given destination.

        The lookup lazily resolves and caches the requested tracer. Missing
        tracer keys and tracers with ``path is None`` return ``None``. A tracer
        with an empty path also returns ``None`` because no traversal is
        required. If a traversable path exists but this PathMap was created from
        a class or string start, MissingStartInstanceError is raised.

        Parameters:
            path_destination (PathDestination | type[GeneralManager] | str): Destination specified as a manager class or string class name. Destination instances are not accepted.

        Returns:
            GeneralManager | Bucket[GeneralManager] | None: The resolved GeneralManager instance, a Bucket of instances reached by the path, or `None` if no cached path exists.

        Raises:
            MissingStartInstanceError: If the cached path requires a concrete start instance but the PathMap was constructed without one.
        """
        tracer = self._get_or_create_tracer(self._destination_name(path_destination))
        if not tracer:
            return None
        if not tracer.path:
            return None
        if self.start_instance is None:
            raise MissingStartInstanceError()
        return tracer.traverse_path(self.start_instance)

    def get_all_connected(self) -> set[str]:
        """
        Return the set of destination class names reachable from the configured start.

        Returns:
            set[str]: Destination class names reachable from the current start_class_name.
        """
        start_class = self._classes_by_name.get(self.start_class_name)
        if start_class is None:
            return set()

        connected_classes: set[str] = set()
        visited = {start_class}
        queue: deque[type[GeneralManager]] = deque([start_class])

        while queue:
            current_class = queue.popleft()
            for _attr, next_class in self._edges_for_class(current_class):
                if next_class in visited:
                    continue
                visited.add(next_class)
                if self._classes_by_name.get(next_class.__name__) is next_class:
                    connected_classes.add(next_class.__name__)
                queue.append(next_class)

        return connected_classes


class PathTracer:
    """
    Resolve attribute paths linking one manager class to another.

    The public ``path`` attribute is ``[]`` for the same start/destination class,
    a list of attribute names for the first discovered route, or ``None`` when no
    route exists.
    """

    def __init__(
        self,
        start_class: type[GeneralManager],
        destination_class: type[GeneralManager],
        path: list[str] | None = None,
        *,
        search: bool = True,
    ) -> None:
        """
        Initialise a path tracer between two manager classes.

        Parameters:
            start_class (type[GeneralManager]): Origin manager class where traversal begins.
            destination_class (type[GeneralManager]): Target manager class to reach.
            path (list[str] | None): Precomputed path used by lazy PathMap lookup when search is False.
            search (bool): Whether to compute the path during construction.

        Returns:
            None
        """
        self.start_class = start_class
        self.destination_class = destination_class
        if not search:
            self.path = path
        elif self.start_class == self.destination_class:
            self.path = []
        else:
            self.path = self.create_path(start_class, [], {start_class})

    def create_path(
        self,
        current_manager: type[GeneralManager],
        path: list[str],
        visited_managers: set[type[GeneralManager]] | None = None,
    ) -> list[str] | None:
        """
        Recursively compute the traversal path from `current_manager` to the destination class.

        Candidate edges come from `Interface.get_attribute_types()` entries that
        expose a `type` key and from `@GraphQLProperty` return annotations. Only
        GeneralManager subclasses are traversed. Each manager class is expanded
        at most once, which bounds missing-path and cyclic graph searches.

        Parameters:
            current_manager (type[GeneralManager]): Manager class used as the current traversal node.
            path (list[str]): Sequence of attribute names accumulated along the traversal.
            visited_managers (set[type[GeneralManager]] | None): Manager classes already expanded during this search.

        Returns:
            list[str] | None: Updated list of attribute names leading to the destination, or None if no route exists.
        """
        if visited_managers is None:
            visited_managers = {current_manager}

        for attr, attr_type in _iter_manager_connections(current_manager):
            if attr_type == self.start_class:
                continue
            if attr_type == self.destination_class:
                return [*path, attr]
            if attr_type in visited_managers:
                continue
            visited_managers.add(attr_type)
            result = self.create_path(attr_type, [*path, attr], visited_managers)
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
