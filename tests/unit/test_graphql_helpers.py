from __future__ import annotations

from types import MappingProxyType
from unittest import mock

import pytest
import graphene  # type: ignore[import]
from graphql import GraphQLError
from django.test import SimpleTestCase
from graphql import parse
from graphql.language.ast import (
    FragmentDefinitionNode,
    IntValueNode,
    OperationDefinitionNode,
    StringValueNode,
)

from general_manager.api.graphql import (
    BigIntScalar,
    GraphQL,
    InvalidGeneralManagerClassError,
    InvalidMeasurementValueError,
    MeasurementScalar,
    MissingChannelLayerError,
    MissingManagerIdentifierError,
    UnsupportedGraphQLFieldTypeError,
    get_read_permission_filter,
)
from typing import ClassVar

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.base_permission import (
    BasePermission,
    ReadPermissionPlan,
)
from general_manager.api.graphql_resolvers import (
    apply_grouping,
    apply_pagination,
    apply_read_authorization,
    parse_input,
    resolve_instance_check_reasons,
    selection_includes_path,
)
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class _DummyInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {1: {"name": "Alpha"}}

    def get_data(self, search_date=None):
        """
        Retrieve the stored data record for this instance's id.

        Parameters:
            search_date (optional): Ignored; accepted for API compatibility.

        Returns:
            dict: The data record associated with this instance's id.
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Provide the mapping of attribute names to their GraphQL-exposed types.

        Returns:
            dict: A mapping where each key is an attribute name and each value is a dict containing type information, e.g. {"name": {"type": str}}.
        """
        return {"name": {"type": str}}

    @classmethod
    def get_attributes(cls):
        """
        Provide attribute extractors for the interface.

        Returns:
            dict: Mapping of attribute names to callables that accept an interface instance and return the attribute's value (e.g., `"name"` -> callable that returns the interface's `"name"`).
        """
        return {"name": lambda interface: interface.get_data()["name"]}

    @classmethod
    def filter(cls, **kwargs):
        """
        Produce a SimpleBucket of parent-manager instances matching the provided ids or all stored ids.

        Parameters:
            id__in (iterable, optional): Iterable of ids to include; if omitted, all keys from cls.data_store are used.

        Returns:
            SimpleBucket: A bucket containing instances of the parent manager class constructed for each selected id.
        """
        ids = kwargs.get("id__in") or list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class _DummyPermission(BasePermission):
    def check_permission(self, *args, **kwargs) -> None:
        """
        Allow access unconditionally by performing no permission checks.

        This implementation never raises and is intended to grant permission for all calls.
        """
        return None

    def check_operation_permission(self, *args, **kwargs) -> bool:
        return True

    def describe_operation_permissions(self, *args, **kwargs) -> tuple[str, ...]:
        return ()

    def get_permission_filter(self):
        """
        Provide the permission filter used for read operations.

        Returns:
            list[dict]: A list containing a single filter specification mapping `"filter"` to `{"status": "public"}` and `"exclude"` to an empty dict.
        """
        return [{"filter": {"status": "public"}, "exclude": {}}]


class _DummyManager(GeneralManager):
    Interface = _DummyInterface
    Permission = _DummyPermission


class _Info:
    def __init__(self) -> None:
        """
        Create a minimal info container with a context object exposing a `user` attribute.

        Sets self.context to a lightweight object with a single attribute `user` initialized to a generic object instance.
        """
        self.context = type("Context", (), {"user": object()})()


def _selection_info(query: str) -> object:
    """
    Build a lightweight GraphQL resolver info object from a query document.

    The returned object provides `field_nodes` and `fragments`, matching the
    attributes consumed by selection traversal helpers.
    """
    document = parse(query)
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if isinstance(definition, FragmentDefinitionNode)
    }
    field_nodes = [
        selection
        for definition in document.definitions
        if isinstance(definition, OperationDefinitionNode)
        for selection in definition.selection_set.selections
    ]
    return type(
        "SelectionInfo", (), {"field_nodes": field_nodes, "fragments": fragments}
    )()


class GraphQLHelperTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Initialize GeneralmanagerConfig with the test dummy manager classes used by the test case.

        This configures both interface and permission manager registries to use _DummyManager so each test runs with the same minimal manager implementations.
        """
        super().setUp()
        self._original_all_classes = list(GeneralManagerMeta.all_classes)
        self._original_pending_attribute_initialization = list(
            GeneralManagerMeta.pending_attribute_initialization
        )
        self._original_pending_graphql_interfaces = list(
            GeneralManagerMeta.pending_graphql_interfaces
        )
        GeneralmanagerConfig.initialize_general_manager_classes(
            [_DummyManager],
            [_DummyManager],
        )

    def tearDown(self) -> None:
        """Restore process-global manager registries mutated by local classes."""
        GeneralManagerMeta.all_classes = self._original_all_classes
        GeneralManagerMeta.pending_attribute_initialization = (
            self._original_pending_attribute_initialization
        )
        GeneralManagerMeta.pending_graphql_interfaces = (
            self._original_pending_graphql_interfaces
        )
        super().tearDown()

    def test_parse_input_ignores_json_non_object_values(self) -> None:
        """
        GraphQL filter/exclude JSON must decode to an object mapping.
        """
        assert parse_input(MappingProxyType({"status": "active"})) == {
            "status": "active"
        }
        assert parse_input("[1, 2]") == {}
        assert parse_input('"value"') == {}
        assert parse_input("null") == {}

    def test_apply_grouping_uses_default_grouping_for_blank_sentinel(self) -> None:
        """
        ``group_by=[""]`` delegates to ``Bucket.group_by()`` without field names.
        """
        queryset = SimpleBucket(
            _DummyManager,
            [_DummyManager(id=1)],
        )

        grouped = apply_grouping(queryset, [""])

        assert grouped._group_by_keys == ()

    def test_apply_grouping_preserves_none_and_delegates_lists(self) -> None:
        """
        ``None`` leaves the bucket unchanged; lists are forwarded as group keys.
        """
        queryset = SimpleBucket(
            _DummyManager,
            [_DummyManager(id=1)],
        )

        assert apply_grouping(queryset, None) is queryset
        grouped = apply_grouping(queryset, ["name"])

        assert grouped._group_by_keys == ("name",)

    def test_apply_pagination_defaults_only_the_missing_argument(self) -> None:
        """
        Pagination defaults to page 1 or size 10 when exactly one value is given.
        """
        queryset = SimpleBucket(
            _DummyManager,
            [_DummyManager(id=value) for value in range(12)],
        )

        assert len(apply_pagination(queryset, page=2, page_size=None)) == 2
        first_page = apply_pagination(queryset, page=None, page_size=5)

        assert [item.identification["id"] for item in first_page] == [0, 1, 2, 3, 4]

    def test_measurement_scalar_invalid(self) -> None:
        """
        Verify that serializing a non-measurement string with MeasurementScalar raises an InvalidMeasurementValueError.

        This test calls MeasurementScalar.serialize with an invalid value and expects an InvalidMeasurementValueError to be raised.
        """
        with pytest.raises(InvalidMeasurementValueError):
            MeasurementScalar.serialize("not-a-measurement")  # type: ignore[arg-type]

    def test_registry_snapshot_includes_capability_type_registry(self) -> None:
        """
        Verify registry snapshots include generated capability GraphQL types.
        """
        capability_type = type("CapabilityType", (graphene.ObjectType,), {})
        original_registry = GraphQL.graphql_capability_type_registry
        GraphQL.graphql_capability_type_registry = {"Capability": capability_type}
        try:
            snapshot = GraphQL.get_registry_snapshot()

            assert snapshot.graphql_capability_type_registry == {
                "Capability": capability_type
            }
            assert snapshot.graphql_capability_type_registry is not (
                GraphQL.graphql_capability_type_registry
            )
        finally:
            GraphQL.graphql_capability_type_registry = original_registry

    def test_registry_snapshot_copies_registry_dictionaries(self) -> None:
        """
        Verify registry snapshots detach every registry dictionary.
        """
        field = graphene.String()
        object_type = type("SnapshotObjectType", (graphene.ObjectType,), {})
        input_type = type("SnapshotInputType", (graphene.InputObjectType,), {})
        union_type = type(
            "SnapshotUnion",
            (graphene.Union,),
            {"Meta": type("Meta", (), {"types": (object_type,)})},
        )
        original_values = {
            "_mutations": GraphQL._mutations,
            "_query_fields": GraphQL._query_fields,
            "_subscription_fields": GraphQL._subscription_fields,
            "_page_type_registry": GraphQL._page_type_registry,
            "_subscription_payload_registry": GraphQL._subscription_payload_registry,
            "graphql_type_registry": GraphQL.graphql_type_registry,
            "graphql_filter_type_registry": GraphQL.graphql_filter_type_registry,
            "graphql_capability_type_registry": GraphQL.graphql_capability_type_registry,
            "manager_registry": GraphQL.manager_registry,
        }
        original_search_union = GraphQL._search_union
        original_search_result_type = GraphQL._search_result_type
        try:
            GraphQL._mutations = {"mutation": object_type}
            GraphQL._query_fields = {"name": field}
            GraphQL._subscription_fields = {"sub": field}
            GraphQL._page_type_registry = {"page": object_type}
            GraphQL._subscription_payload_registry = {"payload": object_type}
            GraphQL.graphql_type_registry = {"manager": object_type}
            GraphQL.graphql_filter_type_registry = {"filter": input_type}
            GraphQL.graphql_capability_type_registry = {"capability": object_type}
            GraphQL.manager_registry = {"manager": _DummyManager}
            GraphQL._search_union = union_type
            GraphQL._search_result_type = object_type

            snapshot = GraphQL.get_registry_snapshot()

            dictionary_pairs = [
                (snapshot.mutations, GraphQL._mutations),
                (snapshot.query_fields, GraphQL._query_fields),
                (snapshot.subscription_fields, GraphQL._subscription_fields),
                (snapshot.page_type_registry, GraphQL._page_type_registry),
                (
                    snapshot.subscription_payload_registry,
                    GraphQL._subscription_payload_registry,
                ),
                (snapshot.graphql_type_registry, GraphQL.graphql_type_registry),
                (
                    snapshot.graphql_filter_type_registry,
                    GraphQL.graphql_filter_type_registry,
                ),
                (
                    snapshot.graphql_capability_type_registry,
                    GraphQL.graphql_capability_type_registry,
                ),
                (snapshot.manager_registry, GraphQL.manager_registry),
            ]
            for snapshot_dict, live_dict in dictionary_pairs:
                snapshot_dict["extra"] = object()
                assert "extra" not in live_dict

            assert snapshot.query_fields["name"] is field
            assert snapshot.search_union is union_type
            assert snapshot.search_result_type is object_type
        finally:
            GraphQL._mutations = original_values["_mutations"]
            GraphQL._query_fields = original_values["_query_fields"]
            GraphQL._subscription_fields = original_values["_subscription_fields"]
            GraphQL._page_type_registry = original_values["_page_type_registry"]
            GraphQL._subscription_payload_registry = original_values[
                "_subscription_payload_registry"
            ]
            GraphQL.graphql_type_registry = original_values["graphql_type_registry"]
            GraphQL.graphql_filter_type_registry = original_values[
                "graphql_filter_type_registry"
            ]
            GraphQL.graphql_capability_type_registry = original_values[
                "graphql_capability_type_registry"
            ]
            GraphQL.manager_registry = original_values["manager_registry"]
            GraphQL._search_union = original_search_union
            GraphQL._search_result_type = original_search_result_type

    def test_measurement_scalar_parse_literal(self) -> None:
        node = StringValueNode(value="10 m")
        assert MeasurementScalar.parse_literal(node) is not None
        assert MeasurementScalar.parse_literal(object()) is None

    def test_bigint_scalar_round_trip(self) -> None:
        value = 9223372036854775807
        assert BigIntScalar.serialize(value) == str(value)
        assert BigIntScalar.parse_value(str(value)) == value
        assert BigIntScalar.parse_value(value) == value

    def test_bigint_scalar_rejects_bools(self) -> None:
        with pytest.raises(TypeError, match="BigIntScalar cannot accept boolean"):
            BigIntScalar.serialize(True)

        with pytest.raises(TypeError, match="BigIntScalar cannot accept boolean"):
            BigIntScalar.parse_value(False)

    def test_bigint_scalar_rejects_non_coercible_values(self) -> None:
        with pytest.raises(TypeError, match="BigIntScalar cannot coerce object"):
            BigIntScalar.serialize(object())

    def test_bigint_scalar_parse_literal(self) -> None:
        string_node = StringValueNode(value="9223372036854775807")
        int_node = IntValueNode(value="9223372036854775807")
        assert BigIntScalar.parse_literal(string_node) == 9223372036854775807
        assert BigIntScalar.parse_literal(int_node) == 9223372036854775807
        assert BigIntScalar.parse_literal(object()) is None

    def test_permission_filter_helper(self) -> None:
        info = _Info()
        plan = get_read_permission_filter(_DummyManager, info)
        assert plan.filters == [{"filter": {"status": "public"}, "exclude": {}}]
        assert plan.requires_instance_check is True
        assert plan.instance_check_reasons in ((), ("no_prefilter_backend",))

    def test_permission_filter_helper_preserves_custom_read_plan(self) -> None:
        class CustomPlanPermission(_DummyPermission):
            def get_read_permission_plan(self) -> ReadPermissionPlan:
                return ReadPermissionPlan(
                    filters=[{"filter": {"status": "public"}}],
                    requires_instance_check=False,
                    instance_check_reasons=("custom",),
                )

            def get_permission_filter(self):
                pytest.fail("legacy filter fallback should not run")

        class CustomPlanManager(GeneralManager):
            Interface = _DummyInterface
            Permission = CustomPlanPermission

        plan = get_read_permission_filter(CustomPlanManager, _Info())

        assert plan.filters == [{"filter": {"status": "public"}}]
        assert plan.requires_instance_check is False
        assert plan.instance_check_reasons == ("custom",)

    def test_permission_filter_helper_defaults_allow_without_permission(self) -> None:
        class NoPermissionManager(GeneralManager):
            Interface = _DummyInterface
            Permission = None

        plan = get_read_permission_filter(NoPermissionManager, _Info())

        assert plan.filters == []
        assert plan.requires_instance_check is False

    def test_handle_graphql_error_preserves_explicit_graphql_error(self) -> None:
        error = GraphQLError("explicit", extensions={"code": "CUSTOM"})

        assert GraphQL._handle_graph_ql_error(error) is error

    def test_apply_permission_filters_enforces_instance_read_gate(self) -> None:
        class AdminOnlyPermission(BasePermission):
            def check_permission(self, *args, **kwargs) -> bool:
                return False

            def check_operation_permission(self, *args, **kwargs) -> bool:
                return False

            def describe_operation_permissions(
                self, *args, **kwargs
            ) -> tuple[str, ...]:
                return ()

            def can_read_instance(self) -> bool:
                return False

            def get_permission_filter(self):
                return [{"filter": {}, "exclude": {}}]

        class AdminOnlyManager(GeneralManager):
            Interface = _DummyInterface
            Permission = AdminOnlyPermission

        GeneralmanagerConfig.initialize_general_manager_classes(
            [AdminOnlyManager],
            [AdminOnlyManager],
        )
        info = _Info()
        queryset = SimpleBucket(
            AdminOnlyManager,
            [AdminOnlyManager(id=1)],
        )

        filtered = GraphQL._apply_permission_filters(queryset, AdminOnlyManager, info)
        assert list(filtered) == []

    def test_apply_permission_filters_logs_aggregate_summary(self) -> None:
        class AdminOnlyPermission(BasePermission):
            def check_permission(self, *args, **kwargs) -> bool:
                return False

            def check_operation_permission(self, *args, **kwargs) -> bool:
                return False

            def describe_operation_permissions(
                self, *args, **kwargs
            ) -> tuple[str, ...]:
                return ()

            def can_read_instance(self) -> bool:
                return False

            def get_permission_filter(self):
                return [{"filter": {}, "exclude": {}}]

        class AdminOnlyManager(GeneralManager):
            Interface = _DummyInterface
            Permission = AdminOnlyPermission

        GeneralmanagerConfig.initialize_general_manager_classes(
            [AdminOnlyManager],
            [AdminOnlyManager],
        )
        info = _Info()
        queryset = SimpleBucket(AdminOnlyManager, [AdminOnlyManager(id=1)])

        with (
            mock.patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=ReadPermissionPlan(
                    filters=[{"filter": {}, "exclude": {}}],
                    requires_instance_check=True,
                    instance_check_reasons=("unfilterable_read_rule",),
                ),
            ),
            mock.patch("general_manager.api.graphql_resolvers.logger") as logger_mock,
        ):
            filtered = GraphQL._apply_permission_filters(
                queryset, AdminOnlyManager, info
            )

        assert list(filtered) == []
        logger_mock.info.assert_called_once()
        context = logger_mock.info.call_args.kwargs["context"]
        assert context["source"] == "list"
        assert context["manager"] == "AdminOnlyManager"
        assert context["candidate_count"] == 1
        assert context["authorized_count"] == 0
        assert context["denied_count"] == 1
        assert context["requires_instance_check"] is True
        assert context["instance_check_reasons"] == ["unfilterable_read_rule"]

    def test_resolve_instance_check_reasons_marks_custom_backend_fallback(self) -> None:
        reasons = resolve_instance_check_reasons(
            ReadPermissionPlan(
                filters=[{"filter": {}, "exclude": {}}],
                requires_instance_check=True,
            ),
            backend_shape="custom",
        )

        assert reasons == ("no_prefilter_backend",)

    def test_apply_read_authorization_without_permission_allows_candidates(
        self,
    ) -> None:
        class NoPermissionManager(GeneralManager):
            Interface = _DummyInterface
            Permission = None

        queryset = SimpleBucket(
            NoPermissionManager,
            [NoPermissionManager(id=1), NoPermissionManager(id=2)],
        )

        with (
            mock.patch(
                "general_manager.api.graphql_resolvers.get_read_permission_filter",
                return_value=ReadPermissionPlan(
                    filters=[],
                    requires_instance_check=True,
                ),
            ),
            mock.patch("general_manager.api.graphql_resolvers.logger") as logger_mock,
        ):
            result = apply_read_authorization(
                queryset,
                NoPermissionManager,
                _Info(),
                source="list",
            )

        assert result.queryset is queryset
        assert result.candidate_count == 2
        assert result.authorized_count == 2
        assert result.denied_count == 0
        assert result.requires_instance_check is False
        logger_mock.info.assert_not_called()

    def test_create_list_resolver_runs_row_gate_once_per_candidate(self) -> None:
        class CountingPermission(BasePermission):
            check_count = 0

            def check_permission(self, *args, **kwargs) -> bool:
                return True

            def check_operation_permission(self, *args, **kwargs) -> bool:
                return True

            def describe_operation_permissions(
                self, *args, **kwargs
            ) -> tuple[str, ...]:
                return ()

            def can_read_instance(self) -> bool:
                type(self).check_count += 1
                return True

            def get_permission_filter(self):
                return [{"filter": {}, "exclude": {}}]

        class CountingManager(GeneralManager):
            Interface = _DummyInterface
            Permission = CountingPermission

        GeneralmanagerConfig.initialize_general_manager_classes(
            [CountingManager],
            [CountingManager],
        )
        info = _Info()
        queryset = SimpleBucket(
            CountingManager,
            [CountingManager(id=1), CountingManager(id=2)],
        )
        resolver = GraphQL._create_list_resolver(
            lambda _self, _include_inactive: queryset,
            CountingManager,
        )

        with mock.patch.object(SimpleBucket, "filter", return_value=queryset):
            result = resolver(object(), info)

        assert result["pageInfo"]["total_count"] == 2
        assert CountingPermission.check_count == 2

    def test_selection_includes_path_handles_direct_nested_fields(self) -> None:
        """
        Verify selection path detection works for directly nested list fields.
        """
        info = _selection_info(
            """
            query {
                projectList {
                    items {
                        capabilities {
                            canRename
                        }
                    }
                }
            }
            """
        )

        assert selection_includes_path(info, ("items", "capabilities")) is True
        assert selection_includes_path(info, ("items", "missing")) is False

    def test_selection_includes_path_handles_fragments(self) -> None:
        """
        Verify selection path detection follows named and inline fragments.
        """
        info = _selection_info(
            """
            query {
                projectList {
                    ...ProjectPageFields
                }
            }

            fragment ProjectPageFields on ProjectPage {
                items {
                    ... on ProjectType {
                        capabilities {
                            canRename
                        }
                    }
                }
            }
            """
        )

        assert selection_includes_path(info, ("items", "capabilities")) is True

    def test_selection_includes_path_skips_fragment_cycles(self) -> None:
        """
        Verify cyclic named fragments do not recurse indefinitely.
        """
        info = _selection_info(
            """
            query {
                projectList {
                    ...ProjectPageFields
                }
            }

            fragment ProjectPageFields on ProjectPage {
                ...ProjectPageFields
            }
            """
        )

        assert selection_includes_path(info, ("items", "capabilities")) is False

    def test_selection_includes_path_handles_empty_or_missing_selections(self) -> None:
        """
        Verify selection path detection returns false for empty or absent selections.
        """
        info = type("SelectionInfo", (), {})()

        assert selection_includes_path(info, ("items", "capabilities")) is False
        assert (
            selection_includes_path(_selection_info("query { projectList }"), ())
            is False
        )

    def test_graphql_error_types(self) -> None:
        """
        Verify GraphQL-related error classes produce the expected human-readable messages.

        Asserts that:
        - `InvalidGeneralManagerClassError(GeneralManager)` message ends with "GeneralManager to create a GraphQL interface."
        - `UnsupportedGraphQLFieldTypeError(dict)` message starts with "GraphQL does not support dict fields"
        - `MissingManagerIdentifierError()` message equals "id is required."
        - `MissingChannelLayerError()` message starts with "No channel layer configured"
        """
        assert str(InvalidGeneralManagerClassError(GeneralManager)).endswith(
            "GeneralManager to create a GraphQL interface."
        )
        assert str(UnsupportedGraphQLFieldTypeError(dict)).startswith(
            "GraphQL does not support dict fields"
        )
        assert str(MissingManagerIdentifierError()) == "id is required."
        assert str(MissingChannelLayerError()).startswith("No channel layer configured")

    def test_handle_graphql_error_codes(self) -> None:
        perm_error = GraphQL._handle_graph_ql_error(PermissionError("nope"))
        assert perm_error.extensions["code"] == "PERMISSION_DENIED"
        value_error = GraphQL._handle_graph_ql_error(ValueError("bad"))
        assert value_error.extensions["code"] == "BAD_USER_INPUT"
        lookup_error = GraphQL._handle_graph_ql_error(LookupError("missing"))
        assert lookup_error.extensions["code"] == "INTERNAL_SERVER_ERROR"
        runtime_error = GraphQL._handle_graph_ql_error(RuntimeError("oops"))
        assert runtime_error.extensions["code"] == "INTERNAL_SERVER_ERROR"
