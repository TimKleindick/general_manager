from __future__ import annotations

import json
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db.models import BooleanField, CharField, IntegerField
from django.test import RequestFactory

from general_manager.api.remote_api import _build_query_view, get_remote_api_config
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class RemoteAPIReadPermissionTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class RemoteTenantRecord(GeneralManager):
            secret_evaluations: ClassVar[list[int]] = []

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                tenant_id = IntegerField()
                visible = BooleanField(default=True)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["isAuthenticated"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                secret: ClassVar[dict[str, list[str]]] = {"read": ["isAdmin"]}

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    if not self.request_user.is_authenticated:
                        return ReadPermissionPlan([], False, decision="deny_all")
                    return ReadPermissionPlan(
                        filters=[{"filter": {"tenant_id": self.request_user.pk}}],
                        requires_instance_check=True,
                    )

                def can_read_instance(self) -> bool:
                    # Tenant filtering is deliberately left to the read plan.
                    return bool(self.instance.visible)

            class RemoteAPI:
                enabled = True
                resource_name = "tenant-records"
                allow_filter = True
                allow_detail = True
                allow_create = True
                allow_update = True

            @property
            def secret(self) -> str:
                self.secret_evaluations.append(self.id)
                return "protected-value"

        cls.Record = RemoteTenantRecord

        class RemoteExcludedRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    return ReadPermissionPlan(
                        filters=[{"exclude": {"name__iexact": "secret"}}],
                        requires_instance_check=False,
                    )

            class RemoteAPI:
                enabled = True
                resource_name = "excluded-records"
                allow_filter = True
                allow_detail = True
                allow_create = True
                allow_update = True

        cls.ExcludedRecord = RemoteExcludedRecord
        cls.general_manager_classes = [RemoteTenantRecord, RemoteExcludedRecord]

    def setUp(self) -> None:
        super().setUp()
        self.user = get_user_model().objects.create_user(username="remote-reader")
        self.other_user = get_user_model().objects.create_user(username="other-reader")
        self.client.force_login(self.user)
        self.foreign = self.Record.create(
            ignore_permission=True, name="A foreign", tenant_id=self.other_user.pk
        )
        self.hidden = self.Record.create(
            ignore_permission=True,
            name="B hidden",
            tenant_id=self.user.pk,
            visible=False,
        )
        self.visible = self.Record.create(
            ignore_permission=True, name="C visible", tenant_id=self.user.pk
        )
        self.Record.secret_evaluations.clear()

    def query_records(self, **controls: object):
        return self.client.post(
            "/gm/tenant-records/query", data=controls, content_type="application/json"
        )

    def test_query_authorizes_before_count_and_pagination(self) -> None:
        response = self.query_records(page=1, page_size=1, ordering="name")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_count"], 1)
        self.assertEqual(
            [item["id"] for item in response.json()["items"]], [self.visible.id]
        )

    def test_default_page_is_bounded_after_row_authorization(self) -> None:
        for index in range(12):
            self.Record.create(
                ignore_permission=True,
                name=f"D visible {index}",
                tenant_id=self.user.pk,
            )
        response = self.query_records(ordering="name")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 10)
        self.assertEqual(payload["total_count"], 13)
        self.assertEqual(payload["metadata"]["page_size"], 10)
        self.assertNotIn(self.hidden.id, [item["id"] for item in payload["items"]])
        self.assertNotIn(self.foreign.id, [item["id"] for item in payload["items"]])

    def test_query_applies_the_requesting_users_tenant_filter(self) -> None:
        self.client.force_login(self.other_user)

        response = self.query_records()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_count"], 1)
        self.assertEqual(
            [item["id"] for item in response.json()["items"]], [self.foreign.id]
        )

    def test_detail_applies_prefilter_and_instance_check(self) -> None:
        for record in (self.foreign, self.hidden):
            with self.subTest(record=record.id):
                response = self.client.get(f"/gm/tenant-records/{record.id}")

                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error_code"], "permission_denied")
                self.assertNotIn("protected-value", response.content.decode())

    def test_read_responses_do_not_evaluate_denied_properties(self) -> None:
        for path, method in (
            ("/gm/tenant-records/query", "post"),
            (f"/gm/tenant-records/{self.visible.id}", "get"),
        ):
            with self.subTest(path=path):
                response = getattr(self.client, method)(
                    path, data={}, content_type="application/json"
                )

                self.assertEqual(response.status_code, 200)
                self.assertNotIn("secret", response.json()["items"][0])
                self.assertEqual(self.Record.secret_evaluations, [])

    def test_anonymous_requests_cannot_read_restricted_records(self) -> None:
        self.client.logout()

        response = self.query_records()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(response.json()["total_count"], 0)
        detail = self.client.get(f"/gm/tenant-records/{self.visible.id}")
        self.assertEqual(detail.status_code, 403)
        self.assertEqual(self.Record.secret_evaluations, [])

    def test_missing_request_user_uses_anonymous_policy(self) -> None:
        config = get_remote_api_config(self.Record)
        assert config is not None
        request = RequestFactory().post(
            "/gm/tenant-records/query", data={}, content_type="application/json"
        )

        response = _build_query_view(config)(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["items"], [])

    def test_create_response_checks_field_permission_before_evaluation(self) -> None:
        response = self.client.post(
            "/gm/tenant-records",
            data={"name": "Created", "tenant_id": self.user.pk},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["items"][0]["name"], "Created")
        self.assertNotIn("secret", response.json()["items"][0])
        self.assertEqual(self.Record.secret_evaluations, [])

    def test_update_response_does_not_evaluate_denied_fields_again(self) -> None:
        response = self.client.patch(
            f"/gm/tenant-records/{self.visible.id}",
            data={"name": "Updated"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["name"], "Updated")
        self.assertNotIn("secret", response.json()["items"][0])
        # The mutation permission adapter reads the existing state once;
        # response serialization must not perform a second protected read.
        self.assertEqual(self.Record.secret_evaluations, [self.visible.id])

    def test_successful_writes_do_not_return_unreadable_records(self) -> None:
        created = self.client.post(
            "/gm/tenant-records",
            data={"name": "Foreign created", "tenant_id": self.other_user.pk},
            content_type="application/json",
        )
        updated = self.client.patch(
            f"/gm/tenant-records/{self.visible.id}",
            data={"tenant_id": self.other_user.pk},
            content_type="application/json",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["items"], [])
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["items"], [])
        self.assertEqual(self.Record(id=self.visible.id).tenant_id, self.other_user.pk)

    def test_detail_preserves_database_exclusion_lookup_semantics(self) -> None:
        record = self.ExcludedRecord.create(ignore_permission=True, name="SECRET")

        query = self.client.post(
            "/gm/excluded-records/query", data={}, content_type="application/json"
        )
        detail = self.client.get(f"/gm/excluded-records/{record.id}")

        self.assertEqual(query.json()["items"], [])
        self.assertEqual(detail.status_code, 403)

    def test_create_response_preserves_database_exclusion_lookup_semantics(
        self,
    ) -> None:
        response = self.client.post(
            "/gm/excluded-records",
            data={"name": "SECRET"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["items"], [])

    def test_update_response_preserves_database_exclusion_lookup_semantics(
        self,
    ) -> None:
        record = self.ExcludedRecord.create(ignore_permission=True, name="Public")

        response = self.client.patch(
            f"/gm/excluded-records/{record.id}",
            data={"name": "SECRET"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
