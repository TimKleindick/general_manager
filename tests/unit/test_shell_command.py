from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from general_manager.management.commands.shell import Command


def test_get_auto_imports_appends_general_manager_classes() -> None:
    command = Command()
    fake_manager = type("RemoteThing", (), {"__module__": "tests.fake_managers"})

    with (
        patch(
            "django.core.management.commands.shell.Command.get_auto_imports",
            return_value=["django.conf.settings"],
        ),
        patch(
            "general_manager.management.commands.shell.GeneralManagerMeta.all_classes",
            [fake_manager],
        ),
    ):
        auto_imports = command.get_auto_imports()

    assert auto_imports == [
        "django.conf.settings",
        "tests.fake_managers.RemoteThing",
    ]


def test_get_auto_imports_replaces_raw_model_paths_with_manager_wrappers() -> None:
    command = Command()
    fake_model = type("Project", (), {"__module__": "tests.fake_models"})
    fake_manager = type("Project", (), {"__module__": "tests.fake_managers"})
    fake_model._general_manager_class = fake_manager

    with (
        patch(
            "django.core.management.commands.shell.Command.get_auto_imports",
            return_value=[
                "tests.fake_models.Project",
                "django.conf.settings",
            ],
        ),
        patch(
            "general_manager.management.commands.shell.apps.get_models",
            return_value=[fake_model],
        ),
        patch(
            "general_manager.management.commands.shell.GeneralManagerMeta.all_classes",
            [fake_manager],
        ),
    ):
        auto_imports = command.get_auto_imports()

    assert auto_imports == [
        "tests.fake_managers.Project",
        "django.conf.settings",
    ]


def test_get_auto_imports_ignores_non_class_model_wrapper_markers() -> None:
    command = Command()
    fake_model = type("Project", (), {"__module__": "tests.fake_models"})
    fake_model._general_manager_class = SimpleNamespace(
        __module__="tests.fake_managers",
        __name__="Project",
    )

    with (
        patch(
            "django.core.management.commands.shell.Command.get_auto_imports",
            return_value=["tests.fake_models.Project"],
        ),
        patch(
            "general_manager.management.commands.shell.apps.get_models",
            return_value=[fake_model],
        ),
        patch(
            "general_manager.management.commands.shell.GeneralManagerMeta.all_classes",
            [],
        ),
    ):
        auto_imports = command.get_auto_imports()

    assert auto_imports == ["tests.fake_models.Project"]


def test_get_auto_imports_preserves_base_none() -> None:
    command = Command()

    with patch(
        "django.core.management.commands.shell.Command.get_auto_imports",
        return_value=None,
    ):
        auto_imports = command.get_auto_imports()

    assert auto_imports is None
