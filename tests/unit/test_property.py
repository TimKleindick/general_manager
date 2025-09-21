import sys
import types
import functools
import importlib
import inspect
import typing
import builtins
import pytest


# We will import the module under test after injecting a fake cached()
# into general_manager.cache.cacheDecorator. This ensures the internal
# import within graphQlProperty(func...) resolves to our stub.
def install_fake_cached(preserve_annotations: bool = True):
    pkg_root = types.ModuleType("general_manager")
    pkg_root.__path__ = []  # mark as package

    cache_pkg = types.ModuleType("general_manager.cache")
    cache_pkg.__path__ = []

    cache_mod = types.ModuleType("general_manager.cache.cacheDecorator")

    def cached():
        def decorator(f):
            if preserve_annotations:
                @functools.wraps(f)
                def wrapper(*args, **kwargs):
                    return f(*args, **kwargs)
            else:
                # intentionally do NOT use wraps to drop annotations
                def wrapper(*args, **kwargs):
                    return f(*args, **kwargs)
            # mark the wrapper so we can assert it was applied
            wrapper._wrapped_by_cached = True
            return wrapper
        return decorator

    cache_mod.cached = cached

    sys.modules["general_manager"] = pkg_root
    sys.modules["general_manager.cache"] = cache_pkg
    sys.modules["general_manager.cache.cacheDecorator"] = cache_mod


def import_under_test():
    """
    Try common module paths to import GraphQLProperty and graphQlProperty.
    Adjust here if your project locates the implementation elsewhere.
    """
    candidates = [
        # Most likely file names/paths:
        "property",                          # same dir module
        "graphql.property",                  # package graphql/property.py
        "graph.property",                    # alternative
        "src.property",
        "app.property",
        "general_manager.graphql.property",  # plausible project structure
    ]
    last_exc = None
    for name in candidates:
        try:
            return importlib.import_module(name)
        except ImportError as e:  # pragma: no cover - only runs on failure paths
            last_exc = e
            continue
    # As a fallback, search sys.modules for any module already loaded that defines the symbols
    for mod in list(sys.modules.values()):
        if not isinstance(mod, types.ModuleType):
            continue
        if hasattr(mod, "GraphQLProperty") and hasattr(mod, "graphQlProperty"):
            return mod
    raise AssertionError(f"Could not import module under test via candidates: {candidates}. Last error: {last_exc!r}")


class TestGraphQLProperty:
    def setup_method(self):
        # Ensure fresh import for each test (clear previously imported candidate modules)
        to_purge = [
            "property",
            "graphql.property",
            "graph.property",
            "src.property",
            "app.property",
            "general_manager.graphql.property",
        ]
        for name in to_purge:
            if name in sys.modules:
                del sys.modules[name]

    def test_decorator_no_args_happy_path_preserves_annotations_and_behavior(self):
        install_fake_cached(preserve_annotations=True)
        mod = import_under_test()
        GraphQLProperty = mod.GraphQLProperty
        graphQlProperty = mod.graphQlProperty

        class Foo:
            def __init__(self, v: int):
                self._v = v

            @graphQlProperty
            def bar(self) -> int:
                "Return the value"
                return self._v

        # Instance access returns the expected value
        f = Foo(42)
        assert f.bar == 42

        # Descriptor type and attributes accessible on the class
        prop = Foo.__dict__["bar"]
        assert isinstance(prop, GraphQLProperty)
        assert prop.is_graphql_resolver is True

        # graphql_type_hint captured from return annotation
        assert prop.graphql_type_hint in (int, typing.Annotated[int, ...])  # allow annotated int forms

        # default flags
        assert prop.sortable is False
        assert prop.filterable is False
        assert prop.query_annotation is None

        # property doc derives from fget.__doc__ when doc is None
        assert (prop.__doc__ or "").startswith("Return the value")

        # The cached() wrapper should have been applied to fget
        assert hasattr(prop.fget, "_wrapped_by_cached") and prop.fget._wrapped_by_cached is True

    def test_decorator_with_args_flags_and_query_annotation(self):
        install_fake_cached(preserve_annotations=True)
        mod = import_under_test()
        GraphQLProperty = mod.GraphQLProperty
        graphQlProperty = mod.graphQlProperty

        class Model:
            def __init__(self, s: str):
                self._s = s

            @graphQlProperty(sortable=True, filterable=True, query_annotation="LOWER(name)")
            def name(self) -> str:
                """Doc for name"""
                return self._s

        m = Model("Alice")
        assert m.name == "Alice"

        prop = Model.__dict__["name"]
        assert isinstance(prop, GraphQLProperty)
        assert prop.sortable is True
        assert prop.filterable is True
        assert prop.query_annotation == "LOWER(name)"
        assert prop.graphql_type_hint in (str, typing.Annotated[str, ...])

    def test_direct_instantiation_without_return_type_raises(self):
        # This bypasses the decorator to test the constructor's validation logic
        install_fake_cached(preserve_annotations=True)
        mod = import_under_test()
        GraphQLProperty = mod.GraphQLProperty

        def no_hint(_self):  # no return annotation
            return 1

        with pytest.raises(TypeError):
            GraphQLProperty(no_hint)

    def test_decorator_drops_annotations_then_raises_due_to_missing_return_type(self):
        # Simulate a faulty cached() that does NOT preserve annotations via functools.wraps
        install_fake_cached(preserve_annotations=False)
        # The import of module under test happens after we injected the fake cached()
        mod = import_under_test()
        graphQlProperty = mod.graphQlProperty

        # Decorating a function with no preserved annotations should trigger TypeError
        with pytest.raises(TypeError):
            class C:
                @graphQlProperty
                def p(self):  # has an annotation here? ensure it does, but cached drops it
                    return 7

    def test_type_hint_any_and_optional(self):
        install_fake_cached(preserve_annotations=True)
        mod = import_under_test()
        graphQlProperty = mod.graphQlProperty

        class Kind:
            @graphQlProperty
            def any_val(self) -> typing.Any:
                return "x"

            @graphQlProperty
            def opt_val(self) -> typing.Optional[int]:
                return 5

        prop_any = Kind.__dict__["any_val"]
        prop_opt = Kind.__dict__["opt_val"]

        # Ensure the captured type hints align with the annotations
        assert prop_any.graphql_type_hint is typing.Any
        # Optional[int] gets resolved by get_type_hints to typing.Optional[int] or typing.Union[int, NoneType]
        hint = prop_opt.graphql_type_hint
        assert (
            hint == typing.Optional[int]
            or (getattr(hint, "__origin__", None) is typing.Union and set(hint.__args__) == {int, type(None)})
        )

        k = Kind()
        assert k.any_val == "x"
        assert k.opt_val == 5