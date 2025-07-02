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
        Initializes the general_manager app on Django startup.
        
        Sets up read-only interface synchronization and schema validation, initializes general manager class attributes and interconnections, and configures the GraphQL schema and endpoint if enabled in settings.
        """
        self.handleReadOnlyInterface()
        self.initializeGeneralManagerClasses()
        if getattr(settings, "AUTOCREATE_GRAPHQL", False):
            self.handleGraphQL()

    def handleReadOnlyInterface(self):
        """
        Configures synchronization and schema validation for all registered read-only interfaces.
        
        This method ensures that read-only interfaces are synchronized before Django management commands execute and registers system checks to validate that each read-only interface's schema remains current.
        """
        self.patchReadOnlyInterfaceSync(GeneralManagerMeta.read_only_classes)
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        logger.debug("starting to register ReadOnlyInterface schema warnings...")
        for general_manager_class in GeneralManagerMeta.read_only_classes:
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
        Monkey-patches Django's management command runner to synchronize data for all provided read-only interfaces before executing management commands.
        
        For each general manager class, its associated read-only interface's `syncData` method is called prior to command execution, except during autoreload subprocesses of `runserver`.
        """
        from general_manager.interface.readOnlyInterface import ReadOnlyInterface

        original_run_from_argv = BaseCommand.run_from_argv

        def run_from_argv_with_sync(self, argv):
            # Ensure syncData is only called at real run of runserver
            """
            Executes a Django management command, synchronizing all registered read-only interfaces before execution unless running an autoreload subprocess of 'runserver'.
            
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

    def initializeGeneralManagerClasses(self):
        """
        Initializes attributes and sets up dynamic relationships for all registered GeneralManager classes.
        
        For each pending GeneralManager class, assigns its interface attributes and creates property accessors. Then, for all GeneralManager classes, dynamically connects input fields that reference other GeneralManager subclasses by adding GraphQL properties to enable filtered access to related objects.
        """
        logger.debug("Initializing GeneralManager classes...")

        logger.debug("starting to create attributes for GeneralManager classes...")
        for (
            general_manager_class
        ) in GeneralManagerMeta.pending_attribute_initialization:
            attributes = general_manager_class.Interface.getAttributes()
            setattr(general_manager_class, "_attributes", attributes)
            GeneralManagerMeta.createAtPropertiesForAttributes(
                attributes.keys(), general_manager_class
            )

        logger.debug("starting to connect inputs to other general manager classes...")
        for general_manager_class in GeneralManagerMeta.all_classes:
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

    def handleGraphQL(self):
        """
        Sets up GraphQL interfaces, mutations, and schema for all pending general manager classes, and adds the GraphQL endpoint to the Django URL configuration.
        """
        logger.debug("Starting to create GraphQL interfaces and mutations...")
        for general_manager_class in GeneralManagerMeta.pending_graphql_interfaces:
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
        self.addGraphqlUrl(schema)

    def addGraphqlUrl(self, schema):
        """
        Dynamically appends a GraphQL endpoint to the Django URL configuration using the given schema.
        
        Raises:
            Exception: If the ROOT_URLCONF setting is not defined in Django settings.
        """
        logging.debug("Adding GraphQL URL to Django settings...")
        root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
        graph_ql_url = getattr(settings, "GRAPHQL_URL", "graphql/")
        if not root_url_conf_path:
            raise Exception("ROOT_URLCONF not found in settings")
        urlconf = import_module(root_url_conf_path)
        urlconf.urlpatterns.append(
            path(
                graph_ql_url,
                GraphQLView.as_view(graphiql=True, schema=schema),
            )
        )
