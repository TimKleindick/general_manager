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
from importlib import import_module, util
from typing import TYPE_CHECKING, Any, Callable, Iterable, Type

import graphene  # type: ignore[import]
from django.conf import settings
from django.core.checks import register
from django.core.management.base import BaseCommand
from django.urls import path, re_path
from graphql import GraphQLDirective, specified_directives

from general_manager.api.graphql_view import GeneralManagerGraphQLView
from general_manager.api.graphql import GraphQL
from general_manager.api.remote_api import add_remote_api_urls
from general_manager.api.remote_invalidation import ensure_remote_invalidation_route
from general_manager.conf import get_setting
from general_manager.api.property import graph_ql_property
from general_manager.logging import get_logger
from general_manager.interface.infrastructure.startup_hooks import (
    registered_startup_hook_entries,
    order_interfaces_by_dependency,
)
from general_manager.interface.infrastructure.system_checks import (
    iter_interface_system_checks,
)
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
        argv: list[str],
    ) -> None:
        run_main = os.environ.get("RUN_MAIN") == "true"
        command = argv[1] if len(argv) > 1 else None
        should_run_hooks = command != "runserver" or run_main
        hooks_registry = registered_startup_hook_entries() if should_run_hooks else {}
        if hooks_registry:
            resolver_map: dict[object, list[type]] = {}
            for interface_cls, entries in hooks_registry.items():
                for entry in entries:
                    resolver_map.setdefault(entry.dependency_resolver, []).append(
                        interface_cls
                    )
            logger.debug(
                "running startup hooks",
                context={
                    "command": command,
                    "count": sum(len(v) for v in hooks_registry.values()),
                    "autoreload": not run_main if command == "runserver" else False,
                },
            )
            for resolver, iface_list in resolver_map.items():
                ordered_interfaces = order_interfaces_by_dependency(
                    iface_list,
                    resolver,  # type: ignore[arg-type]
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

    BaseCommand.run_from_argv = run_from_argv_with_startup_hooks  # type: ignore[assignment]
    BaseCommand._gm_startup_hooks_runner_installed = True  # type: ignore[attr-defined]
    BaseCommand._gm_original_run_from_argv = original_run_from_argv  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Phase 2: Django system checks
# ---------------------------------------------------------------------------


def _wrap_system_check(
    interface_cls: Type[Any],
    hook: Callable[[], list[Any]],
) -> Callable[..., list[Any]]:
    """Wrap a system-check hook so exceptions are caught and logged."""

    def _check(*_: Any, **__: Any) -> list[Any]:
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


def register_system_checks() -> None:
    """Register capability-provided system checks with Django's check framework.

    Idempotent: a given interface's checks are registered at most once per
    process lifetime, so calling ``ready()`` multiple times (common in tests)
    does not produce duplicate check results.
    """
    hooks = list(iter_interface_system_checks())
    if not hooks:
        return
    new_hooks = [
        (iface, hook)
        for iface, hook in hooks
        if iface.__name__ not in _registered_system_check_interfaces
    ]
    if not new_hooks:
        return
    logger.debug(
        "registering capability system checks",
        context={"count": len(new_hooks)},
    )
    for interface_cls, hook in new_hooks:
        _registered_system_check_interfaces.add(interface_cls.__name__)
        register("general_manager")(_wrap_system_check(interface_cls, hook))


# ---------------------------------------------------------------------------
# Phase 3: manager class initialization
# ---------------------------------------------------------------------------


def check_permission_class(general_manager_class: Type[GeneralManager]) -> None:
    """Validate and normalize a GeneralManager class's Permission attribute."""
    from general_manager.permission.base_permission import BasePermission
    from general_manager.permission.manager_based_permission import (
        ManagerBasedPermission,
    )

    if hasattr(general_manager_class, "Permission"):
        permission = general_manager_class.Permission
        if not (
            isinstance(permission, type) and issubclass(permission, BasePermission)
        ):
            permission_name = getattr(permission, "__name__", repr(permission))
            raise InvalidPermissionClassError(permission_name)
    else:
        general_manager_class.Permission = ManagerBasedPermission


def initialize_general_manager_classes(
    pending_attribute_initialization: list[Type[GeneralManager]],
    all_classes: list[Type[GeneralManager]],
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
        attribute_key: str, manager_cls: Type[_GM]
    ) -> Callable[[object], Any]:
        def resolver(value: object) -> Any:
            return manager_cls.filter(**{attribute_key: value})

        resolver.__annotations__ = {"return": manager_cls}
        return resolver

    logger.debug(
        "creating manager attributes",
        context={"pending_attributes": len(pending_attribute_initialization)},
    )
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
        attributes = getattr(general_manager_class.Interface, "input_fields", {})
        for attribute_name, attribute in attributes.items():
            if isinstance(attribute, Input) and issubclass(attribute.type, _GM):
                connected_manager = attribute.type
                resolver = _build_connection_resolver(
                    attribute_name, general_manager_class
                )
                setattr(
                    connected_manager,
                    f"{general_manager_class.__name__.lower()}_list",
                    graph_ql_property(resolver),
                )
    for general_manager_class in all_classes:
        check_permission_class(general_manager_class)


def handle_remote_api(
    manager_classes: list[Type[GeneralManager]],
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
    configured = get_setting("GRAPHQL_DIRECTIVES", ())
    return _normalize_graphql_directives(configured)


def handle_graph_ql(
    pending_graphql_interfaces: list[Type[GeneralManager]],
) -> None:
    """
    Generate GraphQL interfaces and mutations, build a ``graphene.Schema``, and
    add the HTTP and ASGI subscription routes to the project's URL configuration.
    """

    logger.debug(
        "creating graphql interfaces and mutations",
        context={"pending": len(pending_graphql_interfaces)},
    )
    for general_manager_class in pending_graphql_interfaces:
        GraphQL.create_graphql_interface(general_manager_class)
        GraphQL.create_graphql_mutation(general_manager_class)

    GraphQL.register_search_query()

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

    schema_kwargs: dict[str, Any] = {"query": GraphQL._query_class}
    if GraphQL._mutation_class is not None:
        schema_kwargs["mutation"] = GraphQL._mutation_class
    if GraphQL._subscription_class is not None:
        schema_kwargs["subscription"] = GraphQL._subscription_class
    custom_directives = _get_configured_graphql_directives()
    if custom_directives:
        schema_kwargs["directives"] = _build_schema_directives(custom_directives)
    schema = graphene.Schema(**schema_kwargs)
    GraphQL._schema = schema
    add_graphql_url(schema)


# ---------------------------------------------------------------------------
# Phase 5a: URL + ASGI wiring
# ---------------------------------------------------------------------------


def add_graphql_url(schema: graphene.Schema) -> None:
    """Add a GraphQL endpoint to the project's URL configuration."""
    graph_ql_url = get_setting("GRAPHQL_URL", "graphql")
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
    view_kwargs: dict[str, Any] = {"graphiql": True, "schema": schema}
    if middleware is not None:
        view_kwargs["middleware"] = middleware
    urlconf.urlpatterns.append(
        path(
            graph_ql_url,
            GeneralManagerGraphQLView.as_view(**view_kwargs),
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

        def finalize(module: Any) -> None:
            _finalize_asgi_module(module, attr_name, graphql_url)

        class _Loader(importlib.abc.Loader):
            def __init__(self, original_loader: importlib.abc.Loader) -> None:
                self._original_loader = original_loader

            def create_module(self, spec):  # type: ignore[override]
                if hasattr(self._original_loader, "create_module"):
                    return self._original_loader.create_module(spec)  # type: ignore[attr-defined]
                return None

            def exec_module(self, module):  # type: ignore[override]
                try:
                    self._original_loader.exec_module(module)
                    finalize(module)
                finally:
                    with contextlib.suppress(ValueError):
                        sys.meta_path.remove(finder)

        wrapped_loader = _Loader(spec.loader)

        class _Finder(importlib.abc.MetaPathFinder):
            def __init__(self) -> None:
                self._processed = False

            def find_spec(self, fullname, path, target=None):  # type: ignore[override]
                if fullname != module_path or self._processed:
                    return None
                self._processed = True
                new_spec = util.spec_from_loader(fullname, wrapped_loader)
                if new_spec is not None:
                    new_spec.submodule_search_locations = (
                        spec.submodule_search_locations
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


def _finalize_asgi_module(asgi_module: Any, attr_name: str, graphql_url: str) -> None:
    """Configure WebSocket subscription routing in an ASGI module."""
    try:
        from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
        from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
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
        asgi_module.websocket_urlpatterns = websocket_patterns

    if not hasattr(websocket_patterns, "append"):
        logger.warning(
            "websocket_urlpatterns not appendable",
            context={"module": asgi_module.__name__},
        )
        return

    normalized = graphql_url.strip("/")
    escaped = re.escape(normalized)
    pattern = rf"^{escaped}/?$" if normalized else r"^$"

    route_exists = any(
        getattr(route, "_general_manager_graphql_ws", False)
        for route in websocket_patterns
    )
    if not route_exists:
        websocket_route = re_path(pattern, GraphQLSubscriptionConsumer.as_asgi())  # type: ignore[arg-type]
        websocket_route._general_manager_graphql_ws = True
        websocket_patterns.append(websocket_route)

    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return

    if hasattr(application, "application_mapping") and isinstance(
        application.application_mapping, dict
    ):
        if "websocket" not in application.application_mapping:
            application.application_mapping["websocket"] = AuthMiddlewareStack(
                URLRouter(list(websocket_patterns))
            )
    else:
        wrapped_application = ProtocolTypeRouter(
            {
                "http": application,
                "websocket": AuthMiddlewareStack(URLRouter(list(websocket_patterns))),
            }
        )
        setattr(asgi_module, attr_name, wrapped_application)
