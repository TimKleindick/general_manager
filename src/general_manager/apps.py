from __future__ import annotations
from django.apps import AppConfig
import graphene
import os
from django.conf import settings
from django.urls import path
from graphene_django.views import GraphQLView
from importlib import import_module
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.api.property import graphQlProperty
from general_manager.api.graphql import GraphQL
from typing import TYPE_CHECKING, Type, Any, cast
from django.core.checks import register
import logging
from django.core.management.base import BaseCommand


if TYPE_CHECKING:
    from general_manager.interface.readOnlyInterface import ReadOnlyInterface

logger = logging.getLogger(__name__)


class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "general_manager"

    def ready(self):
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
        read_only_classes: list[Type[GeneralManager[Any, ReadOnlyInterface]]],
    ):
        """
        Configures synchronization and schema validation for the provided read-only interface classes.

        Ensures that each read-only interface is synchronized before Django management commands run, and registers system checks to validate that their schemas are up to date.
        """
        GeneralmanagerConfig.patchReadOnlyInterfaceSync(read_only_classes)
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        logger.debug("starting to register ReadOnlyInterface schema warnings...")
        for general_manager_class in read_only_classes:
            read_only_interface = cast(
                Type[ReadOnlyInterface], general_manager_class.Interface
            )

            register(
                lambda app_configs, model=read_only_interface._model, manager_class=general_manager_class, **kwargs: ReadOnlyInterface.ensureSchemaIsUpToDate(
                    manager_class, model
                ),
                "general_manager",
            )

    @staticmethod
    def patchReadOnlyInterfaceSync(
        general_manager_classes: list[Type[GeneralManager[Any, ReadOnlyInterface]]],
    ):
        """
        Monkey-patches Django's management command runner to synchronize all provided read-only interfaces before executing any management command, except during autoreload subprocesses of 'runserver'.

        For each class in `general_manager_classes`, the associated read-only interface's `syncData` method is called prior to command execution, ensuring data consistency before management operations.
        """
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        original_run_from_argv = BaseCommand.run_from_argv

        def run_from_argv_with_sync(self, argv):
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

            return original_run_from_argv(self, argv)

        BaseCommand.run_from_argv = run_from_argv_with_sync

    @staticmethod
    def initializeGeneralManagerClasses(
        pending_attribute_initialization: list[Type[GeneralManager]],
        all_classes: list[Type[GeneralManager]],
    ):
        """
        Initializes attributes and establishes dynamic relationships for GeneralManager classes.

        For each class pending attribute initialization, assigns interface attributes and creates property accessors. Then, for all registered GeneralManager classes, connects input fields referencing other GeneralManager subclasses by adding GraphQL properties to enable filtered access to related objects.
        """
        logger.debug("Initializing GeneralManager classes...")

        logger.debug("starting to create attributes for GeneralManager classes...")
        for general_manager_class in pending_attribute_initialization:
            attributes = general_manager_class.Interface.getAttributes()
            setattr(general_manager_class, "_attributes", attributes)
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
                    func = lambda x, attribute_name=attribute_name: general_manager_class.filter(
                        **{attribute_name: x}
                    )

                    func.__annotations__ = {"return": general_manager_class}
                    setattr(
                        connected_manager,
                        f"{general_manager_class.__name__.lower()}_list",
                        graphQlProperty(func),
                    )
        for general_manager_class in all_classes:
            GeneralmanagerConfig.checkPermissionClass(general_manager_class)

    @staticmethod
    def handleGraphQL(
        pending_graphql_interfaces: list[Type[GeneralManager]],
    ):
        """
        Creates GraphQL interfaces and mutations for the provided general manager classes, builds the GraphQL schema, and registers the GraphQL endpoint in the Django URL configuration.
        """
        logger.debug("Starting to create GraphQL interfaces and mutations...")
        for general_manager_class in pending_graphql_interfaces:
            GraphQL.createGraphqlInterface(general_manager_class)
            GraphQL.createGraphqlMutation(general_manager_class)

        query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
        GraphQL._query_class = query_class

        mutation_class = type(
            "Mutation",
            (graphene.ObjectType,),
            {name: mutation.Field() for name, mutation in GraphQL._mutations.items()},
        )
        GraphQL._mutation_class = mutation_class

        schema = graphene.Schema(
            query=GraphQL._query_class,
            mutation=GraphQL._mutation_class,
        )
        GeneralmanagerConfig.addGraphqlUrl(schema)

    @staticmethod
    def addGraphqlUrl(schema):
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
            raise Exception("ROOT_URLCONF not found in settings")
        urlconf = import_module(root_url_conf_path)
        urlconf.urlpatterns.append(
            path(
                graph_ql_url,
                GraphQLView.as_view(graphiql=True, schema=schema),
            )
        )

    @staticmethod
    def checkPermissionClass(general_manager_class: Type[GeneralManager]):
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
                raise TypeError(
                    f"{permission.__name__} must be a subclass of BasePermission"
                )
            general_manager_class.Permission = permission
        else:
            general_manager_class.Permission = ManagerBasedPermission
