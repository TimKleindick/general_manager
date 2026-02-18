# type: ignore

import json
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db.models import CharField, FloatField
from django.test import override_settings
from django.utils.crypto import get_random_string

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@override_settings(
    GENERAL_MANAGER={
        "MCP_GATEWAY": {
            "ENABLED": True,
            "AI_ASSISTANT": {"PLANNER": "rule_based"},
            "DOMAINS": {
                "Project": {
                    "manager": "Project",
                    "readable_fields": ["id", "name", "status", "budget"],
                    "filterable_fields": ["status", "name"],
                    "sortable_fields": ["name", "status"],
                    "aggregate_fields": ["budget"],
                }
            },
        }
    }
)
class TestMCPGatewayHTTP(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=120)
                status = CharField(max_length=40)
                budget = FloatField(default=0.0)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["isAuthenticated"]

        cls.general_manager_classes = [Project]
        cls.Project = Project

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="mcp-user", password=password
        )
        self.client.login(username="mcp-user", password=password)

        self.Project.create(
            name="Alpha", status="active", budget=10, ignore_permission=True
        )
        self.Project.create(
            name="Beta", status="archived", budget=30, ignore_permission=True
        )

    def test_query_endpoint_returns_filtered_rows(self):
        payload = {
            "domain": "Project",
            "operation": "query",
            "select": ["id", "name", "status"],
            "filters": [{"field": "status", "op": "eq", "value": "active"}],
            "page": 1,
            "page_size": 50,
        }

        response = self.client.post(
            "/ai/query",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["errors"])
        self.assertEqual(len(body["data"]["rows"]), 1)
        self.assertEqual(body["data"]["rows"][0]["status"], "active")

    def test_aggregate_endpoint_returns_metrics(self):
        payload = {
            "domain": "Project",
            "operation": "aggregate",
            "select": ["id", "budget"],
            "metrics": [
                {"field": "budget", "op": "sum", "alias": "budget_sum"},
                {"field": "id", "op": "count", "alias": "row_count"},
            ],
        }

        response = self.client.post(
            "/ai/query",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["errors"])
        self.assertEqual(body["data"]["aggregates"]["budget_sum"], 40.0)
        self.assertEqual(body["data"]["aggregates"]["row_count"], 2)

    def test_unauthenticated_request_is_rejected(self):
        self.client.logout()

        response = self.client.post(
            "/ai/query",
            data=json.dumps(
                {
                    "domain": "Project",
                    "operation": "query",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        body = response.json()
        self.assertEqual(body["errors"][0]["code"], "UNAUTHENTICATED")

    def test_allowlist_violation_is_rejected(self):
        payload = {
            "domain": "Project",
            "operation": "query",
            "select": ["id", "unknown_field"],
        }

        response = self.client.post(
            "/ai/query",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["errors"][0]["code"], "FIELD_NOT_ALLOWED")

    def test_chat_endpoint_returns_answer(self):
        response = self.client.post(
            "/ai/chat",
            data=json.dumps({"question": 'Show projects with name "Alpha"'}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("answer", body)
        self.assertIn("query_request", body)
        self.assertEqual(body["query_request"]["operation"], "query")
