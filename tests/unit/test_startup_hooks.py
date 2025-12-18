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
    order_interfaces_by_dependency,
    register_startup_hook,
    registered_startup_hook_entries,
    registered_startup_hooks,
    StartupHookEntry,
)
from general_manager.interface.infrastructure.system_checks import (
    clear_system_checks,
    registered_system_checks,
)


class DummyStartupCapability(BaseCapability):
    name: ClassVar[str] = "dummy_startup"
    calls: ClassVar[list[type[InterfaceBase]]] = []
    check_calls: ClassVar[list[type[InterfaceBase]]] = []

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
        """
        Register this capability for the given interface class.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class this capability applies to.
        """
        super().setup(interface_cls)

    def get_startup_hooks(
        self,
        interface_cls: type[InterfaceBase],
    ) -> tuple[Callable[[], None], ...]:
        """
        Provide startup hook functions for the given interface class.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class for which to create startup hooks.

        Returns:
            tuple[Callable[[], None], ...]: A tuple of zero-argument callables that, when invoked, record the provided interface class on DummyStartupCapability.calls.
        """

        def _hook(
            interface_cls: type[InterfaceBase] = interface_cls,
        ) -> None:
            DummyStartupCapability.calls.append(interface_cls)

        return (_hook,)

    def get_system_checks(
        self,
        interface_cls: type[InterfaceBase],
    ) -> tuple[Callable[[], list], ...]:
        """
        Provide system check callbacks for the given interface.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class for which system checks are produced.

        Returns:
            tuple[Callable[[], list], ...]: A one-tuple containing a checker that appends the provided interface class to
            `DummyStartupCapability.check_calls` and returns an empty list (indicating no system check errors).
        """

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
    """
    Reset all cached and configuration-related class state on DummyStartupInterface.

    This clears the capability cache and handler mappings, clears any selected capability, and marks configured capabilities as not applied.
    """
    DummyStartupInterface._capabilities = frozenset()
    DummyStartupInterface._capability_handlers = {}
    DummyStartupInterface._capability_selection = None
    DummyStartupInterface._configured_capabilities_applied = False


class StartupHookRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Prepare test fixture by clearing registered startup hooks, resetting DummyStartupCapability call logs,
        and restoring DummyStartupInterface to its initial (unconfigured) state.
        """
        clear_startup_hooks()
        DummyStartupCapability.calls = []
        DummyStartupCapability.check_calls = []
        _reset_dummy_interface_state()

    def tearDown(self) -> None:
        """
        Tear down test state by clearing registered startup hooks and resetting dummy capability check call records.
        """
        clear_startup_hooks()
        DummyStartupCapability.check_calls = []

    def test_capability_registers_startup_hook(self) -> None:
        """
        Verifies that DummyStartupCapability registers a startup hook for DummyStartupInterface and that invoking the registered hooks appends DummyStartupInterface to the capability's call log.
        """
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
            """
            Record the given interface class's name in the surrounding `calls` list.

            Parameters:
                interface_cls (type[InterfaceBase]): Interface class whose `__name__` will be appended to `calls`.
            """
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

    def test_order_interfaces_by_dependency_runs_dependencies_first(self) -> None:
        """
        Ensure interfaces are ordered so dependencies are listed before the interfaces that depend on them.

        Sets up three InterfaceBase subclasses where A depends on B and B depends on C, runs order_interfaces_by_dependency with a resolver for that dependency map, and asserts the resulting order is [InterfaceC, InterfaceB, InterfaceA].
        """

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

        class InterfaceC(InterfaceBase):
            _interface_type = "c"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        dependency_map = {
            InterfaceA: {InterfaceB},
            InterfaceB: {InterfaceC},
            InterfaceC: set(),
        }

        def _resolver(interface_cls: type[InterfaceBase]) -> set[type[InterfaceBase]]:
            """
            Get the set of interface classes that the given interface depends on.

            Parameters:
                interface_cls (type[InterfaceBase]): Interface class to resolve dependencies for.

            Returns:
                dependencies (set[type[InterfaceBase]]): Set of interface classes that `interface_cls` depends on; empty set if no dependencies are registered.
            """
            return dependency_map.get(interface_cls, set())

        ordered = order_interfaces_by_dependency(
            [InterfaceA, InterfaceB, InterfaceC],
            _resolver,
        )
        self.assertEqual(ordered, [InterfaceC, InterfaceB, InterfaceA])


class StartupHookRunnerTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Prepare test environment for startup hook runner tests.

        Clears registered startup hooks, saves the original BaseCommand.run_from_argv method for restoration, and removes any internal runner-related attributes from BaseCommand so tests start with a clean runner state.
        """
        clear_startup_hooks()
        self._original_run = BaseCommand.run_from_argv
        for attr in (
            "_gm_startup_hooks_runner_installed",
            "_gm_original_run_from_argv",
        ):
            if hasattr(BaseCommand, attr):
                delattr(BaseCommand, attr)

    def tearDown(self) -> None:
        """
        Restore global state modified by StartupHookRunnerTests by restoring BaseCommand.run_from_argv, clearing registered startup hooks, and removing the RUN_MAIN environment variable.

        This is executed as test teardown to undo modifications performed during setUp so subsequent tests run with a clean environment.
        """
        BaseCommand.run_from_argv = self._original_run
        clear_startup_hooks()
        os.environ.pop("RUN_MAIN", None)

    def test_runner_executes_hooks_for_regular_commands(self) -> None:
        calls: list[str] = []
        register_startup_hook(DummyStartupInterface, lambda: calls.append("ran"))

        def fake_run(self: BaseCommand, argv: list[str]) -> str:
            """
            Stub replacement for BaseCommand.run_from_argv used in tests.

            Returns:
                'ok' — a constant success marker.
            """
            return "ok"

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        GeneralmanagerConfig.install_startup_hook_runner()
        result = BaseCommand().run_from_argv(["manage.py", "custom"])
        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["ran"])

    def test_runner_skips_runserver_autoreload(self) -> None:
        """
        Verifies that the startup hook runner does not execute registered hooks for the runserver autoreload process.

        Registers a startup hook for DummyStartupInterface, mocks BaseCommand.run_from_argv to simulate the autoreload invocation for the "runserver" command, installs the startup hook runner, and asserts the hook was not called.
        """
        calls: list[str] = []
        register_startup_hook(DummyStartupInterface, lambda: calls.append("ran"))

        def fake_run(self: BaseCommand, argv: list[str]) -> None:
            """
            No-op replacement for BaseCommand.run_from_argv.

            Parameters:
                self (BaseCommand): The command instance.
                argv (list[str]): The argument vector intended for the command runner; ignored by this no-op.
            """
            return None

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        GeneralmanagerConfig.install_startup_hook_runner()
        BaseCommand().run_from_argv(["manage.py", "runserver"])
        self.assertEqual(calls, [])

    def test_runner_executes_runserver_main_process(self) -> None:
        calls: list[str] = []
        register_startup_hook(DummyStartupInterface, lambda: calls.append("ran"))

        def fake_run(self: BaseCommand, argv: list[str]) -> None:
            """
            No-op replacement for BaseCommand.run_from_argv.

            Parameters:
                self (BaseCommand): The command instance.
                argv (list[str]): The argument vector intended for the command runner; ignored by this no-op.
            """
            return None

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        os.environ["RUN_MAIN"] = "true"
        GeneralmanagerConfig.install_startup_hook_runner()
        BaseCommand().run_from_argv(["manage.py", "runserver"])
        self.assertEqual(calls, ["ran"])


class SystemCheckRegistryTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Prepare test state by clearing all registered system checks and resetting DummyStartupInterface internal state.
        """
        clear_system_checks()
        _reset_dummy_interface_state()

    def tearDown(self) -> None:
        """
        Restore global system check registry to an empty state after each test.
        """
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


class StartupHookEntryTests(SimpleTestCase):
    """Tests for the StartupHookEntry dataclass."""

    def test_entry_creation_with_resolver(self) -> None:
        """Verify StartupHookEntry stores hook and dependency resolver correctly."""

        def hook() -> None:
            return None

        def resolver(iface: object) -> set[object]:
            return set()

        entry = StartupHookEntry(hook, resolver)
        self.assertIs(entry.hook, hook)
        self.assertIs(entry.dependency_resolver, resolver)

    def test_entry_creation_without_resolver(self) -> None:
        """Verify StartupHookEntry accepts None as dependency resolver."""

        def hook() -> None:
            return None

        entry = StartupHookEntry(hook, None)
        self.assertIs(entry.hook, hook)
        self.assertIsNone(entry.dependency_resolver)


class RegisteredStartupHookEntriesTests(SimpleTestCase):
    """Tests for registered_startup_hook_entries function."""

    def setUp(self) -> None:
        """Clear startup hooks before each test."""
        clear_startup_hooks()

    def tearDown(self) -> None:
        """Clear startup hooks after each test."""
        clear_startup_hooks()

    def test_returns_empty_dict_when_no_hooks_registered(self) -> None:
        """Verify registered_startup_hook_entries returns empty dict initially."""
        entries = registered_startup_hook_entries()
        self.assertEqual(entries, {})

    def test_returns_entries_with_resolvers(self) -> None:
        """Verify registered_startup_hook_entries includes dependency resolvers."""

        def hook1() -> None:
            return None

        def hook2() -> None:
            return None

        def resolver1(iface: object) -> set[object]:
            return set()

        def resolver2(iface: object) -> set[object]:
            return {DummyStartupInterface}

        register_startup_hook(
            DummyStartupInterface,
            hook1,
            dependency_resolver=resolver1,
        )
        register_startup_hook(
            DummyStartupInterface,
            hook2,
            dependency_resolver=resolver2,
        )

        entries = registered_startup_hook_entries()
        self.assertIn(DummyStartupInterface, entries)
        self.assertEqual(len(entries[DummyStartupInterface]), 2)
        self.assertIs(entries[DummyStartupInterface][0].hook, hook1)
        self.assertIs(entries[DummyStartupInterface][0].dependency_resolver, resolver1)
        self.assertIs(entries[DummyStartupInterface][1].hook, hook2)
        self.assertIs(entries[DummyStartupInterface][1].dependency_resolver, resolver2)

    def test_prevents_duplicate_hook_with_same_resolver(self) -> None:
        """Verify same hook with same resolver is not registered twice."""

        def hook() -> None:
            return None

        def resolver(iface: object) -> set[object]:
            return set()

        register_startup_hook(DummyStartupInterface, hook, dependency_resolver=resolver)
        register_startup_hook(DummyStartupInterface, hook, dependency_resolver=resolver)

        entries = registered_startup_hook_entries()
        self.assertEqual(len(entries[DummyStartupInterface]), 1)

    def test_allows_same_hook_with_different_resolvers(self) -> None:
        """Verify same hook can be registered with different resolvers."""

        def hook() -> None:
            return None

        def resolver1(iface: object) -> set[object]:
            return set()

        def resolver2(iface: object) -> set[object]:
            return {DummyStartupInterface}

        register_startup_hook(
            DummyStartupInterface, hook, dependency_resolver=resolver1
        )
        register_startup_hook(
            DummyStartupInterface, hook, dependency_resolver=resolver2
        )

        entries = registered_startup_hook_entries()
        self.assertEqual(len(entries[DummyStartupInterface]), 2)


class OrderInterfacesByDependencyEdgeCasesTests(SimpleTestCase):
    """Edge case tests for order_interfaces_by_dependency."""

    def test_handles_empty_list(self) -> None:
        """Verify empty interface list returns empty list."""

        def resolver(iface: object) -> set[object]:
            return set()

        ordered = order_interfaces_by_dependency([], resolver)
        self.assertEqual(ordered, [])

    def test_preserves_order_when_no_resolver(self) -> None:
        """Verify input order preserved when resolver is None."""

        class A(InterfaceBase):
            _interface_type = "a"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class B(InterfaceBase):
            _interface_type = "b"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        ordered = order_interfaces_by_dependency([B, A], None)
        self.assertEqual(ordered, [B, A])

    def test_handles_self_dependency(self) -> None:
        """Verify interface depending on itself is handled gracefully."""

        class SelfDependent(InterfaceBase):
            _interface_type = "self"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        def resolver(iface: object) -> set[object]:
            return {iface} if iface == SelfDependent else set()

        ordered = order_interfaces_by_dependency([SelfDependent], resolver)
        self.assertIn(SelfDependent, ordered)

    def test_handles_circular_dependencies(self) -> None:
        """Verify circular dependencies don't cause infinite loop."""

        class A(InterfaceBase):
            _interface_type = "a"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class B(InterfaceBase):
            _interface_type = "b"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        deps = {A: {B}, B: {A}}

        def resolver(iface: object) -> set[type[InterfaceBase]]:
            return deps.get(iface, set())

        ordered = order_interfaces_by_dependency([A, B], resolver)
        self.assertEqual(len(ordered), 2)
        self.assertIn(A, ordered)
        self.assertIn(B, ordered)

    def test_handles_missing_dependencies(self) -> None:
        """Verify dependencies not in interface list are ignored."""

        class A(InterfaceBase):
            _interface_type = "a"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class B(InterfaceBase):
            _interface_type = "b"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class C(InterfaceBase):
            _interface_type = "c"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        deps = {A: {C}}

        def resolver(iface: object) -> set[type[InterfaceBase]]:
            return deps.get(iface, set())

        ordered = order_interfaces_by_dependency([A, B], resolver)
        self.assertEqual(len(ordered), 2)
        self.assertIn(A, ordered)
        self.assertIn(B, ordered)

    def test_complex_dependency_graph(self) -> None:
        """Verify complex multi-level dependencies are ordered correctly."""

        class A(InterfaceBase):
            _interface_type = "a"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class B(InterfaceBase):
            _interface_type = "b"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class C(InterfaceBase):
            _interface_type = "c"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        class D(InterfaceBase):
            _interface_type = "d"
            input_fields: ClassVar[dict[str, object]] = {}
            configured_capabilities: ClassVar[
                tuple[InterfaceCapabilityConfig, ...]
            ] = ()

        deps = {
            D: {C, A},
            C: {B},
            B: {A},
            A: set(),
        }

        def resolver(iface: object) -> set[type[InterfaceBase]]:
            return deps.get(iface, set())

        ordered = order_interfaces_by_dependency([D, C, B, A], resolver)

        a_idx = ordered.index(A)
        b_idx = ordered.index(B)
        c_idx = ordered.index(C)
        d_idx = ordered.index(D)

        self.assertLess(a_idx, b_idx)
        self.assertLess(a_idx, c_idx)
        self.assertLess(a_idx, d_idx)
        self.assertLess(b_idx, c_idx)
        self.assertLess(b_idx, d_idx)
        self.assertLess(c_idx, d_idx)


class StartupHookRunnerDependencyOrderingTests(SimpleTestCase):
    """Tests for startup hook runner with dependency ordering."""

    def setUp(self) -> None:
        """Prepare test environment."""
        clear_startup_hooks()
        self._original_run = BaseCommand.run_from_argv
        for attr in (
            "_gm_startup_hooks_runner_installed",
            "_gm_original_run_from_argv",
        ):
            if hasattr(BaseCommand, attr):
                delattr(BaseCommand, attr)

    def tearDown(self) -> None:
        """Restore global state."""
        BaseCommand.run_from_argv = self._original_run
        clear_startup_hooks()
        os.environ.pop("RUN_MAIN", None)

    def test_runner_orders_hooks_by_dependency(self) -> None:
        """Verify runner executes hooks in dependency order."""

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

        def fake_run(self: BaseCommand, argv: list[str]) -> str:
            return "ok"

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        GeneralmanagerConfig.install_startup_hook_runner()
        BaseCommand().run_from_argv(["manage.py", "migrate"])

        self.assertEqual(execution_order, ["A", "B"])

    def test_runner_groups_hooks_by_resolver(self) -> None:
        """Verify hooks with different resolvers are grouped separately."""

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

        execution_log: list[tuple[str, object]] = []

        def hook_x1() -> None:
            execution_log.append(("X1", resolver1))

        def hook_x2() -> None:
            execution_log.append(("X2", resolver2))

        def hook_y() -> None:
            execution_log.append(("Y", None))

        def resolver1(iface: object) -> set[object]:
            return set()

        def resolver2(iface: object) -> set[object]:
            return set()

        register_startup_hook(InterfaceX, hook_x1, dependency_resolver=resolver1)
        register_startup_hook(InterfaceX, hook_x2, dependency_resolver=resolver2)
        register_startup_hook(InterfaceY, hook_y, dependency_resolver=None)

        def fake_run(self: BaseCommand, argv: list[str]) -> str:
            return "ok"

        BaseCommand.run_from_argv = fake_run  # type: ignore[assignment]
        GeneralmanagerConfig.install_startup_hook_runner()
        BaseCommand().run_from_argv(["manage.py", "test"])

        self.assertEqual(len(execution_log), 3)
        executed_names = {log[0] for log in execution_log}
        self.assertEqual(executed_names, {"X1", "X2", "Y"})
