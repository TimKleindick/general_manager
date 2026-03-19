"""Capabilities for RemoteManagerInterface."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar, TYPE_CHECKING
from urllib.parse import urlsplit

from general_manager.bucket.request_bucket import RequestBucket
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface.capabilities.request import RequestQueryCapability
from general_manager.interface.requests import (
    RequestConfigurationError,
    RequestPlan,
    RequestQueryPlan,
)

from .base import CapabilityName

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.remote_manager import (
        RemoteManagerInterface,
    )
    from general_manager.interface.interfaces.request import RequestInterface

_REMOTE_MANAGER_TOKEN_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class RemoteManagerQueryCapability(RequestQueryCapability):
    """Compile generic GeneralManager filter/exclude calls into remote query payloads."""

    name: ClassVar[CapabilityName] = "query"
    _reserved_query_keys: ClassVar[frozenset[str]] = frozenset(
        {"ordering", "page", "page_size"}
    )

    def validate_lookups(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: Mapping[str, tuple[Any, ...]] | None = None,
        excludes: Mapping[str, tuple[Any, ...]] | None = None,
    ) -> None:
        self._build_request_plan(
            interface_cls,
            operation_name=operation_name,
            filters=self._copy_lookup_map(filters),
            excludes=self._copy_lookup_map(excludes),
        )

    def build_bucket(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None = None,
        filters: Mapping[str, tuple[Any, ...]] | None = None,
        excludes: Mapping[str, tuple[Any, ...]] | None = None,
    ) -> RequestBucket:
        filter_map = self._copy_lookup_map(filters)
        exclude_map = self._copy_lookup_map(excludes)
        request_plan = self._build_request_plan(
            interface_cls,
            operation_name=operation_name,
            filters=filter_map,
            excludes=exclude_map,
        )
        DependencyTracker.track(
            interface_cls._parent_class.__name__,
            "request_query",
            serialize_dependency_identifier(
                {
                    "operation": request_plan.operation_name,
                    "filters": dict(request_plan.filters),
                    "excludes": dict(request_plan.excludes),
                }
            ),
        )
        return RequestBucket(
            interface_cls._parent_class,
            interface_cls,
            operation_name=request_plan.operation_name,
            request_plan=request_plan,
            filters=filter_map,
            excludes=exclude_map,
        )

    def _build_request_plan(
        self,
        interface_cls: type["RequestInterface"],
        *,
        operation_name: str | None,
        filters: Mapping[str, tuple[Any, ...]],
        excludes: Mapping[str, tuple[Any, ...]],
    ) -> RequestPlan:
        operation = interface_cls.get_query_operation(operation_name)
        query_controls = {
            key: values[0]
            for key, values in filters.items()
            if key in self._reserved_query_keys and values
        }
        normalized_filters = {
            key: values
            for key, values in filters.items()
            if key not in self._reserved_query_keys
        }
        body: dict[str, Any] = {
            "filters": {
                key: values[0] if len(values) == 1 else list(values)
                for key, values in normalized_filters.items()
            },
            "excludes": {
                key: values[0] if len(values) == 1 else list(values)
                for key, values in excludes.items()
            },
        }
        body.update(query_controls)
        if operation.name != "list":
            body["operation"] = operation.name
        return RequestQueryPlan(
            operation_name=operation.name,
            action="all" if not normalized_filters and not excludes else "filter",
            method=operation.method,
            path=operation.path,
            headers=dict(operation.static_headers),
            body=body,
            filters=normalized_filters,
            excludes=excludes,
            metadata=operation.metadata,
        )


def validate_remote_manager_meta(interface_cls: type["RemoteManagerInterface"]) -> None:
    """Validate base URL, base path, and remote resource declarations."""
    if not interface_cls.fields:
        raise RequestConfigurationError.missing_remote_manager_fields(
            interface_cls.__name__
        )
    if not interface_cls.remote_manager:
        raise RequestConfigurationError.missing_remote_manager_name(
            interface_cls.__name__
        )
    if not interface_cls.base_url:
        raise RequestConfigurationError.missing_remote_base_url(interface_cls.__name__)
    if not interface_cls.protocol_version:
        raise RequestConfigurationError.missing_remote_protocol_version(
            interface_cls.__name__
        )
    parsed = urlsplit(interface_cls.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RequestConfigurationError.invalid_remote_base_url(interface_cls.__name__)
    if not _REMOTE_MANAGER_TOKEN_RE.match(interface_cls.remote_manager):
        raise RequestConfigurationError.invalid_remote_manager_name(
            interface_cls.__name__
        )
    base_path = interface_cls.base_path
    if base_path == "/":
        raise RequestConfigurationError.invalid_remote_base_path(
            interface_cls.__name__,
            "cannot be '/'",
        )
    if not base_path.startswith("/"):
        raise RequestConfigurationError.invalid_remote_base_path(
            interface_cls.__name__,
            "must start with '/'",
        )
    if "//" in base_path:
        raise RequestConfigurationError.invalid_remote_base_path(
            interface_cls.__name__,
            "cannot contain empty path segments",
        )
    segments = [segment for segment in base_path.split("/") if segment]
    if any(not _REMOTE_MANAGER_TOKEN_RE.match(segment) for segment in segments):
        raise RequestConfigurationError.invalid_remote_base_path(
            interface_cls.__name__,
            "must use lowercase slug segments",
        )
