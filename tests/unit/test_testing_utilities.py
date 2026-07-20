"""Unit tests for general_manager.utils.testing module updates."""

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Callable, ClassVar, cast
from unittest.mock import MagicMock, Mock, patch

from django.apps import apps as global_apps
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
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta


class TestingUtilityDependencyOrderingTests(SimpleTestCase):
    """Tests for _run_registered_startup_hooks with dependency ordering."""

    def setUp(self) -> None:
        """Clear startup hooks before each test."""
        clear_startup_hooks()

    def tearDown(self) -> None:
        """Clear startup hooks after each test."""
        clear_startup_hooks()

    def test_graphene_cursor_cleanup_preserves_django_database_guard(self) -> None:
        """Cursor cleanup removes Graphene beneath Django's test guard."""
        from general_manager.utils import testing as testing_module

        plain_cursor = Mock(name="plain_cursor")
        graphene_cursor = Mock(name="graphene_cursor")
        database_guard = SimpleNamespace(wrapped=graphene_cursor)
        database_connection = SimpleNamespace(
            cursor=database_guard,
            _graphene_cursor=plain_cursor,
        )

        with patch.object(
            testing_module.connections,
            "all",
            return_value=[database_connection],
        ):
            testing_module._restore_graphene_cursor_wrappers()

        self.assertIs(database_connection.cursor, database_guard)
        self.assertIs(database_guard.wrapped, plain_cursor)
        self.assertFalse(hasattr(database_connection, "_graphene_cursor"))

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

    def test_run_hooks_does_not_skip_equal_distinct_resolver_objects(self) -> None:
        """Verify equal resolver objects still execute their own hook entries."""
        from general_manager.utils.testing import run_registered_startup_hooks

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

        class EqualResolver:
            def __call__(self, iface: object) -> set[type[InterfaceBase]]:
                return set()

            def __eq__(self, other: object) -> bool:
                return isinstance(other, EqualResolver)

            def __hash__(self) -> int:
                return 1

        execution_log: list[str] = []

        register_startup_hook(
            InterfaceA,
            lambda: execution_log.append("A"),
            dependency_resolver=EqualResolver(),
        )
        register_startup_hook(
            InterfaceB,
            lambda: execution_log.append("B"),
            dependency_resolver=EqualResolver(),
        )

        run_registered_startup_hooks(interfaces=[InterfaceA, InterfaceB])

        self.assertEqual(sorted(execution_log), ["A", "B"])

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


class GeneralManagerTransactionTestCaseTeardownTests(SimpleTestCase):
    """Tests for failure-safe dynamic model setup and teardown."""

    @staticmethod
    def _model(name: str, table: str) -> type:
        """Build the minimal model surface used at the mocked schema boundary."""

        class ModelMeta:
            db_table = table
            app_label = "general_manager"
            local_many_to_many: ClassVar[tuple[object, ...]] = ()

        return type(name, (), {"_meta": ModelMeta, "history": None})

    @staticmethod
    def _manager(model: type) -> type:
        """Build the minimal manager/interface surface used by the harness."""

        interface = type("Interface", (), {"_model": model})
        return type("Manager", (), {"Interface": interface})

    def test_teardown_drops_created_models_in_reverse_creation_order(self) -> None:
        """Dependent models are deleted before the models they reference."""
        from general_manager.utils import testing as testing_module

        parent_model = self._model("ParentModel", "parent_table")
        child_model = self._model("ChildModel", "child_table")
        parent_manager = self._manager(parent_model)
        child_manager = self._manager(child_model)

        class FakeTestCase(testing_module.GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list[type[GeneralManager]]] = [
                cast(type[GeneralManager], parent_manager),
                cast(type[GeneralManager], child_manager),
            ]

        FakeTestCase._gm_created_models = [parent_model, child_model]
        FakeTestCase._gm_created_tables = {"parent_table", "child_table"}

        editor = Mock()
        mocked_connection = MagicMock()
        mocked_connection.introspection.table_names.return_value = [
            "parent_table",
            "child_table",
        ]
        mocked_connection.constraint_checks_disabled.return_value = nullcontext()
        mocked_connection.schema_editor.return_value.__enter__.return_value = editor

        with (
            patch.object(testing_module, "connection", mocked_connection),
            patch.object(testing_module, "_default_graphql_url_clear"),
            patch.object(testing_module, "_default_remote_api_url_clear"),
            patch.object(global_apps, "clear_cache"),
            patch.object(testing_module.GraphQLTransactionTestCase, "tearDownClass"),
        ):
            FakeTestCase.tearDownClass()

        self.assertEqual(
            [call.args[0] for call in editor.delete_model.call_args_list],
            [child_model, parent_model],
        )
        mocked_connection.constraint_checks_disabled.assert_called_once_with()

    def test_teardown_runs_global_cleanup_when_model_deletion_raises(self) -> None:
        """A DDL failure does not leak dynamic model or manager state."""
        from general_manager.utils import testing as testing_module

        later_model = self._model("LaterModel", "later_table")
        failure_model = self._model("FailureModel", "failure_table")
        manager = self._manager(failure_model)

        class FakeTestCase(testing_module.GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list[type[GeneralManager]]] = [
                cast(type[GeneralManager], manager)
            ]

        FakeTestCase._gm_created_models = [later_model, failure_model]
        FakeTestCase._gm_created_tables = {"later_table", "failure_table"}

        editor = Mock()
        ddl_error = RuntimeError("database refused model deletion")
        editor.delete_model.side_effect = [ddl_error, None]
        mocked_connection = MagicMock()
        mocked_connection.introspection.table_names.return_value = [
            "later_table",
            "failure_table",
        ]
        mocked_connection.schema_editor.return_value.__enter__.return_value = editor

        app_config = global_apps.get_app_config("general_manager")
        all_models = global_apps.all_models["general_manager"]
        with (
            patch.object(testing_module, "connection", mocked_connection),
            patch.object(testing_module, "_default_graphql_url_clear") as graphql_clear,
            patch.object(
                testing_module, "_default_remote_api_url_clear"
            ) as remote_clear,
            patch.object(
                testing_module.GraphQLTransactionTestCase, "tearDownClass"
            ) as base_teardown,
            patch.object(global_apps, "clear_cache") as clear_cache,
            patch.object(
                GeneralManagerMeta,
                "all_classes",
                [manager, object],
            ),
            patch.object(
                GeneralManagerMeta,
                "pending_graphql_interfaces",
                [manager, object],
            ),
            patch.object(
                GeneralManagerMeta,
                "pending_attribute_initialization",
                [manager, object],
            ),
            patch.dict(all_models, {"failuremodel": failure_model}),
            patch.dict(app_config.models, {"failuremodel": failure_model}),
        ):
            with self.assertRaises(RuntimeError) as raised:
                FakeTestCase.tearDownClass()

            self.assertIs(raised.exception, ddl_error)
            self.assertNotIn("failuremodel", all_models)
            self.assertNotIn("failuremodel", app_config.models)
            self.assertNotIn(manager, GeneralManagerMeta.all_classes)
            self.assertNotIn(
                manager,
                GeneralManagerMeta.pending_graphql_interfaces,
            )
            self.assertNotIn(
                manager,
                GeneralManagerMeta.pending_attribute_initialization,
            )
            self.assertIs(
                global_apps.get_containing_app_config,
                testing_module._original_get_app,
            )
            self.assertEqual(FakeTestCase._gm_created_models, [])
            self.assertEqual(FakeTestCase._gm_created_tables, set())
            graphql_clear.assert_called_once_with()
            remote_clear.assert_called_once_with()
            clear_cache.assert_called_once_with()
            base_teardown.assert_called_once_with()
            self.assertEqual(
                [call.args[0] for call in editor.delete_model.call_args_list],
                [failure_model, later_model],
            )

    def test_setup_failure_rolls_back_tracked_models_and_global_state(self) -> None:
        """The first setup error survives comprehensive best-effort cleanup."""
        from general_manager.utils import testing as testing_module

        first_model = self._model("FirstModel", "first_table")
        second_model = self._model("SecondModel", "second_table")
        failing_model = self._model("FailingModel", "failing_table")
        managers = [
            self._manager(first_model),
            self._manager(second_model),
            self._manager(failing_model),
        ]

        class FakeTestCase(testing_module.GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list[type[GeneralManager]]] = cast(
                list[type[GeneralManager]],
                managers,
            )

        setup_error = RuntimeError("database refused model creation")
        cleanup_error = RuntimeError("database refused first rollback")
        editor = Mock()
        editor.create_model.side_effect = [None, None, setup_error]
        editor.delete_model.side_effect = [cleanup_error, None]
        mocked_connection = MagicMock()
        mocked_connection.introspection.table_names.side_effect = [
            [],
            ["first_table", "second_table"],
        ]
        mocked_connection.constraint_checks_disabled.return_value = nullcontext()
        mocked_connection.schema_editor.return_value.__enter__.return_value = editor

        with (
            patch.object(testing_module, "connection", mocked_connection),
            patch.object(testing_module.GraphQL, "reset_registry") as reset_registry,
            patch.object(
                testing_module,
                "_default_graphql_url_clear",
            ) as graphql_clear,
            patch.object(
                testing_module,
                "_default_remote_api_url_clear",
            ) as remote_clear,
            patch.object(global_apps, "clear_cache") as clear_cache,
            patch.object(GeneralManagerMeta, "all_classes", list(managers)),
            patch.object(
                GeneralManagerMeta,
                "pending_graphql_interfaces",
                list(managers),
            ),
            patch.object(
                GeneralManagerMeta,
                "pending_attribute_initialization",
                list(managers),
            ),
        ):
            with self.assertRaises(RuntimeError) as raised:
                FakeTestCase.setUpClass()

            self.assertIs(raised.exception, setup_error)
            self.assertEqual(
                [call.args[0] for call in editor.create_model.call_args_list],
                [first_model, second_model, failing_model],
            )
            self.assertEqual(
                [call.args[0] for call in editor.delete_model.call_args_list],
                [second_model, first_model],
            )
            self.assertEqual(FakeTestCase._gm_created_models, [])
            self.assertEqual(FakeTestCase._gm_created_tables, set())
            self.assertEqual(GeneralManagerMeta.all_classes, [])
            self.assertEqual(GeneralManagerMeta.pending_graphql_interfaces, [])
            self.assertEqual(GeneralManagerMeta.pending_attribute_initialization, [])
            self.assertIs(
                global_apps.get_containing_app_config,
                testing_module._original_get_app,
            )
            self.assertGreaterEqual(reset_registry.call_count, 2)
            self.assertGreaterEqual(graphql_clear.call_count, 2)
            self.assertGreaterEqual(remote_clear.call_count, 2)
            clear_cache.assert_called_once_with()

    def test_teardown_restores_graphene_cursor_on_disallowed_secondary(self) -> None:
        """Graphene instrumentation must not replace Django's database guard."""
        from graphene_django.debug.sql.tracking import wrap_cursor
        from django.db import connections

        from general_manager.utils import testing as testing_module

        class FakeTestCase(testing_module.GeneralManagerTransactionTestCase):
            general_manager_classes: ClassVar[list[type[GeneralManager]]] = []

        disallowed_cursor = Mock()
        disallowed_cursor.wrapped = Mock()
        secondary = SimpleNamespace(
            alias="secondary",
            cursor=disallowed_cursor,
        )
        wrap_cursor(secondary, Mock())

        def assert_guard_is_restored() -> None:
            self.assertIs(secondary.cursor, disallowed_cursor)

        with (
            patch.object(connections, "all", return_value=[secondary]),
            patch.object(testing_module, "_default_graphql_url_clear"),
            patch.object(testing_module, "_default_remote_api_url_clear"),
            patch.object(FakeTestCase, "_drop_created_test_models"),
            patch.object(FakeTestCase, "_unregister_created_test_models"),
            patch.object(global_apps, "clear_cache"),
            patch.object(
                testing_module.GraphQLTransactionTestCase,
                "tearDownClass",
                side_effect=assert_guard_is_restored,
            ),
        ):
            FakeTestCase.tearDownClass()

        self.assertIs(secondary.cursor, disallowed_cursor)
