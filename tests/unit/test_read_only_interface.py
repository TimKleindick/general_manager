# type: ignore
from types import SimpleNamespace
from typing import ClassVar
from django.test import SimpleTestCase, TestCase
from django.core.checks import Warning
from django.db import connection
from unittest import mock

from general_manager.interface.read_only_interface import (
    ReadOnlyInterface,
    GeneralManagerBasisModel,
)

from django.db import models


# ------------------------------------------------------------
# Helper classes for the tests
# ------------------------------------------------------------
class FakeInstance:
    def __init__(self, **kwargs):
        # Initialize all provided attributes
        """
        Initialize a fake instance with dynamic attributes.

        All keyword arguments are set as attributes on the instance. The instance is marked as active and not yet saved.
        """
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.is_active = True
        self.saved = False

    def save(self):
        """
        Mark the instance as saved by setting the `saved` attribute to True.
        """
        self.saved = True


class FakeQuerySet:
    """
    Lightweight queryset-like object that supports the subset of Django's queryset
    API needed by the tests (iteration and ``first``).
    """

    def __init__(self, items: list[FakeInstance]) -> None:
        self._items = items

    def first(self):
        """
        Return the first item in the queryset or None if it is empty.
        """
        return self._items[0] if self._items else None

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)


class FakeManager:
    def __init__(self):
        """
        Initialize the FakeManager with an empty list of instances.
        """
        self._instances: list[FakeInstance] = []

    def get_or_create(self, **lookup):
        # Look up an existing object
        """
        Return an existing instance matching the given lookup parameters, or create and return a new one.

        Parameters:
                lookup: Arbitrary keyword arguments used to match instance attributes.

        Returns:
                A tuple of (instance, created), where `instance` is the found or newly created FakeInstance, and `created` is True if a new instance was created, False otherwise.
        """
        for inst in self._instances:
            if all(getattr(inst, k) == v for k, v in lookup.items()):
                return inst, False
        # Create a new instance when none matched
        inst = FakeInstance(**lookup)
        self._instances.append(inst)
        return inst, True

    def create(self, **kwargs):
        """
        Mimic Django's Manager.create by creating, saving, and tracking a new instance.
        """
        inst = FakeInstance(**kwargs)
        inst.is_active = False
        inst.save()
        self._instances.append(inst)
        return inst

    def filter(self, **kwargs):
        # Return queryset-like wrapper for matching instances
        """
        Return a queryset-like wrapper limited to instances matching the provided lookup kwargs.
        """

        def matches(inst: FakeInstance) -> bool:
            return all(getattr(inst, key) == value for key, value in kwargs.items())

        filtered = [inst for inst in self._instances if matches(inst)]
        return FakeQuerySet(filtered)


class FakeField:
    """
    Minimal stand-in for a Django model field that exposes the attributes required in tests.
    """

    def __init__(
        self,
        name: str,
        *,
        editable: bool = True,
        primary_key: bool = False,
        column: str | None = None,
    ) -> None:
        self.name = name
        self.editable = editable
        self.primary_key = primary_key
        self.column = column or name


class DummyModel:
    # Simulated Django model
    objects = FakeManager()

    class _meta:
        db_table = "dummy_table"
        # Irrelevant for get_unique_fields; patched directly in tests


class DummyManager:
    # Simulates the GeneralManager
    _data = None


class DummyInterface(ReadOnlyInterface):
    _model = DummyModel
    _parent_class = DummyManager


# ------------------------------------------------------------
# Tests for get_unique_fields
# ------------------------------------------------------------
class GetUniqueFieldsTests(SimpleTestCase):
    def test_field_unique_true_and_together_and_constraint(self):
        # Build a fake _meta with local fields, unique_together, and UniqueConstraint
        """
        Tests that get_unique_fields correctly identifies unique fields from unique attributes, unique_together, and UniqueConstraint in a model's _meta.
        """
        Field = SimpleNamespace  # exposes .name, .unique, and .column

        def always_false_instancecheck(_: type, __: object) -> bool:
            """
            Always returns False for any type-instance check inputs.

            Parameters:
                _ (type): Ignored type argument.
                __ (object): Ignored instance/object argument.

            Returns:
                bool: `False` always.
            """
            return False

        fake_meta = SimpleNamespace(
            local_fields=[
                Field(name="id", unique=True, column="id"),
                Field(name="email", unique=True, column="email"),
                Field(name="username", unique=False, column="username"),
            ],
            unique_together=[("username", "other")],
            constraints=[
                mock.Mock(
                    __class__=type(
                        "C", (), {"__instancecheck__": always_false_instancecheck}
                    ),
                    fields=["other_field"],
                ),
                # echtes UniqueConstraint
                mock.Mock(
                    __class__=models.UniqueConstraint,
                    fields=["extra"],
                ),
            ],
        )

        # Patch the model metadata
        class M:
            _meta = fake_meta

        result = ReadOnlyInterface.get_unique_fields(M)
        # id is ignored; email (unique); username (via unique_together);
        # other (unique_together); other_field (constraint); extra (UniqueConstraint)
        self.assertSetEqual(result, {"email", "username", "other", "extra"})


# ------------------------------------------------------------
# Tests for ensure_schema_is_up_to_date
# ------------------------------------------------------------
class EnsureSchemaTests(TestCase):
    def setUp(self):
        # stub introspection
        """
        Saves the original database introspection methods for later restoration during tests.
        """
        self.orig_table_names = connection.introspection.table_names
        self.orig_get_desc = connection.introspection.get_table_description

    def tearDown(self):
        """
        Restores the original database introspection methods after each test.
        """
        connection.introspection.table_names = self.orig_table_names
        connection.introspection.get_table_description = self.orig_get_desc

    def test_table_not_exists(self):
        # table_names liefert leer
        """
        Tests that a warning is returned when the model's database table does not exist.
        """

        def table_names(_: object) -> list[str]:
            """
            Provide an empty list of database table names for tests.

            Parameters:
                _ (object): Ignored database connection or introspection object.

            Returns:
                list[str]: An empty list representing no table names.
            """
            return []

        connection.introspection.table_names = table_names  # type: ignore[assignment]
        warnings = ReadOnlyInterface.ensure_schema_is_up_to_date(
            DummyManager, DummyModel
        )
        self.assertEqual(len(warnings), 1)
        self.assertIsInstance(warnings[0], Warning)
        self.assertIn("does not exist", warnings[0].hint)

    def test_schema_up_to_date(self):
        # table_names returns the target table name
        """
        Tests that ensure_schema_is_up_to_date returns no warnings when the database schema matches the model's fields.
        """

        def table_names(_: object) -> list[str]:
            """
            Return a list containing the DummyModel's database table name.

            Parameters:
                _ (object): Ignored connection/introspection object.

            Returns:
                list[str]: A single-item list with DummyModel._meta.db_table.
            """
            return [DummyModel._meta.db_table]

        connection.introspection.table_names = table_names  # type: ignore[assignment]
        # description returns exactly the columns defined by model._meta.local_fields
        fake_desc = [SimpleNamespace(name="col1"), SimpleNamespace(name="col2")]

        def get_table_description(_: object, __: object) -> list[SimpleNamespace]:
            """
            Return a fake table description for database introspection used in tests.

            Both parameters are ignored; they exist to match the signature of Django's
            introspection.get_table_description.

            Returns:
                list[SimpleNamespace]: A list of SimpleNamespace objects representing
                column descriptions.
            """
            return fake_desc

        connection.introspection.get_table_description = (  # type: ignore[assignment]
            get_table_description
        )

        # Fake model with matching local_fields
        class M:
            class _meta:
                db_table = DummyModel._meta.db_table
                local_fields: ClassVar[list[SimpleNamespace]] = [
                    SimpleNamespace(column="col1"),
                    SimpleNamespace(column="col2"),
                ]

        warnings = ReadOnlyInterface.ensure_schema_is_up_to_date(DummyManager, M)
        self.assertEqual(warnings, [])


# ------------------------------------------------------------
# Tests for sync_data
# ------------------------------------------------------------
class SyncDataTests(SimpleTestCase):
    def setUp(self):
        # Reset manager instances
        """
        Prepare the test environment for SyncDataTests by resetting model state, stubbing DB transaction and interface methods, and capturing logs.

        Resets DummyModel.objects and DummyManager._data, patches transaction.atomic to a no-op context manager, stubs ReadOnlyInterface.get_unique_fields to return {'name'} and ReadOnlyInterface.ensure_schema_is_up_to_date to return an empty list, and starts a logger patch that captures log calls.
        """
        DummyModel.objects = FakeManager()
        DummyManager._data = None
        self._orig_local_fields = getattr(DummyModel._meta, "local_fields", None)
        DummyModel._meta.local_fields = (
            FakeField("id", editable=False, primary_key=True),
            FakeField("name"),
            FakeField("other"),
            FakeField("is_active", editable=False),
        )
        # stub transaction.atomic
        self.atomic_cm = mock.MagicMock()

        def _atomic_enter(_: object) -> None:
            """
            No-op context manager __enter__ function used in tests.

            Parameters:
                _ (object): Ignored context manager instance or resource placeholder.
            """
            return None

        def _atomic_exit(*_: object) -> None:
            """
            No-op context manager exit callable that accepts any arguments and does nothing.

            Ignores all passed-in values and returns None, suitable as a dummy `__exit__` for stubbing context managers.
            """
            return None

        self.atomic_patch = mock.patch(
            "general_manager.interface.read_only_interface.transaction.atomic",
            return_value=mock.MagicMock(__enter__=_atomic_enter, __exit__=_atomic_exit),
        )
        self.atomic_patch.start()
        # Stub get_unique_fields to return {'name'}
        self.gu_patch = mock.patch.object(
            ReadOnlyInterface, "get_unique_fields", return_value={"name"}
        )
        self.gu_patch.start()
        # Stub ensure_schema_is_up_to_date to always return an empty list
        self.es_patch = mock.patch.object(
            ReadOnlyInterface, "ensure_schema_is_up_to_date", return_value=[]
        )
        self.es_patch.start()
        # Capture log output
        self.log_patcher = mock.patch(
            "general_manager.interface.read_only_interface.logger"
        )
        self.logger = self.log_patcher.start()

    def tearDown(self):
        """
        Stops all active patches and restores original behaviors after each test.
        """
        if self._orig_local_fields is None:
            delattr(DummyModel._meta, "local_fields")
        else:
            DummyModel._meta.local_fields = self._orig_local_fields
        self.atomic_patch.stop()
        self.gu_patch.stop()
        self.es_patch.stop()
        self.log_patcher.stop()

    def test_missing_data_raises(self):
        """
        Tests that sync_data raises a ValueError when the required '_data' attribute is not set.
        """
        DummyManager._data = None
        with self.assertRaises(ValueError) as cm:
            DummyInterface.sync_data()
        self.assertIn("must define a '_data'", str(cm.exception))

    def test_invalid_data_type_raises(self):
        """
        Test that sync_data raises a ValueError when _data is neither a string nor a list.
        """
        DummyManager._data = 123  # weder str noch list
        with self.assertRaises(TypeError) as cm:
            DummyInterface.sync_data()
        self.assertIn("_data must be a JSON string or a list", str(cm.exception))

    def test_no_unique_fields_raises(self):
        # Stop the existing get_unique_fields stub and return an empty set
        """
        Test that sync_data raises a ValueError when no unique fields are defined on the model.
        """
        self.gu_patch.stop()
        with mock.patch.object(
            ReadOnlyInterface, "get_unique_fields", return_value=set()
        ):
            DummyManager._data = []
            with self.assertRaises(ValueError) as cm:
                DummyInterface.sync_data()
            self.assertIn("must declare at least one unique field", str(cm.exception))

    def test_ensure_schema_not_up_to_date_logs_and_exits(self):
        # Replace ensure_schema_is_up_to_date with a warning response
        """
        Test that sync_data logs a warning and exits without saving if schema validation returns warnings.
        """
        self.es_patch.stop()
        with mock.patch.object(
            ReadOnlyInterface,
            "ensure_schema_is_up_to_date",
            return_value=[Warning("x", "y", obj=None)],
        ):
            DummyManager._data = "[]"
            DummyInterface.sync_data()
            self.logger.warning.assert_called_once()
            # Verify no additional save() calls occurred
            self.assertEqual(DummyModel.objects._instances, [])

    def test_sync_creates_updates_and_deactivates(self):
        """
        Tests that sync_data creates new instances, updates existing ones, and does not deactivate any when all input data matches active instances.

        Verifies that:
        - Existing instances are updated with new data.
        - New instances are created for unmatched input.
        - No instances are deactivated if all remain present.
        - The logger records the correct counts of created, updated, and deactivated entries.
        """
        DummyModel.objects._instances = [FakeInstance(name="a", other=1)]
        # New JSON data: `a` changes, `b` is new
        DummyManager._data = [{"name": "a", "other": 2}, {"name": "b", "other": 3}]
        # Run sync_data
        DummyInterface.sync_data()
        # Verify `a` was updated
        inst_a = next(i for i in DummyModel.objects._instances if i.name == "a")
        self.assertEqual(inst_a.other, 2)
        self.assertTrue(inst_a.saved)
        # Verify `b` was created
        inst_b = next(i for i in DummyModel.objects._instances if i.name == "b")
        self.assertEqual(inst_b.other, 3)
        self.assertTrue(inst_b.saved)
        # Verify the log message reports 1 created, 1 updated, 0 deactivated
        self.logger.info.assert_called_once()
        msg = self.logger.info.call_args[1]["context"]
        self.assertEqual(msg["created"], 1)
        self.assertEqual(msg["updated"], 1)
        self.assertEqual(msg["deactivated"], 0)


# ------------------------------------------------------------
# Tests for decorators and handle_interface
# ------------------------------------------------------------
class DecoratorTests(SimpleTestCase):
    def test_read_only_post_create_appends_class(self):
        # Reset tracked classes
        """
        Tests that the read_only_post_create decorator appends the class to the read_only_classes list and calls the decorated hook.
        """
        from general_manager.manager.meta import GeneralManagerMeta

        GeneralManagerMeta.read_only_classes = []

        # Dummy function
        @ReadOnlyInterface.read_only_post_create
        def post_hook(new_cls, interface_cls, model):
            # Mark the class when the hook executes
            """
            Marks the given class to indicate that the post hook has been called.

            Parameters:
                new_cls: The class to be marked.
                interface_cls: The interface class associated with the hook.
                model: The model associated with the hook.
            """
            new_cls._hook_called = True

        class C:
            pass

        post_hook(C, ReadOnlyInterface, DummyModel)
        self.assertTrue(hasattr(C, "_hook_called"))
        self.assertIn(C, GeneralManagerMeta.read_only_classes)

    def test_read_only_pre_create_delegates_and_sets_base_model(self):
        """
        Tests that the read_only_pre_create decorator delegates to the original function and sets the base model class to ReadOnlyModel.
        """

        def pre_hook(name, attrs, interface, base_model_class=None):
            """
            Package the pre-create hook inputs into a 4-tuple.

            Parameters:
                name (str): The proposed name for the model/class.
                attrs (dict): Attribute dictionary for the model definition.
                interface (type): The interface class associated with the model.
                base_model_class (type or None): Optional base model class to be set as the model's parent.

            Returns:
                tuple: A tuple (name, attrs, interface, base_model_class) containing the provided inputs.
            """
            return (name, attrs, interface, base_model_class)

        wrapper = ReadOnlyInterface.read_only_pre_create(pre_hook)
        iface = type("iface", (), {})  # just to have an interface class
        result = wrapper("MyName", {"a": 1}, iface)
        # The last parameter must be GeneralManagerBasisModel
        self.assertEqual(result, ("MyName", {"a": 1}, iface, GeneralManagerBasisModel))

    def test_handle_interface_returns_two_callables(self):
        """
        Test that handle_interface returns two callable objects for pre- and post-processing hooks.
        """
        pre, post = ReadOnlyInterface.handle_interface()
        self.assertTrue(callable(pre))
        self.assertTrue(callable(post))
