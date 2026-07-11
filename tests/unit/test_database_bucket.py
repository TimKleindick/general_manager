# type: ignore

from datetime import datetime
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.auth.models import Group
from django.db.models import Prefetch, functions
from django.db.models.query import QuerySet
from django.test import TestCase

from general_manager.bucket.database_bucket import (
    DatabaseBucket,
    DuplicateDatabaseBucketSnapshotError,
    MAX_RUN_SCOPED_BUCKET_RESULT_ROWS,
    QuerysetFilteringError,
    _RUN_SCOPED_BUCKET_RESULT_TOO_LARGE,
    _restore_database_bucket_from_primary_keys,
)
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import CalculationRunContext
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.base_interface import InterfaceBase
from general_manager.api.property import graph_ql_property


# Dummy interface class to satisfy GeneralManager requirements
class DummyInterface(InterfaceBase):
    def __init__(self, pk, **_kwargs):
        # Simulate identification attribute as dict with 'id'
        """
        Initializes the manager with a primary key and sets the identification attribute.
        """
        self.identification = {"id": pk}

    @classmethod
    def create(cls, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that creation is not supported for this interface.
        """
        raise NotImplementedError

    def update(self, *args, **kwargs):
        """
        Indicates that updating is not supported for this interface.

        Raises:
            NotImplementedError: Always raised to signal that update operations are unsupported.
        """
        raise NotImplementedError

    def delete(self, *args, **kwargs):
        """
        Indicates that deletion is not supported for this interface.

        Raises:
            NotImplementedError: Always raised to signal that deletion is unsupported.
        """
        raise NotImplementedError

    def get_data(self, search_date=None):
        """
        Raises NotImplementedError to indicate data retrieval is not implemented for this interface.
        """
        raise NotImplementedError

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict]:  # type: ignore
        """
        Returns an empty dictionary representing attribute types for the class.
        """
        return {}

    @classmethod
    def get_attributes(cls) -> dict[str, dict]:
        """
        Returns an empty dictionary representing the attributes for the class.

        This method can be overridden to provide attribute definitions for the class.
        """
        return {}

    @classmethod
    def filter(cls, **kwargs):  # type: ignore
        """
        Returns a DatabaseBucket containing UserManager instances for users matching the given filter criteria.

        Args:
            **kwargs: Field lookups to filter User objects.

        Returns:
            A DatabaseBucket wrapping UserManager instances for the filtered users.
        """
        kwargs.pop("search_date", None)
        return DatabaseBucket(User.objects.filter(**kwargs), UserManager)

    @classmethod
    def exclude(cls, **_kwargs):  # type: ignore
        """
        Returns an empty list, indicating no objects are excluded.

        This method is a placeholder for exclusion logic in the interface.
        """
        return []

    @classmethod
    def get_field_type(cls, _field_name: str) -> type:
        """
        Returns the type associated with the specified field name.

        Always returns `str` for any field.
        """
        return str

    @classmethod
    def handle_interface(cls):
        """
        Provides pre- and post-creation hooks for class customization.

        Returns:
            A tuple of two functions:
                - pre_creation: Modifies class attributes before class creation by adding a 'marker'.
                - post_creation: Sets a 'post_mark' flag on the newly created class.
        """

        def pre_creation(_name, attrs, _interface):
            """
            Adds a marker attribute to the class attributes before creation.

            Args:
                name: The name of the class being created.
                attrs: The dictionary of class attributes to be modified.
                interface: The interface associated with the class.

            Returns:
                A tuple containing the updated attributes, the class itself, and None.
            """
            attrs["marker"] = "initialized_by_dummy"
            return attrs, cls, None

        def post_creation(new_cls, _interface_cls, _model):
            """
            Sets a flag on the newly created class after its creation.

            Args:
                new_cls: The newly created class instance.
                interface_cls: The interface class used for creation.
                model: The model associated with the class.
            """
            new_cls.post_mark = True

        return pre_creation, post_creation


class TrustedDummyInterface(DummyInterface):
    @classmethod
    def _from_trusted_orm_instance(cls, instance, *, search_date=None):
        interface = cls.__new__(cls)
        interface.identification = {"id": instance.pk}
        interface.pk = instance.pk
        interface._search_date = search_date
        interface._instance = instance
        return interface


class UserManager(GeneralManager):
    """
    Simple GeneralManager subclass for wrapping User PKs.
    """

    def __init__(self, pk, **kwargs):
        """
        Initializes the UserManager with the given primary key.
        """
        super().__init__(pk, **kwargs)

    @graph_ql_property(
        filterable=True, sortable=True, query_annotation=functions.Length("username")
    )
    def username_length(self) -> int:
        return len(User.objects.get(pk=self.identification["id"]).username)

    @graph_ql_property(filterable=True, sortable=True)
    def negative_length(self) -> int:
        return -len(User.objects.get(pk=self.identification["id"]).username)


class AnotherManager(GeneralManager):
    """
    Another GeneralManager subclass to test type mismatches.
    """

    def __init__(self, pk, **kwargs):
        """
        Initializes the UserManager with the given primary key.
        """
        super().__init__(pk, **kwargs)


class SearchDateManager(GeneralManager):
    """
    GeneralManager subclass capturing search_date for testing.
    """

    def __init__(self, pk, **kwargs):
        self.received_search_date = kwargs.get("search_date")
        super().__init__(pk, **kwargs)


class TrustedUserManager(GeneralManager):
    pass


class GroupBackedTrustedInterface(TrustedDummyInterface):
    _model = Group


class GroupBackedTrustedUserManager(GeneralManager):
    pass


class InitTrackingTrustedManager(GeneralManager):
    def __init__(self, pk, **kwargs):
        self.initialized_by_constructor = True
        self.received_search_date = kwargs.get("search_date")
        super().__init__(pk, **kwargs)


class CustomTrackingTrustedManager(GeneralManager):
    tracking_calls: ClassVar[list[dict[str, object]]] = []

    @classmethod
    def _track_identification_dependency_active(cls, identification):
        cls.tracking_calls.append(dict(identification))
        DependencyTracker.track(cls.__name__, "all", "")


class DatabaseBucketTestCase(TestCase):
    def setUp(self):
        """
        Sets up test data and environment for DatabaseBucket tests.

        Initializes DummyInterface for manager classes, creates test User instances, and constructs a DatabaseBucket containing all users with UserManager.
        """
        UserManager.Interface = DummyInterface  # Set the interface for UserManager
        AnotherManager.Interface = DummyInterface
        SearchDateManager.Interface = DummyInterface
        TrustedUserManager.Interface = TrustedDummyInterface
        GroupBackedTrustedUserManager.Interface = GroupBackedTrustedInterface
        InitTrackingTrustedManager.Interface = TrustedDummyInterface
        CustomTrackingTrustedManager.Interface = TrustedDummyInterface
        CustomTrackingTrustedManager.tracking_calls = []
        DummyInterface._parent_class = UserManager
        TrustedDummyInterface._parent_class = TrustedUserManager
        # Create some test users
        self.u1 = User.objects.create(username="alice")
        self.u2 = User.objects.create(username="bob")
        self.u3 = User.objects.create(username="carol")
        # Base bucket with all users
        self.bucket = DatabaseBucket(User.objects.all(), UserManager)

    def test_iter_and_len_and_count(self):
        # __iter__ yields UserManager instances
        """
        Tests that iterating over the bucket yields UserManager instances with correct IDs, and that length and count methods return the expected number of items.
        """
        ids = [mgr.identification["id"] for mgr in self.bucket]
        self.assertListEqual(
            ids,
            [self.u1.id, self.u2.id, self.u3.id],
        )
        # __len__ and count()
        self.assertEqual(len(self.bucket), 3)
        self.assertEqual(self.bucket.count(), 3)

    def test_list_uses_one_query_outside_run_context(self):
        bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with self.assertNumQueries(1):
            managers = list(bucket)

        self.assertEqual(
            [manager.identification["id"] for manager in managers],
            [self.u1.id, self.u2.id, self.u3.id],
        )

    def test_build_manager_dispatches_model_instances_and_primary_keys(self):
        with (
            patch.object(
                self.bucket,
                "_build_manager_from_instance",
                wraps=self.bucket._build_manager_from_instance,
            ) as from_instance,
            patch.object(
                self.bucket,
                "_build_manager_from_primary_key",
                wraps=self.bucket._build_manager_from_primary_key,
            ) as from_primary_key,
        ):
            self.assertEqual(
                self.bucket._build_manager(self.u1).identification["id"],
                self.u1.id,
            )
            self.assertEqual(
                self.bucket._build_manager(self.u2.pk).identification["id"],
                self.u2.id,
            )

        from_instance.assert_called_once_with(self.u1)
        self.assertEqual(
            [call.args[0] for call in from_primary_key.call_args_list],
            [self.u1.pk, self.u2.pk],
        )

    def test_primary_key_snapshot_iteration_fallback_when_rows_are_absent(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )

        with CalculationRunContext() as context:
            signature = bucket._query_signature()
            context.set_orm_bucket_result(signature, (self.u1.id, self.u2.id))

            with patch.object(bucket, "_get_run_scoped_rows", return_value=None):
                self.assertEqual(
                    [manager.identification["id"] for manager in bucket],
                    [self.u1.id, self.u2.id],
                )

    def test_ordered_primary_key_snapshot_materializes_primary_keys(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            primary_keys = bucket._get_run_scoped_primary_keys()

        self.assertEqual(primary_keys, (self.u1.id, self.u2.id))

    def test_unordered_primary_key_snapshot_preserves_queryset_iteration_order(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]),
            UserManager,
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            primary_keys = bucket._get_run_scoped_primary_keys()

        self.assertEqual(set(primary_keys), {self.u1.id, self.u2.id})

        with CalculationRunContext() as context:
            context.set_orm_bucket_result(bucket._query_signature(), primary_keys)
            with patch.object(bucket._data, "values_list") as values_list:
                self.assertEqual(
                    bucket._get_run_scoped_primary_keys(),
                    primary_keys,
                )

        values_list.assert_not_called()

    def test_trusted_hydration_preserves_custom_manager_initializer(self):
        search_date = datetime(2024, 1, 1)
        bucket = DatabaseBucket(
            User.objects.filter(pk=self.u1.pk),
            InitTrackingTrustedManager,
            search_date=search_date,
        )

        manager = next(iter(bucket))

        self.assertTrue(manager.initialized_by_constructor)
        self.assertEqual(manager.received_search_date, search_date)
        self.assertEqual(manager.identification["id"], self.u1.pk)

    def test_prefetched_querysets_do_not_share_trusted_row_snapshots(self):
        allowed = Group.objects.create(name="allowed")
        blocked = Group.objects.create(name="blocked")
        self.u1.groups.add(allowed, blocked)

        allowed_bucket = DatabaseBucket(
            User.objects.filter(pk=self.u1.pk).prefetch_related(
                Prefetch(
                    "groups",
                    queryset=Group.objects.filter(pk=allowed.pk),
                    to_attr="filtered_groups",
                )
            ),
            TrustedUserManager,
        )
        blocked_bucket = DatabaseBucket(
            User.objects.filter(pk=self.u1.pk).prefetch_related(
                Prefetch(
                    "groups",
                    queryset=Group.objects.filter(pk=blocked.pk),
                    to_attr="filtered_groups",
                )
            ),
            TrustedUserManager,
        )

        with CalculationRunContext():
            allowed_manager = next(iter(allowed_bucket))
            blocked_manager = next(iter(blocked_bucket))

        self.assertEqual(
            [
                group.pk
                for group in allowed_manager._interface._instance.filtered_groups
            ],
            [allowed.pk],
        )
        self.assertEqual(
            [
                group.pk
                for group in blocked_manager._interface._instance.filtered_groups
            ],
            [blocked.pk],
        )

    def test_model_mismatch_falls_back_to_primary_key_hydration(self):
        bucket = DatabaseBucket(
            User.objects.filter(pk=self.u1.pk),
            GroupBackedTrustedUserManager,
        )

        manager = next(iter(bucket))

        self.assertEqual(manager.identification["id"], self.u1.pk)
        self.assertFalse(hasattr(manager._interface, "_instance"))

    def test_equivalent_bucket_queries_share_iter_sql_inside_run_context(self):
        first_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(
                [manager.identification["id"] for manager in first_bucket],
                [self.u1.id, self.u2.id],
            )
            self.assertEqual(
                [manager.identification["id"] for manager in second_bucket],
                [self.u1.id, self.u2.id],
            )

    def test_query_signature_reuses_compiled_sql_for_same_bucket(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )

        with patch.object(
            bucket._data.query,
            "sql_with_params",
            wraps=bucket._data.query.sql_with_params,
        ) as sql_with_params:
            first_signature = bucket._query_signature()
            second_signature = bucket._query_signature()

        self.assertEqual(first_signature, second_signature)
        sql_with_params.assert_called_once()

    def test_trusted_signature_payload_freezes_common_filter_values(self):
        frozen = DatabaseBucket._freeze_trusted_signature_payload(
            {
                "name__in": ["alice", "bob"],
                "is_active": True,
                "count": 2,
            }
        )

        self.assertEqual(
            frozen,
            (
                ("count", 2),
                ("is_active", True),
                ("name__in", ("alice", "bob")),
            ),
        )

    def test_unordered_bucket_queries_share_loaded_rows_inside_run_context(self):
        first_bucket = DatabaseBucket(User.objects.all(), UserManager)
        second_bucket = DatabaseBucket(User.objects.all(), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            first_ids = [manager.identification["id"] for manager in first_bucket]
            second_ids = [manager.identification["id"] for manager in second_bucket]

        self.assertEqual(second_ids, first_ids)
        self.assertEqual(sorted(first_ids), [self.u1.id, self.u2.id, self.u3.id])

    def test_cached_trusted_rows_reuse_built_managers_inside_run_context(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            TrustedUserManager,
        )

        with CalculationRunContext():
            first_managers = list(bucket)
            with patch.object(
                bucket,
                "_build_manager_from_instance",
                wraps=bucket._build_manager_from_instance,
            ) as build_manager:
                second_managers = list(bucket)

        build_manager.assert_not_called()
        self.assertEqual(
            [manager.identification["id"] for manager in first_managers],
            [self.u1.id, self.u2.id],
        )
        self.assertEqual(
            [manager.identification["id"] for manager in second_managers],
            [self.u1.id, self.u2.id],
        )
        self.assertIs(first_managers[0], second_managers[0])

    def test_cached_trusted_managers_replay_identification_dependencies_in_bulk(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            TrustedUserManager,
        )

        with CalculationRunContext():
            list(bucket)
            with (
                patch.object(
                    TrustedUserManager,
                    "_track_identification_dependency",
                    side_effect=AssertionError(
                        "cached manager dependencies should be replayed in bulk"
                    ),
                ),
                DependencyTracker() as dependencies,
            ):
                list(bucket)

        self.assertIn(
            (
                "TrustedUserManager",
                "identification",
                f'{{"id": {self.u1.id}}}',
            ),
            dependencies,
        )
        self.assertIn(
            (
                "TrustedUserManager",
                "identification",
                f'{{"id": {self.u2.id}}}',
            ),
            dependencies,
        )

    def test_cached_trusted_manager_reuse_preserves_custom_dependency_tracking(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            CustomTrackingTrustedManager,
        )

        with CalculationRunContext():
            list(bucket)
            with DependencyTracker() as dependencies:
                list(bucket)

        self.assertEqual(
            CustomTrackingTrustedManager.tracking_calls,
            [{"id": self.u1.id}, {"id": self.u2.id}],
        )
        self.assertIn(("CustomTrackingTrustedManager", "all", ""), dependencies)
        self.assertNotIn(
            (
                "CustomTrackingTrustedManager",
                "identification",
                f'{{"id": {self.u1.id}}}',
            ),
            dependencies,
        )

    def test_instance_tracking_helper_preserves_custom_dependency_tracking(self):
        manager = CustomTrackingTrustedManager(self.u1.id)
        CustomTrackingTrustedManager.tracking_calls = []

        with DependencyTracker() as dependencies:
            manager._track_own_identification_dependency_active()

        self.assertEqual(
            CustomTrackingTrustedManager.tracking_calls,
            [{"id": self.u1.id}],
        )
        self.assertEqual(dependencies, {("CustomTrackingTrustedManager", "all", "")})

    def test_equivalent_database_buckets_share_index_inside_run_context(self):
        """Share a bucket index for equivalent querysets in one run context."""
        first_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            first_index = first_bucket.index_by("identification")
            second_index = second_bucket.index_by("identification")

        self.assertIs(first_index, second_index)
        self.assertEqual(
            sorted(manager.identification["id"] for manager in first_index.values()),
            [self.u1.id, self.u2.id],
        )

    def test_repeated_equivalent_index_lookups_reduce_sql_work(self):
        """Avoid a second SQL query for equivalent index lookups in one run."""
        first_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(len(first_bucket.index_by("identification")), 2)
            self.assertEqual(len(second_bucket.index_by("identification")), 2)

    def test_different_ordering_does_not_share_iter_sql_inside_run_context(self):
        ascending = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
            sort_keys=("username",),
            sort_reverse=False,
        )
        descending = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("-username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
            sort_keys=("username",),
            sort_reverse=True,
        )

        with CalculationRunContext(), self.assertNumQueries(2):
            self.assertEqual(
                [manager.identification["id"] for manager in ascending],
                [self.u1.id, self.u2.id],
            )
            self.assertEqual(
                [manager.identification["id"] for manager in descending],
                [self.u2.id, self.u1.id],
            )

    def test_mixed_terminal_operations_reuse_materialized_bucket_snapshot(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(
                [manager.identification["id"] for manager in bucket],
                [self.u1.id, self.u2.id],
            )
            self.assertEqual(bucket.count(), 2)
            self.assertEqual(len(bucket), 2)
            self.assertEqual(bucket.first().identification["id"], self.u1.id)
            self.assertEqual(bucket.last().identification["id"], self.u2.id)
            self.assertEqual(bucket.get(pk=self.u1.pk).identification["id"], self.u1.id)
            self.assertEqual(bucket[1].identification["id"], self.u2.id)
            self.assertIn(self.u1, bucket)

    def test_tracking_unsorted_all_bucket_skips_empty_mapping_normalization(self):
        """Avoid serializing empty mappings that are not part of tracked output."""
        bucket = DatabaseBucket(User.objects.all(), UserManager)
        original_normalize = DatabaseBucket._normalize_dependency_mapping
        normalized_definitions = []

        def spy_normalize(definitions):
            normalized_definitions.append(definitions)
            return original_normalize(definitions)

        with (
            patch.object(
                DatabaseBucket,
                "_normalize_dependency_mapping",
                side_effect=spy_normalize,
            ),
            DependencyTracker() as dependencies,
        ):
            bucket._track_effective_dependencies()

        self.assertEqual(normalized_definitions, [])
        self.assertIn(("UserManager", "all", ""), dependencies)

    def test_tracking_sorted_all_bucket_keeps_empty_mappings_in_sort_payload(self):
        """Sorted buckets still publish empty filters/excludes for sort identity."""
        bucket = DatabaseBucket(
            User.objects.all(),
            UserManager,
            sort_keys=("username",),
            sort_reverse=True,
        )

        with DependencyTracker() as dependencies:
            bucket._track_effective_dependencies()

        expected_sort_identifier = serialize_dependency_identifier(
            {
                "__sort__username": {
                    "filters": {},
                    "excludes": {},
                    "reverse": True,
                }
            }
        )
        self.assertIn(("UserManager", "all", ""), dependencies)
        self.assertIn(
            ("UserManager", "filter", expected_sort_identifier),
            dependencies,
        )

    def test_filtered_count_keeps_scalar_cache_separate_from_iteration(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
            {"username__in": [["alice", "bob"]]},
        )

        with CalculationRunContext(), self.assertNumQueries(2):
            self.assertEqual(bucket.count(), 2)
            self.assertEqual(bucket.count(), 2)
            self.assertEqual(
                [manager.identification["id"] for manager in bucket],
                [self.u1.id, self.u2.id],
            )

    def test_equivalent_filtered_counts_reuse_scalar_count(self):
        first_bucket = DatabaseBucket(
            User.objects.filter(username__startswith="a"),
            UserManager,
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username__startswith="a"),
            UserManager,
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(first_bucket.count(), 1)
            self.assertEqual(second_bucket.count(), 1)

    def test_equivalent_first_reuses_row_inside_run_context(self):
        first_bucket = DatabaseBucket(
            User.objects.filter(username="alice").order_by("id"),
            UserManager,
            {"username": ["alice"]},
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username="alice").order_by("id"),
            UserManager,
            {"username": ["alice"]},
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(first_bucket.first().identification["id"], self.u1.id)
            self.assertEqual(second_bucket.first().identification["id"], self.u1.id)

    def test_equivalent_last_reuses_row_inside_run_context(self):
        first_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(first_bucket.last().identification["id"], self.u2.id)
            self.assertEqual(second_bucket.last().identification["id"], self.u2.id)

    def test_equivalent_last_reuses_empty_outcome_inside_run_context(self):
        first_bucket = DatabaseBucket(
            User.objects.filter(username="nobody"), UserManager
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(username="nobody"), UserManager
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertIsNone(first_bucket.last())
            self.assertIsNone(second_bucket.last())

    def test_equivalent_safe_get_reuses_row_inside_run_context(self):
        first_bucket = DatabaseBucket(User.objects.all(), UserManager)
        second_bucket = DatabaseBucket(User.objects.all(), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(
                first_bucket.get(pk=self.u2.pk).identification["id"], self.u2.id
            )
            self.assertEqual(
                second_bucket.get(id=self.u2.pk).identification["id"], self.u2.id
            )

    def test_equivalent_safe_get_reuses_missing_outcome_inside_run_context(self):
        first_bucket = DatabaseBucket(User.objects.all(), UserManager)
        second_bucket = DatabaseBucket(User.objects.all(), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            with self.assertRaises(User.DoesNotExist):
                first_bucket.get(pk=999)
            with self.assertRaises(User.DoesNotExist):
                second_bucket.get(id=999)

    def test_equivalent_scalar_indexes_reuse_rows_inside_run_context(self):
        first_bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)
        second_bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(first_bucket[1].identification["id"], self.u2.id)
            self.assertEqual(second_bucket[1].identification["id"], self.u2.id)

    def test_equivalent_scalar_indexes_reuse_out_of_range_outcome_inside_run_context(
        self,
    ):
        first_bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)
        second_bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            with self.assertRaises(IndexError):
                first_bucket[999]
            with self.assertRaises(IndexError):
                second_bucket[999]

    def test_equivalent_primary_key_membership_reuses_boolean_inside_run_context(self):
        first_bucket = DatabaseBucket(User.objects.all(), UserManager)
        second_bucket = DatabaseBucket(User.objects.all(), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertIn(self.u1, first_bucket)
            self.assertIn(UserManager(self.u1.pk), second_bucket)

    def test_equivalent_primary_key_membership_reuses_false_inside_run_context(self):
        first_bucket = DatabaseBucket(User.objects.all(), UserManager)
        second_bucket = DatabaseBucket(User.objects.all(), UserManager)
        missing = User(id=999)

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertNotIn(missing, first_bucket)
            self.assertNotIn(missing, second_bucket)

    def test_equivalent_safe_get_reuses_duplicate_outcome_inside_run_context(self):
        first_group = Group.objects.create(name="scalar-first-group")
        second_group = Group.objects.create(name="scalar-second-group")
        self.u1.groups.add(first_group, second_group)
        first_bucket = DatabaseBucket(
            User.objects.filter(groups__name__in=[first_group.name, second_group.name]),
            UserManager,
        )
        second_bucket = DatabaseBucket(
            User.objects.filter(groups__name__in=[first_group.name, second_group.name]),
            UserManager,
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            with self.assertRaises(User.MultipleObjectsReturned):
                first_bucket.get(pk=self.u1.pk)
            with self.assertRaises(User.MultipleObjectsReturned):
                second_bucket.get(id=self.u1.pk)

    def test_primary_key_snapshot_terminal_operations_without_row_snapshot(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )

        with CalculationRunContext() as context:
            signature = bucket._query_signature()
            context.set_orm_bucket_result(signature, (self.u1.id, self.u2.id))

            self.assertEqual(bucket.first().identification["id"], self.u1.id)
            self.assertEqual(bucket.last().identification["id"], self.u2.id)
            self.assertEqual(bucket.get(pk=self.u1.pk).identification["id"], self.u1.id)
            self.assertEqual(bucket[1].identification["id"], self.u2.id)

            with self.assertRaises(ValueError):
                bucket[-1]

    def test_empty_primary_key_snapshot_first_and_last_return_none(self):
        bucket = DatabaseBucket(User.objects.filter(username="nobody"), UserManager)

        with CalculationRunContext() as context:
            context.set_orm_bucket_result(bucket._query_signature(), ())

            self.assertIsNone(bucket.first())
            self.assertIsNone(bucket.last())

    def test_primary_key_snapshot_get_missing_and_duplicate_raise(self):
        bucket = DatabaseBucket(User.objects.filter(username="alice"), UserManager)

        with CalculationRunContext() as context:
            signature = bucket._query_signature()
            context.set_orm_bucket_result(signature, (self.u1.id,))
            with self.assertRaises(User.DoesNotExist):
                bucket.get(pk=self.u2.pk)

            context.set_orm_bucket_result(signature, (self.u1.id, self.u1.id))
            with self.assertRaises(User.MultipleObjectsReturned):
                bucket.get(pk=self.u1.pk)

    def test_peek_run_scoped_rows_returns_none_without_cached_rows(self):
        bucket = DatabaseBucket(User.objects.filter(username="alice"), UserManager)

        self.assertIsNone(bucket._peek_run_scoped_rows())
        with CalculationRunContext():
            self.assertIsNone(bucket._peek_run_scoped_rows())
            with patch.object(bucket, "_query_signature", return_value=None):
                self.assertIsNone(bucket._peek_run_scoped_rows())

    def test_run_scoped_over_limit_sentinels_skip_repeated_orm_work(self):
        bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with CalculationRunContext() as context:
            signature = bucket._query_signature()
            context.set_orm_bucket_result(
                signature, _RUN_SCOPED_BUCKET_RESULT_TOO_LARGE
            )

            with self.assertNumQueries(0):
                self.assertIsNone(bucket._get_run_scoped_primary_keys())
                self.assertIsNone(bucket._get_run_scoped_rows())
                self.assertIsNone(bucket._peek_run_scoped_primary_keys())
                self.assertIsNone(bucket._peek_run_scoped_rows())

            context.set_orm_bucket_rows(signature, _RUN_SCOPED_BUCKET_RESULT_TOO_LARGE)
            with self.assertNumQueries(0):
                self.assertIsNone(bucket._peek_run_scoped_rows())

    def test_primary_key_snapshot_records_over_limit_sentinel(self):
        bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with (
            patch(
                "general_manager.bucket.database_bucket.MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
                1,
            ),
            CalculationRunContext() as context,
        ):
            self.assertIsNone(bucket._get_run_scoped_primary_keys())
            self.assertIs(
                context.get_orm_bucket_result(bucket._query_signature()),
                _RUN_SCOPED_BUCKET_RESULT_TOO_LARGE,
            )

    def test_contains_does_not_materialize_uncached_bucket(self):
        bucket = DatabaseBucket(User.objects.all(), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertIn(self.u1, bucket)

    def test_contains_returns_false_for_unsaved_model_without_query(self):
        unsaved = User(username="unsaved")

        with self.assertNumQueries(0):
            self.assertNotIn(unsaved, self.bucket)

    def test_bucket_result_cache_hit_still_tracks_dependencies(self):
        """Replay queryset dependencies when a database bucket result is cached."""
        bucket = DatabaseBucket(
            User.objects.filter(username="alice"),
            UserManager,
            {"username": ["alice"]},
        )

        with CalculationRunContext():
            list(bucket)
            with DependencyTracker() as dependencies:
                list(bucket)

        self.assertIn(
            (
                "UserManager",
                "filter",
                '{"username": "alice"}',
            ),
            dependencies,
        )

    def test_database_bucket_index_hit_replays_source_dependencies(self):
        """Replay queryset dependencies when a database bucket index is cached."""
        bucket = DatabaseBucket(
            User.objects.filter(username="alice"),
            UserManager,
            {"username": ["alice"]},
        )

        with CalculationRunContext():
            bucket.index_by("identification")
            with DependencyTracker() as dependencies:
                bucket.index_by("identification")

        self.assertIn(
            (
                "UserManager",
                "filter",
                '{"username": "alice"}',
            ),
            dependencies,
        )

    def test_large_bucket_result_bypasses_run_scoped_materialization(self):
        users = [
            User(username=f"user-{index:04d}")
            for index in range(MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1)
        ]
        User.objects.bulk_create(users)
        bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(bucket.count(), MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 4)
            self.assertEqual(bucket.count(), MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 4)

    def test_large_filtered_count_uses_one_scalar_query_and_reuses_result(self):
        users = [
            User(username=f"count-user-{index:04d}")
            for index in range(MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1)
        ]
        User.objects.bulk_create(users)
        bucket = DatabaseBucket(
            User.objects.filter(username__startswith="count-user-"),
            UserManager,
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(bucket.count(), MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1)
            self.assertEqual(bucket.count(), MAX_RUN_SCOPED_BUCKET_RESULT_ROWS + 1)

    def test_over_limit_iteration_reuses_fallback_probe_state(self):
        first_bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)
        second_bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with (
            patch(
                "general_manager.bucket.database_bucket.MAX_RUN_SCOPED_BUCKET_RESULT_ROWS",
                1,
            ),
            CalculationRunContext(),
            self.assertNumQueries(3),
        ):
            self.assertEqual(
                [manager.identification["id"] for manager in first_bucket],
                [self.u1.id, self.u2.id, self.u3.id],
            )
            self.assertEqual(
                [manager.identification["id"] for manager in second_bucket],
                [self.u1.id, self.u2.id, self.u3.id],
            )

    def test_uncacheable_queryset_shapes_bypass_run_scoped_iteration_reuse(self):
        cases = [
            (
                DatabaseBucket(User.objects.select_for_update(), UserManager),
                DatabaseBucket(User.objects.select_for_update(), UserManager),
            ),
            (
                DatabaseBucket(User.objects.distinct(), UserManager),
                DatabaseBucket(User.objects.distinct(), UserManager),
            ),
            (
                DatabaseBucket(
                    User.objects.filter(username="alice").union(
                        User.objects.filter(username="bob")
                    ),
                    UserManager,
                ),
                DatabaseBucket(
                    User.objects.filter(username="alice").union(
                        User.objects.filter(username="bob")
                    ),
                    UserManager,
                ),
            ),
        ]

        for first_bucket, second_bucket in cases:
            with self.subTest(query=first_bucket._data.query):
                with CalculationRunContext(), self.assertNumQueries(2):
                    first_count = sum(1 for _manager in first_bucket)
                    second_count = sum(1 for _manager in second_bucket)
                    self.assertEqual(first_count, second_count)

    def test_non_query_queryset_shape_bypasses_run_scoped_iteration_reuse(self):
        first_bucket = DatabaseBucket(User.objects.all(), UserManager)
        second_bucket = DatabaseBucket(User.objects.all(), UserManager)

        with (
            patch("general_manager.bucket.database_bucket.Query", str),
            CalculationRunContext(),
            self.assertNumQueries(2),
        ):
            first_count = sum(1 for _manager in first_bucket)
            second_count = sum(1 for _manager in second_bucket)
            self.assertEqual(first_count, second_count)

    def test_sql_signature_errors_bypass_run_scoped_iteration_reuse(self):
        bucket = DatabaseBucket(User.objects.filter(username="alice"), UserManager)

        with patch.object(
            bucket._data.query, "sql_with_params", side_effect=ValueError
        ):
            with CalculationRunContext(), self.assertNumQueries(1):
                self.assertEqual(
                    [manager.identification["id"] for manager in bucket],
                    [self.u1.id],
                )

    def test_uncacheable_bucket_count_uses_database_path(self):
        bucket = DatabaseBucket(
            User.objects.all(), UserManager, run_scoped_cacheable=False
        )

        with CalculationRunContext(), self.assertNumQueries(1):
            self.assertEqual(bucket.count(), 3)

    def test_union_bucket_bypasses_run_scoped_result_reuse(self):
        first = DatabaseBucket(User.objects.filter(username="alice"), UserManager)
        second = DatabaseBucket(User.objects.filter(username="bob"), UserManager)
        first_union = first | second
        second_union = DatabaseBucket(
            User.objects.filter(username="alice"), UserManager
        ) | DatabaseBucket(User.objects.filter(username="bob"), UserManager)

        with CalculationRunContext(), self.assertNumQueries(2):
            self.assertEqual(
                sorted(manager.identification["id"] for manager in first_union),
                sorted([self.u1.id, self.u2.id]),
            )
            self.assertEqual(
                sorted(manager.identification["id"] for manager in second_union),
                sorted([self.u1.id, self.u2.id]),
            )

    def test_empty_snapshot_first_and_last_return_none_without_queries(self):
        bucket = DatabaseBucket(User.objects.filter(username="nobody"), UserManager)

        with CalculationRunContext():
            self.assertEqual(list(bucket), [])
            with self.assertNumQueries(0):
                self.assertIsNone(bucket.first())
                self.assertIsNone(bucket.last())

    def test_snapshot_get_uses_id_lookup_without_query(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            UserManager,
        )

        with CalculationRunContext():
            list(bucket)
            with self.assertNumQueries(0):
                manager = bucket.get(id=self.u2.id)

        self.assertEqual(manager.identification["id"], self.u2.id)

    def test_snapshot_get_missing_primary_key_raises_without_query(self):
        bucket = DatabaseBucket(User.objects.filter(username="alice"), UserManager)

        with CalculationRunContext():
            list(bucket)
            with self.assertNumQueries(0), self.assertRaises(User.DoesNotExist):
                bucket.get(pk=999)

    def test_snapshot_get_duplicate_primary_key_raises_without_query(self):
        first_group = Group.objects.create(name="first-group")
        second_group = Group.objects.create(name="second-group")
        self.u1.groups.add(first_group, second_group)
        bucket = DatabaseBucket(
            User.objects.filter(groups__name__in=[first_group.name, second_group.name]),
            UserManager,
        )

        with CalculationRunContext():
            self.assertEqual(
                [manager.identification["id"] for manager in bucket],
                [self.u1.id, self.u1.id],
            )
            with (
                self.assertNumQueries(0),
                self.assertRaises(User.MultipleObjectsReturned),
            ):
                bucket.get(pk=self.u1.pk)

    def test_snapshot_get_falls_back_for_non_primary_key_lookup(self):
        bucket = DatabaseBucket(User.objects.filter(username="alice"), UserManager)

        with CalculationRunContext():
            list(bucket)
            with self.assertNumQueries(1):
                manager = bucket.get(username="alice")

        self.assertEqual(manager.identification["id"], self.u1.id)

    def test_snapshot_get_falls_back_for_ambiguous_lookup_shape(self):
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]),
            UserManager,
        )

        with CalculationRunContext():
            list(bucket)
            with (
                self.assertNumQueries(1),
                self.assertRaises(User.MultipleObjectsReturned),
            ):
                bucket.get()

    def test_first_and_last(self):
        # first() returns the first manager
        """
        Tests that the first() and last() methods of DatabaseBucket return the correct manager instances or None for empty buckets.
        """
        first_mgr = self.bucket.first()
        self.assertIsInstance(first_mgr, UserManager)
        self.assertEqual(first_mgr.identification["id"], self.u1.id)
        # last() returns the last manager
        last_mgr = self.bucket.last()
        self.assertIsInstance(last_mgr, UserManager)
        self.assertEqual(last_mgr.identification["id"], self.u3.id)
        # on empty bucket
        empty = DatabaseBucket(User.objects.none(), UserManager)
        self.assertIsNone(empty.first())
        self.assertIsNone(empty.last())

    def test_search_date_is_passed_to_manager(self):
        """
        Tests that search_date is propagated to manager instances.
        """
        search_date = datetime(2024, 1, 1)
        bucket = DatabaseBucket(
            User.objects.all(),
            SearchDateManager,
            search_date=search_date,
        )
        first_mgr = bucket.first()
        self.assertIsInstance(first_mgr, SearchDateManager)
        self.assertEqual(first_mgr.received_search_date, search_date)

    def test_get(self):
        """
        Tests that the `get` method returns the correct manager for an existing user and raises `User.DoesNotExist` when the user does not exist.
        """
        mgr = self.bucket.get(username="bob")
        self.assertIsInstance(mgr, UserManager)
        self.assertEqual(mgr.identification["id"], self.u2.id)
        # get non-existing should raise
        with self.assertRaises(User.DoesNotExist):
            self.bucket.get(username="doesnotexist")

    def test_getitem(self):
        # index
        """
        Tests indexing and slicing behavior of the DatabaseBucket.

        Verifies that indexing returns the correct manager instance for a user and that slicing returns a DatabaseBucket containing the expected subset of users.
        """
        mgr0 = self.bucket[0]
        self.assertIsInstance(mgr0, UserManager)
        self.assertEqual(mgr0.identification["id"], self.u1.id)
        mgr2 = self.bucket[2]
        self.assertEqual(mgr2.identification["id"], self.u3.id)
        # slice
        subbucket = self.bucket[:2]
        self.assertIsInstance(subbucket, DatabaseBucket)
        self.assertEqual(len(subbucket), 2)
        ids = [mgr.identification["id"] for mgr in subbucket]
        self.assertListEqual(ids, [self.u1.id, self.u2.id])

    def test_all(self):
        """
        Tests that the all() method returns a DatabaseBucket containing all users.
        """
        all_bucket = self.bucket.all()
        self.assertIsInstance(all_bucket, DatabaseBucket)
        self.assertEqual(len(all_bucket), 3)

    def test_filter_and_exclude(self):
        # filter
        """
        Tests the filter and exclude methods of DatabaseBucket.

        Verifies that filtering returns a bucket with only matching users and merges filter definitions, while excluding removes specified users and merges exclude definitions.
        """
        alice_bucket = self.bucket.filter(username="alice")
        self.assertIsInstance(alice_bucket, DatabaseBucket)
        self.assertEqual(len(alice_bucket), 1)
        self.assertEqual(alice_bucket.first().identification["id"], self.u1.id)
        # filter definitions merged
        self.assertIn("username", alice_bucket.filters)
        self.assertListEqual(alice_bucket.filters["username"], ["alice"])
        # exclude
        no_bob = self.bucket.exclude(username="bob")
        self.assertEqual(len(no_bob), 2)
        self.assertNotIn(self.u2, no_bob._data)
        # exclude definitions merged
        self.assertIn("username", no_bob.excludes)
        self.assertListEqual(no_bob.excludes["username"], ["bob"])

    def test_filter_and_exclude_definitions_do_not_share_nested_lists(self):
        """
        Cloned buckets should not share nested filter/exclude definition lists.
        """
        filtered = self.bucket.filter(username="alice")
        sibling_filtered = filtered.all()
        filtered.filters["username"].append("carol")
        self.assertListEqual(sibling_filtered.filters["username"], ["alice"])

        excluded = self.bucket.exclude(username="bob")
        sibling_excluded = excluded.all()
        excluded.excludes["username"].append("carol")
        self.assertListEqual(sibling_excluded.excludes["username"], ["bob"])

    def test_reduce_stores_primary_keys_instead_of_queryset(self):
        """
        Cache serialization should store a stable identity snapshot, not a live QuerySet.
        """
        search_date = datetime(2024, 1, 1)
        bucket = DatabaseBucket(
            User.objects.filter(username__in=["alice", "bob"]).order_by("username"),
            SearchDateManager,
            {"username__in": [["alice", "bob"]]},
            {"is_staff": [True]},
            search_date=search_date,
            sort_keys=("username",),
            sort_reverse=True,
        )
        reduced = bucket.__reduce__()
        restore_func, args = reduced

        self.assertEqual(restore_func, _restore_database_bucket_from_primary_keys)
        self.assertEqual(args[0], User)
        self.assertEqual(args[2], (self.u1.pk, self.u2.pk))
        self.assertEqual(args[3], bucket.filters)
        self.assertEqual(args[4], bucket.excludes)
        self.assertEqual(args[5], bucket._data.db)
        self.assertEqual(args[6], bucket._search_date)
        self.assertEqual(args[7], bucket._sort_keys)
        self.assertEqual(args[8], bucket._sort_reverse)
        self.assertFalse(any(isinstance(arg, QuerySet) for arg in args))

        restored = restore_func(*args)
        self.assertEqual(
            [manager.identification["id"] for manager in restored],
            [self.u1.pk, self.u2.pk],
        )
        self.assertEqual(restored.filters, bucket.filters)
        self.assertEqual(restored.excludes, bucket.excludes)
        self.assertEqual(restored._search_date, bucket._search_date)
        self.assertEqual(restored._sort_keys, bucket._sort_keys)
        self.assertEqual(restored._sort_reverse, bucket._sort_reverse)

    def test_restore_rejects_duplicate_primary_key_snapshots(self):
        """
        DatabaseBucket snapshots cannot silently collapse duplicate primary keys.
        """
        with self.assertRaises(DuplicateDatabaseBucketSnapshotError):
            _restore_database_bucket_from_primary_keys(
                User,
                UserManager,
                (self.u1.pk, self.u1.pk),
                {},
                {},
                "default",
                None,
                None,
                False,
            )

    def test_or_union_with_bucket(self):
        # split buckets
        """
        Tests that the union of two DatabaseBuckets returns a new bucket containing unique manager instances from both buckets.
        """
        b1 = self.bucket.filter(username="alice")
        b2 = self.bucket.filter(username="carol")
        union = b1 | b2
        self.assertIsInstance(union, DatabaseBucket)
        self.assertEqual(len(union), 2)
        ids = sorted([mgr.identification["id"] for mgr in union])
        self.assertListEqual(ids, sorted([self.u1.id, self.u3.id]))

    def test_or_union_preserves_dependency_filter_metadata(self):
        b1 = self.bucket.filter(username="alice").exclude(is_staff=True)
        b2 = self.bucket.filter(username="carol").exclude(is_staff=False)

        union = b1 | b2

        self.assertEqual(union.filters, {"username": ["alice", "carol"]})
        self.assertEqual(union.excludes, {"is_staff": [True, False]})

    def test_or_with_manager(self):
        """
        Tests that the union of a DatabaseBucket and a manager instance returns a bucket containing both items.
        """
        b1 = self.bucket.filter(username="alice")
        mgr_bob = UserManager(self.u2.id)
        union = b1 | mgr_bob
        self.assertEqual(len(union), 2)
        ids = sorted([mgr.identification["id"] for mgr in union])
        self.assertListEqual(ids, sorted([self.u1.id, self.u2.id]))

    def test_or_errors(self):
        # incompatible type
        """
        Tests that union operations with incompatible types or different manager classes raise TypeError.
        """
        with self.assertRaises(TypeError):
            _ = self.bucket | 123
        # different manager class
        b_other = DatabaseBucket(User.objects.all(), AnotherManager)
        with self.assertRaises(TypeError):
            _ = self.bucket | b_other

    def test_contains(self):
        # model instance
        """
        Tests membership checks for model and manager instances in the DatabaseBucket.

        Verifies that both model instances and their corresponding manager instances are recognized as members of the bucket, and that non-existent users are not considered members.
        """
        self.assertIn(self.u1, self.bucket)
        # manager instance
        mgr2 = UserManager(self.u2.id)
        self.assertIn(mgr2, self.bucket)
        # not in
        fake = User(id=999)
        self.assertNotIn(fake, self.bucket)

    def test_contains_uses_targeted_exists_lookup(self):
        mgr = UserManager(self.u1.id)

        with patch.object(
            self.bucket._data,
            "values_list",
            side_effect=AssertionError("values_list should not be used"),
        ):
            self.assertIn(mgr, self.bucket)
            self.assertIn(self.u1, self.bucket)

    def test_sort(self):
        # default ordering by username asc
        """
        Tests that the sort method orders the bucket by username in ascending and descending order.

        Verifies that sorting by username returns all original members in sorted order, and that reverse sorting places the user with the highest username first.
        """
        sorted_bucket = self.bucket.sort("username")
        ordered_ids = [mgr.identification["id"] for mgr in sorted_bucket]
        # ensure same members
        self.assertListEqual(
            ordered_ids,
            [self.u1.id, self.u2.id, self.u3.id],  # alice, bob, carol
        )

        # reverse ordering
        rev = self.bucket.sort("username", reverse=True)
        # highest username first
        self.assertEqual(rev.first().identification["id"], self.u3.id)

    def test_property_filter_and_sort(self):
        bucket = self.bucket.filter(username_length__gte=4)
        self.assertEqual(len(bucket), 2)
        sorted_bucket = bucket.sort("negative_length")
        first_id = sorted_bucket.first().identification["id"]
        self.assertIn(first_id, [self.u1.id, self.u3.id])

    def test_getitem_negative_and_out_of_range(self):
        """
        Validates negative indexing and out-of-range behavior:
        - Negative index returns ValueError
        - Out-of-range index raises IndexError
        """
        # [-1] should be the last (carol)
        with self.assertRaises(ValueError):
            _ = self.bucket[-1]

        with self.assertRaises(IndexError):
            _ = self.bucket[999]

        with self.assertRaises(ValueError):
            _ = self.bucket[-10]

    def test_getitem_cached_negative_index_matches_queryset_behavior(self):
        bucket = DatabaseBucket(User.objects.order_by("username"), UserManager)

        with CalculationRunContext():
            list(bucket)
            with self.assertRaises(ValueError):
                _ = bucket[-1]

    def test_slice_with_step_and_negative_slice(self):
        """
        Ensures slicing with steps and negative ranges behave consistently and preserve type.
        """
        step_bucket = self.bucket[0:3:2]
        self.assertIsInstance(step_bucket, DatabaseBucket)
        ids = [mgr.identification["id"] for mgr in step_bucket]
        self.assertListEqual(ids, [self.u1.id, self.u3.id])

        neg_slice = self.bucket[::-1]
        self.assertIsInstance(neg_slice, DatabaseBucket)
        ids_rev = [mgr.identification["id"] for mgr in neg_slice]
        self.assertListEqual(ids_rev, [self.u3.id, self.u2.id, self.u1.id])

    def test_union_deduplicates_and_handles_empty_bucket(self):
        """
        Union operator should:
        - Deduplicate overlapping entries
        - Work with empty right/left operands
        """
        only_alice = self.bucket.filter(username="alice")

        dup_union = only_alice | only_alice
        self.assertEqual(len(dup_union), 1)
        self.assertEqual(dup_union.first().identification["id"], self.u1.id)

        empty = DatabaseBucket(User.objects.none(), UserManager)

        u1_union_empty = only_alice | empty
        empty_union_u1 = empty | only_alice
        self.assertEqual(len(u1_union_empty), 1)
        self.assertEqual(len(empty_union_u1), 1)
        self.assertEqual(u1_union_empty.first().identification["id"], self.u1.id)
        self.assertEqual(empty_union_u1.first().identification["id"], self.u1.id)

    def test_filter_chaining_merges_and_results_match(self):
        """
        Chaining filter calls should merge filter definitions and return OR of values for same key.
        """
        # Start with no filter then add multiple username filters
        chained = self.bucket.filter(username="alice").filter(username="carol")
        # Definitions merged
        self.assertIn("username", chained.filters)
        self.assertCountEqual(chained.filters["username"], ["alice", "carol"])
        # Data should contain no results because no user is both alice and carol
        self.assertEqual(len(chained), 0)

    def test_exclude_chaining_merges_and_results_match(self):
        """
        Chaining exclude calls should merge exclusion definitions and remove all specified values (OR semantics).
        """
        chained = self.bucket.exclude(username="alice").exclude(username="bob")
        self.assertIn("username", chained.excludes)
        self.assertCountEqual(chained.excludes["username"], ["alice", "bob"])
        remaining_ids = [mgr.identification["id"] for mgr in chained]
        self.assertCountEqual(remaining_ids, [self.u3.id])

    def test_sort_invalid_key_raises(self):
        """
        Sorting by an unknown field/property should raise a ValueError.
        """
        with self.assertRaises(ValueError):
            _ = self.bucket.sort("does_not_exist")

    def test_filter_invalid_lookup_raises(self):
        """
        Filtering by an unknown attribute/property should use the queryset wrapper.
        """
        with self.assertRaises(QuerysetFilteringError):
            _ = self.bucket.filter(nonexistent_attr__gte=1)

    def test_exclude_invalid_lookup_raises_filtering_error(self):
        """
        Excluding by an unknown attribute/property should use the same wrapper as filter().
        """
        with self.assertRaises(QuerysetFilteringError):
            _ = self.bucket.exclude(nonexistent_attr__gte=1)

    def test_truthiness_and_bool_semantics(self):
        """
        Buckets should be truthy if non-empty and falsy if empty (via __len__).
        """
        self.assertTrue(bool(self.bucket))
        empty = DatabaseBucket(User.objects.none(), UserManager)
        self.assertFalse(bool(empty))

    def test_repr_and_str_include_model_and_count(self):
        """
        __repr__/__str__ should include useful debugging info like model and size.
        """
        r = repr(self.bucket)
        s = str(self.bucket)
        # Heuristic checks to avoid over-specifying format
        self.assertTrue("DatabaseBucket" in r or "DatabaseBucket" in s)
        self.assertTrue(
            "User" in r or "auth.User" in r or "User" in s or "auth.User" in s
        )
        self.assertTrue("3" in r or "3" in s)

    def test_property_filter_multiple_operators(self):
        """
        Validate property-based filtering with multiple operators against graph_ql_property fields.
        """
        # username_length values: alice=5, bob=3, carol=5
        gte_five = self.bucket.filter(username_length__gte=5)
        self.assertEqual(len(gte_five), 2)
        lte_three = self.bucket.filter(username_length__lte=3)
        self.assertEqual(len(lte_three), 1)
        self.assertEqual(lte_three.first().identification["id"], self.u2.id)

    def test_property_sort_desc_then_asc_stability(self):
        """
        Ensure property-based sort supports direction and returns consistent ordering.
        """
        desc_sorted = self.bucket.sort("username_length", reverse=True)
        ids_desc = [m.identification["id"] for m in desc_sorted]
        # Both alice and carol have same length; bob shortest
        self.assertEqual(ids_desc[-1], self.u2.id)

        asc_sorted = self.bucket.sort("username_length", reverse=False)
        ids_asc = [m.identification["id"] for m in asc_sorted]
        self.assertEqual(ids_asc[0], self.u2.id)
