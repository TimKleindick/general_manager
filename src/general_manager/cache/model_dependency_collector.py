"""Helpers that derive cache dependency metadata from GeneralManager objects."""

from typing import Generator
from general_manager.manager.general_manager import GeneralManager
from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.dependency_index import (
    general_manager_name,
    Dependency,
    filter_type,
)


class ModelDependencyCollector:
    """Collect dependency tuples from cached arguments."""

    @staticmethod
    def collect(
        obj: object,
    ) -> Generator[tuple[general_manager_name, filter_type, str], None, None]:
        """
        Traverse arbitrary objects and yield cache dependency tuples.

        Parameters:
            obj (object): Object that may contain GeneralManager instances, buckets, or nested collections.

        Yields:
            tuple[str, filter_type, str]: Dependency descriptors combining manager name, dependency type, and lookup data.
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
    def add_args(dependencies: set[Dependency], args: tuple, kwargs: dict) -> None:
        """
        Enrich the dependency set with values discovered in positional and keyword arguments.

        Parameters:
            dependencies (set[Dependency]): Target collection that accumulates dependency tuples.
            args (tuple): Positional arguments from the cached function.
            kwargs (dict): Keyword arguments from the cached function.

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
