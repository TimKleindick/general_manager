from django.test import SimpleTestCase
from unittest.mock import patch

from general_manager.cache.modelDependencyCollector import ModelDependencyCollector


class FakeGM:
    def __init__(self, identification):
        self.identification = identification
        self.child: FakeGM


class FakeBucket:
    def __init__(self, manager_class, filters, excludes):
        self._manager_class = manager_class
        self.filters = filters
        self.excludes = excludes


@patch("general_manager.cache.modelDependencyCollector.GeneralManager", new=FakeGM)
@patch("general_manager.cache.modelDependencyCollector.Bucket", new=FakeBucket)
class TestModelDependencyCollector(SimpleTestCase):
    def test_collect_general_manager(self):
        gm = FakeGM("id123")
        deps = list(ModelDependencyCollector.collect(gm))
        self.assertEqual(deps, [(gm.__class__.__name__, "identification", "id123")])

    def test_collect_bucket(self):
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
        gm = FakeGM("root")
        other = "no-dep"
        deps_set = set()
        ModelDependencyCollector.addArgs(deps_set, (), {"gm": gm, "val": other})
        # kwargs contain gm -> should include its identification
        expected = {("FakeGM", "identification", "root")}
        self.assertEqual(deps_set, expected)
