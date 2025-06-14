from django.test import TestCase
from general_manager.manager.generalManager import GeneralManager
from unittest.mock import patch
from general_manager.cache.signals import post_data_change, pre_data_change


class DummyInterface:
    def __init__(self, *args, **kwargs):
        """
        Initializes the DummyInterface instance by setting attributes from keyword arguments.

        Each keyword argument is assigned as an instance attribute.
        """
        self.__dict__ = kwargs

    @classmethod
    def filter(cls, *args, **kwargs):
        """
        Returns an empty list, simulating a filter operation for testing purposes.
        """
        return []

    @classmethod
    def exclude(cls, *args, **kwargs):
        """
        Returns an empty list to simulate exclusion of items in the mock interface.
        """
        return []

    @classmethod
    def create(cls, *args, **kwargs):
        """
        Creates a new dummy record and returns a dictionary with a fixed ID.

        Returns:
            dict: A dictionary with the key "id" set to "dummy_id".
        """
        return {"id": "dummy_id"}

    def update(self, *args, **kwargs):
        """
        Simulates an update operation and returns a fixed identification dictionary.

        Returns:
            dict: A dictionary with a dummy ID.
        """
        return {"id": "dummy_id"}

    def deactivate(self, *args, **kwargs):
        """
        Simulates deactivating an object and returns a dummy identification dictionary.
        """
        return {"id": "dummy_id"}

    @property
    def identification(self):
        """
        Returns a fixed identification dictionary with a dummy ID.
        """
        return {"id": "dummy_id"}


class GeneralManagerTestCase(TestCase):
    def setUp(self):
        # Set up any necessary data or state before each test
        """
        Prepares the test environment before each test.

        Initializes the GeneralManager with test attributes, assigns a dummy interface, and connects temporary receivers to pre- and post-data change signals to capture emitted events.
        """
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
            """
            Appends keyword arguments received from a signal to the post_list attribute.
            """
            self.post_list.append(kwargs)

        self.pre_list = []

        def temp_pre_receiver(sender, **kwargs):
            """
            Temporary signal receiver that appends received keyword arguments to the pre_list.
            """
            self.pre_list.append(kwargs)

        self.post_data_change = temp_post_receiver
        self.pre_data_change = temp_pre_receiver

        post_data_change.connect(self.post_data_change)
        pre_data_change.connect(self.pre_data_change)

    def tearDown(self):
        # Clean up after each test
        """
        Disconnects temporary signal receivers after each test to clean up the test environment.
        """
        post_data_change.disconnect(self.post_data_change)
        pre_data_change.disconnect(self.pre_data_change)

    @patch("general_manager.cache.cacheTracker.DependencyTracker.track")
    def test_initialization(self, mock_track):
        # Test if the manager initializes correctly
        """
        Tests initialization of GeneralManager and dependency tracking.

        Asserts that DependencyTracker.track is called with the correct arguments and that the created object is an instance of GeneralManager.
        """
        manager = self.manager()
        mock_track.assert_called_once_with(
            "GeneralManager", "identification", "{'id': 'dummy_id'}"
        )
        self.assertIsInstance(manager, GeneralManager)

    def test_str_and_repr(self):
        # Test string representation
        """
        Tests that the string and representation methods of GeneralManager return the expected format.
        """
        manager = self.manager()
        self.assertEqual(str(manager), "GeneralManager(**{'id': 'dummy_id'})")
        self.assertEqual(repr(manager), "GeneralManager(**{'id': 'dummy_id'})")

    def test_reduce(self):
        # Test the __reduce__ method
        """
        Tests that the __reduce__ method returns the correct tuple for object serialization.
        """
        manager = self.manager()
        reduced = manager.__reduce__()
        self.assertEqual(reduced, (self.manager, ("dummy_id",)))

    def test_or_operator(self):
        # Test the __or__ operator
        """
        Tests that the bitwise OR operator combines two GeneralManager instances by invoking
        the filter method with their IDs and returns a list of manager instances with correct identifications.
        """
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
        """
        Tests that the identification property of GeneralManager returns the expected dummy ID dictionary.
        """
        manager = self.manager()
        self.assertEqual(manager.identification, {"id": "dummy_id"})

    def test_iter(self):
        # Test the __iter__ method
        """
        Tests that iterating over a GeneralManager instance yields its attributes as key-value pairs.

        Asserts that the resulting dictionary includes the expected keys and correct values for 'name', 'version', 'active', and 'id'.
        """
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
        """
        Tests that the GeneralManager.filter class method delegates to the interface's filter method with the correct arguments and returns its result.
        """
        with patch.object(DummyInterface, "filter", return_value=[]) as mock_filter:
            result = self.manager.filter(id__in=["dummy_id", 123])
            mock_filter.assert_called_once_with(id__in=["dummy_id", 123])
            self.assertEqual(result, [])

    def test_classmethod_exclude(self):
        # Test the exclude class method
        """
        Tests that the GeneralManager.exclude class method delegates to the interface's exclude method with the correct arguments and returns the expected result.
        """
        with patch.object(DummyInterface, "exclude", return_value=[]) as mock_filter:
            result = self.manager.exclude(id__in=("dummy_id", 123))
            mock_filter.assert_called_once_with(id__in=("dummy_id", 123))
            self.assertEqual(result, [])

    def test_classmethod_create(self):
        # Test the create class method
        """
        Tests that the GeneralManager.create class method calls the interface's create method with correct arguments, returns a GeneralManager instance, and emits pre- and post-data change signals with expected payloads.
        """
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
        """
        Tests that the update method of GeneralManager calls the interface's update method
        with correct arguments, returns a GeneralManager instance, and emits pre- and
        post-data change signals with expected payloads.
        """
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
        """
        Tests that the deactivate method calls the interface's deactivate, returns a GeneralManager instance, and emits pre- and post-data change signals with the correct action and instance.
        """
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
