# type: ignore

from django.test import TestCase
from django.contrib.auth.models import User
from general_manager.bucket.databaseBucket import DatabaseBucket
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.baseInterface import InterfaceBase


# Dummy interface class to satisfy GeneralManager requirements
class DummyInterface(InterfaceBase):
    def __init__(self, pk):
        # Simulate identification attribute as dict with 'id'
        """
        Initializes the manager with a primary key and sets the identification attribute.
        """
        self.identification = {"id": pk}

    @classmethod
    def create(cls, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that creation is not supported for this interface.
        """
        raise NotImplementedError

    def update(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that the update operation is not supported.
        """
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that deactivation is not supported.
        """
        raise NotImplementedError

    def getData(self, search_date=None):
        """
        Raises NotImplementedError to indicate data retrieval is not implemented for this interface.
        """
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls) -> dict[str, dict]:  # type: ignore
        """
        Returns an empty dictionary representing attribute types for the class.
        """
        return {}

    @classmethod
    def getAttributes(cls) -> dict[str, dict]:
        """
        Returns an empty dictionary representing the attributes for the class.

        This method can be overridden to provide attribute definitions for the class.
        """
        return {}

    @classmethod
    def filter(cls, **kwargs):  # type: ignore
        """
        Returns a DatabaseBucket containing UserManager instances for users matching the given filter criteria.

        Args:
            **kwargs: Field lookups to filter User objects.

        Returns:
            A DatabaseBucket wrapping UserManager instances for the filtered users.
        """
        return DatabaseBucket(User.objects.filter(**kwargs), UserManager)

    @classmethod
    def exclude(cls, **kwargs):  # type: ignore
        """
        Returns an empty list, indicating no objects are excluded.

        This method is a placeholder for exclusion logic in the interface.
        """
        return []

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Returns the type associated with the specified field name.

        Always returns `str` for any field.
        """
        return str

    @classmethod
    def handleInterface(cls):
        """
        Provides pre- and post-creation hooks for class customization.

        Returns:
            A tuple of two functions:
                - preCreation: Modifies class attributes before class creation by adding a 'marker'.
                - postCreation: Sets a 'post_mark' flag on the newly created class.
        """

        def preCreation(name, attrs, interface):
            """
            Adds a marker attribute to the class attributes before creation.

            Args:
                name: The name of the class being created.
                attrs: The dictionary of class attributes to be modified.
                interface: The interface associated with the class.

            Returns:
                A tuple containing the updated attributes, the class itself, and None.
            """
            attrs["marker"] = "initialized_by_dummy"
            return attrs, cls, None

        def postCreation(new_cls, interface_cls, model):
            """
            Sets a flag on the newly created class after its creation.

            Args:
                new_cls: The newly created class instance.
                interface_cls: The interface class used for creation.
                model: The model associated with the class.
            """
            new_cls.post_mark = True

        return preCreation, postCreation


class UserManager(GeneralManager):
    """
    Simple GeneralManager subclass for wrapping User PKs.
    """

    def __init__(self, pk):
        """
        Initializes the UserManager with the given primary key.
        """
        super().__init__(pk)


class AnotherManager(GeneralManager):
    """
    Another GeneralManager subclass to test type mismatches.
    """

    def __init__(self, pk):
        """
        Initializes the UserManager with the given primary key.
        """
        super().__init__(pk)


class DatabaseBucketTestCase(TestCase):
    def setUp(self):
        """
        Sets up test data and environment for DatabaseBucket tests.

        Initializes DummyInterface for manager classes, creates test User instances, and constructs a DatabaseBucket containing all users with UserManager.
        """
        UserManager.Interface = DummyInterface  # Set the interface for UserManager
        AnotherManager.Interface = (
            DummyInterface  # Set the interface for AnotherManager
        )
        # Create some test users
        self.u1 = User.objects.create(username="alice")
        self.u2 = User.objects.create(username="bob")
        self.u3 = User.objects.create(username="carol")
        # Base bucket with all users
        self.bucket = DatabaseBucket(User.objects.all(), UserManager)

    def test_iter_and_len_and_count(self):
        # __iter__ yields UserManager instances
        """
        Tests that iterating over the bucket yields UserManager instances with correct IDs, and that length and count methods return the expected number of items.
        """
        ids = [mgr.identification["id"] for mgr in self.bucket]
        self.assertListEqual(
            ids,
            [self.u1.id, self.u2.id, self.u3.id],
        )
        # __len__ and count()
        self.assertEqual(len(self.bucket), 3)
        self.assertEqual(self.bucket.count(), 3)

    def test_first_and_last(self):
        # first() returns the first manager
        """
        Tests that the first() and last() methods of DatabaseBucket return the correct manager instances or None for empty buckets.
        """
        first_mgr = self.bucket.first()
        self.assertIsInstance(first_mgr, UserManager)
        self.assertEqual(first_mgr.identification["id"], self.u1.id)
        # last() returns the last manager
        last_mgr = self.bucket.last()
        self.assertIsInstance(last_mgr, UserManager)
        self.assertEqual(last_mgr.identification["id"], self.u3.id)
        # on empty bucket
        empty = DatabaseBucket(User.objects.none(), UserManager)
        self.assertIsNone(empty.first())
        self.assertIsNone(empty.last())

    def test_get(self):
        """
        Tests that the `get` method returns the correct manager for an existing user and raises `User.DoesNotExist` when the user does not exist.
        """
        mgr = self.bucket.get(username="bob")
        self.assertIsInstance(mgr, UserManager)
        self.assertEqual(mgr.identification["id"], self.u2.id)
        # get non-existing should raise
        with self.assertRaises(User.DoesNotExist):
            self.bucket.get(username="doesnotexist")

    def test_getitem(self):
        # index
        """
        Tests indexing and slicing behavior of the DatabaseBucket.

        Verifies that indexing returns the correct manager instance for a user and that slicing returns a DatabaseBucket containing the expected subset of users.
        """
        mgr0 = self.bucket[0]
        self.assertIsInstance(mgr0, UserManager)
        self.assertEqual(mgr0.identification["id"], self.u1.id)
        mgr2 = self.bucket[2]
        self.assertEqual(mgr2.identification["id"], self.u3.id)
        # slice
        subbucket = self.bucket[:2]
        self.assertIsInstance(subbucket, DatabaseBucket)
        self.assertEqual(len(subbucket), 2)
        ids = [mgr.identification["id"] for mgr in subbucket]
        self.assertListEqual(ids, [self.u1.id, self.u2.id])

    def test_all(self):
        """
        Tests that the all() method returns a DatabaseBucket containing all users.
        """
        all_bucket = self.bucket.all()
        self.assertIsInstance(all_bucket, DatabaseBucket)
        self.assertEqual(len(all_bucket), 3)

    def test_filter_and_exclude(self):
        # filter
        """
        Tests the filter and exclude methods of DatabaseBucket.

        Verifies that filtering returns a bucket with only matching users and merges filter definitions, while excluding removes specified users and merges exclude definitions.
        """
        alice_bucket = self.bucket.filter(username="alice")
        self.assertIsInstance(alice_bucket, DatabaseBucket)
        self.assertEqual(len(alice_bucket), 1)
        self.assertEqual(alice_bucket.first().identification["id"], self.u1.id)
        # filter definitions merged
        self.assertIn("username", alice_bucket.filters)
        self.assertListEqual(alice_bucket.filters["username"], ["alice"])
        # exclude
        no_bob = self.bucket.exclude(username="bob")
        self.assertEqual(len(no_bob), 2)
        self.assertNotIn(self.u2, no_bob._data)
        # exclude definitions merged
        self.assertIn("username", no_bob.excludes)
        self.assertListEqual(no_bob.excludes["username"], ["bob"])

    def test_or_union_with_bucket(self):
        # split buckets
        """
        Tests that the union of two DatabaseBuckets returns a new bucket containing unique manager instances from both buckets.
        """
        b1 = self.bucket.filter(username="alice")
        b2 = self.bucket.filter(username="carol")
        union = b1 | b2
        self.assertIsInstance(union, DatabaseBucket)
        self.assertEqual(len(union), 2)
        ids = sorted([mgr.identification["id"] for mgr in union])
        self.assertListEqual(ids, sorted([self.u1.id, self.u3.id]))

    def test_or_with_manager(self):
        """
        Tests that the union of a DatabaseBucket and a manager instance returns a bucket containing both items.
        """
        b1 = self.bucket.filter(username="alice")
        mgr_bob = UserManager(self.u2.id)
        union = b1 | mgr_bob
        self.assertEqual(len(union), 2)
        ids = sorted([mgr.identification["id"] for mgr in union])
        self.assertListEqual(ids, sorted([self.u1.id, self.u2.id]))

    def test_or_errors(self):
        # incompatible type
        """
        Tests that union operations with incompatible types or different manager classes raise ValueError.
        """
        with self.assertRaises(ValueError):
            _ = self.bucket | 123
        # different manager class
        b_other = DatabaseBucket(User.objects.all(), AnotherManager)
        with self.assertRaises(ValueError):
            _ = self.bucket | b_other

    def test_contains(self):
        # model instance
        """
        Tests membership checks for model and manager instances in the DatabaseBucket.

        Verifies that both model instances and their corresponding manager instances are recognized as members of the bucket, and that non-existent users are not considered members.
        """
        self.assertIn(self.u1, self.bucket)
        # manager instance
        mgr2 = UserManager(self.u2.id)
        self.assertIn(mgr2, self.bucket)
        # not in
        fake = User(id=999)
        self.assertNotIn(fake, self.bucket)

    def test_sort(self):
        # default ordering by username asc
        """
        Tests that the sort method orders the bucket by username in ascending and descending order.

        Verifies that sorting by username returns all original members in sorted order, and that reverse sorting places the user with the highest username first.
        """
        sorted_bucket = self.bucket.sort("username")
        ordered_ids = [mgr.identification["id"] for mgr in sorted_bucket]
        # ensure same members
        self.assertListEqual(
            ordered_ids,
            [self.u1.id, self.u2.id, self.u3.id],  # alice, bob, carol
        )

        # reverse ordering
        rev = self.bucket.sort("username", reverse=True)
        # highest username first
        self.assertEqual(rev.first().identification["id"], self.u3.id)
