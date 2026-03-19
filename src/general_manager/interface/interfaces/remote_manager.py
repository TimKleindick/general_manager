"""RemoteManagerInterface built on top of RequestInterface."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit
from typing import Any, ClassVar, cast

from general_manager.cache.dependency_index import (
    get_full_index,
    invalidate_cache_key,
    remove_cache_key_from_index,
)
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.base_interface import CapabilityOverride
from general_manager.interface.bundles.remote_manager import (
    REMOTE_MANAGER_CAPABILITIES,
)
from general_manager.interface.capabilities.remote_manager import (
    RemoteManagerQueryCapability,
    validate_remote_manager_meta,
)
from general_manager.interface.interfaces.request import RequestInterface
from general_manager.interface.requests import (
    RequestMutationOperation,
    RequestQueryOperation,
    RequestOperation,
    RequestPlan,
    RequestTransportConfig,
    RequestTransportResponse,
    RequestConfigurationError,
    RequestQueryResult,
    RequestSchemaError,
)


def _normalize_remote_envelope(
    response: RequestTransportResponse | Mapping[str, Any] | list[Mapping[str, Any]],
    interface_cls: type[Any],
    operation: RequestOperation,
    plan: RequestPlan,
) -> RequestQueryResult:
    metadata: dict[str, Any] = dict(plan.metadata)
    payload: Any
    if isinstance(response, RequestTransportResponse):
        payload = response.payload
        metadata.update(response.metadata)
        metadata["status_code"] = response.status_code
        metadata.setdefault("retry_count", 0)
        request_id = response.headers.get("x-request-id")
        if request_id is not None:
            metadata["request_id"] = request_id
    else:
        payload = response

    if isinstance(payload, list):
        if not all(isinstance(item, Mapping) for item in payload):
            raise RequestSchemaError.non_object_json_payload()
        return RequestQueryResult(items=tuple(payload), metadata=metadata)
    if not isinstance(payload, dict):
        raise RequestSchemaError.non_object_json_payload()
    items = payload.get("items", [])
    if not isinstance(items, list):
        raise RequestSchemaError.non_object_json_payload()
    total_count = payload.get("total_count")
    payload_metadata = payload.get("metadata", {})
    if not all(isinstance(item, Mapping) for item in items):
        raise RequestSchemaError.non_object_json_payload()
    if not isinstance(payload_metadata, Mapping):
        raise RequestSchemaError.non_object_json_payload()
    if total_count is not None and not isinstance(total_count, int):
        raise RequestSchemaError.non_object_json_payload()
    if "error" in payload:
        raise RequestConfigurationError.unmapped_remote_error(interface_cls.__name__)
    metadata.update(payload_metadata)
    return RequestQueryResult(
        items=tuple(items), total_count=total_count, metadata=metadata
    )


class RemoteManagerInterface(RequestInterface):
    """Request-backed interface specialized for exposed GeneralManager REST endpoints."""

    base_url: ClassVar[str] = ""
    base_path: ClassVar[str] = "/gm"
    remote_manager: ClassVar[str] = ""
    protocol_version: ClassVar[str] = "v1"
    websocket_invalidation_enabled: ClassVar[bool] = False
    capability_overrides: ClassVar[dict[CapabilityName, CapabilityOverride]] = cast(
        dict[CapabilityName, CapabilityOverride],
        {"query": RemoteManagerQueryCapability},
    )
    configured_capabilities = (REMOTE_MANAGER_CAPABILITIES,)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls is RemoteManagerInterface:
            return
        if RemoteManagerInterface not in cls.__bases__:
            return

        meta_class = getattr(cls, "Meta", None)
        cls.base_url = getattr(meta_class, "base_url", "")
        raw_base_path = getattr(meta_class, "base_path", None)
        cls.base_path = "/gm" if raw_base_path is None else raw_base_path
        cls.remote_manager = getattr(meta_class, "remote_manager", "")
        cls.protocol_version = getattr(meta_class, "protocol_version", "v1")
        cls.websocket_invalidation_enabled = bool(
            getattr(meta_class, "websocket_invalidation_enabled", False)
        )
        validate_remote_manager_meta(cls)

        normalized_base_path = cls.base_path.rstrip("/") or "/gm"
        cls.base_path = normalized_base_path
        resource_path = f"{normalized_base_path}/{cls.remote_manager}".rstrip("/")
        protocol_headers = {
            "X-General-Manager-Protocol-Version": cls.protocol_version,
        }

        cls.query_operations = {
            "detail": RequestQueryOperation(
                name="detail",
                method="GET",
                path=f"{resource_path}/{{id}}",
                static_headers=protocol_headers,
            ),
            "list": RequestQueryOperation(
                name="list",
                method="POST",
                path=f"{resource_path}/query",
                collection=True,
                static_headers=protocol_headers,
            ),
        }
        cls.create_operation = RequestMutationOperation(
            name="create",
            method="POST",
            path=resource_path,
            static_headers=protocol_headers,
        )
        cls.update_operation = RequestMutationOperation(
            name="update",
            method="PATCH",
            path=f"{resource_path}/{{id}}",
            static_headers=protocol_headers,
        )
        cls.delete_operation = RequestMutationOperation(
            name="delete",
            method="DELETE",
            path=f"{resource_path}/{{id}}",
            static_headers=protocol_headers,
        )

        transport_config = getattr(cls, "transport_config", None)
        if transport_config is None:
            cls.transport_config = RequestTransportConfig(
                base_url=cls.base_url,
                response_normalizer=_normalize_remote_envelope,
            )
        else:
            cls.transport_config = RequestTransportConfig(
                base_url=cls.base_url or transport_config.base_url,
                timeout=transport_config.timeout,
                auth_provider=transport_config.auth_provider,
                response_normalizer=_normalize_remote_envelope,
                retry_policy=transport_config.retry_policy,
                metrics_backend=transport_config.metrics_backend,
                trace_backend=transport_config.trace_backend,
            )

    @classmethod
    def get_websocket_invalidation_url(cls) -> str:
        parsed = urlsplit(cls.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        normalized_base_path = cls.base_path.rstrip("/") or "/gm"
        base_url_path = parsed.path.rstrip("/")
        if base_url_path:
            path = f"{base_url_path}{normalized_base_path}/ws/{cls.remote_manager}"
        else:
            path = f"{normalized_base_path}/ws/{cls.remote_manager}"
        query = urlencode({"version": cls.protocol_version})
        return urlunsplit((scheme, parsed.netloc, path, query, ""))

    @classmethod
    def handle_invalidation_event(cls, event: Mapping[str, Any]) -> bool:
        if (
            event.get("protocol_version") != cls.protocol_version
            or event.get("base_path") != cls.base_path
            or event.get("resource_name") != cls.remote_manager
        ):
            return False
        manager_name = cls._parent_class.__name__
        idx = get_full_index()
        request_queries = cast(
            dict[str, set[str]],
            idx.get("request_query", {}).get(manager_name, {}),
        )
        for cache_keys in list(request_queries.values()):
            for cache_key in list(cache_keys):
                invalidate_cache_key(cache_key)
                remove_cache_key_from_index(cache_key)
        return True
