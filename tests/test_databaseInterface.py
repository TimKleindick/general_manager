# type: ignore
from typing import Any
from unittest.mock import patch
from django.contrib.auth.models import User
from django.db import models, connection
from django.test import TransactionTestCase

from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.input import Input


class SafeDict(dict):
    def items(self):
        return list(super().items())


class DatabaseInterfaceTestCase(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        class UserInterface(DatabaseInterface):
            _model = User
            _parent_class = None
            input_fields = {"id": Input(int)}

            @classmethod
            def handleInterface(cls):
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
            input_fields = {"id": Input(int)}

            @classmethod
            def handleInterface(cls):
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

        with connection.schema_editor() as schema:
            schema.create_model(BookModel)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            schema.delete_model(cls.BookModel)
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
        self.BookInterface._checkForInvalidKwargs(
            self.BookModel, {"title": "a", "readers_id_list": []}
        )
        with self.assertRaises(ValueError):
            self.BookInterface._checkForInvalidKwargs(self.BookModel, {"wrong": 1})

    def test_sort_kwargs(self):
        kwargs = SafeDict({"title": "b", "readers_id_list": [1]})
        normal, m2m = self.BookInterface._sortKwargs(self.BookModel, kwargs)
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
            "general_manager.interface.databaseInterface.update_change_reason"
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
            )
            self.assertEqual(pk, 99)
            self.assertEqual(captured["instance"].title, "Created")
            self.assertEqual(captured["comment"], "new")

            mgr = self.BookManager(self.book.pk)
            pk2 = mgr._interface.update(
                creator_id=self.user.pk,
                history_comment="up",
                title="Updated",
            )

            self.assertEqual(pk2, self.book.pk)
            self.assertEqual(captured["instance"].title, "Updated")
            self.assertEqual(captured["comment"], "up")

            pk2 = mgr._interface.update(
                creator_id=self.user.pk,
                readers_id_list=[self.user.pk],
            )
            pk3 = mgr._interface.deactivate(
                creator_id=self.user.pk, history_comment="reason"
            )
            self.assertEqual(pk3, self.book.pk)
            self.assertFalse(captured["instance"].is_active)
            self.assertEqual(captured["comment"], "reason (deactivated)")
