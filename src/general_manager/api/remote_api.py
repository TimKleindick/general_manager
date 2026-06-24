"""Opt-in REST exposure for GeneralManager classes."""

from __future__ import annotations

import json
import re
from uuid import uuid4
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from importlib import import_module
from typing import Callable, Mapping, Protocol, TYPE_CHECKING, cast

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import URLPattern, path

from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.remote")

_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RemoteAPIView = Callable[[HttpRequest], HttpResponse]
RemoteAPIItemView = Callable[[HttpRequest, str], HttpResponse]
RemoteAPIRouteKey = tuple[str, str]
type RemoteJSONPayload = dict[str, object]
type RemoteJSONList = list[RemoteJSONPayload]
type RemoteOrdering = str | Iterable[str] | None


class RemoteAPIBucket(Protocol):
    """Bucket operations used by RemoteAPI query endpoints."""

    def filter(self, **kwargs: object) -> "RemoteAPIBucket":
        """Return a bucket restricted by lookup keyword arguments."""
        ...

    def exclude(self, **kwargs: object) -> "RemoteAPIBucket":
        """Return a bucket excluding lookup keyword matches."""
        ...

    def sort(self, key: str, reverse: bool = False) -> "RemoteAPIBucket":
        """Return a bucket sorted by one field."""
        ...

    def count(self) -> int:
        """Return the number of matching items before pagination."""
        ...

    def __getitem__(self, item: slice) -> "RemoteAPIBucket":
        """Return a sliced bucket."""
        ...

    def __iter__(self) -> Iterator["GeneralManager"]:
        """Yield manager instances for serialization."""
        ...


class RemoteAPIMutableManager(Protocol):
    """Manager instance operations used by item RemoteAPI endpoints."""

    def update(self, **kwargs: object) -> "GeneralManager":
        """Apply update payload values and return the updated manager."""
        ...

    def delete(self) -> None:
        """Delete the manager instance."""
        ...


class RemoteAPIManagerClass(Protocol):
    """Manager class operations used by RemoteAPI endpoints."""

    def __call__(self, **kwargs: object) -> "GeneralManager":
        """Construct a manager from keyword identification values."""
        ...

    def create(self, **kwargs: object) -> "GeneralManager":
        """Create a manager from request payload values."""
        ...

    def all(self) -> RemoteAPIBucket:
        """Return a bucket for query endpoints."""
        ...


class RemoteAPIConfigurationError(ValueError):
    """Raised when RemoteAPI declarations are invalid."""

    @classmethod
    def empty_segments(cls, base_path: str) -> "RemoteAPIConfigurationError":
        return cls(
            f"Invalid RemoteAPI.base_path {base_path!r}: empty path segments are not allowed."
        )

    @classmethod
    def invalid_base_path(cls, base_path: str) -> "RemoteAPIConfigurationError":
        return cls(
            f"Invalid RemoteAPI.base_path {base_path!r}: use lowercase slug segments."
        )

    @classmethod
    def missing_resource_name(cls, manager_name: str) -> "RemoteAPIConfigurationError":
        return cls(f"{manager_name}.RemoteAPI.enabled=True requires resource_name.")

    @classmethod
    def invalid_resource_name(cls, resource_name: str) -> "RemoteAPIConfigurationError":
        return cls(
            f"Invalid RemoteAPI.resource_name {resource_name!r}: use a lowercase slug."
        )

    @classmethod
    def duplicate_exposure(
        cls, base_path: str, resource_name: str
    ) -> "RemoteAPIConfigurationError":
        return cls(f"Duplicate RemoteAPI exposure for {base_path}/{resource_name}.")

    @classmethod
    def version_mismatch(
        cls, provided: str, expected: str
    ) -> "RemoteAPIConfigurationError":
        return cls(f"Unsupported protocol version {provided!r}; expected {expected!r}.")

    @classmethod
    def no_allowed_operations(cls, manager_name: str) -> "RemoteAPIConfigurationError":
        return cls(f"{manager_name}.RemoteAPI must enable at least one operation.")

    @classmethod
    def invalid_websocket_configuration(
        cls, manager_name: str
    ) -> "RemoteAPIConfigurationError":
        return cls(
            f"{manager_name}.RemoteAPI websocket_invalidation requires at least one mutation operation."
        )


class RemoteAPIRequestError(ValueError):
    """Raised when a remote API request cannot be parsed safely."""

    @classmethod
    def malformed_json(cls) -> "RemoteAPIRequestError":
        return cls("Malformed JSON in request body.")

    @classmethod
    def non_object_json(cls) -> "RemoteAPIRequestError":
        return cls("JSON request body must decode to an object.")


@dataclass(frozen=True, slots=True)
class RemoteAPIConfig:
    """Normalized opt-in REST exposure settings for one manager.

    `get_remote_api_config()` creates this value from a manager's nested
    `RemoteAPI` declaration. `base_path` defaults to `/gm` and is normalized to
    lowercase slug path segments with one leading slash and no trailing slash;
    root, empty, double-slash, and non-slug paths are rejected. `resource_name`
    is required when enabled, surrounding slashes are stripped before slug
    validation, `protocol_version` defaults to `"v1"`, at least one operation
    flag must be true, and websocket invalidation requires at least one mutation
    operation. `identifier_type` is extracted from the interface `id` input when
    available and item views coerce URL identifiers only when it is exactly
    `int`; every other type leaves identifiers as strings.
    """

    manager_cls: type["GeneralManager"]
    base_path: str
    resource_name: str
    allow_filter: bool
    allow_detail: bool
    allow_create: bool
    allow_update: bool
    allow_delete: bool
    websocket_invalidation: bool
    protocol_version: str
    identifier_type: type[object] | None = None


def _extract_identifier_type(manager_cls: type["GeneralManager"]) -> type[object] | None:
    interface = getattr(manager_cls, "Interface", None)
    if interface is None:
        return None
    input_fields = getattr(interface, "input_fields", None)
    if input_fields is None:
        return None
    id_field = input_fields.get("id")
    return cast(type[object] | None, getattr(id_field, "type", None))


def _normalize_base_path(raw: str | None) -> str:
    base_path = (raw or "/gm").strip()
    if not base_path.startswith("/"):
        base_path = f"/{base_path}"
    base_path = base_path.rstrip("/") or "/gm"
    if "//" in base_path:
        raise RemoteAPIConfigurationError.empty_segments(base_path)
    segments = [segment for segment in base_path.split("/") if segment]
    if not segments or any(not _SEGMENT_RE.match(segment) for segment in segments):
        raise RemoteAPIConfigurationError.invalid_base_path(base_path)
    return "/" + "/".join(segments)


def _normalize_resource_name(raw: str | None, manager_name: str) -> str:
    if not raw:
        raise RemoteAPIConfigurationError.missing_resource_name(manager_name)
    resource_name = raw.strip().strip("/")
    if not _SEGMENT_RE.match(resource_name):
        raise RemoteAPIConfigurationError.invalid_resource_name(resource_name)
    return resource_name


def get_remote_api_config(
    manager_cls: type["GeneralManager"],
) -> RemoteAPIConfig | None:
    """Return normalized RemoteAPI config for an enabled manager, if any.

    Raises:
        RemoteAPIConfigurationError: If the manager opts in with invalid path,
            resource, operation, protocol, or websocket settings.
    """
    remote_api = getattr(manager_cls, "RemoteAPI", None)
    if remote_api is None or not getattr(remote_api, "enabled", False):
        return None
    config = RemoteAPIConfig(
        manager_cls=manager_cls,
        base_path=_normalize_base_path(getattr(remote_api, "base_path", "/gm")),
        resource_name=_normalize_resource_name(
            getattr(remote_api, "resource_name", None),
            manager_cls.__name__,
        ),
        allow_filter=bool(getattr(remote_api, "allow_filter", False)),
        allow_detail=bool(getattr(remote_api, "allow_detail", False)),
        allow_create=bool(getattr(remote_api, "allow_create", False)),
        allow_update=bool(getattr(remote_api, "allow_update", False)),
        allow_delete=bool(getattr(remote_api, "allow_delete", False)),
        websocket_invalidation=bool(
            getattr(remote_api, "websocket_invalidation", False)
        ),
        protocol_version=str(getattr(remote_api, "protocol_version", "v1")),
        identifier_type=_extract_identifier_type(manager_cls),
    )
    if not any(
        (
            config.allow_filter,
            config.allow_detail,
            config.allow_create,
            config.allow_update,
            config.allow_delete,
        )
    ):
        raise RemoteAPIConfigurationError.no_allowed_operations(manager_cls.__name__)
    if config.websocket_invalidation and not any(
        (config.allow_create, config.allow_update, config.allow_delete)
    ):
        raise RemoteAPIConfigurationError.invalid_websocket_configuration(
            manager_cls.__name__
        )
    return config


def build_remote_api_registry(
    manager_classes: list[type["GeneralManager"]],
) -> dict[tuple[str, str], RemoteAPIConfig]:
    """Build a registry keyed by `(base_path, resource_name)`.

    Raises:
        RemoteAPIConfigurationError: If two enabled managers expose the same
            `(base_path, resource_name)` pair.
    """
    registry: dict[tuple[str, str], RemoteAPIConfig] = {}
    for manager_cls in manager_classes:
        config = get_remote_api_config(manager_cls)
        if config is None:
            continue
        key = (config.base_path, config.resource_name)
        if key in registry:
            raise RemoteAPIConfigurationError.duplicate_exposure(
                config.base_path,
                config.resource_name,
            )
        registry[key] = config
    return registry


def _mark_remote_api_route(pattern: URLPattern, key: RemoteAPIRouteKey) -> URLPattern:
    """Mark a generated URL pattern so it can be deduplicated or cleared later."""
    vars(pattern)["_general_manager_remote_api"] = True
    vars(pattern)["_general_manager_remote_api_key"] = key
    return pattern


def add_remote_api_urls(manager_classes: list[type["GeneralManager"]]) -> None:
    """Append generated RemoteAPI URL patterns to the configured root URLconf.

    Generated routes are added in query, item, then create order when the
    corresponding operation is enabled. Existing generated routes with the same
    route key are skipped, so repeated startup registration is idempotent.
    Duplicate exposures are rejected while the registry is built before any new
    route from that registry pass is appended. If `settings.ROOT_URLCONF` is not
    set, this helper returns without changes.
    """
    root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
    if not root_url_conf_path:
        return
    urlconf = import_module(root_url_conf_path)
    registry = build_remote_api_registry(manager_classes)
    for config in registry.values():
        route_prefix = f"{config.base_path.strip('/')}/{config.resource_name}"
        existing = {
            getattr(pattern, "_general_manager_remote_api_key", None)
            for pattern in urlconf.urlpatterns
        }
        if config.allow_filter and (route_prefix, "query") not in existing:
            query_route = path(
                f"{route_prefix}/query",
                _build_query_view(config),
            )
            urlconf.urlpatterns.append(
                _mark_remote_api_route(query_route, (route_prefix, "query"))
            )
        if (
            any((config.allow_detail, config.allow_update, config.allow_delete))
            and (route_prefix, "item") not in existing
        ):
            detail_route = path(
                f"{route_prefix}/<str:identifier>",
                _build_item_view(config),
            )
            urlconf.urlpatterns.append(
                _mark_remote_api_route(detail_route, (route_prefix, "item"))
            )
        if config.allow_create and (route_prefix, "create") not in existing:
            create_route = path(
                route_prefix,
                _build_create_view(config),
            )
            urlconf.urlpatterns.append(
                _mark_remote_api_route(create_route, (route_prefix, "create"))
            )


def clear_remote_api_urls() -> None:
    """Remove only URL patterns marked as generated RemoteAPI routes."""
    root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
    if not root_url_conf_path:
        return
    urlconf = import_module(root_url_conf_path)
    urlconf.urlpatterns[:] = [
        pattern
        for pattern in urlconf.urlpatterns
        if not getattr(pattern, "_general_manager_remote_api", False)
    ]


def _check_protocol_version(request: HttpRequest, config: RemoteAPIConfig) -> None:
    provided = request.headers.get("X-General-Manager-Protocol-Version")
    if provided is not None and provided != config.protocol_version:
        raise RemoteAPIConfigurationError.version_mismatch(
            provided,
            config.protocol_version,
        )


def _parse_json_body(request: HttpRequest) -> RemoteJSONPayload:
    """Decode a JSON request body into an object mapping.

    Empty bodies produce an empty mapping. Malformed JSON and valid JSON values
    other than objects raise `RemoteAPIRequestError`.
    """
    if not request.body:
        return {}
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise RemoteAPIRequestError.malformed_json() from error
    if not isinstance(body, dict):
        raise RemoteAPIRequestError.non_object_json()
    return cast(RemoteJSONPayload, body)


def _coerce_identifier(config: RemoteAPIConfig, identifier: str) -> object:
    return int(identifier) if config.identifier_type is int else identifier


def _request_id(
    request: HttpRequest,
    *,
    prefix: str,
    identifier: str | None = None,
) -> str:
    incoming = request.headers.get("X-Request-ID")
    if incoming:
        return str(incoming)
    if identifier is not None:
        return f"gm-{prefix}-{identifier}"
    return f"gm-{prefix}-{uuid4()}"


def _serialize_manager(manager: "GeneralManager") -> RemoteJSONPayload:
    return dict(manager)


def _success_payload(
    *,
    items: RemoteJSONList,
    config: RemoteAPIConfig,
    request_id: str,
    total_count: int | None = None,
    status: int = 200,
    metadata_extra: Mapping[str, object] | None = None,
) -> JsonResponse:
    payload: RemoteJSONPayload = {"items": items}
    metadata: RemoteJSONPayload = {
        "protocol_version": config.protocol_version,
        "request_id": request_id,
    }
    if metadata_extra:
        metadata.update(metadata_extra)
    payload["metadata"] = metadata
    if total_count is not None:
        payload["total_count"] = total_count
    response = JsonResponse(payload, status=status)
    response["X-Request-ID"] = request_id
    return response


def _error_payload(
    *,
    config: RemoteAPIConfig,
    request_id: str,
    error: str,
    error_code: str,
    status: int,
    details: Mapping[str, object] | None = None,
) -> JsonResponse:
    payload: RemoteJSONPayload = {
        "error": error,
        "error_code": error_code,
        "metadata": {
            "protocol_version": config.protocol_version,
            "request_id": request_id,
        },
    }
    if details:
        payload["details"] = dict(details)
    response = JsonResponse(payload, status=status)
    response["X-Request-ID"] = request_id
    return response


def _remote_api_error_details(error: Exception) -> tuple[str, str, int]:
    if isinstance(error, ObjectDoesNotExist):
        return "Resource not found.", "not_found", 404
    if isinstance(error, PermissionError):
        return "Permission denied.", "permission_denied", 403
    if isinstance(error, ValidationError):
        return "Validation failed.", "validation_error", 400
    if isinstance(error, RuntimeError):
        return "Internal server error.", "internal_error", 500
    return "Invalid request.", "invalid_request", 400


def _remote_api_error_payload(
    *,
    config: RemoteAPIConfig,
    request_id: str,
    error: Exception,
    operation: str,
    method: str,
) -> JsonResponse:
    error_message, error_code, status = _remote_api_error_details(error)
    logger.exception(
        "remote api request failed",
        context={
            "operation": operation,
            "method": method,
            "error_class": error.__class__.__name__,
            "error_code": error_code,
            "request_id": request_id,
        },
    )
    return _error_payload(
        config=config,
        request_id=request_id,
        error=error_message,
        error_code=error_code,
        status=status,
    )


def _apply_ordering(bucket: RemoteAPIBucket, ordering: RemoteOrdering) -> RemoteAPIBucket:
    if ordering is None:
        return bucket
    ordering_values = [ordering] if isinstance(ordering, str) else list(ordering)
    if not ordering_values:
        return bucket
    ordered_bucket = bucket
    for order_key in reversed(ordering_values):
        reverse = order_key.startswith("-")
        sort_key = order_key[1:] if reverse else order_key
        ordered_bucket = ordered_bucket.sort(sort_key, reverse=reverse)
    return ordered_bucket


def _build_query_view(config: RemoteAPIConfig) -> RemoteAPIView:
    """Return the `POST <base_path>/<resource_name>/query` view.

    The view validates the `X-General-Manager-Protocol-Version` header, requires
    `POST`, starts with `manager_cls.all()`, accepts an object body containing
    optional `filters`, `excludes`, `ordering`, `page`, and `page_size`, applies
    truthy `filters` and `excludes`, applies ordering, computes `total_count`,
    and then slices only for positive integer pagination. The success envelope
    includes `items`, `metadata.protocol_version`, `metadata.request_id`,
    response `X-Request-ID`, query-control metadata extras, and `total_count`.
    Errors are converted into sanitized JSON envelopes with an `X-Request-ID`.
    """

    def view(request: HttpRequest) -> HttpResponse:
        request_id = _request_id(request, prefix="query")
        if request.method != "POST":
            return _error_payload(
                config=config,
                request_id=request_id,
                error="Method not allowed.",
                error_code="method_not_allowed",
                status=405,
            )
        try:
            _check_protocol_version(request, config)
            manager_cls = cast(RemoteAPIManagerClass, config.manager_cls)
            body = _parse_json_body(request)
            filters = body.get("filters", {})
            excludes = body.get("excludes", {})
            ordering = body.get("ordering")
            page = body.get("page")
            page_size = body.get("page_size")
            bucket = manager_cls.all()
            if filters:
                bucket = bucket.filter(**cast(Mapping[str, object], filters))
            if excludes:
                bucket = bucket.exclude(**cast(Mapping[str, object], excludes))
            bucket = _apply_ordering(bucket, cast(RemoteOrdering, ordering))
            total_count = bucket.count()
            if (
                isinstance(page, int)
                and isinstance(page_size, int)
                and page > 0
                and page_size > 0
            ):
                start = (page - 1) * page_size
                end = start + page_size
                bucket = bucket[start:end]
            items = [_serialize_manager(item) for item in bucket]
            return _success_payload(
                items=items,
                total_count=total_count,
                config=config,
                request_id=request_id,
                metadata_extra={
                    "ordering": ordering,
                    "page": page,
                    "page_size": page_size,
                },
            )
        except (
            AttributeError,
            LookupError,
            ObjectDoesNotExist,
            PermissionError,
            RemoteAPIConfigurationError,
            RuntimeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as error:
            return _remote_api_error_payload(
                config=config,
                request_id=request_id,
                error=error,
                operation="query",
                method=request.method,
            )

    return view


def _build_item_view(config: RemoteAPIConfig) -> RemoteAPIItemView:
    """Return the item `GET`/`PATCH`/`DELETE` view for one URL identifier.

    `GET`, `PATCH`, and `DELETE` each respect the matching allow flag. The URL
    identifier is coerced through the configured identifier type, currently
    converting to `int` only when the interface `id` input type is exactly
    `int`; coercion failures are mapped to remote error envelopes. Disabled
    operations and unsupported methods return HTTP 405 without constructing a
    manager. `PATCH` requires an object JSON body and calls
    `manager.update(**payload)`. `DELETE` calls `manager.delete()`. Successful
    responses use the standard envelope; delete returns an empty item list.
    """

    def view(request: HttpRequest, identifier: str) -> HttpResponse:
        method = (request.method or "").upper()
        request_prefix = "detail" if method == "GET" else method.lower() or "item"
        request_id = _request_id(
            request,
            prefix=request_prefix,
            identifier=identifier,
        )
        try:
            _check_protocol_version(request, config)
            if method == "GET":
                if not config.allow_detail:
                    return _error_payload(
                        config=config,
                        request_id=request_id,
                        error="Method not allowed.",
                        error_code="method_not_allowed",
                        status=405,
                )
                manager_cls = cast(RemoteAPIManagerClass, config.manager_cls)
                detail_manager = manager_cls(id=_coerce_identifier(config, identifier))
                return _success_payload(
                    items=[_serialize_manager(detail_manager)],
                    config=config,
                    request_id=request_id,
                )
            if method == "PATCH":
                if not config.allow_update:
                    return _error_payload(
                        config=config,
                        request_id=request_id,
                        error="Method not allowed.",
                        error_code="method_not_allowed",
                        status=405,
                    )
                manager_cls = cast(RemoteAPIManagerClass, config.manager_cls)
                update_manager = cast(
                    RemoteAPIMutableManager,
                    manager_cls(id=_coerce_identifier(config, identifier)),
                )
                payload = _parse_json_body(request)
                updated = update_manager.update(**payload)
                return _success_payload(
                    items=[_serialize_manager(updated)],
                    config=config,
                    request_id=request_id,
                )
            if method == "DELETE":
                if not config.allow_delete:
                    return _error_payload(
                        config=config,
                        request_id=request_id,
                        error="Method not allowed.",
                        error_code="method_not_allowed",
                        status=405,
                    )
                manager_cls = cast(RemoteAPIManagerClass, config.manager_cls)
                delete_manager = cast(
                    RemoteAPIMutableManager,
                    manager_cls(id=_coerce_identifier(config, identifier)),
                )
                delete_manager.delete()
                return _success_payload(
                    items=[],
                    config=config,
                    request_id=request_id,
                )
            return _error_payload(
                config=config,
                request_id=request_id,
                error="Method not allowed.",
                error_code="method_not_allowed",
                status=405,
            )
        except (
            AttributeError,
            LookupError,
            ObjectDoesNotExist,
            PermissionError,
            RemoteAPIConfigurationError,
            RuntimeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as error:
            return _remote_api_error_payload(
                config=config,
                request_id=request_id,
                error=error,
                operation=method.lower(),
                method=method,
            )

    return view


def _build_create_view(config: RemoteAPIConfig) -> RemoteAPIView:
    """Return the `POST <base_path>/<resource_name>` create view.

    The view validates the protocol header, requires `POST`, requires an object
    JSON body, calls the manager create operation, and returns the standard
    envelope with HTTP 201. Disabled or unsupported methods return HTTP 405
    without calling `manager_cls.create()`. Errors are converted into sanitized
    JSON envelopes with an `X-Request-ID`.
    """

    def view(request: HttpRequest) -> HttpResponse:
        request_id = _request_id(request, prefix="create")
        if request.method != "POST":
            return _error_payload(
                config=config,
                request_id=request_id,
                error="Method not allowed.",
                error_code="method_not_allowed",
                status=405,
            )
        try:
            _check_protocol_version(request, config)
            manager_cls = cast(RemoteAPIManagerClass, config.manager_cls)
            payload = _parse_json_body(request)
            manager = manager_cls.create(**payload)
            return _success_payload(
                items=[_serialize_manager(manager)],
                config=config,
                request_id=request_id,
                status=201,
            )
        except (
            AttributeError,
            LookupError,
            ObjectDoesNotExist,
            PermissionError,
            RemoteAPIConfigurationError,
            RuntimeError,
            TypeError,
            ValidationError,
            ValueError,
        ) as error:
            return _remote_api_error_payload(
                config=config,
                request_id=request_id,
                error=error,
                operation="create",
                method="POST",
            )

    return view
