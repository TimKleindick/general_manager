"""RemoteManagerInterface built on top of RequestInterface."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit
from typing import ClassVar, cast

from general_manager.cache.dependency_index import (
    invalidate_request_query_dependencies,
)
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.factory import CapabilityOverride
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
    RequestPayload,
    RequestResponse,
    RequestTransportConfig,
    RequestTransportResponse,
    RequestConfigurationError,
    RequestQueryResult,
    RequestSchemaError,
)


def _normalize_remote_envelope(
    response: RequestTransportResponse | RequestResponse,
    interface_cls: type[object],
    operation: RequestOperation,
    plan: RequestPlan,
) -> RequestQueryResult:
    """Convert a remote-manager JSON envelope into a query result.

    Args:
        response: Either a decoded transport response or a decoded response body.
            Bodies may be a list of item mappings or an object envelope with
            optional ``items``, ``total_count``, and ``metadata`` keys.
        interface_cls: Interface class used only to identify unmapped remote
            errors in configuration exceptions.
        operation: Request operation being normalized. The remote-manager
            normalizer currently does not branch on the operation.
        plan: Request plan whose metadata is copied into the normalized result.

    Returns:
        A ``RequestQueryResult`` with tuple-normalized item mappings, optional
        total count, and merged metadata. Metadata precedence is plan metadata,
        then transport metadata, then transport ``status_code`` and
        ``retry_count`` defaulting to ``0``, then transport ``x-request-id`` as
        ``request_id``, and finally envelope ``metadata``. An envelope that omits
        ``items`` returns an empty item tuple.

    Raises:
        RequestSchemaError: If the payload is not a list of object items or an
            object envelope with list ``items``, mapping ``metadata``, and an
            integer ``total_count`` when supplied.
        RequestConfigurationError: If the payload envelope contains ``error``;
            remote-manager errors do not map to local request operations.
    """
    del operation

    metadata: dict[str, object] = dict(plan.metadata)
    payload: RequestResponse
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
    if not isinstance(payload, Mapping):
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
    metadata.update(cast(RequestPayload, payload_metadata))
    return RequestQueryResult(
        items=tuple(cast(RequestPayload, item) for item in items),
        total_count=total_count,
        metadata=metadata,
    )


class RemoteManagerInterface(RequestInterface):
    """Request-backed interface for a remote GeneralManager REST resource.

    Direct subclasses read ``Meta.base_url``, optional ``Meta.base_path``,
    ``Meta.remote_manager``, optional ``Meta.protocol_version``, and optional
    ``Meta.websocket_invalidation_enabled`` when the class is defined. The
    interface validates that metadata, defaults ``base_path`` to ``/gm`` when it
    is omitted, defaults ``protocol_version`` to ``"v1"``, coerces
    ``websocket_invalidation_enabled`` with ``bool(...)``, normalizes
    ``base_path``, installs the
    generated ``GET <base_path>/<remote_manager>/{id}`` detail query,
    ``POST <base_path>/<remote_manager>/query`` list query, and
    create/update/delete mutation operations, and configures response
    normalization for the remote-manager envelope. Generated operations include
    the ``X-General-Manager-Protocol-Version`` header. If a subclass already
    defines ``transport_config``, its timeout, auth provider, retry policy,
    metrics backend, and trace backend are preserved while the validated
    ``Meta.base_url`` and response normalizer replace the transport's values.

    Configuration errors are raised during direct subclass creation by
    ``validate_remote_manager_meta(...)``. Indirect subclasses are not
    reconfigured by this class hook. Query execution can additionally raise
    request operation, schema, transport, serializer, or validator errors from
    the underlying request interface.
    """

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

    def __init_subclass__(cls, **kwargs: object) -> None:
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
                base_url=cls.base_url,
                timeout=transport_config.timeout,
                auth_provider=transport_config.auth_provider,
                response_normalizer=_normalize_remote_envelope,
                retry_policy=transport_config.retry_policy,
                metrics_backend=transport_config.metrics_backend,
                trace_backend=transport_config.trace_backend,
            )

    @classmethod
    def get_websocket_invalidation_url(cls) -> str:
        """Return the websocket URL used for remote cache invalidation.

        The URL is derived from ``base_url`` and the normalized ``base_path``.
        Class creation validates ``base_url`` as ``http`` or ``https`` before
        this helper is available on a configured interface. ``https`` base URLs
        become ``wss`` and valid ``http`` base URLs become ``ws``. A path prefix
        already present on ``base_url`` is stripped of trailing slashes and
        preserved before ``<base_path>/ws/<remote_manager>``. Query and fragment
        components from ``base_url`` are not preserved; the protocol version is
        emitted as the ``version`` query parameter. This helper does not check
        ``websocket_invalidation_enabled`` by itself.

        Returns:
            Absolute websocket URL for this interface's invalidation stream.
        """
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
    def handle_invalidation_event(cls, event: Mapping[str, object]) -> bool:
        """Invalidate cached remote queries when a websocket event matches.

        Args:
            event: Decoded websocket payload. Only ``protocol_version``,
                ``base_path``, and ``resource_name`` are used for matching.
                ``action``, ``identification``, and ``event_id`` are intentionally
                ignored by the local request-query cache invalidation path.

        Returns:
            ``True`` when the event matches this interface and invalidation was
            requested for the parent manager; ``False`` for non-matching events.

        Raises:
            AttributeError: If called before the interface is bound to a parent
                manager class.
            Exception: Propagates cache invalidation backend errors.
        """
        if (
            event.get("protocol_version") != cls.protocol_version
            or event.get("base_path") != cls.base_path
            or event.get("resource_name") != cls.remote_manager
        ):
            return False
        manager_name = cls._parent_class.__name__
        invalidate_request_query_dependencies(manager_name)
        return True
