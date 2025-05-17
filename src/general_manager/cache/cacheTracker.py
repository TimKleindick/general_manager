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
        """
        Enters a new dependency tracking context and returns the set for collecting dependencies.

        Initializes thread-local storage for dependency tracking if not already present, supports nested contexts, and provides a set to accumulate dependencies at the current nesting level.

        Returns:
            The set used to collect dependencies for the current context level.
        """
        if not hasattr(_dependency_storage, "dependencies"):
            _dependency_storage._depth = 0
            _dependency_storage.dependencies = list()
        else:
            _dependency_storage._depth += 1
        _dependency_storage.dependencies.append(set())
        return _dependency_storage.dependencies[_dependency_storage._depth]

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exits the dependency tracking context, managing cleanup for nested scopes.

        If exiting the outermost context, removes all dependency tracking data from thread-local storage. Otherwise, decrements the nesting depth and removes the most recent dependency set.
        """
        if hasattr(_dependency_storage, "dependencies"):
            if _dependency_storage._depth == 0:
                self.reset_thread_local_storage()

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
        Records a dependency in all active dependency tracking contexts.

        Adds the specified dependency tuple to each set in the current stack of dependency tracking scopes, ensuring it is tracked at all nested levels.
        """
        if hasattr(_dependency_storage, "dependencies"):
            for dep_set in _dependency_storage.dependencies[
                : _dependency_storage._depth + 1
            ]:
                dep_set: set[Dependency]
                dep_set.add((class_name, operation, identifier))

    @staticmethod
    def reset_thread_local_storage() -> None:
        """
        Resets the thread-local storage for dependency tracking.

        This method clears the thread-local storage, ensuring that all dependency tracking data is removed. It is useful for cleaning up after operations that may have modified the state of the tracker.
        """
        if hasattr(_dependency_storage, "dependencies"):
            del _dependency_storage.dependencies
            del _dependency_storage._depth
