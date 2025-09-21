# Test framework: pytest
# These tests validate GraphQLProperty and graphQlProperty (focus on PR diff content).
# They import the implementation from tests/unit/test_property.py via importlib to avoid
# module path ambiguity and to keep tests self-contained.


from __future__ import annotations

import sys
import types
import importlib.util
from pathlib import Path
import contextlib
import pytest


def _load_impl_module():
    """
    Dynamically load the implementation module from the sibling file
    tests/unit/test_property.py to avoid relying on package import paths.
    """
    mod_path = Path(__file__).parent / "test_property.py"
    assert mod_path.exists(), f"Implementation module not found at {mod_path}"
    spec = importlib.util.spec_from_file_location("graphql_property_impl", mod_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def impl():
    # Load once per module to keep a single class/type identity for assertions
    return _load_impl_module()


def _install_fake_cache(monkeypatch):
    """
    Provide a fake general_manager.cache.cacheDecorator.cached implementation
    so graphQlProperty can import and wrap fget with a recognizable wrapper.

    The fake cached() returns a decorator that:
      - attaches attributes _is_cached_wrapper = True
      - records invocations in _cached_calls
      - preserves __name__ and __doc__ of the wrapped function
    """
    gm = types.ModuleType("general_manager")
    cache_pkg = types.ModuleType("general_manager.cache")
    cache_deco_mod = types.ModuleType("general_manager.cache.cacheDecorator")

    def cached():
        def decorator(fn):
            calls = []

            def wrapper(*args, **kwargs):
                calls.append((args, kwargs))
                return fn(*args, **kwargs)

            wrapper._is_cached_wrapper = True
            wrapper._cached_calls = calls
            wrapper.__wrapped__ = fn
            with contextlib.suppress(AttributeError, TypeError):
                wrapper.__name__ = getattr(fn, "__name__", "wrapped")
                wrapper.__doc__ = getattr(fn, "__doc__", None)
            return wrapper
        return decorator

    cache_deco_mod.cached = cached
    cache_pkg.cacheDecorator = cache_deco_mod
    gm.cache = cache_pkg

    monkeypatch.setitem(sys.modules, "general_manager", gm)
    monkeypatch.setitem(sys.modules, "general_manager.cache", cache_pkg)
    monkeypatch.setitem(sys.modules, "general_manager.cache.cacheDecorator", cache_deco_mod)
    return cached


# ---------- GraphQLProperty direct construction tests ----------


def test_requires_return_type_hint_raises_typeerror(impl):
    # No return annotation -> should raise
    def no_hint(_self):
        return 1

    with pytest.raises(TypeError) as ei:
        impl.GraphQLProperty(no_hint)
    assert "requires a return type hint" in str(ei.value)


def test_initialization_sets_flags_and_type_hint(impl):
    def with_hint(_self) -> int:
        return 42

    prop = impl.GraphQLProperty(
        with_hint,
        sortable=True,
        filterable=True,
        query_annotation={"anno": "v"},
    )
    # Base property behavior
    assert isinstance(prop, property)
    # Custom attributes
    assert getattr(prop, "is_graphql_resolver", False) is True
    assert prop.graphql_type_hint is int
    assert prop.sortable is True
    assert prop.filterable is True
    assert prop.query_annotation == {"anno": "v"}


def test_explicit_docstring_passthrough(impl):
    def fn(_self) -> str:
        "original function doc"
        return "ok"

    prop = impl.GraphQLProperty(fn, doc="explicit doc")
    assert prop.__doc__ == "explicit doc"


# ---------- graphQlProperty decorator usage (bare) ----------


def test_decorator_bare_wraps_and_resolves_value(monkeypatch, impl):
    _install_fake_cache(monkeypatch)

    class Thing:
        def __init__(self, base: int):
            self.base = base

        @impl.graphQlProperty
        def value(self) -> int:
            return self.base * 2

    # Class-level descriptor should be GraphQLProperty
    assert isinstance(Thing.value, impl.GraphQLProperty)
    # Cached wrapper should be present on fget
    assert getattr(Thing.value.fget, "_is_cached_wrapper", False) is True

    t = Thing(3)

    # Accessing the property should invoke the cached wrapper and compute correct result
    assert t.value == 6
    # Verify the wrapper was called once and with the instance
    calls = getattr(Thing.value.fget, "_cached_calls", [])
    assert len(calls) == 1
    assert calls[0][0][0] is t  # first positional arg is the instance


def test_decorator_bare_defaults(monkeypatch, impl):
    _install_fake_cache(monkeypatch)

    class Item:
        @impl.graphQlProperty
        def score(self) -> float:
            return 1.5

    prop = Item.score
    assert prop.sortable is False
    assert prop.filterable is False
    assert prop.query_annotation is None
    assert prop.graphql_type_hint is float


# ---------- graphQlProperty decorator with options ----------


def test_decorator_with_args_sets_flags_and_annotation(monkeypatch, impl):
    _install_fake_cache(monkeypatch)

    sentinel_annotation = ("join", "users")

    class User:
        @impl.graphQlProperty(sortable=True, filterable=True, query_annotation=sentinel_annotation)
        def rating(self) -> float:
            return 4.25

    # Descriptor type and attributes
    assert isinstance(User.rating, impl.GraphQLProperty)
    assert User.rating.sortable is True
    assert User.rating.filterable is True
    assert User.rating.query_annotation == sentinel_annotation
    assert User.rating.graphql_type_hint is float

    # Behavior
    u = User()
    assert u.rating == 4.25
    assert getattr(User.rating.fget, "_is_cached_wrapper", False) is True


# ---------- Negative/edge case around decorator type hints ----------


def test_decorator_raises_without_return_annotation(monkeypatch, impl):
    _install_fake_cache(monkeypatch)

    # Decorating a function without a return hint should raise at decoration time
    with pytest.raises(TypeError):
        class Bad:
            @impl.graphQlProperty
            def bad(self):  # no return type hint
                return "x"


# ---------- Sanity: GraphQLProperty acts as standard descriptor ----------


def test_descriptor_invocation_path(impl):
    def get_name(self) -> str:
        return self._name

    class Person:
        name = impl.GraphQLProperty(get_name, sortable=True)

        def __init__(self, name: str):
            self._name = name

    assert isinstance(Person.name, property)
    p = Person("Ada")
    assert p.name == "Ada"
    assert Person.name.sortable is True
    assert Person.name.filterable is False
    assert Person.name.query_annotation is None