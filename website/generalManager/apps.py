from django.apps import AppConfig
import graphene
from django.conf import settings
from django.urls import path
from graphene_django.views import GraphQLView
from importlib import import_module


class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "generalManager"

    def ready(self):
        if getattr(settings, "AUTOCREATE_GRAPHQL", False):
            from generalManager.src.manager.meta import GeneralManagerMeta
            from generalManager.src.api.graphql import GraphQL

            for general_manager_class in GeneralManagerMeta.pending_graphql_interfaces:
                GraphQL._createGraphQlInterface(general_manager_class)

            query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
            GraphQL._query_class = query_class

            schema = graphene.Schema(query=GraphQL._query_class)
            self.add_graphql_url(schema)

    def add_graphql_url(self, schema):
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
