# ruff: noqa: S106
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID
from types import SimpleNamespace
import graphene
from django.contrib.auth import get_user_model
from django.db.models import CharField, ForeignKey, CASCADE
from unittest.mock import patch, MagicMock
from django.db import connection
from django.test.utils import CaptureQueriesContext
from general_manager.api.graphql_resolvers import (
    apply_read_authorization,
    create_list_resolver,
)
from general_manager.api.graphql_errors import get_read_permission_filter
from general_manager.api.graphql_query_permissions import authorize_query_fields
from graphql import GraphQLError
from general_manager.api.graphql import GraphQL
from general_manager.api.property import GraphQLProperty
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.search.config import IndexConfig, SearchConfigSpec
from general_manager.search.projection import (
    projection_source_paths,
    validate_source_paths,
)
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import (
    AdditiveManagerPermission,
    OverrideManagerPermission,
)
from general_manager.permission.permission_checks import (
    permission_functions,
    register_permission,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestFieldInference(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """Register real ORM sources with separate owner, field and target gates."""

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

        class ConfidentialReference(GeneralManager):
            class SearchConfig:
                indexes: ClassVar[list] = [
                    IndexConfig(
                        name="global",
                        fields=["reference"],
                        filters=["reference"],
                        identity_fields={"reference": int},
                    )
                ]
                field_dependencies: ClassVar[dict] = {"reference": ("entry_id",)}

                @staticmethod
                def to_document(instance):
                    return {"reference": instance.entry_id}

            class Interface(DatabaseInterface):
                entry = ForeignKey(
                    "general_manager.ConfidentialEntry",
                    on_delete=CASCADE,
                    null=True,
                    blank=True,
                )

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        cls.Link = ConfidentialLink
        cls.Reference = ConfidentialReference
        cls.general_manager_classes = [
            ConfidentialEntry,
            ConfidentialLink,
            ConfidentialReference,
        ]
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

    def test_prefilter_only_plan_does_not_prove_native_field_read(self):
        """A stable registry snapshot cannot prove filter/predicate equivalence."""
        user = get_user_model().objects.create_user(username="native-proof-gap")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        original = permission_functions["public"]
        # The mutation precedes planning and consumption; no stale-request cache
        # is involved. Keep the native filter, but replace its predicate.
        with patch.dict(
            permission_functions,
            {
                "public": {
                    "permission_method": lambda *_: False,
                    "permission_filter": original["permission_filter"],
                }
            },
        ):
            plan = get_read_permission_filter(self.Entry, info)
            assert plan.decision == "allow_all"
            assert plan.requires_instance_check is False
            result = apply_read_authorization(
                self.Entry.all(), self.Entry, info, source="list"
            )
            assert result.requires_instance_check is False
            assert [row.id for row in result.queryset] == [entry.id]
            assert self.Entry.Permission(entry, user).can_read_instance() is False
            assert (
                self.Entry.Permission(entry, user).check_permission("read", "name")
                is False
            )
            with self.assertRaisesMessage(
                GraphQLError, "Permission denied to use query field"
            ):
                authorize_query_fields(result.queryset, info, {"name"})

    def test_native_matches_prefilter_coercion_cannot_grant_query_field_read(self):
        """Native SQL coercion is not the native string-comparison predicate."""
        user = get_user_model().objects.create_user(username="matches-proof-gap")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        with patch.object(
            self.Entry.Permission, "__read__", [f"matches:id:0{entry.id}"]
        ):
            result = apply_read_authorization(
                self.Entry.all(), self.Entry, info, source="list"
            )
            assert result.requires_instance_check is False
            assert [row.id for row in result.queryset] == [entry.id]
            assert self.Entry.Permission(entry, user).can_read_instance() is False
            with self.assertRaisesMessage(
                GraphQLError, "Permission denied to use query field"
            ):
                authorize_query_fields(result.queryset, info, {"name"})

    def test_backend_operand_checks_denied_aliases_before_filter_exclude_and_sort(self):
        """Every owning alias remains a gate even for nonmatching predicates."""
        user = get_user_model().objects.create_user(username="backend-alias-gates")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        assert self.Entry.Permission(entry, user).check_permission("read", "name")
        assert not self.Entry.Permission(entry, user).check_permission("read", "secret")
        metadata = self.Entry.Interface.get_attribute_types()
        metadata = {
            **metadata,
            "secret": {**metadata["secret"], "filter_lookup": "name"},
        }
        resolver = create_list_resolver(lambda *_: self.Entry.all(), self.Entry)
        with patch.object(
            self.Entry.Interface, "get_attribute_types", return_value=metadata
        ):
            for arguments in (
                {"filter": {"name": "visible"}},
                {"filter": {"name": "absent"}},
                {"filter": {"name__exact": "visible"}},
                {"filter": {"name__exact": "absent"}},
                {"exclude": {"name": "visible"}},
                {"exclude": {"name": "absent"}},
                {"order_by": [{"field": "name", "direction": "ASC"}]},
                {"order_by": [{"field": "name", "direction": "DESC"}]},
            ):
                with self.subTest(arguments=arguments):
                    with self.assertRaisesMessage(
                        GraphQLError, "Permission denied to use query field 'name'"
                    ):
                        resolver(None, info, page_size=1, **arguments)

    def test_generated_foreign_key_id_alias_checks_native_owner_before_count(self):
        """The genuine ORM attname does not borrow the owning field's grant."""
        user = get_user_model().objects.create_user(username="native-fk-alias-gates")
        entry = self.Entry.create(name="visible", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry=entry, creator_id=user.id)
        permission = self.Link.Permission(link, user)
        assert permission.check_permission("read", "entry_id") is True
        assert permission.check_permission("read", "entry") is False
        metadata = self.Link.Interface.get_attribute_types()
        assert metadata["entry_id"]["filter_lookup"] == "entry_id"
        assert metadata["entry"]["filter_lookup"] == "entry"
        schema = graphene.Schema(query=GraphQL._query_class)
        for identifier in (entry.id, entry.id + 1):
            with self.subTest(identifier=identifier):
                result = schema.execute(
                    "{ confidentialLinkList(filter: {entryId_Exact: "
                    + str(identifier)
                    + "}) { pageInfo { totalCount } } }",
                    context_value=SimpleNamespace(user=user),
                )
                assert result.errors
                assert "Permission denied to use query field 'entry_id'" in str(
                    result.errors
                )

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

    def test_projection_source_paths_are_exact_on_empty_candidate_scopes(self):
        """Static source proof rejects lookup syntax even without candidate rows."""
        for path in [
            "missing",
            "name__in",
            "entry__missing",
            "entry__name__in",
            "entry",
        ]:
            with self.subTest(path=path):
                with self.assertRaisesMessage(ValueError, "source path"):
                    validate_source_paths(self.Link, [path])
        validate_source_paths(self.Link, ["entry__contains", "entry__id"])

    def test_projection_relation_dependencies_use_native_field_and_row_gates(self):
        """Every relation hop and terminal field needs its native read permission."""
        user = get_user_model().objects.create_user(username="projection-relations")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry=entry, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        config = SearchConfigSpec(
            indexes=(IndexConfig(name="global", fields=["snippet"]),),
            to_document=lambda item: {"snippet": item.entry.secret},
            field_dependencies={"snippet": ("entry__secret",)},
        )
        paths = projection_source_paths(self.Link, config, ["snippet"])
        with self.assertRaisesMessage(GraphQLError, "query field 'entry'"):
            authorize_query_fields([link], info, paths, native_paths=True)
        native = self.Link.Permission.check_permission

        def allow_relation(permission, action, field):
            """Open only the owner hop so target-field denial stays observable."""
            return True if field == "entry" else native(permission, action, field)

        with patch.object(self.Link.Permission, "check_permission", allow_relation):
            with self.assertRaisesMessage(GraphQLError, "query field 'secret'"):
                authorize_query_fields([link], info, paths, native_paths=True)
            authorize_query_fields([link], info, ["entry__name"], native_paths=True)
            with patch.object(
                self.Entry.Permission, "can_read_instance", return_value=False
            ):
                with self.assertRaisesMessage(GraphQLError, "traverse query relation"):
                    authorize_query_fields(
                        [link], info, ["entry__name"], native_paths=True
                    )
            with self.assertRaisesMessage(GraphQLError, "query field 'contains'"):
                authorize_query_fields(
                    [link], info, ["entry__contains"], native_paths=True
                )

    def test_projection_native_source_names_ignore_backend_filter_aliases(self):
        user = get_user_model().objects.create_user(username="projection-names")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        metadata = self.Entry.Interface.get_attribute_types()
        metadata = {
            **metadata,
            "secret": {**metadata["secret"], "filter_lookup": "name"},
        }
        with patch.object(
            self.Entry.Interface, "get_attribute_types", return_value=metadata
        ):
            validate_source_paths(self.Entry, ["name"])
            authorize_query_fields(
                [entry],
                SimpleNamespace(context=SimpleNamespace(user=user)),
                ["name"],
                native_paths=True,
            )

    def test_projection_rejects_unknown_and_ambiguous_leaf_types_without_rows(self):
        """Opaque annotations cannot establish scalar projection provenance."""
        metadata = self.Link.Interface.get_attribute_types()
        for annotation in (
            None,
            Any,
            object,
            self.Entry | self.Link,
            list[self.Entry | self.Link],
        ):
            with self.subTest(annotation=annotation):
                with patch.object(
                    self.Link.Interface,
                    "get_attribute_types",
                    return_value={**metadata, "entry": {"type": annotation}},
                ):
                    with self.assertRaisesMessage(ValueError, "source path"):
                        validate_source_paths(self.Link, ["entry"])
        with patch.object(
            self.Link.Interface,
            "get_attribute_types",
            return_value={
                key: value for key, value in metadata.items() if key != "entry"
            },
        ):
            with self.assertRaisesMessage(ValueError, "source path"):
                validate_source_paths(self.Link, ["entry"])

    def test_projection_accepts_proven_scalar_container_annotations(self):
        """Typed scalar containers remain valid sources without instance reads."""
        metadata = self.Entry.Interface.get_attribute_types()
        for annotation in (
            int | str,
            str | None,
            UUID,
            list[str | None],
            tuple[int, ...],
            Annotated[list[str], "native values"],
            dict[str, list[int]],
            Literal["open", 3],
        ):
            with self.subTest(annotation=annotation):
                with patch.object(
                    self.Entry.Interface,
                    "get_attribute_types",
                    return_value={**metadata, "name": {"type": annotation}},
                ):
                    validate_source_paths(self.Entry, ["name"])

    def test_projection_scalar_id_name_does_not_inherit_another_relation_gate(self):
        """A genuine scalar name ending in _id is independent of another relation."""
        user = get_user_model().objects.create_user(username="scalar-id-source")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        metadata = self.Entry.Interface.get_attribute_types()
        metadata = {
            **metadata,
            "subject": {"type": self.Link, "relation_kind": "direct"},
            "subject_id": {"type": int},
        }
        native = self.Entry.Permission.check_permission

        def permission(rule, action, field):
            """Deny the unrelated hop while retaining the real scalar's rules."""
            return False if field == "subject" else native(rule, action, field)

        with (
            patch.object(
                self.Entry.Interface, "get_attribute_types", return_value=metadata
            ),
            patch.object(self.Entry.Permission, "check_permission", permission),
        ):
            validate_source_paths(self.Entry, ["subject_id"])
            authorize_query_fields(
                [entry],
                SimpleNamespace(context=SimpleNamespace(user=user)),
                ["subject_id"],
                native_paths=True,
            )

    def test_projection_native_foreign_key_alias_still_checks_owning_relation(self):
        user = get_user_model().objects.create_user(username="native-fk-source")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry=entry, creator_id=user.id)
        validate_source_paths(self.Link, ["entry_id"])
        with self.assertRaisesMessage(GraphQLError, "query field 'entry_id'"):
            authorize_query_fields(
                [link],
                SimpleNamespace(context=SimpleNamespace(user=user)),
                ["entry_id"],
                native_paths=True,
            )

    def test_projection_native_foreign_key_requires_referenced_row_and_field(self):
        """An attname source has the same target gates as its explicit ID path."""
        user = get_user_model().objects.create_user(username="native-fk-target")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        reference = self.Reference.create(entry=entry, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        validate_source_paths(self.Reference, ["entry_id"])
        authorize_query_fields([reference], info, ["entry_id"], native_paths=True)
        with patch.object(
            self.Entry.Permission, "can_read_instance", return_value=False
        ):
            with self.assertRaisesMessage(GraphQLError, "traverse query relation"):
                authorize_query_fields(
                    [reference], info, ["entry_id"], native_paths=True
                )
        native = self.Entry.Permission.check_permission

        def deny_id(permission, action, field):
            """Deny the referenced identity independently of its row permission."""
            return False if field == "id" else native(permission, action, field)

        with patch.object(self.Entry.Permission, "check_permission", deny_id):
            with self.assertRaisesMessage(GraphQLError, "query field 'id'"):
                authorize_query_fields(
                    [reference], info, ["entry_id"], native_paths=True
                )

    def test_projection_native_foreign_key_denies_hits_and_nonhits_before_search(self):
        """A projected FK cannot disclose a denied target through search counts."""
        from general_manager.search.backend_registry import (
            configure_search_backend,
            get_search_backend,
        )
        from general_manager.search.backends.dev import DevSearchBackend
        from general_manager.search.indexer import SearchIndexer

        user = get_user_model().objects.create_user(username="native-fk-search")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        reference = self.Reference.create(entry=entry, creator_id=user.id)
        previous_backend = get_search_backend()
        backend = DevSearchBackend()
        configure_search_backend(backend)
        self.addCleanup(configure_search_backend, previous_backend)
        SearchIndexer(backend).index_instance(reference)
        schema = graphene.Schema(query=GraphQL._query_class)
        query = '{ search(query: "", types: ["ConfidentialReference"]) { total } }'
        result = schema.execute(query, context_value=SimpleNamespace(user=user))
        assert result.errors is None, result.errors
        assert result.data["search"]["total"] == 1
        with (
            patch.object(
                self.Entry.Permission, "can_read_instance", return_value=False
            ),
            patch.object(backend, "search", wraps=backend.search) as search,
        ):
            for value in (entry.id, entry.id + 100):
                with self.subTest(value=value):
                    result = schema.execute(
                        '{ search(query: "", types: ["ConfidentialReference"], '
                        f'filters: "{{\\"reference\\": {value}}}") {{ total }} }}',
                        context_value=SimpleNamespace(user=user),
                    )
                    assert result.errors
                    assert "traverse query relation" in str(result.errors)
            search.assert_not_called()

    def test_projection_null_native_foreign_key_does_not_traverse_a_target(self):
        """An absent target needs no row decision or target getter access."""
        user = get_user_model().objects.create_user(username="native-fk-null")
        reference = self.Reference.create(entry=None, creator_id=user.id)
        with patch.object(
            self.Entry.Permission, "can_read_instance", return_value=False
        ):
            authorize_query_fields(
                [reference],
                SimpleNamespace(context=SimpleNamespace(user=user)),
                ["entry_id"],
                native_paths=True,
            )

    def test_projection_collection_sources_check_every_member(self):
        """Collection dependency checks include every member, including nonhits."""
        user = get_user_model().objects.create_user(username="projection-members")
        first = self.Entry.create(name="first", secret="one", creator_id=user.id)
        second = self.Entry.create(name="second", secret="two", creator_id=user.id)
        link = self.Link.create(name="link", entry=first, creator_id=user.id)
        info = SimpleNamespace(context=SimpleNamespace(user=user))

        def members(_instance):
            return [first, second]

        members.__annotations__["return"] = list[self.Entry]
        descriptor = GraphQLProperty(members, cache="none")
        descriptor.__set_name__(self.Link, "members")
        with patch.object(self.Link, "members", descriptor, create=True):
            validate_source_paths(self.Link, ["members__name"])
            authorize_query_fields([link], info, ["members__name"], native_paths=True)
            native = self.Entry.Permission.check_permission

            def conditional(permission, action, field):
                """Deny one member without changing the owner's readable relation."""
                return permission.instance.id != second.id and native(
                    permission, action, field
                )

            with patch.object(self.Entry.Permission, "check_permission", conditional):
                with self.assertRaisesMessage(GraphQLError, "query field 'name'"):
                    authorize_query_fields(
                        [link], info, ["members__name"], native_paths=True
                    )

    def test_projection_rejects_incomplete_nested_source_bucket(self):
        user = get_user_model().objects.create_user(username="projection-page")
        entry = self.Entry.create(name="entry", secret="hidden", creator_id=user.id)
        link = self.Link.create(name="link", entry=entry, creator_id=user.id)
        partial = RequestBucket(
            self.Entry,
            self.Entry.Interface,
            items=(entry,),
            response_is_complete=False,
        )

        def members(_instance):
            return partial

        members.__annotations__["return"] = list[self.Entry]
        descriptor = GraphQLProperty(members, cache="none")
        descriptor.__set_name__(self.Link, "members")
        with patch.object(self.Link, "members", descriptor, create=True):
            validate_source_paths(self.Link, ["members__name"])
            with self.assertRaisesMessage(GraphQLError, "complete candidate set"):
                authorize_query_fields(
                    [link],
                    SimpleNamespace(context=SimpleNamespace(user=user)),
                    ["members__name"],
                    native_paths=True,
                )


class TestNativeAdditiveQueryProofBoundary(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """Use the proposed native Additive scope without field read rules."""

        registry_patch = patch.dict(permission_functions)
        registry_patch.start()
        cls.addClassCleanup(registry_patch.stop)

        def visible_name_filter(_user, _config):
            return {"filter": {"name": "visible"}}

        @register_permission("nativeProofDenied", permission_filter=visible_name_filter)
        def deny_native_proof(_instance, _user, _config):
            return False

        class AdditiveProofEntry(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class AdditiveRegisteredProofEntry(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["nativeProofDenied"]

        class AdditiveMatchesProofEntry(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["matches:id:0123"]

        cls.Entry = AdditiveProofEntry
        cls.RegisteredEntry = AdditiveRegisteredProofEntry
        cls.MatchesEntry = AdditiveMatchesProofEntry
        cls.general_manager_classes = [
            AdditiveProofEntry,
            AdditiveRegisteredProofEntry,
            AdditiveMatchesProofEntry,
        ]

    def _assert_native_additive_scope(self, entry, user):
        owner = type(entry)
        permission_class = owner.Permission
        assert permission_class.__init__ is AdditiveManagerPermission.__init__
        assert (
            permission_class.can_read_instance
            is AdditiveManagerPermission.can_read_instance
        )
        assert (
            permission_class.check_permission
            is AdditiveManagerPermission.check_permission
        )
        assert (
            permission_class._evaluate_local_permission
            is AdditiveManagerPermission._evaluate_local_permission
        )
        permission = permission_class(entry, user)
        assert permission.__based_on__ is None
        for field in owner.Interface.get_attribute_types():
            assert permission._get_attribute_permission_expressions("read", field) == (
                [],
                False,
            )

    def _assert_graphql_counts_reject_hits_and_nonhits(
        self, user, list_field="additiveProofEntryList"
    ):
        schema = graphene.Schema(query=GraphQL._query_class)
        for name in ("visible", "absent"):
            with self.subTest(name=name):
                result = schema.execute(
                    "{ "
                    + list_field
                    + '(filter: {name: "'
                    + name
                    + '"}) { pageInfo { totalCount } } }',
                    context_value=SimpleNamespace(user=user),
                )
                assert result.errors
                assert "Permission denied to use query field 'name'" in str(
                    result.errors
                )

    def test_native_additive_public_counts_preserve_hits_and_nonhits(self):
        user = get_user_model().objects.create_user(username="additive-public-count")
        entry = self.Entry.create(name="visible", creator_id=user.id)
        self._assert_native_additive_scope(entry, user)
        schema = graphene.Schema(query=GraphQL._query_class)
        for name, expected_count in (("visible", 1), ("absent", 0)):
            with self.subTest(name=name):
                result = schema.execute(
                    '{ additiveProofEntryList(filter: {name: "'
                    + name
                    + '"}) { pageInfo { totalCount } } }',
                    context_value=SimpleNamespace(user=user),
                )
                assert result.errors is None
                assert (
                    result.data["additiveProofEntryList"]["pageInfo"]["totalCount"]
                    == expected_count
                )

    def test_native_additive_prefilter_only_plan_does_not_prove_field_read(self):
        user = get_user_model().objects.create_user(username="additive-registry-gap")
        entry = self.Entry.create(name="visible", creator_id=user.id)
        self._assert_native_additive_scope(entry, user)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        original = permission_functions["public"]
        with patch.dict(
            permission_functions,
            {
                "public": {
                    "permission_method": lambda *_: False,
                    "permission_filter": original["permission_filter"],
                }
            },
        ):
            result = apply_read_authorization(
                self.Entry.all(), self.Entry, info, source="list"
            )
            assert result.requires_instance_check is False
            assert [row.id for row in result.queryset] == [entry.id]
            assert self.Entry.Permission(entry, user).can_read_instance() is False
            self._assert_graphql_counts_reject_hits_and_nonhits(user)

    def test_native_additive_matches_coercion_does_not_grant_field_read(self):
        user = get_user_model().objects.create_user(username="additive-matches-gap")
        self.MatchesEntry.Interface._model.objects.create(id=123, name="visible")
        entry = self.MatchesEntry(123)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        self._assert_native_additive_scope(entry, user)
        result = apply_read_authorization(
            self.MatchesEntry.all(), self.MatchesEntry, info, source="list"
        )
        assert result.requires_instance_check is False
        assert [row.id for row in result.queryset] == [entry.id]
        assert self.MatchesEntry.Permission(entry, user).can_read_instance() is False
        self._assert_graphql_counts_reject_hits_and_nonhits(
            user, "additiveMatchesProofEntryList"
        )

    def test_registered_conditional_additive_filter_does_not_prove_field_read(self):
        user = get_user_model().objects.create_user(username="additive-registered-gap")
        entry = self.RegisteredEntry.create(name="visible", creator_id=user.id)
        self._assert_native_additive_scope(entry, user)
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        plan = get_read_permission_filter(self.RegisteredEntry, info)
        assert plan.decision == "conditional"
        assert plan.requires_instance_check is False
        result = apply_read_authorization(
            self.RegisteredEntry.all(), self.RegisteredEntry, info, source="list"
        )
        assert result.requires_instance_check is False
        assert [row.id for row in result.queryset] == [entry.id]
        assert self.RegisteredEntry.Permission(entry, user).can_read_instance() is False
        self._assert_graphql_counts_reject_hits_and_nonhits(
            user, "additiveRegisteredProofEntryList"
        )
