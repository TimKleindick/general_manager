from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import pickle
from types import MethodType
from typing import Any, Literal, Protocol, Self, cast
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import connection, models
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from pytest_django.plugin import DjangoDbBlocker

from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.bucket.calculation_bucket import _database_source_signature
from general_manager.cache.dependency_cache import DependencyCacheHit
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.run_context import (
    BUCKET_INDEX_PREFIX,
    ORM_BUCKET_EXISTS_PREFIX,
    ORM_BUCKET_FIRST_ROW_PREFIX,
    ORM_BUCKET_MANAGER_RESULT_PREFIX,
    ORM_BUCKET_RESULT_PREFIX,
    ORM_BUCKET_ROW_RESULT_PREFIX,
    ORM_MODEL_RELATION_PREFETCH_PREFIX,
    ORM_MODEL_ROW_INDEX_PREFIX,
    ORM_QUERY_BUCKET_PREFIX,
    ORM_RELATION_MANAGER_PREFIX,
    TRUSTED_ORM_MANAGER_PREFIX,
    CalculationRunContext,
    current_calculation_run_context,
)
from general_manager.cache.signals import data_change, post_data_change, pre_data_change
from general_manager.interface import DatabaseInterface
from general_manager.interface.interfaces.calculation import CalculationInterface
from general_manager.interface.base_interface import (
    InterfaceBase,
    InvalidInputValueError,
)
from general_manager.interface.capabilities.orm.history import OrmHistoryCapability
from general_manager.interface.capabilities.orm.mutations import OrmMutationCapability
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from tests.perf.support import (
    Counter,
    DiagnosticObservation,
    PerfBudgets,
    capture_diagnostics,
)

pytestmark = [pytest.mark.perf, pytest.mark.django_db]

ROW_COUNT = 10_000
USERNAME_PREFIX = "perf-db-"
RowCount = Literal[999, 1000, 1001, 10000]
Operation = Literal["first", "get", "contains", "count", "list"]
DatabaseAccess = Callable[[], AbstractContextManager[None]]
RUN_CACHE_ENTRY_COUNT = 500
RUN_CACHE_PREFIXES = (
    ORM_BUCKET_RESULT_PREFIX,
    ORM_BUCKET_ROW_RESULT_PREFIX,
    ORM_BUCKET_MANAGER_RESULT_PREFIX,
    ORM_BUCKET_FIRST_ROW_PREFIX,
    ORM_MODEL_ROW_INDEX_PREFIX,
    ORM_MODEL_RELATION_PREFETCH_PREFIX,
    ORM_RELATION_MANAGER_PREFIX,
    ORM_QUERY_BUCKET_PREFIX,
    ORM_BUCKET_EXISTS_PREFIX,
    BUCKET_INDEX_PREFIX,
    TRUSTED_ORM_MANAGER_PREFIX,
)


def _assert_mixed_cache_observations(
    perf_budgets: PerfBudgets,
    *,
    discard_calls: int,
    key_inspections: int,
) -> None:
    perf_budgets.assert_observation(
        "RUN_CACHE_MIXED_500_DISCARD_CALLS",
        discard_calls,
    )
    perf_budgets.assert_observation(
        "RUN_CACHE_MIXED_500_KEY_INSPECTIONS",
        key_inspections,
    )


def test_mixed_cache_observations_accept_improvements_below_the_ceiling() -> None:
    budgets = PerfBudgets(
        {
            "RUN_CACHE_MIXED_500_DISCARD_CALLS": 22,
            "RUN_CACHE_MIXED_500_KEY_INSPECTIONS": 44_000,
        }
    )

    _assert_mixed_cache_observations(
        budgets,
        discard_calls=11,
        key_inspections=22_000,
    )

    assert budgets.observations == {
        "RUN_CACHE_MIXED_500_DISCARD_CALLS": 11,
        "RUN_CACHE_MIXED_500_KEY_INSPECTIONS": 22_000,
    }


class RawDeleteQuerySet(Protocol):
    @property
    def db(self) -> str: ...

    def _raw_delete(self, using: str) -> int: ...


class RunCacheMutationTarget:
    def __init__(self, body_calls: Counter) -> None:
        self._body_calls = body_calls

    @data_change
    def mutate(self) -> Self:
        self._body_calls.increment()
        return self


class PerfUserInterface(InterfaceBase):
    _model = User
    _instance: models.Model
    _search_date: object | None

    def __init__(self, pk: object, **_kwargs: object) -> None:
        self.identification = {"id": pk}

    @classmethod
    def _from_trusted_orm_instance(
        cls,
        instance: models.Model,
        *,
        search_date: object | None = None,
    ) -> Self:
        interface = cls.__new__(cls)
        interface.identification = {"id": instance.pk}
        interface._instance = instance
        interface._search_date = search_date
        return interface


def _manager_registry_snapshot() -> tuple[tuple[type[GeneralManager], ...], ...]:
    return (
        tuple(GeneralManagerMeta.all_classes),
        tuple(GeneralManagerMeta.read_only_classes),
        tuple(GeneralManagerMeta.pending_attribute_initialization),
        tuple(GeneralManagerMeta.pending_graphql_interfaces),
    )


_REGISTRIES_BEFORE_PERF_USER_MANAGER = _manager_registry_snapshot()


class PerfUserManager(GeneralManager):
    pass


for manager_registry in (
    GeneralManagerMeta.pending_graphql_interfaces,
    GeneralManagerMeta.all_classes,
    GeneralManagerMeta.read_only_classes,
    GeneralManagerMeta.pending_attribute_initialization,
):
    while PerfUserManager in manager_registry:
        manager_registry.remove(PerfUserManager)
assert _manager_registry_snapshot() == _REGISTRIES_BEFORE_PERF_USER_MANAGER
PerfUserManager.Interface = PerfUserInterface
PerfUserInterface._parent_class = PerfUserManager


def test_database_performance_manager_is_registry_isolated() -> None:
    for registry in (
        GeneralManagerMeta.all_classes,
        GeneralManagerMeta.read_only_classes,
        GeneralManagerMeta.pending_attribute_initialization,
        GeneralManagerMeta.pending_graphql_interfaces,
    ):
        assert PerfUserManager not in registry


def _expected_repeated_fk_parent_ids(
    parent_ids: tuple[int, ...],
) -> tuple[int, ...]:
    assert len(parent_ids) == 10
    return tuple(parent_ids[index // 100] for index in range(1_000))


def test_repeated_fk_shape_uses_consecutive_blocks_per_parent() -> None:
    parent_ids = tuple(range(1, 11))

    assert _expected_repeated_fk_parent_ids(parent_ids) == tuple(
        parent_id for parent_id in parent_ids for _ in range(100)
    )


def _unrestricted_database_access() -> AbstractContextManager[None]:
    return nullcontext()


def _delete_perf_users(prefix: str) -> int:
    """Delete relation-free perf users without traversing collected test models."""
    if not prefix.startswith(USERNAME_PREFIX):
        message = f"performance user prefix must start with {USERNAME_PREFIX!r}"
        raise ValueError(message)
    queryset = cast(
        RawDeleteQuerySet,
        User.objects.filter(username__startswith=prefix),
    )
    # This private API intentionally bypasses relation collection for fixture-only rows.
    return queryset._raw_delete(using=queryset.db)


@pytest.mark.parametrize("prefix", ["", "perf-db", "perf-", "users-"])
def test_delete_perf_users_rejects_a_prefix_outside_its_namespace(
    prefix: str,
) -> None:
    with (
        patch(
            "tests.perf.test_database_bucket_perf.User.objects.filter"
        ) as filter_users,
        pytest.raises(
            ValueError, match="performance user prefix must start with 'perf-db-'"
        ),
    ):
        _delete_perf_users(prefix)

    filter_users.assert_not_called()


def test_delete_perf_users_removes_only_the_requested_prefix() -> None:
    prefix = "perf-db-delete-target-"
    sentinel_prefix = "perf-db-delete-sentinel-"
    target = User.objects.create(username=f"{prefix}user")
    sentinel = User.objects.create(username=f"{sentinel_prefix}user")
    try:
        assert _delete_perf_users(prefix) == 1

        assert not User.objects.filter(pk=target.pk).exists()
        assert User.objects.filter(pk=sentinel.pk).exists()
    finally:
        _delete_perf_users(prefix)
        _delete_perf_users(sentinel_prefix)


@contextmanager
def _managed_perf_users(
    prefix: str,
    total: int,
    *,
    database_access: DatabaseAccess = _unrestricted_database_access,
) -> Iterator[tuple[int, ...]]:
    try:
        with database_access():
            _delete_perf_users(prefix)
            # Explicit negative IDs keep this large fixture from advancing the shared
            # auth_user sequence used by tests that create ordinary positive IDs.
            users = [
                User(id=index - total, username=f"{prefix}{index:05d}")
                for index in range(total)
            ]
            created_users = User.objects.bulk_create(users)
            primary_keys = tuple(int(user.pk) for user in created_users)
            assert len(primary_keys) == total
            assert primary_keys == tuple(
                range(primary_keys[0], primary_keys[0] + total)
            )
        yield primary_keys
    finally:
        with database_access():
            _delete_perf_users(prefix)


@contextmanager
def _managed_isolated_perf_users(
    prefix: str,
    total: int,
) -> Iterator[tuple[int, ...]]:
    """Create a small positive-ID fixture that cannot overlap the module fixture."""
    try:
        _delete_perf_users(prefix)
        created = User.objects.bulk_create(
            User(username=f"{prefix}{index:05d}") for index in range(total)
        )
        yield tuple(int(user.pk) for user in created)
    finally:
        _delete_perf_users(prefix)


def test_managed_perf_users_replaces_stale_rows_and_cleans_up_after_failure() -> None:
    prefix = "perf-db-lifecycle-"
    shared_prefix = f"{USERNAME_PREFIX}shared-sentinel-"
    shared_prefix_sentinel = User.objects.create(username=f"{shared_prefix}user")
    try:
        User.objects.create(username=f"{prefix}stale")

        with pytest.raises(RuntimeError, match="forced fixture body failure"):
            with _managed_perf_users(prefix, 2) as primary_keys:
                assert len(primary_keys) == 2
                assert list(
                    User.objects.filter(username__startswith=prefix)
                    .order_by("pk")
                    .values_list("username", flat=True)
                ) == [f"{prefix}00000", f"{prefix}00001"]
                assert User.objects.filter(pk=shared_prefix_sentinel.pk).exists()
                message = "forced fixture body failure"
                raise RuntimeError(message)

        assert not User.objects.filter(username__startswith=prefix).exists()
        assert User.objects.filter(pk=shared_prefix_sentinel.pk).exists()
    finally:
        _delete_perf_users(prefix)
        _delete_perf_users(shared_prefix)


def test_managed_perf_users_do_not_consume_ordinary_positive_primary_keys() -> None:
    prefix = "perf-db-primary-key-namespace-"
    ordinary_before = User.objects.create(username=f"{prefix}ordinary-before")
    try:
        with _managed_perf_users(prefix, 3) as primary_keys:
            assert primary_keys == (-3, -2, -1)

        ordinary_after = User.objects.create(username=f"{prefix}ordinary-after")
        assert ordinary_after.pk == ordinary_before.pk + 1
    finally:
        _delete_perf_users(prefix)


@pytest.fixture(scope="module")
def perf_user_primary_keys(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[tuple[int, ...]]:
    with _managed_perf_users(
        USERNAME_PREFIX,
        ROW_COUNT,
        database_access=django_db_blocker.unblock,
    ) as primary_keys:
        yield primary_keys


def _make_database_enumeration_manager(
    name: str,
    source: DatabaseBucket[PerfUserManager],
) -> type[GeneralManager]:
    """Build a default calculation manager and isolate all metaclass registries."""
    return _make_database_enumeration_manager_for_input(
        name,
        cast(Input[type[object]], Input(PerfUserManager, possible_values=source)),
    )


def _make_database_enumeration_manager_for_input(
    name: str,
    input_field: Input[type[object]],
) -> type[GeneralManager]:
    """Build a default calculation manager for one explicitly configured input."""
    interface = cast(
        type[CalculationInterface],
        type(
            f"{name}Interface",
            (CalculationInterface,),
            {
                "__module__": __name__,
                "value": input_field,
            },
        ),
    )
    registries_before = _manager_registry_snapshot()
    manager = cast(
        type[GeneralManager],
        type(
            name,
            (GeneralManager,),
            {"__module__": __name__, "Interface": interface},
        ),
    )
    for registry in (
        GeneralManagerMeta.all_classes,
        GeneralManagerMeta.read_only_classes,
        GeneralManagerMeta.pending_attribute_initialization,
        GeneralManagerMeta.pending_graphql_interfaces,
    ):
        while manager in registry:
            registry.remove(manager)
    manager.Interface._parent_class = manager
    assert manager.__init__ is GeneralManager.__init__
    assert _manager_registry_snapshot() == registries_before
    return manager


@pytest.mark.parametrize("size", [400, 800])
def test_database_manager_input_enumeration_work(
    size: int,
    perf_user_primary_keys: tuple[int, ...],
    perf_budgets: PerfBudgets,
) -> None:
    included_primary_keys = perf_user_primary_keys[:size]
    queryset = User.objects.filter(pk__in=included_primary_keys).order_by("pk")
    source = DatabaseBucket(
        cast(models.QuerySet[models.Model], queryset),
        PerfUserManager,
    )
    manager = _make_database_enumeration_manager(
        f"DatabaseEnumeration{size}Manager",
        source,
    )

    with (
        patch(
            "general_manager.bucket.calculation_bucket._database_source_signature",
            wraps=_database_source_signature,
        ) as compiled_signatures,
        override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
        CaptureQueriesContext(connection) as captured_queries,
    ):
        managers = list(CalculationBucket(manager))

    assert len(managers) == size
    assert [
        cast(dict[str, object], item.identification["value"])["id"] for item in managers
    ] == list(included_primary_keys)
    assert len(captured_queries) == 2
    assert compiled_signatures.call_count == 2
    prefix = f"CALC_ENUM_MANAGER_{size}"
    perf_budgets.assert_observation(f"{prefix}_QUERIES", len(captured_queries))
    perf_budgets.assert_observation(f"{prefix}_MANAGERS", len(managers))


def test_database_enumeration_mutation_falls_back_to_live_membership() -> None:
    prefix = "perf-db-enumeration-mutation-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        manager = _make_database_enumeration_manager(
            "DatabaseEnumerationMutationManager",
            source,
        )
        bucket = CalculationBucket(manager)
        combinations = bucket._materialize_combinations(expose=False)
        candidate = cast(GeneralManager, combinations[0]["value"])
        candidate.identification["id"] = 987_654_321

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            pytest.raises(InvalidInputValueError),
        ):
            list(bucket)

        assert bucket._combination_evidence == {}


def test_public_database_combinations_use_live_membership_after_row_deletion() -> None:
    prefix = "perf-db-enumeration-exposure-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        manager = _make_database_enumeration_manager(
            "DatabaseEnumerationExposureManager",
            source,
        )
        bucket = CalculationBucket(manager)
        assert len(bucket.generate_combinations()) == 2
        User.objects.filter(pk=primary_keys[-1]).delete()

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            pytest.raises(InvalidInputValueError),
        ):
            list(bucket)


def test_database_source_query_mutation_falls_back_and_rejects() -> None:
    prefix = "perf-db-enumeration-source-mutation-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        manager = _make_database_enumeration_manager(
            "DatabaseEnumerationSourceMutationManager",
            source,
        )
        bucket = CalculationBucket(manager)
        assert len(bucket._materialize_combinations(expose=False)) == 2
        source._data = source._data.none()

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            pytest.raises(InvalidInputValueError),
        ):
            list(bucket)


def test_database_validator_deleting_later_row_uses_live_fallback() -> None:
    prefix = "perf-db-enumeration-validator-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        validator_calls = Counter()

        def validator(_value: object) -> bool:
            validator_calls.increment()
            if validator_calls.value == 1:
                queryset = cast(
                    RawDeleteQuerySet,
                    User.objects.filter(pk=primary_keys[-1]),
                )
                queryset._raw_delete(using=queryset.db)
            return True

        manager = _make_database_enumeration_manager_for_input(
            "DatabaseEnumerationValidatorManager",
            cast(
                Input[type[object]],
                Input(
                    PerfUserManager,
                    possible_values=source,
                    validator=validator,
                ),
            ),
        )

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            pytest.raises(InvalidInputValueError),
        ):
            list(CalculationBucket(manager))

        assert validator_calls.value == 2


def test_callable_database_provider_remains_on_per_candidate_fallback() -> None:
    prefix = "perf-db-enumeration-callback-"
    with _managed_isolated_perf_users(prefix, 3) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        calls = Counter()

        def possible_values() -> DatabaseBucket[PerfUserManager]:
            calls.increment()
            return source

        manager = _make_database_enumeration_manager_for_input(
            "DatabaseEnumerationCallbackManager",
            cast(
                Input[type[object]],
                Input(PerfUserManager, possible_values=possible_values),
            ),
        )
        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            CaptureQueriesContext(connection) as captured_queries,
        ):
            managers = list(CalculationBucket(manager))

        assert len(managers) == 3
        assert calls.value == 4
        assert len(captured_queries) == 4


def test_streaming_database_preview_does_not_prepare_the_whole_source() -> None:
    prefix = "perf-db-enumeration-preview-"
    with _managed_isolated_perf_users(prefix, 40) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        manager = _make_database_enumeration_manager(
            "DatabaseEnumerationPreviewManager",
            source,
        )
        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            CaptureQueriesContext(connection) as captured_queries,
        ):
            preview = str(CalculationBucket(manager))

        assert "..." in preview
        assert len(captured_queries) == 1


def test_database_trust_tracks_the_same_source_dependency_as_fallback() -> None:
    prefix = "perf-db-enumeration-dependency-"
    with _managed_isolated_perf_users(prefix, 3) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        optimized_manager = _make_database_enumeration_manager(
            "DatabaseEnumerationDependencyOptimizedManager",
            source,
        )
        fallback_manager = _make_database_enumeration_manager(
            "DatabaseEnumerationDependencyFallbackManager",
            source,
        )

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            DependencyTracker() as optimized_dependencies,
        ):
            list(CalculationBucket(optimized_manager))
        fallback_bucket = CalculationBucket(fallback_manager)
        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            DependencyTracker() as fallback_dependencies,
        ):
            fallback_bucket.generate_combinations()
            list(fallback_bucket)

        optimized_source_dependencies = {
            dependency
            for dependency in optimized_dependencies
            if dependency[0] == "PerfUserManager"
        }
        fallback_source_dependencies = {
            dependency
            for dependency in fallback_dependencies
            if dependency[0] == "PerfUserManager"
        }
        assert optimized_source_dependencies == fallback_source_dependencies
        assert ("PerfUserManager", "all", "") in optimized_source_dependencies


@pytest.mark.parametrize("source_kind", ["historical", "custom_bucket"])
def test_unsupported_database_source_shapes_use_per_candidate_fallback(
    source_kind: str,
) -> None:
    prefix = f"perf-db-enumeration-{source_kind}-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        queryset = cast(
            models.QuerySet[models.Model],
            User.objects.filter(pk__in=primary_keys).order_by("pk"),
        )
        if source_kind == "historical":
            source = DatabaseBucket(
                queryset,
                PerfUserManager,
                search_date=datetime.now(UTC),
            )
        else:

            class CustomDatabaseBucket(DatabaseBucket[PerfUserManager]):
                pass

            source = CustomDatabaseBucket(queryset, PerfUserManager)
        manager = _make_database_enumeration_manager(
            f"DatabaseEnumeration{source_kind.title()}Manager",
            source,
        )

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            CaptureQueriesContext(connection) as queries,
        ):
            managers = list(CalculationBucket(manager))

        assert len(managers) == 2
        assert len(queries) == 3


@pytest.mark.parametrize("override_kind", ["identification", "tracking"])
def test_custom_database_source_manager_hooks_disable_batch_trust(
    override_kind: str,
) -> None:
    prefix = f"perf-db-enumeration-source-{override_kind}-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        if override_kind == "identification":
            attributes: dict[str, object] = {
                "identification": property(
                    lambda manager: GeneralManager.identification.__get__(
                        manager, type(manager)
                    )
                )
            }
        else:

            @classmethod
            def custom_tracking(
                cls: type[GeneralManager], identification: dict[str, object]
            ) -> None:
                GeneralManager._track_identification_dependency_active.__func__(
                    cls, identification
                )

            attributes = {"_track_identification_dependency_active": custom_tracking}
        source_manager = cast(
            type[GeneralManager],
            type(
                f"CustomSource{override_kind.title()}Manager",
                (PerfUserManager,),
                {"__module__": __name__, **attributes},
            ),
        )
        for registry in (
            GeneralManagerMeta.all_classes,
            GeneralManagerMeta.read_only_classes,
            GeneralManagerMeta.pending_attribute_initialization,
            GeneralManagerMeta.pending_graphql_interfaces,
        ):
            while source_manager in registry:
                registry.remove(source_manager)
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            source_manager,
        )
        calculation_manager = _make_database_enumeration_manager_for_input(
            f"CustomSource{override_kind.title()}Calculation",
            cast(
                Input[type[object]],
                Input(source_manager, possible_values=source),
            ),
        )

        with (
            override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
            CaptureQueriesContext(connection) as queries,
        ):
            managers = list(CalculationBucket(calculation_manager))

        assert len(managers) == 2
        assert len(queries) == 3


def test_database_copy_slice_and_pickle_use_live_membership_after_deletion() -> None:
    prefix = "perf-db-enumeration-copy-slice-pickle-"
    with _managed_isolated_perf_users(prefix, 2) as primary_keys:
        source = DatabaseBucket(
            cast(
                models.QuerySet[models.Model],
                User.objects.filter(pk__in=primary_keys).order_by("pk"),
            ),
            PerfUserManager,
        )
        manager_name = "DatabaseEnumerationLifecycleManager"
        manager = _make_database_enumeration_manager(manager_name, source)
        manager.__qualname__ = manager_name
        globals()[manager_name] = manager
        try:
            bucket = CalculationBucket(manager)
            bucket._materialize_combinations(expose=False)
            copied = copy(bucket)
            sliced = bucket[:]
            restored = pickle.loads(pickle.dumps(bucket))  # noqa: S301
        finally:
            del globals()[manager_name]
        User.objects.filter(pk=primary_keys[-1]).delete()

        for candidate_bucket in (bucket, copied, sliced, restored):
            with (
                override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True),
                pytest.raises(InvalidInputValueError),
            ):
                list(candidate_bucket)


@dataclass
class ManagerConstructionCounters:
    instances: Counter
    primary_keys: Counter

    @classmethod
    def create(cls) -> ManagerConstructionCounters:
        return cls(Counter(), Counter())

    def reset(self) -> None:
        self.instances.reset()
        self.primary_keys.reset()

    def snapshot(self, queries: int) -> PhaseObservation:
        return PhaseObservation(
            queries=queries,
            instance_constructors=self.instances.value,
            primary_key_constructors=self.primary_keys.value,
        )


@dataclass(frozen=True)
class PhaseObservation:
    queries: int
    instance_constructors: int
    primary_key_constructors: int

    @property
    def constructors(self) -> int:
        return self.instance_constructors + self.primary_key_constructors


def _invoke_operation(
    operation: Operation,
    bucket: DatabaseBucket[PerfUserManager],
    middle_primary_key: int,
    target_manager: PerfUserManager,
) -> object:
    if operation == "first":
        return bucket.first()
    if operation == "get":
        return bucket.get(id=middle_primary_key)
    if operation == "contains":
        return target_manager in bucket
    if operation == "count":
        return bucket.count()
    if operation == "list":
        return list(bucket)
    message = f"unsupported operation: {operation}"
    raise AssertionError(message)


def _assert_operation_result(
    operation: Operation,
    result: object,
    primary_keys: tuple[int, ...],
    middle_primary_key: int,
) -> None:
    if operation == "first":
        manager = cast(PerfUserManager, result)
        assert manager.identification["id"] == primary_keys[0]
        return
    if operation == "get":
        manager = cast(PerfUserManager, result)
        assert manager.identification["id"] == middle_primary_key
        return
    if operation == "contains":
        assert result is True
        return
    if operation == "count":
        assert result == len(primary_keys)
        return
    if operation == "list":
        managers = cast(list[PerfUserManager], result)
        assert len(managers) == len(primary_keys)
        assert managers[0].identification["id"] == primary_keys[0]
        assert managers[-1].identification["id"] == primary_keys[-1]
        assert [manager.identification["id"] for manager in managers] == list(
            primary_keys
        )
        return
    message = f"unsupported operation: {operation}"
    raise AssertionError(message)


def _assert_phase_budget(
    perf_budgets: PerfBudgets,
    prefix: str,
    observation: PhaseObservation,
) -> None:
    assert observation.primary_key_constructors == 0
    perf_budgets.assert_observation(f"{prefix}_QUERIES", observation.queries)
    perf_budgets.assert_observation(f"{prefix}_CONSTRUCTORS", observation.constructors)


def test_invoke_operation_rejects_an_unknown_operation() -> None:
    bucket = DatabaseBucket(
        cast(models.QuerySet[models.Model], User.objects.none()),
        PerfUserManager,
    )
    target_manager = PerfUserManager(0)

    with pytest.raises(AssertionError, match="unsupported operation: unknown"):
        _invoke_operation(cast(Operation, "unknown"), bucket, 0, target_manager)


def test_assert_operation_result_rejects_an_unknown_operation() -> None:
    with pytest.raises(AssertionError, match="unsupported operation: unknown"):
        _assert_operation_result(cast(Operation, "unknown"), object(), (), 0)


def test_data_change_mixed_run_cache_invalidation_work(
    monkeypatch: pytest.MonkeyPatch,
    perf_budgets: PerfBudgets,
    pytestconfig: pytest.Config,
) -> None:
    monkeypatch.setattr(pre_data_change, "send", lambda **_kwargs: [])
    monkeypatch.setattr(post_data_change, "send", lambda **_kwargs: [])
    monkeypatch.setattr(
        "general_manager.cache.dependency_index.begin_dependency_data_change",
        lambda: 0,
    )
    monkeypatch.setattr(
        "general_manager.cache.dependency_index.end_dependency_data_change",
        lambda: None,
    )
    monkeypatch.setattr(
        "general_manager.cache.dependency_index.is_dependency_data_change_active",
        lambda: False,
    )
    monkeypatch.setattr(
        "general_manager.cache.dependency_index."
        "drain_invalidated_cache_keys_for_graphql_rewarm",
        lambda: (),
    )

    diagnostic_captures = Counter()
    original_capture_diagnostics = capture_diagnostics

    def counted_capture_diagnostics(
        callback: Callable[[], RunCacheMutationTarget],
    ) -> DiagnosticObservation[RunCacheMutationTarget]:
        diagnostic_captures.increment()
        return original_capture_diagnostics(callback)

    monkeypatch.setattr(
        "tests.perf.test_database_bucket_perf.capture_diagnostics",
        counted_capture_diagnostics,
    )
    diagnostics_enabled = pytestconfig.getoption("verbose") >= 2

    body_calls = Counter()
    discard_calls = Counter()
    key_inspections = Counter()
    target = RunCacheMutationTarget(body_calls)
    dependency_hits = {
        f"mixed-run-cache-{index}": DependencyCacheHit(
            value=index,
            dependencies=frozenset(),
        )
        for index in range(RUN_CACHE_ENTRY_COUNT)
    }
    unrelated_values = {
        ("unrelated", index): ("unrelated-value", index)
        for index in range(RUN_CACHE_ENTRY_COUNT)
    }

    with CalculationRunContext() as context:
        for prefix in RUN_CACHE_PREFIXES:
            for index in range(RUN_CACHE_ENTRY_COUNT):
                context.set((prefix, index), index)
        for key, value in unrelated_values.items():
            context.set(key, value)
        context.set_dependency_cache_hits(dependency_hits)

        initial_targeted_count = sum(
            1
            for key in context._values
            if isinstance(key, tuple) and key and key[0] in RUN_CACHE_PREFIXES
        )
        assert initial_targeted_count == 5_500
        assert len(context._values) == 6_000

        phase_snapshots: list[dict[Hashable, object]] = []
        original_discard_prefix = context.discard_prefix

        def counted_discard_prefix(
            _context: CalculationRunContext,
            prefix: tuple[Hashable, ...],
        ) -> None:
            discard_calls.increment()
            key_inspections.increment(len(context._values))
            original_discard_prefix(prefix)

        monkeypatch.setattr(
            context,
            "discard_prefix",
            MethodType(counted_discard_prefix, context),
        )
        original_clear_trusted_orm_managers = context.clear_trusted_orm_managers

        def observed_clear_trusted_orm_managers(
            _context: CalculationRunContext,
        ) -> None:
            original_clear_trusted_orm_managers()
            phase_snapshots.append(dict(context._values))

        monkeypatch.setattr(
            context,
            "clear_trusted_orm_managers",
            MethodType(observed_clear_trusted_orm_managers, context),
        )

        if diagnostics_enabled:
            diagnostic = capture_diagnostics(target.mutate)
            result = diagnostic.result
        else:
            result = target.mutate()

        observed_discard_calls = discard_calls.value
        observed_key_inspections = key_inspections.value
        observed_phase_snapshots = tuple(phase_snapshots)

        assert body_calls.value == 1
        assert result is target
        assert len(observed_phase_snapshots) == 2
        for snapshot in observed_phase_snapshots:
            assert not any(
                isinstance(key, tuple) and key and key[0] in RUN_CACHE_PREFIXES
                for key in snapshot
            )
            assert snapshot == unrelated_values
        assert context._values == unrelated_values
        assert all(
            context.get_dependency_cache_hit(key) is hit
            for key, hit in dependency_hits.items()
        )
        assert len(context._dependency_cache_hits) == RUN_CACHE_ENTRY_COUNT
        _assert_mixed_cache_observations(
            perf_budgets,
            discard_calls=observed_discard_calls,
            key_inspections=observed_key_inspections,
        )
        assert diagnostic_captures.value == int(diagnostics_enabled)
        if diagnostics_enabled:
            print(
                "RUN_CACHE_MIXED_500_DIAGNOSTIC "
                f"elapsed={diagnostic.elapsed_seconds:.6f}s "
                f"peak={diagnostic.peak_bytes}B"
            )

    assert context._values == {}
    assert context._dependency_cache_hits == {}
    assert current_calculation_run_context() is None


@pytest.mark.parametrize("row_count", [999, 1000, 1001, 10000])
@pytest.mark.parametrize("operation", ["first", "get", "contains", "count", "list"])
def test_database_bucket_terminal_operation_work(
    row_count: RowCount,
    operation: Operation,
    perf_user_primary_keys: tuple[int, ...],
    monkeypatch: pytest.MonkeyPatch,
    perf_budgets: PerfBudgets,
    pytestconfig: pytest.Config,
) -> None:
    registries_before = _manager_registry_snapshot()
    included_primary_keys = perf_user_primary_keys[:row_count]
    middle_primary_key = included_primary_keys[row_count // 2]
    queryset = User.objects.filter(
        pk__gte=included_primary_keys[0],
        pk__lte=included_primary_keys[-1],
        username__startswith=USERNAME_PREFIX,
    ).order_by("pk")
    assert queryset.count() == row_count
    bucket = DatabaseBucket(
        cast(models.QuerySet[models.Model], queryset),
        PerfUserManager,
    )
    target_manager = PerfUserManager(included_primary_keys[-1])

    construction_counters = ManagerConstructionCounters.create()
    original_from_instance = cast(
        Callable[[DatabaseBucket[PerfUserManager], models.Model], PerfUserManager],
        DatabaseBucket.__dict__["_build_manager_from_instance"],
    )
    original_from_primary_key = cast(
        Callable[[DatabaseBucket[PerfUserManager], object], PerfUserManager],
        DatabaseBucket.__dict__["_build_manager_from_primary_key"],
    )

    def counted_from_instance(
        measured_bucket: DatabaseBucket[PerfUserManager],
        instance: models.Model,
    ) -> PerfUserManager:
        construction_counters.instances.increment()
        return original_from_instance(measured_bucket, instance)

    def counted_from_primary_key(
        measured_bucket: DatabaseBucket[PerfUserManager],
        primary_key: object,
    ) -> PerfUserManager:
        construction_counters.primary_keys.increment()
        return original_from_primary_key(measured_bucket, primary_key)

    monkeypatch.setattr(
        DatabaseBucket,
        "_build_manager_from_instance",
        counted_from_instance,
    )
    monkeypatch.setattr(
        DatabaseBucket,
        "_build_manager_from_primary_key",
        counted_from_primary_key,
    )

    diagnostic_captures = Counter()
    original_capture_diagnostics = capture_diagnostics

    def counted_capture_diagnostics(
        callback: Callable[[], object],
    ) -> DiagnosticObservation[object]:
        diagnostic_captures.increment()
        return original_capture_diagnostics(callback)

    monkeypatch.setattr(
        "tests.perf.test_database_bucket_perf.capture_diagnostics",
        counted_capture_diagnostics,
    )
    diagnostics_enabled = (
        row_count == ROW_COUNT
        and operation == "list"
        and pytestconfig.getoption("verbose") >= 2
    )

    construction_counters.reset()
    with CalculationRunContext():
        with CaptureQueriesContext(connection) as cold_queries:
            if diagnostics_enabled:
                diagnostic = capture_diagnostics(
                    lambda: _invoke_operation(
                        operation,
                        bucket,
                        middle_primary_key,
                        target_manager,
                    )
                )
                cold_result = diagnostic.result
            else:
                cold_result = _invoke_operation(
                    operation,
                    bucket,
                    middle_primary_key,
                    target_manager,
                )
        cold_observation = construction_counters.snapshot(len(cold_queries))
        _assert_operation_result(
            operation,
            cold_result,
            included_primary_keys,
            middle_primary_key,
        )
        del cold_result

        construction_counters.reset()
        with CaptureQueriesContext(connection) as warm_queries:
            warm_result = _invoke_operation(
                operation,
                bucket,
                middle_primary_key,
                target_manager,
            )
        warm_observation = construction_counters.snapshot(len(warm_queries))
        _assert_operation_result(
            operation,
            warm_result,
            included_primary_keys,
            middle_primary_key,
        )
        del warm_result

    if diagnostics_enabled:
        print(
            "DB_LIST_10000_COLD_DIAGNOSTIC "
            f"elapsed={diagnostic.elapsed_seconds:.6f}s "
            f"peak={diagnostic.peak_bytes}B"
        )
    assert diagnostic_captures.value == int(diagnostics_enabled)

    budget_prefix = f"DB_{operation.upper()}_{row_count}"
    _assert_phase_budget(
        perf_budgets,
        f"{budget_prefix}_COLD",
        cold_observation,
    )
    _assert_phase_budget(
        perf_budgets,
        f"{budget_prefix}_WARM",
        warm_observation,
    )
    assert _manager_registry_snapshot() == registries_before


class TestPerf337RelationAndHistoryWorkloads(GeneralManagerTransactionTestCase):
    perf_budgets: PerfBudgets
    Perf337Parent: type[GeneralManager]
    Perf337Child: type[GeneralManager]
    Perf337History: type[GeneralManager]

    @classmethod
    def setUpClass(cls) -> None:
        class Perf337Parent(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=64)

        class Perf337Child(GeneralManager):
            shape: str
            position: int
            parent: Perf337Parent | None

            class Interface(DatabaseInterface):
                shape = models.CharField(max_length=16)
                position = models.IntegerField()
                parent = models.ForeignKey(
                    "general_manager.Perf337Parent",
                    on_delete=models.CASCADE,
                    related_name="perf337_children",
                    null=True,
                    blank=True,
                )

        class Perf337History(GeneralManager):
            revision: int

            class Interface(DatabaseInterface):
                historical_lookup_buffer_seconds = 0
                revision = models.IntegerField()

        cls.Perf337Parent = Perf337Parent
        cls.Perf337Child = Perf337Child
        cls.Perf337History = Perf337History
        cls.general_manager_classes = [
            Perf337Parent,
            Perf337Child,
            Perf337History,
        ]

    @pytest.fixture(autouse=True)
    def _inject_perf_budgets(self, perf_budgets: PerfBudgets) -> None:
        self.perf_budgets = perf_budgets

    @staticmethod
    def _database_model(manager_class: type[GeneralManager]) -> type[models.Model]:
        return cast(
            type[models.Model],
            cast(Any, manager_class.Interface)._model,
        )

    def _seed_foreign_key_workloads(
        self,
    ) -> tuple[tuple[int, ...], tuple[int, ...]]:
        parent_model = self._database_model(self.Perf337Parent)
        child_model = self._database_model(self.Perf337Child)
        unique_parent_rows = parent_model.objects.bulk_create(
            [parent_model(name=f"unique-{index:04d}") for index in range(1_000)]
        )
        unique_parent_ids = tuple(int(parent.pk) for parent in unique_parent_rows)
        child_model.objects.bulk_create(
            [
                child_model(
                    shape="unique",
                    position=index,
                    parent_id=unique_parent_ids[index],
                )
                for index in range(1_000)
            ]
        )

        repeated_parent_rows = parent_model.objects.bulk_create(
            [parent_model(name=f"repeated-{index:02d}") for index in range(10)]
        )
        repeated_parent_ids = tuple(int(parent.pk) for parent in repeated_parent_rows)
        expected_repeated_parent_ids = _expected_repeated_fk_parent_ids(
            repeated_parent_ids
        )
        child_model.objects.bulk_create(
            [
                child_model(
                    shape="repeated",
                    position=index,
                    parent_id=expected_repeated_parent_ids[index],
                )
                for index in range(1_000)
            ]
        )

        return unique_parent_ids, expected_repeated_parent_ids

    def _measure_foreign_key_shape(
        self,
        shape: str,
        expected_parent_ids: tuple[int, ...],
    ) -> dict[str, int]:
        original_parent_init = cast(
            Callable[..., None],
            self.Perf337Parent.Interface.__init__,
        )

        def count_parent_constructions(counter: Counter) -> Callable[..., None]:
            def counted_parent_init(
                interface: InterfaceBase,
                *args: object,
                **kwargs: object,
            ) -> None:
                counter.increment()
                original_parent_init(interface, *args, **kwargs)

            return counted_parent_init

        child_bucket = self.Perf337Child.filter(shape=shape.lower()).sort("position")
        parent_constructors = Counter()
        with patch.object(
            self.Perf337Parent.Interface,
            "__init__",
            count_parent_constructions(parent_constructors),
        ):
            with CalculationRunContext():
                parent_constructors.reset()
                with CaptureQueriesContext(connection) as cold_queries:
                    cold_children = tuple(child_bucket)
                    assert all(
                        not cast(Any, child._interface)._instance._state.fields_cache
                        for child in cold_children
                    )
                    cold_parents = tuple(
                        cast(GeneralManager | None, child.parent)
                        for child in cold_children
                    )
                cold_parent_ids = tuple(
                    cast(int, parent.identification["id"])
                    for parent in cold_parents
                    if parent is not None
                )
                cold_constructor_count = parent_constructors.value
                cold_query_count = len(cold_queries)

                parent_constructors.reset()
                with CaptureQueriesContext(connection) as warm_queries:
                    warm_children = tuple(child_bucket)
                    warm_parents = tuple(
                        cast(GeneralManager | None, child.parent)
                        for child in warm_children
                    )
                warm_parent_ids = tuple(
                    cast(int, parent.identification["id"])
                    for parent in warm_parents
                    if parent is not None
                )
                warm_constructor_count = parent_constructors.value
                warm_query_count = len(warm_queries)

        assert len(cold_children) == 1_000
        assert len(warm_children) == 1_000
        assert tuple(
            cast(int, child.identification["id"]) for child in warm_children
        ) == tuple(cast(int, child.identification["id"]) for child in cold_children)
        assert all(
            not cast(Any, child._interface)._instance._state.fields_cache
            for child in cold_children
        )
        assert cold_parent_ids == expected_parent_ids
        assert warm_parent_ids == expected_parent_ids
        expected_distinct = 1_000 if shape == "UNIQUE" else 10
        assert len(set(cold_parent_ids)) == expected_distinct
        assert len(set(warm_parent_ids)) == expected_distinct

        prefix = f"DB_FK_{shape}"
        return {
            f"{prefix}_COLD_QUERIES": cold_query_count,
            f"{prefix}_COLD_CONSTRUCTORS": cold_constructor_count,
            f"{prefix}_WARM_QUERIES": warm_query_count,
            f"{prefix}_WARM_CONSTRUCTORS": warm_constructor_count,
        }

    def _seed_history_workload(
        self,
    ) -> tuple[
        type[models.Model],
        tuple[GeneralManager, ...],
        tuple[int, ...],
        datetime,
        datetime,
    ]:
        history_model = self._database_model(self.Perf337History)
        history_rows = cast(Any, history_model).history
        base_time = datetime(2025, 1, 15, 12, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=base_time):
            history_managers = tuple(
                self.Perf337History.create(
                    revision=0,
                    creator_id=None,
                    ignore_permission=True,
                )
                for _ in range(100)
            )

        for revision in range(1, 4):
            round_time = base_time + timedelta(minutes=revision)
            with patch("django.utils.timezone.now", return_value=round_time):
                for manager in history_managers:
                    manager.update(
                        revision=revision,
                        creator_id=None,
                        ignore_permission=True,
                    )

        search_time = base_time + timedelta(minutes=3, seconds=30)
        with patch(
            "django.utils.timezone.now",
            return_value=base_time + timedelta(minutes=4),
        ):
            for manager in history_managers:
                manager.update(
                    revision=4,
                    creator_id=None,
                    ignore_permission=True,
                )

        expected_history_ids = tuple(
            cast(int, manager.identification["id"]) for manager in history_managers
        )
        assert history_rows.count() == 500
        assert tuple(manager.revision for manager in history_managers) == (4,) * 100
        assert (
            tuple(
                history_model.objects.order_by("pk").values_list("revision", flat=True)
            )
            == (4,) * 100
        )
        return (
            history_model,
            history_managers,
            expected_history_ids,
            base_time,
            search_time,
        )

    def _measure_history_read(
        self,
        search_time: datetime,
        expected_history_ids: tuple[int, ...],
    ) -> dict[str, int]:
        history_read_callbacks = Counter()
        history_capture_active = False
        original_get_historical_queryset = OrmHistoryCapability.get_historical_queryset

        def counted_get_historical_queryset(
            capability: OrmHistoryCapability,
            interface_cls: type[OrmInterfaceBase[models.Model]],
            historical_search_time: datetime,
        ) -> models.QuerySet[models.Model]:
            assert history_capture_active
            history_read_callbacks.increment()
            return original_get_historical_queryset(
                capability,
                interface_cls,
                historical_search_time,
            )

        with patch.object(
            OrmHistoryCapability,
            "get_historical_queryset",
            counted_get_historical_queryset,
        ):
            with CaptureQueriesContext(connection) as history_read_queries:
                history_capture_active = True
                historical_bucket = self.Perf337History.filter(
                    search_date=search_time
                ).sort("id")
                historical_managers = tuple(historical_bucket)
                historical_ids = tuple(
                    cast(int, manager.identification["id"])
                    for manager in historical_managers
                )
                historical_revisions = tuple(
                    manager.revision for manager in historical_managers
                )
                history_capture_active = False
            history_read_query_count = len(history_read_queries)
            history_read_callback_count = history_read_callbacks.value

        assert len(historical_managers) == 100
        assert historical_ids == expected_history_ids
        assert set(historical_ids) == set(expected_history_ids)
        assert historical_revisions == (3,) * 100
        return {
            "DB_HISTORY_READ_100_QUERIES": history_read_query_count,
            "DB_HISTORY_READ_100_CALLBACKS": history_read_callback_count,
        }

    def _measure_history_write(
        self,
        history_model: type[models.Model],
        history_managers: tuple[GeneralManager, ...],
        base_time: datetime,
    ) -> dict[str, int]:
        history_rows = cast(Any, history_model).history
        history_count_before_write = history_rows.count()
        history_write_callbacks = Counter()
        original_save_with_history = OrmMutationCapability.save_with_history

        def counted_save_with_history(
            capability: OrmMutationCapability,
            interface_cls: type[OrmInterfaceBase[models.Model]],
            instance: models.Model,
            *,
            creator_id: int | None,
            history_comment: str | None,
        ) -> object:
            history_write_callbacks.increment()
            return original_save_with_history(
                capability,
                interface_cls,
                instance,
                creator_id=creator_id,
                history_comment=history_comment,
            )

        with patch.object(
            OrmMutationCapability,
            "save_with_history",
            counted_save_with_history,
        ):
            with patch(
                "django.utils.timezone.now",
                return_value=base_time + timedelta(minutes=5),
            ):
                with CaptureQueriesContext(connection) as history_write_queries:
                    for manager in history_managers:
                        manager.update(
                            revision=5,
                            creator_id=None,
                            ignore_permission=True,
                        )
            history_write_query_count = len(history_write_queries)
            history_write_callback_count = history_write_callbacks.value

        assert tuple(manager.revision for manager in history_managers) == (5,) * 100
        assert (
            tuple(
                history_model.objects.order_by("pk").values_list("revision", flat=True)
            )
            == (5,) * 100
        )
        assert history_rows.count() == history_count_before_write + 100
        assert history_rows.count() == 600
        return {
            "DB_HISTORY_WRITE_100_QUERIES": history_write_query_count,
            "DB_HISTORY_WRITE_100_CALLBACKS": history_write_callback_count,
        }

    def test_foreign_key_traversal_and_history_read_write_work(self) -> None:
        unique_parent_ids, expected_repeated_parent_ids = (
            self._seed_foreign_key_workloads()
        )
        fk_observations: dict[str, int] = {}
        for shape, expected_parent_ids in (
            ("UNIQUE", unique_parent_ids),
            ("REPEATED", expected_repeated_parent_ids),
        ):
            fk_observations.update(
                self._measure_foreign_key_shape(shape, expected_parent_ids)
            )

        (
            history_model,
            history_managers,
            expected_history_ids,
            base_time,
            search_time,
        ) = self._seed_history_workload()
        history_read_observations = self._measure_history_read(
            search_time,
            expected_history_ids,
        )
        history_write_observations = self._measure_history_write(
            history_model,
            history_managers,
            base_time,
        )

        observations = {
            **fk_observations,
            **history_read_observations,
            **history_write_observations,
        }
        assert set(observations) == {
            "DB_FK_UNIQUE_COLD_QUERIES",
            "DB_FK_UNIQUE_COLD_CONSTRUCTORS",
            "DB_FK_UNIQUE_WARM_QUERIES",
            "DB_FK_UNIQUE_WARM_CONSTRUCTORS",
            "DB_FK_REPEATED_COLD_QUERIES",
            "DB_FK_REPEATED_COLD_CONSTRUCTORS",
            "DB_FK_REPEATED_WARM_QUERIES",
            "DB_FK_REPEATED_WARM_CONSTRUCTORS",
            "DB_HISTORY_READ_100_QUERIES",
            "DB_HISTORY_READ_100_CALLBACKS",
            "DB_HISTORY_WRITE_100_QUERIES",
            "DB_HISTORY_WRITE_100_CALLBACKS",
        }
        for name, observed in observations.items():
            self.perf_budgets.assert_observation(name, observed)
