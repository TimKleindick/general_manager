from __future__ import annotations

from datetime import datetime
import pickle
from typing import Any, ClassVar
from unittest.mock import patch

from django.test import SimpleTestCase

from general_manager.api.property import GraphQLProperty
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.as_of import HistoricalReadNotSupportedError, as_of
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface.bundles import REQUEST_CAPABILITIES
from general_manager.interface.capabilities.request import RequestQueryCapability
from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    InvalidRequestFilterValueError,
    MissingRequestPayloadFieldError,
    RequestExcludeNotSupportedError,
    RequestField,
    RequestFilter,
    RequestLocalPaginationUnsupportedError,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestSingleResponseRequiredError,
    RequestTransportConfig,
    RequestTransportResponse,
    UnknownRequestFilterError,
    apply_request_lookup,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.meta import AttributeEvaluationError


class RemoteProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str)
        updated_at = RequestField(datetime)
        local_name = RequestField(str)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(
                    remote_name="state",
                    value_type=str,
                    supports_exclude=True,
                    exclude_remote_name="state_not",
                ),
                "name__icontains": RequestFilter(remote_name="search", value_type=str),
                "updated_at__gte": RequestFilter(
                    remote_name="modifiedAfter",
                    value_type=datetime,
                ),
                "ordering": RequestFilter(remote_name="sort", value_type=str),
                "page": RequestFilter(remote_name="page", value_type=int),
                "page_size": RequestFilter(remote_name="pageSize", value_type=int),
                "list_only": RequestFilter(
                    remote_name="listOnly",
                    value_type=str,
                    operation_names=frozenset({"list"}),
                ),
                "local_name__icontains": RequestFilter(
                    allow_local_fallback=True,
                    value_type=str,
                ),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/projects/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/projects",
                ),
                "search": RequestQueryOperation(
                    name="search",
                    method="POST",
                    path="/projects/search",
                    filters={
                        "search_only": RequestFilter(
                            remote_name="q",
                            location="body",
                            value_type=str,
                        )
                    },
                ),
            }

        calls: ClassVar[list[dict[str, Any]]] = []

        @classmethod
        def execute_request_plan(cls, plan: RequestQueryPlan) -> RequestQueryResult:
            cls.calls.append(
                {
                    "operation": plan.operation_name,
                    "plan": plan,
                }
            )
            if plan.operation_name == "detail":
                if plan.path_params["id"] == -1:
                    return RequestQueryResult(items=())
                if plan.path_params["id"] == 99:
                    return RequestQueryResult(
                        items=(
                            {
                                "id": 99,
                                "name": "Duplicate One",
                                "status": "active",
                                "updated_at": datetime(2026, 3, 11, 12, 0, 0),
                                "local_name": "Duplicate One",
                            },
                            {
                                "id": 99,
                                "name": "Duplicate Two",
                                "status": "active",
                                "updated_at": datetime(2026, 3, 11, 12, 1, 0),
                                "local_name": "Duplicate Two",
                            },
                        )
                    )
                return RequestQueryResult(
                    items=(
                        {
                            "id": plan.path_params["id"],
                            "name": "Detail Alpha",
                            "status": "active",
                            "updated_at": datetime(2026, 3, 11, 12, 0, 0),
                            "local_name": "Alpha Detail",
                        },
                    )
                )
            if plan.operation_name == "search":
                return RequestQueryResult(
                    items=(
                        {
                            "id": 7,
                            "name": "Search Result",
                            "status": "active",
                            "updated_at": datetime(2026, 3, 11, 10, 0, 0),
                            "local_name": "Search Local",
                        },
                    ),
                    total_count=1,
                )
            if plan.query_params.get("pageSize") == 1:
                return RequestQueryResult(
                    items=(
                        {
                            "id": 1,
                            "name": "Alpha",
                            "status": "active",
                            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
                            "local_name": "Alpha Local",
                        },
                    ),
                    total_count=2,
                )
            return RequestQueryResult(
                items=(
                    {
                        "id": 1,
                        "name": "Alpha",
                        "status": "active",
                        "updated_at": datetime(2026, 3, 11, 9, 0, 0),
                        "local_name": "Alpha Local",
                    },
                    {
                        "id": 2,
                        "name": "Beta",
                        "status": "inactive",
                        "updated_at": datetime(2026, 3, 10, 9, 0, 0),
                        "local_name": "Beta Local",
                    },
                ),
                total_count=2,
            )


RemoteProject._attributes = RemoteProject.Interface.get_attributes()
GeneralManagerMeta.create_at_properties_for_attributes(
    RemoteProject._attributes.keys(),
    RemoteProject,
)


class PayloadProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        display_name = RequestField(
            str,
            source=("record", "name"),
            normalizer=lambda value: str(value).upper(),
        )
        optional_label = RequestField(
            str,
            source=("record", "optional"),
            default="UNKNOWN",
            is_required=False,
        )
        required_label = RequestField(
            str,
            source=("record", "required"),
        )

        class Meta:
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/payload-projects/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/payload-projects",
                ),
            }

        calls: ClassVar[list[dict[str, Any]]] = []

        @classmethod
        def execute_request_plan(cls, plan: RequestQueryPlan) -> RequestQueryResult:
            cls.calls.append({"plan": plan})
            return RequestQueryResult(
                items=(
                    {"id": 1, "record": {"name": "alpha"}},
                    {"id": 2, "record": {"name": "beta", "optional": "known"}},
                ),
                total_count=2,
            )


PayloadProject._attributes = PayloadProject.Interface.get_attributes()
GeneralManagerMeta.create_at_properties_for_attributes(
    PayloadProject._attributes.keys(),
    PayloadProject,
)


def _trusted_pickle_loads(data: bytes) -> Any:
    return pickle.loads(data)  # noqa: S301 - test data is created locally


class TestRequestInterface(SimpleTestCase):
    def test_request_query_capability_direct_paths_fail_closed_in_as_of(self) -> None:
        capability = RemoteProject.Interface.require_capability("query")
        self.assertIsInstance(capability, RequestQueryCapability)
        bucket = RemoteProject.all()
        plan = bucket.request_plan
        self.assertIsNotNone(plan)
        RemoteProject.Interface.calls.clear()

        with as_of("2022-01-01"):
            operations = (
                lambda: capability.validate_lookups(RemoteProject.Interface),
                lambda: capability.build_bucket(RemoteProject.Interface),
                lambda: capability.execute_plan(RemoteProject.Interface, plan),
                lambda: list(bucket),
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(HistoricalReadNotSupportedError):
                        operation()

        self.assertEqual(RemoteProject.Interface.calls, [])

    def test_apply_request_lookup_in_rejects_string_collections(self) -> None:
        self.assertFalse(apply_request_lookup("A", "in", "AB"))
        self.assertFalse(apply_request_lookup(65, "in", b"ABC"))
        self.assertTrue(apply_request_lookup("A", "in", ("A", "B")))

    def test_request_query_capability_rejects_non_result_execution_value(self) -> None:
        class InvalidResultInterface:
            _parent_class = type("InvalidResultProject", (), {})

            @classmethod
            def get_capability_handler(cls, name: str) -> object | None:
                return None

            @classmethod
            def execute_request_plan(cls, plan: RequestQueryPlan) -> object:
                return {"items": ()}

        plan = RequestQueryPlan(
            operation_name="list",
            action="all",
            method="GET",
            path="/projects",
        )

        with self.assertRaises(TypeError):
            RequestQueryCapability().execute_plan(InvalidResultInterface, plan)  # type: ignore[arg-type]

    def setUp(self) -> None:
        RemoteProject.Interface.calls.clear()

    def test_filter_returns_request_bucket(self) -> None:
        bucket = RemoteProject.filter(status="active")

        self.assertIsInstance(bucket, RequestBucket)

    def test_with_instances_reuses_materialized_request_candidates(self) -> None:
        """Build an exact materialized subset without another request execution."""
        bucket = RemoteProject.filter(status="active")
        first, second = tuple(bucket)
        calls_after_materialization = len(RemoteProject.Interface.calls)

        subset = bucket.with_instances([first, second])

        self.assertEqual(
            [item.identification for item in subset],
            [first.identification, second.identification],
        )
        self.assertEqual(
            len(RemoteProject.Interface.calls), calls_after_materialization
        )

    def test_request_capabilities_alias_is_reexported_from_bundles_package(
        self,
    ) -> None:
        self.assertIs(
            REQUEST_CAPABILITIES, RemoteProject.Interface.configured_capabilities[0]
        )

    def test_meta_configuration_is_normalized_onto_interface(self) -> None:
        self.assertEqual(
            set(RemoteProject.Interface.fields),
            {"name", "status", "updated_at", "local_name"},
        )
        self.assertIn("status", RemoteProject.Interface.filters)
        self.assertIn("detail", RemoteProject.Interface.query_operations)

    def test_inherited_request_fields_are_preserved(self) -> None:
        class BaseRequest(RequestInterface):
            id = Input(type=int)
            name = RequestField(str)

            class Meta:
                query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                    "detail": RequestQueryOperation(
                        name="detail",
                        method="GET",
                        path="/items/{id}",
                    ),
                    "list": RequestQueryOperation(
                        name="list",
                        method="GET",
                        path="/items",
                    ),
                }

        class DerivedRequest(BaseRequest):
            status = RequestField(str)

        self.assertEqual(set(DerivedRequest.fields), {"name", "status"})

    def test_filter_compiles_remote_request_plan(self) -> None:
        bucket = RemoteProject.filter(
            status="active",
            name__icontains="alp",
            updated_at__gte=datetime(2026, 3, 11, 8, 0, 0),
            page=2,
            page_size=50,
            ordering="-updated_at",
        )

        items = list(bucket)

        self.assertEqual(len(items), 2)
        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(call["operation"], "list")
        self.assertEqual(
            dict(call["plan"].query_params),
            {
                "state": "active",
                "search": "alp",
                "modifiedAfter": datetime(2026, 3, 11, 8, 0, 0),
                "page": 2,
                "pageSize": 50,
                "sort": "-updated_at",
            },
        )

    def test_chained_remote_filter_normalizes_scalar_lookup_values(self) -> None:
        list(RemoteProject.filter(status="active").filter(name__icontains="alp"))

        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(
            dict(call["plan"].query_params),
            {"state": "active", "search": "alp"},
        )

    def test_chained_remote_exclude_normalizes_scalar_lookup_values(self) -> None:
        list(RemoteProject.filter(status="active").exclude(status="inactive"))

        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(
            dict(call["plan"].query_params),
            {"state": "active", "state_not": "inactive"},
        )

    def test_unknown_filter_fails_early(self) -> None:
        with self.assertRaises(UnknownRequestFilterError):
            RemoteProject.filter(foo="bar")

    def test_invalid_filter_type_fails_early(self) -> None:
        with self.assertRaises(InvalidRequestFilterValueError):
            RemoteProject.filter(page="2")

    def test_exclude_rejects_undeclared_negation(self) -> None:
        with self.assertRaises(RequestExcludeNotSupportedError):
            RemoteProject.exclude(name__icontains="beta")

    def test_exclude_uses_declared_remote_negation(self) -> None:
        list(RemoteProject.exclude(status="inactive"))

        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(dict(call["plan"].query_params), {"state_not": "inactive"})

    def test_all_uses_unfiltered_collection_request(self) -> None:
        list(RemoteProject.all())

        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(call["operation"], "list")
        self.assertEqual(dict(call["plan"].query_params), {})

    def test_local_fallback_filters_materialized_payload(self) -> None:
        items = list(RemoteProject.filter(local_name__icontains="alpha"))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "Alpha")
        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(dict(call["plan"].query_params), {})

    def test_request_plan_pickle_preserves_body_and_metadata(self) -> None:
        plan = RequestQueryPlan(
            operation_name="search",
            action="filter",
            method="POST",
            path="/projects/search",
            query_params={"page": 2},
            headers={"X-Test": "1"},
            path_params={"id": 7},
            body={"filters": {"status": "active"}},
            filters={"status": ("active",)},
            excludes={"name": ("Beta",)},
            metadata={"request_id": "req-1"},
        )

        round_tripped = _trusted_pickle_loads(pickle.dumps(plan))

        self.assertEqual(round_tripped, plan)
        self.assertEqual(
            dict(round_tripped.body or {}), {"filters": {"status": "active"}}
        )
        self.assertEqual(dict(round_tripped.metadata), {"request_id": "req-1"})

    def test_len_reflects_current_page_size_not_remote_total(self) -> None:
        bucket = RemoteProject.filter(page=1, page_size=1)

        self.assertEqual(len(bucket), 1)
        self.assertEqual(bucket.count(), 2)

    def test_execute_request_plan_normalizes_transport_response_before_serializing(
        self,
    ) -> None:
        class ResponseTransport:
            def execute(
                self,
                *,
                interface_cls: type[Any],
                operation: Any,
                plan: Any,
                identification: dict[str, Any] | None = None,
            ) -> RequestTransportResponse:
                del interface_cls, operation, plan, identification
                return RequestTransportResponse(
                    payload={"id": 7, "name": "Alpha"},
                    status_code=200,
                )

        class SerializedProject(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail",
                            method="GET",
                            path="/projects/{id}",
                        ),
                        "list": RequestQueryOperation(
                            name="list",
                            method="GET",
                            path="/projects",
                        ),
                    }
                    transport = ResponseTransport()
                    transport_config = RequestTransportConfig(
                        base_url="https://service.example.test"
                    )
                    response_serializer = staticmethod(
                        lambda item: {"id": item["id"], "name": item["name"].upper()}
                    )

        result = SerializedProject.Interface.execute_request_plan(
            RequestQueryPlan(
                operation_name="detail",
                action="detail",
                method="GET",
                path="/projects/{id}",
                path_params={"id": 7},
            )
        )

        self.assertEqual(result.items, ({"id": 7, "name": "ALPHA"},))
        self.assertEqual(result.metadata["status_code"], 200)

    def test_materialized_bucket_filter_still_validates_declared_filters(self) -> None:
        materialized = RemoteProject.filter(status="active")[:1]

        with self.assertRaises(UnknownRequestFilterError):
            materialized.filter(foo="bar")

    def test_materialized_bucket_exclude_still_enforces_supported_negation(
        self,
    ) -> None:
        materialized = RemoteProject.filter(status="active")[:1]

        with self.assertRaises(RequestExcludeNotSupportedError):
            materialized.exclude(name__icontains="beta")

    def test_operation_restricted_filter_fails_on_other_operations(self) -> None:
        list(RemoteProject.filter(list_only="recent"))

        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(dict(call["plan"].query_params), {"listOnly": "recent"})

        with self.assertRaises(UnknownRequestFilterError):
            list(RemoteProject.Interface.query_operation("search", list_only="recent"))

    def test_operation_specific_filters_are_enforced(self) -> None:
        with self.assertRaises(UnknownRequestFilterError):
            RemoteProject.filter(search_only="alpha")

        items = list(
            RemoteProject.Interface.query_operation("search", search_only="alpha")
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(RemoteProject.Interface.calls[-1]["operation"], "search")
        self.assertEqual(
            dict(RemoteProject.Interface.calls[-1]["plan"].body),
            {"q": "alpha"},
        )

    def test_operation_specific_filters_fall_back_to_interface_filters(self) -> None:
        items = list(
            RemoteProject.Interface.query_operation(
                "search",
                search_only="alpha",
                status="active",
            )
        )

        self.assertEqual(len(items), 1)
        plan = RemoteProject.Interface.calls[-1]["plan"]
        self.assertEqual(dict(plan.query_params), {"state": "active"})
        self.assertEqual(dict(plan.body), {"q": "alpha"})

    def test_explicit_empty_operation_filters_do_not_inherit_interface_filters(
        self,
    ) -> None:
        class EmptyFilterProject(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    filters: ClassVar[dict[str, RequestFilter]] = {
                        "status": RequestFilter(remote_name="state", value_type=str),
                    }
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail",
                            method="GET",
                            path="/items/{id}",
                        ),
                        "list": RequestQueryOperation(
                            name="list",
                            method="GET",
                            path="/items",
                        ),
                        "search": RequestQueryOperation(
                            name="search",
                            method="GET",
                            path="/items/search",
                            filters={},
                        ),
                    }

        self.assertEqual(
            EmptyFilterProject.Interface.get_query_operation("search").filters,
            {},
        )

    def test_prefetched_payload_is_used_for_attributes(self) -> None:
        project = RemoteProject.filter(status="active").first()

        self.assertIsNotNone(project)
        assert project is not None
        self.assertEqual(project.name, "Alpha")
        self.assertEqual(len(RemoteProject.Interface.calls), 1)

    def test_ensure_items_marks_truthy_prefetched_data_as_materialized(self) -> None:
        bucket = RemoteProject.filter(status="active")
        project = RemoteProject(id=1)
        project._interface.set_request_payload_cache(
            {
                "id": 1,
                "name": "Alpha",
                "status": "active",
                "updated_at": datetime(2026, 3, 11, 9, 0, 0),
                "local_name": "Alpha Local",
            }
        )
        bucket._data = (project,)
        bucket._materialized = False

        self.assertEqual(bucket._ensure_items(), (project,))
        self.assertTrue(bucket._materialized)

    def test_raw_materialization_executes_without_building_managers(self) -> None:
        bucket = RemoteProject.filter(status="active")

        with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
            raw_items = bucket._ensure_raw_items()

        self.assertEqual([item["id"] for item in raw_items], [1, 2])
        self.assertEqual(bucket.count(), 2)

    def test_request_projection_uses_raw_payload_without_managers(self) -> None:
        bucket = RemoteProject.filter(status="active")

        with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
            result = bucket.values_list("id", "name")

        self.assertEqual(result, ((1, "Alpha"), (2, "Beta")))

    def test_request_projection_preserves_payload_resolution_semantics(self) -> None:
        bucket = PayloadProject.all()

        with patch.object(PayloadProject, "__init__", side_effect=AssertionError):
            result = bucket.values("id", "display_name", "optional_label")

        self.assertEqual(
            result,
            (
                {"id": 1, "display_name": "ALPHA", "optional_label": "UNKNOWN"},
                {"id": 2, "display_name": "BETA", "optional_label": "known"},
            ),
        )
        self.assertEqual(len(PayloadProject.Interface.calls), 1)

    def test_request_projection_preserves_required_payload_errors(self) -> None:
        bucket = PayloadProject.all()
        missing_required = RequestQueryResult(
            items=({"id": 1, "record": {"name": "alpha"}},),
        )

        with patch.object(
            PayloadProject.Interface,
            "execute_request_plan",
            return_value=missing_required,
        ):
            with patch.object(PayloadProject, "__init__", side_effect=AssertionError):
                with self.assertRaises(MissingRequestPayloadFieldError):
                    bucket.values_list("required_label")

    def test_request_projection_applies_local_predicates_and_count_metadata(
        self,
    ) -> None:
        bucket = RemoteProject.filter(local_name__icontains="alpha")

        with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
            result = bucket.values_list("id", "name")

        self.assertEqual(result, ((1, "Alpha"),))
        self.assertEqual(bucket.count(), 1)

    def test_request_projection_rejects_partial_local_pages(self) -> None:
        bucket = RemoteProject.filter(
            local_name__icontains="alpha",
            page=1,
            page_size=1,
        )
        partial_page = RequestQueryResult(
            items=(
                {
                    "id": 1,
                    "name": "Alpha",
                    "status": "active",
                    "updated_at": datetime(2026, 3, 11, 9, 0, 0),
                    "local_name": "Alpha Local",
                },
            ),
            total_count=2,
        )

        with patch.object(
            RemoteProject.Interface,
            "execute_request_plan",
            return_value=partial_page,
        ):
            with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
                with self.assertRaises(RequestLocalPaginationUnsupportedError):
                    bucket.values_list("id")

    def test_request_projection_reuses_one_request_in_active_run(self) -> None:
        bucket = RemoteProject.filter(status="active")

        with CalculationRunContext():
            with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
                self.assertEqual(
                    bucket.values("id", "name"),
                    ({"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}),
                )
                self.assertEqual(
                    bucket.values_list("id", "name"),
                    ((1, "Alpha"), (2, "Beta")),
                )

        self.assertEqual(len(RemoteProject.Interface.calls), 1)

    def test_equivalent_lazy_request_buckets_share_one_active_run_projection(
        self,
    ) -> None:
        first = RemoteProject.filter(status="active")
        second = RemoteProject.filter(status="active")

        with CalculationRunContext():
            self.assertEqual(
                first.values_list("id", "name"),
                ((1, "Alpha"), (2, "Beta")),
            )
            self.assertEqual(
                second.values("id", "name"),
                (
                    {"id": 1, "name": "Alpha"},
                    {"id": 2, "name": "Beta"},
                ),
            )

        self.assertEqual(len(RemoteProject.Interface.calls), 1)

    def test_serialized_manager_items_with_retained_plan_use_portable_projection(
        self,
    ) -> None:
        source = RemoteProject.filter(status="active")
        items = tuple(source)
        serialized = RequestBucket(
            RemoteProject,
            RemoteProject.Interface,
            request_plan=source.request_plan,
            items=items,
        )
        restored = _trusted_pickle_loads(pickle.dumps(serialized))
        calls_before_projection = len(RemoteProject.Interface.calls)

        self.assertEqual(
            restored.values_list("id"),
            ((1,), (2,)),
        )
        self.assertEqual(len(RemoteProject.Interface.calls), calls_before_projection)

    def test_serialized_empty_manager_items_with_retained_plan_do_not_collide(
        self,
    ) -> None:
        broad = RemoteProject.all()
        serialized = RequestBucket(
            RemoteProject,
            RemoteProject.Interface,
            request_plan=broad.request_plan,
            items=(),
        )
        restored = _trusted_pickle_loads(pickle.dumps(serialized))
        self.assertNotEqual(
            restored._bucket_index_source_signature(),
            broad._bucket_index_source_signature(),
        )

        with CalculationRunContext():
            self.assertEqual(restored.values_list("id", "name"), ())
            self.assertEqual(
                broad.values_list("id", "name"),
                ((1, "Alpha"), (2, "Beta")),
            )

        self.assertEqual(len(RemoteProject.Interface.calls), 1)

    def test_pickled_unmaterialized_plan_is_conservative_after_restore(self) -> None:
        lazy = RemoteProject.all()
        restored = _trusted_pickle_loads(pickle.dumps(lazy))
        broad = RemoteProject.all()

        with CalculationRunContext():
            self.assertEqual(restored.values_list("id"), ())
            self.assertEqual(broad.values_list("id"), ((1,), (2,)))

        self.assertEqual(len(RemoteProject.Interface.calls), 1)

    def test_request_projection_rejects_unsupported_historical_reads_before_raw_access(
        self,
    ) -> None:
        bucket = RemoteProject.filter(status="active")

        with as_of("2022-01-01"):
            with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
                with self.assertRaises(HistoricalReadNotSupportedError):
                    bucket.values_list("id")

        self.assertEqual(RemoteProject.Interface.calls, [])

    def test_request_native_dependencies_match_portable_projection(self) -> None:
        with DependencyTracker() as native_dependencies:
            native_bucket = RemoteProject.filter(status="active")
            native_result = native_bucket.values_list("id", "name")

        with DependencyTracker() as portable_dependencies:
            portable_source = RemoteProject.filter(status="active")
            portable_items = tuple(portable_source)
            portable_bucket = portable_source.with_instances(portable_items)
            portable_result = portable_bucket.values_list("id", "name")

        self.assertEqual(native_result, portable_result)
        self.assertEqual(native_dependencies, portable_dependencies)

    def test_materialized_request_subset_uses_portable_projection(self) -> None:
        source = RemoteProject.filter(status="active")
        items = tuple(source)
        subset = source.with_instances(items[:1])

        self.assertEqual(subset.values("name"), ({"name": items[0].name},))

    def test_request_property_projection_falls_back_to_managers(self) -> None:
        class PropertyProject(GeneralManager):
            class Interface(RemoteProject.Interface):
                pass

            @GraphQLProperty
            def display_name(self) -> str:
                return self.name.upper()

        PropertyProject._attributes = PropertyProject.Interface.get_attributes()
        GeneralManagerMeta.create_at_properties_for_attributes(
            PropertyProject._attributes.keys(),
            PropertyProject,
        )

        bucket = PropertyProject.filter(status="active")
        init_calls = 0
        original_init = PropertyProject.__init__

        def counting_init(instance: object, *args: object, **kwargs: object) -> None:
            nonlocal init_calls
            init_calls += 1
            original_init(instance, *args, **kwargs)

        with patch.object(
            PropertyProject,
            "__init__",
            autospec=True,
            side_effect=counting_init,
        ):
            result = bucket.values_list("id", "display_name")

        self.assertEqual(result, ((1, "ALPHA"), (2, "BETA")))
        self.assertEqual(init_calls, 2)

    def test_empty_raw_materialization_is_cached_without_rerunning_plan(self) -> None:
        bucket = RemoteProject.all()

        with patch.object(
            RemoteProject.Interface,
            "execute_request_plan",
            return_value=RequestQueryResult(items=()),
        ) as execute_plan:
            self.assertEqual(bucket._ensure_raw_items(), ())
            self.assertTrue(bucket._materialized)
            self.assertEqual(bucket._ensure_raw_items(), ())

        execute_plan.assert_called_once()

    def test_raw_materialization_applies_local_predicates_and_preserves_count(
        self,
    ) -> None:
        matching_payload = {
            "id": 1,
            "name": "Alpha",
            "status": "active",
            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
            "local_name": "Alpha Local",
        }
        excluded_payload = {
            "id": 2,
            "name": "Beta",
            "status": "inactive",
            "updated_at": datetime(2026, 3, 10, 9, 0, 0),
            "local_name": "Beta Local",
        }
        bucket = RemoteProject.filter(local_name__icontains="alpha")

        with patch.object(
            RemoteProject.Interface,
            "execute_request_plan",
            return_value=RequestQueryResult(
                items=(matching_payload, excluded_payload),
                total_count=2,
            ),
        ) as execute_plan:
            with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
                raw_items = bucket._ensure_raw_items()
                self.assertEqual(raw_items, (matching_payload,))
                self.assertIs(raw_items[0], matching_payload)
                self.assertEqual(bucket._count_override, 1)

        self.assertEqual(bucket.count(), 1)
        execute_plan.assert_called_once()

    def test_raw_materialization_rejects_partial_local_page_without_managers(
        self,
    ) -> None:
        partial_payload = {
            "id": 1,
            "name": "Alpha",
            "status": "active",
            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
            "local_name": "Alpha Local",
        }
        bucket = RemoteProject.filter(
            local_name__icontains="alpha",
            page=1,
            page_size=1,
        )

        with patch.object(
            RemoteProject.Interface,
            "execute_request_plan",
            return_value=RequestQueryResult(
                items=(partial_payload,),
                total_count=2,
            ),
        ) as execute_plan:
            with patch.object(RemoteProject, "__init__", side_effect=AssertionError):
                with self.assertRaises(RequestLocalPaginationUnsupportedError):
                    bucket._ensure_raw_items()

        execute_plan.assert_called_once()

    def test_nonempty_raw_materialization_reuses_one_request_for_raw_and_managers(
        self,
    ) -> None:
        payload = {
            "id": 1,
            "name": "Alpha",
            "status": "active",
            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
            "local_name": "Alpha Local",
        }
        bucket = RemoteProject.filter(status="active")

        with patch.object(
            RemoteProject.Interface,
            "execute_request_plan",
            return_value=RequestQueryResult(items=(payload,), total_count=1),
        ) as execute_plan:
            first_raw_items = bucket._ensure_raw_items()
            second_raw_items = bucket._ensure_raw_items()
            first_items = bucket._ensure_items()
            second_items = bucket._ensure_items()

        self.assertIs(first_raw_items, second_raw_items)
        self.assertEqual(first_raw_items, (payload,))
        self.assertIs(first_raw_items[0], payload)
        self.assertIs(first_items, second_items)
        self.assertEqual([item.identification for item in first_items], [{"id": 1}])
        execute_plan.assert_called_once()

    def test_request_bucket_hydrates_items_from_raw_items(self) -> None:
        payload = {
            "id": 1,
            "name": "Alpha",
            "status": "active",
            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
            "local_name": "Alpha Local",
        }
        bucket = RequestBucket(
            RemoteProject,
            RemoteProject.Interface,
            raw_items=(payload,),
        )

        item = bucket.first()

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.name, "Alpha")
        self.assertTrue(bucket._materialized)

        round_tripped = _trusted_pickle_loads(pickle.dumps(bucket))
        restored = round_tripped.first()

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(restored.name, "Alpha")
        self.assertEqual(restored._interface._request_payload_cache["name"], "Alpha")

    def test_materialized_request_bucket_indexes_keep_distinct_payloads(self) -> None:
        """Keep indexes for separately materialized request buckets isolated."""
        alpha_payload = {
            "id": 1,
            "name": "Alpha",
            "status": "active",
            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
            "local_name": "Alpha Local",
        }
        beta_payload = {
            "id": 1,
            "name": "Beta",
            "status": "active",
            "updated_at": datetime(2026, 3, 11, 9, 0, 0),
            "local_name": "Beta Local",
        }
        alpha_bucket = RequestBucket(
            RemoteProject,
            RemoteProject.Interface,
            raw_items=(alpha_payload,),
        )
        beta_bucket = RequestBucket(
            RemoteProject,
            RemoteProject.Interface,
            raw_items=(beta_payload,),
        )

        with CalculationRunContext():
            alpha_index = alpha_bucket.index_by("name")
            beta_index = beta_bucket.index_by("name")

        self.assertEqual(sorted(alpha_index), ["Alpha"])
        self.assertEqual(sorted(beta_index), ["Beta"])
        self.assertIsNot(alpha_index, beta_index)

    def test_all_preserves_existing_request_filters(self) -> None:
        bucket = RemoteProject.filter(status="active")

        list(bucket.all())

        call = RemoteProject.Interface.calls[-1]
        self.assertEqual(dict(call["plan"].query_params), {"state": "active"})

    def test_lookup_normalization_preserves_tuple_arity(self) -> None:
        self.assertEqual(
            RequestBucket._normalize_lookup_kwargs({"page": (1, 2)}),
            {"page": (1, 2)},
        )

    def test_direct_manager_read_uses_detail_operation(self) -> None:
        project = RemoteProject(id=5)

        self.assertEqual(project.name, "Detail Alpha")
        self.assertEqual(RemoteProject.Interface.calls[-1]["operation"], "detail")
        self.assertEqual(
            dict(RemoteProject.Interface.calls[-1]["plan"].path_params),
            {"id": 5},
        )

    def test_direct_manager_read_raises_for_missing_detail_item(self) -> None:
        with self.assertRaises(AttributeEvaluationError) as error:
            _ = RemoteProject(id=-1).name

        self.assertIsInstance(
            error.exception.__cause__, RequestSingleResponseRequiredError
        )

    def test_direct_manager_read_raises_for_multiple_detail_items(self) -> None:
        with self.assertRaises(AttributeEvaluationError) as error:
            _ = RemoteProject(id=99).name

        self.assertIsInstance(
            error.exception.__cause__, RequestSingleResponseRequiredError
        )

    def test_local_fallback_rejects_partial_remote_pages(self) -> None:
        with self.assertRaises(RequestLocalPaginationUnsupportedError):
            list(
                RemoteProject.filter(local_name__icontains="alpha", page=1, page_size=1)
            )
