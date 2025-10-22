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
        """
        Initialize the MissingRootUrlconfError with a default message indicating ROOT_URLCONF is missing from Django settings.
        """
        super().__init__("ROOT_URLCONF not found in settings.")


class InvalidPermissionClassError(TypeError):
    """Raised when a GeneralManager Permission attribute is not a BasePermission subclass."""

    def __init__(self, permission_name: str) -> None:
        """
        Create an InvalidPermissionClassError indicating a permission is not a subclass of BasePermission.

        Parameters:
            permission_name (str): The name of the permission (typically the class or attribute name) that failed validation. The exception message will be "`{permission_name} must be a subclass of BasePermission.`"
        """
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
        Configure synchronization and register schema checks for read-only GeneralManager classes.

        Parameters:
            read_only_classes (list[Type[GeneralManager]]): GeneralManager subclasses whose Interface implements a ReadOnlyInterface; each class will have its read-only data synchronized before management commands and a Django system check registered to verify the Interface schema is up to date.
        """
        GeneralmanagerConfig.patchReadOnlyInterfaceSync(read_only_classes)
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        logger.debug("starting to register ReadOnlyInterface schema warnings...")

        def _build_schema_check(
            manager_cls: Type[GeneralManager], model: Any
        ) -> Callable[..., list[Any]]:
            """
            Builds a Django system check callable that verifies the read-only interface schema for a manager against a model is current.

            Parameters:
                manager_cls (Type[GeneralManager]): The GeneralManager class whose ReadOnlyInterface schema will be validated.
                model (Any): The model (or model-like descriptor) to check the schema against.

            Returns:
                Callable[..., list[Any]]: A callable suitable for Django's system checks framework that returns a list of check messages.
            """

            def schema_check(*_: Any, **__: Any) -> list[Any]:
                return ReadOnlyInterface.ensureSchemaIsUpToDate(manager_cls, model)

            return schema_check

        for general_manager_class in read_only_classes:
            read_only_interface = cast(
                Type[ReadOnlyInterface], general_manager_class.Interface
            )

            register("general_manager")(
                _build_schema_check(
                    general_manager_class,
                    read_only_interface._model,
                )
            )

    @staticmethod
    def patchReadOnlyInterfaceSync(
        general_manager_classes: list[Type[GeneralManager]],
    ) -> None:
        """
        Ensure the provided GeneralManager classes' ReadOnlyInterfaces synchronize their data before any Django management command is executed.

        For each class in `general_manager_classes`, calls the class's `Interface.syncData()` to keep read-only data consistent. Skips synchronization when running the autoreload subprocess of `runserver`.
        Parameters:
            general_manager_classes (list[Type[GeneralManager]]): GeneralManager subclasses whose `Interface` implements `syncData`.
        """
        """
        Wrap BaseCommand.run_from_argv to call `syncData()` on registered ReadOnlyInterfaces before executing the original command.

        Skips synchronization when the command is `runserver` and the process is the autoreload subprocess.
        Parameters:
            self (BaseCommand): The management command instance.
            argv (list[str]): Command-line arguments for the management command.
        Returns:
            The result returned by the original `BaseCommand.run_from_argv` call.
        """
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        original_run_from_argv = BaseCommand.run_from_argv

        def run_from_argv_with_sync(
            self: BaseCommand,
            argv: list[str],
        ) -> None:
            # Ensure syncData is only called at real run of runserver
            """
            Synchronizes all registered ReadOnlyInterface data before running a Django management command, except when running the autoreload subprocess of `runserver`.

            Parameters:
                argv (list[str]): The management command `argv`, including the program name and command.

            Returns:
                The value returned by the original `BaseCommand.run_from_argv` invocation.
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

        BaseCommand.run_from_argv = run_from_argv_with_sync  # type: ignore[assignment]

    @staticmethod
    def initializeGeneralManagerClasses(
        pending_attribute_initialization: list[Type[GeneralManager]],
        all_classes: list[Type[GeneralManager]],
    ) -> None:
        """
        Initialize GeneralManager classes' interface attributes, create attribute-based accessors, wire GraphQL connection properties between related managers, and validate each class's permission configuration.

        For each class in `pending_attribute_initialization` this assigns the class's Interface attributes to its internal `_attributes` and creates property accessors for those attributes. For each class in `all_classes` this scans its Interface `input_fields` for inputs whose type is another GeneralManager subclass and adds a GraphQL property on the connected manager that resolves related objects filtered by the input attribute. Finally, validate and normalize the Permission attribute on every class via GeneralmanagerConfig.checkPermissionClass.

        Parameters:
            pending_attribute_initialization (list[type[GeneralManager]]): GeneralManager classes whose Interface attributes need to be initialized and whose attribute properties should be created.
            all_classes (list[type[GeneralManager]]): All registered GeneralManager classes to inspect for input-field connections and to validate permissions.
        """
        logger.debug("Initializing GeneralManager classes...")

        def _build_connection_resolver(
            attribute_key: str, manager_cls: Type[GeneralManager]
        ) -> Callable[[object], Any]:
            """
            Create a resolver that queries the given GeneralManager class by matching an attribute to a provided value.

            Parameters:
                attribute_key (str): Name of the attribute to filter on.
                manager_cls (Type[GeneralManager]): GeneralManager subclass to query.

            Returns:
                Callable[[object], Any]: A callable that accepts a value and returns the result of filtering `manager_cls` where `attribute_key` equals that value.
            """

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
        Generate GraphQL types, assemble the GraphQL schema for the given manager classes, and expose the GraphQL endpoint in the project's URL configuration.

        Parameters:
            pending_graphql_interfaces (list[Type[GeneralManager]]): GeneralManager classes for which GraphQL interfaces, mutations, and optional subscriptions should be created and included in the assembled schema.
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
        Add a GraphQL endpoint to the project's URL configuration and ensure the ASGI subscription route is configured.

        Parameters:
            schema (graphene.Schema): GraphQL schema to serve at the configured GRAPHQL_URL.

        Raises:
            MissingRootUrlconfError: If ROOT_URLCONF is not defined in Django settings.
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
        """
        Ensure GraphQL websocket subscription route is integrated into the project's ASGI application when ASGI is configured.

        If settings.ASGI_APPLICATION is absent or invalid, the function logs and returns without making changes. When the ASGI application module can be imported, delegates to GeneralmanagerConfig._finalize_asgi_module to add or wire WebSocket routes for GraphQL subscriptions at the given URL. If the ASGI module cannot be imported during Django's application population phase, the function arranges a deferred finalization so the ASGI module will be finalized once it is loaded; other import failures are logged and cause a no-op return.
        Parameters:
            graphql_url (str): URL path at which the GraphQL websocket subscription endpoint should be exposed (e.g., "/graphql/").
        """
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
                """
                Finalize integration of GraphQL subscription routing into the given ASGI module using the surrounding attribute name and GraphQL URL.

                Ensure the ASGI module exposes websocket_urlpatterns and add a websocket route for the GraphQL subscription endpoint if missing; modify or wrap the module's application mapping or ASGI application as needed to include an authenticated websocket route for subscriptions.

                Parameters:
                    module (Any): The imported ASGI module object to be modified in-place to support GraphQL websocket subscriptions.
                """
                GeneralmanagerConfig._finalize_asgi_module(
                    module, attr_name, graphql_url
                )

            class _Loader(importlib.abc.Loader):
                def __init__(self, original_loader: importlib.abc.Loader) -> None:
                    """
                    Initialize the wrapper with the given original importlib loader.

                    Parameters:
                        original_loader (importlib.abc.Loader): The underlying loader that this wrapper will delegate to.
                    """
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
                    """
                    Return a ModuleSpec for the wrapped loader the first time the specified module is requested.

                    If `fullname` matches the loader's target module name and this loader has not yet produced a spec, mark the loader as processed, create a new spec using the wrapped loader, copy `submodule_search_locations` from the original `spec` if present, and return the new spec; otherwise return `None`.

                    Parameters:
                        fullname (str): Fully-qualified name of the module being imported; matched against the loader's target module name.
                        path (Sequence[str] | None): Import path for package search (not used by this finder).
                        target (ModuleSpec | None): Optional existing spec suggested by the import machinery (ignored).

                    Returns:
                        ModuleSpec | None: The created ModuleSpec when `fullname` matches and has not been processed, `None` otherwise.
                    """
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
        """
        Ensure the ASGI module exposes a websocket route for GraphQL subscriptions and integrate it into the ASGI application.

        If Channels and the GraphQL subscription consumer are available, this function appends a websocket URL pattern for the GraphQL subscriptions to asgi_module.websocket_urlpatterns (creating the list if needed) and then wires that websocket route into the module's ASGI application. If the module exposes an application mapping (application.application_mapping is a dict) the websocket entry is added there; otherwise the existing application is wrapped in a ProtocolTypeRouter that routes websocket traffic through Channels' AuthMiddlewareStack. If optional dependencies are missing, or websocket_urlpatterns cannot be appended, the function returns without modifying the ASGI module.

        Parameters:
            asgi_module (module): The imported ASGI module object referenced by ASGI_APPLICATION in settings.
            attr_name (str): Attribute name on asgi_module that holds the ASGI application (e.g., "application").
            graphql_url (str): URL path (relative, may include leading/trailing slashes) at which the GraphQL HTTP endpoint is mounted; used to build the websocket route for subscriptions.
        """
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
        Validate and normalize a GeneralManager class's Permission attribute.

        If the class defines a Permission attribute, ensure it is a subclass of BasePermission and leave it assigned; if it is not a subclass, raise InvalidPermissionClassError. If the class does not define Permission, assign ManagerBasedPermission as the default.

        Parameters:
            general_manager_class (Type[GeneralManager]): GeneralManager subclass whose Permission attribute will be validated or initialized.

        Raises:
            InvalidPermissionClassError: If the existing Permission attribute is not a subclass of BasePermission.
        """
        from general_manager.permission.basePermission import BasePermission
        from general_manager.permission.managerBasedPermission import (
            ManagerBasedPermission,
        )

        if hasattr(general_manager_class, "Permission"):
            permission = general_manager_class.Permission
            if not (
                isinstance(permission, type) and issubclass(permission, BasePermission)
            ):
                permission_name = getattr(permission, "__name__", repr(permission))
                raise InvalidPermissionClassError(permission_name)
            general_manager_class.Permission = permission
        else:
            general_manager_class.Permission = ManagerBasedPermission
