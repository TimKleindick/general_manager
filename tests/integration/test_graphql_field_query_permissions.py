# ruff: noqa: S106
from typing import ClassVar
from types import SimpleNamespace
import graphene
from django.contrib.auth import get_user_model
from django.db.models import CharField, ForeignKey, CASCADE
from unittest.mock import patch, MagicMock
from django.db import connection
from django.test.utils import CaptureQueriesContext
from general_manager.api.graphql_resolvers import create_list_resolver
from general_manager.api.graphql_query_permissions import authorize_query_fields
from graphql import GraphQLError
from general_manager.api.graphql import GraphQL
from general_manager.search.config import IndexConfig
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import OverrideManagerPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestFieldInference(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class ConfidentialEntry(GeneralManager):
            class SearchConfig:
                indexes: ClassVar[list] = [
                    IndexConfig(
                        name="global",
                        fields=["secret"],
                        filters=["name"],
                        sorts=["name"],
                    )
                ]

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                secret = CharField(max_length=100)
                contains = CharField(max_length=100, default="hidden")

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                secret: ClassVar[dict] = {"read": ["isAdmin"]}
                contains: ClassVar[dict] = {"read": ["isAdmin"]}

        class ConfidentialLink(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                entry = ForeignKey(
                    "general_manager.ConfidentialEntry", on_delete=CASCADE
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]
                entry: ClassVar[dict] = {"read": ["isAdmin"]}

        cls.Link = ConfidentialLink
        cls.general_manager_classes = [ConfidentialEntry, ConfidentialLink]
        cls.Entry = ConfidentialEntry

    def test_filter_reveals_secret_while_field_denied(self):
        user = get_user_model().objects.create_user(username="field-probe")
        self.Entry.create(name="visible", secret="hidden-value", creator_id=user.id)
        schema = graphene.Schema(query=GraphQL._query_class)
        context = SimpleNamespace(user=user)
        direct = schema.execute(
            "{ confidentialEntryList { items { secret } } }", context_value=context
        )
        present = schema.execute(
            '{ confidentialEntryList(filter: {secret: "hidden-value"}) { pageInfo { totalCount } } }',
            context_value=context,
        )
        absent = schema.execute(
            '{ confidentialEntryList(filter: {secret: "wrong"}) { pageInfo { totalCount } } }',
            context_value=context,
        )
        assert direct.data["confidentialEntryList"]["items"][0]["secret"] is None
        assert present.errors and absent.errors
        assert "Permission denied to use query field" in str(present.errors)
        assert "Permission denied to use query field" in str(absent.errors)

    def test_allowed_field_filter_preserves_membership_and_count(self):
        user = get_user_model().objects.create_user(username="allowed")
        self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        result = graphene.Schema(query=GraphQL._query_class).execute(
            '{ confidentialEntryList(filter: {name: "visible"}) { items { name } pageInfo { totalCount } } }',
            context_value=SimpleNamespace(user=user),
        )
        assert result.errors is None
        assert result.data["confidentialEntryList"]["pageInfo"]["totalCount"] == 1

    def test_order_and_exclude_rejected_before_value_dependent_result(self):
        user = get_user_model().objects.create_user(username="ordering")
        self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        for arguments in [
            'exclude: {secret: "absent"}',
            "orderBy: [{field: secret, direction: ASC}]",
        ]:
            result = graphene.Schema(query=GraphQL._query_class).execute(
                "{ confidentialEntryList("
                + arguments
                + ") { items { name } pageInfo { totalCount } } }",
                context_value=SimpleNamespace(user=user),
            )
            assert result.errors
            assert "Permission denied to use query field" in str(result.errors)

    def test_conditional_field_checks_cover_nonmatching_rows(self):
        user = get_user_model().objects.create_user(username="conditional")
        for name in ["allowed", "denied"]:
            self.Entry.create(name=name, secret=name, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        checks = []

        def permission(instance, info, field):
            checks.append((instance.name, field))
            return instance.name == "allowed"

        resolver = create_list_resolver(lambda *_: self.Entry.all(), self.Entry)
        with patch(
            "general_manager.api.graphql_resolvers.check_read_permission",
            side_effect=permission,
        ):
            with self.assertRaisesMessage(GraphQLError, "Permission denied"):
                resolver(None, info, filter={"secret": "allowed"})
        assert set(checks) == {("allowed", "secret"), ("denied", "secret")}

    def test_all_conditionally_authorized_candidates_allowed_with_measured_queries(
        self,
    ):
        user = get_user_model().objects.create_user(username="measurement")
        for name in ["first", "second"]:
            self.Entry.create(name=name, secret=name, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        checks = []

        def permission(instance, info, field):
            checks.append((instance.name, field))
            return instance.name in {"first", "second"}

        with patch(
            "general_manager.api.graphql_resolvers.check_read_permission",
            side_effect=permission,
        ):
            with CaptureQueriesContext(connection) as captured:
                authorize_query_fields(self.Entry.all(), info, ["secret"])
        assert len(checks) == 2
        assert len(captured) == 1, list(captured)

    def test_authorization_budget_fails_explicitly(self):
        user = get_user_model().objects.create_user(username="budget")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        with patch(
            "general_manager.api.graphql_query_permissions.MAX_QUERY_PERMISSION_CANDIDATES",
            1,
        ):
            with self.assertRaisesMessage(GraphQLError, "candidate limit"):
                authorize_query_fields(
                    [entry, entry],
                    SimpleNamespace(context=SimpleNamespace(user=user)),
                    ["name"],
                )

    def test_relation_id_alias_and_nested_field_require_permissions(self):
        user = get_user_model().objects.create_user(username="relations")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry=entry, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        for path in ["entry_id", "entry__secret", "entry__id"]:
            with self.assertRaisesMessage(GraphQLError, "Permission denied"):
                authorize_query_fields([link], info, [path])
        native = self.Link.Permission.check_permission

        def allow_relation(permission, action, field):
            return True if field == "entry" else native(permission, action, field)

        with patch.object(self.Link.Permission, "check_permission", allow_relation):
            with self.assertRaisesMessage(GraphQLError, "Permission denied"):
                authorize_query_fields([link], info, ["entry__secret"])
            authorize_query_fields([link], info, ["entry__name"])

    def test_fulltext_projection_denied_before_backend_search(self):
        user = get_user_model().objects.create_user(username="search-projection")
        self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        backend = MagicMock()
        with patch(
            "general_manager.api.graphql_search.get_search_backend",
            return_value=backend,
        ):
            result = graphene.Schema(query=GraphQL._query_class).execute(
                '{ search(query: "hidden", types: ["ConfidentialEntry"]) { total } }',
                context_value=SimpleNamespace(user=user),
            )
        assert result.errors
        assert "Permission denied to use query field" in str(result.errors)
        backend.search.assert_not_called()

    def test_lookup_named_fields_are_not_discarded(self):
        user = get_user_model().objects.create_user(username="lookup-field")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry=entry, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        authorize_query_fields([entry], info, ["name__in"])
        for path in ["contains", "contains__exact"]:
            with self.assertRaisesMessage(GraphQLError, "Permission denied"):
                authorize_query_fields([entry], info, [path])
        with patch.object(self.Link.Permission, "check_permission", return_value=True):
            with self.assertRaisesMessage(GraphQLError, "Permission denied"):
                authorize_query_fields([link], info, ["entry__contains"])
            with self.assertRaisesMessage(GraphQLError, "Permission denied"):
                authorize_query_fields([link], info, ["entry__contains__in"])

    def test_search_checks_conditionally_denied_nonhits_before_backend(self):
        user = get_user_model().objects.create_user(username="search-nonhit")
        for name in ["match", "nonmatch"]:
            self.Entry.create(name=name, secret=name, creator_id=user.id)
        checked = []

        def conditional(instance, info, field):
            checked.append(instance.name)
            return instance.name == "match"

        backend = MagicMock()
        with (
            patch(
                "general_manager.api.graphql_search.get_search_backend",
                return_value=backend,
            ),
            patch(
                "general_manager.api.graphql_resolvers.check_read_permission",
                side_effect=conditional,
            ),
        ):
            result = graphene.Schema(query=GraphQL._query_class).execute(
                '{ search(query: "match", types: ["ConfidentialEntry"]) { total } }',
                context_value=SimpleNamespace(user=user),
            )
        assert result.errors
        assert set(checked) == {"match", "nonmatch"}
        backend.search.assert_not_called()

    def test_relation_isnull_lookup_does_not_authorize_nonexistent_related_field(self):
        user = get_user_model().objects.create_superuser(
            username="relation-lookup", password="test"
        )
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry_id=entry.id, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        authorize_query_fields([link], info, ["entry__isnull"])
        # Real related fields whose names are lookup words still need field rights.
        with patch(
            "general_manager.api.graphql_resolvers.check_read_permission",
            side_effect=lambda _instance, _info, field: field != "contains",
        ):
            with self.assertRaisesMessage(
                GraphQLError, "Permission denied to use query field"
            ):
                authorize_query_fields([link], info, ["entry__contains"])

    def test_aggregate_gate_does_not_reuse_django_permission_cache(self):
        from django.contrib.auth.models import Permission
        from general_manager.api.graphql_resolvers import can_read_class_for_user

        user = get_user_model().objects.create_user(username="aggregate-revocation")
        permission = Permission.objects.get(
            content_type__app_label="auth", codename="view_user"
        )
        user.user_permissions.add(permission)
        self.assertTrue(user.has_perm("auth.view_user"))
        with patch.object(
            self.Entry.Permission, "__read__", ["hasPermission:auth.view_user"]
        ):
            self.assertTrue(can_read_class_for_user(self.Entry, user))
            user.user_permissions.remove(permission)
            self.assertFalse(can_read_class_for_user(self.Entry, user))
