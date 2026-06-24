from __future__ import annotations

from collections.abc import Callable
from typing import cast

from django.apps import apps
from django.core.management.commands.shell import Command as DjangoShellCommand
from general_manager.manager.meta import GeneralManagerMeta


def _qualified_class_path(class_: type[object]) -> str:
    """Return the import path Django shell auto-imports expect for a class."""
    return f"{class_.__module__}.{class_.__name__}"


class Command(DjangoShellCommand):
    """Prefer GeneralManager wrappers over raw models in shell auto-imports."""

    def get_auto_imports(self) -> list[str] | None:
        """Return Django shell auto-imports with manager wrappers preferred.

        Returns:
            ``None`` when Django's base shell command disables auto-imports or
            exposes no auto-import provider. Otherwise, a list where raw Django
            model import paths that expose a ``_general_manager_class`` type are
            replaced by that wrapper class, followed by registered manager
            classes missing from the resolved import list. Manager classes are
            appended in reverse registration order, matching Django shell's
            later-import-wins behavior for duplicate class names.

        Raises:
            Exception: Propagates errors from Django's base auto-import
                provider and app-registry model discovery.
        """
        auto_imports_getter = cast(
            Callable[[], list[str] | None] | None,
            getattr(super(), "get_auto_imports", None),
        )
        auto_imports = auto_imports_getter() if auto_imports_getter else None
        if auto_imports is None:
            return None

        wrapper_paths = {
            _qualified_class_path(model): _qualified_class_path(manager_cls)
            for model in apps.get_models()
            if isinstance(
                manager_cls := getattr(model, "_general_manager_class", None),
                type,
            )
        }
        resolved_imports = [wrapper_paths.get(path, path) for path in auto_imports]
        seen_paths = set(resolved_imports)
        manager_paths = [
            _qualified_class_path(manager_cls)
            for manager_cls in reversed(GeneralManagerMeta.all_classes)
        ]
        for manager_path in manager_paths:
            if manager_path in seen_paths:
                continue
            resolved_imports.append(manager_path)
            seen_paths.add(manager_path)
        return resolved_imports
