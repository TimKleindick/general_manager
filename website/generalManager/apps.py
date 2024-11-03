from django.apps import AppConfig
import graphene
from website.settings import AUTOCREATE_GRAPHQL, GRAPHQL_URL, ROOT_URLCONF
from django.urls import path
from graphene_django.views import GraphQLView
from importlib import import_module


class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "generalManager"

    def ready(self):
        if AUTOCREATE_GRAPHQL:
            # Importieren Sie die Metaklasse
            from generalManager.src.manager.meta import GeneralManagerMeta
            from generalManager.src.api.graphql import GraphQL

            # Erstellen der GraphQL-Interfaces
            for general_manager_class in GeneralManagerMeta.pending_graphql_interfaces:
                GraphQL._createGraphQlInterface(general_manager_class)

            # Erstellen der Query-Klasse
            query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
            GraphQL._query_class = query_class

            schema = graphene.Schema(query=GraphQL._query_class)
            self.add_graphql_url(schema)

    def add_graphql_url(self, schema):
        # Hier erst nach dem Laden der Apps auf die URLs zugreifen
        urlconf = import_module(ROOT_URLCONF)
        urlconf.urlpatterns.append(
            path(
                GRAPHQL_URL,
                GraphQLView.as_view(graphiql=True, schema=schema),
            )
        )
