from __future__ import annotations

from django.test import SimpleTestCase

from general_manager.api.remote_api import (
    RemoteAPIConfigurationError,
    build_remote_api_registry,
)
from general_manager.interface import RemoteManagerInterface
from general_manager.interface.requests import RequestConfigurationError, RequestField
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class RemoteAPIRegistryTests(SimpleTestCase):
    def test_duplicate_base_path_and_resource_name_is_rejected(self) -> None:
        class FirstProject(GeneralManager):
            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "projects"

        class SecondProject(GeneralManager):
            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "projects"

        with self.assertRaises(RemoteAPIConfigurationError):
            build_remote_api_registry([FirstProject, SecondProject])

    def test_invalid_base_path_is_rejected(self) -> None:
        class Project(GeneralManager):
            class RemoteAPI:
                enabled = True
                base_path = "/internal//gm"
                resource_name = "projects"

        with self.assertRaises(RemoteAPIConfigurationError):
            build_remote_api_registry([Project])

    def test_enabled_remote_api_requires_at_least_one_allowed_operation(self) -> None:
        class Project(GeneralManager):
            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "projects"

        with self.assertRaises(RemoteAPIConfigurationError):
            build_remote_api_registry([Project])

    def test_websocket_invalidation_requires_mutation_operation(self) -> None:
        class Project(GeneralManager):
            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "projects"
                allow_filter = True
                websocket_invalidation = True

        with self.assertRaises(RemoteAPIConfigurationError):
            build_remote_api_registry([Project])


class RemoteManagerInterfaceValidationTests(SimpleTestCase):
    def test_default_base_path_is_gm(self) -> None:
        class RemoteProject(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    base_url = "http://testserver"
                    remote_manager = "projects"
                    protocol_version = "v1"

        self.assertEqual(
            RemoteProject.Interface.get_query_operation("detail").path,
            "/gm/projects/{id}",
        )

    def test_missing_remote_manager_is_rejected_at_class_definition(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class InvalidRemoteProject(GeneralManager):
                class Interface(RemoteManagerInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        base_url = "http://testserver"
                        protocol_version = "v1"

    def test_invalid_base_url_is_rejected_at_class_definition(self) -> None:
        with self.assertRaises(RequestConfigurationError):

            class InvalidRemoteProject(GeneralManager):
                class Interface(RemoteManagerInterface):
                    id = Input(type=int)
                    name = RequestField(str)

                    class Meta:
                        base_url = "ftp://testserver"
                        remote_manager = "projects"
                        protocol_version = "v1"

    def test_websocket_url_defaults_from_http_base_url(self) -> None:
        class RemoteProject(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    base_url = "https://example.test"
                    remote_manager = "projects"
                    protocol_version = "v1"

        self.assertEqual(
            RemoteProject.Interface.get_websocket_invalidation_url(),
            "wss://example.test/gm/ws/projects?version=v1",
        )
