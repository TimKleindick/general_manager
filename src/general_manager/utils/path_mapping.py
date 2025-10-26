"""Utilities for tracing relationships between GeneralManager classes."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, ClassVar, cast, get_args
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.property import GraphQLProperty

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


type PathStart = str
type PathDestination = str


class MissingStartInstanceError(ValueError):
    """Raised when attempting to traverse a path without a starting instance."""

    def __init__(self) -> None:
        """
        Create the MissingStartInstanceError with its default message.

        This initializer constructs the exception with the message: "Cannot call go_to on a PathMap without a start instance."
        """
        super().__init__("Cannot call go_to on a PathMap without a start instance.")


class PathMap:
    """Maintain cached traversal paths between GeneralManager classes."""

    instance: PathMap
    mapping: ClassVar[dict[tuple[PathStart, PathDestination], PathTracer]] = {}

    def __new__(cls, *args: Any, **kwargs: Any) -> PathMap:
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
            path_start (PathStart | GeneralManager | type[GeneralManager]): Name, instance, or class that serves as the origin for future path lookups. The value determines both the stored starting instance and the class metadata used for path resolution.

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
            self.start_class = cast(type[GeneralManager], path_start)
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

        Parameters:
            path_destination (PathDestination | type[GeneralManager] | str): Target manager identifier, either as a class, instance name, or class name.

        Returns:
            PathTracer | None: The tracer describing how to traverse to the destination, or None if no path is known.
        """
        if isinstance(path_destination, type):
            path_destination = path_destination.__name__

        tracer = self.mapping.get((self.start_class_name, path_destination), None)
        if not tracer:
            return None
        return tracer

    def go_to(
        self, path_destination: PathDestination | type[GeneralManager] | str
    ) -> GeneralManager | Bucket | None:
        """
        Traverse the cached path from the configured start to the given destination.

        Parameters:
            path_destination (PathDestination | type[GeneralManager] | str): Destination specified as a GeneralManager class, a destination name, or a PathDestination identifier.

        Returns:
            GeneralManager | Bucket | None: The resolved GeneralManager instance, a Bucket of instances reached by the path, or `None` if no cached path exists.

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
    """Resolve attribute paths linking one manager class to another."""

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

        Parameters:
            current_manager (type[GeneralManager]): Manager class used as the current traversal node.
            path (list[str]): Sequence of attribute names accumulated along the traversal.

        Returns:
            list[str] | None: Updated list of attribute names leading to the destination, or None if no route exists.
        """
        current_connections = {
            attr_name: attr_value["type"]
            for attr_name, attr_value in current_manager.Interface.get_attribute_types().items()
        }
        for attr_name, attr_value in current_manager.__dict__.items():
            if not isinstance(attr_value, GraphQLProperty):
                continue
            type_hints = get_args(attr_value.graphql_type_hint)
            field_type = (
                type_hints[0]
                if type_hints
                else cast(type, attr_value.graphql_type_hint)
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

    def traverse_path(
        self, start_instance: GeneralManager | Bucket
    ) -> GeneralManager | Bucket | None:
        """
        Traverse the stored path starting from the provided manager or bucket instance.

        Parameters:
            start_instance (GeneralManager | Bucket): Object used as the traversal root.

        Returns:
            GeneralManager | Bucket | None: The resolved destination object, a merged bucket, or None when no traversal is required.
        """
        current_instance: Any = start_instance
        if not self.path:
            return None
        for attr in self.path:
            if not isinstance(current_instance, Bucket):
                current_instance = getattr(current_instance, attr)
                continue
            new_instance: Any = None
            for entry in current_instance:
                attr_value = getattr(entry, attr)
                if new_instance is None:
                    new_instance = attr_value
                else:
                    new_instance = new_instance | attr_value  # type: ignore[operator]
            current_instance = new_instance

        return cast(GeneralManager | Bucket[Any] | None, current_instance)
