"""Opt-in REST exposure for GeneralManager classes."""

from __future__ import annotations

import json
import re
from uuid import uuid4
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Mapping, TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path

from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.remote")

_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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


@dataclass(frozen=True, slots=True)
class RemoteAPIConfig:
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


def add_remote_api_urls(manager_classes: list[type["GeneralManager"]]) -> None:
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
            query_route._general_manager_remote_api = True
            query_route._general_manager_remote_api_key = (route_prefix, "query")
            urlconf.urlpatterns.append(query_route)
        if (
            any((config.allow_detail, config.allow_update, config.allow_delete))
            and (route_prefix, "item") not in existing
        ):
            detail_route = path(
                f"{route_prefix}/<str:identifier>",
                _build_item_view(config),
            )
            detail_route._general_manager_remote_api = True
            detail_route._general_manager_remote_api_key = (route_prefix, "item")
            urlconf.urlpatterns.append(detail_route)
        if config.allow_create and (route_prefix, "create") not in existing:
            create_route = path(
                route_prefix,
                _build_create_view(config),
            )
            create_route._general_manager_remote_api = True
            create_route._general_manager_remote_api_key = (route_prefix, "create")
            urlconf.urlpatterns.append(create_route)


def clear_remote_api_urls() -> None:
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


def _parse_json_body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


def _coerce_identifier(config: RemoteAPIConfig, identifier: str) -> Any:
    return int(identifier) if identifier.isdigit() else identifier


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


def _serialize_manager(manager: "GeneralManager") -> dict[str, Any]:
    return dict(manager)


def _success_payload(
    *,
    items: list[dict[str, Any]],
    config: RemoteAPIConfig,
    request_id: str,
    total_count: int | None = None,
    status: int = 200,
    metadata_extra: Mapping[str, Any] | None = None,
) -> JsonResponse:
    payload: dict[str, Any] = {"items": items}
    metadata: dict[str, Any] = {
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
    details: Mapping[str, Any] | None = None,
) -> JsonResponse:
    payload: dict[str, Any] = {
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


def _apply_ordering(
    bucket: Any, ordering: str | list[str] | tuple[str, ...] | None
) -> Any:
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


def _build_query_view(config: RemoteAPIConfig):
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
            body = _parse_json_body(request)
            filters = body.get("filters", {})
            excludes = body.get("excludes", {})
            ordering = body.get("ordering")
            page = body.get("page")
            page_size = body.get("page_size")
            bucket: Any = config.manager_cls.all()
            if filters:
                bucket = bucket.filter(**filters)
            if excludes:
                bucket = bucket.exclude(**excludes)
            bucket = _apply_ordering(bucket, ordering)
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


def _build_item_view(config: RemoteAPIConfig):
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
                manager = config.manager_cls(id=_coerce_identifier(config, identifier))
                return _success_payload(
                    items=[_serialize_manager(manager)],
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
                manager = config.manager_cls(id=_coerce_identifier(config, identifier))
                payload = _parse_json_body(request)
                updated = manager.update(**payload)
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
                manager = config.manager_cls(id=_coerce_identifier(config, identifier))
                manager.delete()
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


def _build_create_view(config: RemoteAPIConfig):
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
            payload = _parse_json_body(request)
            manager = config.manager_cls.create(**payload)
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
