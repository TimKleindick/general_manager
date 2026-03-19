from __future__ import annotations

from datetime import datetime
import pickle
from typing import Any, ClassVar

from django.test import SimpleTestCase

from general_manager.bucket.request_bucket import RequestBucket
from general_manager.interface.bundles import REQUEST_CAPABILITIES
from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    InvalidRequestFilterValueError,
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


def _trusted_pickle_loads(data: bytes) -> Any:
    return pickle.loads(data)  # noqa: S301 - test data is created locally


class TestRequestInterface(SimpleTestCase):
    def setUp(self) -> None:
        RemoteProject.Interface.calls.clear()

    def test_filter_returns_request_bucket(self) -> None:
        bucket = RemoteProject.filter(status="active")

        self.assertIsInstance(bucket, RequestBucket)

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
