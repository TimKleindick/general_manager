from __future__ import annotations

import os
from typing import Callable, ClassVar

from django.test import SimpleTestCase
from django.core.management.base import BaseCommand

from general_manager.apps import GeneralmanagerConfig
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.interfaces.read_only import (
    ReadOnlyInterface,
)
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)
from general_manager.interface.infrastructure.startup_hooks import (
    clear_startup_hooks,
    register_startup_hook,
    registered_startup_hooks,
)
from general_manager.interface.infrastructure.system_checks import (
    clear_system_checks,
    register_system_check,
    registered_system_checks,
)


class DummyStartupCapability(BaseCapability):
    name: ClassVar[str] = "dummy_startup"
    calls: ClassVar[list[type[InterfaceBase]]] = []
    check_calls: ClassVar[list[type[InterfaceBase]]] = []

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
        super().setup(interface_cls)

    def get_startup_hooks(
        self,
        interface_cls: type[InterfaceBase],
    ) -> tuple[Callable[[], None], ...]:
        def _hook(
            interface_cls: type[InterfaceBase] = interface_cls,
        ) -> None:
            DummyStartupCapability.calls.append(interface_cls)

        return (_hook,)

    def get_system_checks(
        self,
        interface_cls: type[InterfaceBase],
    ) -> tuple[Callable[[], list], ...]:
        def _check(
            interface_cls: type[InterfaceBase] = interface_cls,
        ) -> list:
            DummyStartupCapability.check_calls.append(interface_cls)
            return []

        return (_check,)


class DummyStartupInterface(InterfaceBase):
    _interface_type = "dummy"
    input_fields: ClassVar[dict[str, object]] = {}
    configured_capabilities: ClassVar[tuple[InterfaceCapabilityConfig, ...]] = (
        InterfaceCapabilityConfig(DummyStartupCapability),
    )


def _reset_dummy_interface_state() -> None:
    DummyStartupInterface._capabilities = frozenset()
    DummyStartupInterface._capability_handlers = {}
    DummyStartupInterface._capability_selection = None
    DummyStartupInterface._configured_capabilities_applied = False


class StartupHookRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        clear_startup_hooks()
        DummyStartupCapability.calls = []
        DummyStartupCapability.check_calls = []
        _reset_dummy_interface_state()

    def tearDown(self) -> None:
        clear_startup_hooks()
        DummyStartupCapability.check_calls = []

    def test_capability_registers_startup_hook(self) -> None:
        DummyStartupInterface.get_capabilities()
        hooks = registered_startup_hooks()
        self.assertIn(DummyStartupInterface, hooks)
        for hook in hooks[DummyStartupInterface]:
            hook()
        self.assertEqual(DummyStartupCapability.calls, [DummyStartupInterface])

    def test_read_only_management_exposes_sync_hook(self) -> None:
        capability = ReadOnlyManagementCapability()
        calls: list[str] = []

        def _sync(interface_cls: type[InterfaceBase]) -> None:
            calls.append(interface_cls.__name__)

        capability.sync_data = _sync  # type: ignore[assignment]

        class ReadyInterface(ReadOnlyInterface):
            pass

        class _DummyManager:
            pass

        class _DummyModel:
            pass

        ReadyInterface._parent_class = _DummyManager
        ReadyInterface._model = _DummyModel

        hooks = capability.get_startup_hooks(ReadyInterface)
        for hook in hooks:
            hook()
        self.assertEqual(calls, ["ReadyInterface"])


class StartupHookRunnerTests(SimpleTestCase):
    def setUp(self) -> None:
        clear_startup_hooks()
        self._original_run = BaseCommand.run_from_argv
        for attr in (
            "_gm_startup_hooks_runner_installed",
            "_gm_original_run_from_argv",
        ):
            if hasattr(BaseCommand, attr):
                delattr(BaseCommand, attr)

    def tearDown(self) -> None:
        BaseCommand.run_from_argv = self._original_run
        clear_startup_hooks()
        os.environ.pop("RUN_MAIN", None)

    def test_runner_executes_hooks_for_regular_commands(self) -> None:
        calls: list[str] = []
        register_startup_hook(DummyStartupInterface, lambda: calls.append("ran"))

        def fake_run(self: BaseCommand, argv: list[str]) -> str:
            return "ok"

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        GeneralmanagerConfig.install_startup_hook_runner()
        result = BaseCommand().run_from_argv(["manage.py", "custom"])
        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["ran"])

    def test_runner_skips_runserver_autoreload(self) -> None:
        calls: list[str] = []
        register_startup_hook(DummyStartupInterface, lambda: calls.append("ran"))

        def fake_run(self: BaseCommand, argv: list[str]) -> None:
            return None

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        GeneralmanagerConfig.install_startup_hook_runner()
        BaseCommand().run_from_argv(["manage.py", "runserver"])
        self.assertEqual(calls, [])

    def test_runner_executes_runserver_main_process(self) -> None:
        calls: list[str] = []
        register_startup_hook(DummyStartupInterface, lambda: calls.append("ran"))

        def fake_run(self: BaseCommand, argv: list[str]) -> None:
            return None

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        os.environ["RUN_MAIN"] = "true"
        GeneralmanagerConfig.install_startup_hook_runner()
        BaseCommand().run_from_argv(["manage.py", "runserver"])
        self.assertEqual(calls, ["ran"])


class SystemCheckRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        clear_system_checks()
        _reset_dummy_interface_state()

    def tearDown(self) -> None:
        clear_system_checks()

    def test_capability_registers_system_check(self) -> None:
        DummyStartupInterface.get_capabilities()
        checks = registered_system_checks()
        self.assertIn(DummyStartupInterface, checks)
        for check in checks[DummyStartupInterface]:
            result = check()
            self.assertEqual(result, [])
        self.assertEqual(
            DummyStartupCapability.check_calls,
            [DummyStartupInterface] * len(checks[DummyStartupInterface]),
        )
