import threading
from general_manager.cache.dependencyIndex import (
    general_manager_name,
    Dependency,
    filter_type,
)

# Thread-lokale Variable zur Speicherung der Abhängigkeiten
_dependency_storage = threading.local()


class DependencyTracker:
    def __enter__(
        self,
    ) -> set[Dependency]:
        if not hasattr(_dependency_storage, "dependencies"):
            _dependency_storage._depth = 0
            _dependency_storage.dependencies = list()
        else:
            _dependency_storage._depth += 1
        _dependency_storage.dependencies.append(set())
        return _dependency_storage.dependencies[_dependency_storage._depth]

    def __exit__(self, exc_type, exc_val, exc_tb):
        if hasattr(_dependency_storage, "dependencies"):
            if _dependency_storage._depth == 0:
                # Wenn wir die oberste Ebene verlassen, löschen wir die Abhängigkeiten
                del _dependency_storage.dependencies
                del _dependency_storage._depth

            else:
                # Ansonsten reduzieren wir nur die Tiefe
                _dependency_storage._depth -= 1
                _dependency_storage.dependencies.pop()

    @staticmethod
    def track(
        class_name: general_manager_name,
        operation: filter_type,
        identifier: str,
    ) -> None:
        """
        Adds a dependency to the dependency storage.
        """
        if hasattr(_dependency_storage, "dependencies"):
            for dep_set in _dependency_storage.dependencies[
                : _dependency_storage._depth + 1
            ]:
                dep_set: set[Dependency]
                dep_set.add((class_name, operation, identifier))
