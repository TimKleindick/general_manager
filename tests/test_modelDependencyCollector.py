from django.test import SimpleTestCase
from unittest.mock import patch

from general_manager.cache.modelDependencyCollector import ModelDependencyCollector


class FakeGM:
    def __init__(self, identification):
        """
        Initializes a FakeGM instance with the given identification.
        
        Args:
            identification: A value representing the identification of this FakeGM instance.
        """
        self.identification = identification
        self.child: FakeGM


class FakeBucket:
    def __init__(self, manager_class, filters, excludes):
        """
        Initializes a FakeBucket instance with a manager class, filters, and excludes.
        
        Args:
            manager_class: The class representing the manager associated with this bucket.
            filters: A dictionary of filter criteria.
            excludes: A dictionary of exclusion criteria.
        """
        self._manager_class = manager_class
        self.filters = filters
        self.excludes = excludes


@patch("general_manager.cache.modelDependencyCollector.GeneralManager", new=FakeGM)
@patch("general_manager.cache.modelDependencyCollector.Bucket", new=FakeBucket)
class TestModelDependencyCollector(SimpleTestCase):
    def test_collect_general_manager(self):
        """
        Tests that ModelDependencyCollector.collect extracts the identification from a FakeGM instance.
        
        Asserts that the collected dependencies include a tuple with the class name, the string "identification", and the identification value.
        """
        gm = FakeGM("id123")
        deps = list(ModelDependencyCollector.collect(gm))
        self.assertEqual(deps, [(gm.__class__.__name__, "identification", "id123")])

    def test_collect_bucket(self):
        """
        Tests that ModelDependencyCollector.collect correctly extracts filter and exclude
        dependencies from a FakeBucket instance using the manager class name.
        """
        class Mgr:
            pass

        bucket = FakeBucket(Mgr, {"a": 1}, {"b": 2})
        deps = set(ModelDependencyCollector.collect(bucket))
        expected = {
            (Mgr.__name__, "filter", "{'a': 1}"),
            (Mgr.__name__, "exclude", "{'b': 2}"),
        }
        self.assertEqual(deps, expected)

    def test_collect_nested_structures(self):
        """
        Tests that ModelDependencyCollector.collect can extract dependencies from nested
        data structures containing FakeGM and FakeBucket instances.
        
        Verifies that dependencies are correctly identified within dictionaries, lists,
        and tuples, and that the expected dependency tuples are collected.
        """
        gm = FakeGM("root")

        class Mgr2:
            pass

        bucket = FakeBucket(Mgr2, {"x": 10}, {})
        # nested container with dict, list, tuple
        nested = {"one": [gm, {"inner": bucket}], "two": (gm,)}
        deps = set(ModelDependencyCollector.collect(nested))
        expected = {
            ("FakeGM", "identification", "root"),
            (Mgr2.__name__, "filter", "{'x': 10}"),
            (Mgr2.__name__, "exclude", "{}"),
        }
        self.assertEqual(deps, expected)

    def test_addArgs_collects_args_and_nested_attributes(self):
        # GM with nested attribute child (another GM)
        """
        Tests that addArgs collects dependencies from positional arguments and their nested attributes.
        
        Verifies that when a FakeGM instance with a nested child FakeGM is passed as an argument, addArgs recursively collects identification dependencies from both the root and child instances.
        """
        gm = FakeGM("root")
        child = FakeGM("child")
        gm.child = child

        deps_set = set()
        # first arg is gm, second is ignored, no kwargs
        ModelDependencyCollector.addArgs(deps_set, (gm, 42), {})
        expected = {
            ("FakeGM", "identification", "child"),
            ("FakeGM", "identification", "root"),
        }
        self.assertEqual(deps_set, expected)

    def test_addArgs_includes_kwargs(self):
        """
        Tests that addArgs collects dependencies from keyword arguments containing dependency objects.
        
        Verifies that when a dependency object is passed in kwargs, its identifying information is added to the dependencies set.
        """
        gm = FakeGM("root")
        other = "no-dep"
        deps_set = set()
        ModelDependencyCollector.addArgs(deps_set, (), {"gm": gm, "val": other})
        # kwargs contain gm -> should include its identification
        expected = {("FakeGM", "identification", "root")}
        self.assertEqual(deps_set, expected)
