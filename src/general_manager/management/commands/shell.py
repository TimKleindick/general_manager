from __future__ import annotations

from typing import Any, Callable, cast

from django.apps import apps
from django.core.management.commands.shell import Command as DjangoShellCommand
from general_manager.manager.meta import GeneralManagerMeta


class Command(DjangoShellCommand):
    """Prefer GeneralManager wrappers over raw models in shell auto-imports."""

    def get_auto_imports(self) -> list[str] | None:
        auto_imports_getter = cast(
            Callable[[], list[str] | None] | None,
            getattr(super(), "get_auto_imports", None),
        )
        auto_imports = auto_imports_getter() if auto_imports_getter else None
        if auto_imports is None:
            return None

        wrapper_paths = {
            f"{model.__module__}.{model.__name__}": (
                f"{cast(Any, manager_cls).__module__}.{cast(Any, manager_cls).__name__}"
            )
            for model in apps.get_models()
            if (manager_cls := getattr(model, "_general_manager_class", None))
            is not None
        }
        resolved_imports = [wrapper_paths.get(path, path) for path in auto_imports]
        seen_paths = set(resolved_imports)
        manager_paths = [
            f"{cast(Any, manager_cls).__module__}.{cast(Any, manager_cls).__name__}"
            for manager_cls in reversed(GeneralManagerMeta.all_classes)
        ]
        for manager_path in manager_paths:
            if manager_path in seen_paths:
                continue
            resolved_imports.append(manager_path)
            seen_paths.add(manager_path)
        return resolved_imports
