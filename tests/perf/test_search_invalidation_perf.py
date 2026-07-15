"""Operation-count regression tests for bounded search invalidation."""

from __future__ import annotations

from unittest.mock import patch

from django.db import models

from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import IndexConfig, SearchInvalidationRule
from general_manager.search.invalidation import (
    SearchInvalidationPlan,
    finalize_search_invalidation_capture,
    resolve_search_invalidation_phase,
    SearchScheduledWork,
    schedule_search_invalidation_work,
)
from general_manager.search.m2m_invalidation import (
    M2MInvalidationBinding,
    _schedule_owner_ids,
)
from tests.unit.test_search_invalidation import (
    Owner,
    Source,
    change,
    scheduled_target,
)


def test_scheduler_operation_count_scales_with_unique_targets_per_batch() -> None:
    """Duplicates add no backend units beyond ceil(unique / batch size)."""
    unique = tuple(scheduled_target(Owner, target_id) for target_id in range(1000))
    duplicates = tuple(scheduled_target(Owner, target_id) for target_id in range(1000))

    with (
        patch("general_manager.search.invalidation.get_setting", return_value=100),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            return_value=None,
        ) as mark_dirty,
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch",
            return_value=100,
        ) as dispatch_batch,
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(
                upserts=SearchInvalidationPlan(targets=(*unique, *duplicates))
            ),
            source_database_alias="default",
        )

    mark_dirty.assert_called_once_with(Owner, "global")
    assert dispatch_batch.call_count == 10
    assert sum(len(call.args[2]) for call in dispatch_batch.call_args_list) == 1000


def test_resolver_overflow_consumes_only_limit_plus_one_targets() -> None:
    """Overflow detection remains bounded to one item beyond the event cap."""
    yielded = 0

    def resolve(_change, owner):
        nonlocal yielded
        for target_id in range(10_000):
            yielded += 1
            yield owner(id=target_id)

    class SearchConfig:
        indexes = (IndexConfig(name="global", fields=["id"]),)
        invalidation_rules = (SearchInvalidationRule(source=Source, resolve=resolve),)

    with (
        patch.object(Owner, "SearchConfig", SearchConfig, create=True),
        patch.object(GeneralManagerMeta, "all_classes", [Owner]),
        patch(
            "general_manager.search.invalidation.get_setting",
            side_effect=lambda name, default: (
                1000 if name == "SEARCH_INVALIDATION_MAX_TARGETS" else default
            ),
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        capture = resolve_search_invalidation_phase(
            change("create", "after", Source(id=1))
        )

    assert yielded == 1001
    plan = finalize_search_invalidation_capture(capture)
    assert plan.targets == ()
    assert len(plan.dirty_fallbacks) == 1


def test_m2m_event_deduplicates_ids_before_scheduler_operation_count() -> None:
    """Duplicate public owner ids do not amplify one event's targeted work."""
    with (
        patch(
            "general_manager.search.m2m_invalidation.get_search_invalidation_max_targets",
            return_value=1000,
        ),
        patch(
            "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
        ) as schedule,
    ):
        binding = M2MInvalidationBinding(
            owner_manager=Owner,
            source_manager=Source,
            index_names=("global", "secondary"),
            owner_model=models.Model,
            source_model=models.Model,
            relation_name="unused",
            through_model=models.Model,
            owner_through_field="unused_owner",
            source_through_field="unused_source",
        )
        _schedule_owner_ids(binding, (1, 1, 2, 2, 3, 3), using="default")

    work = schedule.call_args.args[0]
    assert len(work.upserts.targets) == 6
    assert {
        (target.index_name, target.identification["id"])
        for target in work.upserts.targets
    } == {
        ("global", 1),
        ("secondary", 1),
        ("global", 2),
        ("secondary", 2),
        ("global", 3),
        ("secondary", 3),
    }
