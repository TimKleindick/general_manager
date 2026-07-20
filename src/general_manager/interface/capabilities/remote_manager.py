"""Capabilities for RemoteManagerInterface."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import ClassVar, TYPE_CHECKING, NotRequired, TypedDict, cast
from urllib.parse import urlsplit

from general_manager.bucket.request_bucket import RequestBucket
from general_manager.bucket.base_bucket import GeneralManagerType
from general_manager.as_of import ensure_as_of_read_supported
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
type RemoteManagerOperationName = str | None
type RemoteManagerLookupValues = tuple[object, ...]
type RemoteManagerLookupMap = Mapping[str, RemoteManagerLookupValues]


class RemoteManagerQueryControls(TypedDict, total=False):
    """Optional top-level query controls from reserved filters or operation name."""

    ordering: object
    page: object
    page_size: object
    operation: str


class RemoteManagerQueryPayload(TypedDict):
    """Remote query POST body sent to the generated query endpoint."""

    filters: dict[str, object]
    excludes: dict[str, object]
    ordering: NotRequired[object]
    page: NotRequired[object]
    page_size: NotRequired[object]
    operation: NotRequired[str]


__all__ = [
    "RemoteManagerLookupMap",
    "RemoteManagerLookupValues",
    "RemoteManagerOperationName",
    "RemoteManagerQueryCapability",
    "RemoteManagerQueryControls",
    "RemoteManagerQueryPayload",
    "validate_remote_manager_meta",
]


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
        operation_name: RemoteManagerOperationName = None,
        filters: RemoteManagerLookupMap | None = None,
        excludes: RemoteManagerLookupMap | None = None,
    ) -> None:
        """
        Validate remote-manager filter and exclude lookup maps.

        The method compiles the requested operation without building a bucket or
        tracking cache dependencies. Reserved query-control filters (`ordering`,
        `page`, and `page_size`) are accepted only from `filters`, use their
        first supplied value, are not additionally validated here, and are not
        sent as remote filters. The default operation is whatever
        `interface_cls.get_query_operation(None)` resolves to, normally `list`.
        `operation_name` must be `None` or a string; an empty string is an
        intentional default-operation alias and behaves like `None`. Lookup
        maps contain already-normalized tuple values; empty tuples are skipped
        for reserved controls and serialize as empty lists for normal
        filters/excludes.
        Lookup names and operators are not validated by this remote compiler;
        they are serialized to the remote service, which owns acceptance or
        rejection.

        Raises:
            UnknownRequestOperationError: If `operation_name` is not declared on
                the request interface.
            RequestConfigurationError: If operation lookup/configuration fails
                before a request plan can be built.
        """
        ensure_as_of_read_supported(interface_cls)
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
        operation_name: RemoteManagerOperationName = None,
        filters: RemoteManagerLookupMap | None = None,
        excludes: RemoteManagerLookupMap | None = None,
    ) -> RequestBucket[GeneralManagerType]:
        """
        Compile remote-manager lookups into a lazy request bucket.

        Filters and excludes become the POST body for the remote GeneralManager
        query endpoint. Single-value lookups serialize as the scalar value;
        multi-value lookups serialize as lists. Reserved query-control filters
        (`ordering`, `page`, and `page_size`) are lifted from `filters` to
        top-level body keys using the first supplied value; matching names in
        `excludes` remain ordinary exclude keys. Non-list operations also
        include an `operation` body key; the `list` operation never adds that
        key, even when requested explicitly. Lookup names and operators are
        forwarded to the remote service instead of being validated locally. The
        compiled request plan is tracked in `DependencyTracker` before the
        bucket is returned.

        `operation_name` must be `None` or a string; an empty string is an
        intentional default-operation alias and behaves like `None`.
        Lookup values are normalized before this method by the inherited request
        query API: ordinary keyword arguments become one-item tuples, and callers
        that pass lookup maps directly may supply any tuple of objects. Tuple
        length controls serialization, not the lookup suffix: one item becomes a
        scalar, zero or multiple items become a list. Reserved controls use only
        index `0`, so empty tuples are ignored and strings are treated as scalar
        values, not character iterables.

        Returns:
            A `RequestBucket` for the interface's parent manager.

        Raises:
            UnknownRequestOperationError: If `operation_name` is not declared on
                the request interface.
            RequestConfigurationError: If operation lookup/configuration fails
                before a request plan can be built.
        """
        ensure_as_of_read_supported(interface_cls)
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
        manager_cls = cast(type[GeneralManagerType], interface_cls._parent_class)
        return RequestBucket(
            manager_cls,
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
        operation_name: RemoteManagerOperationName,
        filters: RemoteManagerLookupMap,
        excludes: RemoteManagerLookupMap,
    ) -> RequestPlan:
        """
        Build the remote query request plan used by validation and bucket creation.

        Returns:
            A `RequestQueryPlan` whose body contains normalized `filters`,
            `excludes`, reserved query controls, and optionally `operation`.
            Reserved query controls are taken only from `filters`, and their
            first tuple value wins. `operation` is omitted for `list` and added
            for every other operation name.

        Raises:
            UnknownRequestOperationError: If `operation_name` is not declared on
                `interface_cls`.
            RequestConfigurationError: If operation lookup/configuration fails
                before a request plan can be built.
        """
        operation = interface_cls.get_query_operation(operation_name)
        query_controls: RemoteManagerQueryControls = {}
        for key, values in filters.items():
            if key == "ordering" and values:
                query_controls["ordering"] = values[0]
            elif key == "page" and values:
                query_controls["page"] = values[0]
            elif key == "page_size" and values:
                query_controls["page_size"] = values[0]
        normalized_filters = {
            key: values
            for key, values in filters.items()
            if key not in self._reserved_query_keys
        }
        body: RemoteManagerQueryPayload = {
            "filters": {
                key: values[0] if len(values) == 1 else list(values)
                for key, values in normalized_filters.items()
            },
            "excludes": {
                key: values[0] if len(values) == 1 else list(values)
                for key, values in excludes.items()
            },
        }
        if "ordering" in query_controls:
            body["ordering"] = query_controls["ordering"]
        if "page" in query_controls:
            body["page"] = query_controls["page"]
        if "page_size" in query_controls:
            body["page_size"] = query_controls["page_size"]
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
    """
    Validate a `RemoteManagerInterface` subclass's remote endpoint metadata.

    The interface must declare at least one request field via `RequestField`
    attributes, plus `Meta.remote_manager` and `Meta.base_url`.
    `Meta.protocol_version` defaults to `"v1"` when omitted; the effective
    protocol version is only required to be a non-empty string by this validator.
    `base_url` must parse as an HTTP(S) URL with a network location; paths,
    ports, credentials, query strings, fragments, and trailing slashes are not
    rejected by this validator.
    The slug grammar is `^[a-z0-9]+(?:-[a-z0-9]+)*$`: lowercase ASCII letters,
    digits, and single hyphens between alphanumeric runs; underscores,
    uppercase letters, leading hyphens, trailing hyphens, and doubled hyphens are
    invalid.
    `remote_manager` and each `base_path` segment must be lowercase slug tokens
    made from alphanumeric segments separated by single hyphens; `base_path`
    must start with `/`, must not be `/`, and must not contain empty segments.

    Raises:
        RequestConfigurationError: If any required declaration is missing or any
            URL, resource name, or path segment is invalid.
    """
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
