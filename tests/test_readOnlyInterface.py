# type: ignore
from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase
from django.core.checks import Warning
from django.db import connection
from unittest import mock

from general_manager.interface.readOnlyInterface import (
    ReadOnlyInterface,
    ReadOnlyModel,
)

from django.db import models


# ------------------------------------------------------------
# Hilfsklassen für die Tests
# ------------------------------------------------------------
class FakeInstance:
    def __init__(self, **kwargs):
        # initialisiere alle übergebenen Attribute
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.is_active = True
        self.saved = False

    def save(self):
        self.saved = True


class FakeManager:
    def __init__(self):
        self._instances: list[FakeInstance] = []

    def get_or_create(self, **lookup):
        # Suche nach bestehendem Objekt
        for inst in self._instances:
            if all(getattr(inst, k) == v for k, v in lookup.items()):
                return inst, False
        # neu anlegen
        inst = FakeInstance(**lookup)
        self._instances.append(inst)
        return inst, True

    def filter(self, **kwargs):
        # nur aktive Instanzen
        return [inst for inst in self._instances if inst.is_active]


class DummyModel:
    # simuliertes Django-Modell
    objects = FakeManager()

    class _meta:
        db_table = "dummy_table"
        # für getUniqueFields irrelevant, wir patchen direkt


class DummyManager:
    # simuliert den GeneralManager
    _data = None


class DummyInterface(ReadOnlyInterface):
    _model = DummyModel
    _parent_class = DummyManager


# ------------------------------------------------------------
# Tests für getUniqueFields
# ------------------------------------------------------------
class GetUniqueFieldsTests(SimpleTestCase):
    def test_field_unique_true_and_together_and_constraint(self):
        # Erzeuge eine Fake-Meta mit lokalen Feldern, unique_together und UniqueConstraint
        Field = SimpleNamespace  # mit .name, .unique, .column
        fake_meta = SimpleNamespace(
            local_fields=[
                Field(name="id", unique=True, column="id"),
                Field(name="email", unique=True, column="email"),
                Field(name="username", unique=False, column="username"),
            ],
            unique_together=[("username", "other")],
            constraints=[
                mock.Mock(
                    __class__=type("C", (), {"__instancecheck__": lambda s, x: False}),
                    fields=["other_field"],
                ),
                # echtes UniqueConstraint
                mock.Mock(
                    __class__=models.UniqueConstraint,
                    fields=["extra"],
                ),
            ],
        )

        # patchen
        class M:
            _meta = fake_meta

        result = ReadOnlyInterface.getUniqueFields(M)
        # id wird ignoriert, email (unique), username (über unique_together),
        # other (unique_together), other_field (constraint), extra (UniqueConstraint)
        self.assertSetEqual(result, {"email", "username", "other", "extra"})


# ------------------------------------------------------------
# Tests für ensureSchemaIsUpToDate
# ------------------------------------------------------------
class EnsureSchemaTests(TestCase):
    def setUp(self):
        # stub introspection
        self.orig_table_names = connection.introspection.table_names
        self.orig_get_desc = connection.introspection.get_table_description

    def tearDown(self):
        connection.introspection.table_names = self.orig_table_names
        connection.introspection.get_table_description = self.orig_get_desc

    def test_table_not_exists(self):
        # table_names liefert leer
        connection.introspection.table_names = lambda cursor: []
        warnings = ReadOnlyInterface.ensureSchemaIsUpToDate(DummyManager, DummyModel)
        self.assertEqual(len(warnings), 1)
        self.assertIsInstance(warnings[0], Warning)
        self.assertIn("does not exist", warnings[0].hint)

    def test_schema_up_to_date(self):
        # table_names enthält unseren Tabellennamen
        connection.introspection.table_names = lambda cursor: [
            DummyModel._meta.db_table
        ]
        # description liefert genau die Spalten, die model._meta.local_fields vorgibt
        fake_desc = [SimpleNamespace(name="col1"), SimpleNamespace(name="col2")]
        connection.introspection.get_table_description = lambda cursor, table: fake_desc

        # FakeModel mit passenden local_fields
        class M:
            class _meta:
                db_table = DummyModel._meta.db_table
                local_fields = [
                    SimpleNamespace(column="col1"),
                    SimpleNamespace(column="col2"),
                ]

        warnings = ReadOnlyInterface.ensureSchemaIsUpToDate(DummyManager, M)
        self.assertEqual(warnings, [])


# ------------------------------------------------------------
# Tests für syncData
# ------------------------------------------------------------
class SyncDataTests(SimpleTestCase):
    def setUp(self):
        # leere Manager-Instanzen
        DummyModel.objects = FakeManager()
        DummyManager._data = None
        # stub transaction.atomic
        self.atomic_cm = mock.MagicMock()
        self.atomic_patch = mock.patch(
            "general_manager.interface.readOnlyInterface.transaction.atomic",
            return_value=mock.MagicMock(
                __enter__=lambda s: None, __exit__=lambda *a: None
            ),
        )
        self.atomic_patch.start()
        # stub getUniqueFields auf {'name'}
        self.gu_patch = mock.patch.object(
            ReadOnlyInterface, "getUniqueFields", return_value={"name"}
        )
        self.gu_patch.start()
        # stub ensureSchemaIsUpToDate immer leer
        self.es_patch = mock.patch.object(
            ReadOnlyInterface, "ensureSchemaIsUpToDate", return_value=[]
        )
        self.es_patch.start()
        # log-capture
        self.log_patcher = mock.patch(
            "general_manager.interface.readOnlyInterface.logger"
        )
        self.logger = self.log_patcher.start()

    def tearDown(self):
        self.atomic_patch.stop()
        self.gu_patch.stop()
        self.es_patch.stop()
        self.log_patcher.stop()

    def test_missing_data_raises(self):
        DummyManager._data = None
        with self.assertRaises(ValueError) as cm:
            DummyInterface.syncData()
        self.assertIn("must set '_data'", str(cm.exception))

    def test_invalid_data_type_raises(self):
        DummyManager._data = 123  # weder str noch list
        with self.assertRaises(ValueError) as cm:
            DummyInterface.syncData()
        self.assertIn("_data must be a JSON string or a list", str(cm.exception))

    def test_no_unique_fields_raises(self):
        # stop getUniqueFields und liefere leere Menge
        self.gu_patch.stop()
        with mock.patch.object(
            ReadOnlyInterface, "getUniqueFields", return_value=set()
        ):
            DummyManager._data = []
            with self.assertRaises(ValueError) as cm:
                DummyInterface.syncData()
            self.assertIn("must have at least one unique field", str(cm.exception))

    def test_ensure_schema_not_up_to_date_logs_and_exits(self):
        # ersetze ensureSchemaIsUpToDate durch Warnung
        self.es_patch.stop()
        with mock.patch.object(
            ReadOnlyInterface,
            "ensureSchemaIsUpToDate",
            return_value=[Warning("x", "y", obj=None)],
        ):
            DummyManager._data = "[]"
            DummyInterface.syncData()
            self.logger.warning.assert_called_once()
            # keine weiteren Aufrufe an save()
            self.assertEqual(DummyModel.objects._instances, [])

    def test_sync_creates_updates_and_deactivates(self):
        # Setup: schon ein Eintrag a vorhanden
        DummyModel.objects._instances = [FakeInstance(name="a", other=1)]
        # neue JSON-Daten: a verändert, b neu
        DummyManager._data = [{"name": "a", "other": 2}, {"name": "b", "other": 3}]
        # führe syncData aus
        DummyInterface.syncData()
        # prüfe a wurde updated
        inst_a = next(i for i in DummyModel.objects._instances if i.name == "a")
        self.assertEqual(inst_a.other, 2)
        self.assertTrue(inst_a.saved)
        # prüfe b wurde erstellt
        inst_b = next(i for i in DummyModel.objects._instances if i.name == "b")
        self.assertEqual(inst_b.other, 3)
        self.assertTrue(inst_b.saved)
        # prüfe Log-Info enthält 1 created, 1 updated, 0 deactivated
        self.logger.info.assert_called_once()
        msg = self.logger.info.call_args[0][0]
        self.assertIn("Created: 1", msg)
        self.assertIn("Updated: 1", msg)
        self.assertIn("Deactivated: 0", msg)


# ------------------------------------------------------------
# Tests für Decorators und handleInterface
# ------------------------------------------------------------
class DecoratorTests(SimpleTestCase):
    def test_readOnlyPostCreate_appends_class(self):
        # reset Liste
        from general_manager.manager.meta import GeneralManagerMeta

        GeneralManagerMeta.read_only_classes = []

        # Dummy-Funktion
        @ReadOnlyInterface.readOnlyPostCreate
        def post_hook(new_cls, interface_cls, model):
            # setze eine Marke
            new_cls._hook_called = True

        class C:
            pass

        post_hook(C, ReadOnlyInterface, DummyModel)
        self.assertTrue(hasattr(C, "_hook_called"))
        self.assertIn(C, GeneralManagerMeta.read_only_classes)

    def test_readOnlyPreCreate_delegates_and_sets_base_model(self):
        def pre_hook(name, attrs, interface, base_model_class=None):
            return (name, attrs, interface, base_model_class)

        wrapper = ReadOnlyInterface.readOnlyPreCreate(pre_hook)
        result = wrapper("MyName", {"a": 1}, "iface")
        # der letzte Parameter muss ReadOnlyModel sein
        self.assertEqual(result, ("MyName", {"a": 1}, "iface", ReadOnlyModel))

    def test_handleInterface_returns_two_callables(self):
        pre, post = ReadOnlyInterface.handleInterface()
        self.assertTrue(callable(pre))
        self.assertTrue(callable(post))
