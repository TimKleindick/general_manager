"""Unit tests for static search-projection authorization classification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, cast

import pytest

from general_manager.interface.base_interface import AttributeTypedDict
from general_manager.api.property import graph_ql_property
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
    OverrideManagerPermission,
)
from general_manager.search.authorization import (
    SearchProjectionAuthorization,
    analyze_search_projection,
)
from general_manager.search.config import IndexConfig, SearchConfigSpec
from tests.utils.simple_manager_interface import BaseTestInterface


def _attribute(field_type: type, **metadata: object) -> AttributeTypedDict:
    """Build the complete metadata shape required by ``InterfaceBase``."""
    return cast(
        AttributeTypedDict,
        {
            "type": field_type,
            "default": None,
            "is_required": False,
            "is_editable": True,
            "is_derived": False,
            **metadata,
        },
    )


def _empty_document(instance: GeneralManager) -> Mapping[str, object]:
    """Provide a harmless serializer; classification never calls it."""
    del instance
    return {}


class SearchFieldsInterface(BaseTestInterface):
    """Native scalar metadata used by the authorization fixtures."""

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        return {
            "name": _attribute(str),
            "status": _attribute(str),
            "secret": _attribute(str),
        }


class SearchFieldsManager(GeneralManager):
    Interface = SearchFieldsInterface


def _analyze(
    manager: type[GeneralManager],
    index: IndexConfig,
    *,
    config: SearchConfigSpec | None = None,
    registry: Mapping[str, type[GeneralManager]] | None = None,
) -> SearchProjectionAuthorization:
    """Analyze one fixture index with a registry containing its manager."""
    resolved_config = config or SearchConfigSpec(indexes=(index,))
    return analyze_search_projection(
        manager,
        resolved_config,
        index,
        registry or {manager.__name__: manager},
    )


def test_index_without_additional_field_is_fast() -> None:
    """Unrelated restricted fields must not make an index thorough."""

    class PermissionClass(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        secret: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class Manager(SearchFieldsManager):
        Permission = PermissionClass

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert result.fast
    assert result.paths == ("name",)
    assert result.reasons == ()


def test_python_annotation_compiler_metadata_does_not_make_a_policy_custom() -> None:
    """Python's annotation implementation details are not permission code."""

    class PermissionClass(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        __annotate_func__ = staticmethod(lambda: None)

    class Manager(SearchFieldsManager):
        Permission = PermissionClass

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert result.fast
    assert result.reasons == ()


def test_callable_permission_factory_requires_thorough_authorization() -> None:
    """A supported runtime factory has no statically provable field policy."""

    class Manager(SearchFieldsManager):
        Permission = staticmethod(lambda *_args, **_kwargs: None)

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert not result.fast
    assert result.error is None
    assert "additional or custom field permission" in result.reasons


def test_invalid_dependency_in_another_index_does_not_change_this_projection() -> None:
    public = IndexConfig("public", ["name"])
    private = IndexConfig("private", ["secret"])
    config = SearchConfigSpec(
        indexes=(public, private),
        to_document=_empty_document,
        field_dependencies={"name": ("name",), "secret": ("missing",)},
    )
    assert _analyze(SearchFieldsManager, public, config=config).fast
    unresolved = _analyze(SearchFieldsManager, private, config=config)
    assert not unresolved.fast
    assert unresolved.error is not None


@pytest.mark.parametrize(
    "field_rules",
    [
        ["public"],
        ["public", "staff"],
        [],
    ],
    ids=("same-as-row", "superset-of-row", "empty"),
)
def test_same_superset_or_empty_field_rules_do_not_add_restriction(
    field_rules: list[str],
) -> None:
    """Declarative field rules that cannot narrow the row gate stay fast."""

    class PermissionClass(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        name: ClassVar[dict[str, list[str]]] = {"read": field_rules}

    class Manager(SearchFieldsManager):
        Permission = PermissionClass

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert result.fast
    assert result.reasons == ()


def test_restricted_field_is_ignored_when_not_in_projection() -> None:
    """Only fields used by the index can affect its static classification."""

    class PermissionClass(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        secret: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class Manager(SearchFieldsManager):
        Permission = PermissionClass

    result = _analyze(
        Manager,
        IndexConfig(name="global", fields=("name",), filters=("status",)),
    )

    assert result.fast
    assert result.paths == ("name", "status")
    assert result.reasons == ()


def test_restricted_filter_and_sort_fields_require_thorough_classification() -> None:
    """Filter-only and sort-only document fields are part of the proof."""

    class PermissionClass(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        status: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class Manager(SearchFieldsManager):
        Permission = PermissionClass

    result = _analyze(
        Manager,
        IndexConfig(
            name="global",
            fields=("name",),
            filters=("status",),
            sorts=("status",),
        ),
    )

    assert not result.fast
    assert result.paths == ("name", "status")
    assert "additional or custom field permission" in result.reasons


def test_classification_is_independent_per_manager_and_index() -> None:
    """One manager/index proof must not be reused for another pair."""

    class RestrictedPermission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        status: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class FastManager(SearchFieldsManager):
        Permission = AdditiveManagerPermission

    class RestrictedManager(SearchFieldsManager):
        Permission = RestrictedPermission

    fast_index = IndexConfig(name="global", fields=("name",))
    restricted_index = IndexConfig(
        name="private", fields=("name",), filters=("status",)
    )
    fast_config = SearchConfigSpec(indexes=(fast_index, restricted_index))
    restricted_config = SearchConfigSpec(indexes=(fast_index, restricted_index))
    registry = {
        FastManager.__name__: FastManager,
        RestrictedManager.__name__: RestrictedManager,
    }

    assert _analyze(
        FastManager,
        fast_index,
        config=fast_config,
        registry=registry,
    ).fast
    assert _analyze(
        FastManager,
        restricted_index,
        config=fast_config,
        registry=registry,
    ).fast
    assert _analyze(
        RestrictedManager,
        fast_index,
        config=restricted_config,
        registry=registry,
    ).fast
    assert not _analyze(
        RestrictedManager,
        restricted_index,
        config=restricted_config,
        registry=registry,
    ).fast


def test_inherited_field_rule_is_classified() -> None:
    """Permission declarations inherited by a manager remain effective."""

    class ParentPermission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        name: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class ChildPermission(ParentPermission):
        pass

    class Manager(SearchFieldsManager):
        Permission = ChildPermission

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert not result.fast
    assert "additional or custom field permission" in result.reasons


@pytest.mark.parametrize("shadow", [None, "not-a-field-rule"])
def test_non_dict_subclass_shadow_does_not_hide_inherited_field_rule(
    shadow: object,
) -> None:
    """Non-dict class attributes do not erase inherited rule dictionaries."""

    class ParentPermission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        name: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class ChildPermission(ParentPermission):
        name = shadow  # type: ignore[assignment]

    class Manager(SearchFieldsManager):
        Permission = ChildPermission

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert not result.fast
    assert "additional or custom field permission" in result.reasons


def test_overridden_executable_permission_is_thorough_without_execution() -> None:
    """Custom permission code is unknown statically, but is never executed."""

    class ExplodingPermission(AdditiveManagerPermission):
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError

        def check_permission(self, action: str, attribute: str) -> bool:
            del action, attribute
            raise AssertionError

    class Manager(SearchFieldsManager):
        Permission = ExplodingPermission

    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert not result.fast
    assert "additional or custom field permission" in result.reasons


def test_custom_policy_after_built_in_mro_member_is_thorough() -> None:
    """Executable policy classes remain unknown across the full MRO."""

    calls: list[tuple[str, str]] = []

    class CustomPolicy(OverrideManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]

        def check_permission(self, action: str, attribute: str) -> bool:
            calls.append((action, attribute))
            return False

    class CombinedPolicy(AdditiveManagerPermission, CustomPolicy):
        __read__: ClassVar[list[str]] = ["public"]

    class Manager(SearchFieldsManager):
        Permission = CombinedPolicy

    combined = object.__new__(CombinedPolicy)
    assert combined.check_permission("read", "name") is False
    calls.clear()
    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert not result.fast
    assert "additional or custom field permission" in result.reasons
    assert calls == []


def test_noncallable_permission_descriptor_is_thorough_without_access() -> None:
    """Descriptor-shaped policy attributes are executable permission code."""

    accesses: list[object] = []

    class DenyingDescriptor:
        def __get__(self, instance: object, owner: type[object]) -> object:
            del instance, owner
            accesses.append(object())

            def deny(*args: object, **kwargs: object) -> bool:
                del args, kwargs
                return False

            return deny

    class DescriptorPolicy(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        check_permission = DenyingDescriptor()

    class Manager(SearchFieldsManager):
        Permission = DescriptorPolicy

    descriptor = DescriptorPolicy.__dict__["check_permission"]
    assert descriptor.__get__(object(), DescriptorPolicy)("read", "name") is False
    accesses.clear()
    result = _analyze(Manager, IndexConfig(name="global", fields=("name",)))

    assert not result.fast
    assert "additional or custom field permission" in result.reasons
    assert accesses == []


def test_property_without_complete_dependencies_is_thorough() -> None:
    """A custom serializer without a dependency declaration has no proof."""

    class PropertyInterface(BaseTestInterface):
        @classmethod
        def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
            return {"name": _attribute(str)}

    class Manager(GeneralManager):
        Interface = PropertyInterface

        @graph_ql_property
        def display_name(self) -> str:
            raise AssertionError

    config = SearchConfigSpec(
        indexes=(IndexConfig(name="global", fields=("display_name",)),),
        to_document=_empty_document,
    )
    result = _analyze(Manager, config.indexes[0], config=config)

    assert not result.fast
    assert result.reasons == ("unknown dependencies",)
    assert result.error is not None
    assert "field_dependencies" in result.error


def test_native_property_without_dependencies_is_thorough() -> None:
    """A native property without a declared source remains unknown."""

    class PropertyInterface(BaseTestInterface):
        @classmethod
        def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
            return {"name": _attribute(str)}

    class Manager(GeneralManager):
        Interface = PropertyInterface

        @graph_ql_property
        def display_name(self) -> str:
            raise AssertionError

    index = IndexConfig(name="global", fields=("display_name",))
    result = _analyze(Manager, index)

    assert not result.fast
    assert result.paths == ()
    assert result.reasons == ("unknown dependencies",)
    assert result.error is not None
    assert "without complete field_dependencies" in result.error


def test_field_dependencies_check_scalar_relationship_and_property_paths() -> None:
    """Custom aliases retain target field and delegated-permission checks."""

    class TargetInterface(BaseTestInterface):
        @classmethod
        def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
            return {"name": _attribute(str)}

    class TargetPermission(AdditiveManagerPermission):
        __read__: ClassVar[list[str]] = ["public"]
        name: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}
        display_name: ClassVar[dict[str, list[str]]] = {"read": ["staff"]}

    class TargetManager(GeneralManager):
        Interface = TargetInterface
        Permission = TargetPermission

        @graph_ql_property
        def display_name(self) -> str:
            raise AssertionError

    class SourceInterface(BaseTestInterface):
        @classmethod
        def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
            return {
                "name": _attribute(str),
                "target": _attribute(TargetManager, relation_kind="direct"),
            }

    class SourcePermission(AdditiveManagerPermission):
        __based_on__: ClassVar[str] = "target"

    class SourceManager(GeneralManager):
        Interface = SourceInterface
        Permission = SourcePermission

    index = IndexConfig(
        name="global",
        fields=("title", "target_title", "target_label"),
    )
    config = SearchConfigSpec(
        indexes=(index,),
        to_document=_empty_document,
        field_dependencies={
            "title": ("name",),
            "target_title": ("target__name",),
            "target_label": ("target__display_name",),
        },
    )
    registry = {
        SourceManager.__name__: SourceManager,
        TargetManager.__name__: TargetManager,
    }

    result = _analyze(
        SourceManager,
        index,
        config=config,
        registry=registry,
    )

    assert not result.fast
    assert result.paths == (
        "name",
        "target__display_name",
        "target__name",
    )
    assert "additional or custom field permission" in result.reasons


def test_field_dependencies_reject_an_unresolved_source() -> None:
    """An invalid custom dependency cannot accidentally produce a fast plan."""

    index = IndexConfig(name="global", fields=("title",))
    config = SearchConfigSpec(
        indexes=(index,),
        to_document=lambda instance: {"title": instance.name},
        field_dependencies={"title": ("missing",)},
    )

    result = _analyze(SearchFieldsManager, index, config=config)

    assert not result.fast
    assert result.reasons == ("unknown dependencies",)
    assert result.error is not None
    assert "Invalid search source path" in result.error


def test_native_field_dependencies_are_supported_without_custom_serializer() -> None:
    """Native dependency declarations may be used without ``to_document``."""

    index = IndexConfig(name="global", fields=("name",))
    config = SearchConfigSpec(
        indexes=(index,),
        field_dependencies={"name": ("name",)},
    )

    result = _analyze(SearchFieldsManager, index, config=config)

    assert result.fast
    assert result.paths == ("name",)
