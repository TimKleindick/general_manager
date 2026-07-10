"""Dynamic route lifecycle tests for framework-owned upload endpoints."""

from __future__ import annotations

from importlib import import_module
from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import override_settings
from django.urls import Resolver404, include, path, re_path, resolve

from general_manager import bootstrap


_ENABLED = {"FILE_UPLOADS": {"ENABLED": True, "HTTP_UPLOAD_PATH": "gm/uploads/"}}


def _empty_response(*_args: object, **_kwargs: object) -> HttpResponse:
    return HttpResponse()


@pytest.fixture(autouse=True)
def clean_test_urls() -> object:
    from general_manager.uploads.urls import clear_file_upload_urls

    urlconf = import_module("tests.test_urls")
    original = list(urlconf.urlpatterns)
    clear_file_upload_urls()
    try:
        yield
    finally:
        urlconf.urlpatterns[:] = original


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
def test_upload_routes_are_marked_idempotent_and_reset_safe() -> None:
    from general_manager.uploads.urls import (
        add_file_upload_urls,
        clear_file_upload_urls,
    )

    urlconf = import_module("tests.test_urls")
    add_file_upload_urls()
    add_file_upload_urls()

    generated = [
        route
        for route in urlconf.urlpatterns
        if getattr(route, "_general_manager_file_upload", False)
    ]
    assert len(generated) == 2
    assert {route.name for route in generated} == {
        "general_manager_file_upload",
        "general_manager_file_download",
    }
    assert {str(route.pattern) for route in generated} == {
        "gm/uploads/<uuid:intent_id>",
        "gm/uploads/download/<str:capability>",
    }

    clear_file_upload_urls()
    assert all(
        not getattr(route, "_general_manager_file_upload", False)
        for route in urlconf.urlpatterns
    )


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
def test_upload_route_mutations_invalidate_django_resolver_caches() -> None:
    from general_manager.uploads.urls import (
        add_file_upload_urls,
        clear_file_upload_urls,
    )

    concrete = "/gm/uploads/00000000-0000-4000-8000-000000000001"
    with pytest.raises(Resolver404):
        resolve(concrete)

    add_file_upload_urls()
    assert resolve(concrete).url_name == "general_manager_file_upload"

    clear_file_upload_urls()
    with pytest.raises(Resolver404):
        resolve(concrete)


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
def test_route_registration_rejects_unowned_path_collision_without_mutation() -> None:
    from general_manager.uploads.urls import add_file_upload_urls

    urlconf = import_module("tests.test_urls")
    custom = path(
        "gm/uploads/<uuid:intent_id>",
        _empty_response,
        name="project_upload",
    )
    urlconf.urlpatterns.append(custom)
    before = list(urlconf.urlpatterns)

    with pytest.raises(ValueError, match="gm/uploads"):
        add_file_upload_urls()

    assert urlconf.urlpatterns == before


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
@pytest.mark.parametrize(
    "catch_all",
    [
        path("gm/uploads/<path:value>", _empty_response, name="project_catch_all"),
        path(
            "gm/uploads/download/<str:value>",
            _empty_response,
            name="project_download_catch_all",
        ),
        path(
            "gm/uploads/",
            include([path("<path:value>", _empty_response)]),
        ),
    ],
)
def test_route_registration_rejects_semantic_project_catch_all(
    catch_all: object,
) -> None:
    from general_manager.uploads.urls import add_file_upload_urls

    urlconf = import_module("tests.test_urls")
    urlconf.urlpatterns.append(catch_all)
    before = list(urlconf.urlpatterns)

    with pytest.raises(ValueError, match="gm/uploads"):
        add_file_upload_urls()

    assert urlconf.urlpatterns == before


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
def test_framework_download_route_precedes_capability_shaped_project_route() -> None:
    from general_manager.uploads.graphql_types import (
        issue_local_download_capability,
    )
    from general_manager.uploads.urls import add_file_upload_urls

    urlconf = import_module("tests.test_urls")
    project_before = path("project-before/", _empty_response, name="project_before")
    project_capability = re_path(
        r"^gm/uploads/download/(?:eyJ|\.eJ)[^/]+$",
        _empty_response,
        name="project_capability_logger",
    )
    project_after = path("project-after/", _empty_response, name="project_after")
    urlconf.urlpatterns.extend([project_before, project_capability, project_after])
    capability = issue_local_download_capability(
        manager_name="tests.document",
        object_id="1",
        field_name="image",
        current_key="images/example.png",
        expires_in=60,
    )

    assert resolve(capability.url).url_name == "project_capability_logger"

    add_file_upload_urls()

    assert resolve(capability.url).url_name == "general_manager_file_download"
    unowned = [
        pattern
        for pattern in urlconf.urlpatterns
        if not getattr(pattern, "_general_manager_file_upload", False)
    ]
    assert unowned[-3:] == [project_before, project_capability, project_after]


@override_settings(
    ROOT_URLCONF="tests.test_urls",
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}},
)
def test_disabling_uploads_removes_only_framework_owned_routes() -> None:
    from general_manager.uploads.urls import add_file_upload_urls

    urlconf = import_module("tests.test_urls")
    before = list(urlconf.urlpatterns)
    project_route = path("project/", _empty_response)
    urlconf.urlpatterns.append(project_route)

    add_file_upload_urls()

    assert urlconf.urlpatterns == [*before, project_route]
    assert all(
        not getattr(route, "_general_manager_file_upload", False)
        for route in urlconf.urlpatterns
    )


@override_settings(ROOT_URLCONF=None, GENERAL_MANAGER=_ENABLED)
def test_enabled_routes_require_a_root_urlconf() -> None:
    from general_manager.bootstrap import MissingRootUrlconfError
    from general_manager.uploads.urls import add_file_upload_urls

    with pytest.raises(MissingRootUrlconfError):
        add_file_upload_urls()


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
def test_graphql_bootstrap_registers_upload_route() -> None:
    with (
        patch.object(bootstrap.GraphQL, "register_file_upload_mutation"),
        patch.object(bootstrap.GraphQL, "register_search_query"),
        patch.object(bootstrap.GraphQL, "register_current_user_capabilities"),
        patch.object(bootstrap, "add_graphql_url"),
        patch("general_manager.uploads.urls.add_file_upload_urls") as add_uploads,
    ):
        bootstrap.handle_graph_ql([])

    add_uploads.assert_called_once_with()


@override_settings(ROOT_URLCONF="tests.test_urls", GENERAL_MANAGER=_ENABLED)
def test_general_manager_test_utility_clears_upload_routes() -> None:
    from general_manager.uploads.urls import add_file_upload_urls
    from general_manager.utils.testing import _default_remote_api_url_clear

    urlconf = import_module("tests.test_urls")
    add_file_upload_urls()
    assert any(
        getattr(route, "_general_manager_file_upload", False)
        for route in urlconf.urlpatterns
    )

    _default_remote_api_url_clear()

    assert all(
        not getattr(route, "_general_manager_file_upload", False)
        for route in urlconf.urlpatterns
    )
