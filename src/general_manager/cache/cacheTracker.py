import threading
from general_manager.cache.dependencyIndex import general_manager_name
from typing import Any, Literal


# Thread-lokale Variable zur Speicherung der Abhängigkeiten
_dependency_storage = threading.local()


class DependencyTracker:
    def __enter__(
        self,
    ) -> set[
        tuple[general_manager_name, Literal["filter", "exclude", "identification"], str]
    ]:
        _dependency_storage.dependencies = set()
        return _dependency_storage.dependencies

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Optional: Aufräumen oder weitere Verarbeitung
        pass


def addDependency(class_name: str, operation: str, identifier: str) -> None:
    """
    Adds a dependency to the dependency storage.
    """
    if hasattr(_dependency_storage, "dependencies"):
        dependencies: set[
            tuple[general_manager_name, Literal["filter", "exclude", "id"], str]
        ] = _dependency_storage.dependencies

        _dependency_storage.dependencies.add((class_name, operation, identifier))
