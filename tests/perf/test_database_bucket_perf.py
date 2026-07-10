from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal, Self, cast

import pytest
from django.contrib.auth.models import User
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from pytest_django.plugin import DjangoDbBlocker

from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.cache.run_context import CalculationRunContext
from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
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


@pytest.fixture(scope="module")
def perf_user_primary_keys(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[tuple[int, ...]]:
    users = [
        User(username=f"{USERNAME_PREFIX}{index:05d}") for index in range(ROW_COUNT)
    ]
    with django_db_blocker.unblock():
        created_users = User.objects.bulk_create(users)
        primary_keys = tuple(int(user.pk) for user in created_users)

    assert len(primary_keys) == ROW_COUNT
    assert primary_keys == tuple(range(primary_keys[0], primary_keys[0] + ROW_COUNT))
    yield primary_keys

    with django_db_blocker.unblock():
        User.objects.filter(
            pk__gte=primary_keys[0],
            pk__lte=primary_keys[-1],
            username__startswith=USERNAME_PREFIX,
        ).delete()


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
    return list(bucket)


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
    managers = cast(list[PerfUserManager], result)
    assert len(managers) == len(primary_keys)
    assert managers[0].identification["id"] == primary_keys[0]
    assert managers[-1].identification["id"] == primary_keys[-1]
    assert [manager.identification["id"] for manager in managers] == list(primary_keys)


def _assert_phase_budget(
    perf_budgets: PerfBudgets,
    prefix: str,
    observation: PhaseObservation,
) -> None:
    assert observation.primary_key_constructors == 0
    perf_budgets.assert_observation(f"{prefix}_QUERIES", observation.queries)
    perf_budgets.assert_observation(f"{prefix}_CONSTRUCTORS", observation.constructors)


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
