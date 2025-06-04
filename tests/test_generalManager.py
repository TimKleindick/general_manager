from django.test import TestCase
from general_manager.manager.generalManager import GeneralManager
from unittest.mock import patch
from general_manager.cache.signals import post_data_change, pre_data_change


class DummyInterface:
    def __init__(self, *args, **kwargs):
        self.__dict__ = kwargs

    @classmethod
    def filter(cls, *args, **kwargs):
        return []

    @classmethod
    def exclude(cls, *args, **kwargs):
        return []

    @classmethod
    def create(cls, *args, **kwargs):
        return {"id": "dummy_id"}

    def update(self, *args, **kwargs):
        return {"id": "dummy_id"}

    def deactivate(self, *args, **kwargs):
        return {"id": "dummy_id"}

    @property
    def identification(self):
        return {"id": "dummy_id"}


class GeneralManagerTestCase(TestCase):
    def setUp(self):
        # Set up any necessary data or state before each test
        self.manager = GeneralManager
        self.manager._attributes = {
            "name": "Test Manager",
            "version": "1.0",
            "active": True,
            "id": "dummy_id",
        }
        self.manager.Interface = DummyInterface  # type: ignore

        self.post_list = []

        def temp_post_receiver(sender, **kwargs):
            self.post_list.append(kwargs)

        self.pre_list = []

        def temp_pre_receiver(sender, **kwargs):
            self.pre_list.append(kwargs)

        self.post_data_change = temp_post_receiver
        self.pre_data_change = temp_pre_receiver

        post_data_change.connect(self.post_data_change)
        pre_data_change.connect(self.pre_data_change)

    def tearDown(self):
        # Clean up after each test
        post_data_change.disconnect(self.post_data_change)
        pre_data_change.disconnect(self.pre_data_change)

    @patch("general_manager.cache.cacheTracker.DependencyTracker.track")
    def test_initialization(self, mock_track):
        # Test if the manager initializes correctly
        manager = self.manager()
        mock_track.assert_called_once_with(
            "GeneralManager", "identification", "{'id': 'dummy_id'}"
        )
        self.assertIsInstance(manager, GeneralManager)

    def test_str_and_repr(self):
        # Test string representation
        manager = self.manager()
        self.assertEqual(str(manager), "GeneralManager(**{'id': 'dummy_id'})")
        self.assertEqual(repr(manager), "GeneralManager(**{'id': 'dummy_id'})")

    def test_reduce(self):
        # Test the __reduce__ method
        manager = self.manager()
        reduced = manager.__reduce__()
        self.assertEqual(reduced, (self.manager, ("dummy_id",)))

    def test_or_operator(self):
        # Test the __or__ operator
        manager1 = self.manager()
        manager2 = self.manager()
        result = manager1 | manager2
        with patch.object(
            self.manager, "filter", return_value=[manager1, manager2]
        ) as mock_filter:
            result = manager1 | manager2
            mock_filter.assert_called_once_with(
                id__in=[{"id": "dummy_id"}, {"id": "dummy_id"}]
            )
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].identification, {"id": "dummy_id"})  # type: ignore
        self.assertEqual(
            result[1].identification, {"id": "dummy_id"}  # type: ignore
        )  # Assuming both managers have the same id

    def test_identification_property(self):
        # Test the identification property
        manager = self.manager()
        self.assertEqual(manager.identification, {"id": "dummy_id"})

    def test_iter(self):
        # Test the __iter__ method
        manager = self.manager()
        attributes = dict(manager)
        self.assertIn("name", attributes)
        self.assertIn("version", attributes)
        self.assertIn("active", attributes)
        self.assertEqual(attributes["name"], "Test Manager")
        self.assertEqual(attributes["version"], "1.0")
        self.assertTrue(attributes["active"])
        self.assertEqual(attributes["id"], "dummy_id")

    def test_classmethod_filter(self):
        # Test the filter class method
        with patch.object(DummyInterface, "filter", return_value=[]) as mock_filter:
            result = self.manager.filter(id__in=["dummy_id", 123])
            mock_filter.assert_called_once_with(id__in=["dummy_id", 123])
            self.assertEqual(result, [])

    def test_classmethod_exclude(self):
        # Test the exclude class method
        with patch.object(DummyInterface, "exclude", return_value=[]) as mock_filter:
            result = self.manager.exclude(id__in=("dummy_id", 123))
            mock_filter.assert_called_once_with(id__in=("dummy_id", 123))
            self.assertEqual(result, [])

    def test_classmethod_create(self):
        # Test the create class method
        with (
            patch.object(
                DummyInterface, "create", return_value={"id": "new_id"}
            ) as mock_create,
        ):
            new_manager = self.manager.create(creator_id=1, name="New Manager")
            mock_create.assert_called_once_with(
                creator_id=1, history_comment=None, name="New Manager"
            )
            self.assertIsInstance(new_manager, GeneralManager)
            self.assertEqual(len(self.pre_list), 1)
            self.assertEqual(self.pre_list[0]["action"], "create")
            self.assertEqual(self.pre_list[0]["instance"], None)

            self.assertEqual(len(self.post_list), 1)
            self.assertEqual(self.post_list[0]["action"], "create")
            self.assertEqual(self.post_list[0]["name"], "New Manager")

    def test_classmethod_update(self):
        # Test the update class method
        manager_obj = self.manager()
        with (
            patch.object(
                DummyInterface, "update", return_value={"id": "new_id"}
            ) as mock_create,
        ):
            new_manager = manager_obj.update(creator_id=1, name="New Manager")
            mock_create.assert_called_once_with(
                creator_id=1, history_comment=None, name="New Manager"
            )
            self.assertIsInstance(new_manager, GeneralManager)
            self.assertEqual(len(self.pre_list), 1)
            self.assertEqual(self.pre_list[0]["action"], "update")
            self.assertEqual(self.pre_list[0]["instance"], manager_obj)

            self.assertEqual(len(self.post_list), 1)
            self.assertEqual(self.post_list[0]["action"], "update")
            self.assertEqual(self.post_list[0]["name"], "New Manager")

    def test_classmethod_deactivate(self):
        # Test the deactivate class method
        manager_obj = self.manager()
        with (
            patch.object(
                DummyInterface, "deactivate", return_value={"id": "new_id"}
            ) as mock_create,
        ):
            new_manager = manager_obj.deactivate(creator_id=1)
            mock_create.assert_called_once_with(creator_id=1, history_comment=None)
            self.assertIsInstance(new_manager, GeneralManager)
            self.assertEqual(len(self.pre_list), 1)
            self.assertEqual(self.pre_list[0]["action"], "deactivate")
            self.assertEqual(self.pre_list[0]["instance"], manager_obj)

            self.assertEqual(len(self.post_list), 1)
            self.assertEqual(self.post_list[0]["action"], "deactivate")
