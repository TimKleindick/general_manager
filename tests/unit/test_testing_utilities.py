"""Unit tests for general_manager.utils.testing module updates."""

from typing import Callable, ClassVar

from django.test import SimpleTestCase

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.interface.infrastructure.startup_hooks import (
    clear_startup_hooks,
    register_startup_hook,
)


class TestingUtilityDependencyOrderingTests(SimpleTestCase):
    """Tests for _run_registered_startup_hooks with dependency ordering."""

    def setUp(self) -> None:
        """Clear startup hooks before each test."""
        clear_startup_hooks()

    def tearDown(self) -> None:
        """Clear startup hooks after each test."""
        clear_startup_hooks()

    def test_run_hooks_orders_by_dependency(self) -> None:
        """Verify _run_registered_startup_hooks executes in dependency order."""
        from general_manager.utils.testing import GeneralManagerTransactionTestCase

        class InterfaceA(InterfaceBase):
            _interface_type = "a"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class InterfaceB(InterfaceBase):
            _interface_type = "b"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        execution_order: list[str] = []

        def hook_a() -> None:
            execution_order.append("A")

        def hook_b() -> None:
            execution_order.append("B")

        deps = {InterfaceB: {InterfaceA}, InterfaceA: set()}

        def resolver(iface: object) -> set[type[InterfaceBase]]:
            return deps.get(iface, set())

        register_startup_hook(InterfaceA, hook_a, dependency_resolver=resolver)
        register_startup_hook(InterfaceB, hook_b, dependency_resolver=resolver)

        class FakeTestCase(GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list] = []

            class FakeManager:
                Interface = InterfaceA

            class FakeManagerB:
                Interface = InterfaceB

        FakeTestCase.general_manager_classes = [
            FakeTestCase.FakeManagerB,
            FakeTestCase.FakeManager,
        ]

        FakeTestCase._run_registered_startup_hooks()

        self.assertEqual(execution_order, ["A", "B"])

    def test_run_hooks_groups_by_resolver(self) -> None:
        """Verify hooks with different resolvers are grouped and ordered independently."""

        class InterfaceX(InterfaceBase):
            _interface_type = "x"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class InterfaceY(InterfaceBase):
            _interface_type = "y"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        execution_log: list[str] = []

        def hook_x1() -> None:
            execution_log.append("X1")

        def hook_x2() -> None:
            execution_log.append("X2")

        def hook_y() -> None:
            execution_log.append("Y")

        def resolver1(iface: object) -> set[object]:
            return set()

        def resolver2(iface: object) -> set[object]:
            return set()

        register_startup_hook(InterfaceX, hook_x1, dependency_resolver=resolver1)
        register_startup_hook(InterfaceX, hook_x2, dependency_resolver=resolver2)
        register_startup_hook(InterfaceY, hook_y, dependency_resolver=None)

        from general_manager.utils.testing import GeneralManagerTransactionTestCase

        class FakeTestCase(GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list] = []

            class FakeManagerX:
                Interface = InterfaceX

            class FakeManagerY:
                Interface = InterfaceY

        FakeTestCase.general_manager_classes = [
            FakeTestCase.FakeManagerX,
            FakeTestCase.FakeManagerY,
        ]

        FakeTestCase._run_registered_startup_hooks()

        self.assertEqual(len(execution_log), 3)
        self.assertIn("X1", execution_log)
        self.assertIn("X2", execution_log)
        self.assertIn("Y", execution_log)

    def test_run_hooks_ensures_capabilities_initialized(self) -> None:
        """Verify _run_registered_startup_hooks calls get_capabilities on interfaces."""

        class TestCapability(BaseCapability):
            name: ClassVar[str] = "test"
            setup_called: ClassVar[list[type]] = []

            def setup(self, interface_cls: type[InterfaceBase]) -> None:
                TestCapability.setup_called.append(interface_cls)
                super().setup(interface_cls)

            def get_startup_hooks(
                self, interface_cls: type[InterfaceBase]
            ) -> tuple[Callable[[], None], ...]:
                return (lambda: None,)

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[tuple[InterfaceCapabilityConfig, ...]] = (
                InterfaceCapabilityConfig(TestCapability),
            )

        from general_manager.utils.testing import GeneralManagerTransactionTestCase

        class FakeTestCase(GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list] = []

            class FakeManager:
                Interface = TestInterface

        FakeTestCase.general_manager_classes = [FakeTestCase.FakeManager]

        TestInterface._capabilities = frozenset()
        TestInterface._capability_handlers = {}
        TestInterface._configured_capabilities_applied = False
        TestCapability.setup_called = []

        FakeTestCase._run_registered_startup_hooks()

        self.assertIn(TestInterface, TestCapability.setup_called)

    def test_run_hooks_skips_non_interface_classes(self) -> None:
        """Verify _run_registered_startup_hooks skips managers without Interface."""
        from general_manager.utils.testing import GeneralManagerTransactionTestCase

        execution_log: list[str] = []

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        def hook() -> None:
            execution_log.append("ran")

        register_startup_hook(TestInterface, hook)

        class FakeTestCase(GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list] = []

            class FakeManagerNoInterface:
                pass

            class FakeManagerWithInterface:
                Interface = TestInterface

        FakeTestCase.general_manager_classes = [
            FakeTestCase.FakeManagerNoInterface,
            FakeTestCase.FakeManagerWithInterface,
        ]

        FakeTestCase._run_registered_startup_hooks()

        self.assertEqual(execution_log, ["ran"])

    def test_run_hooks_deduplicates_interfaces(self) -> None:
        """Verify _run_registered_startup_hooks doesn't run same interface twice."""
        from general_manager.utils.testing import GeneralManagerTransactionTestCase

        execution_count: list[int] = [0]

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        def hook() -> None:
            execution_count[0] += 1

        register_startup_hook(TestInterface, hook)

        class FakeTestCase(GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list] = []

            class FakeManager1:
                Interface = TestInterface

            class FakeManager2:
                Interface = TestInterface

        FakeTestCase.general_manager_classes = [
            FakeTestCase.FakeManager1,
            FakeTestCase.FakeManager2,
        ]

        FakeTestCase._run_registered_startup_hooks()

        self.assertEqual(execution_count[0], 1)

    def test_public_run_hooks_accepts_interfaces(self) -> None:
        """Verify run_registered_startup_hooks executes hooks for explicit interfaces."""
        from general_manager.utils.testing import run_registered_startup_hooks

        execution_log: list[str] = []

        class TestInterface(InterfaceBase):
            _interface_type = "test"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        def hook() -> None:
            """
            Append the string "ran" to the surrounding `execution_log` list to record that the hook executed.

            This function is intended to be used as a startup hook that records invocation by mutating the enclosing `execution_log`.
            """
            execution_log.append("ran")

        register_startup_hook(TestInterface, hook)

        run_registered_startup_hooks(interfaces=[TestInterface])

        self.assertEqual(execution_log, ["ran"])

    def test_public_run_hooks_combines_managers_and_interfaces(self) -> None:
        """Verify run_registered_startup_hooks merges manager and interface inputs."""
        from general_manager.utils.testing import run_registered_startup_hooks

        execution_log: list[str] = []

        class InterfaceA(InterfaceBase):
            _interface_type = "a"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class InterfaceB(InterfaceBase):
            _interface_type = "b"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        def hook_a() -> None:
            """
            Append the string "A" to the shared execution_log list.

            This function records that hook A ran by mutating the module-level `execution_log` list.
            """
            execution_log.append("A")

        def hook_b() -> None:
            """
            Record execution by appending "B" to the shared `execution_log` list.
            """
            execution_log.append("B")

        register_startup_hook(InterfaceA, hook_a)
        register_startup_hook(InterfaceB, hook_b)

        class FakeManager:
            Interface = InterfaceA

        run_registered_startup_hooks(
            managers=[FakeManager],
            interfaces=[InterfaceB, InterfaceA],
        )

        self.assertEqual(sorted(execution_log), ["A", "B"])

    def test_run_hooks_calls_registered_startup_hooks(self) -> None:
        """Verify _run_registered_startup_hooks forwards managers list."""
        from general_manager.utils import testing as testing_module
        from general_manager.utils.testing import GeneralManagerTransactionTestCase

        calls: list[list[type]] = []

        def _record_hooks(*, managers: list[type]) -> None:
            calls.append(managers)

        class FakeTestCase(GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list[type]] = [object]

        original = testing_module.run_registered_startup_hooks
        testing_module.run_registered_startup_hooks = _record_hooks
        try:
            FakeTestCase._run_registered_startup_hooks()
        finally:
            testing_module.run_registered_startup_hooks = original

        self.assertEqual(calls, [[object]])
