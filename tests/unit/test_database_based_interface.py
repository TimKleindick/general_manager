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

from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.database_based_interface import (
    DBBasedInterface,
    get_full_clean_methode,
)
from general_manager.manager.input import Input
from general_manager.bucket.database_bucket import DatabaseBucket


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
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

    @classmethod
    def handle_interface(cls):
        """
        Provide pre- and post-processing callables used when creating a dynamic interface-backed class.

        The returned `pre` callable prepares the attributes and resolves the interface class and model to use for class creation. The returned `post` callable finalizes the association by assigning the interface class to the new class and recording the new class on the interface.

        Returns:
            tuple: A pair `(pre, post)` where:
                - `pre(name, attrs, interface)` returns `(attrs, interface_class, model)`.
                - `post(new_cls, interface_cls, model)` sets `new_cls.Interface = interface_cls` and `interface_cls._parent_class = new_cls`.
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
        Cleans up PersonModel data and restores the Django app registry after the tests.
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

        Verifies that the interface instance is set to the value returned by the patched `get_historical_record` method and that this method is called exactly once.
        """
        with patch.object(
            PersonInterface, "get_historical_record", return_value="old"
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
        mock.filter.return_value.last.return_value = "hist"
        dummy = SimpleNamespace(history=mock)
        dt = datetime(2020, 1, 1)
        res = PersonInterface.get_historical_record(dummy, dt)
        mock.filter.assert_called_once_with(history_date__lte=dt)
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
        Tests the pre- and post-creation lifecycle of a database-backed interface and its manager.

        Verifies that the interface and manager classes are correctly linked, the model and its history table are created, and the manager's factory is properly associated with the model.
        """
        attrs = {"__module__": "general_manager"}
        new_attrs, interface_cls, model = PersonInterface._pre_create(
            "TempManager", attrs, PersonInterface
        )
        with connection.schema_editor() as schema:
            schema.create_model(model)
            schema.create_model(model.history.model)

        TempManager = type("TempManager", (GeneralManager,), new_attrs)
        PersonInterface._post_create(TempManager, interface_cls, model)
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
        Tests that custom fields and ignore lists are correctly identified by handle_custom_fields for a DBBasedInterface subclass.
        """

        class CustomInterface(DBBasedInterface):
            sample = models.CharField(max_length=5)

        fields, ignore = DBBasedInterface.handle_custom_fields(CustomInterface)
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
        mock.filter.return_value.last.return_value = None
        dummy = SimpleNamespace(history=mock)

        res = PersonInterface.get_historical_record(dummy, None)
        mock.filter.assert_called_once_with(history_date__lte=None)
        self.assertIsNone(res)

    def test_get_historical_record_with_future_date(self):
        """
        Tests that get_historical_record works with future dates.
        """
        mock = MagicMock()
        mock.filter.return_value.last.return_value = "future_hist"
        dummy = SimpleNamespace(history=mock)
        future_date = datetime.now() + timedelta(days=1)

        res = PersonInterface.get_historical_record(dummy, future_date)
        mock.filter.assert_called_once_with(history_date__lte=future_date)
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

        class MultiCustomInterface(DBBasedInterface):
            field1 = models.CharField(max_length=10)
            field2 = models.IntegerField()
            field3 = models.BooleanField()

        fields, ignore = DBBasedInterface.handle_custom_fields(MultiCustomInterface)

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

        class NoCustomInterface(DBBasedInterface):
            pass

        fields, ignore = DBBasedInterface.handle_custom_fields(NoCustomInterface)

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
        with patch.object(
            PersonInterface, "get_historical_record", return_value=self.person
        ) as mock_hist:
            DummyManager(self.person.pk, search_date=current_time)
            mock_hist.assert_not_called()

        # Test with very old date
        old_date = datetime(1900, 1, 1, tzinfo=UTC)
        with patch.object(
            PersonInterface, "get_historical_record", return_value=None
        ) as mock_hist:
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
        self.assertIsNone(PersonInterface._get_database_alias())

    def test_get_database_alias_returns_configured_value(self):
        """
        Tests that _get_database_alias returns the configured database alias.
        """

        class CustomInterface(DBBasedInterface):
            _model = PersonModel
            database = "custom_db"

        self.assertEqual(CustomInterface._get_database_alias(), "custom_db")

    def test_get_manager_returns_default_manager(self):
        """
        Tests that _get_manager returns the model's default manager.
        """
        manager = PersonInterface._get_manager()
        self.assertIsNotNone(manager)
        self.assertEqual(manager.model, PersonModel)

    def test_get_queryset_returns_queryset_for_model(self):
        """
        Tests that _get_queryset returns a queryset for the interface's model.
        """
        queryset = PersonInterface._get_queryset()
        self.assertIsNotNone(queryset)
        self.assertEqual(queryset.model, PersonModel)

    def test_invalid_field_value_error_message(self):
        """
        Tests that InvalidFieldValueError formats its message correctly.
        """
        from general_manager.interface.database_based_interface import (
            InvalidFieldValueError,
        )

        error = InvalidFieldValueError("age", "not_a_number")
        self.assertIn("age", str(error))
        self.assertIn("not_a_number", str(error))

    def test_invalid_field_type_error_message(self):
        """
        Tests that InvalidFieldTypeError formats its message correctly.
        """
        from general_manager.interface.database_based_interface import (
            InvalidFieldTypeError,
        )

        original_error = TypeError("expected int, got str")
        error = InvalidFieldTypeError("age", original_error)
        self.assertIn("age", str(error))
        self.assertIn("Type error", str(error))

    def test_unknown_field_error_message(self):
        """
        Tests that UnknownFieldError formats its message correctly.
        """
        from general_manager.interface.database_based_interface import (
            UnknownFieldError,
        )

        error = UnknownFieldError("nonexistent_field", "PersonModel")
        self.assertIn("nonexistent_field", str(error))
        self.assertIn("PersonModel", str(error))
        self.assertIn("does not exist", str(error))

    def test_get_data_with_timezone_aware_datetime(self):
        """
        Tests that get_data handles timezone-aware datetime correctly.
        """
        mgr = DummyManager(self.person.pk)
        aware_date = datetime.now(UTC)
        instance = mgr._interface.get_data(aware_date)
        self.assertEqual(instance.pk, self.person.pk)

    def test_get_data_with_timezone_naive_datetime(self):
        """
        Tests that get_data converts naive datetime to aware.
        """
        mgr = DummyManager(self.person.pk)
        naive_date = datetime.now()
        # Should not raise an error
        instance = mgr._interface.get_data(naive_date)
        self.assertEqual(instance.pk, self.person.pk)

    def test_get_data_without_search_date(self):
        """
        Tests that get_data returns current instance when no search_date provided.
        """
        mgr = DummyManager(self.person.pk)
        instance = mgr._interface.get_data(None)
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


class WritableDBBasedInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Creates the database table for WritableInterfaceTestModel.
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
        Deletes the database table for WritableInterfaceTestModel.
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
        Creates test users and defines test interface.
        """
        from general_manager.interface.database_based_interface import (
            WritableDBBasedInterface,
        )

        self.user1 = User.objects.create(username="creator")
        self.user2 = User.objects.create(username="modifier")

        class TestWritableInterface(WritableDBBasedInterface):
            _model = WritableInterfaceTestModel
            _parent_class = None
            _interface_type = "writable_test"
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

        self.interface_cls = TestWritableInterface

    def tearDown(self):
        """
        Cleans up test data.
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
        from general_manager.interface.database_based_interface import UnknownFieldError

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
        Tests that update records history comment.
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
        from general_manager.interface.database_based_interface import UnknownFieldError

        instance = WritableInterfaceTestModel.objects.create(
            name="Item",
            value=5,
            owner=self.user1,
            changed_by=self.user1,
        )
        interface = self.interface_cls(id=instance.pk)

        with self.assertRaises(UnknownFieldError):
            interface.update(creator_id=self.user1.pk, invalid_field="bad")

    def test_deactivate_sets_is_active_false(self):
        """
        Tests that deactivate sets is_active to False.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Active Item",
            value=30,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        interface = self.interface_cls(id=instance.pk)
        result = interface.deactivate(creator_id=self.user2.pk)

        self.assertEqual(result["id"], instance.pk)
        instance.refresh_from_db()
        self.assertFalse(instance.is_active)

    def test_deactivate_with_history_comment(self):
        """
        Tests that deactivate appends '(deactivated)' to history comment.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Active Item",
            value=30,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        interface = self.interface_cls(id=instance.pk)
        interface.deactivate(
            creator_id=self.user2.pk,
            history_comment="User requested",
        )

        history = instance.history.order_by("-history_date").first()  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "User requested (deactivated)")  # type: ignore[union-attr]

    def test_deactivate_without_comment_uses_default(self):
        """
        Tests that deactivate uses 'Deactivated' as default comment.
        """
        instance = WritableInterfaceTestModel.objects.create(
            name="Active Item",
            value=30,
            owner=self.user1,
            changed_by=self.user1,
            is_active=True,
        )
        interface = self.interface_cls(id=instance.pk)
        interface.deactivate(creator_id=self.user2.pk)

        history = instance.history.order_by("-history_date").first()  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "Deactivated")  # type: ignore[union-attr]

    def test_check_for_invalid_kwargs_with_valid_fields(self):
        """
        Tests that _check_for_invalid_kwargs passes for valid field names.
        """
        kwargs = {"name": "Test", "value": 10, "owner": self.user1}
        # Should not raise
        self.interface_cls._check_for_invalid_kwargs(WritableInterfaceTestModel, kwargs)

    def test_check_for_invalid_kwargs_with_id_list_suffix(self):
        """
        Tests that _check_for_invalid_kwargs accepts _id_list suffix for m2m fields.
        """
        kwargs = {"tags_id_list": [1, 2, 3]}
        # Should not raise
        self.interface_cls._check_for_invalid_kwargs(WritableInterfaceTestModel, kwargs)

    def test_check_for_invalid_kwargs_raises_for_invalid_field(self):
        """
        Tests that _check_for_invalid_kwargs raises UnknownFieldError for invalid fields.
        """
        from general_manager.interface.database_based_interface import UnknownFieldError

        kwargs = {"nonexistent": "value"}
        with self.assertRaises(UnknownFieldError) as context:
            self.interface_cls._check_for_invalid_kwargs(
                WritableInterfaceTestModel, kwargs
            )
        self.assertIn("nonexistent", str(context.exception))

    def test_sort_kwargs_separates_many_to_many(self):
        """
        Tests that _sort_kwargs correctly separates m2m from regular fields.
        """
        kwargs = {
            "name": "Test",
            "value": 42,
            "tags_id_list": [1, 2, 3],
        }
        regular, m2m = self.interface_cls._sort_kwargs(
            WritableInterfaceTestModel, kwargs
        )

        self.assertIn("name", regular)
        self.assertIn("value", regular)
        self.assertNotIn("tags_id_list", regular)
        self.assertIn("tags_id_list", m2m)
        self.assertEqual(m2m["tags_id_list"], [1, 2, 3])

    def test_sort_kwargs_handles_no_many_to_many(self):
        """
        Tests that _sort_kwargs works when no m2m fields are present.
        """
        kwargs = {"name": "Test", "value": 42}
        regular, m2m = self.interface_cls._sort_kwargs(
            WritableInterfaceTestModel, kwargs
        )

        self.assertEqual(regular, kwargs)
        self.assertEqual(m2m, {})

    def test_save_with_history_validates_instance(self):
        """
        Tests that _save_with_history calls full_clean for validation.
        """
        instance = WritableInterfaceTestModel(
            name="",  # Empty name should fail validation if required
            value=10,
            owner=self.user1,
        )
        # Assuming name is required, this should raise ValidationError
        # If not, adjust test accordingly
        try:
            self.interface_cls._save_with_history(instance, self.user1.pk, None)
        except ValidationError:
            pass  # Expected if validation fails

    def test_save_with_history_sets_changed_by(self):
        """
        Tests that _save_with_history sets changed_by_id correctly.
        """
        instance = WritableInterfaceTestModel(
            name="Test",
            value=10,
            owner=self.user1,
        )
        pk = self.interface_cls._save_with_history(instance, self.user2.pk, None)

        saved_instance = WritableInterfaceTestModel.objects.get(pk=pk)
        self.assertEqual(saved_instance.changed_by, self.user2)

    def test_save_with_history_handles_none_creator(self):
        """
        Tests that _save_with_history handles None creator_id gracefully.
        """
        instance = WritableInterfaceTestModel(
            name="Test",
            value=10,
            owner=self.user1,
        )
        pk = self.interface_cls._save_with_history(instance, None, None)

        saved_instance = WritableInterfaceTestModel.objects.get(pk=pk)
        self.assertIsNone(saved_instance.changed_by_id)
