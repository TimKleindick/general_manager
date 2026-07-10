"""Dynamic URL registration for framework-owned file transfer routes."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Protocol, cast

from django.conf import settings as django_settings
from django.urls import Resolver404, URLPattern, clear_url_caches, path, resolve

from general_manager.uploads.config import get_file_upload_settings
from general_manager.uploads.views import private_download_view, proxy_upload_view


_UPLOAD_ROUTE_MARKER = "_general_manager_file_upload"
_PROXY_UPLOAD_ROUTE_KEY = "proxy-upload"
_PRIVATE_DOWNLOAD_ROUTE_KEY = "private-download"


class FileUploadRouteCollisionError(ValueError):
    """Raised when a project route already owns the configured upload path."""

    def __init__(self, route_pattern: str) -> None:
        super().__init__(
            f"The file upload route '{route_pattern}' collides with an "
            "existing project URL pattern."
        )


class _MarkedUploadPattern(Protocol):
    _general_manager_file_upload: bool
    _general_manager_file_upload_key: str


def _mark_upload_route(pattern: URLPattern, key: str) -> URLPattern:
    marked = cast(_MarkedUploadPattern, pattern)
    marked._general_manager_file_upload = True
    marked._general_manager_file_upload_key = key
    return pattern


def add_file_upload_urls() -> None:
    """Install the upload route once, rejecting project-owned path collisions."""

    configured = get_file_upload_settings()
    root_urlconf = getattr(django_settings, "ROOT_URLCONF", None)
    if not configured.enabled:
        clear_file_upload_urls()
        return
    if not root_urlconf:
        # Import lazily to avoid coupling bootstrap module import order to views.
        from general_manager.bootstrap import MissingRootUrlconfError

        raise MissingRootUrlconfError

    urlconf = import_module(root_urlconf)
    route_specs = (
        (
            _PROXY_UPLOAD_ROUTE_KEY,
            f"{configured.http_upload_path}<uuid:intent_id>",
            proxy_upload_view,
            "general_manager_file_upload",
        ),
        (
            _PRIVATE_DOWNLOAD_ROUTE_KEY,
            f"{configured.http_upload_path}download/<str:capability>",
            private_download_view,
            "general_manager_file_download",
        ),
    )
    patterns = list(urlconf.urlpatterns)
    for _key, route_pattern, _view, _name in route_specs:
        for existing in patterns:
            if str(existing.pattern) == route_pattern and not getattr(
                existing, _UPLOAD_ROUTE_MARKER, False
            ):
                raise FileUploadRouteCollisionError(route_pattern)

    unowned = [
        existing
        for existing in patterns
        if not getattr(existing, _UPLOAD_ROUTE_MARKER, False)
    ]
    # Resolve representative upload URLs against only project-owned patterns to
    # detect routes that would shadow either framework endpoint.
    representative_paths = (
        f"/{configured.http_upload_path}00000000-0000-4000-8000-000000000001",
        f"/{configured.http_upload_path}download/gm-private-capability",
    )
    for representative in representative_paths:
        try:
            resolve(representative, urlconf=cast(Any, tuple(unowned)))
        except Resolver404:
            continue
        raise FileUploadRouteCollisionError(representative.lstrip("/"))
    generated: list[URLPattern] = []
    for key, route_pattern, view, name in route_specs:
        matching_owned = [
            existing
            for existing in patterns
            if getattr(existing, _UPLOAD_ROUTE_MARKER, False)
            and getattr(existing, "_general_manager_file_upload_key", None) == key
            and str(existing.pattern) == route_pattern
        ]
        if matching_owned:
            generated.append(matching_owned[0])
            continue
        generated.append(
            _mark_upload_route(
                path(
                    route_pattern,
                    view,
                    name=name,
                ),
                key,
            )
        )
    urlconf.urlpatterns[:] = [*generated, *unowned]
    clear_url_caches()


def clear_file_upload_urls() -> None:
    """Remove only routes marked as owned by this upload integration."""

    root_urlconf = getattr(django_settings, "ROOT_URLCONF", None)
    if not root_urlconf:
        return
    urlconf = import_module(root_urlconf)
    urlconf.urlpatterns[:] = [
        pattern
        for pattern in urlconf.urlpatterns
        if not getattr(pattern, _UPLOAD_ROUTE_MARKER, False)
    ]
    clear_url_caches()
