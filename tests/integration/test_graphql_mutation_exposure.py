"""Generated CRUD exposure does not change manager writes or custom workflows."""

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db.models import CharField
from graphql import GraphQLResolveInfo

from general_manager.api.graphql import GraphQL
from general_manager.api.mutation import graph_ql_mutation
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.permission.mutation_permission import MutationPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class GraphQLMutationExposureTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class NoCreateRecord(GeneralManager):
            __graphql_mutations__: ClassVar[set[str]] = {"update", "delete"}

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class NoUpdateRecord(GeneralManager):
            __graphql_mutations__: ClassVar[frozenset[str]] = frozenset(
                {"create", "delete"}
            )

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class NoDeleteRecord(GeneralManager):
            __graphql_mutations__: ClassVar[set[str]] = {"create", "update"}

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class NoCrudRecord(GeneralManager):
            __graphql_mutations__: ClassVar[set[str]] = set()

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(ManagerBasedPermission):
                __create__: ClassVar[list[str]] = ["isAdmin"]
                __update__: ClassVar[list[str]] = ["isAdmin"]
                __delete__: ClassVar[list[str]] = ["isAdmin"]

        class DefaultExposureRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                graphql_mutations = CharField(max_length=100, default="ordinary field")

        cls.NoCreateRecord = NoCreateRecord
        cls.NoUpdateRecord = NoUpdateRecord
        cls.NoDeleteRecord = NoDeleteRecord
        cls.NoCrudRecord = NoCrudRecord
        cls.DefaultRecord = DefaultExposureRecord
        cls.general_manager_classes = [
            NoCreateRecord,
            NoUpdateRecord,
            NoDeleteRecord,
            NoCrudRecord,
            DefaultExposureRecord,
        ]

        class AuthenticatedMutation(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAuthenticated"]

        @graph_ql_mutation(AuthenticatedMutation)
        def rename_exposure_record(
            info: GraphQLResolveInfo, record: NoUpdateRecord, name: str
        ) -> NoUpdateRecord:
            return record.update(name=name, creator_id=info.context.user.pk)

    def setUp(self):
        super().setUp()
        self.actor = get_user_model().objects.create_user(username="exposure-actor")
        self.admin = get_user_model().objects.create_user(
            username="exposure-admin", is_staff=True, is_superuser=True
        )
        self.client.force_login(self.actor)

    def test_schema_contains_only_enabled_crud_and_keeps_custom_mutations(self):
        mutation = GraphQL.get_schema().graphql_schema.mutation_type
        generated_names = {
            f"{operation}{manager.__name__}"
            for manager in self.general_manager_classes
            for operation in ("create", "update", "delete")
        }
        self.assertEqual(
            set(mutation.fields) & generated_names,
            {
                "updateNoCreateRecord",
                "deleteNoCreateRecord",
                "createNoUpdateRecord",
                "deleteNoUpdateRecord",
                "createNoDeleteRecord",
                "updateNoDeleteRecord",
                "createDefaultExposureRecord",
                "updateDefaultExposureRecord",
                "deleteDefaultExposureRecord",
            },
        )
        self.assertIn("renameExposureRecord", mutation.fields)

    def test_disabled_generated_mutations_are_rejected_before_writing(self):
        for manager, operation in (
            (self.NoCreateRecord, "create"),
            (self.NoUpdateRecord, "update"),
            (self.NoDeleteRecord, "delete"),
            (self.NoCrudRecord, "create"),
            (self.NoCrudRecord, "update"),
            (self.NoCrudRecord, "delete"),
        ):
            count_before = manager.all().count()
            record = manager.create(name="Unchanged", creator_id=self.admin.pk)
            field = f"{operation}{manager.__name__}"
            with self.subTest(field=field):
                arguments = {
                    "create": 'name: "Changed"',
                    "update": f'id: "{record.id}", name: "Changed"',
                    "delete": f'id: "{record.id}"',
                }[operation]
                response = self.query(
                    f"mutation {{ {field}({arguments}) {{ success }} }}"
                )
                self.assertResponseHasErrors(response)
                errors = response.json()["errors"]
                self.assertIn(f"Cannot query field '{field}'", errors[0]["message"])
                self.assertEqual(manager(id=record.id).name, "Unchanged")
                self.assertEqual(manager.all().count(), count_before + 1)

    def test_enabled_operations_and_unconfigured_defaults_still_execute(self):
        for manager, operations in (
            (self.NoCreateRecord, {"update", "delete"}),
            (self.NoUpdateRecord, {"create", "delete"}),
            (self.NoDeleteRecord, {"create", "update"}),
            (self.DefaultRecord, {"create", "update", "delete"}),
        ):
            with self.subTest(manager=manager.__name__):
                if "create" in operations:
                    response = self.query(
                        f'mutation {{ create{manager.__name__}(name: "Created") {{ success }} }}'
                    )
                    self.assertResponseNoErrors(response)
                    self.assertTrue(
                        response.json()["data"][f"create{manager.__name__}"]["success"]
                    )
                    record = manager.all().first()
                else:
                    record = manager.create(name="Created", creator_id=self.actor.pk)
                self.assertEqual(record.name, "Created")
                if "update" in operations:
                    response = self.query(
                        f'mutation {{ update{manager.__name__}(id: "{record.id}", name: "Updated") {{ success }} }}'
                    )
                    self.assertResponseNoErrors(response)
                    self.assertTrue(
                        response.json()["data"][f"update{manager.__name__}"]["success"]
                    )
                    self.assertEqual(manager(id=record.id).name, "Updated")
                if "delete" in operations:
                    response = self.query(
                        f'mutation {{ delete{manager.__name__}(id: "{record.id}") {{ success }} }}'
                    )
                    self.assertResponseNoErrors(response)
                    self.assertTrue(
                        response.json()["data"][f"delete{manager.__name__}"]["success"]
                    )
                    self.assertEqual(manager.all().count(), 0)

    def test_python_writes_still_work_and_enforce_manager_permissions(self):
        with self.assertRaises(PermissionError):
            self.NoCrudRecord.create(name="Denied", creator_id=self.actor.pk)
        self.assertEqual(self.NoCrudRecord.all().count(), 0)

        record = self.NoCrudRecord.create(name="Created", creator_id=self.admin.pk)
        with self.assertRaises(PermissionError):
            record.update(name="Denied", creator_id=self.actor.pk)
        self.assertEqual(self.NoCrudRecord(id=record.id).name, "Created")

        record = record.update(name="Updated", creator_id=self.admin.pk)
        self.assertEqual(self.NoCrudRecord(id=record.id).name, "Updated")
        with self.assertRaises(PermissionError):
            record.delete(creator_id=self.actor.pk)
        self.assertEqual(self.NoCrudRecord.all().count(), 1)

        record.delete(creator_id=self.admin.pk)
        self.assertEqual(self.NoCrudRecord.all().count(), 0)

    def test_read_queries_remain_when_all_generated_writes_are_hidden(self):
        record = self.NoCrudRecord.create(name="Readable", creator_id=self.admin.pk)

        response = self.query(
            f'{{ noCrudRecord(id: "{record.id}") {{ id name }} '
            "noCrudRecordList { items { id name } } }"
        )

        self.assertResponseNoErrors(response)
        expected = {"id": str(record.id), "name": "Readable"}
        self.assertEqual(
            response.json()["data"],
            {"noCrudRecord": expected, "noCrudRecordList": {"items": [expected]}},
        )

    def test_public_mutation_named_field_keeps_default_exposure_and_remains_readable(
        self,
    ):
        record = self.DefaultRecord.create(name="Default", creator_id=self.actor.pk)

        response = self.query(
            f'{{ defaultExposureRecord(id: "{record.id}") {{ graphqlMutations }} }}'
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["defaultExposureRecord"],
            {"graphqlMutations": "ordinary field"},
        )

    def test_custom_update_remains_callable_and_keeps_its_permission_check(self):
        record = self.NoUpdateRecord.create(name="Original", creator_id=self.actor.pk)
        mutation = (
            f'mutation {{ renameExposureRecord(record: "{record.id}", name: "Renamed") '
            "{ success noUpdateRecord { id name } } }"
        )

        response = self.query(mutation)
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["renameExposureRecord"],
            {
                "success": True,
                "noUpdateRecord": {"id": str(record.id), "name": "Renamed"},
            },
        )
        self.assertEqual(self.NoUpdateRecord(id=record.id).name, "Renamed")

        self.client.logout()
        response = self.query(mutation.replace("Renamed", "Denied"))
        self.assertResponseHasErrors(response)
        self.assertEqual(
            response.json()["errors"][0]["extensions"]["code"], "PERMISSION_DENIED"
        )
        self.assertEqual(self.NoUpdateRecord(id=record.id).name, "Renamed")


class GraphQLGlobalMutationDefaultTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        from django.test import override_settings

        cls.settings_override = override_settings(
            GENERAL_MANAGER={"GRAPHQL_MUTATIONS_ENABLED": False}
        )
        cls.settings_override.enable()
        cls.addClassCleanup(cls.settings_override.disable)

        class GloballyHiddenRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class OptedInRecord(GeneralManager):
            __graphql_mutations__: ClassVar[set[str]] = {"create", "update", "delete"}

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        cls.HiddenRecord = GloballyHiddenRecord
        cls.general_manager_classes = [GloballyHiddenRecord, OptedInRecord]
        super().setUpClass()

    def test_global_false_hides_only_undeclared_generated_mutations(self):
        schema = GraphQL.get_schema().graphql_schema
        for operation in ("create", "update", "delete"):
            self.assertNotIn(
                f"{operation}GloballyHiddenRecord", schema.mutation_type.fields
            )
            self.assertIn(f"{operation}OptedInRecord", schema.mutation_type.fields)
        self.assertTrue(schema.query_type.fields)

    def test_global_false_does_not_disable_python_writes(self):
        actor = get_user_model().objects.create_user(username="global-default-actor")
        record = self.HiddenRecord.create(name="before", creator_id=actor.pk)
        updated = record.update(name="after", creator_id=actor.pk)
        self.assertEqual(updated.name, "after")
        updated.delete(creator_id=actor.pk)
