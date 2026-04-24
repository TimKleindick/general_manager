from __future__ import annotations

import pytest
from django.db import models
from typing import ClassVar

from general_manager.seeding.manager_landscape import (
    InvalidSeedTargetError,
    ManagerSeedFailure,
    ManagerSelectionError,
    SeedExecutionResult,
    SeedPlanRow,
    SeedTarget,
    build_seed_plan,
    discover_seedable_managers,
    execute_seed_plan,
    order_targets_by_dependencies,
    parse_target_overrides,
    select_seed_targets,
    _required_manager_dependencies,
)


def test_parse_target_overrides_returns_name_to_count() -> None:
    assert parse_target_overrides(["Project=3", "Team=10"]) == {
        "Project": 3,
        "Team": 10,
    }


@pytest.mark.parametrize(
    "raw",
    ["Project", "Project=", "=3", "Project=0", "Project=-1", "Project=abc"],
)
def test_parse_target_overrides_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(InvalidSeedTargetError):
        parse_target_overrides([raw])


def test_parse_target_overrides_rejects_duplicate_names() -> None:
    with pytest.raises(InvalidSeedTargetError, match="Duplicate target override"):
        parse_target_overrides(["Project=3", "Project=4"])


def test_seed_target_missing_count_uses_default() -> None:
    target = SeedTarget(manager_name="Project", count=5)

    assert target.manager_name == "Project"
    assert target.count == 5


class _FactoryWithBatch:
    @staticmethod
    def create_batch(_count: int) -> list[object]:
        return []


class _SeedableProject:
    Factory = _FactoryWithBatch


class _NoFactory:
    pass


class _FactoryWithoutBatch:
    class Factory:
        pass


def _duplicate_seedable_manager(name: str, module: str) -> type[object]:
    return type(name, (), {"__module__": module, "Factory": _FactoryWithBatch})


def test_discover_seedable_managers_filters_to_create_batch_factories() -> None:
    assert discover_seedable_managers(
        [_SeedableProject, _NoFactory, _FactoryWithoutBatch]
    ) == {"_SeedableProject": _SeedableProject}


def test_discover_seedable_managers_rejects_name_collisions() -> None:
    first = _duplicate_seedable_manager("DuplicateManager", "tests.first")
    second = _duplicate_seedable_manager("DuplicateManager", "tests.second")

    with pytest.raises(ValueError, match=r"tests\.first\.DuplicateManager"):
        discover_seedable_managers([first, second])


def test_select_seed_targets_requires_explicit_selection() -> None:
    with pytest.raises(ManagerSelectionError):
        select_seed_targets(
            managers_by_name={"Project": _SeedableProject},
            selected_names=[],
            include_all=False,
            default_count=2,
            overrides={},
        )


def test_select_seed_targets_supports_all_with_default_count() -> None:
    targets = select_seed_targets(
        managers_by_name={"Project": _SeedableProject},
        selected_names=[],
        include_all=True,
        default_count=2,
        overrides={},
    )

    assert targets == [SeedTarget(manager_name="Project", count=2)]


def test_select_seed_targets_supports_repeated_managers_and_overrides() -> None:
    targets = select_seed_targets(
        managers_by_name={"Project": _SeedableProject, "Team": _SeedableProject},
        selected_names=["Team", "Project", "Team"],
        include_all=False,
        default_count=2,
        overrides={"Team": 7},
    )

    assert targets == [
        SeedTarget(manager_name="Team", count=7),
        SeedTarget(manager_name="Project", count=2),
    ]


def test_select_seed_targets_rejects_unknown_manager() -> None:
    with pytest.raises(ManagerSelectionError, match="Unknown manager"):
        select_seed_targets(
            managers_by_name={"Project": _SeedableProject},
            selected_names=["Missing"],
            include_all=False,
            default_count=2,
            overrides={},
        )


def test_select_seed_targets_rejects_override_for_unselected_manager() -> None:
    with pytest.raises(ManagerSelectionError, match="Target override provided"):
        select_seed_targets(
            managers_by_name={"Project": _SeedableProject, "Team": _SeedableProject},
            selected_names=["Project"],
            include_all=False,
            default_count=2,
            overrides={"Team": 4},
        )


class _BaseFakeModel(models.Model):
    class Meta:
        abstract = True
        app_label = "tests"


class _OwnerModel(_BaseFakeModel):
    name = models.CharField(max_length=64)


class _ProjectModel(_BaseFakeModel):
    owner = models.ForeignKey(_OwnerModel, on_delete=models.CASCADE)
    optional_owner = models.ForeignKey(
        _OwnerModel,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )


class _OwnerManager:
    Factory = _FactoryWithBatch

    class Interface:
        _model = _OwnerModel


class _ProjectManager:
    Factory = _FactoryWithBatch

    class Interface:
        _model = _ProjectModel


_OwnerModel._general_manager_class = _OwnerManager  # type: ignore[attr-defined]
_ProjectModel._general_manager_class = _ProjectManager  # type: ignore[attr-defined]


def test_required_manager_dependencies_resolves_required_relation_managers() -> None:
    assert _required_manager_dependencies(_ProjectManager) == {_OwnerManager}


def test_test_managers_point_interfaces_at_expected_models() -> None:
    assert _OwnerManager.Interface._model is _OwnerModel
    assert _ProjectManager.Interface._model is _ProjectModel


def test_order_targets_by_dependencies_runs_required_relations_first() -> None:
    targets = [
        SeedTarget("_ProjectManager", 2),
        SeedTarget("_OwnerManager", 2),
    ]

    ordered = order_targets_by_dependencies(
        targets,
        {
            "_ProjectManager": _ProjectManager,
            "_OwnerManager": _OwnerManager,
        },
    )

    assert ordered == [
        SeedTarget("_OwnerManager", 2),
        SeedTarget("_ProjectManager", 2),
    ]


def test_order_targets_by_dependencies_deduplicates_manager_names() -> None:
    targets = [
        SeedTarget("_OwnerManager", 2),
        SeedTarget("_OwnerManager", 5),
        SeedTarget("_ProjectManager", 2),
    ]

    ordered = order_targets_by_dependencies(
        targets,
        {
            "_ProjectManager": _ProjectManager,
            "_OwnerManager": _OwnerManager,
        },
    )

    assert ordered == [
        SeedTarget("_OwnerManager", 2),
        SeedTarget("_ProjectManager", 2),
    ]


def test_build_seed_plan_reports_missing_required_dependencies() -> None:
    rows = build_seed_plan(
        targets=[SeedTarget("_ProjectManager", 2)],
        managers_by_name={"_ProjectManager": _ProjectManager},
    )

    assert rows == [
        SeedPlanRow(
            manager_name="_ProjectManager",
            target_count=2,
            missing_dependencies=("_OwnerManager",),
        )
    ]


class _AllResult:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _CountingFactory:
    created_batches: ClassVar[list[int]] = []

    @classmethod
    def create_batch(cls, count: int) -> list[object]:
        cls.created_batches.append(count)
        return [object() for _ in range(count)]


class _CountingManager:
    Factory = _CountingFactory
    existing_count = 0

    @classmethod
    def all(cls) -> _AllResult:
        return _AllResult(cls.existing_count)


class _PartiallyFailingFactory:
    created_batches: ClassVar[list[int]] = []

    @classmethod
    def create_batch(cls, count: int) -> list[object]:
        if cls.created_batches:
            raise _FactoryExplodedError
        cls.created_batches.append(count)
        return [object() for _ in range(count)]


class _PartiallyFailingManager:
    Factory = _PartiallyFailingFactory

    @classmethod
    def all(cls) -> _AllResult:
        return _AllResult(0)


class _FailingFactory:
    @staticmethod
    def create_batch(_count: int) -> list[object]:
        raise _FactoryExplodedError


class _FailingManager:
    Factory = _FailingFactory

    @classmethod
    def all(cls) -> _AllResult:
        return _AllResult(0)


class _FactoryExplodedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _reset_counting_state() -> None:
    _CountingFactory.created_batches = []
    _CountingManager.existing_count = 0
    _PartiallyFailingFactory.created_batches = []


@pytest.mark.django_db
def test_execute_seed_plan_creates_only_missing_rows_in_batches() -> None:
    _CountingManager.existing_count = 2

    result = execute_seed_plan(
        targets=[SeedTarget("_CountingManager", 7)],
        managers_by_name={"_CountingManager": _CountingManager},
        batch_size=3,
        continue_on_error=False,
    )

    assert result == SeedExecutionResult(created={"_CountingManager": 5}, failures=())
    assert _CountingFactory.created_batches == [3, 2]


@pytest.mark.django_db
def test_execute_seed_plan_skips_when_existing_rows_meet_target() -> None:
    _CountingManager.existing_count = 7

    result = execute_seed_plan(
        targets=[SeedTarget("_CountingManager", 7)],
        managers_by_name={"_CountingManager": _CountingManager},
        batch_size=3,
        continue_on_error=False,
    )

    assert result == SeedExecutionResult(created={"_CountingManager": 0}, failures=())
    assert _CountingFactory.created_batches == []


@pytest.mark.django_db
def test_execute_seed_plan_fails_fast_by_default() -> None:
    with pytest.raises(ManagerSeedFailure, match="_FailingManager"):
        execute_seed_plan(
            targets=[
                SeedTarget("_FailingManager", 1),
                SeedTarget("_CountingManager", 1),
            ],
            managers_by_name={
                "_FailingManager": _FailingManager,
                "_CountingManager": _CountingManager,
            },
            batch_size=1,
            continue_on_error=False,
        )


@pytest.mark.django_db
def test_execute_seed_plan_collects_failures_when_requested() -> None:
    result = execute_seed_plan(
        targets=[SeedTarget("_FailingManager", 1), SeedTarget("_CountingManager", 1)],
        managers_by_name={
            "_FailingManager": _FailingManager,
            "_CountingManager": _CountingManager,
        },
        batch_size=1,
        continue_on_error=True,
    )

    assert result.failures
    assert result.failures[0].manager_name == "_FailingManager"
    assert result.failures[0].created_count == 0
    assert result.failures[0].remaining_count == 1
    assert result.failures[0].batch_size == 1
    assert "_CountingManager" in result.created


@pytest.mark.django_db
def test_execute_seed_plan_failure_reports_partial_progress() -> None:
    result = execute_seed_plan(
        targets=[SeedTarget("_PartiallyFailingManager", 5)],
        managers_by_name={"_PartiallyFailingManager": _PartiallyFailingManager},
        batch_size=2,
        continue_on_error=True,
    )

    assert result.created["_PartiallyFailingManager"] == 2
    assert result.failures[0].created_count == 2
    assert result.failures[0].remaining_count == 3
    assert result.failures[0].batch_size == 2
