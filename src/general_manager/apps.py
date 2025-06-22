from django.apps import AppConfig
import graphene
from django.conf import settings
from django.urls import path
from graphene_django.views import GraphQLView
from importlib import import_module
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.api.property import graphQlProperty
from general_manager.api.graphql import GraphQL
from typing import TYPE_CHECKING
from django.core.checks import register

if TYPE_CHECKING:
    from general_manager.interface.readOnlyInterface import ReadOnlyInterface


class GeneralmanagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "general_manager"

    def ready(self):

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

        for general_manager_class in GeneralManagerMeta.read_only_classes:
            read_only_interface: ReadOnlyInterface = general_manager_class.Interface  # type: ignore
            register(
                lambda app_configs, model=read_only_interface._model, manager_class=general_manager_class, **kwargs: read_only_interface.ensureSchemaIsUpToDate(
                    manager_class, model
                ),
                "general_manager",
            )

        for (
            general_manager_class
        ) in GeneralManagerMeta.pending_attribute_initialization:
            attributes = general_manager_class.Interface.getAttributes()
            setattr(general_manager_class, "_attributes", attributes)
            GeneralManagerMeta.createAtPropertiesForAttributes(
                attributes.keys(), general_manager_class
            )

        if getattr(settings, "AUTOCREATE_GRAPHQL", False):

            for general_manager_class in GeneralManagerMeta.pending_graphql_interfaces:
                GraphQL.createGraphqlInterface(general_manager_class)
                GraphQL.createGraphqlMutation(general_manager_class)

            query_class = type("Query", (graphene.ObjectType,), GraphQL._query_fields)
            GraphQL._query_class = query_class

            mutation_class = type(
                "Mutation",
                (graphene.ObjectType,),
                {
                    name: mutation.Field()
                    for name, mutation in GraphQL._mutations.items()
                },
            )
            GraphQL._mutation_class = mutation_class

            schema = graphene.Schema(
                query=GraphQL._query_class,
                mutation=GraphQL._mutation_class,
            )
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
