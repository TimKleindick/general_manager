"""
Bootstrap phases for the General Manager Django app.

Each function corresponds to one independent step in ``GeneralmanagerConfig.ready()``.
Keeping the logic here (instead of inside the ``AppConfig`` class) makes every phase
independently importable and testable.
"""

from __future__ import annotations

import contextlib
import importlib.abc
import os
import re
import sys
from collections.abc import Coroutine
from importlib import import_module, machinery, util
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Callable,
    Iterable,
    Iterator,
    Protocol,
    Sequence,
    cast,
)

import graphene
from django.conf import settings
from django.core.checks import register
from django.core.management.base import BaseCommand
from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.urls import path, re_path
from graphql import GraphQLDirective, specified_directives

from general_manager.api.graphql_view import GeneralManagerGraphQLView
from general_manager.api.graphql import GraphQL
from general_manager.api.remote_api import add_remote_api_urls
from general_manager.api.remote_invalidation import ensure_remote_invalidation_route
from general_manager.conf import get_setting
from general_manager.api.property import GraphQLProperty
from general_manager.logging import get_logger
from general_manager.interface.infrastructure.startup_hooks import (
    DependencyResolver,
    registered_startup_hook_entries,
    order_interfaces_by_dependency,
)
from general_manager.interface.infrastructure.system_checks import (
    iter_interface_system_checks,
)


class _AppendableRoutes(Protocol):
    """Route container shape required by ASGI subscription wiring."""

    def append(self, route: object) -> None: ...

    def __iter__(self) -> Iterator[object]: ...


class _ApplicationMapping(Protocol):
    """Channels protocol router shape used for in-place websocket insertion."""

    application_mapping: dict[str, object]


class _CreateModuleLoader(Protocol):
    """Optional importlib loader hook for custom module creation."""

    def create_module(self, spec: machinery.ModuleSpec) -> ModuleType | None: ...


class _ExecModuleLoader(Protocol):
    """Importlib loader hook required for deferred ASGI module execution."""

    def exec_module(self, module: ModuleType) -> None: ...


class _AsgiModuleRoutes(Protocol):
    """ASGI module attribute patched by subscription route setup."""

    websocket_urlpatterns: object


class _GraphQLWebsocketRouteMarker(Protocol):
    """Dynamic marker attached to generated GraphQL websocket URL patterns."""

    _general_manager_graphql_ws: bool


class _FieldDescriptorCache(Protocol):
    """Interface classes that cache generated field descriptors."""

    _field_descriptors: object | None


class _GeneratedRelationProperty(Protocol):
    """GraphQL property shape after attaching the generated-relation marker."""

    _general_manager_generated_relation: bool


from general_manager.metrics import build_graphql_middleware

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("apps")


# ---------------------------------------------------------------------------
# Error classes
# ---------------------------------------------------------------------------


class MissingRootUrlconfError(RuntimeError):
    """Raised when Django settings do not define ROOT_URLCONF."""

    def __init__(self) -> None:
        super().__init__("ROOT_URLCONF not found in settings.")


class InvalidPermissionClassError(TypeError):
    """Raised when a GeneralManager Permission attribute is not a BasePermission subclass."""

    def __init__(self, permission_name: str) -> None:
        super().__init__(f"{permission_name} must be a subclass of BasePermission.")


class InvalidGraphQLDirectiveError(TypeError):
    """Raised when GRAPHQL_DIRECTIVES contains an invalid entry."""

    def __init__(self, *, index: int, directive: object) -> None:
        directive_type = type(directive).__name__
        super().__init__(
            "GRAPHQL_DIRECTIVES must contain GraphQLDirective instances; "
            f"entry {index} is {directive_type}."
        )


class InvalidGraphQLDirectivesSettingError(TypeError):
    """Raised when GRAPHQL_DIRECTIVES is not iterable."""

    def __init__(self, directives: object) -> None:
        directive_type = type(directives).__name__
        super().__init__(
            "GRAPHQL_DIRECTIVES must be an iterable of GraphQLDirective "
            f"instances; got {directive_type}."
        )


class DuplicateGraphQLDirectiveError(ValueError):
    """Raised when a custom directive name collides with another directive."""

    def __init__(self, directive_name: str) -> None:
        super().__init__(
            f"Duplicate GraphQL directive name '{directive_name}' is not allowed."
        )


# ---------------------------------------------------------------------------
# Phase 1: install management-command startup hooks
# ---------------------------------------------------------------------------


def install_startup_hook_runner() -> None:
    """
    Install a runner that executes registered startup hooks before Django management
    commands run.

    Idempotent: does nothing if already installed.
    """
    if getattr(BaseCommand, "_gm_startup_hooks_runner_installed", False):
        return

    original_run_from_argv = BaseCommand.run_from_argv

    def run_from_argv_with_startup_hooks(
        self: BaseCommand,
        argv: Sequence[str],
    ) -> None:
        run_main = os.environ.get("RUN_MAIN") == "true"
        command = argv[1] if len(argv) > 1 else None
        should_run_hooks = command != "runserver" or run_main
        hooks_registry = registered_startup_hook_entries() if should_run_hooks else {}
        if hooks_registry:
            resolver_groups: list[
                tuple[DependencyResolver | None, list[type[object]]]
            ] = []

            def _group_for_resolver(
                resolver: DependencyResolver | None,
            ) -> list[type[object]]:
                for registered_resolver, resolver_interfaces in resolver_groups:
                    if registered_resolver is resolver:
                        return resolver_interfaces
                new_group: list[type[object]] = []
                resolver_groups.append((resolver, new_group))
                return new_group

            for interface_cls, entries in hooks_registry.items():
                for entry in entries:
                    _group_for_resolver(entry.dependency_resolver).append(interface_cls)
            logger.debug(
                "running startup hooks",
                context={
                    "command": command,
                    "count": sum(len(v) for v in hooks_registry.values()),
                    "autoreload": not run_main if command == "runserver" else False,
                },
            )
            for resolver, iface_list in resolver_groups:
                ordered_interfaces = order_interfaces_by_dependency(
                    iface_list,
                    resolver,
                )
                for interface_cls in ordered_interfaces:
                    for entry in hooks_registry.get(interface_cls, ()):
                        if entry.dependency_resolver is resolver:
                            try:
                                entry.hook()
                            except Exception:
                                logger.exception(
                                    "startup hook failed",
                                    context={"interface": interface_cls.__name__},
                                )
            logger.debug(
                "finished startup hooks",
                context={
                    "command": command,
                    "count": sum(len(v) for v in hooks_registry.values()),
                },
            )
        result = original_run_from_argv(self, argv)
        return result

    BaseCommand.run_from_argv = run_from_argv_with_startup_hooks  # type: ignore[method-assign]
    BaseCommand._gm_startup_hooks_runner_installed = True  # type: ignore[attr-defined]
    BaseCommand._gm_original_run_from_argv = original_run_from_argv  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Phase 2: Django system checks
# ---------------------------------------------------------------------------


def _wrap_system_check(
    interface_cls: type[object],
    hook: Callable[[], list[object]],
) -> Callable[..., list[object]]:
    """Wrap a system-check hook so exceptions are caught and logged."""

    def _check(*_: object, **__: object) -> list[object]:
        try:
            return hook()
        except Exception:
            logger.exception(
                "system check hook failed",
                context={"interface": interface_cls.__name__},
            )
            return []

    return _check


# Tracks which Interface classes have already had their system checks registered
# so that repeated ready() calls (e.g. during tests) do not double-register.
_registered_system_check_interfaces: set[str] = set()


def _interface_identity(interface_cls: type[object]) -> str:
    """Return the module-qualified identity used for bootstrap idempotency."""
    return f"{interface_cls.__module__}.{interface_cls.__qualname__}"


def register_system_checks() -> None:
    """Register capability-provided system checks with Django's check framework.

    Idempotent: a given module-qualified interface class's checks are registered
    at most once per process lifetime, so calling ``ready()`` multiple times
    (common in tests) does not produce duplicate check results. Distinct
    interface classes with the same bare ``__name__`` still register separately.
    """
    hooks = list(iter_interface_system_checks())
    if not hooks:
        return
    new_hooks = [
        (iface, hook)
        for iface, hook in hooks
        if _interface_identity(iface) not in _registered_system_check_interfaces
    ]
    if not new_hooks:
        return
    logger.debug(
        "registering capability system checks",
        context={"count": len(new_hooks)},
    )
    for interface_cls, hook in new_hooks:
        _registered_system_check_interfaces.add(_interface_identity(interface_cls))
        register("general_manager")(_wrap_system_check(interface_cls, hook))


# ---------------------------------------------------------------------------
# Phase 3: manager class initialization
# ---------------------------------------------------------------------------


def check_permission_class(general_manager_class: type[GeneralManager]) -> None:
    """Validate and normalize a GeneralManager class's Permission attribute."""
    from general_manager.permission.base_permission import BasePermission
    from general_manager.permission.manager_based_permission import (
        AdditiveManagerPermission,
    )

    if hasattr(general_manager_class, "Permission"):
        permission = general_manager_class.Permission
        if not (
            isinstance(permission, type) and issubclass(permission, BasePermission)
        ):
            permission_name = getattr(permission, "__name__", repr(permission))
            raise InvalidPermissionClassError(permission_name)
    else:
        general_manager_class.Permission = AdditiveManagerPermission


def initialize_general_manager_classes(
    pending_attribute_initialization: list[type[GeneralManager]],
    all_classes: list[type[GeneralManager]],
) -> None:
    """
    Initialize GeneralManager interface attributes, create attribute accessors,
    wire GraphQL connection properties, and validate permission configuration.
    """
    from general_manager.manager.general_manager import GeneralManager as _GM
    from general_manager.manager.input import Input
    from general_manager.manager.meta import GeneralManagerMeta

    logger.debug(
        "initializing general manager classes",
        context={
            "pending_attributes": len(pending_attribute_initialization),
            "total": len(all_classes),
        },
    )

    def _build_connection_resolver(
        attribute_key: str, manager_cls: type[_GM]
    ) -> Callable[[object], object]:
        def resolver(value: object) -> object:
            return manager_cls.filter(**{attribute_key: value})

        resolver.__annotations__ = {"return": manager_cls}
        return resolver

    def _iter_manager_relation_fields(
        general_manager_class: type[_GM],
    ) -> Iterable[tuple[str, type[_GM]]]:
        seen: set[str] = set()
        input_fields = getattr(general_manager_class.Interface, "input_fields", {})
        for attribute_name, attribute in input_fields.items():
            if isinstance(attribute, Input) and issubclass(attribute.type, _GM):
                seen.add(attribute_name)
                yield attribute_name, attribute.type

        get_attribute_types = getattr(
            general_manager_class.Interface, "get_attribute_types", None
        )
        if not callable(get_attribute_types):
            return
        try:
            attribute_types = get_attribute_types()
        except NotImplementedError:
            return
        for attribute_name, metadata in attribute_types.items():
            if attribute_name in seen or metadata.get("relation_kind") != "direct":
                continue
            field_type = metadata.get("type")
            if isinstance(field_type, type) and issubclass(field_type, _GM):
                yield attribute_name, field_type

    def _has_reverse_relation_attribute(
        manager_class: type[_GM],
        related_manager_class: type[_GM],
    ) -> bool:
        get_attribute_types = getattr(
            manager_class.Interface, "get_attribute_types", None
        )
        if not callable(get_attribute_types):
            return False
        try:
            attribute_types = get_attribute_types()
        except NotImplementedError:
            return False
        return any(
            metadata.get("is_derived", False)
            and metadata.get("relation_kind") in {"collection", "direct"}
            and metadata.get("type") is related_manager_class
            for metadata in attribute_types.values()
        )

    def _to_snake_case(name: str) -> str:
        snake = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", snake)
        return snake.lower()

    logger.debug(
        "creating manager attributes",
        context={"pending_attributes": len(pending_attribute_initialization)},
    )
    for general_manager_class in dict.fromkeys(all_classes):
        interface = getattr(general_manager_class, "Interface", None)
        if hasattr(interface, "_field_descriptors"):
            interface_with_cache = cast(_FieldDescriptorCache, interface)
            interface_with_cache._field_descriptors = None

    for general_manager_class in pending_attribute_initialization:
        attributes = general_manager_class.Interface.get_attributes()
        general_manager_class._attributes = attributes
        GeneralManagerMeta.create_at_properties_for_attributes(
            attributes.keys(), general_manager_class
        )

    logger.debug(
        "linking manager inputs",
        context={"total_classes": len(all_classes)},
    )
    for general_manager_class in all_classes:
        for attribute_name, connected_manager in _iter_manager_relation_fields(
            general_manager_class
        ):
            if _has_reverse_relation_attribute(
                connected_manager,
                general_manager_class,
            ):
                continue
            resolver = _build_connection_resolver(attribute_name, general_manager_class)
            relation_property = GraphQLProperty(resolver)
            marked_relation_property = cast(
                _GeneratedRelationProperty,
                relation_property,
            )
            marked_relation_property._general_manager_generated_relation = True
            setattr(
                connected_manager,
                f"{_to_snake_case(general_manager_class.__name__)}_list",
                relation_property,
            )
    for general_manager_class in all_classes:
        check_permission_class(general_manager_class)


def handle_remote_api(
    manager_classes: list[type[GeneralManager]],
) -> None:
    """Generate REST routes for opt-in RemoteAPI manager exposures."""
    add_remote_api_urls(manager_classes)
    ensure_remote_invalidation_route(manager_classes)


# ---------------------------------------------------------------------------
# Phase 5: GraphQL schema
# ---------------------------------------------------------------------------


def _normalize_graphql_directives(
    directives: Iterable[GraphQLDirective] | GraphQLDirective | None,
) -> tuple[GraphQLDirective, ...]:
    """Validate a settings-provided directive collection and preserve order."""
    if directives is None:
        return ()

    if isinstance(directives, GraphQLDirective):
        candidates: Iterable[object] = (directives,)
    else:
        if isinstance(directives, (str, bytes)) or not isinstance(directives, Iterable):
            raise InvalidGraphQLDirectivesSettingError(directives)
        candidates = directives

    normalized: list[GraphQLDirective] = []
    for index, directive in enumerate(candidates):
        if not isinstance(directive, GraphQLDirective):
            raise InvalidGraphQLDirectiveError(index=index, directive=directive)
        normalized.append(directive)
    return tuple(normalized)


def _build_schema_directives(
    directives: Iterable[GraphQLDirective] | GraphQLDirective | None = None,
) -> tuple[GraphQLDirective, ...]:
    """Return built-in directives followed by validated custom directives."""
    custom_directives = _normalize_graphql_directives(directives)
    used_names = {directive.name for directive in specified_directives}

    for directive in custom_directives:
        if directive.name in used_names:
            raise DuplicateGraphQLDirectiveError(directive.name)
        used_names.add(directive.name)

    return (*specified_directives, *custom_directives)


def _get_configured_graphql_directives() -> tuple[GraphQLDirective, ...]:
    """Resolve and validate custom GraphQL directives from Django settings."""
    configured = cast(
        Iterable[GraphQLDirective] | GraphQLDirective | None,
        get_setting("GRAPHQL_DIRECTIVES", ()),
    )
    return _normalize_graphql_directives(configured)


def handle_graph_ql(
    pending_graphql_interfaces: list[type[GeneralManager]],
) -> None:
    """
    Generate GraphQL interfaces and mutations, build a ``graphene.Schema``, and
    add the HTTP and ASGI subscription routes to the project's URL configuration.
    """

    logger.debug(
        "creating graphql interfaces and mutations",
        context={"pending": len(pending_graphql_interfaces)},
    )
    GraphQL.manager_registry.update(
        {
            general_manager_class.__name__: general_manager_class
            for general_manager_class in pending_graphql_interfaces
            if getattr(general_manager_class, "Interface", None) is not None
        }
    )
    for general_manager_class in pending_graphql_interfaces:
        GraphQL.create_graphql_interface(general_manager_class)
        GraphQL.create_graphql_mutation(general_manager_class)

    GraphQL.register_file_upload_mutation()
    GraphQL.register_search_query()
    GraphQL.register_current_user_capabilities()

    query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
    GraphQL._query_class = query_class

    if GraphQL._mutations:
        mutation_class = type(
            "Mutation",
            (graphene.ObjectType,),
            {name: mutation.Field() for name, mutation in GraphQL._mutations.items()},
        )
        GraphQL._mutation_class = mutation_class
    else:
        GraphQL._mutation_class = None

    if GraphQL._subscription_fields:
        subscription_class = type(
            "Subscription",
            (graphene.ObjectType,),
            GraphQL._subscription_fields,
        )
        GraphQL._subscription_class = subscription_class
    else:
        GraphQL._subscription_class = None

    schema_kwargs: dict[str, object] = {"query": GraphQL._query_class}
    if GraphQL._mutation_class is not None:
        schema_kwargs["mutation"] = GraphQL._mutation_class
    if GraphQL._subscription_class is not None:
        schema_kwargs["subscription"] = GraphQL._subscription_class
    custom_directives = _get_configured_graphql_directives()
    if custom_directives:
        schema_kwargs["directives"] = _build_schema_directives(custom_directives)
    schema = graphene.Schema(**schema_kwargs)
    GraphQL._schema = schema
    from general_manager.uploads.urls import add_file_upload_urls

    add_file_upload_urls()
    add_graphql_url(schema)


# ---------------------------------------------------------------------------
# Phase 5a: URL + ASGI wiring
# ---------------------------------------------------------------------------


def add_graphql_url(schema: graphene.Schema) -> None:
    """Add a GraphQL endpoint to the project's URL configuration."""
    graph_ql_url = str(get_setting("GRAPHQL_URL", "graphql"))
    root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
    logger.debug(
        "configuring graphql http endpoint",
        context={
            "root_urlconf": root_url_conf_path,
            "graphql_url": graph_ql_url,
        },
    )
    if not root_url_conf_path:
        raise MissingRootUrlconfError()
    urlconf = import_module(root_url_conf_path)
    middleware = build_graphql_middleware()
    view_kwargs: dict[str, object] = {"graphiql": True, "schema": schema}
    if middleware is not None:
        view_kwargs["middleware"] = middleware
    view = cast(
        Callable[[HttpRequest], HttpResponseBase],
        GeneralManagerGraphQLView.as_view(**view_kwargs),
    )
    urlconf.urlpatterns.append(
        path(
            graph_ql_url,
            view,
        )
    )
    _ensure_asgi_subscription_route(graph_ql_url)


def _ensure_asgi_subscription_route(graphql_url: str) -> None:
    """Wire a WebSocket subscription route into the project's ASGI application."""
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        logger.debug(
            "asgi application missing",
            context={"graphql_url": graphql_url},
        )
        return

    try:
        module_path, attr_name = asgi_path.rsplit(".", 1)
    except ValueError:
        logger.warning(
            "invalid asgi application path",
            context={"asgi_application": asgi_path},
        )
        return

    try:
        asgi_module = import_module(module_path)
    except RuntimeError as exc:
        if "populate() isn't reentrant" not in str(exc):
            logger.warning(
                "unable to import asgi module",
                context={
                    "module": module_path,
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                exc_info=True,
            )
            return

        spec = util.find_spec(module_path)
        if spec is None or spec.loader is None:
            logger.warning(
                "missing loader for asgi module",
                context={"module": module_path},
            )
            return

        original_spec = spec

        def finalize(module: ModuleType) -> None:
            _finalize_asgi_module(module, attr_name, graphql_url)

        class _Loader(importlib.abc.Loader):
            def __init__(self, original_loader: importlib.abc.Loader) -> None:
                self._original_loader = original_loader

            def create_module(
                self,
                spec: machinery.ModuleSpec,
            ) -> ModuleType | None:
                if hasattr(self._original_loader, "create_module"):
                    return cast(
                        _CreateModuleLoader,
                        self._original_loader,
                    ).create_module(spec)
                return None

            def exec_module(self, module: ModuleType) -> None:
                try:
                    cast(_ExecModuleLoader, self._original_loader).exec_module(module)
                    finalize(module)
                finally:
                    with contextlib.suppress(ValueError):
                        sys.meta_path.remove(finder)

        wrapped_loader = _Loader(spec.loader)

        class _Finder(importlib.abc.MetaPathFinder):
            def __init__(self) -> None:
                self._processed = False

            def find_spec(
                self,
                fullname: str,
                path: Sequence[str] | None,
                target: ModuleType | None = None,
            ) -> machinery.ModuleSpec | None:
                if fullname != module_path or self._processed:
                    return None
                self._processed = True
                new_spec = util.spec_from_loader(fullname, wrapped_loader)
                if new_spec is not None:
                    new_spec.submodule_search_locations = (
                        original_spec.submodule_search_locations
                    )
                return new_spec

        finder = _Finder()
        sys.meta_path.insert(0, finder)
        return
    except ImportError as exc:  # pragma: no cover - defensive
        logger.warning(
            "unable to import asgi module",
            context={
                "module": module_path,
                "error": type(exc).__name__,
                "message": str(exc),
            },
            exc_info=True,
        )
        return

    _finalize_asgi_module(asgi_module, attr_name, graphql_url)


def _finalize_asgi_module(
    asgi_module: ModuleType,
    attr_name: str,
    graphql_url: str,
) -> None:
    """Configure WebSocket subscription routing in an ASGI module."""
    try:
        from channels.auth import AuthMiddlewareStack
        from channels.routing import ProtocolTypeRouter, URLRouter
        from general_manager.api.graphql_subscription_consumer import (
            GraphQLSubscriptionConsumer,
        )
    except (
        ImportError,
        RuntimeError,
    ) as exc:  # pragma: no cover - optional dependency
        logger.debug(
            "channels dependencies unavailable",
            context={
                "error": type(exc).__name__,
                "message": str(exc),
            },
        )
        return

    websocket_patterns = getattr(asgi_module, "websocket_urlpatterns", None)
    if websocket_patterns is None:
        websocket_patterns = []
        cast(_AsgiModuleRoutes, asgi_module).websocket_urlpatterns = websocket_patterns

    if not hasattr(websocket_patterns, "append"):
        logger.warning(
            "websocket_urlpatterns not appendable",
            context={"module": asgi_module.__name__},
        )
        return
    routes = cast(_AppendableRoutes, websocket_patterns)

    normalized = graphql_url.strip("/")
    escaped = re.escape(normalized)
    pattern = rf"^{escaped}/?$" if normalized else r"^$"

    route_exists = any(
        getattr(route, "_general_manager_graphql_ws", False) for route in routes
    )
    if not route_exists:
        websocket_route = re_path(
            pattern,
            cast(
                Callable[..., Coroutine[object, object, None]],
                GraphQLSubscriptionConsumer.as_asgi(),
            ),
        )
        cast(
            _GraphQLWebsocketRouteMarker, websocket_route
        )._general_manager_graphql_ws = True
        routes.append(websocket_route)

    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return

    if hasattr(application, "application_mapping"):
        mapped_application = cast(_ApplicationMapping, application)
        if "websocket" not in mapped_application.application_mapping:
            mapped_application.application_mapping["websocket"] = AuthMiddlewareStack(
                URLRouter(list(routes))
            )
    else:
        wrapped_application = ProtocolTypeRouter(
            {
                "http": application,
                "websocket": AuthMiddlewareStack(URLRouter(list(routes))),
            }
        )
        setattr(asgi_module, attr_name, wrapped_application)
