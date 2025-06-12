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
