from __future__ import annotations

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
