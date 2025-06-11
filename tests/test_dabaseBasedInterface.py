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
        def pre(name, attrs, interface):
            return attrs, cls, cls._model

        def post(new_cls, interface_cls, model):
            new_cls.Interface = interface_cls
            interface_cls._parent_class = new_cls

        return pre, post


class DummyManager(GeneralManager):
    Interface = PersonInterface


PersonInterface._parent_class = DummyManager


class DBBasedInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema:
            schema.create_model(PersonModel)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            schema.delete_model(PersonModel)
        super().tearDownClass()

    def tearDown(self):
        PersonModel.objects.all().delete()
        User.objects.all().delete()

    def setUp(self):
        self.user = User.objects.create(username="tester")
        self.person = PersonModel.objects.create(
            name="Alice",
            age=30,
            owner=self.user,
            changed_by=self.user,
        )
        self.person.tags.add(self.user)

    def test_get_data_and_initialization(self):
        mgr = DummyManager(self.person.pk)
        self.assertEqual(mgr._interface._instance.pk, self.person.pk)

    def test_get_data_with_history_date(self):
        with patch.object(
            PersonInterface, "getHistoricalRecord", return_value="old"
        ) as mock_hist:
            mgr = DummyManager(
                self.person.pk, search_date=datetime.now() - timedelta(minutes=1)
            )
            self.assertEqual(mgr._interface._instance, "old")
            mock_hist.assert_called_once()

    def test_filter_and_exclude(self):
        bucket = PersonInterface.filter(name="Alice")
        self.assertIsInstance(bucket, DatabaseBucket)
        self.assertEqual(bucket.count(), 1)
        excluded = PersonInterface.exclude(name="Alice")
        self.assertEqual(excluded.count(), 0)

    def test_get_historical_record(self):
        mock = MagicMock()
        mock.filter.return_value.last.return_value = "hist"
        dummy = SimpleNamespace(history=mock)
        dt = datetime(2020, 1, 1)
        res = PersonInterface.getHistoricalRecord(dummy, dt)
        mock.filter.assert_called_once_with(history_date__lte=dt)
        self.assertEqual(res, "hist")

    def test_get_attribute_types_and_field_type(self):
        types = PersonInterface.getAttributeTypes()
        self.assertEqual(types["name"]["type"], str)
        self.assertTrue(types["name"]["is_required"])
        self.assertEqual(types["tags_list"]["type"], User)
        self.assertTrue(types["tags_list"]["is_editable"])
        self.assertFalse(types["tags_list"]["is_required"])
        self.assertIs(PersonInterface.getFieldType("name"), models.CharField)

    def test_get_attributes_values(self):
        mgr = DummyManager(self.person.pk)
        attrs = PersonInterface.getAttributes()
        self.assertEqual(attrs["name"](mgr._interface), "Alice")
        self.assertEqual(attrs["age"](mgr._interface), 30)
        self.assertEqual(attrs["is_active"](mgr._interface), True)
        self.assertEqual(attrs["owner"](mgr._interface), self.user)
        self.assertEqual(attrs["changed_by"](mgr._interface), self.user)
        self.assertEqual(list(attrs["tags_list"](mgr._interface)), [self.user])

    def test_pre_and_post_create_and_handle_interface(self):
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
        class CustomInterface(DBBasedInterface):
            sample = models.CharField(max_length=5)

        fields, ignore = DBBasedInterface.handleCustomFields(CustomInterface)
        self.assertIn(None, fields)
        self.assertIn("None_value", ignore)
        self.assertIn("None_unit", ignore)
