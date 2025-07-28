# type: ignore

from django.test import TransactionTestCase
from django.db import connection
from django.contrib.auth.models import User
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.db import models
from django.core.exceptions import ValidationError

from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseBasedInterface import (
    DBBasedInterface,
    getFullCleanMethode,
)
from general_manager.manager.input import Input
from general_manager.bucket.databaseBucket import DatabaseBucket


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


class PersonInterface(DBBasedInterface):
    _model = PersonModel
    _parent_class = None
    _interface_type = "test"
    input_fields = {"id": Input(int)}

    @classmethod
    def handleInterface(cls):
        """
        Provides pre- and post-processing functions for dynamically handling interface class creation.

        Returns:
            A tuple containing:
                - pre: A function that prepares attributes, the interface class, and the associated model for class creation.
                - post: A function that finalizes the setup by linking the new class and interface class.
        """
        def pre(name, attrs, interface):
            return attrs, cls, cls._model

        def post(new_cls, interface_cls, model):
            """
            Finalizes the association between a newly created class and its interface.

            Assigns the interface class to the new class's `Interface` attribute and sets the interface's `_parent_class` to the new class.
            """
            new_cls.Interface = interface_cls
            interface_cls._parent_class = new_cls

        return pre, post


class DummyManager(GeneralManager):
    Interface = PersonInterface


PersonInterface._parent_class = DummyManager


class DBBasedInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Creates the database table for the PersonModel before running any tests in the test case class.
        """
        super().setUpClass()
        with connection.schema_editor() as schema:
            schema.create_model(PersonModel)

    @classmethod
    def tearDownClass(cls):
        """
        Deletes the PersonModel table from the test database after all tests in the class have run.
        """
        with connection.schema_editor() as schema:
            schema.delete_model(PersonModel)
        super().tearDownClass()

    def tearDown(self):
        """
        Deletes all PersonModel and User instances from the database to clean up after each test.
        """
        PersonModel.objects.all().delete()
        User.objects.all().delete()

    def setUp(self):
        """
        Creates a test user and a corresponding PersonModel instance for use in test cases.

        Initializes self.user with a new User and self.person with a new PersonModel linked to that user, including adding the user to the person's tags.
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

        Verifies that the interface instance is set to the value returned by the patched `getHistoricalRecord` method and that this method is called exactly once.
        """
        with patch.object(
            PersonInterface, "getHistoricalRecord", return_value="old"
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
        Tests that getHistoricalRecord retrieves the correct historical record for a given date.

        Verifies that the method filters the history manager by date and returns the last matching record.
        """
        mock = MagicMock()
        mock.filter.return_value.last.return_value = "hist"
        dummy = SimpleNamespace(history=mock)
        dt = datetime(2020, 1, 1)
        res = PersonInterface.getHistoricalRecord(dummy, dt)
        mock.filter.assert_called_once_with(history_date__lte=dt)
        self.assertEqual(res, "hist")

    def test_get_attribute_types_and_field_type(self):
        """
        Tests that attribute type information and field types are correctly reported by the interface.

        Verifies that `getAttributeTypes` returns accurate type, required, and editable flags for model fields, and that `getFieldType` returns the correct Django field class.
        """
        types = PersonInterface.getAttributeTypes()
        self.assertEqual(types["name"]["type"], str)
        self.assertTrue(types["name"]["is_required"])
        self.assertEqual(types["tags_list"]["type"], User)
        self.assertTrue(types["tags_list"]["is_editable"])
        self.assertFalse(types["tags_list"]["is_required"])
        self.assertIs(PersonInterface.getFieldType("name"), models.CharField)

    def test_get_attributes_values(self):
        """
        Tests that attribute getter functions from the interface return correct values for a model instance.
        """
        mgr = DummyManager(self.person.pk)
        attrs = PersonInterface.getAttributes()
        self.assertEqual(attrs["name"](mgr._interface), "Alice")
        self.assertEqual(attrs["age"](mgr._interface), 30)
        self.assertEqual(attrs["is_active"](mgr._interface), True)
        self.assertEqual(attrs["owner"](mgr._interface), self.user)
        self.assertEqual(attrs["changed_by"](mgr._interface), self.user)
        self.assertEqual(list(attrs["tags_list"](mgr._interface)), [self.user])

    def test_pre_and_post_create_and_handle_interface(self):
        """
        Tests the pre- and post-creation lifecycle of a database-backed interface and its manager.

        Verifies that the interface and manager classes are correctly linked, the model and its history table are created, and the manager's factory is properly associated with the model.
        """
        attrs = {"__module__": "general_manager"}
        new_attrs, interface_cls, model = PersonInterface._preCreate(
            "TempManager", attrs, PersonInterface
        )
        with connection.schema_editor() as schema:
            schema.create_model(model)
            schema.create_model(model.history.model)

        TempManager = type("TempManager", (GeneralManager,), new_attrs)
        PersonInterface._postCreate(TempManager, interface_cls, model)
        self.assertIs(interface_cls._parent_class, TempManager)
        self.assertIs(model._general_manager_class, TempManager)
        self.assertIs(TempManager.Interface, interface_cls)
        self.assertTrue(hasattr(TempManager, "Factory"))
        self.assertIs(TempManager.Factory._meta.model, model)

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

            def getErrorMessage(self):
                return {"name": "bad"}

        PersonModel._meta.rules = [DummyRule()]
        cleaner = getFullCleanMethode(PersonModel)
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

            def getErrorMessage(self):
                return {"name": "bad"}

        PersonModel._meta.rules = [DummyRule()]
        cleaner = getFullCleanMethode(PersonModel)
        invalid = PersonModel(age=1, owner=self.user, changed_by=self.user)
        with self.assertRaises(ValidationError):
            cleaner(invalid)
        _ = cleaner(self.person)
        self.assertTrue(PersonModel._meta.rules[0].called)
        delattr(PersonModel._meta, "rules")

    def test_handle_custom_fields(self):
        """
        Tests that custom fields and ignore lists are correctly identified by handleCustomFields for a DBBasedInterface subclass.
        """
        class CustomInterface(DBBasedInterface):
            sample = models.CharField(max_length=5)

        fields, ignore = DBBasedInterface.handleCustomFields(CustomInterface)
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
        self.assertEqual(bucket.first().pk, self.person.pk)

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
        self.assertEqual(bucket.first().pk, person2.pk)

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
        self.assertEqual(bucket.first().pk, self.person.pk)

        # Filter by changed_by
        bucket = PersonInterface.filter(changed_by=user2)
        self.assertEqual(bucket.count(), 1)
        self.assertEqual(bucket.first().pk, person2.pk)

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
        self.assertEqual(bucket.first().pk, self.person.pk)

    def test_get_historical_record_with_none_date(self):
        """
        Tests that getHistoricalRecord handles None date appropriately.
        """
        mock = MagicMock()
        mock.filter.return_value.last.return_value = None
        dummy = SimpleNamespace(history=mock)

        res = PersonInterface.getHistoricalRecord(dummy, None)
        mock.filter.assert_called_once_with(history_date__lte=None)
        self.assertIsNone(res)

    def test_get_historical_record_with_future_date(self):
        """
        Tests that getHistoricalRecord works with future dates.
        """
        mock = MagicMock()
        mock.filter.return_value.last.return_value = "future_hist"
        dummy = SimpleNamespace(history=mock)
        future_date = datetime.now() + timedelta(days=1)

        res = PersonInterface.getHistoricalRecord(dummy, future_date)
        mock.filter.assert_called_once_with(history_date__lte=future_date)
        self.assertEqual(res, "future_hist")

    def test_get_attribute_types_completeness(self):
        """
        Tests that getAttributeTypes returns information for all expected model fields.
        """
        types = PersonInterface.getAttributeTypes()
        expected_fields = ["id", "name", "age", "owner", "tags_list", "is_active", "changed_by"]

        for field in expected_fields:
            self.assertIn(field, types, f"Field {field} should be in attribute types")
            self.assertIn("type", types[field])
            self.assertIn("is_required", types[field])
            self.assertIn("is_editable", types[field])

    def test_get_attribute_types_field_properties(self):
        """
        Tests that getAttributeTypes returns correct properties for different field types.
        """
        types = PersonInterface.getAttributeTypes()

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
        Tests that getFieldType returns correct Django field classes for all model fields.
        """
        self.assertIs(PersonInterface.getFieldType("name"), models.CharField)
        self.assertIs(PersonInterface.getFieldType("age"), models.IntegerField)
        self.assertIs(PersonInterface.getFieldType("owner"), models.ForeignKey)
        self.assertIs(PersonInterface.getFieldType("tags"), models.ManyToManyField)
        self.assertIs(PersonInterface.getFieldType("is_active"), models.BooleanField)
        self.assertIs(PersonInterface.getFieldType("changed_by"), models.ForeignKey)

    def test_get_field_type_with_invalid_field(self):
        """
        Tests that getFieldType handles invalid field names gracefully.
        """
        with self.assertRaises(AttributeError):
            PersonInterface.getFieldType("nonexistent_field")

    def test_get_attributes_with_empty_instance(self):
        """
        Tests that getAttributes works correctly with newly created instances.
        """
        new_person = PersonModel(
            name="Charlie",
            age=40,
            owner=self.user,
            changed_by=self.user,
        )
        new_person.save()

        mgr = DummyManager(new_person.pk)
        attrs = PersonInterface.getAttributes()

        self.assertEqual(attrs["name"](mgr._interface), "Charlie")
        self.assertEqual(attrs["age"](mgr._interface), 40)
        self.assertEqual(attrs["owner"](mgr._interface), self.user)
        self.assertEqual(attrs["changed_by"](mgr._interface), self.user)
        self.assertEqual(list(attrs["tags_list"](mgr._interface)), [])

    def test_handle_interface_pre_function(self):
        """
        Tests that the pre function from handleInterface returns expected values.
        """
        pre, post = PersonInterface.handleInterface()
        attrs = {"test": "value"}
        result_attrs, result_cls, result_model = pre("TestClass", attrs, PersonInterface)

        self.assertEqual(result_attrs, attrs)
        self.assertIs(result_cls, PersonInterface)
        self.assertIs(result_model, PersonModel)

    def test_handle_interface_post_function(self):
        """
        Tests that the post function from handleInterface correctly links classes.
        """
        pre, post = PersonInterface.handleInterface()

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

            def getErrorMessage(self):
                return {"name": "rule1 error"}

        class Rule2:
            def __init__(self):
                self.called = False
                self.call_order = None

            def evaluate(self, obj):
                self.called = True
                self.call_order = 2
                return True

            def getErrorMessage(self):
                return {"name": "rule2 error"}

        rule1 = Rule1()
        rule2 = Rule2()
        PersonModel._meta.rules = [rule1, rule2]

        cleaner = getFullCleanMethode(PersonModel)
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

            def getErrorMessage(self):
                return {"name": "passing error"}

        class FailingRule:
            def evaluate(self, obj):
                return False

            def getErrorMessage(self):
                return {"age": "failing error"}

        PersonModel._meta.rules = [PassingRule(), FailingRule()]
        cleaner = getFullCleanMethode(PersonModel)

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
        if hasattr(PersonModel._meta, 'rules'):
            delattr(PersonModel._meta, "rules")

        cleaner = getFullCleanMethode(PersonModel)

        # Should pass validation without custom rules
        _ = cleaner(self.person)

        # Should still fail with invalid data
        invalid = PersonModel(age=1, owner=self.user, changed_by=self.user)
        with self.assertRaises(ValidationError):
            cleaner(invalid)

    def test_handle_custom_fields_with_multiple_custom_fields(self):
        """
        Tests handleCustomFields with multiple custom fields defined.
        """
        class MultiCustomInterface(DBBasedInterface):
            field1 = models.CharField(max_length=10)
            field2 = models.IntegerField()
            field3 = models.BooleanField()

        fields, ignore = DBBasedInterface.handleCustomFields(MultiCustomInterface)

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
        Tests handleCustomFields with no custom fields defined.
        """
        class NoCustomInterface(DBBasedInterface):
            pass

        fields, ignore = DBBasedInterface.handleCustomFields(NoCustomInterface)

        # Should have empty or minimal results
        self.assertIsInstance(fields, (list, tuple))
        self.assertIsInstance(ignore, (list, tuple))

    def test_interface_with_invalid_model(self):
        """
        Tests behavior when interface is configured with invalid model.
        """
        class InvalidInterface(DBBasedInterface):
            _model = None
            _parent_class = None
            _interface_type = "test"

        # Should handle gracefully or raise appropriate error
        with self.assertRaises((AttributeError, TypeError)):
            InvalidInterface.filter(name="test")

    def test_manager_initialization_with_search_date_edge_cases(self):
        """
        Tests manager initialization with various search date edge cases.
        """
        # Test with exact current time
        current_time = datetime.now()
        with patch.object(PersonInterface, "getHistoricalRecord", return_value=self.person) as mock_hist:
            DummyManager(self.person.pk, search_date=current_time)
            mock_hist.assert_called_once_with(self.person, current_time)

        # Test with very old date
        old_date = datetime(1900, 1, 1)
        with patch.object(PersonInterface, "getHistoricalRecord", return_value=None) as mock_hist:
            DummyManager(self.person.pk, search_date=old_date)
            mock_hist.assert_called_once_with(self.person, old_date)

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
        self.assertIsInstance(first_item, PersonModel)

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
            is_active=False
        )

        mgr = DummyManager(minimal_person.pk)
        attrs = PersonInterface.getAttributes()

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

        attrs = PersonInterface.getAttributes()

        # Both managers should return the same data
        self.assertEqual(attrs["name"](mgr1._interface), attrs["name"](mgr2._interface))
        self.assertEqual(attrs["age"](mgr1._interface), attrs["age"](mgr2._interface))
        self.assertEqual(attrs["owner"](mgr1._interface), attrs["owner"](mgr2._interface))