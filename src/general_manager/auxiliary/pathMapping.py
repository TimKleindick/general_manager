from __future__ import annotations
from typing import TYPE_CHECKING, cast, get_args
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.property import GraphQLProperty

from general_manager.interface.baseInterface import Bucket
from general_manager.manager.generalManager import GeneralManager


type PathStart = str
type PathDestination = str


class PathMap:

    instance: PathMap
    mapping: dict[tuple[PathStart, PathDestination], PathTracer] = {}

    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, "instance"):
            cls.instance = super().__new__(cls)
            cls.createPathMapping()
        return cls.instance

    @classmethod
    def createPathMapping(cls):
        """
        Builds the mapping of paths between all pairs of distinct managed classes.
        
        Iterates over all registered managed classes and creates a PathTracer for each unique start and destination class pair, storing them in the mapping dictionary.
        """
        all_managed_classes = GeneralManagerMeta.all_classes
        for start_class in all_managed_classes:
            for destination_class in all_managed_classes:
                if start_class != destination_class:
                    cls.instance.mapping[
                        (start_class.__name__, destination_class.__name__)
                    ] = PathTracer(start_class, destination_class)

    def __init__(self, path_start: PathStart | GeneralManager | type[GeneralManager]):

        """
        Initializes a PathMap with a specified starting point.
        
        The starting point can be a class name (string), a GeneralManager instance, or a GeneralManager subclass. Sets internal attributes for the start instance, class, and class name based on the input.
        """
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
        if isinstance(path_destination, type):
            path_destination = path_destination.__name__

        tracer = self.mapping.get((self.start_class_name, path_destination), None)
        if not tracer:
            return None
        return tracer

    def goTo(
        self, path_destination: PathDestination | type[GeneralManager] | str
    ) -> GeneralManager | Bucket | None:
        if isinstance(path_destination, type):
            path_destination = path_destination.__name__

        tracer = self.mapping.get((self.start_class_name, path_destination), None)
        if not tracer:
            return None
        if not self.start_instance:
            raise ValueError("Cannot call goTo on a PathMap without a start instance.")
        return tracer.traversePath(self.start_instance)

    def getAllConnected(self) -> set[str]:
        """
        Returns a list of all classes that are connected to the start class.
        """
        connected_classes: set[str] = set()
        for path_tuple in self.mapping.keys():
            if path_tuple[0] == self.start_class_name:
                destination_class_name = path_tuple[1]
                connected_classes.add(destination_class_name)
        return connected_classes


class PathTracer:
    def __init__(
        self, start_class: type[GeneralManager], destination_class: type[GeneralManager]
    ):
        self.start_class = start_class
        self.destination_class = destination_class
        if self.start_class == self.destination_class:
            self.path = []
        else:
            self.path = self.createPath(start_class, [])

    def createPath(
        self, current_manager: type[GeneralManager], path: list[str]
    ) -> list[str] | None:

        """
        Recursively constructs a path of attribute names from the current manager class to the destination class.
        
        Args:
            current_manager: The current GeneralManager subclass being inspected.
            path: The list of attribute names traversed so far.
        
        Returns:
            A list of attribute names representing the path to the destination class, or None if no path exists.
        """
        current_connections = {
            attr_name: attr_value["type"]
            for attr_name, attr_value in current_manager.Interface.getAttributeTypes().items()
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
            if not issubclass(attr_type, GeneralManager):
                continue
            if attr_type == self.destination_class:
                return [*path, attr]
            result = self.createPath(attr_type, [*path, attr])
            if result:
                return result

        return None

    def traversePath(
        self, start_instance: GeneralManager | Bucket
    ) -> GeneralManager | Bucket | None:

        """
        Traverses the stored path from a starting instance to reach the destination instance or bucket.
        
        Args:
            start_instance: The initial GeneralManager or Bucket instance from which to begin traversal.
        
        Returns:
            The resulting GeneralManager or Bucket instance at the end of the path, or None if the path is empty.
        """
        current_instance = start_instance
        if not self.path:
            return None
        for attr in self.path:
            if not isinstance(current_instance, Bucket):
                current_instance = getattr(current_instance, attr)
                continue
            new_instance = None
            for entry in current_instance:
                if not new_instance:
                    new_instance = getattr(entry, attr)
                else:
                    new_instance = new_instance | getattr(entry, attr)
            current_instance = new_instance

        return current_instance
