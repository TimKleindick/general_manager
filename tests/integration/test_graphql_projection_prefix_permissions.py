"""Native projection paths share traversal while retaining every permission gate."""

from collections import Counter
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import graphene
from django.contrib.auth import get_user_model
from django.db.models import (
    BooleanField,
    CASCADE,
    CharField,
    ForeignKey,
    ManyToManyField,
)
from graphql import GraphQLError

from general_manager.api.graphql import GraphQL
from general_manager.api import graphql_resolvers
from general_manager.api.property import GraphQLProperty
from general_manager.api.graphql_query_permissions import authorize_query_fields
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.search.backend_registry import (
    configure_search_backend,
    get_search_backend,
)
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from general_manager.search.projection import validate_source_paths
from general_manager.search.utils import extract_value
from general_manager.utils.testing import GeneralManagerTransactionTestCase


SOURCE_PATHS = (
    "id",
    "industrial_engineering_officer__first_name",
    "industrial_engineering_officer__full_name",
    "industrial_engineering_officer__id",
    "industrial_engineering_officer__is_employed",
    "industrial_engineering_officer__last_name",
    "name",
    "name_erp",
    "plant_floor__constellium_plant__id",
    "plant_floor__constellium_plant__name",
    "plant_floor__floor_name",
    "plant_floor__id",
    "process_chain_list__id",
    "process_chain_list__part__part_type__id",
    "process_chain_list__part__part_type__name",
    "production_officer__first_name",
    "production_officer__full_name",
    "production_officer__id",
    "production_officer__is_employed",
    "production_officer__last_name",
    "supplier_list__id",
    "supplier_list__name",
    "typical_part_type__id",
    "typical_part_type__name",
    "work_center_type__id",
    "work_center_type__work_center_type",
)
DOCUMENT_FIELDS = tuple(f"value_{index}" for index in range(len(SOURCE_PATHS)))
FIELD_DEPENDENCIES = {
    field: (path,) for field, path in zip(DOCUMENT_FIELDS, SOURCE_PATHS, strict=True)
}


class TestProjectionPrefixPlanning(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """Build the native 26-path/10-root shape used by the KH projection."""

        class ProjectionDetails(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100, default="detail")
                floor_name = CharField(max_length=100, default="floor")
                work_center_type = CharField(max_length=100, default="type")
                first_name = CharField(max_length=100, default="first")
                last_name = CharField(max_length=100, default="last")
                full_name = CharField(max_length=100, default="full")
                is_employed = BooleanField(default=True)
                constellium_plant = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                part = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                part_type = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                floor_name: ClassVar[dict] = {"read": ["isAdmin"]}

        class NativeWorkCenterSource(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                name_erp = CharField(max_length=100)
                plant_floor = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                work_center_type = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                typical_part_type = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                industrial_engineering_officer = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                production_officer = ForeignKey(
                    "general_manager.ProjectionDetails",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                    related_name="+",
                )
                supplier = ManyToManyField(
                    "general_manager.ProjectionDetails", blank=True, related_name="+"
                )
                process_chain = ManyToManyField(
                    "general_manager.ProjectionDetails", blank=True, related_name="+"
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

            class SearchConfig:
                indexes: ClassVar[list] = [
                    IndexConfig(name="global", fields=DOCUMENT_FIELDS)
                ]
                field_dependencies: ClassVar[dict[str, tuple[str, ...]]] = (
                    FIELD_DEPENDENCIES
                )

                @staticmethod
                def to_document(instance):
                    """Project each declared native path without changing its source."""
                    return {
                        field: extract_value(instance, paths[0])
                        for field, paths in FIELD_DEPENDENCIES.items()
                    }

        cls.Source = NativeWorkCenterSource
        cls.Details = ProjectionDetails
        cls.general_manager_classes = [ProjectionDetails, NativeWorkCenterSource]

    def setUp(self):
        """Use a real in-memory search backend and synthetic native ORM rows."""
        super().setUp()
        self.user = get_user_model().objects.create_user(username="prefix-user")
        self.backend = DevSearchBackend()
        previous = get_search_backend()
        configure_search_backend(self.backend)
        self.addCleanup(configure_search_backend, previous)
        model = self.Source.Interface._model
        model.objects.bulk_create(
            [
                model(name=f"center-{index}", name_erp=f"erp-{index}")
                for index in range(39)
            ]
        )
        for source in self.Source.all():
            SearchIndexer(self.backend).index_instance(source)
        self.schema = graphene.Schema(query=GraphQL._query_class)

    def search(self, query=""):
        """Exercise the generated resolver, source gates and backend together."""
        return self.schema.execute(
            'query($query: String!) { search(query: $query, types: ["NativeWorkCenterSource"]) { total results { __typename } } }',
            variable_values={"query": query},
            context_value=SimpleNamespace(user=self.user),
        )

    def test_shared_native_prefixes_fit_the_budget_without_dropping_sources(self):
        """Thirty-nine null/empty rows need ten distinct root checks each."""
        checked = []
        native = graphql_resolvers.check_read_permission

        def check(instance, info, field):
            """Record exact source decisions while retaining native permission logic."""
            checked.append((instance.identification["id"], field))
            return native(instance, info, field)

        with patch.object(
            graphql_resolvers, "check_read_permission", side_effect=check
        ):
            result = self.search()
        assert result.errors is None, result.errors
        assert result.data["search"]["total"] == 39
        assert len(result.data["search"]["results"]) == 10
        roots = {path.split("__")[0] for path in SOURCE_PATHS}
        assert len(roots) == 10
        assert len(checked) == 39 * len(roots)
        assert {field for _, field in checked} == roots
        assert set(Counter(checked).values()) == {1}

    def test_denied_nonmatching_child_still_rejects_before_backend(self):
        """Every leaf remains authorized even when another child is empty."""
        detail = self.Details.create(creator_id=self.user.id)
        first = self.Source.all().first()
        self.Source.Interface._model.objects.filter(pk=first.id).update(
            plant_floor_id=detail.id
        )
        with patch.object(self.backend, "search", wraps=self.backend.search) as search:
            for query in ("center", "nonmatching-value"):
                with self.subTest(query=query):
                    result = self.search(query)
                    assert result.errors
                    assert "query field 'floor_name'" in str(result.errors)
            search.assert_not_called()

    def test_unknown_child_rejects_even_when_all_relations_are_empty(self):
        """Prefix reuse never replaces static validation of every complete path."""
        dependencies = {**FIELD_DEPENDENCIES, "value_0": ("plant_floor__unknown",)}
        with (
            patch.object(self.Source.SearchConfig, "field_dependencies", dependencies),
            patch.object(self.backend, "search", wraps=self.backend.search) as search,
        ):
            result = self.search()
            assert result.errors
            assert "Invalid search source path" in str(result.errors)
            search.assert_not_called()

    def test_prefix_plan_does_not_share_decisions_between_candidates(self):
        """Even repeated identities remain separate budgeted candidate visits."""
        source = self.Source.all().first()
        with (
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                1,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            authorize_query_fields(
                [source, source],
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                ["plant_floor__id", "plant_floor__floor_name"],
                native_paths=True,
            )

    def test_repeated_candidate_permissions_are_evaluated_again(self):
        """Structural sharing never treats a previous candidate grant as authority."""
        source = self.Source.all().first()
        with (
            patch.object(
                graphql_resolvers, "check_read_permission", side_effect=[True, False]
            ) as check,
            self.assertRaisesMessage(GraphQLError, "query field 'plant_floor'"),
        ):
            authorize_query_fields(
                [source, source],
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                ["plant_floor__id", "plant_floor__floor_name"],
                native_paths=True,
            )
        assert check.call_count == 2

    def test_related_row_denial_still_precedes_all_child_checks(self):
        """Sharing a relation prefix retains its target row gate before leaves."""
        detail = self.Details.create(creator_id=self.user.id)
        first = self.Source.all().first()
        self.Source.Interface._model.objects.filter(pk=first.id).update(
            plant_floor_id=detail.id
        )
        with (
            patch.object(
                self.Details.Permission, "can_read_instance", return_value=False
            ),
            patch.object(self.backend, "search", wraps=self.backend.search) as search,
        ):
            result = self.search("nonmatching-value")
            assert result.errors
            assert "traverse query relation" in str(result.errors)
            search.assert_not_called()

    def _members_descriptor(self, field_name, members):
        """Expose a real typed manager collection with controlled member occurrences."""

        def getter(_instance):
            """Return the exact members so identity reuse remains observable."""
            return members

        getter.__annotations__["return"] = list[self.Details]
        descriptor = GraphQLProperty(getter, cache="none")
        descriptor.__set_name__(self.Source, field_name)
        return descriptor

    def test_every_collection_child_is_checked_for_each_occurrence_and_branch(self):
        """Duplicate members and another prefix cannot reuse a target grant."""
        detail = self.Details.create(creator_id=self.user.id)
        source = self.Source.all().first()
        paths = [
            "left_members__id",
            "left_members__name",
            "right_members__id",
            "right_members__name",
        ]
        checked = []
        native = graphql_resolvers.check_read_permission

        def check(instance, info, field):
            """Measure all leaf checks while keeping the real native policy."""
            checked.append((instance, field))
            return native(instance, info, field)

        with (
            patch.object(
                self.Source,
                "left_members",
                self._members_descriptor("left_members", [detail, detail]),
                create=True,
            ),
            patch.object(
                self.Source,
                "right_members",
                self._members_descriptor("right_members", [detail]),
                create=True,
            ),
            patch.object(graphql_resolvers, "check_read_permission", side_effect=check),
            patch.object(
                graphql_resolvers,
                "can_read_instance",
                wraps=graphql_resolvers.can_read_instance,
            ) as row,
        ):
            validate_source_paths(self.Source, paths)
            authorize_query_fields(
                [source],
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                paths,
                native_paths=True,
            )
        assert sum(instance is source for instance, _ in checked) == 2
        assert [field for instance, field in checked if instance is detail] == [
            "id",
            "name",
        ] * 3
        assert row.call_count == 3

    def test_conditional_field_denial_in_a_second_prefix_is_not_cached(self):
        """A second source branch still evaluates its own conditional leaf gate."""
        detail = self.Details.create(creator_id=self.user.id)
        source = self.Source.all().first()
        paths = [
            "left_members__id",
            "left_members__name",
            "right_members__id",
            "right_members__name",
        ]
        native = graphql_resolvers.check_read_permission
        name_checks = 0

        def check(instance, info, field):
            """Deny the same exact target object when reached through another branch."""
            nonlocal name_checks
            if instance is detail and field == "name":
                name_checks += 1
                return name_checks == 1
            return native(instance, info, field)

        with (
            patch.object(
                self.Source,
                "left_members",
                self._members_descriptor("left_members", [detail]),
                create=True,
            ),
            patch.object(
                self.Source,
                "right_members",
                self._members_descriptor("right_members", [detail]),
                create=True,
            ),
            patch.object(graphql_resolvers, "check_read_permission", side_effect=check),
            self.assertRaisesMessage(GraphQLError, "query field 'name'"),
        ):
            validate_source_paths(self.Source, paths)
            authorize_query_fields(
                [source],
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                paths,
                native_paths=True,
            )
        assert name_checks == 2

    def test_duplicate_members_still_consume_the_unchanged_budget(self):
        """Two required fields on two occurrences plus their owner cost five visits."""
        detail = self.Details.create(creator_id=self.user.id)
        source = self.Source.all().first()
        paths = ["members__id", "members__name"]
        with (
            patch.object(
                self.Source,
                "members",
                self._members_descriptor("members", [detail, detail]),
                create=True,
            ),
            patch(
                "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
                4,
            ),
            self.assertRaisesMessage(GraphQLError, "candidate limit"),
        ):
            validate_source_paths(self.Source, paths)
            authorize_query_fields(
                [source],
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                paths,
                native_paths=True,
            )

    def test_multiple_child_fields_cannot_hide_an_incomplete_related_bucket(self):
        """All child fields on a visible member still require a complete collection."""
        detail = self.Details.create(creator_id=self.user.id)
        source = self.Source.all().first()
        partial = RequestBucket(
            self.Details,
            self.Details.Interface,
            items=(detail,),
            response_is_complete=False,
        )
        paths = ["members__id", "members__name"]
        with (
            patch.object(
                self.Source,
                "members",
                self._members_descriptor("members", partial),
                create=True,
            ),
            self.assertRaisesMessage(GraphQLError, "complete candidate set"),
        ):
            validate_source_paths(self.Source, paths)
            authorize_query_fields(
                [source],
                SimpleNamespace(context=SimpleNamespace(user=self.user)),
                paths,
                native_paths=True,
            )
