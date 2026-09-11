# type: ignore

"""Focused schema and resolver coverage for GraphQL grouping helpers."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import graphene

from general_manager.api.graphql import (
    GraphQL,
    UnsupportedGroupedFieldError,
)
from general_manager.api.registry import GraphQLRegistry
from general_manager.bucket._materialized_bucket import MaterializedBucket
from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.manager.general_manager import GeneralManager, GeneralManagerMeta
from graphql import GraphQLError


def _restore_registry(snapshot: GraphQLRegistry) -> None:
    """Restore class-level schema state after an isolated schema test."""
    GraphQL._query_class = snapshot.query_class
    GraphQL._mutation_class = snapshot.mutation_class
    GraphQL._subscription_class = snapshot.subscription_class
    GraphQL._schema = snapshot.schema
    GraphQL._mutations = snapshot.mutations
    GraphQL._query_fields = snapshot.query_fields
    GraphQL._subscription_fields = snapshot.subscription_fields
    GraphQL._page_type_registry = snapshot.page_type_registry
    GraphQL._group_type_registry = snapshot.group_type_registry
    GraphQL._group_page_type_registry = snapshot.group_page_type_registry
    GraphQL._subscription_payload_registry = snapshot.subscription_payload_registry
    GraphQL.graphql_type_registry = snapshot.graphql_type_registry
    GraphQL.graphql_output_type_registry = snapshot.graphql_output_type_registry
    GraphQL.graphql_filter_type_registry = snapshot.graphql_filter_type_registry
    GraphQL.graphql_capability_type_registry = snapshot.graphql_capability_type_registry
    GraphQL.manager_registry = snapshot.manager_registry
    GraphQL._search_union = snapshot.search_union
    GraphQL._search_result_type = snapshot.search_result_type


def _restore_manager_meta(
    snapshots: dict[str, list[type[GeneralManager]]],
) -> None:
    """Restore append-only manager registries after temporary test classes."""
    for name, values in snapshots.items():
        getattr(GeneralManagerMeta, name)[:] = values


class GroupSchemaCoverageTests(TestCase):
    """Exercise grouped schema branches with small in-memory manager types."""

    def setUp(self) -> None:
        snapshot = GraphQL.get_registry_snapshot()
        manager_snapshots = {
            name: list(getattr(GeneralManagerMeta, name))
            for name in (
                "all_classes",
                "read_only_classes",
                "pending_graphql_interfaces",
                "pending_attribute_initialization",
            )
        }
        self.addCleanup(_restore_manager_meta, manager_snapshots)
        self.addCleanup(_restore_registry, snapshot)
        self.addCleanup(GraphQL.reset_registry)
        GraphQL.reset_registry()

    @staticmethod
    def _manager_with_id(
        manager_type: type[GeneralManager], value: int
    ) -> GeneralManager:
        manager = manager_type.__new__(manager_type)
        manager._GeneralManager__id = {"id": value}
        return manager

    def _related_manager_types(
        self,
    ) -> tuple[type[GeneralManager], type[GeneralManager]]:
        class RelatedManager(GeneralManager):
            pass

        class ParentManager(GeneralManager):
            pass

        RelatedManager.Interface = SimpleNamespace(
            input_fields={},
            get_attribute_types=lambda: {
                "id": {"type": int},
                "name": {"type": str},
            },
            get_graph_ql_properties=lambda: {},
        )

        def get_parent_attributes() -> dict[str, dict[str, object]]:
            return {
                "name": {"type": str},
                "owner": {"type": RelatedManager, "relation_kind": "direct"},
                "stored_file": {"type": str, "orm_field_kind": "file"},
            }

        ParentManager.Interface = SimpleNamespace(
            input_fields={},
            get_attribute_types=get_parent_attributes,
            get_graph_ql_properties=lambda: {},
        )
        return RelatedManager, ParentManager

    def test_grouped_property_without_list_suffix_gets_scoped_pages(self) -> None:
        related_manager, parent_manager = self._related_manager_types()
        related_type = type(
            "RelatedManagerType",
            (graphene.ObjectType,),
            {"name": graphene.String()},
        )
        parent_type = type(
            "ParentManagerType",
            (graphene.ObjectType,),
            {"secrets": graphene.Field(related_type)},
        )

        property_definition = SimpleNamespace(
            graphql_type_hint=Bucket[related_manager],
            _raw_fget=lambda _instance: None,
        )
        parent_manager.Interface.get_graph_ql_properties = lambda: {
            "secrets": property_definition,
            # An attribute takes precedence over a same-named projection.
            "name": SimpleNamespace(graphql_type_hint=str),
            # Plain typed lists remain lists and do not get grouping controls.
            "labels": SimpleNamespace(graphql_type_hint=list[str]),
        }
        GraphQL.manager_registry = {
            related_manager.__name__: related_manager,
            parent_manager.__name__: parent_manager,
        }
        GraphQL.graphql_type_registry = {
            related_manager.__name__: related_type,
            parent_manager.__name__: parent_type,
        }

        grouped_type = GraphQL._get_or_create_group_type(parent_manager)
        field_names = set(grouped_type._meta.fields)

        self.assertIn("secrets_list", field_names)
        self.assertIn("secrets_groups", field_names)
        self.assertNotIn("secrets", field_names)
        self.assertIn("owner_list", field_names)
        self.assertIn("owner_groups", field_names)
        self.assertIn("labels", field_names)
        self.assertNotIn("labels_groups", field_names)
        self.assertIs(GraphQL._get_or_create_group_type(parent_manager), grouped_type)

    def test_grouped_file_resolver_rejects_aggregate_file_values(self) -> None:
        related_manager, parent_manager = self._related_manager_types()
        GraphQL.manager_registry = {
            related_manager.__name__: related_manager,
            parent_manager.__name__: parent_manager,
        }
        GraphQL.graphql_type_registry[parent_manager.__name__] = type(
            "ParentManagerType",
            (graphene.ObjectType,),
            {},
        )

        grouped_type = GraphQL._get_or_create_group_type(parent_manager)
        resolver = grouped_type.resolve_stored_file
        with self.assertRaises(UnsupportedGroupedFieldError):
            resolver(SimpleNamespace(), SimpleNamespace())

    def test_relation_bucket_helpers_preserve_scope_and_deduplicate(self) -> None:
        related_manager, _parent_manager = self._related_manager_types()
        first = self._manager_with_id(related_manager, 1)
        duplicate = self._manager_with_id(related_manager, 1)
        second = self._manager_with_id(related_manager, 2)
        source = MaterializedBucket(related_manager, [first, duplicate])

        self.assertIs(GraphQL._as_relation_bucket(source, related_manager), source)
        self.assertEqual(
            list(GraphQL._as_relation_bucket(first, related_manager)), [first]
        )
        self.assertEqual(list(GraphQL._as_relation_bucket(None, related_manager)), [])

        grouped_parent = SimpleNamespace(
            _ensure_as_of_compatible=lambda: None,
            members=(
                SimpleNamespace(relations=source),
                SimpleNamespace(relations=None),
                SimpleNamespace(relations=second),
                SimpleNamespace(relations=object()),
            ),
        )
        relation_bucket = GraphQL._grouped_relation_bucket(
            grouped_parent,
            "relations",
            related_manager,
        )
        self.assertEqual(
            [item.identification for item in relation_bucket],
            [{"id": 1}, {"id": 2}],
        )

        incomplete = RequestBucket(
            related_manager,
            SimpleNamespace(),
            response_is_complete=False,
        )
        with self.assertRaises(GraphQLError):
            GraphQL._grouped_relation_bucket(
                SimpleNamespace(members=(SimpleNamespace(relations=incomplete),)),
                "relations",
                related_manager,
            )

        self.assertTrue(
            GraphQL._is_bucket_collection_field(Bucket[related_manager] | None)
        )
        self.assertFalse(
            GraphQL._is_bucket_collection_field(Bucket[related_manager] | str)
        )

    def test_relation_group_field_handles_eligibility_collisions_and_lazy_pages(
        self,
    ) -> None:
        related_manager, _parent_manager = self._related_manager_types()
        GraphQL.manager_registry = {related_manager.__name__: related_manager}

        fields: dict[str, object] = {}
        GraphQL._add_relation_group_field(fields, "related_list", related_manager)
        self.assertIn("related_groups", fields)
        self.assertIn("resolve_related_groups", fields)

        with patch(
            "general_manager.api.graphql._eligible_group_key_fields_fn",
            return_value={},
        ):
            skipped: dict[str, object] = {}
            GraphQL._add_relation_group_field(skipped, "related_list", related_manager)
        self.assertEqual(skipped, {})

        with self.assertRaisesRegex(ValueError, "collides"):
            GraphQL._add_relation_group_field(
                {"related_groups": graphene.String()},
                "related_list",
                related_manager,
            )

        lazy_fields: dict[str, object] = {}
        GraphQL._add_relation_group_field(
            lazy_fields,
            "related_list",
            related_manager,
            lazy_page=True,
        )
        self.assertIn("related_groups", lazy_fields)

    def test_grouped_singular_relation_uses_fallback_names_and_reports_collision(
        self,
    ) -> None:
        related_manager, _parent_manager = self._related_manager_types()
        GraphQL.manager_registry = {related_manager.__name__: related_manager}

        fields: dict[str, object] = {}
        GraphQL._add_grouped_singular_relation_fields(
            fields,
            "owner",
            related_manager,
            reserved_relation_names={
                "owner_list",
                "owner_groups",
            },
        )
        self.assertIn("owner_relation_list", fields)
        self.assertIn("owner_relation_groups", fields)

        with self.assertRaisesRegex(ValueError, "collides"):
            GraphQL._add_grouped_singular_relation_fields(
                {},
                "owner",
                related_manager,
                reserved_relation_names={
                    "owner_list",
                    "owner_groups",
                    "owner_relation_list",
                    "owner_relation_groups",
                },
            )
