from typing import Generator
from general_manager.manager.generalManager import GeneralManager
from general_manager.bucket.baseBucket import Bucket
from general_manager.cache.dependencyIndex import (
    general_manager_name,
    Dependency,
    filter_type,
)


class ModelDependencyCollector:

    @staticmethod
    def collect(
        obj,
    ) -> Generator[tuple[general_manager_name, filter_type, str], None, None]:
        """
        Recursively extracts dependency information from Django model-related objects.

        Inspects the input object and its nested structures to identify instances of GeneralManager and Bucket, yielding a tuple for each dependency found. Each tuple contains the manager class name, the dependency type ("identification", "filter", or "exclude"), and the string representation of the dependency value.

        Args:
            obj: The object or collection to inspect for model dependencies.

        Yields:
            Tuples of (manager class name, dependency type, dependency value) for each dependency discovered.
        """
        if isinstance(obj, GeneralManager):
            yield (
                obj.__class__.__name__,
                "identification",
                f"{obj.identification}",
            )
        elif isinstance(obj, Bucket):
            yield (obj._manager_class.__name__, "filter", f"{obj.filters}")
            yield (obj._manager_class.__name__, "exclude", f"{obj.excludes}")
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from ModelDependencyCollector.collect(v)
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                yield from ModelDependencyCollector.collect(item)

    @staticmethod
    def addArgs(dependencies: set[Dependency], args: tuple, kwargs: dict) -> None:
        """
        Add dependencies to the dependency set.
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
