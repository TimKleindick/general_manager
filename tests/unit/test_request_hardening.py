from __future__ import annotations

from typing import ClassVar

from django.test import SimpleTestCase

from general_manager.bucket.request_bucket import (
    RequestBucketManagerMismatchError,
    RequestBucketSortAttributeError,
    RequestBucketTypeMismatchError,
)
from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    InvalidRequestFilterConfigurationError,
    MissingRequestDetailOperationError,
    RequestConfigurationError,
    RequestField,
    RequestFilter,
    RequestMutationOperation,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestRetryPolicy,
    UnknownRequestFilterOperationReferenceError,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta


class EqualityProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)
        status = RequestField(str)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "status": RequestFilter(remote_name="state", value_type=str),
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
            }

        @classmethod
        def execute_request_plan(cls, plan: RequestQueryPlan) -> RequestQueryResult:
            if plan.operation_name == "detail":
                return RequestQueryResult(
                    items=(
                        {
                            "id": plan.path_params["id"],
                            "name": "Detail",
                            "status": "active",
                        },
                    )
                )

            status = plan.query_params.get("state", "active")
            return RequestQueryResult(
                items=(
                    {
                        "id": 1 if status == "active" else 2,
                        "name": "Alpha" if status == "active" else "Beta",
                        "status": status,
                    },
                )
            )


class OtherEqualityProject(GeneralManager):
    class Interface(RequestInterface):
        id = Input(type=int)

        name = RequestField(str)

        class Meta:
            filters: ClassVar[dict[str, RequestFilter]] = {
                "name": RequestFilter(remote_name="name", value_type=str),
            }
            query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                "detail": RequestQueryOperation(
                    name="detail",
                    method="GET",
                    path="/other/{id}",
                ),
                "list": RequestQueryOperation(
                    name="list",
                    method="GET",
                    path="/other",
                ),
            }

        @classmethod
        def execute_request_plan(cls, plan: RequestQueryPlan) -> RequestQueryResult:
            return RequestQueryResult(items=({"id": 1, "name": "Other"},))


for manager_cls in (EqualityProject, OtherEqualityProject):
    manager_cls._attributes = manager_cls.Interface.get_attributes()
    GeneralManagerMeta.create_at_properties_for_attributes(
        manager_cls._attributes.keys(),
        manager_cls,
    )


class RequestValidationCapabilityTests(SimpleTestCase):
    def test_legacy_fields_declaration_is_rejected(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class LegacyFieldProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    fields: ClassVar[dict[str, RequestField]] = {
                        "name": RequestField(str)
                    }

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

    def test_legacy_filters_declaration_is_rejected(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class LegacyFilterProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)
                    filters: ClassVar[dict[str, RequestFilter]] = {
                        "status": RequestFilter(remote_name="state", value_type=str)
                    }

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

    def test_legacy_transport_declaration_is_rejected(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class LegacyTransportProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)
                    transport = object()

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

    def test_validation_requires_detail_operation(self) -> None:
        with self.assertRaises(MissingRequestDetailOperationError):

            class MissingDetailProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                            "list": RequestQueryOperation(
                                name="list",
                                method="GET",
                                path="/items",
                            )
                        }

    def test_validation_rejects_unknown_filter_operation_reference(self) -> None:
        with self.assertRaises(UnknownRequestFilterOperationReferenceError):

            class UnknownOperationFilterProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        filters: ClassVar[dict[str, RequestFilter]] = {
                            "status": RequestFilter(
                                remote_name="state",
                                operation_names=frozenset({"search"}),
                            )
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
                        }

    def test_validation_rejects_local_only_filter_without_fallback(self) -> None:
        with self.assertRaises(InvalidRequestFilterConfigurationError):

            class InvalidLocalFilterProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        filters: ClassVar[dict[str, RequestFilter]] = {
                            "name__icontains": RequestFilter(value_type=str)
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
                        }

    def test_validation_rejects_exclude_param_without_exclude_support(self) -> None:
        with self.assertRaises(InvalidRequestFilterConfigurationError):

            class InvalidExcludeFilterProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        filters: ClassVar[dict[str, RequestFilter]] = {
                            "status": RequestFilter(
                                remote_name="state",
                                exclude_remote_name="state_not",
                            )
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
                        }

    def test_validation_rejects_duplicate_operation_filter_keys(self) -> None:
        with self.assertRaises(InvalidRequestFilterConfigurationError):

            class DuplicateOperationFilterProject(GeneralManager):
                class Interface(RequestInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        filters: ClassVar[dict[str, RequestFilter]] = {
                            "status": RequestFilter(
                                remote_name="state",
                                value_type=str,
                            )
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
                                filters={
                                    "status": RequestFilter(
                                        remote_name="override",
                                        value_type=str,
                                    )
                                },
                            ),
                        }

    def test_validation_rejects_rules_without_mutation_operations(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class InvalidRulesProject(GeneralManager):
                class Interface(RequestInterface):
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
                        rules: ClassVar[list[object]] = ["not-a-real-rule"]

    def test_validation_rejects_non_callable_serializer(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class InvalidSerializerProject(GeneralManager):
                class Interface(RequestInterface):
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
                        create_operation = RequestMutationOperation(
                            name="create",
                            method="POST",
                            path="/items",
                        )
                        create_serializer = "not-callable"

    def test_validation_rejects_invalid_auth_provider(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class InvalidAuthProviderProject(GeneralManager):
                class Interface(RequestInterface):
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
                        auth_provider = object()

    def test_validation_rejects_invalid_retry_policy(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class InvalidRetryPolicyProject(GeneralManager):
                class Interface(RequestInterface):
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
                        retry_policy = RequestRetryPolicy(max_attempts=0)


class RequestBucketHardeningTests(SimpleTestCase):
    def test_request_bucket_equality_uses_request_plan(self) -> None:
        left = EqualityProject.filter(status="active")
        right = EqualityProject.filter(status="active")
        different = EqualityProject.filter(status="inactive")

        self.assertEqual(left, right)
        self.assertNotEqual(left, different)

    def test_request_bucket_equality_survives_materialization(self) -> None:
        left = EqualityProject.filter(status="active")
        right = EqualityProject.filter(status="active")

        list(left)

        self.assertEqual(left, right)

    def test_request_bucket_union_rejects_incompatible_bucket_types(self) -> None:
        bucket = EqualityProject.filter(status="active")

        with self.assertRaises(RequestBucketTypeMismatchError):
            _ = bucket | object()

    def test_request_bucket_union_rejects_manager_mismatch(self) -> None:
        left = EqualityProject.filter(status="active")
        right = OtherEqualityProject.filter(name="Other")

        with self.assertRaises(RequestBucketManagerMismatchError):
            _ = left | right

    def test_request_bucket_sort_reports_missing_attribute(self) -> None:
        bucket = EqualityProject.filter(status="active")

        with self.assertRaises(RequestBucketSortAttributeError) as context:
            bucket.sort("missing")

        self.assertIn("missing", str(context.exception))
        self.assertIn("EqualityProject", str(context.exception))

    def test_request_interface_clone_preserves_mutation_capabilities(self) -> None:
        class MutationProject(GeneralManager):
            class Interface(RequestInterface):
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
                    create_operation = RequestMutationOperation(
                        name="create",
                        method="POST",
                        path="/items",
                    )
                    update_operation = RequestMutationOperation(
                        name="update",
                        method="PATCH",
                        path="/items/{id}",
                    )
                    delete_operation = RequestMutationOperation(
                        name="delete",
                        method="DELETE",
                        path="/items/{id}",
                    )

        self.assertEqual(
            MutationProject.Interface.get_mutation_operation("create").path,
            "/items",
        )
        self.assertEqual(
            MutationProject.Interface.get_mutation_operation("update").path,
            "/items/{id}",
        )
        self.assertEqual(
            MutationProject.Interface.get_mutation_operation("delete").path,
            "/items/{id}",
        )
        self.assertIsNotNone(MutationProject.Interface.get_capability_handler("create"))
        self.assertIsNotNone(MutationProject.Interface.get_capability_handler("update"))
        self.assertIsNotNone(MutationProject.Interface.get_capability_handler("delete"))
