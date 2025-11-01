# type: ignore
from typing import Any, ClassVar
from unittest.mock import patch
from django.contrib.auth.models import User
from django.db import models, connection
from django.test import TransactionTestCase
from django.apps import apps

from general_manager.interface.database_interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class SafeDict(dict):
    def items(self):
        return list(super().items())


class DatabaseInterfaceTestCase(TransactionTestCase):
    _created_tables: ClassVar[set[str]] = set()

    @classmethod
    def setUpClass(cls):
        """
        Prepare test fixtures for DatabaseInterface tests by defining in-test manager and interface classes and creating the BookModel table.

        Defines UserInterface and its UserManager, BookInterface and its BookManager, and a BookModel Django model attached to the test case. Creates the database schema for BookModel so tests can run against the model.
        """
        super().setUpClass()

        class UserInterface(DatabaseInterface):
            _model = User
            _parent_class = None
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

            @classmethod
            def handle_interface(cls):
                """
                Provide pre/post handler callables used when attaching an interface to a class.

                Parameters:
                    cls (type): The class that will act as the parent/owner of the interface.

                Returns:
                    tuple: A pair `(pre, post)` where:
                        - `pre(name, attrs, interface)` returns a three-tuple `(attrs, parent_class, model)` to be used when preparing the interface class.
                        - `post(new_cls, interface_cls, model)` is called after the interface class is created; it assigns the interface to the new class and sets the interface's `_parent_class` to that new class.
                """

                def pre(name, attrs, interface):
                    return attrs, cls, cls._model

                def post(new_cls, interface_cls, model):
                    new_cls.Interface = interface_cls
                    interface_cls._parent_class = new_cls

                return pre, post

        class UserManager(GeneralManager):
            Interface = UserInterface

        cls.UserManager = UserManager
        UserInterface._parent_class = UserManager

        class BookModel(models.Model):
            title = models.CharField(max_length=50)
            author = models.ForeignKey(User, on_delete=models.CASCADE)
            readers = models.ManyToManyField(User, blank=True)
            is_active = models.BooleanField(default=True)
            changed_by = models.ForeignKey(User, on_delete=models.PROTECT)

            class Meta:
                app_label = "general_manager"

        cls.BookModel = BookModel

        class BookInterface(DatabaseInterface):
            _model = BookModel
            _parent_class = None
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

            @classmethod
            def handle_interface(cls):
                """
                Provide pre/post handler callables used when attaching an interface to a class.

                Parameters:
                    cls (type): The class that will act as the parent/owner of the interface.

                Returns:
                    tuple: A pair `(pre, post)` where:
                        - `pre(name, attrs, interface)` returns a three-tuple `(attrs, parent_class, model)` to be used when preparing the interface class.
                        - `post(new_cls, interface_cls, model)` is called after the interface class is created; it assigns the interface to the new class and sets the interface's `_parent_class` to that new class.
                """

                def pre(name, attrs, interface):
                    return attrs, cls, cls._model

                def post(new_cls, interface_cls, model):
                    new_cls.Interface = interface_cls
                    interface_cls._parent_class = new_cls

                return pre, post

        cls.BookInterface = BookInterface

        class BookManager(GeneralManager):
            Interface = BookInterface

        cls.BookManager = BookManager
        BookInterface._parent_class = BookManager

        cls._created_tables = set()
        app_config = apps.get_app_config("general_manager")
        for model in (cls.BookModel, cls.BookModel.readers.through):
            model_key = model._meta.model_name
            if model_key not in app_config.models:
                apps.register_model("general_manager", model)

        before_tables = set(connection.introspection.table_names())
        if cls.BookModel._meta.db_table not in before_tables:
            with connection.schema_editor() as schema:
                schema.create_model(cls.BookModel)
        after_tables = set(connection.introspection.table_names())
        cls._created_tables.update(after_tables - before_tables)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            if cls.BookModel._meta.db_table in cls._created_tables:
                schema.delete_model(cls.BookModel)

        app_config = apps.get_app_config("general_manager")
        for model in (cls.BookModel, cls.BookModel.readers.through):
            model_key = model._meta.model_name
            app_config.models.pop(model_key, None)
            apps.all_models["general_manager"].pop(model_key, None)

        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create(username="tester")
        self.book = self.BookModel.objects.create(
            title="Initial",
            author=self.user,
            changed_by=self.user,
        )
        self.book.readers.add(self.user)
        self.user_manager = self.UserManager(self.user.pk)

    def test_check_for_invalid_kwargs(self):
        self.BookInterface._check_for_invalid_kwargs(
            self.BookModel, {"title": "a", "readers_id_list": []}
        )
        with self.assertRaises(ValueError):
            self.BookInterface._check_for_invalid_kwargs(self.BookModel, {"wrong": 1})

    def test_sort_kwargs(self):
        kwargs = SafeDict({"title": "b", "readers_id_list": [1]})
        normal, m2m = self.BookInterface._sort_kwargs(self.BookModel, kwargs)
        self.assertEqual(normal, {"title": "b"})
        self.assertEqual(m2m, {"readers_id_list": [1]})

    def test_save_with_history(self):
        class Dummy:
            def __init__(self):
                self.pk = 5
                self.saved = False
                self.cleaned = False

            def full_clean(self):
                self.cleaned = True

            def save(self):
                self.saved = True

        inst = Dummy()
        with patch(
            "general_manager.interface.database_based_interface.update_change_reason"
        ) as mock_update:
            pk = self.BookInterface._save_with_history(inst, 7, "comment")
        self.assertEqual(pk, 5)
        self.assertEqual(inst.changed_by_id, 7)
        self.assertTrue(inst.cleaned)
        self.assertTrue(inst.saved)
        mock_update.assert_called_once_with(inst, "comment")

    def test_create_update_and_deactivate(self):
        captured: dict[str, Any] = {}

        def fake_save(instance, creator_id, comment):
            captured["instance"] = instance
            captured["creator"] = creator_id
            captured["comment"] = comment
            return getattr(instance, "pk", 99) or 99

        with patch.object(
            self.BookInterface,
            "_save_with_history",
            side_effect=fake_save,
        ):
            pk = self.BookInterface.create(
                creator_id=self.user.pk,
                history_comment="new",
                title="Created",
                author=self.user_manager,
            )["id"]
            self.assertEqual(pk, 99)
            self.assertEqual(captured["instance"].title, "Created")
            self.assertEqual(captured["comment"], "new")

            mgr = self.BookManager(self.book.pk)
            pk2 = mgr._interface.update(
                creator_id=self.user.pk,
                history_comment="up",
                title="Updated",
            )["id"]

            self.assertEqual(pk2, self.book.pk)
            self.assertEqual(captured["instance"].title, "Updated")
            self.assertEqual(captured["comment"], "up")

            pk2 = mgr._interface.update(
                creator_id=self.user.pk,
                readers_id_list=[self.user.pk],
            )["id"]
            pk3 = mgr._interface.deactivate(
                creator_id=self.user.pk, history_comment="reason"
            )["id"]
            self.assertEqual(pk3, self.book.pk)
            self.assertFalse(captured["instance"].is_active)
            self.assertEqual(captured["comment"], "reason (deactivated)")
