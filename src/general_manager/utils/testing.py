from graphene_django.utils.testing import GraphQLTransactionTestCase
from general_manager.apps import GeneralmanagerConfig
from importlib import import_module
from django.db import connection
from django.conf import settings
from typing import cast
from django.db import models
from general_manager.manager.generalManager import GeneralManager
from general_manager.api.graphql import GraphQL


def _default_graphql_url_clear():
    """
    Removes the first URL pattern for the GraphQL view from the project's root URL configuration.
    
    This function searches the root URL patterns for a pattern whose callback is a `GraphQLView` and removes it, effectively clearing the default GraphQL endpoint from the URL configuration.
    """
    urlconf = import_module(settings.ROOT_URLCONF)
    for pattern in urlconf.urlpatterns:
        if (
            hasattr(pattern, "callback")
            and hasattr(pattern.callback, "view_class")
            and pattern.callback.view_class.__name__ == "GraphQLView"
        ):
            urlconf.urlpatterns.remove(pattern)
            break


class GMTestCaseMeta(type):
    """
    Metaclass that wraps setUpClass: first calls user-defined setup,
    then performs GM environment initialization, then super().setUpClass().
    """

    def __new__(mcs, name, bases, attrs):
        """
        Creates a new test case class with a customized setUpClass that prepares the database schema and GraphQL environment for GeneralManager integration tests.
        
        The generated setUpClass method resets GraphQL class registries, invokes any user-defined setUpClass, clears default GraphQL URL patterns, creates missing database tables for specified GeneralManager classes and their history models, initializes GeneralManager and GraphQL configurations, and finally calls the original GraphQLTransactionTestCase setUpClass.
        """
        user_setup = attrs.get("setUpClass")
        # MERKE dir das echte GraphQLTransactionTestCase.setUpClass
        base_setup = GraphQLTransactionTestCase.setUpClass

        def wrapped_setUpClass(cls):
            """
            Performs comprehensive setup for a test case class, initializing GraphQL and GeneralManager environments and ensuring required database tables exist.
            
            This method resets internal GraphQL registries, invokes any user-defined setup, removes default GraphQL URL patterns, creates missing database tables for models and their history associated with specified GeneralManager classes, initializes GeneralManager and GraphQL configurations, and finally calls the base test case setup.
            """
            GraphQL._query_class = None
            GraphQL._mutation_class = None
            GraphQL._mutations = {}
            GraphQL._query_fields = {}
            GraphQL.graphql_type_registry = {}
            GraphQL.graphql_filter_type_registry = {}

            # 1) user-defined setUpClass (if any)
            if user_setup:
                user_setup.__func__(cls)
            # 2) clear URL patterns
            _default_graphql_url_clear()
            # 3) register models & create tables
            existing = connection.introspection.table_names()
            with connection.schema_editor() as editor:
                for manager_class in cls.general_manager_classes:
                    model_class = cast(
                        type[models.Model], manager_class.Interface._model  # type: ignore
                    )
                    if model_class._meta.db_table not in existing:
                        editor.create_model(model_class)
                        editor.create_model(model_class.history.model)  # type: ignore
            # 4) GM & GraphQL initialization
            GeneralmanagerConfig.initializeGeneralManagerClasses(
                cls.general_manager_classes, cls.general_manager_classes
            )
            GeneralmanagerConfig.handleReadOnlyInterface(cls.read_only_classes)
            GeneralmanagerConfig.handleGraphQL(cls.general_manager_classes)
            # 5) GraphQLTransactionTestCase.setUpClass
            base_setup.__func__(cls)

        attrs["setUpClass"] = classmethod(wrapped_setUpClass)
        return super().__new__(mcs, name, bases, attrs)


class GeneralManagerTransactionTestCase(
    GraphQLTransactionTestCase, metaclass=GMTestCaseMeta
):
    general_manager_classes: list[type[GeneralManager]] = []
    read_only_classes: list[type[GeneralManager]] = []
