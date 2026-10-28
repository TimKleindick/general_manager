"""Historical property wrapping retains Python's ordinary MRO choices."""

import asyncio
from datetime import UTC, datetime

from django.test import override_settings
import pytest

from general_manager.api.property import GraphQLProperty
from general_manager.as_of import HistoricalReadNotSupportedError, current_as_of_date
from general_manager.manager.meta import GeneralManagerMeta


@override_settings(AUTOCREATE_GRAPHQL=False)
def test_synthetic_inherited_properties_do_not_shadow_a_later_mixin_override():
    class RootMixin:
        @property
        def label(self):
            return "root"

    class OverrideMixin(RootMixin):
        @property
        def label(self):
            return "override"

    class Base(RootMixin, metaclass=GeneralManagerMeta):
        pass

    class Child(Base, OverrideMixin):
        pass

    class ExplicitChild(Base, OverrideMixin):
        @property
        def label(self):
            return "explicit"

    class ConstantOverride(RootMixin):
        label = "constant"

    class ConstantChild(Base, ConstantOverride):
        pass

    class LaterOverride(ConstantOverride):
        @property
        def label(self):
            return "later"

    class GrandChild(ConstantChild, LaterOverride):
        pass

    assert Base().label == "root"
    assert Child().label == "override"
    assert ExplicitChild().label == "explicit"
    constant = ConstantChild()
    assert constant.label == "constant"
    constant.label = "assigned"
    assert constant.label == "assigned"
    assert GrandChild().label == "later"
    assert Base.__getattribute__ is object.__getattribute__
    assert ExplicitChild.__getattribute__ is object.__getattribute__


@override_settings(AUTOCREATE_GRAPHQL=False)
def test_custom_property_keeps_identity_state_and_descriptor_protocol():
    """Historical reads retain the original custom descriptor and write hooks."""
    calls = []
    snapshot = datetime(2026, 10, 20, tzinfo=UTC)

    class CustomProperty(property):
        __slots__ = ("declared_as", "marker")

        def __init__(self, getter, marker):
            super().__init__(getter, doc="custom docs")
            self.marker = marker

        def __set_name__(self, owner, name):
            self.declared_as = (owner, name)

        def __get__(self, instance, owner=None):
            """Record the descriptor's actual evaluation context."""
            if instance is None:
                return self
            calls.append(("get", current_as_of_date(), self.marker))
            return super().__get__(instance, owner)

        def __set__(self, instance, value):
            calls.append(("set", value))

        def __delete__(self, instance):
            calls.append(("delete",))

    marker = object()
    descriptor = CustomProperty(lambda _instance: current_as_of_date(), marker)

    class Mixin:
        value = descriptor

    class Manager(Mixin, metaclass=GeneralManagerMeta):
        pass

    manager = Manager()
    manager._effective_search_date = snapshot
    assert Manager.value is descriptor
    assert descriptor.declared_as == (Mixin, "value")
    assert descriptor.marker is marker
    assert manager.value == snapshot
    assert calls == [("get", snapshot, marker)]
    manager.value = "assigned"
    del manager.value
    assert calls[1:] == [("set", "assigned"), ("delete",)]
    assert current_as_of_date() is None


@override_settings(AUTOCREATE_GRAPHQL=False)
def test_custom_property_respects_descendant_mro_and_getattribute_override():
    """Custom lookups and property/constant shadowing keep Python's MRO."""
    snapshot = datetime(2026, 10, 20, tzinfo=UTC)

    class CustomProperty(property):
        pass

    class Root:
        @CustomProperty
        def value(self):
            return current_as_of_date()

    class Base(Root, metaclass=GeneralManagerMeta):
        pass

    class Override(Root):
        value = "constant"

    class ConstantChild(Base, Override):
        pass

    class CustomLookup(Base):
        def __getattribute__(self, name):
            """Exercise an explicit lookup override that cooperates with super."""
            if name == "other":
                return "lookup"
            return super().__getattribute__(name)

    class Descendant(CustomLookup):
        pass

    for cls in (Base, CustomLookup, Descendant):
        instance = cls()
        instance._effective_search_date = snapshot
        assert instance.value == snapshot
    assert CustomLookup().other == "lookup"
    constant = ConstantChild()
    assert constant.value == "constant"
    constant.value = "assigned"
    assert constant.value == "assigned"


@override_settings(AUTOCREATE_GRAPHQL=False)
def test_injected_lookup_does_not_shadow_later_mixin_lookup():
    """Synthetic base hooks cannot hide the actual mixin lookup winner."""

    class CustomProperty(property):
        pass

    class Root:
        @CustomProperty
        def value(self):
            return "root"

    class Base(Root, metaclass=GeneralManagerMeta):
        pass

    class LookupMixin(Root):
        def __getattribute__(self, name):
            """Provide a later MRO lookup behind a synthetic base hook."""
            if name == "other":
                return "mixin"
            return super().__getattribute__(name)

    class Child(Base, LookupMixin):
        pass

    class ConstantMixin(LookupMixin):
        value = "constant"

    class ConstantChild(Base, ConstantMixin):
        pass

    assert Child().other == "mixin"
    assert ConstantChild().other == "mixin"
    assert ConstantChild().value == "constant"


@override_settings(AUTOCREATE_GRAPHQL=False)
def test_custom_graphql_property_and_descendant_property_use_full_lookup_context():
    """Include subclass descriptor work and newly declared descendant names."""
    snapshot = datetime(2026, 10, 20, tzinfo=UTC)

    class CustomGraphQLProperty(GraphQLProperty):
        def __get__(self, instance, owner=None):
            """Observe the context after GraphQLProperty's own getter returns."""
            if instance is None:
                return self
            return super().__get__(instance, owner), current_as_of_date()

    class Base(metaclass=GeneralManagerMeta):
        @CustomGraphQLProperty
        def value(self) -> datetime | None:
            return current_as_of_date()

    class CustomProperty(property):
        pass

    class Child(Base):
        @CustomProperty
        def added(self):
            return current_as_of_date()

    manager = Child()
    manager._effective_search_date = snapshot
    assert manager.value == (snapshot, snapshot)
    assert manager.added == snapshot
    assert current_as_of_date() is None


@override_settings(AUTOCREATE_GRAPHQL=False)
def test_custom_descriptor_can_materialize_generator_and_bind_awaitable():
    """Judge actual descriptor results while keeping deferred reads scoped."""
    snapshot = datetime(2026, 10, 20, tzinfo=UTC)

    class MaterializedProperty(property):
        def __get__(self, instance, owner=None):
            """Materialize a generator inside the descriptor's own protocol."""
            if instance is None:
                return self
            return list(super().__get__(instance, owner))

    class CustomProperty(property):
        pass

    class Manager(metaclass=GeneralManagerMeta):
        @MaterializedProperty
        def materialized(self):
            yield current_as_of_date()

        @CustomProperty
        def raw_iterator(self):
            yield current_as_of_date()

        @CustomProperty
        async def deferred(self):
            return current_as_of_date()

    manager = Manager()
    manager._effective_search_date = snapshot
    assert manager.materialized == [snapshot]
    assert asyncio.run(manager.deferred) == snapshot
    with pytest.raises(HistoricalReadNotSupportedError):
        _ = manager.raw_iterator
    assert current_as_of_date() is None
