"""GraphQL CRUD exposure is independent of interface write support."""

from typing import ClassVar

import graphene
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.interface.base_interface import InterfaceBase
from tests.utils.simple_manager_interface import BaseTestInterface


@pytest.fixture
def mutations(monkeypatch):
    registry = {}
    monkeypatch.setattr(GraphQL, "_mutations", registry)
    return registry


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        (set(), set()),
        ({"create"}, {"createExposureRecord"}),
        ({"update"}, {"updateExposureRecord"}),
        ({"delete"}, {"deleteExposureRecord"}),
        ({"create", "update"}, {"createExposureRecord", "updateExposureRecord"}),
        ({"create", "delete"}, {"createExposureRecord", "deleteExposureRecord"}),
        ({"update", "delete"}, {"updateExposureRecord", "deleteExposureRecord"}),
        (
            {"create", "update", "delete"},
            {"createExposureRecord", "updateExposureRecord", "deleteExposureRecord"},
        ),
    ],
)
@pytest.mark.parametrize("container", [set, frozenset])
def test_only_allowlisted_generated_mutations_are_registered(
    enabled, expected, container, mutations
):
    class ExposureRecord:
        Interface = BaseTestInterface
        __graphql_mutations__ = container(enabled)

    GraphQL.create_graphql_mutation(ExposureRecord)

    assert set(mutations) == expected


@pytest.mark.parametrize(
    "configuration",
    [
        None,
        False,
        "update",
        ["create"],
        ("create",),
        {"update": False},
        {"Create"},
        {"read"},
        {"create", "udpate"},
        {3},
        frozenset({"create", None}),
    ],
)
def test_invalid_exposure_fails_before_mutation_registry_changes(
    configuration, mutations
):
    class InvalidExposureRecord:
        Interface = BaseTestInterface
        __graphql_mutations__ = configuration

    mutations["existingCustomMutation"] = object()
    before = dict(mutations)

    with pytest.raises(
        TypeError, match=r"InvalidExposureRecord\.__graphql_mutations__"
    ):
        GraphQL.create_graphql_mutation(InvalidExposureRecord)

    assert mutations == before


def test_inherited_allowlist_can_be_replaced_without_changing_parent(mutations):
    class BaseRecord:
        Interface = BaseTestInterface
        __graphql_mutations__: ClassVar[set[str]] = {"create", "delete"}

    class InheritedRecord(BaseRecord):
        pass

    class OverriddenRecord(BaseRecord):
        __graphql_mutations__: ClassVar[set[str]] = {"update"}

    GraphQL.create_graphql_mutation(InheritedRecord)
    GraphQL.create_graphql_mutation(OverriddenRecord)

    assert set(mutations) == {
        "createInheritedRecord",
        "deleteInheritedRecord",
        "updateOverriddenRecord",
    }
    assert BaseRecord.__graphql_mutations__ == {"create", "delete"}


def test_allowlist_does_not_enable_unsupported_interface_operations(mutations):
    class ReadOnlyRecord:
        Interface = InterfaceBase
        __graphql_mutations__: ClassVar[set[str]] = {"create", "update", "delete"}

    GraphQL.create_graphql_mutation(ReadOnlyRecord)

    assert mutations == {}


def test_disabled_generated_mutation_preserves_same_named_custom_mutation(mutations):
    class CustomMutation(graphene.Mutation):
        success = graphene.Boolean()

        @staticmethod
        def mutate(root, info):
            return CustomMutation(success=True)

    class CustomRecord:
        Interface = BaseTestInterface
        __graphql_mutations__: ClassVar[set[str]] = set()

    mutations["updateCustomRecord"] = CustomMutation

    GraphQL.create_graphql_mutation(CustomRecord)

    assert mutations == {"updateCustomRecord": CustomMutation}


@pytest.mark.parametrize("enabled", [True, False])
def test_global_default_controls_undeclared_manager(enabled, settings, mutations):
    from general_manager.manager.general_manager import GeneralManager

    settings.GENERAL_MANAGER = {"GRAPHQL_MUTATIONS_ENABLED": enabled}

    class DefaultRecord(GeneralManager):
        class Interface(BaseTestInterface):
            pass

    GraphQL.create_graphql_mutation(DefaultRecord)
    assert set(mutations) == (
        {f"{operation}DefaultRecord" for operation in ("create", "update", "delete")}
        if enabled
        else set()
    )


@pytest.mark.parametrize("configuration", [None, 0, 1, "false", [], {}, set()])
def test_invalid_global_default_fails_before_registry_changes(
    configuration, settings, mutations
):
    settings.GENERAL_MANAGER = {"GRAPHQL_MUTATIONS_ENABLED": configuration}

    class DefaultRecord:
        Interface = BaseTestInterface

    mutations["custom"] = object()
    before = dict(mutations)
    with pytest.raises(TypeError, match="GRAPHQL_MUTATIONS_ENABLED"):
        GraphQL.create_graphql_mutation(DefaultRecord)
    assert mutations == before


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "allowlist",
    [set(), {"create"}, {"update"}, {"delete"}, {"create", "update", "delete"}],
)
def test_explicit_inherited_allowlist_overrides_global_default(
    enabled, allowlist, settings, mutations
):
    settings.GENERAL_MANAGER = {"GRAPHQL_MUTATIONS_ENABLED": enabled}

    class ParentRecord:
        Interface = BaseTestInterface
        __graphql_mutations__ = allowlist

    class ChildRecord(ParentRecord):
        pass

    GraphQL.create_graphql_mutation(ChildRecord)
    assert set(mutations) == {f"{operation}ChildRecord" for operation in allowlist}


@pytest.mark.parametrize(
    "setting_name",
    ["GENERAL_MANAGER_GRAPHQL_MUTATIONS_ENABLED", "GRAPHQL_MUTATIONS_ENABLED"],
)
def test_global_default_uses_shared_settings_fallback(
    setting_name, settings, mutations
):
    settings.GENERAL_MANAGER = {}
    setattr(settings, setting_name, False)

    class LegacyConfiguredRecord:
        Interface = BaseTestInterface

    GraphQL.create_graphql_mutation(LegacyConfiguredRecord)
    assert mutations == {}


def test_dictionary_global_default_takes_precedence(settings, mutations):
    settings.GENERAL_MANAGER = {"GRAPHQL_MUTATIONS_ENABLED": False}
    settings.GENERAL_MANAGER_GRAPHQL_MUTATIONS_ENABLED = True
    settings.GRAPHQL_MUTATIONS_ENABLED = True

    class DictionaryConfiguredRecord:
        Interface = BaseTestInterface

    GraphQL.create_graphql_mutation(DictionaryConfiguredRecord)
    assert mutations == {}
