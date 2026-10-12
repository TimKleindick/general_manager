"""Keep root and relation pages bound to their declared manager identities."""

from __future__ import annotations

from importlib import import_module
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.urls import clear_url_caches
from graphql import GraphQLObjectType, GraphQLSchema, get_named_type, parse, validate

from general_manager.api.graphql import GraphQL
from general_manager.apps import GeneralmanagerConfig
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLPageTypeIdentity(GeneralManagerTransactionTestCase):
    manager_pairs: ClassVar[
        tuple[tuple[type[GeneralManager], type[GeneralManager]], ...]
    ]
    container: ClassVar[type[GeneralManager]]
    category: ClassVar[type[GeneralManager]]

    @classmethod
    def setUpClass(cls) -> None:
        class PageIdentityItemType(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)

        class PageIdentityItem(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40)

        class PageIdentityProjectType(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)

        class PageIdentityProject(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40)
                project_type = models.ForeignKey(
                    "general_manager.PageIdentityProjectType",
                    on_delete=models.CASCADE,
                )

        class PageIdentityMaterialType(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)

        class PageIdentityMaterial(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40)
                material_type = models.ForeignKey(
                    "general_manager.PageIdentityMaterialType",
                    on_delete=models.CASCADE,
                )

        class PageIdentityBusinessCaseType(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)

        class PageIdentityBusinessCase(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40)
                business_case_type = models.ForeignKey(
                    "general_manager.PageIdentityBusinessCaseType",
                    on_delete=models.CASCADE,
                )

        class PageIdentityCategory(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=40)

        class PageIdentityContainer(GeneralManager):
            class Interface(DatabaseInterface):
                item_type = models.ManyToManyField(
                    "general_manager.PageIdentityItemType"
                )
                category = models.ManyToManyField(
                    "general_manager.PageIdentityCategory"
                )

        cls.manager_pairs = (
            (PageIdentityItem, PageIdentityItemType),
            (PageIdentityProject, PageIdentityProjectType),
            (PageIdentityMaterial, PageIdentityMaterialType),
            (PageIdentityBusinessCase, PageIdentityBusinessCaseType),
        )
        cls.container = PageIdentityContainer
        cls.category = PageIdentityCategory
        cls.general_manager_classes = [
            *(reference for _entity, reference in cls.manager_pairs),
            *(entity for entity, _reference in cls.manager_pairs),
            cls.category,
            cls.container,
        ]

    def _schema(self, *, references_first: bool) -> GraphQLSchema:
        """Rebuild the real schema in either order without changing the models."""
        entities = [entity for entity, _reference in self.manager_pairs]
        references = [reference for _entity, reference in self.manager_pairs]
        ordered = (
            [*references, *entities] if references_first else [*entities, *references]
        )
        GraphQL.reset_registry()
        urlconf = import_module(settings.ROOT_URLCONF)
        original_patterns = list(urlconf.urlpatterns)
        try:
            GeneralmanagerConfig.handle_graph_ql(
                [*ordered, self.category, self.container]
            )
            return GraphQL.get_schema().graphql_schema
        finally:
            urlconf.urlpatterns[:] = original_patterns
            clear_url_caches()

    def _assert_grouped_reference(
        self, parent: str, relation: str, expected: str
    ) -> None:
        for references_first in (False, True):
            with self.subTest(references_first=references_first):
                schema = self._schema(references_first=references_first)
                parent_type = schema.get_type(parent)
                self.assertIsInstance(parent_type, GraphQLObjectType)
                page = get_named_type(parent_type.fields[relation].type)
                self.assertIsInstance(page, GraphQLObjectType)
                item = get_named_type(page.fields["items"].type)
                self.assertIs(item, schema.get_type(expected))
                self.assertEqual(item.name, expected)
                self.assertIn("name", item.fields)

    def test_existing_ordinary_reference_selection_remains_valid(self) -> None:
        query = parse(
            """
            query {
              pageIdentityContainerList {
                items { itemTypeList { items { id name } } }
              }
            }
            """
        )
        for references_first in (False, True):
            with self.subTest(references_first=references_first):
                schema = self._schema(references_first=references_first)
                self.assertEqual(
                    [error.message for error in validate(schema, query)], []
                )

    def test_grouped_project_reference_keeps_its_manager_type(self) -> None:
        self._assert_grouped_reference(
            "PageIdentityProjectGroupType",
            "projectTypeList",
            "PageIdentityProjectTypeType",
        )

    def test_grouped_material_reference_keeps_its_manager_type(self) -> None:
        self._assert_grouped_reference(
            "PageIdentityMaterialGroupType",
            "materialTypeList",
            "PageIdentityMaterialTypeType",
        )

    def test_grouped_business_case_reference_keeps_its_manager_type(self) -> None:
        self._assert_grouped_reference(
            "PageIdentityBusinessCaseGroupType",
            "businessCaseTypeList",
            "PageIdentityBusinessCaseTypeType",
        )

    def test_root_page_names_and_item_identities_are_preserved(self) -> None:
        for references_first in (False, True):
            with self.subTest(references_first=references_first):
                schema = self._schema(references_first=references_first)
                for pair in self.manager_pairs:
                    for manager in pair:
                        field_name = (
                            manager.__name__[0].lower() + manager.__name__[1:] + "List"
                        )
                        page = get_named_type(schema.query_type.fields[field_name].type)
                        self.assertEqual(page.name, f"{manager.__name__}TypePage")
                        self.assertIs(
                            get_named_type(page.fields["items"].type),
                            schema.get_type(f"{manager.__name__}Type"),
                        )

    def test_noncolliding_relation_accepts_canonical_page_fragment(self) -> None:
        query = parse(
            """
            query {
              pageIdentityContainerList {
                items { categoryList { ...CategoryPage } }
              }
            }
            fragment CategoryPage on PageIdentityCategoryTypePage {
              items { id name }
              pageInfo { totalCount }
            }
            """
        )
        for references_first in (False, True):
            with self.subTest(references_first=references_first):
                schema = self._schema(references_first=references_first)
                self.assertEqual(
                    [error.message for error in validate(schema, query)], []
                )

    def test_schema_probe_preserves_http_route_configuration(self) -> None:
        urlconf = import_module(settings.ROOT_URLCONF)
        original_patterns = list(urlconf.urlpatterns)
        for references_first in (False, True):
            with self.subTest(references_first=references_first):
                self._schema(references_first=references_first)
                self.assertEqual(urlconf.urlpatterns, original_patterns)
