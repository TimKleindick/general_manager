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
        self.identification = {"id": pk}

    @classmethod
    def create(cls, *args, **kwargs):
        raise NotImplementedError

    def update(self, *args, **kwargs):
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        raise NotImplementedError

    def getData(self, search_date=None):
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls) -> dict[str, dict]:  # type: ignore
        return {}

    @classmethod
    def getAttributes(cls) -> dict[str, dict]:
        return {}

    @classmethod
    def filter(cls, **kwargs):  # type: ignore
        return DatabaseBucket(User.objects.filter(**kwargs), UserManager)

    @classmethod
    def exclude(cls, **kwargs):  # type: ignore
        return []

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        return str

    @classmethod
    def handleInterface(cls):
        """
        Returns two functions:
        - preCreation: modifies attrs before the class is created (adds 'marker').
        - postCreation: sets a flag on the newly created class.
        """

        def preCreation(name, attrs, interface):
            attrs["marker"] = "initialized_by_dummy"
            return attrs, cls, None

        def postCreation(new_cls, interface_cls, model):
            new_cls.post_mark = True

        return preCreation, postCreation


class UserManager(GeneralManager):
    """
    Simple GeneralManager subclass for wrapping User PKs.
    """

    def __init__(self, pk):
        super().__init__(pk)


class AnotherManager(GeneralManager):
    """
    Another GeneralManager subclass to test type mismatches.
    """

    def __init__(self, pk):
        super().__init__(pk)


class DatabaseBucketTestCase(TestCase):
    def setUp(self):
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
        mgr = self.bucket.get(username="bob")
        self.assertIsInstance(mgr, UserManager)
        self.assertEqual(mgr.identification["id"], self.u2.id)
        # get non-existing should raise
        with self.assertRaises(User.DoesNotExist):
            self.bucket.get(username="doesnotexist")

    def test_getitem(self):
        # index
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
        all_bucket = self.bucket.all()
        self.assertIsInstance(all_bucket, DatabaseBucket)
        self.assertEqual(len(all_bucket), 3)

    def test_filter_and_exclude(self):
        # filter
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
        b1 = self.bucket.filter(username="alice")
        b2 = self.bucket.filter(username="carol")
        union = b1 | b2
        self.assertIsInstance(union, DatabaseBucket)
        self.assertEqual(len(union), 2)
        ids = sorted([mgr.identification["id"] for mgr in union])
        self.assertListEqual(ids, sorted([self.u1.id, self.u3.id]))

    def test_or_with_manager(self):
        b1 = self.bucket.filter(username="alice")
        mgr_bob = UserManager(self.u2.id)
        union = b1 | mgr_bob
        self.assertEqual(len(union), 2)
        ids = sorted([mgr.identification["id"] for mgr in union])
        self.assertListEqual(ids, sorted([self.u1.id, self.u2.id]))

    def test_or_errors(self):
        # incompatible type
        with self.assertRaises(ValueError):
            _ = self.bucket | 123
        # different manager class
        b_other = DatabaseBucket(User.objects.all(), AnotherManager)
        with self.assertRaises(ValueError):
            _ = self.bucket | b_other

    def test_repr(self):
        r = repr(self.bucket)
        self.assertTrue("UserManagerBucket" in r)
        self.assertTrue("QuerySet" in r)

    def test_contains(self):
        # model instance
        self.assertIn(self.u1, self.bucket)
        # manager instance
        mgr2 = UserManager(self.u2.id)
        self.assertIn(mgr2, self.bucket)
        # not in
        fake = User(id=999)
        self.assertNotIn(fake, self.bucket)

    def test_sort(self):
        # default ordering by username asc
        sorted_bucket = self.bucket.sort("username")
        names = [mgr.identification["id"] for mgr in sorted_bucket]
        # ensure same members
        self.assertEqual(
            sorted([u.id for u in [self.u1, self.u2, self.u3]]), sorted(names)
        )
        # reverse ordering
        rev = self.bucket.sort("username", reverse=True)
        # highest username first
        self.assertEqual(rev.first().identification["id"], self.u3.id)
