import threading
from general_manager.cache.dependencyIndex import (
    general_manager_name,
    Dependency,
    filter_type,
)
from general_manager.cache.dependencyIndex import general_manager_name


# Thread-lokale Variable zur Speicherung der Abhängigkeiten
_dependency_storage = threading.local()


class DependencyTracker:
    def __enter__(
        self,
    ) -> set[Dependency]:
        _dependency_storage.dependencies = set()
        return _dependency_storage.dependencies

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(_dependency_storage, "dependencies"):
            del _dependency_storage.dependencies

    @staticmethod
    def trackMe(
        class_name: general_manager_name,
        operation: filter_type,
        identifier: str,
    ) -> None:
        """
        Adds a dependency to the dependency storage.
        """
        if hasattr(_dependency_storage, "dependencies"):
            dependencies: set[Dependency] = _dependency_storage.dependencies
            dependencies.add((class_name, operation, identifier))
