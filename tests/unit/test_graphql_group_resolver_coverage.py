"""Focused edge coverage for GraphQL resolver helpers.

These cases exercise the resolver boundary where grouped projections and
request-backed sources have different semantics from ordinary buckets.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, ClassVar
from unittest.mock import Mock, patch

import pytest
from graphql import GraphQLError

from general_manager.api.property import graph_ql_property
from general_manager.api.graphql_ordering import GraphQLOrderingInputError
from general_manager.api.graphql_resolvers import (
    ReadAuthorizationResult,
    UnsupportedGroupedFieldError,
    _NO_GROUPED_RELATION_ID_ALIAS,
    _grouped_field_declaration,
    _is_singular_grouped_relation,
    _is_supported_grouped_annotation,
    _request_pagination_provenance,
    _requested_request_global_operation,
    _unwrap_optional_annotation,
    _forward_remote_request_controls,
    apply_grouped_projection_sorting,
    apply_sorting,
    can_read_instance_for_user,
    contains_none_relation_filter,
    create_list_resolver,
    get_backend_shape,
    grouped_relation_id_alias_value,
    project_grouped_field_value,
    read_grouped_field_value,
    relation_id_alias_field,
)
from general_manager.bucket.group_bucket import GroupBucket
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.interface import (
    DatabaseInterface,
    ExistingModelInterface,
    RequestInterface,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.group_manager import GroupManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.base_permission import ReadPermissionPlan
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class _ResolverInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, object]] = {"id": Input(int)}

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls) -> dict[str, object]:
        return {"name": lambda _interface: "name"}


class _ResolverManager(GeneralManager):
    Interface = _ResolverInterface
    Permission = None


class _RelatedInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, object]] = {"id": Input(int)}


class _RelatedManager(GeneralManager):
    Interface = _RelatedInterface
    Permission = None


class _GroupingInterface(_ResolverInterface):
    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {
            "name": {"type": str},
            "commercial": {
                "type": _RelatedManager,
                "relation_kind": "direct",
            },
        }

    @classmethod
    def get_attributes(cls) -> dict[str, object]:
        return {
            "name": lambda _interface: "name",
            "commercial": lambda _interface: None,
        }


class _GroupingManager(GeneralManager):
    Interface = _GroupingInterface
    Permission = None


# These helper managers are deliberately local to this module. Keep their
# metaclass registrations from changing the process-wide manager registry used
# by the rest of the test suite.
_TEST_MANAGERS = (_ResolverManager, _RelatedManager, _GroupingManager)
for _registry_name in (
    "all_classes",
    "pending_attribute_initialization",
    "pending_graphql_interfaces",
):
    _registry = getattr(GeneralManagerMeta, _registry_name)
    _registry[:] = [manager for manager in _registry if manager not in _TEST_MANAGERS]


class _RequestInterface:
    supports_upstream_query_controls = True


class _LocalRequestInterface:
    supports_upstream_query_controls = False


class _DatabaseShapeInterface(DatabaseInterface):
    pass


class _RequestShapeInterface(RequestInterface):
    pass


class _DatabaseShapeManager:
    Interface = _DatabaseShapeInterface


class _RequestShapeManager:
    Interface = _RequestShapeInterface


class _ExistingShapeInterface(ExistingModelInterface):
    pass


class _ExistingShapeManager:
    Interface = _ExistingShapeInterface


class _UnknownShapeManager:
    Interface = None


class _InheritedProperty:
    @graph_ql_property
    def inherited_label(self) -> str:
        return "label"


class _PropertyManager(_InheritedProperty):
    class Interface:
        @staticmethod
        def get_attribute_types() -> dict[str, dict[str, object]]:
            return {}

        @staticmethod
        def get_graph_ql_properties() -> dict[str, object]:
            return {}


class _ResolverInfo:
    context = SimpleNamespace(user=object())


def _group_bucket(
    manager_class: type[GeneralManager],
    group: GroupManager[GeneralManager] | list[GroupManager[GeneralManager]],
) -> GroupBucket[GeneralManager]:
    """Build a valid group bucket, replacing its groups with test groups."""
    bucket = GroupBucket(
        manager_class,
        ("name",),
        SimpleBucket(manager_class, []),
    )
    bucket._data = group if isinstance(group, list) else [group]
    return bucket


def _group(
    manager_class: type[GeneralManager],
    group_by_value: dict[str, object],
    members: list[object] | None = None,
) -> GroupManager[GeneralManager]:
    return GroupManager(
        manager_class,
        group_by_value,
        SimpleBucket(manager_class, members or []),  # type: ignore[arg-type]
    )


def test_grouped_projection_error_factories_and_none_filter_lists() -> None:
    assert str(UnsupportedGroupedFieldError.file_fields()) == (
        "File fields is not available for grouped results."
    )
    assert str(UnsupportedGroupedFieldError.capabilities()) == (
        "Capabilities is not available for grouped results."
    )
    assert contains_none_relation_filter([{"nested": [{"none": {"id": 1}}]}])


def test_invalid_ordering_is_translated_for_normal_and_grouped_buckets() -> None:
    with pytest.raises(GraphQLError, match="orderBy must be a list"):
        apply_sorting(Mock(), {"field": "name"})

    group = _group(_ResolverManager, {}, [])
    with pytest.raises(GraphQLError, match="must be ASC or DESC"):
        apply_grouped_projection_sorting(
            _group_bucket(_ResolverManager, group),
            [{"field": "name", "direction": "SIDEWAYS"}],
        )


def test_grouped_projection_sorting_resolves_relation_ids_from_all_sources() -> None:
    related = _RelatedManager(7)

    # An explicit relation-id group key is already a safe scalar projection.
    alias_low_group = _group(_GroupingManager, {"commercial_id": 2})
    alias_high_group = _group(_GroupingManager, {"commercial_id": 7})
    alias_bucket = _group_bucket(
        _GroupingManager,
        [alias_high_group, alias_low_group],
    )
    sorted_alias_asc = apply_grouped_projection_sorting(
        alias_bucket,
        [{"field": "commercial__id", "direction": "ASC"}],
    )
    assert [group is alias_low_group for group in sorted_alias_asc] == [True, False]
    sorted_alias_desc = apply_grouped_projection_sorting(
        alias_bucket,
        [{"field": "commercial__id", "direction": "DESC"}],
    )
    assert [group is alias_high_group for group in sorted_alias_desc] == [True, False]

    # A selected relation object can provide the identity without reading members.
    class _NoReadMember:
        @property
        def commercial(self) -> object:
            raise AssertionError

    selected_group = _group(
        _ResolverManager,
        {"commercial": related},
        [_NoReadMember()],
    )
    sorted_selected = apply_grouped_projection_sorting(
        _group_bucket(_ResolverManager, [selected_group]),
        [{"field": "commercial__id", "direction": "ASC"}],
    )
    assert next(iter(sorted_selected)) is selected_group

    same_one = _RelatedManager(1)
    same_two = _RelatedManager(1)
    same_group = _group(
        _ResolverManager,
        {},
        [SimpleNamespace(commercial=same_one), SimpleNamespace(commercial=same_two)],
    )

    different_group = _group(
        _ResolverManager,
        {},
        [
            SimpleNamespace(commercial=_RelatedManager(1)),
            SimpleNamespace(commercial=_RelatedManager(2)),
        ],
    )
    empty_group = _group(_ResolverManager, {}, [])
    member_groups = _group_bucket(
        _ResolverManager,
        [empty_group, selected_group, different_group, same_group],
    )
    sorted_members_asc = apply_grouped_projection_sorting(
        member_groups,
        [{"field": "commercial__id", "direction": "ASC"}],
    )
    assert [group is same_group for group in sorted_members_asc] == [
        True,
        False,
        False,
        False,
    ]
    assert [group is selected_group for group in sorted_members_asc] == [
        False,
        True,
        False,
        False,
    ]
    assert [group is empty_group for group in sorted_members_asc] == [
        False,
        False,
        True,
        False,
    ]
    assert [group is different_group for group in sorted_members_asc] == [
        False,
        False,
        False,
        True,
    ]
    sorted_members_desc = apply_grouped_projection_sorting(
        member_groups,
        [{"field": "commercial__id", "direction": "DESC"}],
    )
    assert [group is selected_group for group in sorted_members_desc] == [
        True,
        False,
        False,
        False,
    ]
    assert [group is same_group for group in sorted_members_desc] == [
        False,
        True,
        False,
        False,
    ]
    assert [group is empty_group for group in sorted_members_desc] == [
        False,
        False,
        True,
        False,
    ]
    assert [group is different_group for group in sorted_members_desc] == [
        False,
        False,
        False,
        True,
    ]


def test_grouped_projection_sorting_rejects_relation_traversal_beyond_id() -> None:
    group = _group(_ResolverManager, {})
    with pytest.raises(GraphQLOrderingInputError, match="not available in this scope"):
        apply_grouped_projection_sorting(
            _group_bucket(_ResolverManager, group),
            [{"field": "commercial__name", "direction": "ASC"}],
        )


def test_grouped_annotation_and_relation_helpers_cover_supported_boundaries() -> None:
    annotated = Annotated[str | None, "label"]
    assert _unwrap_optional_annotation(annotated) is str
    assert _is_singular_grouped_relation(_RelatedManager, None, "commercial")
    assert not _is_singular_grouped_relation(
        list[str], {"relation_kind": "collection"}, "items"
    )
    assert _is_supported_grouped_annotation(
        _RelatedManager, {"relation_kind": "collection"}, "items"
    )
    assert _is_supported_grouped_annotation(list[_RelatedManager], None, "items")
    assert not _is_supported_grouped_annotation(dict[str, int], None, "payload")
    assert not _is_supported_grouped_annotation(list, None, "values")
    assert not _is_supported_grouped_annotation(list[str, int], None, "values")

    collection_manager = SimpleNamespace(
        Interface=SimpleNamespace(
            get_attribute_types=lambda: {
                "commercial": {
                    "type": _RelatedManager,
                    "relation_kind": "collection",
                }
            }
        )
    )
    assert relation_id_alias_field(collection_manager, "commercial_id") is None

    unknown_relation_manager = SimpleNamespace(
        Interface=SimpleNamespace(
            get_attribute_types=lambda: {
                "commercial": {"type": object, "relation_kind": "direct"}
            }
        )
    )
    assert relation_id_alias_field(unknown_relation_manager, "commercial_id") is None


def test_grouped_field_declaration_finds_inherited_properties_and_missing_fields() -> (
    None
):
    group = _group(_PropertyManager, {})
    field_info, annotation = _grouped_field_declaration(group, "inherited_label")
    assert field_info is None
    assert annotation is str
    assert _grouped_field_declaration(group, "missing") == (None, None)


def test_grouped_relation_alias_and_lazy_grouped_reads() -> None:
    assert grouped_relation_id_alias_value(_ResolverManager(id=1), "name_id") is (
        _NO_GROUPED_RELATION_ID_ALIAS
    )

    empty_relation_group = _group(_GroupingManager, {})
    assert (
        grouped_relation_id_alias_value(empty_relation_group, "commercial_id") is None
    )

    member = SimpleNamespace(name="alpha")
    group = _group(_ResolverManager, {}, [member])
    assert read_grouped_field_value(group, "name") == "alpha"
    keyed_group = _group(_ResolverManager, {"name": "group"})
    assert read_grouped_field_value(keyed_group, "name") == "group"


def test_grouped_singular_relation_projection_returns_null_for_disagreement() -> None:
    group = _group(_GroupingManager, {})
    relation_bucket = SimpleBucket(_RelatedManager, [])
    assert project_grouped_field_value(group, "commercial", relation_bucket) is None


def test_backend_shape_and_request_operation_helpers_cover_boundary_errors() -> None:
    assert get_backend_shape(_UnknownShapeManager) == "unknown"
    assert get_backend_shape(_DatabaseShapeManager) == "database"
    assert get_backend_shape(_ExistingShapeManager) == "existing_model"
    assert get_backend_shape(_RequestShapeManager) == "request"

    with pytest.raises(GraphQLError, match="must be ASC or DESC"):
        _requested_request_global_operation(
            SimpleNamespace(requested=False),
            None,
            [{"field": "name", "direction": "SIDEWAYS"}],
        )


def test_request_provenance_and_remote_controls_preserve_source_metadata() -> None:
    item = _ResolverManager(id=1)
    remote = RequestBucket(
        _ResolverManager,
        _RequestInterface,
        items=(item,),
        total_count=5,
        response_is_complete=False,
        upstream_page=2,
        upstream_page_size=3,
    )
    provenance = _request_pagination_provenance(remote)
    assert provenance is not None
    assert provenance.page == 2
    assert provenance.is_partial
    assert _request_pagination_provenance(SimpleBucket(_ResolverManager, [])) is None

    unchanged, forwarded = _forward_remote_request_controls(
        remote,
        SimpleNamespace(requested=False),
        None,
    )
    assert unchanged is remote
    assert forwarded is False

    with pytest.raises(GraphQLError, match="must be ASC or DESC"):
        _forward_remote_request_controls(
            remote,
            SimpleNamespace(requested=False),
            [{"field": "name", "direction": "SIDEWAYS"}],
        )

    local = RequestBucket(
        _ResolverManager,
        _LocalRequestInterface,
        items=(item,),
    )
    unchanged, forwarded = _forward_remote_request_controls(
        local,
        SimpleNamespace(requested=True, page=2, page_size=5),
        None,
    )
    assert unchanged is local
    assert forwarded is False


def test_list_resolver_uses_fallback_for_empty_base_getter() -> None:
    bucket = SimpleBucket(_ResolverManager, [_ResolverManager(id=1)])
    resolver = create_list_resolver(lambda _parent, _inactive: None, _ResolverManager)
    result = ReadAuthorizationResult(
        queryset=bucket,
        candidate_count=None,
        authorized_count=None,
        denied_count=None,
        backend_shape="custom",
        requires_instance_check=False,
        instance_check_reasons=(),
    )
    with (
        patch.object(_ResolverManager, "all", return_value=bucket) as all_mock,
        patch(
            "general_manager.api.graphql_resolvers.apply_read_authorization",
            return_value=result,
        ),
    ):
        payload = resolver(object(), _ResolverInfo())
    all_mock.assert_called_once_with()
    assert payload["pageInfo"]["total_count"] == 1

    with (
        patch.object(_ResolverManager, "filter", return_value=bucket) as filter_mock,
        patch(
            "general_manager.api.graphql_resolvers.apply_read_authorization",
            return_value=result,
        ),
    ):
        payload = resolver(object(), _ResolverInfo(), include_inactive=True)
    filter_mock.assert_called_once_with(include_inactive=True)
    assert payload["pageInfo"]["total_count"] == 1


def test_request_resolver_deny_all_returns_empty_request_bucket() -> None:
    item = _ResolverManager(id=1)
    source = RequestBucket(_ResolverManager, _RequestInterface, items=(item,))
    resolver = create_list_resolver(lambda _parent, _inactive: source, _ResolverManager)
    deny_all = ReadPermissionPlan(
        filters=[], requires_instance_check=False, decision="deny_all"
    )
    with patch(
        "general_manager.api.graphql_resolvers.get_read_permission_filter",
        return_value=deny_all,
    ):
        payload = resolver(object(), _ResolverInfo())
    assert list(payload["items"]) == []
    assert payload["pageInfo"]["total_count"] == 0


def test_request_resolver_rejects_global_operations_after_local_materialization() -> (
    None
):
    item = _ResolverManager(id=1)
    source = RequestBucket(_ResolverManager, _RequestInterface, items=(item,))
    local = SimpleBucket(_ResolverManager, [item])
    resolver = create_list_resolver(lambda _parent, _inactive: source, _ResolverManager)
    conditional = ReadPermissionPlan(filters=[], requires_instance_check=False)

    with (
        patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=conditional,
        ),
        patch(
            "general_manager.api.graphql_resolvers.apply_query_parameter_plan",
            return_value=local,
        ),
    ):
        payload = resolver(object(), _ResolverInfo())
    assert payload["pageInfo"]["total_count"] is None

    with (
        patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=conditional,
        ),
        patch(
            "general_manager.api.graphql_resolvers.apply_query_parameter_plan",
            return_value=local,
        ),
        pytest.raises(GraphQLError, match="global pagination"),
    ):
        resolver(object(), _ResolverInfo(), page=2, page_size=4)

    with (
        patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=conditional,
        ),
        patch(
            "general_manager.api.graphql_resolvers.apply_query_parameter_plan",
            return_value=local,
        ),
        pytest.raises(GraphQLError, match="global ordering"),
    ):
        resolver(
            object(),
            _ResolverInfo(),
            order_by=[{"field": "name", "direction": "ASC"}],
        )

    # The first operation check normally validates ordering before the source
    # is planned. Once that check has already been handled by a caller, the
    # incomplete-response guard still translates malformed ordering input.
    with (
        patch(
            "general_manager.api.graphql_resolvers.get_read_permission_filter",
            return_value=conditional,
        ),
        patch(
            "general_manager.api.graphql_resolvers.apply_query_parameter_plan",
            return_value=local,
        ),
        patch(
            "general_manager.api.graphql_resolvers._requested_request_global_operation",
            return_value=None,
        ),
        pytest.raises(GraphQLError, match="must be ASC or DESC"),
    ):
        resolver(
            object(),
            _ResolverInfo(),
            order_by=[{"field": "name", "direction": "SIDEWAYS"}],
        )


def test_request_resolver_preserves_upstream_page_metadata_without_client_pagination() -> (
    None
):
    item = _ResolverManager(id=1)
    source = RequestBucket(
        _ResolverManager,
        _RequestInterface,
        items=(item,),
        total_count=5,
        response_is_complete=True,
        upstream_page=2,
        upstream_page_size=3,
    )
    resolver = create_list_resolver(lambda _parent, _inactive: source, _ResolverManager)
    allow_all = ReadPermissionPlan(
        filters=[], requires_instance_check=False, decision="allow_all"
    )
    with patch(
        "general_manager.api.graphql_resolvers.get_read_permission_filter",
        return_value=allow_all,
    ):
        payload = resolver(object(), _ResolverInfo())
    assert payload["pageInfo"]["current_page"] == 2
    assert payload["pageInfo"]["page_size"] == 3


def test_instance_read_without_permission_class_defaults_to_allowed() -> None:
    assert can_read_instance_for_user(_ResolverManager(id=1), object()) is True
