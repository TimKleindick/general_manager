# type: ignore
from types import SimpleNamespace
from typing import ClassVar
from django.test import SimpleTestCase, TestCase
from django.core.checks import Warning
from django.db import connection
from unittest import mock

from general_manager.interface import ReadOnlyInterface
from general_manager.interface.capabilities.orm import OrmLifecycleCapability
from general_manager.interface.capabilities.read_only import (
    ReadOnlyLifecycleCapability,
    ReadOnlyManagementCapability,
)
from general_manager.interface.capabilities.read_only import (
    management as read_only_management,
)
from general_manager.interface.capabilities import read_only as read_only_package
from general_manager.interface.utils.models import GeneralManagerBasisModel
from general_manager.interface.utils.errors import (
    MissingReadOnlyBindingError,
    ReadOnlyRelationLookupError,
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

        capability = ReadOnlyManagementCapability()
        result = capability.get_unique_fields(M)
        # id is ignored; email (unique); username (via unique_together);
        # other (unique_together); other_field (constraint); extra (UniqueConstraint)
        self.assertSetEqual(result, {"email", "username", "other", "extra"})


class ReadOnlyLoggerResolutionTests(SimpleTestCase):
    def test_resolve_logger_prefers_package_logger(self) -> None:
        """
        Ensure _resolve_logger returns the package-level logger when it is overridden.
        """
        original_logger = read_only_package.logger
        sentinel_logger = mock.Mock()
        read_only_package.logger = sentinel_logger
        try:
            resolved = read_only_management._resolve_logger()
            self.assertIs(resolved, sentinel_logger)
        finally:
            read_only_package.logger = original_logger


class ReadOnlyDependencyResolutionTests(SimpleTestCase):
    def test_related_readonly_interfaces_filters_candidates(self) -> None:
        """
        Ensure related read-only interfaces are discovered only for concrete relations.
        """

        class RelatedInterface:
            _interface_type = "readonly"

        class NonReadOnlyInterface:
            _interface_type = "other"

        class MainInterface:
            _interface_type = "readonly"

        class RelatedManager:
            Interface = RelatedInterface

        class NonReadOnlyManager:
            Interface = NonReadOnlyInterface

        class MainManager:
            Interface = MainInterface

        class RelatedModel:
            _general_manager_class = RelatedManager

        class NonReadOnlyModel:
            _general_manager_class = NonReadOnlyManager

        class MainModel:
            _general_manager_class = MainManager

        class FakeRelationField:
            def __init__(self, model: type, *, auto_created: bool = False) -> None:
                """
                Initialize a minimal relation descriptor that marks a field as a relation to a target model.
                
                Parameters:
                    model (type): The related model class this relation points to.
                    auto_created (bool): Whether the relation was automatically created (default False).
                """
                self.is_relation = True
                self.auto_created = auto_created
                self.remote_field = SimpleNamespace(model=model)

        class InterfaceModel:
            class _meta:
                @staticmethod
                def get_fields():
                    """
                    Return a list of relation field descriptors used by the interface model.
                    
                    Returns:
                        list: A list of FakeRelationField instances in this order:
                            - a relation to `RelatedModel`
                            - a relation to `NonReadOnlyModel`
                            - a relation to `MainModel`
                            - an auto-created relation to `RelatedModel` (`auto_created=True`)
                    """
                    return [
                        FakeRelationField(RelatedModel),
                        FakeRelationField(NonReadOnlyModel),
                        FakeRelationField(MainModel),
                        FakeRelationField(RelatedModel, auto_created=True),
                    ]

        MainInterface._model = InterfaceModel

        capability = ReadOnlyManagementCapability()
        related = capability._related_readonly_interfaces(MainInterface)
        self.assertEqual(related, {RelatedInterface})

        resolver = capability.get_startup_hook_dependency_resolver(MainInterface)
        self.assertEqual(resolver(MainInterface), {RelatedInterface})


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
        capability = ReadOnlyManagementCapability()
        warnings = capability.ensure_schema_is_up_to_date(
            DummyInterface,
            DummyManager,
            DummyModel,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIsInstance(warnings[0], Warning)
        self.assertIn("does not exist", warnings[0].hint)

    def test_missing_model_meta_warns(self):
        """
        Tests that a warning is returned when the model lacks Django metadata.
        """

        class ModelWithoutMeta:
            pass

        capability = ReadOnlyManagementCapability()
        warnings = capability.ensure_schema_is_up_to_date(
            DummyInterface,
            DummyManager,
            ModelWithoutMeta,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("cannot validate schema", warnings[0].hint)

    def test_missing_db_table_warns(self):
        """
        Tests that a warning is returned when the model metadata has no db_table.
        """

        class ModelMissingTable:
            class _meta:
                db_table = None

        capability = ReadOnlyManagementCapability()
        warnings = capability.ensure_schema_is_up_to_date(
            DummyInterface,
            DummyManager,
            ModelMissingTable,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("db_table", warnings[0].hint)

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

        capability = ReadOnlyManagementCapability()
        warnings = capability.ensure_schema_is_up_to_date(
            DummyInterface, DummyManager, M
        )
        self.assertEqual(warnings, [])

    def test_schema_ignores_non_concrete_fields(self):
        """
        Ensure virtual/non-concrete fields (e.g., MeasurementField descriptors) do not trigger missing-column warnings.
        """

        def table_names(_: object) -> list[str]:
            """
            Return the database table name(s) used by the dummy model.
            
            Returns:
                list[str]: A list containing DummyModel._meta.db_table.
            """
            return [DummyModel._meta.db_table]

        connection.introspection.table_names = table_names  # type: ignore[assignment]
        fake_desc = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="volume_value"),
            SimpleNamespace(name="volume_unit"),
        ]

        def get_table_description(_: object, __: object) -> list[SimpleNamespace]:
            """
            Provide a fake table description used in tests.
            
            Returns:
                list[SimpleNamespace]: List of SimpleNamespace objects representing column metadata for a table (e.g., column name and attributes).
            """
            return fake_desc

        connection.introspection.get_table_description = (  # type: ignore[assignment]
            get_table_description
        )

        non_concrete_field = SimpleNamespace(
            name="volume",
            column=None,
            concrete=False,
        )
        concrete_fields = [
            SimpleNamespace(name="id", column="id", concrete=True),
            SimpleNamespace(name="volume_value", column="volume_value", concrete=True),
            SimpleNamespace(name="volume_unit", column="volume_unit", concrete=True),
        ]

        class M:
            class _meta:
                db_table = DummyModel._meta.db_table
                local_fields: ClassVar[list[SimpleNamespace]] = [
                    non_concrete_field,
                    *concrete_fields,
                ]
                local_concrete_fields: ClassVar[list[SimpleNamespace]] = concrete_fields

        capability = ReadOnlyManagementCapability()
        warnings = capability.ensure_schema_is_up_to_date(
            DummyInterface, DummyManager, M
        )
        self.assertEqual(warnings, [])


# ------------------------------------------------------------
# Tests for sync_data
# ------------------------------------------------------------
class SyncDataTests(SimpleTestCase):
    def setUp(self):
        # Reset manager instances
        """
        Prepare the test environment for SyncDataTests by resetting model manager state, stubbing database transaction and capability methods, and capturing logs.

        Resets DummyModel.objects and DummyManager._data, replaces DummyModel._meta.local_fields with test fields, patches the transaction.atomic context manager to a no-op, stubs ReadOnlyManagementCapability.get_unique_fields to return {'name'} and ensure_schema_is_up_to_date to return an empty list, and starts a logger patch to capture log calls for assertions.
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
            A no-op context manager exit callable that accepts any arguments and does nothing.

            Intended for use as a dummy `__exit__` implementation; it ignores all positional and keyword arguments and performs no action.
            """
            return None

        self.atomic_patch = mock.patch(
            "general_manager.interface.capabilities.read_only.django_transaction.atomic",
            return_value=mock.MagicMock(__enter__=_atomic_enter, __exit__=_atomic_exit),
        )
        self.atomic_patch.start()
        # Stub get_unique_fields to return {'name'}
        self.gu_patch = mock.patch.object(
            ReadOnlyManagementCapability, "get_unique_fields", return_value={"name"}
        )
        self.gu_patch.start()
        # Stub ensure_schema_is_up_to_date to always return an empty list
        self.es_patch = mock.patch.object(
            ReadOnlyManagementCapability,
            "ensure_schema_is_up_to_date",
            return_value=[],
        )
        self.es_patch.start()
        # Capture log output
        self.log_patcher = mock.patch(
            "general_manager.interface.capabilities.read_only.logger"
        )
        self.logger = self.log_patcher.start()
        self.capability = ReadOnlyManagementCapability()

    def tearDown(self):
        """
        Restore DummyModel._meta.local_fields to its original state and stop all active test patches.

        If the original local_fields was None, the attribute is removed; otherwise the saved value is restored. Stops the patched atomic context manager, get_unique_fields, ensure_schema_is_up_to_date, and logger patchers used in the test.
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
            self.capability.sync_data(DummyInterface)
        self.assertIn("must define a '_data'", str(cm.exception))

    def test_invalid_data_type_raises(self):
        """
        Verifies sync_data raises a TypeError when the manager's _data is neither a JSON string nor a list.
        
        Asserts the raised exception message contains "_data must be a JSON string or a list".
        """
        DummyManager._data = 123  # weder str noch list
        with self.assertRaises(TypeError) as cm:
            self.capability.sync_data(DummyInterface)
        self.assertIn("_data must be a JSON string or a list", str(cm.exception))

    def test_invalid_json_format_raises(self):
        """
        Test that sync_data raises when JSON data does not decode to a list.
        """
        DummyManager._data = '{"name": "alpha"}'
        with self.assertRaises(TypeError) as cm:
            self.capability.sync_data(DummyInterface)
        self.assertIn("JSON must decode to a list", str(cm.exception))

    def test_no_unique_fields_raises(self):
        # Stop the existing get_unique_fields stub and return an empty set
        """
        Test that sync_data raises a ValueError when no unique fields are defined on the model.
        """
        self.gu_patch.stop()
        with mock.patch.object(
            ReadOnlyManagementCapability, "get_unique_fields", return_value=set()
        ):
            DummyManager._data = []
            with self.assertRaises(ValueError) as cm:
                self.capability.sync_data(DummyInterface)
            self.assertIn("must declare at least one unique field", str(cm.exception))

    def test_ensure_schema_not_up_to_date_logs_and_exits(self):
        # Replace ensure_schema_is_up_to_date with a warning response
        """
        Test that sync_data logs a warning and exits without saving if schema validation returns warnings.
        """
        self.es_patch.stop()
        with mock.patch.object(
            ReadOnlyManagementCapability,
            "ensure_schema_is_up_to_date",
            return_value=[Warning("x", "y", obj=None)],
        ):
            DummyManager._data = "[]"
            self.capability.sync_data(DummyInterface)
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
        self.capability.sync_data(DummyInterface)
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


class SyncDataMetadataValidationTests(SimpleTestCase):
    def test_missing_binding_raises(self):
        class IncompleteInterface(ReadOnlyInterface):
            pass

        capability = ReadOnlyManagementCapability()
        with self.assertRaises(MissingReadOnlyBindingError):
            capability.sync_data(IncompleteInterface)


class SyncDataRelationResolutionTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Replace transaction.atomic with a no-op context manager for relation resolution tests.
        """

        class _DummyAtomic:
            def __enter__(self) -> None:
                """
                Enter the context for this manager without yielding a context value.
                
                Returns:
                    None
                """
                return None

            def __exit__(self, *_: object) -> None:
                """
                No-op context manager exit that ignores all exception information and does not suppress exceptions.
                
                This method accepts the standard context manager exit arguments but ignores them; any exception raised inside the context will propagate.
                """
                return None

        self.atomic_patch = mock.patch(
            "general_manager.interface.capabilities.read_only.django_transaction.atomic",
            return_value=_DummyAtomic(),
        )
        self.atomic_patch.start()

    def tearDown(self) -> None:
        """
        Stop the atomic transaction patch applied during test setup.
        
        This restores the original django_transaction.atomic by stopping the patch started in setUp.
        """
        self.atomic_patch.stop()

    def test_relation_lookup_failure_logs_and_raises(self) -> None:
        """
        Ensure relation lookup errors are logged and propagated when no match exists.
        """

        class RelatedQuerySet:
            def __init__(self, items: list[object]) -> None:
                """
                Initialize the instance's internal storage with the provided list of items.
                
                Parameters:
                	items (list[object]): List used as the instance's internal item storage.
                """
                self._items = items

            def __getitem__(self, item: object):
                """
                Retrieve an element or subsequence from the container.
                
                Parameters:
                	item (int | slice): An index to select a single element or a slice to select a subsequence.
                
                Returns:
                	The element at `item` when an index is provided, or the subsequence corresponding to the slice.
                """
                if isinstance(item, slice):
                    return self._items[item]
                return self._items[item]

            def count(self) -> int:
                """
                Return the number of items in the collection.
                
                Returns:
                    int: The count of items contained.
                """
                return len(self._items)

        class RelatedManager:
            def filter(self, **_: object) -> RelatedQuerySet:
                """
                Return an empty RelatedQuerySet regardless of provided lookup arguments.
                
                Parameters:
                    **_ (object): Arbitrary lookup keyword arguments which are ignored.
                
                Returns:
                    RelatedQuerySet: An empty RelatedQuerySet.
                """
                return RelatedQuerySet([])

        class RelatedModel:
            objects = RelatedManager()

        class FakeForeignKey(models.ForeignKey):
            def __init__(self, name: str, remote_model: type) -> None:
                """
                Create a lightweight relation-like field for tests.
                
                Parameters:
                    name (str): The attribute name of the field.
                    remote_model (type): The model class this field relates to; assigned to `remote_field.model`.
                
                Notes:
                    The created object will present as a relation (`is_relation = True`) and not auto-created (`auto_created = False`).
                """
                self.name = name
                self.remote_field = SimpleNamespace(model=remote_model)
                self.is_relation = True
                self.auto_created = False

        class RelationModel:
            objects = FakeManager()

            class _meta:
                local_fields: ClassVar[list[FakeField]] = [
                    FakeField("id", editable=False, primary_key=True),
                    FakeField("name"),
                ]

                @staticmethod
                def get_fields():
                    """
                    Provide the list of model fields for tests, including a foreign key named "related" to RelatedModel.
                    
                    Returns:
                        list: A list containing a FakeForeignKey instance named "related" that references `RelatedModel`.
                    """
                    return [FakeForeignKey("related", RelatedModel)]

        class RelationManager:
            _data: ClassVar[list[dict[str, object]]] = [
                {"name": "alpha", "related": {"code": "missing"}}
            ]

        class RelationInterface(ReadOnlyInterface):
            _model = RelationModel
            _parent_class = RelationManager

        capability = ReadOnlyManagementCapability()
        logger = mock.Mock()

        with self.assertRaises(ReadOnlyRelationLookupError):
            capability.sync_data(
                RelationInterface,
                unique_fields={"name"},
                schema_validated=True,
                logger_instance=logger,
            )

        logger.warning.assert_called_once()
        context = logger.warning.call_args[1]["context"]
        self.assertEqual(context["field"], "related")
        self.assertEqual(context["matches"], 0)
        self.assertEqual(context["index"], 0)


class SyncDataRelatedInterfaceTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Replace transaction.atomic with a no-op context manager for related sync tests.
        """

        class _DummyAtomic:
            def __enter__(self) -> None:
                """
                Enter the context for this manager without yielding a context value.
                
                Returns:
                    None
                """
                return None

            def __exit__(self, *_: object) -> None:
                """
                No-op context manager exit that ignores all exception information and does not suppress exceptions.
                
                This method accepts the standard context manager exit arguments but ignores them; any exception raised inside the context will propagate.
                """
                return None

        self.atomic_patch = mock.patch(
            "general_manager.interface.capabilities.read_only.django_transaction.atomic",
            return_value=_DummyAtomic(),
        )
        self.atomic_patch.start()

    def tearDown(self) -> None:
        """
        Stop the atomic transaction patch applied during test setup.
        
        This restores the original django_transaction.atomic by stopping the patch started in setUp.
        """
        self.atomic_patch.stop()

    def test_related_interface_sync_runs_first(self) -> None:
        """
        Verify that related read-only interfaces are synchronized before local data.
        """

        class RelatedInterface:
            _interface_type = "readonly"

            @classmethod
            def require_capability(cls, *_: object, **__: object) -> object:
                """
                Provide the capability associated with this class.
                
                Returns:
                    related_capability (object): The capability object required by the class.
                """
                return related_capability

        class RelatedManager:
            Interface = RelatedInterface

        class RelatedModel:
            _general_manager_class = RelatedManager

        class FakeForeignKey(models.ForeignKey):
            def __init__(self, name: str, remote_model: type) -> None:
                """
                Create a lightweight relation-like field for tests.
                
                Parameters:
                    name (str): The attribute name of the field.
                    remote_model (type): The model class this field relates to; assigned to `remote_field.model`.
                
                Notes:
                    The created object will present as a relation (`is_relation = True`) and not auto-created (`auto_created = False`).
                """
                self.name = name
                self.remote_field = SimpleNamespace(model=remote_model)
                self.is_relation = True
                self.auto_created = False

        class MainModel:
            objects = FakeManager()

            class _meta:
                local_fields: ClassVar[list[FakeField]] = [
                    FakeField("id", editable=False, primary_key=True),
                    FakeField("name"),
                ]

                @staticmethod
                def get_fields():
                    """
                    Provide the list of model fields for tests, including a foreign key named "related" to RelatedModel.
                    
                    Returns:
                        list: A list containing a FakeForeignKey instance named "related" that references `RelatedModel`.
                    """
                    return [FakeForeignKey("related", RelatedModel)]

        class MainManager:
            _data: ClassVar[list[dict[str, object]]] = []

        class MainInterface(ReadOnlyInterface):
            _model = MainModel
            _parent_class = MainManager

        related_capability = ReadOnlyManagementCapability()
        related_capability.sync_data = mock.Mock()

        capability = ReadOnlyManagementCapability()
        capability.sync_data(
            MainInterface,
            unique_fields={"name"},
            schema_validated=True,
        )

        related_capability.sync_data.assert_called_once()


class SystemCheckHookTests(SimpleTestCase):
    def test_get_system_checks_invokes_capability(self):
        capability = ReadOnlyManagementCapability()
        DummyInterface._parent_class = DummyManager
        hooks = capability.get_system_checks(DummyInterface)
        with mock.patch.object(
            ReadOnlyManagementCapability,
            "ensure_schema_is_up_to_date",
            return_value=[Warning("warn", obj=None)],
        ) as mock_check:
            results = [hook() for hook in hooks]
        mock_check.assert_called_once_with(
            DummyInterface,
            DummyManager,
            DummyInterface._model,
        )
        self.assertEqual(results, [[Warning("warn", obj=None)]])


class ReadOnlyStartupHookTests(SimpleTestCase):
    def test_hook_not_registered_without_metadata(self):
        """
        Ensure get_startup_hooks returns no hooks when the interface lacks required metadata.
        """

        class MissingMetadataInterface(ReadOnlyInterface):
            pass

        capability = ReadOnlyManagementCapability()
        hooks = capability.get_startup_hooks(MissingMetadataInterface)
        self.assertEqual(hooks, tuple())

    def test_hook_available_when_metadata_present(self):
        """
        Ensure get_startup_hooks returns a callable once the interface exposes manager and model metadata.
        """

        class ReadyInterface(ReadOnlyInterface):
            pass

        ReadyInterface._parent_class = DummyManager
        ReadyInterface._model = DummyModel

        capability = ReadOnlyManagementCapability()
        hooks = capability.get_startup_hooks(ReadyInterface)
        self.assertEqual(len(hooks), 1)
        DummyManager._data = []
        with mock.patch.object(ReadOnlyManagementCapability, "sync_data") as mock_sync:
            hooks[0]()
        mock_sync.assert_called_once_with(ReadyInterface)


# ------------------------------------------------------------
# Tests for decorators and handle_interface
# ------------------------------------------------------------
class ReadOnlyLifecycleCapabilityTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Prepare the test fixture by creating a ReadOnlyLifecycleCapability and isolating read-only class registry.

        Saves the current GeneralManagerMeta.read_only_classes to self._original and replaces it with an empty list, and stores a new ReadOnlyLifecycleCapability instance on self.capability for use by tests.
        """
        from general_manager.manager.meta import GeneralManagerMeta

        self.capability = ReadOnlyLifecycleCapability()
        self._original = list(GeneralManagerMeta.read_only_classes)
        GeneralManagerMeta.read_only_classes = []

    def tearDown(self) -> None:
        """
        Restore GeneralManagerMeta.read_only_classes to its original value saved during setUp.

        This resets the global registry of read-only manager classes modified by the test to avoid side effects on other tests.
        """
        from general_manager.manager.meta import GeneralManagerMeta

        GeneralManagerMeta.read_only_classes = self._original

    def test_pre_create_enforces_soft_delete_and_base_model(self):
        class DummyInterface(ReadOnlyInterface):
            pass

        if hasattr(DummyInterface, "Meta"):
            delattr(DummyInterface, "Meta")

        with mock.patch.object(
            OrmLifecycleCapability,
            "pre_create",
            return_value=({}, DummyInterface, GeneralManagerBasisModel),
        ) as mock_parent:
            self.capability.pre_create(
                name="Test",
                attrs={},
                interface=DummyInterface,
                base_model_class=GeneralManagerBasisModel,
            )

        self.assertTrue(hasattr(DummyInterface, "Meta"))
        self.assertTrue(DummyInterface.Meta.use_soft_delete)
        self.assertIs(
            mock_parent.call_args.kwargs["base_model_class"], GeneralManagerBasisModel
        )

    def test_post_create_registers_read_only_class(self):
        from general_manager.manager.meta import GeneralManagerMeta

        class DummyManager:
            pass

        with mock.patch.object(
            OrmLifecycleCapability,
            "post_create",
            return_value=None,
        ) as mock_parent:
            self.capability.post_create(
                new_class=DummyManager,
                interface_class=ReadOnlyInterface,
                model=None,
            )

        mock_parent.assert_called_once()
        self.assertIn(DummyManager, GeneralManagerMeta.read_only_classes)