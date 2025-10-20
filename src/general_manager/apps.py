from __future__ import annotations
from django.apps import AppConfig
import graphene  # type: ignore[import]
import os
from django.conf import settings
from django.urls import path, re_path
from graphene_django.views import GraphQLView  # type: ignore[import]
from importlib import import_module, util
import importlib.abc
import sys
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.api.property import graphQlProperty
from general_manager.api.graphql import GraphQL
from typing import TYPE_CHECKING, Any, Callable, Type, cast
from django.core.checks import register
import logging
from django.core.management.base import BaseCommand


class MissingRootUrlconfError(RuntimeError):
    """Raised when Django settings do not define ROOT_URLCONF."""

    def __init__(self) -> None:
        super().__init__("ROOT_URLCONF not found in settings.")


class InvalidPermissionClassError(TypeError):
    """Raised when a GeneralManager Permission attribute is not a BasePermission subclass."""

    def __init__(self, permission_name: str) -> None:
        super().__init__(f"{permission_name} must be a subclass of BasePermission.")


if TYPE_CHECKING:
    from general_manager.interface.readOnlyInterface import ReadOnlyInterface

logger = logging.getLogger(__name__)


class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "general_manager"

    def ready(self) -> None:
        """
        Performs initialization tasks for the general_manager app when Django starts.

        Sets up synchronization and schema validation for read-only interfaces, initializes attributes and property accessors for general manager classes, and configures the GraphQL schema and endpoint if enabled in settings.
        """
        self.handleReadOnlyInterface(GeneralManagerMeta.read_only_classes)
        self.initializeGeneralManagerClasses(
            GeneralManagerMeta.pending_attribute_initialization,
            GeneralManagerMeta.all_classes,
        )
        if getattr(settings, "AUTOCREATE_GRAPHQL", False):
            self.handleGraphQL(GeneralManagerMeta.pending_graphql_interfaces)

    @staticmethod
    def handleReadOnlyInterface(
        read_only_classes: list[Type[GeneralManager]],
    ) -> None:
        """
        Configures synchronization and schema validation for the given read-only GeneralManager classes.

        For each provided class, ensures that its data is synchronized before any Django management command executes, and registers a system check to verify that the associated schema remains up to date.
        """
        GeneralmanagerConfig.patchReadOnlyInterfaceSync(read_only_classes)
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        logger.debug("starting to register ReadOnlyInterface schema warnings...")

        def _build_schema_check(
            manager_cls: Type[GeneralManager], model: Any
        ) -> Callable[[object], list[Any]]:
            def schema_check(_: object, **__: Any) -> list[Any]:
                return ReadOnlyInterface.ensureSchemaIsUpToDate(manager_cls, model)

            return schema_check

        for general_manager_class in read_only_classes:
            read_only_interface = cast(
                Type[ReadOnlyInterface], general_manager_class.Interface
            )

            register(
                _build_schema_check(general_manager_class, read_only_interface._model),
                "general_manager",
            )

    @staticmethod
    def patchReadOnlyInterfaceSync(
        general_manager_classes: list[Type[GeneralManager]],
    ) -> None:
        """
        Monkey-patches Django's management command runner to synchronize all provided read-only interfaces before executing any management command, except during autoreload subprocesses of 'runserver'.

        For each class in `general_manager_classes`, the associated read-only interface's `syncData` method is called prior to command execution, ensuring data consistency before management operations.
        """
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        original_run_from_argv = BaseCommand.run_from_argv

        def run_from_argv_with_sync(
            self: BaseCommand,
            argv: list[str],
        ) -> None:
            # Ensure syncData is only called at real run of runserver
            """
            Executes a Django management command, synchronizing all registered read-only interfaces before execution unless running in an autoreload subprocess of 'runserver'.

            Parameters:
                argv (list): Command-line arguments for the management command.

            Returns:
                The result of the original management command execution.
            """
            run_main = os.environ.get("RUN_MAIN") == "true"
            command = argv[1] if len(argv) > 1 else None
            if command != "runserver" or run_main:
                logger.debug("start syncing ReadOnlyInterface data...")
                for general_manager_class in general_manager_classes:
                    read_only_interface = cast(
                        Type[ReadOnlyInterface], general_manager_class.Interface
                    )
                    read_only_interface.syncData()

                logger.debug("finished syncing ReadOnlyInterface data.")

            result = original_run_from_argv(self, argv)
            return result

        BaseCommand.run_from_argv = run_from_argv_with_sync

    @staticmethod
    def initializeGeneralManagerClasses(
        pending_attribute_initialization: list[Type[GeneralManager]],
        all_classes: list[Type[GeneralManager]],
    ) -> None:
        """
        Initializes attributes and establishes dynamic relationships for GeneralManager classes.

        For each class pending attribute initialization, assigns interface attributes and creates property accessors. Then, for all registered GeneralManager classes, connects input fields referencing other GeneralManager subclasses by adding GraphQL properties to enable filtered access to related objects.
        """
        logger.debug("Initializing GeneralManager classes...")

        def _build_connection_resolver(
            attribute_key: str, manager_cls: Type[GeneralManager]
        ) -> Callable[[object], Any]:
            def resolver(value: object) -> Any:
                return manager_cls.filter(**{attribute_key: value})

            resolver.__annotations__ = {"return": manager_cls}
            return resolver

        logger.debug("starting to create attributes for GeneralManager classes...")
        for general_manager_class in pending_attribute_initialization:
            attributes = general_manager_class.Interface.getAttributes()
            general_manager_class._attributes = attributes
            GeneralManagerMeta.createAtPropertiesForAttributes(
                attributes.keys(), general_manager_class
            )

        logger.debug("starting to connect inputs to other general manager classes...")
        for general_manager_class in all_classes:
            attributes = getattr(general_manager_class.Interface, "input_fields", {})
            for attribute_name, attribute in attributes.items():
                if isinstance(attribute, Input) and issubclass(
                    attribute.type, GeneralManager
                ):
                    connected_manager = attribute.type
                    resolver = _build_connection_resolver(
                        attribute_name, general_manager_class
                    )
                    setattr(
                        connected_manager,
                        f"{general_manager_class.__name__.lower()}_list",
                        graphQlProperty(resolver),
                    )
        for general_manager_class in all_classes:
            GeneralmanagerConfig.checkPermissionClass(general_manager_class)

    @staticmethod
    def handleGraphQL(
        pending_graphql_interfaces: list[Type[GeneralManager]],
    ) -> None:
        """
        Create GraphQL interfaces and mutations for the given manager classes, build the GraphQL schema, and add the GraphQL endpoint to the URL configuration.

        Parameters:
            pending_graphql_interfaces (list[Type[GeneralManager]]): GeneralManager classes that require GraphQL interface and mutation generation.
        """
        logger.debug("Starting to create GraphQL interfaces and mutations...")
        for general_manager_class in pending_graphql_interfaces:
            GraphQL.createGraphqlInterface(general_manager_class)
            GraphQL.createGraphqlMutation(general_manager_class)

        query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
        GraphQL._query_class = query_class

        if GraphQL._mutations:
            mutation_class = type(
                "Mutation",
                (graphene.ObjectType,),
                {
                    name: mutation.Field()
                    for name, mutation in GraphQL._mutations.items()
                },
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
        schema = graphene.Schema(**schema_kwargs)
        GraphQL._schema = schema
        GeneralmanagerConfig.addGraphqlUrl(schema)

    @staticmethod
    def addGraphqlUrl(schema: graphene.Schema) -> None:
        """
        Adds a GraphQL endpoint to the Django URL configuration using the provided schema.

        Parameters:
            schema: The GraphQL schema to use for the endpoint.

        Raises:
            Exception: If the ROOT_URLCONF setting is not defined in Django settings.
        """
        logging.debug("Adding GraphQL URL to Django settings...")
        root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
        graph_ql_url = getattr(settings, "GRAPHQL_URL", "graphql")
        if not root_url_conf_path:
            raise MissingRootUrlconfError()
        urlconf = import_module(root_url_conf_path)
        urlconf.urlpatterns.append(
            path(
                graph_ql_url,
                GraphQLView.as_view(graphiql=True, schema=schema),
            )
        )
        GeneralmanagerConfig._ensure_asgi_subscription_route(graph_ql_url)

    @staticmethod
    def _ensure_asgi_subscription_route(graphql_url: str) -> None:
        asgi_path = getattr(settings, "ASGI_APPLICATION", None)
        if not asgi_path:
            logger.debug("ASGI_APPLICATION not configured; skipping websocket setup.")
            return

        try:
            module_path, attr_name = asgi_path.rsplit(".", 1)
        except ValueError:
            logger.warning(
                "ASGI_APPLICATION '%s' is not a valid module path; skipping websocket setup.",
                asgi_path,
            )
            return

        try:
            asgi_module = import_module(module_path)
        except RuntimeError as exc:
            if "populate() isn't reentrant" not in str(exc):
                logger.warning(
                    "Unable to import ASGI module '%s': %s",
                    module_path,
                    exc,
                    exc_info=True,
                )
                return

            spec = util.find_spec(module_path)
            if spec is None or spec.loader is None:
                logger.warning(
                    "Could not locate loader for ASGI module '%s'; skipping websocket setup.",
                    module_path,
                )
                return

            def finalize(module: Any) -> None:
                GeneralmanagerConfig._finalize_asgi_module(
                    module, attr_name, graphql_url
                )

            class _Loader(importlib.abc.Loader):
                def __init__(self, original_loader: importlib.abc.Loader) -> None:
                    self._original_loader = original_loader

                def create_module(self, spec):  # type: ignore[override]
                    if hasattr(self._original_loader, "create_module"):
                        return self._original_loader.create_module(spec)  # type: ignore[attr-defined]
                    return None

                def exec_module(self, module):  # type: ignore[override]
                    self._original_loader.exec_module(module)
                    finalize(module)

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
                "Unable to import ASGI module '%s': %s", module_path, exc, exc_info=True
            )
            return

        GeneralmanagerConfig._finalize_asgi_module(asgi_module, attr_name, graphql_url)

    @staticmethod
    def _finalize_asgi_module(
        asgi_module: Any, attr_name: str, graphql_url: str
    ) -> None:
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
                "Channels or GraphQL subscription consumer unavailable (%s); skipping websocket setup.",
                exc,
            )
            return

        websocket_patterns = getattr(asgi_module, "websocket_urlpatterns", None)
        if websocket_patterns is None:
            websocket_patterns = []
            asgi_module.websocket_urlpatterns = websocket_patterns

        if not hasattr(websocket_patterns, "append"):
            logger.warning(
                "websocket_urlpatterns in '%s' does not support appending; skipping websocket setup.",
                asgi_module.__name__,
            )
            return

        normalized = graphql_url.strip("/")
        pattern = rf"^{normalized}/?$" if normalized else r"^$"

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
            application.application_mapping["websocket"] = AuthMiddlewareStack(
                URLRouter(list(websocket_patterns))
            )
        else:
            wrapped_application = ProtocolTypeRouter(
                {
                    "http": application,
                    "websocket": AuthMiddlewareStack(
                        URLRouter(list(websocket_patterns))
                    ),
                }
            )
            setattr(asgi_module, attr_name, wrapped_application)

    @staticmethod
    def checkPermissionClass(general_manager_class: Type[GeneralManager]) -> None:
        """
        Checks if the class has a Permission attribute and if it is a subclass of BasePermission.
        If so, it sets the Permission attribute on the class.
        """
        from general_manager.permission.basePermission import BasePermission
        from general_manager.permission.managerBasedPermission import (
            ManagerBasedPermission,
        )

        if hasattr(general_manager_class, "Permission"):
            permission = general_manager_class.Permission
            if not issubclass(permission, BasePermission):
                raise InvalidPermissionClassError(permission.__name__)
            general_manager_class.Permission = permission
        else:
            general_manager_class.Permission = ManagerBasedPermission
