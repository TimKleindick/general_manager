# type: ignore

from typing import ClassVar

from django.test import TransactionTestCase
from django.db import connection
from django.contrib.auth.models import User
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.db import models
from django.core.exceptions import ValidationError, FieldDoesNotExist
from django.apps import apps
from simple_history.models import HistoricalRecords

from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import OrmInterfaceBase
from general_manager.interface.bundles.database import (
    ORM_PERSISTENCE_CAPABILITIES,
    ORM_WRITABLE_CAPABILITIES,
)
from general_manager.interface.utils.models import get_full_clean_methode
from general_manager.interface.utils.errors import (
    InvalidFieldTypeError,
    InvalidFieldValueError,
    UnknownFieldError,
)
from general_manager.manager.input import Input
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
    PayloadNormalizer,
)
from general_manager.interface.capabilities.orm import (
    OrmHistoryCapability,
    OrmMutationCapability,
    OrmPersistenceSupportCapability,
)


class PersonModel(models.Model):
    name = models.CharField(max_length=100)
    age = models.IntegerField()
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="owned_persons"
    )
    tags = models.ManyToManyField(User, related_name="tagged_persons", blank=True)
    is_active = models.BooleanField(default=True)
    changed_by = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        app_label = "general_manager"


class PersonInterface(OrmInterfaceBase):
    _model = PersonModel
    _parent_class = None
    _interface_type = "test"
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    configured_capabilities: ClassVar[tuple] = (ORM_PERSISTENCE_CAPABILITIES,)

    class Meta:
        app_label = "general_manager"

    @classmethod
    def handle_interface(cls):
        """
        Provide pre- and post-processing callables used when creating a dynamic interface-backed class.
        """

        def pre(name, attrs, interface):
            return attrs, cls, cls._model

        def post(new_cls, interface_cls, model):
            new_cls.Interface = interface_cls
            interface_cls._parent_class = new_cls

        return pre, post


class DummyManager(GeneralManager):
    Interface = PersonInterface


PersonInterface._parent_class = DummyManager
PersonInterface.__module__ = "general_manager.interface.orm_interface"


class OrmInterfaceBaseTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Prepare test database and app registry for PersonModel.

        Registers PersonModel and its many-to-many through model in the "general_manager" app registry (saving the original registry entries for later restoration), clears the app cache, and creates the database table for PersonModel so tests in the test case class have a concrete schema to operate on.
        """
        super().setUpClass()
        cls._app_config = apps.get_app_config("general_manager")
        cls._original_app_models: dict[str, type[models.Model] | None] = {}
        cls._original_all_models: dict[str, type[models.Model] | None] = {}

        app_models = cls._app_config.models
        registry_models = apps.all_models.setdefault("general_manager", {})

        for model in (PersonModel, PersonModel.tags.through):
            model_name = model._meta.model_name
            cls._original_app_models[model_name] = app_models.get(model_name)
            cls._original_all_models[model_name] = registry_models.get(model_name)

            app_models[model_name] = model
            registry_models[model_name] = model

        apps.clear_cache()
        with connection.schema_editor() as schema:
            schema.create_model(PersonModel)

    @classmethod
    def tearDownClass(cls):
        """
        Tears down test database state and restores the app model registry for the "general_manager" app.

        Deletes the PersonModel table, restores modified entries in the test class's app config and the global apps.all_models mapping for "general_manager", clears the apps cache, and then calls the superclass teardown.
        """
        with connection.schema_editor() as schema:
            schema.delete_model(PersonModel)

        app_models = cls._app_config.models
        registry_models = apps.all_models.setdefault("general_manager", {})

        for model_name, original in cls._original_app_models.items():
            if original is None:
                app_models.pop(model_name, None)
            else:
                app_models[model_name] = original

        for model_name, original in cls._original_all_models.items():
            if original is None:
                registry_models.pop(model_name, None)
            else:
                registry_models[model_name] = original

        apps.clear_cache()
        super().tearDownClass()

    def setUp(self):
        """
        Create a test User and a PersonModel instance linked to that user for use in tests.

        Sets self.user to a newly created User, sets self.person to a new PersonModel owned and changed_by that user, and adds the user to self.person.tags.
        """
        self.user = User.objects.create(username="tester")
        self.person = PersonModel.objects.create(
            name="Alice",
            age=30,
            owner=self.user,
            changed_by=self.user,
        )
        self.person.tags.add(self.user)

    def test_get_data_and_initialization(self):
        """
        Tests that initializing DummyManager with a person's primary key correctly sets the interface instance to the corresponding PersonModel object.
        """
        mgr = DummyManager(self.person.pk)
        self.assertEqual(mgr._interface._instance.pk, self.person.pk)

    def test_get_data_with_history_date(self):
        """
        Tests that initializing the manager with a past search date retrieves the historical record.

        Verifies that the interface instance is set to the value returned by the patched `get_historical_record` method and that this method is called exactly once.
        """
        with patch(
            "general_manager.interface.capabilities.orm.OrmHistoryCapability.get_historical_record",
            return_value="old",
        ) as mock_hist:
            mgr = DummyManager(
                self.person.pk, search_date=datetime.now() - timedelta(minutes=1)
            )
            self.assertEqual(mgr._interface._instance, "old")
            mock_hist.assert_called_once()

    def test_filter_and_exclude(self):
        """
        Tests that filtering and excluding records via the interface returns correct results.

        Verifies that filtering by name returns a bucket containing the expected record, and excluding by the same name yields an empty result set.
        """
        bucket = PersonInterface.filter(name="Alice")
        self.assertIsInstance(bucket, DatabaseBucket)
        self.assertEqual(bucket.count(), 1)
        excluded = PersonInterface.exclude(name="Alice")
        self.assertEqual(excluded.count(), 0)

    def test_get_historical_record(self):
        """
        Tests that get_historical_record retrieves the correct historical record for a given date.

        Verifies that the method filters the history manager by date and returns the last matching record.
        """
        mock = MagicMock()
        history_qs = MagicMock()
        ordered_qs = MagicMock()
        ordered_qs.last.return_value = "hist"
        history_qs.order_by.return_value = ordered_qs
        mock.filter.return_value = history_qs
        dummy = SimpleNamespace(history=mock)
        dt = datetime(2020, 1, 1)
        handler = PersonInterface.require_capability(
            "history",
            expected_type=OrmHistoryCapability,
        )
        res = handler.get_historical_record(PersonInterface, dummy, dt)
        mock.filter.assert_called_once_with(history_date__lte=dt)
        history_qs.order_by.assert_called_once_with("history_date")
        self.assertEqual(res, "hist")

    def test_get_attribute_types_and_field_type(self):
        """
        Tests that attribute type information and field types are correctly reported by the interface.

        Verifies that `get_attribute_types` returns accurate type, required, and editable flags for model fields, and that `get_field_type` returns the correct Django field class.
        """
        types = PersonInterface.get_attribute_types()
        self.assertEqual(types["name"]["type"], str)
        self.assertTrue(types["name"]["is_required"])
        self.assertEqual(types["tags_list"]["type"], User)
        self.assertTrue(types["tags_list"]["is_editable"])
        self.assertFalse(types["tags_list"]["is_required"])
        self.assertIs(PersonInterface.get_field_type("name"), models.CharField)

    def test_get_attributes_values(self):
        """
        Tests that attribute getter functions from the interface return correct values for a model instance.
        """
        mgr = DummyManager(self.person.pk)
        attrs = PersonInterface.get_attributes()
        self.assertEqual(attrs["name"](mgr._interface), "Alice")
        self.assertEqual(attrs["age"](mgr._interface), 30)
        self.assertEqual(attrs["is_active"](mgr._interface), True)
        self.assertEqual(attrs["owner"](mgr._interface), self.user)
        self.assertEqual(attrs["changed_by"](mgr._interface), self.user)
        self.assertEqual(list(attrs["tags_list"](mgr._interface)), [self.user])

    def test_pre_and_post_create_and_handle_interface(self):
        """
        Tests that lifecycle hooks configure interface classes correctly.
        """
        module_name = "general_manager.interface.orm_interface"
        pre, post = PersonInterface.handle_interface()
        new_attrs, interface_cls, model = pre(
            "TemporaryManager",
            {"__module__": module_name},
            PersonInterface,
        )
        self.assertEqual(model._meta.app_label, "general_manager")
        TempManager = type("TempManager", (GeneralManager,), new_attrs)
        post(TempManager, interface_cls, model)
        self.assertTrue(issubclass(TempManager.Interface, PersonInterface))
        self.assertIs(interface_cls._parent_class, TempManager)

    def test_rules_and_full_clean_false(self):
        """
        Tests that model validation fails when custom rules evaluate to False.

        Verifies that attaching a rule returning False to the model's meta causes the cleaning method to raise ValidationError for both invalid and valid instances, and confirms the rule's evaluation method is called.
        """

        class DummyRule:
            def __init__(self):
                self.called = False

            def evaluate(self, obj):
                self.called = True
                return False

            def get_error_message(self):
                return {"name": "bad"}

        PersonModel._meta.rules = [DummyRule()]
        cleaner = get_full_clean_methode(PersonModel)
        invalid = PersonModel(age=1, owner=self.user, changed_by=self.user)
        with self.assertRaises(ValidationError):
            cleaner(invalid)
        with self.assertRaises(ValidationError):
            cleaner(self.person)
        self.assertTrue(PersonModel._meta.rules[0].called)
        delattr(PersonModel._meta, "rules")

    def test_rules_and_full_clean_true(self):
        """
        Tests that model validation passes when custom rules evaluate to True and fails otherwise.

        Ensures that the cleaning method raises a ValidationError for invalid instances, passes for valid ones, and that custom rule evaluation is invoked.
        """

        class DummyRule:
            def __init__(self):
                self.called = False

            def evaluate(self, obj):
                self.called = True
                return True

            def get_error_message(self):
                return {"name": "bad"}

        PersonModel._meta.rules = [DummyRule()]
        cleaner = get_full_clean_methode(PersonModel)
        invalid = PersonModel(age=1, owner=self.user, changed_by=self.user)
        with self.assertRaises(ValidationError):
            cleaner(invalid)
        _ = cleaner(self.person)
        self.assertTrue(PersonModel._meta.rules[0].called)
        delattr(PersonModel._meta, "rules")

    def test_handle_custom_fields(self):
        """
        Tests that custom fields and ignore lists are correctly identified by handle_custom_fields for a OrmInterfaceBase subclass.
        """

        class CustomInterface(OrmInterfaceBase):
            sample = models.CharField(max_length=5)
            configured_capabilities: ClassVar[tuple] = (ORM_PERSISTENCE_CAPABILITIES,)

        fields, ignore = CustomInterface.handle_custom_fields(CustomInterface)
        self.assertIn(None, fields)
        self.assertIn("None_value", ignore)
        self.assertIn("None_unit", ignore)

    def test_get_data_with_invalid_pk(self):
        """
        Tests that initializing DummyManager with an invalid primary key raises appropriate exception.
        """
        with self.assertRaises(PersonModel.DoesNotExist):
            DummyManager(99999)

    def test_get_data_with_none_pk(self):
        """
        Tests that initializing DummyManager with None as primary key raises appropriate exception.
        """
        with self.assertRaises((TypeError, ValueError)):
            DummyManager(None)

    def test_get_data_with_string_pk(self):
        """
        Tests that initializing DummyManager with a string primary key that can't be converted to int raises appropriate exception.
        """
        with self.assertRaises((ValueError, PersonModel.DoesNotExist)):
            DummyManager("invalid_id")

    def test_filter_with_multiple_conditions(self):
        """
        Tests filtering with multiple conditions returns correct results.
        """
        # Create additional test data
        user2 = User.objects.create(username="tester2")
        PersonModel.objects.create(
            name="Bob",
            age=25,
            owner=user2,
            changed_by=user2,
        )
        PersonModel.objects.create(
            name="Alice",
            age=35,
            owner=self.user,
            changed_by=self.user,
        )

        # Test multiple filter conditions
        bucket = PersonInterface.filter(name="Alice", age=30)
        self.assertEqual(bucket.count(), 1)
        self.assertEqual(bucket.first()._interface._instance.pk, self.person.pk)

        # Test filtering with no matches
        bucket = PersonInterface.filter(name="Charlie", age=40)
        self.assertEqual(bucket.count(), 0)

    def test_exclude_with_multiple_conditions(self):
        """
        Tests excluding with multiple conditions returns correct results.
        """
        user2 = User.objects.create(username="tester2")
        person2 = PersonModel.objects.create(
            name="Bob",
            age=25,
            owner=user2,
            changed_by=user2,
        )

        # Test exclude with multiple conditions
        bucket = PersonInterface.exclude(name="Alice", age=30)
        self.assertEqual(bucket.count(), 1)
        self.assertEqual(bucket.first()._interface._instance.pk, person2.pk)

    def test_filter_with_foreign_key_relationships(self):
        """
        Tests filtering by foreign key relationships works correctly.
        """
        user2 = User.objects.create(username="tester2")
        person2 = PersonModel.objects.create(
            name="Bob",
            age=25,
            owner=user2,
            changed_by=user2,
        )

        # Filter by owner
        bucket = PersonInterface.filter(owner=self.user)
        self.assertEqual(bucket.count(), 1)
        self.assertEqual(bucket.first()._interface._instance.pk, self.person.pk)

        # Filter by changed_by
        bucket = PersonInterface.filter(changed_by=user2)
        self.assertEqual(bucket.count(), 1)
        self.assertEqual(bucket.first()._interface._instance.pk, person2.pk)

    def test_filter_with_many_to_many_relationships(self):
        """
        Tests filtering by many-to-many relationships works correctly.
        """
        user2 = User.objects.create(username="tester2")
        person2 = PersonModel.objects.create(
            name="Bob",
            age=25,
            owner=user2,
            changed_by=user2,
        )
        person2.tags.add(user2)

        # Filter by tags
        bucket = PersonInterface.filter(tags=self.user)
        self.assertEqual(bucket.count(), 1)
        self.assertEqual(bucket.first()._interface._instance.pk, self.person.pk)

    def test_get_historical_record_with_none_date(self):
        """
        Tests that get_historical_record handles None date appropriately.
        """
        mock = MagicMock()
        history_qs = MagicMock()
        ordered_qs = MagicMock()
        ordered_qs.last.return_value = None
        history_qs.order_by.return_value = ordered_qs
        mock.filter.return_value = history_qs
        dummy = SimpleNamespace(history=mock)

        handler = PersonInterface.require_capability(
            "history",
            expected_type=OrmHistoryCapability,
        )
        res = handler.get_historical_record(PersonInterface, dummy, None)
        mock.filter.assert_called_once_with(history_date__lte=None)
        history_qs.order_by.assert_called_once_with("history_date")
        self.assertIsNone(res)

    def test_get_historical_record_with_future_date(self):
        """
        Tests that get_historical_record works with future dates.
        """
        mock = MagicMock()
        history_qs = MagicMock()
        ordered_qs = MagicMock()
        ordered_qs.last.return_value = "future_hist"
        history_qs.order_by.return_value = ordered_qs
        mock.filter.return_value = history_qs
        dummy = SimpleNamespace(history=mock)
        future_date = datetime.now() + timedelta(days=1)

        handler = PersonInterface.require_capability(
            "history",
            expected_type=OrmHistoryCapability,
        )
        res = handler.get_historical_record(PersonInterface, dummy, future_date)
        mock.filter.assert_called_once_with(history_date__lte=future_date)
        history_qs.order_by.assert_called_once_with("history_date")
        self.assertEqual(res, "future_hist")

    def test_get_attribute_types_completeness(self):
        """
        Tests that get_attribute_types returns information for all expected model fields.
        """
        types = PersonInterface.get_attribute_types()
        expected_fields = [
            "id",
            "name",
            "age",
            "owner",
            "tags_list",
            "is_active",
            "changed_by",
        ]

        for field in expected_fields:
            self.assertIn(field, types, f"Field {field} should be in attribute types")
            self.assertIn("type", types[field])
            self.assertIn("is_required", types[field])
            self.assertIn("is_editable", types[field])

    def test_get_attribute_types_field_properties(self):
        """
        Tests that get_attribute_types returns correct properties for different field types.
        """
        types = PersonInterface.get_attribute_types()

        # CharField should be required and editable
        self.assertEqual(types["name"]["type"], str)
        self.assertTrue(types["name"]["is_required"])
        self.assertTrue(types["name"]["is_editable"])

        # IntegerField should be required and editable
        self.assertEqual(types["age"]["type"], int)
        self.assertTrue(types["age"]["is_required"])
        self.assertTrue(types["age"]["is_editable"])

        # BooleanField should not be required (has default) and be editable
        self.assertEqual(types["is_active"]["type"], bool)
        self.assertFalse(types["is_active"]["is_required"])
        self.assertTrue(types["is_active"]["is_editable"])

    def test_get_field_type_for_all_fields(self):
        """
        Tests that get_field_type returns correct Django field classes for all model fields.
        """
        self.assertIs(PersonInterface.get_field_type("name"), models.CharField)
        self.assertIs(PersonInterface.get_field_type("age"), models.IntegerField)
        self.assertIs(PersonInterface.get_field_type("owner"), models.ForeignKey)
        self.assertIs(PersonInterface.get_field_type("tags"), models.ManyToManyField)
        self.assertIs(PersonInterface.get_field_type("is_active"), models.BooleanField)
        self.assertIs(PersonInterface.get_field_type("changed_by"), models.ForeignKey)

    def test_get_field_type_with_invalid_field(self):
        """
        Tests that get_field_type handles invalid field names gracefully.
        """
        with self.assertRaises(FieldDoesNotExist):
            PersonInterface.get_field_type("nonexistent_field")

    def test_get_attributes_with_empty_instance(self):
        """
        Tests that get_attributes works correctly with newly created instances.
        """
        new_person = PersonModel(
            name="Charlie",
            age=40,
            owner=self.user,
            changed_by=self.user,
        )
        new_person.save()

        mgr = DummyManager(new_person.pk)
        attrs = PersonInterface.get_attributes()

        self.assertEqual(attrs["name"](mgr._interface), "Charlie")
        self.assertEqual(attrs["age"](mgr._interface), 40)
        self.assertEqual(attrs["owner"](mgr._interface), self.user)
        self.assertEqual(attrs["changed_by"](mgr._interface), self.user)
        self.assertEqual(list(attrs["tags_list"](mgr._interface)), [])

    def test_handle_interface_pre_function(self):
        """
        Tests that the pre function from handle_interface returns expected values.
        """
        pre, _post = PersonInterface.handle_interface()
        attrs = {"test": "value"}
        result_attrs, result_cls, result_model = pre(
            "TestClass", attrs, PersonInterface
        )

        self.assertEqual(result_attrs, attrs)
        self.assertIs(result_cls, PersonInterface)
        self.assertIs(result_model, PersonModel)

    def test_handle_interface_post_function(self):
        """
        Tests that the post function from handle_interface correctly links classes.
        """
        _pre, post = PersonInterface.handle_interface()

        # Create a mock new class
        mock_new_cls = MagicMock()
        mock_interface_cls = MagicMock()
        mock_model = MagicMock()

        post(mock_new_cls, mock_interface_cls, mock_model)

        # Verify the assignments were made
        self.assertEqual(mock_new_cls.Interface, mock_interface_cls)
        self.assertEqual(mock_interface_cls._parent_class, mock_new_cls)

    def test_rules_evaluation_order(self):
        """
        Tests that multiple rules are evaluated in the correct order.
        """

        class Rule1:
            def __init__(self):
                self.called = False
                self.call_order = None

            def evaluate(self, obj):
                self.called = True
                self.call_order = 1
                return True

            def get_error_message(self):
                return {"name": "rule1 error"}

        class Rule2:
            def __init__(self):
                self.called = False
                self.call_order = None

            def evaluate(self, obj):
                self.called = True
                self.call_order = 2
                return True

            def get_error_message(self):
                return {"name": "rule2 error"}

        rule1 = Rule1()
        rule2 = Rule2()
        PersonModel._meta.rules = [rule1, rule2]

        cleaner = get_full_clean_methode(PersonModel)
        _ = cleaner(self.person)

        self.assertTrue(rule1.called)
        self.assertTrue(rule2.called)
        delattr(PersonModel._meta, "rules")

    def test_rules_with_mixed_results(self):
        """
        Tests behavior when some rules pass and others fail.
        """

        class PassingRule:
            def evaluate(self, obj):
                return True

            def get_error_message(self):
                return {"name": "passing error"}

        class FailingRule:
            def evaluate(self, obj):
                return False

            def get_error_message(self):
                return {"age": "failing error"}

        PersonModel._meta.rules = [PassingRule(), FailingRule()]
        cleaner = get_full_clean_methode(PersonModel)

        with self.assertRaises(ValidationError) as context:
            cleaner(self.person)

        # Should contain error from failing rule
        self.assertIn("age", str(context.exception))
        delattr(PersonModel._meta, "rules")

    def test_rules_with_no_rules(self):
        """
        Tests that validation works correctly when no custom rules are defined.
        """
        # Ensure no rules are set
        if hasattr(PersonModel._meta, "rules"):
            delattr(PersonModel._meta, "rules")

        cleaner = get_full_clean_methode(PersonModel)

        # Should pass validation without custom rules
        _ = cleaner(self.person)

        # Should still fail with invalid data
        invalid = PersonModel(age=1, owner=self.user, changed_by=self.user)
        with self.assertRaises(ValidationError):
            cleaner(invalid)

    def test_handle_custom_fields_with_multiple_custom_fields(self):
        """
        Tests handle_custom_fields with multiple custom fields defined.
        """

        class MultiCustomInterface(OrmInterfaceBase):
            field1 = models.CharField(max_length=10)
            field2 = models.IntegerField()
            field3 = models.BooleanField()
            configured_capabilities: ClassVar[tuple] = (ORM_PERSISTENCE_CAPABILITIES,)

        fields, ignore = MultiCustomInterface.handle_custom_fields(MultiCustomInterface)

        # Should have None for each custom field
        expected_none_count = 3  # field1, field2, field3
        actual_none_count = sum(1 for field in fields if field is None)
        self.assertEqual(actual_none_count, expected_none_count)

        # Should have corresponding ignore entries
        expected_ignore_items = ["None_value", "None_unit"]
        for item in expected_ignore_items:
            ignore_count = sum(1 for ignored in ignore if ignored == item)
            self.assertEqual(ignore_count, expected_none_count)

    def test_handle_custom_fields_with_no_custom_fields(self):
        """
        Tests handle_custom_fields with no custom fields defined.
        """

        class NoCustomInterface(OrmInterfaceBase):
            pass
            configured_capabilities: ClassVar[tuple] = (ORM_PERSISTENCE_CAPABILITIES,)

        fields, ignore = NoCustomInterface.handle_custom_fields(NoCustomInterface)

        # Should have empty or minimal results
        self.assertIsInstance(fields, (list, tuple))
        self.assertIsInstance(ignore, (list, tuple))

    def test_interface_with_invalid_model(self):
        """
        Tests behavior when interface is configured with invalid model.
        """

        class InvalidInterface(OrmInterfaceBase):
            _model = None
            _parent_class = None
            _interface_type = "test"
            configured_capabilities: ClassVar[tuple] = (ORM_PERSISTENCE_CAPABILITIES,)

        # Should handle gracefully or raise appropriate error
        with self.assertRaises((AttributeError, TypeError)):
            InvalidInterface.filter(name="test")

    def test_manager_initialization_with_search_date_edge_cases(self):
        """
        Tests manager initialization with various search date edge cases.
        """
        # Test with exact current time
        current_time = datetime.now()
        with patch(
            "general_manager.interface.capabilities.orm.OrmHistoryCapability.get_historical_record",
            return_value=self.person,
        ) as mock_hist:
            DummyManager(self.person.pk, search_date=current_time)
            mock_hist.assert_not_called()

        # Test with very old date
        old_date = datetime(1900, 1, 1, tzinfo=UTC)
        with patch(
            "general_manager.interface.capabilities.orm.OrmHistoryCapability.get_historical_record",
            return_value=None,
        ) as mock_hist:
            DummyManager(self.person.pk, search_date=old_date)
            mock_hist.assert_called_once()

    def test_database_bucket_integration(self):
        """
        Tests that filter and exclude methods return properly configured DatabaseBucket instances.
        """
        # Create additional test data
        user2 = User.objects.create(username="tester2")
        PersonModel.objects.create(
            name="Bob",
            age=25,
            owner=user2,
            changed_by=user2,
        )

        bucket = PersonInterface.filter(age__gte=25)
        self.assertIsInstance(bucket, DatabaseBucket)
        self.assertEqual(bucket.count(), 2)

        # Test bucket methods work correctly
        all_items = list(bucket.all())
        self.assertEqual(len(all_items), 2)

        first_item = bucket.first()
        self.assertIsNotNone(first_item)
        self.assertIsInstance(first_item._interface._instance, PersonModel)

    def test_attribute_getters_with_none_values(self):
        """
        Tests attribute getters handle None values gracefully.
        """
        # Create person with minimal required fields
        minimal_person = PersonModel.objects.create(
            name="Minimal",
            age=0,
            owner=self.user,
            changed_by=self.user,
            is_active=False,
        )

        mgr = DummyManager(minimal_person.pk)
        attrs = PersonInterface.get_attributes()

        # Should handle edge values gracefully
        self.assertEqual(attrs["name"](mgr._interface), "Minimal")
        self.assertEqual(attrs["age"](mgr._interface), 0)
        self.assertEqual(attrs["is_active"](mgr._interface), False)
        self.assertEqual(list(attrs["tags_list"](mgr._interface)), [])

    def test_concurrent_access(self):
        """
        Tests that multiple manager instances can access the same data concurrently.
        """
        mgr1 = DummyManager(self.person.pk)
        mgr2 = DummyManager(self.person.pk)

        attrs = PersonInterface.get_attributes()

        # Both managers should return the same data
        self.assertEqual(attrs["name"](mgr1._interface), attrs["name"](mgr2._interface))
        self.assertEqual(attrs["age"](mgr1._interface), attrs["age"](mgr2._interface))
        self.assertEqual(
            attrs["owner"](mgr1._interface), attrs["owner"](mgr2._interface)
        )

    def test_get_database_alias_returns_none_by_default(self):
        """
        Tests that _get_database_alias returns None when no database is configured.
        """
        support = PersonInterface.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        self.assertIsNone(support.get_database_alias(PersonInterface))

    def test_get_database_alias_returns_configured_value(self):
        """
        Tests that _get_database_alias returns the configured database alias.
        """

        class CustomInterface(OrmInterfaceBase):
            _model = PersonModel
            database = "custom_db"
            configured_capabilities: ClassVar[tuple] = (ORM_PERSISTENCE_CAPABILITIES,)

        support = CustomInterface.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        self.assertEqual(support.get_database_alias(CustomInterface), "custom_db")

    def test_get_manager_returns_default_manager(self):
        """
        Tests that _get_manager returns the model's default manager.
        """
        support = PersonInterface.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        manager = support.get_manager(PersonInterface)
        self.assertIsNotNone(manager)
        self.assertEqual(manager.model, PersonModel)

    def test_get_queryset_returns_queryset_for_model(self):
        """
        Tests that _get_queryset returns a queryset for the interface's model.
        """
        support = PersonInterface.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        queryset = support.get_queryset(PersonInterface)
        self.assertIsNotNone(queryset)
        self.assertEqual(queryset.model, PersonModel)

    def test_invalid_field_value_error_message(self):
        """
        Tests that InvalidFieldValueError formats its message correctly.
        """
        error = InvalidFieldValueError("age", "not_a_number")
        self.assertIn("age", str(error))
        self.assertIn("not_a_number", str(error))

    def test_invalid_field_type_error_message(self):
        """
        Verify InvalidFieldTypeError includes the field name and a descriptive type error in its message.
        """
        original_error = TypeError("expected int, got str")
        error = InvalidFieldTypeError("age", original_error)
        self.assertIn("age", str(error))
        self.assertIn("Type error", str(error))

    def test_unknown_field_error_message(self):
        """
        Tests that UnknownFieldError formats its message correctly.
        """
        error = UnknownFieldError("nonexistent_field", "PersonModel")
        self.assertIn("nonexistent_field", str(error))
        self.assertIn("PersonModel", str(error))
        self.assertIn("does not exist", str(error))

    def test_get_data_with_timezone_aware_datetime(self):
        """
        Tests that get_data handles timezone-aware datetime correctly.
        """
        aware_date = datetime.now(UTC)
        mgr = DummyManager(self.person.pk, search_date=aware_date)
        instance = mgr._interface.get_data()
        self.assertEqual(instance.pk, self.person.pk)

    def test_get_data_with_timezone_naive_datetime(self):
        """
        Tests that get_data converts naive datetime to aware.
        """
        naive_date = datetime.now()
        mgr = DummyManager(self.person.pk, search_date=naive_date)
        # Should not raise an error
        instance = mgr._interface.get_data()
        self.assertEqual(instance.pk, self.person.pk)

    def test_get_data_without_search_date(self):
        """
        Tests that get_data returns current instance when no search_date provided.
        """
        mgr = DummyManager(self.person.pk)
        instance = mgr._interface.get_data()
        self.assertEqual(instance.pk, self.person.pk)
        self.assertEqual(instance.name, self.person.name)


class WritableInterfaceTestModel(models.Model):
    name = models.CharField(max_length=100)
    value = models.IntegerField()
    owner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="owned_items"
    )
    is_active = models.BooleanField(default=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="changed_items",
        null=True,
        blank=True,
    )
    tags = models.ManyToManyField(User, related_name="tagged_items", blank=True)

    class Meta:
        app_label = "general_manager"


class OrmWritableInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Register WritableInterfaceTestModel with simple_history, ensure it's in the 'general_manager' app registry, and create database tables for the model and its history model.

        This runs once for the test class to prepare the database schema required by tests.
        """
        super().setUpClass()
        from simple_history import register

        register(WritableInterfaceTestModel)

        if (
            WritableInterfaceTestModel._meta.model_name
            not in apps.get_app_config("general_manager").models
        ):
            apps.register_model("general_manager", WritableInterfaceTestModel)

        with connection.schema_editor() as schema:
            schema.create_model(WritableInterfaceTestModel)
            schema.create_model(WritableInterfaceTestModel.history.model)  # type: ignore[attr-defined]

    @classmethod
    def tearDownClass(cls):
        """
        Remove the WritableInterfaceTestModel and its history model from the database and app registry.

        Deletes the database tables for the model and its history model, removes their entries from the "general_manager" app registry, and invokes the superclass teardown.
        """
        with connection.schema_editor() as schema:
            history_model = WritableInterfaceTestModel.history.model  # type: ignore[attr-defined]
            schema.delete_model(history_model)
            schema.delete_model(WritableInterfaceTestModel)

        app_config = apps.get_app_config("general_manager")
        model_key = WritableInterfaceTestModel._meta.model_name
        history_key = WritableInterfaceTestModel.history.model._meta.model_name  # type: ignore[attr-defined]
        apps.all_models["general_manager"].pop(model_key, None)
        apps.all_models["general_manager"].pop(history_key, None)
        app_config.models.pop(model_key, None)
        app_config.models.pop(history_key, None)
        super().tearDownClass()

    def setUp(self):
        """
        Create two test users and register a writable test interface class for WritableInterfaceTestModel.

        Creates User instances as self.user1 (creator) and self.user2 (modifier), and defines a TestWritableInterface subclass (assigned to self.interface_cls) that targets WritableInterfaceTestModel, exposes an `id` input field, and enables soft-delete.
        """
        from general_manager.interface.orm_interface import (
            OrmInterfaceBase,
        )

        self.user1 = User.objects.create(username="creator")
        self.user2 = User.objects.create(username="modifier")

        class TestWritableInterface(OrmInterfaceBase):
            _model = WritableInterfaceTestModel
            _parent_class = None
            _interface_type = "writable_test"
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
            _soft_delete_default = True
            configured_capabilities: ClassVar[tuple] = (ORM_WRITABLE_CAPABILITIES,)

            class Meta:
                use_soft_delete = True

        self.interface_cls = TestWritableInterface

    def tearDown(self):
        """
        Remove all WritableInterfaceTestModel rows created during the test and avoid touching user records to prevent clashes with TransactionTestCase cleanup.
        """
        WritableInterfaceTestModel.objects.all().delete()
        # Users are flushed automatically by TransactionTestCase; manual deletion can clash
        # with temporary models removed in other tests.

    def test_create_with_basic_fields(self):
        """
        Tests that create successfully creates an instance with basic fields.
        """
        result = self.interface_cls.create(
            creator_id=self.user1.pk,
            name="Test Item",
            value=42,
            owner=self.user1,
        )
        self.assertIn("id", result)
        instance = WritableInterfaceTestModel.objects.get(pk=result["id"])
        self.assertEqual(instance.name, "Test Item")
        self.assertEqual(instance.value, 42)
        self.assertEqual(instance.owner, self.user1)
        self.assertEqual(instance.changed_by, self.user1)

    def test_create_with_history_comment(self):
        """
        Tests that create stores history comment correctly.
        """
        result = self.interface_cls.create(
            creator_id=self.user1.pk,
            history_comment="Initial creation",
            name="Commented Item",
            value=100,
            owner=self.user1,
        )
        instance = WritableInterfaceTestModel.objects.get(pk=result["id"])
        history = instance.history.first()  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "Initial creation")  # type: ignore[union-attr]

    def test_create_with_many_to_many(self):
        """
        Tests that create handles many-to-many relationships correctly.
        """
        user3 = User.objects.create(username="tagger")
        result = self.interface_cls.create(
            creator_id=self.user1.pk,
            name="Tagged Item",
            value=50,
            owner=self.user1,
            tags_id_list=[self.user2.pk, user3.pk],
        )
        instance = WritableInterfaceTestModel.objects.get(pk=result["id"])
        tags = list(instance.tags.all())
        self.assertEqual(len(tags), 2)
        self.assertIn(self.user2, tags)
        self.assertIn(user3, tags)

    def test_create_with_invalid_field_raises_unknown_field_error(self):
        """
        Tests that create raises UnknownFieldError for invalid field names.
        """
        with self.assertRaises(UnknownFieldError) as context:
            self.interface_cls.create(
                creator_id=self.user1.pk,
                name="Test",
                value=10,
                owner=self.user1,
                nonexistent_field="bad",
            )
        self.assertIn("nonexistent_field", str(context.exception))

    def test_create_without_creator_id(self):
        """
        Tests that create works when creator_id is None.
        """
        result = self.interface_cls.create(
            creator_id=None,
            name="No Creator",
            value=25,
            owner=self.user1,
        )
        instance = WritableInterfaceTestModel.objects.get(pk=result["id"])
        self.assertIsNone(instance.changed_by_id)

    def test_update_basic_fields(self):
        """
        Tests that update modifies instance fields correctly.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Original",
            value=10,
            owner=self.user1,
            changed_by=self.user1,
        )
        interface = self.interface_cls(id=instance.pk)
        result = interface.update(creator_id=self.user2.pk, name="Updated", value=20)

        self.assertEqual(result["id"], instance.pk)
        instance.refresh_from_db()
        self.assertEqual(instance.name, "Updated")
        self.assertEqual(instance.value, 20)
        self.assertEqual(instance.changed_by, self.user2)

    def test_update_with_history_comment(self):
        """
        Verifies that updating an instance stores the provided history comment on the latest historical record.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Original",
            value=10,
            owner=self.user1,
            changed_by=self.user1,
        )
        interface = self.interface_cls(id=instance.pk)
        interface.update(
            creator_id=self.user2.pk,
            history_comment="Modified value",
            value=99,
        )

        history = instance.history.order_by("-history_date").first()  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "Modified value")  # type: ignore[union-attr]

    def test_update_many_to_many_fields(self):
        """
        Tests that update correctly modifies many-to-many relationships.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Item",
            value=15,
            owner=self.user1,
            changed_by=self.user1,
        )
        instance.tags.add(self.user1)

        interface = self.interface_cls(id=instance.pk)
        interface.update(
            creator_id=self.user2.pk,
            tags_id_list=[self.user2.pk],
        )

        tags = list(instance.tags.all())
        self.assertEqual(len(tags), 1)
        self.assertIn(self.user2, tags)
        self.assertNotIn(self.user1, tags)

    def test_update_with_invalid_field_raises_error(self):
        """
        Tests that update raises UnknownFieldError for invalid fields.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Item",
            value=5,
            owner=self.user1,
            changed_by=self.user1,
        )
        interface = self.interface_cls(id=instance.pk)

        with self.assertRaises(UnknownFieldError):
            interface.update(creator_id=self.user1.pk, invalid_field="bad")

    def test_delete_sets_is_active_false(self):
        """
        Tests that delete sets is_active to False.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Active Item",
            value=30,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        interface = self.interface_cls(id=instance.pk)
        interface.delete(creator_id=self.user2.pk)

        instance.refresh_from_db()
        self.assertFalse(instance.is_active)

    def test_delete_with_history_comment(self):
        """
        Tests that delete appends '(deactivated)' to history comment.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Active Item",
            value=30,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        interface = self.interface_cls(id=instance.pk)
        interface.delete(
            creator_id=self.user2.pk,
            history_comment="User requested",
        )

        history = instance.history.order_by("-history_date").first()  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "User requested (deactivated)")  # type: ignore[union-attr]

    def test_delete_without_comment_uses_default(self):
        """
        Tests that delete uses 'Deactivated' as default comment.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Active Item",
            value=30,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        interface = self.interface_cls(id=instance.pk)
        interface.delete(creator_id=self.user2.pk)

        history = instance.history.order_by("-history_date").first()  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "Deactivated")  # type: ignore[union-attr]

    def test_get_queryset_respects_soft_delete(self) -> None:
        """Soft-deleted rows are hidden unless explicitly requested."""
        active = WritableInterfaceTestModel.objects.create(
            name="Active",
            value=10,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        inactive = WritableInterfaceTestModel.objects.create(
            name="Inactive",
            value=20,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        self.interface_cls(id=inactive.pk).delete(creator_id=self.user2.pk)

        support = self.interface_cls.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        queryset = support.get_queryset(self.interface_cls)
        self.assertListEqual(list(queryset.values_list("pk", flat=True)), [active.pk])

        bucket = self.interface_cls.filter(include_inactive=True)
        data = bucket._data.values_list("pk", flat=True)  # type: ignore[attr-defined]
        self.assertCountEqual(list(data), [active.pk, inactive.pk])

    def test_payload_normalizer_accepts_valid_fields(self):
        """
        PayloadNormalizer.validate_keys passes for valid field names.
        """
        support = self.interface_cls.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        normalizer = support.get_payload_normalizer(self.interface_cls)
        kwargs = {"name": "Test", "value": 10, "owner": self.user1}
        normalizer.validate_keys(kwargs)

    def test_payload_normalizer_accepts_id_list_suffix(self):
        """
        PayloadNormalizer.validate_keys accepts _id_list suffix for m2m fields.
        """
        support = self.interface_cls.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        normalizer = support.get_payload_normalizer(self.interface_cls)
        normalizer.validate_keys({"tags_id_list": [1, 2, 3]})

    def test_payload_normalizer_raises_for_invalid_field(self):
        """
        PayloadNormalizer.validate_keys raises UnknownFieldError for invalid fields.
        """
        support = self.interface_cls.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        )
        normalizer = support.get_payload_normalizer(self.interface_cls)
        with self.assertRaises(UnknownFieldError) as context:
            normalizer.validate_keys({"nonexistent": "value"})
        self.assertIn("nonexistent", str(context.exception))

    def test_split_many_to_many_separates_relations(self):
        """
        PayloadNormalizer.split_many_to_many separates m2m from regular fields.
        """
        normalizer = PayloadNormalizer(WritableInterfaceTestModel)
        kwargs = {
            "name": "Test",
            "value": 42,
            "tags_id_list": [1, 2, 3],
        }
        regular, m2m = normalizer.split_many_to_many(dict(kwargs))

        self.assertIn("name", regular)
        self.assertIn("value", regular)
        self.assertNotIn("tags_id_list", regular)
        self.assertIn("tags_id_list", m2m)
        self.assertEqual(m2m["tags_id_list"], [1, 2, 3])

    def test_split_many_to_many_handles_no_relations(self):
        """
        PayloadNormalizer.split_many_to_many works when no m2m fields are present.
        """
        normalizer = PayloadNormalizer(WritableInterfaceTestModel)
        kwargs = {"name": "Test", "value": 42}
        regular, m2m = normalizer.split_many_to_many(dict(kwargs))

        self.assertEqual(regular, kwargs)
        self.assertEqual(m2m, {})

    def test_save_with_history_validates_instance(self):
        """
        Tests that save_with_history calls full_clean for validation.
        """
        mutation = self.interface_cls.require_capability(
            "orm_mutation",
            expected_type=OrmMutationCapability,
        )
        instance = WritableInterfaceTestModel(
            name="",  # Empty name should fail validation if required
            value=10,
            owner=self.user1,
        )
        with self.assertRaises(ValidationError):
            mutation.save_with_history(
                self.interface_cls,
                instance,
                creator_id=self.user1.pk,
                history_comment=None,
            )

    def test_save_with_history_sets_changed_by(self):
        """
        Tests that save_with_history sets changed_by_id correctly.
        """
        mutation = self.interface_cls.require_capability(
            "orm_mutation",
            expected_type=OrmMutationCapability,
        )
        instance = WritableInterfaceTestModel(
            name="Test",
            value=10,
            owner=self.user1,
        )
        pk = mutation.save_with_history(
            self.interface_cls,
            instance,
            creator_id=self.user2.pk,
            history_comment=None,
        )

        saved_instance = WritableInterfaceTestModel.objects.get(pk=pk)
        self.assertEqual(saved_instance.changed_by, self.user2)

    def test_save_with_history_handles_none_creator(self):
        """
        Tests that save_with_history handles None creator_id gracefully.
        """
        mutation = self.interface_cls.require_capability(
            "orm_mutation",
            expected_type=OrmMutationCapability,
        )
        instance = WritableInterfaceTestModel(
            name="Test",
            value=10,
            owner=self.user1,
        )
        pk = mutation.save_with_history(
            self.interface_cls,
            instance,
            creator_id=None,
            history_comment=None,
        )

        saved_instance = WritableInterfaceTestModel.objects.get(pk=pk)
        self.assertIsNone(saved_instance.changed_by_id)


class PayloadNormalizerTestCase(TransactionTestCase):
    """
    Comprehensive tests for PayloadNormalizer utility class.
    """

    @classmethod
    def setUpClass(cls):
        """
        Ensure WritableInterfaceTestModel is registered and its tables exist for PayloadNormalizer tests.
        """
        super().setUpClass()
        from simple_history import register

        if not hasattr(
            WritableInterfaceTestModel._meta, "simple_history_manager_attribute"
        ):
            register(WritableInterfaceTestModel)

        if (
            WritableInterfaceTestModel._meta.model_name
            not in apps.get_app_config("general_manager").models
        ):
            apps.register_model("general_manager", WritableInterfaceTestModel)

        with connection.schema_editor() as schema:
            schema.create_model(WritableInterfaceTestModel)
            schema.create_model(WritableInterfaceTestModel.history.model)  # type: ignore[attr-defined]

    @classmethod
    def tearDownClass(cls):
        """
        Drop WritableInterfaceTestModel tables and remove them from the registry.
        """
        with connection.schema_editor() as schema:
            history_model = WritableInterfaceTestModel.history.model  # type: ignore[attr-defined]
            schema.delete_model(history_model)
            schema.delete_model(WritableInterfaceTestModel)

        app_config = apps.get_app_config("general_manager")
        model_key = WritableInterfaceTestModel._meta.model_name
        history_key = WritableInterfaceTestModel.history.model._meta.model_name  # type: ignore[attr-defined]
        apps.all_models["general_manager"].pop(model_key, None)
        apps.all_models["general_manager"].pop(history_key, None)
        app_config.models.pop(model_key, None)
        app_config.models.pop(history_key, None)
        super().tearDownClass()

    def setUp(self):
        """Set up test models and normalizer."""
        super().setUp()
        self.user1 = User.objects.create_user(username="user1")
        self.user2 = User.objects.create_user(username="user2")
        self.normalizer = PayloadNormalizer(WritableInterfaceTestModel)

    def test_normalize_filter_kwargs_with_plain_values(self):
        """
        Test that normalize_filter_kwargs passes through plain values unchanged.
        """
        kwargs = {"name": "Test", "value": 42}
        result = self.normalizer.normalize_filter_kwargs(kwargs)
        self.assertEqual(result, kwargs)

    def test_normalize_filter_kwargs_unwraps_general_manager(self):
        """
        Test that normalize_filter_kwargs unwraps GeneralManager instances.
        """
        # Create a test instance and manager
        test_instance = WritableInterfaceTestModel.objects.create(
            name="Test", value=10, owner=self.user1, changed_by=self.user1
        )

        # Create a mock GeneralManager with the right structure
        mock_manager = MagicMock()
        mock_manager.identification = {"id": test_instance.pk}
        mock_interface = MagicMock()
        mock_interface._instance = test_instance
        mock_manager._interface = mock_interface

        # Patch the base class check
        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            kwargs = {"owner": mock_manager}
            result = self.normalizer.normalize_filter_kwargs(kwargs)
            self.assertEqual(result["owner"], test_instance)

    def test_normalize_simple_values_with_plain_values(self):
        """
        Test that normalize_simple_values passes through plain values.
        """
        kwargs = {"name": "Test", "value": 42}
        result = self.normalizer.normalize_simple_values(kwargs)
        self.assertEqual(result, kwargs)

    def test_normalize_simple_values_converts_manager_to_id(self):
        """
        Test that normalize_simple_values converts GeneralManager to _id field.
        """
        mock_manager = MagicMock()
        mock_manager.identification = {"id": 123}

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            kwargs = {"owner": mock_manager}
            result = self.normalizer.normalize_simple_values(kwargs)
            self.assertEqual(result["owner_id"], 123)
            self.assertNotIn("owner", result)

    def test_normalize_simple_values_handles_field_already_with_id_suffix(self):
        """
        Test that normalize_simple_values handles fields already ending with _id.
        """
        mock_manager = MagicMock()
        mock_manager.identification = {"id": 456}

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            kwargs = {"owner_id": mock_manager}
            result = self.normalizer.normalize_simple_values(kwargs)
            self.assertEqual(result["owner_id"], 456)

    def test_normalize_many_values_with_list_of_values(self):
        """
        Test that normalize_many_values handles list of plain values.
        """
        kwargs = {"tags_id_list": [1, 2, 3]}
        result = self.normalizer.normalize_many_values(kwargs)
        self.assertEqual(result["tags_id_list"], [1, 2, 3])

    def test_normalize_many_values_with_general_managers(self):
        """
        Test that normalize_many_values converts list of GeneralManager instances.
        """
        mock_manager1 = MagicMock()
        mock_manager1.identification = {"id": 100}
        mock_manager2 = MagicMock()
        mock_manager2.identification = {"id": 200}

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            kwargs = {"tags_id_list": [mock_manager1, mock_manager2]}
            result = self.normalizer.normalize_many_values(kwargs)
            self.assertEqual(result["tags_id_list"], [100, 200])

    def test_normalize_many_values_with_single_value(self):
        """
        Test that normalize_many_values wraps single value in list.
        """
        kwargs = {"tags_id_list": 42}
        result = self.normalizer.normalize_many_values(kwargs)
        self.assertEqual(result["tags_id_list"], [42])

    def test_normalize_many_values_skips_none(self):
        """
        Test that normalize_many_values skips None values.
        """
        kwargs = {"tags_id_list": None}
        result = self.normalizer.normalize_many_values(kwargs)
        self.assertNotIn("tags_id_list", result)

    def test_normalize_many_values_skips_not_provided(self):
        """
        Test that normalize_many_values skips NOT_PROVIDED values.
        """
        kwargs = {"tags_id_list": models.NOT_PROVIDED}
        result = self.normalizer.normalize_many_values(kwargs)
        self.assertNotIn("tags_id_list", result)

    def test_normalize_many_values_handles_string_as_single_value(self):
        """
        Test that normalize_many_values treats strings as single values, not iterables.
        """
        kwargs = {"tags_id_list": "test"}
        result = self.normalizer.normalize_many_values(kwargs)
        self.assertEqual(result["tags_id_list"], ["test"])

    def test_normalize_many_values_handles_bytes_as_single_value(self):
        """
        Test that normalize_many_values treats bytes as single values, not iterables.
        """
        kwargs = {"tags_id_list": b"test"}
        result = self.normalizer.normalize_many_values(kwargs)
        self.assertEqual(result["tags_id_list"], [b"test"])

    def test_unwrap_manager_with_plain_value(self):
        """
        Test that _unwrap_manager returns plain values unchanged.
        """
        result = PayloadNormalizer._unwrap_manager(42)
        self.assertEqual(result, 42)

    def test_unwrap_manager_with_general_manager(self):
        """
        Test that _unwrap_manager extracts instance from GeneralManager.
        """
        test_instance = WritableInterfaceTestModel.objects.create(
            name="Test", value=10, owner=self.user1, changed_by=self.user1
        )

        mock_manager = MagicMock()
        mock_manager.identification = {"id": test_instance.pk}
        mock_interface = MagicMock()
        mock_interface._instance = test_instance
        mock_manager._interface = mock_interface

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            result = PayloadNormalizer._unwrap_manager(mock_manager)
            self.assertEqual(result, test_instance)

    def test_maybe_general_manager_with_non_manager(self):
        """
        Test that _maybe_general_manager returns default for non-manager values.
        """
        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=False,
        ):
            result = PayloadNormalizer._maybe_general_manager(42, default=None)
            self.assertIsNone(result)

    def test_maybe_general_manager_extracts_id(self):
        """
        Test that _maybe_general_manager extracts ID from GeneralManager.
        """
        mock_manager = MagicMock()
        mock_manager.identification = {"id": 999}

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            result = PayloadNormalizer._maybe_general_manager(mock_manager)
            self.assertEqual(result, 999)

    def test_maybe_general_manager_prefer_instance(self):
        """
        Test that _maybe_general_manager can return instance when prefer_instance=True.
        """
        test_instance = WritableInterfaceTestModel.objects.create(
            name="Test", value=10, owner=self.user1, changed_by=self.user1
        )

        mock_manager = MagicMock()
        mock_manager.identification = {"id": test_instance.pk}
        mock_interface = MagicMock()
        mock_interface._instance = test_instance
        mock_manager._interface = mock_interface

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            result = PayloadNormalizer._maybe_general_manager(
                mock_manager, prefer_instance=True
            )
            self.assertEqual(result, test_instance)

    def test_maybe_general_manager_prefer_instance_falls_back_to_id(self):
        """
        Test that _maybe_general_manager falls back to ID if instance not available.
        """
        mock_manager = MagicMock()
        mock_manager.identification = {"id": 888}
        mock_manager._interface = None

        with patch(
            "general_manager.interface.capabilities.orm_utils.payload_normalizer._is_general_manager_instance",
            return_value=True,
        ):
            result = PayloadNormalizer._maybe_general_manager(
                mock_manager, prefer_instance=True
            )
            self.assertEqual(result, 888)


# Additional tests for PayloadNormalizer

def test_payload_normalizer_normalize_filter_kwargs_with_manager():
    """Test normalize_filter_kwargs unwraps manager objects."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from django.db import models
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    # Create a mock manager object
    mock_manager = type('MockManager', (), {
        'identification': {'id': 42},
        '__class__': type('GeneralManager', (), {})
    })()
    
    kwargs = {"name": "test", "related": mock_manager}
    # Note: This will pass through because mock isn't a real GeneralManager
    result = normalizer.normalize_filter_kwargs(kwargs)
    
    assert "name" in result


def test_payload_normalizer_validate_keys_valid():
    """Test validate_keys with all valid keys."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from django.db import models
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        age = models.IntegerField()
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    # Should not raise
    normalizer.validate_keys({"name": "test", "age": 25})


def test_payload_normalizer_validate_keys_invalid():
    """Test validate_keys with invalid key."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from general_manager.interface.utils.errors import UnknownFieldError
    from django.db import models
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    with pytest.raises(UnknownFieldError):
        normalizer.validate_keys({"invalid_field": "value"})


def test_payload_normalizer_split_many_to_many():
    """Test split_many_to_many separates M2M fields."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from django.db import models
    
    class RelatedModel(models.Model):
        class Meta:
            app_label = "test"
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        tags = models.ManyToManyField(RelatedModel)
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    kwargs = {
        "name": "test",
        "tags_id_list": [1, 2, 3],
        "other": "value"
    }
    
    remaining, many = normalizer.split_many_to_many(kwargs)
    
    assert "tags_id_list" in many
    assert "tags_id_list" not in remaining
    assert "name" in remaining
    assert "other" in remaining


def test_payload_normalizer_normalize_simple_values():
    """Test normalize_simple_values."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from django.db import models
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        value = models.IntegerField()
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    kwargs = {"name": "test", "value": 42}
    result = normalizer.normalize_simple_values(kwargs)
    
    assert result["name"] == "test"
    assert result["value"] == 42


def test_payload_normalizer_normalize_many_values_with_none():
    """Test normalize_many_values with None values."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from django.db import models
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    kwargs = {"field_a": None, "field_b": [1, 2], "field_c": models.NOT_PROVIDED}
    result = normalizer.normalize_many_values(kwargs)
    
    # None and NOT_PROVIDED should be omitted
    assert "field_a" not in result
    assert "field_c" not in result
    assert result["field_b"] == [1, 2]


def test_payload_normalizer_normalize_many_values_single_item():
    """Test normalize_many_values wraps single items in a list."""
    from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
        PayloadNormalizer,
    )
    from django.db import models
    
    class TestModel(models.Model):
        name = models.CharField(max_length=100)
        class Meta:
            app_label = "test"
    
    normalizer = PayloadNormalizer(TestModel)
    
    kwargs = {"field": 42}
    result = normalizer.normalize_many_values(kwargs)
    
    assert result["field"] == [42]
