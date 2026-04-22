from __future__ import annotations

from typing import Any, ClassVar

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.test import override_settings
from django.utils.crypto import get_random_string

from general_manager.api.mutation import graph_ql_mutation
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)
from general_manager.permission.mutation_permission import MutationPermission
from general_manager.permission.graphql_capabilities import (
    mutation_capability,
    object_capability,
    permission_capability,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase

CapabilityProjectForMutation = GeneralManager


class TestCapabilitiesProvider:
    graphql_fields: ClassVar[dict[str, type]] = {"username": str}
    graphql_capabilities: ClassVar[tuple] = (
        object_capability(
            "canOpenAdmin",
            lambda _current_user, request_user: request_user.is_staff,
        ),
    )

    def resolve_username(self, user: Any, info: Any) -> str:
        return user.username


class TestGraphQLPermissionCapabilities(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rename_calls = 0
        cls.batch_calls = 0

        def can_rename(project: Any, user: Any) -> bool:
            cls.rename_calls += 1
            return project.status == "draft" and user.is_authenticated

        def can_rename_batch(projects: list[Any], user: Any) -> list[bool]:
            cls.batch_calls += 1
            return [
                project.status == "draft" and user.is_authenticated
                for project in projects
            ]

        class ArchiveProjectPermission(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAuthenticated"]
            status: ClassVar[list[str]] = ["matches:status:draft"]

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                status = CharField(max_length=20)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["isAuthenticated"]
                __delete__: ClassVar[list[str]] = ["isAuthenticated"]
                graphql_capabilities = (
                    object_capability(
                        "canRename",
                        can_rename,
                        batch_evaluator=can_rename_batch,
                    ),
                )

        globals()["CapabilityProjectForMutation"] = Project

        @graph_ql_mutation(permission=ArchiveProjectPermission)
        def archive_project(info: Any, status: str) -> CapabilityProjectForMutation:
            del info
            return Project(name="preview", status=status)

        Project.Permission.graphql_capabilities = (
            *Project.Permission.graphql_capabilities,
            permission_capability(
                Project,
                "update",
                name="canUpdateProject",
            ),
            mutation_capability(
                archive_project,
                name="canArchiveProject",
                payload=lambda project, _user: {"status": project.status},
            ),
        )
        cls.Project = Project
        cls.general_manager_classes = [Project]

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="capability-user",
            password=password,
        )
        self.client.login(username="capability-user", password=password)
        self.Project.create(
            creator_id=None,
            name="Apollo",
            status="draft",
            ignore_permission=True,
        )
        self.Project.create(
            creator_id=None,
            name="Zeus",
            status="locked",
            ignore_permission=True,
        )
        type(self).rename_calls = 0
        type(self).batch_calls = 0

    def test_object_capabilities_are_exposed_on_list_items_and_batch_warmed(
        self,
    ) -> None:
        query = """
        query {
            projectList(sortBy: name) {
                items {
                    name
                    capabilities {
                        canRename
                    }
                }
            }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual(
            items,
            [
                {"name": "Apollo", "capabilities": {"canRename": True}},
                {"name": "Zeus", "capabilities": {"canRename": False}},
            ],
        )
        self.assertEqual(type(self).batch_calls, 1)
        self.assertEqual(type(self).rename_calls, 0)

    def test_permission_and_mutation_capability_helpers_are_exposed(self) -> None:
        query = """
        query {
            projectList(sortBy: name) {
                items {
                    name
                    capabilities {
                        canUpdateProject
                        canArchiveProject
                    }
                }
            }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["projectList"]["items"]
        self.assertEqual(
            items,
            [
                {
                    "name": "Apollo",
                    "capabilities": {
                        "canUpdateProject": True,
                        "canArchiveProject": True,
                    },
                },
                {
                    "name": "Zeus",
                    "capabilities": {
                        "canUpdateProject": True,
                        "canArchiveProject": False,
                    },
                },
            ],
        )

    def test_list_query_does_not_warm_capabilities_when_unselected(self) -> None:
        query = """
        query {
            projectList(sortBy: name) {
                items {
                    name
                }
            }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        self.assertEqual(type(self).batch_calls, 0)
        self.assertEqual(type(self).rename_calls, 0)


class TestGraphQLCurrentUserCapabilities(GeneralManagerTransactionTestCase):
    general_manager_classes: ClassVar[list[type[GeneralManager]]] = []

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings_override = override_settings(
            GENERAL_MANAGER={
                "GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER": (
                    "tests.integration.test_graphql_permission_capabilities."
                    "TestCapabilitiesProvider"
                )
            }
        )
        cls.settings_override.enable()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.settings_override.disable()

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="staff-user",
            password=password,
            is_staff=True,
        )
        self.client.login(username="staff-user", password=password)

    def test_provider_exposes_me_capabilities(self) -> None:
        query = """
        query {
            me {
                username
                capabilities {
                    canOpenAdmin
                }
            }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["me"],
            {
                "username": "staff-user",
                "capabilities": {"canOpenAdmin": True},
            },
        )
