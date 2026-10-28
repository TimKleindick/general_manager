"""Historical property wrapping retains Python's ordinary MRO choices."""

from django.test import override_settings

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
