from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.core.exceptions import ObjectDoesNotExist
from django.test import RequestFactory, SimpleTestCase

from general_manager.api.remote_api import (
    RemoteAPIConfig,
    RemoteAPIConfigurationError,
    _build_create_view,
    _build_item_view,
    _build_query_view,
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
        self.assertEqual(
            RemoteProject.Interface.get_query_operation("list").path,
            "/gm/projects/query",
        )

    def test_item_view_unsupported_methods_do_not_instantiate_manager(self) -> None:
        factory = RequestFactory()
        manager_cls = MagicMock(name="RemoteProjectManager")
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=False,
            allow_detail=True,
            allow_create=False,
            allow_update=False,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        response = _build_item_view(config)(factory.post("/gm/projects/1"), "1")

        self.assertEqual(response.status_code, 405)
        manager_cls.assert_not_called()

    def test_item_view_mutations_do_not_bypass_permissions(self) -> None:
        factory = RequestFactory()
        manager_instance = MagicMock()
        manager_cls = MagicMock(return_value=manager_instance)
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=False,
            allow_detail=False,
            allow_create=False,
            allow_update=True,
            allow_delete=True,
            websocket_invalidation=False,
            protocol_version="v1",
        )
        item_view = _build_item_view(config)

        with patch(
            "general_manager.api.remote_api._serialize_manager",
            return_value={"id": 7, "name": "Updated"},
        ):
            patch_response = item_view(
                factory.patch(
                    "/gm/projects/7",
                    data=json.dumps({"name": "Updated"}),
                    content_type="application/json",
                ),
                "7",
            )

        self.assertEqual(patch_response.status_code, 200)
        manager_cls.assert_called_once_with(id=7)
        manager_instance.update.assert_called_once_with(name="Updated")

        manager_cls.reset_mock()
        manager_instance.reset_mock()

        delete_response = item_view(factory.delete("/gm/projects/7"), "7")

        self.assertEqual(delete_response.status_code, 200)
        manager_cls.assert_called_once_with(id=7)
        manager_instance.delete.assert_called_once_with()

    def test_item_view_sanitizes_exception_text(self) -> None:
        factory = RequestFactory()
        manager_instance = MagicMock()
        manager_instance.update.side_effect = ValueError("secret failure")
        manager_cls = MagicMock(return_value=manager_instance)
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=False,
            allow_detail=False,
            allow_create=False,
            allow_update=True,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        response = _build_item_view(config)(
            factory.patch(
                "/gm/projects/7",
                data=json.dumps({"name": "Updated"}),
                content_type="application/json",
            ),
            "7",
        )
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"], "Invalid request.")
        self.assertEqual(payload["error_code"], "invalid_request")
        self.assertNotIn("secret failure", payload["error"])

    def test_query_view_maps_permission_error(self) -> None:
        factory = RequestFactory()
        manager_cls = MagicMock()
        manager_cls.all.side_effect = PermissionError("sensitive")
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=True,
            allow_detail=False,
            allow_create=False,
            allow_update=False,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        response = _build_query_view(config)(
            factory.post(
                "/gm/projects/query",
                data=json.dumps({"filters": {}, "excludes": {}}),
                content_type="application/json",
            )
        )
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"], "Permission denied.")
        self.assertEqual(payload["error_code"], "permission_denied")

    def test_query_view_maps_runtime_error_to_internal_error(self) -> None:
        factory = RequestFactory()
        manager_cls = MagicMock()
        manager_cls.all.side_effect = RuntimeError("database leaked")
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=True,
            allow_detail=False,
            allow_create=False,
            allow_update=False,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        response = _build_query_view(config)(
            factory.post(
                "/gm/projects/query",
                data=json.dumps({"filters": {}, "excludes": {}}),
                content_type="application/json",
            )
        )
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"], "Internal server error.")
        self.assertEqual(payload["error_code"], "internal_error")
        self.assertNotIn("database leaked", payload["error"])

    def test_item_view_get_maps_object_does_not_exist(self) -> None:
        factory = RequestFactory()
        manager_cls = MagicMock(side_effect=ObjectDoesNotExist("secret missing"))
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=False,
            allow_detail=True,
            allow_create=False,
            allow_update=False,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        response = _build_item_view(config)(factory.get("/gm/projects/7"), "7")
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"], "Resource not found.")
        self.assertEqual(payload["error_code"], "not_found")
        self.assertNotIn("secret missing", payload["error"])

    def test_create_view_does_not_bypass_permissions(self) -> None:
        factory = RequestFactory()
        manager_instance = MagicMock()
        manager_cls = MagicMock()
        manager_cls.create.return_value = manager_instance
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=False,
            allow_detail=False,
            allow_create=True,
            allow_update=False,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        with patch(
            "general_manager.api.remote_api._serialize_manager",
            return_value={"id": 13, "name": "Gamma"},
        ):
            response = _build_create_view(config)(
                factory.post(
                    "/gm/projects",
                    data=json.dumps({"name": "Gamma"}),
                    content_type="application/json",
                )
            )

        self.assertEqual(response.status_code, 201)
        manager_cls.create.assert_called_once_with(name="Gamma")

    def test_create_view_maps_runtime_error_to_internal_error(self) -> None:
        factory = RequestFactory()
        manager_cls = MagicMock()
        manager_cls.create.side_effect = RuntimeError("secret create failure")
        config = RemoteAPIConfig(
            manager_cls=manager_cls,
            base_path="/gm",
            resource_name="projects",
            allow_filter=False,
            allow_detail=False,
            allow_create=True,
            allow_update=False,
            allow_delete=False,
            websocket_invalidation=False,
            protocol_version="v1",
        )

        response = _build_create_view(config)(
            factory.post(
                "/gm/projects",
                data=json.dumps({"name": "Gamma"}),
                content_type="application/json",
            )
        )
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"], "Internal server error.")
        self.assertEqual(payload["error_code"], "internal_error")
        self.assertNotIn("secret create failure", payload["error"])

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

    def test_websocket_url_preserves_base_url_path_prefix(self) -> None:
        class RemoteProject(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    base_url = "https://example.test/api"
                    base_path = "/internal/gm/"
                    remote_manager = "projects"
                    protocol_version = "v1"

        self.assertEqual(RemoteProject.Interface.base_path, "/internal/gm")
        self.assertEqual(
            RemoteProject.Interface.get_query_operation("detail").path,
            "/internal/gm/projects/{id}",
        )
        self.assertEqual(
            RemoteProject.Interface.get_websocket_invalidation_url(),
            "wss://example.test/api/internal/gm/ws/projects?version=v1",
        )
