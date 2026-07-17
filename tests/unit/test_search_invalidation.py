"""Unit tests for bounded related-search invalidation planning."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import ClassVar
from unittest.mock import call, patch

import pytest

import general_manager.search.invalidation as invalidation
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import (
    IndexConfig,
    SearchChange,
    SearchInvalidationRule,
)
from general_manager.search.invalidation import (
    SearchInvalidationPlan,
    SearchInvalidationPair,
    SearchInvalidationTarget,
    SearchScheduledWork,
    finalize_search_invalidation_capture,
    resolve_search_invalidation_phase,
    schedule_search_invalidation_work,
)
from general_manager.search.indexer import SearchDeleteTarget
from general_manager.search.reconciliation import DirtySearchIndex
from general_manager.search.registry import get_search_config as registry_search_config
from tests.utils.simple_manager_interface import BaseTestInterface


class IdentifiedInterface(BaseTestInterface):
    """Simple interface whose identification is an integer id."""

    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}


class Source(GeneralManager):
    Interface = IdentifiedInterface


class SourceChild(Source):
    pass


class Owner(GeneralManager):
    Interface = IdentifiedInterface


class SecondOwner(GeneralManager):
    Interface = IdentifiedInterface


INDEXES = (
    IndexConfig(name="global", fields=["id"]),
    IndexConfig(name="private", fields=["id"]),
)


class AfterPhaseFailure(RuntimeError):
    """Intentional update-after resolver failure."""


class DeclarationAccessFailure(RuntimeError):
    """Intentional malformed declaration accessor failure."""


class SchedulerBackendFailure(RuntimeError):
    """Intentional inline scheduler unit failure."""


class SchedulerBrokerFailure(RuntimeError):
    """Intentional async scheduler acceptance failure."""


class SchedulerPreparationFailure(RuntimeError):
    """Intentional hostile metadata preparation failure."""


class HostileIdentification(Mapping[str, object]):
    """Identification mapping that cannot be copied for dispatch."""

    def __getitem__(self, key: str) -> object:
        if key == "id":
            return 1
        raise KeyError(key)

    def __iter__(self):
        raise SchedulerPreparationFailure

    def __len__(self) -> int:
        return 1


class ToggleIndexesRule:
    """Rule-like object whose selected indexes can fail between phases."""

    source = Source
    relation = None

    def __init__(self, resolver: object) -> None:
        self.resolve = resolver
        self.fail_indexes = False

    @property
    def indexes(self) -> tuple[str, ...]:
        """Return one index until the test toggles an accessor failure."""
        if self.fail_indexes:
            raise DeclarationAccessFailure
        return ("global",)


class ThrowingIndexName:
    """Index-like object with an extensible failing name accessor."""

    @property
    def name(self) -> str:
        """Fail whenever invalidation attempts to inspect this index."""
        raise DeclarationAccessFailure


@pytest.fixture(autouse=True)
def restore_manager_registry() -> Iterator[None]:
    """Isolate the mutable manager registry and search configs."""
    original = GeneralManagerMeta.all_classes
    owner_config = getattr(Owner, "SearchConfig", None)
    second_config = getattr(SecondOwner, "SearchConfig", None)
    yield
    GeneralManagerMeta.all_classes = original
    for manager, config in ((Owner, owner_config), (SecondOwner, second_config)):
        if config is None:
            if "SearchConfig" in manager.__dict__:
                delattr(manager, "SearchConfig")
        else:
            manager.SearchConfig = config


def configure(
    manager: type[GeneralManager],
    *rules: SearchInvalidationRule,
    indexes: tuple[IndexConfig, ...] = INDEXES,
) -> None:
    """Attach a test search config to one owner."""

    class SearchConfig:
        pass

    SearchConfig.indexes = indexes
    SearchConfig.invalidation_rules = rules
    manager.SearchConfig = SearchConfig


def change(
    action: str,
    phase: str,
    instance: GeneralManager,
    alias: str = "default",
) -> SearchChange:
    """Build a lifecycle change for tests."""
    return SearchChange(
        action=action,  # type: ignore[arg-type]
        phase=phase,  # type: ignore[arg-type]
        instance=instance,
        database_alias=alias,
    )


def target_ids(plan: object) -> list[int]:
    """Return target ids from a finalized plan."""
    return [
        target.identification["id"]
        for target in plan.targets  # type: ignore[attr-defined]
    ]


def test_create_after_resolves_class_source_and_selected_indexes() -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            indexes=("private",),
            resolve=lambda _change, owner: (owner(id=7),),
        ),
    )
    GeneralManagerMeta.all_classes = [Source, Owner]

    capture = resolve_search_invalidation_phase(
        change("create", "after", SourceChild(id=1))
    )
    plan = finalize_search_invalidation_capture(capture)

    assert target_ids(plan) == [7]
    assert [target.index_name for target in plan.targets] == ["private"]
    assert plan.dirty_fallbacks == ()


def test_relation_metadata_does_not_suppress_normal_resolution() -> None:
    calls: list[str] = []

    def resolver(_change: SearchChange, owner: type[GeneralManager]):
        calls.append("resolved")
        return (owner(id=71),)

    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=resolver,
            relation="sources",
        ),
    )
    GeneralManagerMeta.all_classes = [Owner]

    plan = finalize_search_invalidation_capture(
        resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
    )

    assert calls == ["resolved"]
    assert [target.index_name for target in plan.targets] == ["global", "private"]


def test_dotted_source_is_lazy_and_subclass_matches() -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=f"{__name__}.Source",
            resolve=lambda _change, owner: (owner(id=8),),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    plan = finalize_search_invalidation_capture(
        resolve_search_invalidation_phase(change("create", "after", SourceChild(id=1)))
    )

    assert target_ids(plan) == [8]


def test_inherited_owner_config_is_resolved_for_concrete_registered_subclass() -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _change, owner: (owner(id=9),),
        ),
        indexes=(INDEXES[0],),
    )

    class ConcreteOwner(Owner):
        pass

    GeneralManagerMeta.all_classes = [ConcreteOwner]

    plan = finalize_search_invalidation_capture(
        resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
    )

    assert len(plan.targets) == 1
    assert plan.targets[0].owner_class is ConcreteOwner
    assert target_ids(plan) == [9]


def test_update_unions_old_and_new_targets_and_copies_identification() -> None:
    yielded = Owner(id=10)
    phases: list[str] = []

    def resolver(event: SearchChange, _owner: type[GeneralManager]):
        phases.append(event.phase)
        return (yielded,) if event.phase == "before" else (Owner(id=11),)

    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=resolver),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    before = resolve_search_invalidation_phase(change("update", "before", Source(id=1)))
    yielded.identification["id"] = 999
    after = resolve_search_invalidation_phase(
        change("update", "after", Source(id=1)), previous=before
    )
    plan = finalize_search_invalidation_capture(after)

    assert phases == ["before", "after"]
    assert target_ids(plan) == [10, 11]


def test_delete_uses_before_capture_without_after_resolution() -> None:
    phases: list[str] = []

    def resolver(event: SearchChange, owner: type[GeneralManager]):
        phases.append(event.phase)
        return (owner(id=12),)

    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=resolver),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    capture = resolve_search_invalidation_phase(
        change("delete", "before", Source(id=1))
    )
    plan = finalize_search_invalidation_capture(capture)

    assert phases == ["before"]
    assert target_ids(plan) == [12]


def test_canonical_target_dedupe_preserves_first_seen_order() -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _change, owner: (owner(id=2), owner(id=1), owner(id=2)),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    plan = finalize_search_invalidation_capture(
        resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
    )

    assert target_ids(plan) == [2, 1]


def test_late_manager_is_discovered_on_every_phase() -> None:
    GeneralManagerMeta.all_classes = []
    empty = resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _change, owner: (owner(id=13),),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes.append(Owner)

    found = resolve_search_invalidation_phase(change("create", "after", Source(id=1)))

    assert finalize_search_invalidation_capture(empty).targets == ()
    assert target_ids(finalize_search_invalidation_capture(found)) == [13]


@pytest.mark.parametrize(
    "resolver",
    [
        None,
        lambda _change, _owner: (_ for _ in ()).throw(RuntimeError("boom")),
        lambda _change, _owner: (Source(id=4),),
    ],
)
def test_unresolvable_rule_falls_back_to_exact_selected_pairs(resolver: object) -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=resolver,  # type: ignore[arg-type]
            indexes=("private",),
        ),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with patch("general_manager.search.invalidation.logger.warning"):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "private"),)


def test_limit_plus_one_detects_overflow_without_exhausting_iterable() -> None:
    consumed: list[int] = []

    def infinite(_change: SearchChange, owner: type[GeneralManager]):
        value = 0
        while True:
            consumed.append(value)
            yield owner(id=value)
            value += 1

    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=infinite),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_setting",
            side_effect=lambda name, default: 2
            if name == "SEARCH_INVALIDATION_MAX_TARGETS"
            else default,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert consumed == [0, 1, 2]
    assert plan.targets == ()
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.5, "2"])
@pytest.mark.parametrize(
    "setting", ["SEARCH_INVALIDATION_MAX_TARGETS", "SEARCH_INVALIDATION_BATCH_SIZE"]
)
def test_invalid_runtime_settings_isolate_event_and_fallback(
    setting: str, bad: object
) -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _change, owner: (owner(id=14),),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_setting",
            side_effect=lambda name, default: bad if name == setting else default,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


@pytest.mark.parametrize("failure", ["error", "overflow"])
def test_failed_update_rule_discards_both_phases_but_keeps_independent_rule(
    failure: str,
) -> None:
    def unstable(event: SearchChange, owner: type[GeneralManager]):
        if event.phase == "before":
            return (owner(id=20),)
        if failure == "error":
            raise AfterPhaseFailure
        return (owner(id=value) for value in range(100))

    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=unstable),
        SearchInvalidationRule(
            source=Source,
            resolve=lambda event, owner: (
                owner(id=30 if event.phase == "before" else 31),
            ),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_setting",
            side_effect=lambda name, default: 4
            if name == "SEARCH_INVALIDATION_MAX_TARGETS"
            else default,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        before = resolve_search_invalidation_phase(
            change("update", "before", Source(id=1))
        )
        after = resolve_search_invalidation_phase(
            change("update", "after", Source(id=1)), previous=before
        )
        plan = finalize_search_invalidation_capture(after)

    assert target_ids(plan) == [30, 31]
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


def test_consumed_budget_spans_update_phases() -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda event, owner: (
                (owner(id=1), owner(id=2))
                if event.phase == "before"
                else (owner(id=3), owner(id=4))
            ),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_setting",
            side_effect=lambda name, default: 3
            if name == "SEARCH_INVALIDATION_MAX_TARGETS"
            else default,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        before = resolve_search_invalidation_phase(
            change("update", "before", Source(id=1))
        )
        after = resolve_search_invalidation_phase(
            change("update", "after", Source(id=1)), previous=before
        )

    plan = finalize_search_invalidation_capture(after)
    assert plan.targets == ()
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


def test_update_declaration_failure_discards_same_rule_prior_targets() -> None:
    rule = ToggleIndexesRule(
        lambda event, owner: (owner(id=60 if event.phase == "before" else 61),)
    )
    configure(
        Owner,
        rule,  # type: ignore[arg-type]
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    before = resolve_search_invalidation_phase(change("update", "before", Source(id=1)))
    rule.fail_indexes = True
    with patch("general_manager.search.invalidation.logger.warning"):
        after = resolve_search_invalidation_phase(
            change("update", "after", Source(id=1)), previous=before
        )
    plan = finalize_search_invalidation_capture(after)

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


def test_malformed_after_rule_keeps_independent_update_targets() -> None:
    malformed = ToggleIndexesRule(
        lambda event, owner: (owner(id=70 if event.phase == "before" else 71),)
    )
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda event, owner: (
                owner(id=80 if event.phase == "before" else 81),
            ),
            indexes=("global",),
        ),
        malformed,  # type: ignore[arg-type]
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    before = resolve_search_invalidation_phase(change("update", "before", Source(id=1)))
    malformed.fail_indexes = True
    with patch("general_manager.search.invalidation.logger.warning"):
        after = resolve_search_invalidation_phase(
            change("update", "after", Source(id=1)), previous=before
        )
    plan = finalize_search_invalidation_capture(after)

    assert target_ids(plan) == [80, 81]
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)
    assert after.consumed_target_budget == 3


def test_throwing_configured_index_name_isolated_to_unknown_name() -> None:
    malformed = ToggleIndexesRule(lambda _event, _owner: ())
    malformed.fail_indexes = True
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _event, owner: (owner(id=90),),
            indexes=("global",),
        ),
        malformed,  # type: ignore[arg-type]
        indexes=(INDEXES[0], ThrowingIndexName()),  # type: ignore[arg-type]
    )
    GeneralManagerMeta.all_classes = [Owner]

    with patch("general_manager.search.invalidation.logger.warning"):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert target_ids(plan) == [90]
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


def test_update_owner_config_failure_falls_back_prior_rules_only() -> None:
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _event, owner: (owner(id=100),),
        ),
    )
    configure(
        SecondOwner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda event, owner: (
                owner(id=110 if event.phase == "before" else 111),
            ),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner, SecondOwner]
    before = resolve_search_invalidation_phase(change("update", "before", Source(id=1)))

    def fail_one_owner(manager: type[GeneralManager]):
        if manager is Owner:
            raise DeclarationAccessFailure
        return registry_search_config(manager)

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=fail_one_owner,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        after = resolve_search_invalidation_phase(
            change("update", "after", Source(id=1)), previous=before
        )
    plan = finalize_search_invalidation_capture(after)

    assert target_ids(plan) == [110, 111]
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )
    assert after.consumed_target_budget == 3


def test_create_owner_config_failure_falls_back_static_exact_indexes() -> None:
    """Create config failures retain exact owner/index dirty-only recovery."""
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _event, owner: (owner(id=120),),
        ),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )


def test_delete_owner_config_failure_falls_back_static_exact_indexes() -> None:
    """Delete config failures retain exact owner/index dirty-only recovery."""
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _event, owner: (owner(id=121),),
        ),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("delete", "before", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )


@pytest.mark.django_db
def test_create_config_failure_falls_back_existing_durable_exact_indexes() -> None:
    """Durable owner/index state recovers when static config is unavailable."""
    from general_manager.search.models import SearchIndexState

    Owner.SearchConfig = object()
    GeneralManagerMeta.all_classes = [Owner]
    owner_path = f"{Owner.__module__}.{Owner.__name__}"
    SearchIndexState.objects.create(
        manager_path=owner_path,
        index_name="durable-only",
        schema_fingerprint="known-schema",
    )

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "durable-only"),)


@pytest.mark.django_db
def test_partial_static_config_failure_uses_all_durable_exact_indexes() -> None:
    """One unreadable static index makes the whole declaration unavailable."""
    from general_manager.search.models import SearchIndexState

    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=None),
        indexes=(INDEXES[0], ThrowingIndexName()),  # type: ignore[arg-type]
    )
    GeneralManagerMeta.all_classes = [Owner]
    owner_path = f"{Owner.__module__}.{Owner.__name__}"
    for index_name in ("global", "private"):
        SearchIndexState.objects.create(
            manager_path=owner_path,
            index_name=index_name,
            schema_fingerprint=f"{index_name}-schema",
        )

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )


def test_update_before_config_failure_synthesizes_exact_fallback() -> None:
    """A failed update-before capture conservatively dirties every owner index."""
    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=None),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("update", "before", Source(id=1)))
        )

    assert plan.targets == ()
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )


def test_persistent_update_config_failure_retains_synthetic_fallback() -> None:
    """Update-after retains the exact synthetic fallback captured before."""
    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=None),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        before = resolve_search_invalidation_phase(
            change("update", "before", Source(id=1))
        )
        after = resolve_search_invalidation_phase(
            change("update", "after", Source(id=1)),
            previous=before,
        )

    plan = finalize_search_invalidation_capture(after)
    assert plan.targets == ()
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )


def test_transient_update_before_config_failure_survives_successful_after() -> None:
    """Successful after targets cannot clear uncertainty from failed before."""
    configure(
        Owner,
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _event, owner: (owner(id=130),),
        ),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        before = resolve_search_invalidation_phase(
            change("update", "before", Source(id=1))
        )
    after = resolve_search_invalidation_phase(
        change("update", "after", Source(id=1)),
        previous=before,
    )

    plan = finalize_search_invalidation_capture(after)
    assert target_ids(plan) == [130, 130]
    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )


def test_config_and_durable_fallback_failures_never_escape_mutation() -> None:
    """Unavailable durable state leaves no fallback instead of failing writes."""
    from general_manager.search.models import SearchIndexState

    Owner.SearchConfig = object()
    GeneralManagerMeta.all_classes = [Owner]

    with (
        patch(
            "general_manager.search.invalidation.get_search_config",
            side_effect=DeclarationAccessFailure,
        ),
        patch.object(
            SearchIndexState.objects,
            "filter",
            side_effect=RuntimeError("state database unavailable"),
        ),
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert plan == SearchInvalidationPlan()


def test_fallback_does_not_consume_capacity_or_revisit_prior_rule() -> None:
    configure(
        Owner,
        SearchInvalidationRule(source=Source, resolve=None),
        SearchInvalidationRule(
            source=Source,
            resolve=lambda _change, owner: (owner(id=40), owner(id=41)),
        ),
        indexes=(INDEXES[0],),
    )
    GeneralManagerMeta.all_classes = [Owner]

    with patch(
        "general_manager.search.invalidation.get_setting",
        side_effect=lambda name, default: 2
        if name == "SEARCH_INVALIDATION_MAX_TARGETS"
        else default,
    ):
        plan = finalize_search_invalidation_capture(
            resolve_search_invalidation_phase(change("create", "after", Source(id=1)))
        )

    assert target_ids(plan) == [40, 41]
    assert plan.dirty_fallbacks == (SearchInvalidationPair(Owner, "global"),)


def scheduled_target(
    owner_class: type[GeneralManager],
    target_id: int,
    *,
    index_name: str = "global",
    alias: str = "default",
) -> SearchInvalidationTarget:
    """Build one scheduler target with a stable canonical identity."""
    owner_path = f"{owner_class.__module__}.{owner_class.__name__}"
    return SearchInvalidationTarget(
        owner_class=owner_class,
        owner_path=owner_path,
        identification={"id": target_id},
        index_name=index_name,
        database_alias=alias,
        canonical_key=(owner_path, f"id:{target_id}", index_name, alias),
    )


def dirty_token(
    owner_class: type[GeneralManager],
    index_name: str,
    state_id: int,
) -> DirtySearchIndex:
    """Build one acknowledgeable scheduler generation token."""
    return DirtySearchIndex(
        state_id=state_id,
        manager_path=f"{owner_class.__module__}.{owner_class.__name__}",
        index_name=index_name,
        generation=state_id + 10,
        acknowledgeable=True,
    )


def test_scheduler_groups_deduplicates_and_chunks_exact_pair_payloads() -> None:
    """Unique owner identities produce one copied payload per bounded chunk."""
    callbacks: list[object] = []
    targets = tuple(
        scheduled_target(Owner, target_id) for target_id in (1, 2, 2, 3, 4, 5)
    )
    token = dirty_token(Owner, "global", 1)

    with (
        patch(
            "general_manager.search.invalidation.get_setting",
            return_value=2,
        ),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callbacks.append(callback),
        ) as on_commit,
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            return_value=token,
        ) as mark_dirty,
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch",
            return_value=2,
        ) as dispatch_batch,
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty"
        ) as acknowledge,
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(upserts=SearchInvalidationPlan(targets=targets)),
            source_database_alias="default",
        )

        mark_dirty.assert_called_once_with(Owner, "global")
        on_commit.assert_called_once()
        dispatch_batch.assert_not_called()
        assert len(callbacks) == 1
        callbacks[0]()  # type: ignore[operator]

    owner_path = f"{Owner.__module__}.{Owner.__name__}"
    assert dispatch_batch.call_args_list == [
        call(owner_path, "global", ({"id": 1}, {"id": 2})),
        call(owner_path, "global", ({"id": 3}, {"id": 4})),
        call(owner_path, "global", ({"id": 5},)),
    ]
    assert all(
        type(identification) is dict
        for dispatch_call in dispatch_batch.call_args_list
        for identification in dispatch_call.args[2]
    )
    acknowledge.assert_called_once_with(token)


def test_scheduler_requires_every_pair_unit_before_ack_and_continues() -> None:
    """One failed chunk keeps its pair dirty without blocking another pair."""
    targets = (
        *(scheduled_target(Owner, target_id) for target_id in (1, 2, 3)),
        scheduled_target(SecondOwner, 9),
    )
    owner_token = dirty_token(Owner, "global", 1)
    second_token = dirty_token(SecondOwner, "global", 2)
    tokens = iter((owner_token, second_token))
    calls: list[tuple[str, tuple[dict[str, object], ...]]] = []

    def dispatch(
        manager_path: str,
        _index_name: str,
        identifications: tuple[dict[str, object], ...],
    ) -> int:
        calls.append((manager_path, identifications))
        if manager_path.endswith(".Owner") and identifications == ({"id": 3},):
            raise SchedulerBackendFailure
        return len(identifications)

    with (
        patch("general_manager.search.invalidation.get_setting", return_value=2),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            side_effect=lambda *_args: next(tokens),
        ),
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch",
            side_effect=dispatch,
        ),
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty"
        ) as acknowledge,
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(upserts=SearchInvalidationPlan(targets=targets)),
            source_database_alias="default",
        )

    assert [payload for _path, payload in calls] == [
        ({"id": 1}, {"id": 2}),
        ({"id": 3},),
        ({"id": 9},),
    ]
    acknowledge.assert_called_once_with(second_token)


def test_scheduler_fallback_wins_over_upsert_and_delete_for_same_pair() -> None:
    """An exact dirty fallback suppresses every targeted lane for that pair."""
    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    work = SearchScheduledWork(
        upserts=SearchInvalidationPlan(
            targets=(scheduled_target(Owner, 1),),
            dirty_fallbacks=(SearchInvalidationPair(Owner, "global"),),
        ),
        deletes=(
            SearchDeleteTarget(
                manager_class=Owner,
                manager_path=manager_path,
                index_name="global",
                document_id="owner:1",
            ),
        ),
    )

    with (
        patch("general_manager.search.invalidation.get_setting", return_value=100),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            return_value=dirty_token(Owner, "global", 1),
        ) as mark_dirty,
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch_batch,
        patch(
            "general_manager.search.invalidation.dispatch_delete_documents"
        ) as dispatch_delete,
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty"
        ) as acknowledge,
    ):
        schedule_search_invalidation_work(work, source_database_alias="default")

    mark_dirty.assert_called_once_with(Owner, "global")
    dispatch_batch.assert_not_called()
    dispatch_delete.assert_not_called()
    acknowledge.assert_not_called()


def test_scheduler_delete_is_a_separate_unit_and_shares_pair_ack() -> None:
    """Related upserts never replace immutable source document deletion work."""
    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    token = dirty_token(Owner, "global", 1)
    work = SearchScheduledWork(
        upserts=SearchInvalidationPlan(
            targets=(scheduled_target(Owner, 2),),
        ),
        deletes=(
            SearchDeleteTarget(
                manager_class=Owner,
                manager_path=manager_path,
                index_name="global",
                document_id="owner:1",
            ),
        ),
    )

    with (
        patch("general_manager.search.invalidation.get_setting", return_value=100),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            return_value=token,
        ),
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch_batch,
        patch(
            "general_manager.search.invalidation.dispatch_delete_documents"
        ) as dispatch_delete,
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty"
        ) as acknowledge,
    ):
        schedule_search_invalidation_work(work, source_database_alias="default")

    dispatch_batch.assert_called_once_with(
        manager_path,
        "global",
        ({"id": 2},),
    )
    dispatch_delete.assert_called_once_with(
        manager_path,
        ({"index_name": "global", "document_id": "owner:1"},),
        expected_generations={"global": token.generation},
        require_generation_fence=True,
    )
    acknowledge.assert_called_once_with(token)


def test_scheduler_invalid_batch_setting_becomes_marker_only_fallback() -> None:
    """Invalid event settings never abort and submit no targeted work."""
    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    work = SearchScheduledWork(
        upserts=SearchInvalidationPlan(targets=(scheduled_target(Owner, 1),)),
        deletes=(SearchDeleteTarget(Owner, manager_path, "global", "owner:1"),),
    )

    with (
        patch(
            "general_manager.search.invalidation.get_setting",
            return_value=0,
        ),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            return_value=dirty_token(Owner, "global", 1),
        ) as mark_dirty,
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch_batch,
        patch(
            "general_manager.search.invalidation.dispatch_delete_documents"
        ) as dispatch_delete,
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty"
        ) as acknowledge,
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        schedule_search_invalidation_work(work, source_database_alias="default")

    mark_dirty.assert_called_once_with(Owner, "global")
    dispatch_batch.assert_not_called()
    dispatch_delete.assert_not_called()
    acknowledge.assert_not_called()


def test_scheduler_empty_work_has_no_marker_callback_or_dispatch() -> None:
    """An empty lifecycle event is a complete scheduling no-op."""
    with (
        patch("general_manager.search.invalidation.get_setting") as get_setting,
        patch("general_manager.search.invalidation.transaction.on_commit") as on_commit,
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty"
        ) as mark_dirty,
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch_batch,
        patch(
            "general_manager.search.invalidation.dispatch_delete_documents"
        ) as dispatch_delete,
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(), source_database_alias="default"
        )

    get_setting.assert_not_called()
    mark_dirty.assert_not_called()
    on_commit.assert_not_called()
    dispatch_batch.assert_not_called()
    dispatch_delete.assert_not_called()


def test_scheduler_nondefault_marks_and_dispatches_only_after_source_commit() -> None:
    """Cross-database control-plane work begins inside the one source callback."""
    callbacks: list[object] = []
    events: list[str] = []

    with (
        patch("general_manager.search.invalidation.get_setting", return_value=100),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callbacks.append(callback),
        ) as on_commit,
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            side_effect=lambda *_args: (
                events.append("mark") or dirty_token(Owner, "global", 1)
            ),
        ),
        patch(
            "general_manager.search.invalidation.dispatch_index_manager_batch",
            side_effect=lambda *_args: events.append("dispatch") or 1,
        ),
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty",
            side_effect=lambda *_args: events.append("ack") or True,
        ),
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(
                upserts=SearchInvalidationPlan(
                    targets=(scheduled_target(Owner, 1, alias="secondary"),)
                )
            ),
            source_database_alias="secondary",
        )
        assert events == []
        assert len(callbacks) == 1
        callbacks[0]()  # type: ignore[operator]

    assert events == ["mark", "dispatch", "ack"]
    assert on_commit.call_args.kwargs == {"using": "secondary"}


def test_scheduler_async_ack_waits_for_every_broker_acceptance() -> None:
    """The producer clears a pair only after every chunk delay returns normally."""
    import general_manager.search.async_tasks as async_tasks

    events: list[tuple[str, object]] = []
    token = dirty_token(Owner, "global", 1)

    class BatchTask:
        def delay(self, *args: object) -> None:
            events.append(("accepted", args[2]))

    with (
        patch.object(async_tasks, "CELERY_AVAILABLE", True),
        patch.object(async_tasks, "_async_enabled", return_value=True),
        patch.object(async_tasks, "index_manager_index_batch_task", BatchTask()),
        patch("general_manager.search.invalidation.get_setting", return_value=2),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            return_value=token,
        ),
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty",
            side_effect=lambda acknowledged: (
                events.append(("ack", acknowledged)) or True
            ),
        ),
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(
                upserts=SearchInvalidationPlan(
                    targets=tuple(
                        scheduled_target(Owner, target_id) for target_id in (1, 2, 3)
                    )
                )
            ),
            source_database_alias="default",
        )

    assert events == [
        ("accepted", [{"id": 1}, {"id": 2}]),
        ("accepted", [{"id": 3}]),
        ("ack", token),
    ]


def test_scheduler_async_enqueue_failure_keeps_pair_dirty_and_continues() -> None:
    """A broker rejection leaves its token dirty while independent pairs enqueue."""
    import general_manager.search.async_tasks as async_tasks

    events: list[tuple[str, str]] = []
    owner_token = dirty_token(Owner, "global", 1)
    second_token = dirty_token(SecondOwner, "global", 2)
    tokens = iter((owner_token, second_token))

    class BatchTask:
        def delay(
            self,
            manager_path: str,
            _index_name: str,
            identifications: object,
        ) -> None:
            events.append(("enqueue", manager_path))
            if manager_path.endswith(".Owner") and identifications == [{"id": 3}]:
                raise SchedulerBrokerFailure

    with (
        patch.object(async_tasks, "CELERY_AVAILABLE", True),
        patch.object(async_tasks, "_async_enabled", return_value=True),
        patch.object(async_tasks, "index_manager_index_batch_task", BatchTask()),
        patch("general_manager.search.invalidation.get_setting", return_value=2),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            side_effect=lambda *_args: next(tokens),
        ),
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty",
            side_effect=lambda token: events.append(("ack", token.manager_path))
            or True,
        ) as acknowledge,
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        schedule_search_invalidation_work(
            SearchScheduledWork(
                upserts=SearchInvalidationPlan(
                    targets=(
                        *(scheduled_target(Owner, value) for value in (1, 2, 3)),
                        scheduled_target(SecondOwner, 9),
                    )
                )
            ),
            source_database_alias="default",
        )

    assert [event for event, _value in events].count("enqueue") == 3
    acknowledge.assert_called_once_with(second_token)


def test_scheduler_preparation_failure_marks_all_recoverable_pairs_only() -> None:
    """Hostile metadata degrades the entire event without escaping the mutation."""
    owner_path = f"{Owner.__module__}.{Owner.__name__}"
    source_path = f"{Source.__module__}.{Source.__name__}"
    hostile = SearchInvalidationTarget(
        owner_class=Owner,
        owner_path=owner_path,
        identification=HostileIdentification(),
        index_name="global",
        database_alias="default",
        canonical_key=(owner_path, "hostile", "global", "default"),
    )
    safe = scheduled_target(SecondOwner, 2, index_name="private")
    work = SearchScheduledWork(
        upserts=SearchInvalidationPlan(targets=(hostile, safe)),
        deletes=(
            SearchDeleteTarget(
                manager_class=Source,
                manager_path=source_path,
                index_name="private",
                document_id="source:1",
            ),
        ),
    )

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
            "general_manager.search.invalidation.dispatch_index_manager_batch"
        ) as dispatch_batch,
        patch(
            "general_manager.search.invalidation.dispatch_delete_documents"
        ) as dispatch_delete,
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        schedule_search_invalidation_work(work, source_database_alias="default")

    assert mark_dirty.call_args_list == [
        call(Owner, "global"),
        call(SecondOwner, "private"),
        call(Source, "private"),
    ]
    dispatch_batch.assert_not_called()
    dispatch_delete.assert_not_called()


def test_post_handler_cleans_internal_context_after_preparation_failure() -> None:
    """Preparation degradation cannot leak internal objects to later receivers."""
    import general_manager.search.invalidation as invalidation

    class SearchConfig:
        indexes = (INDEXES[0],)
        invalidation_rules: tuple[object, ...] = ()

    context: dict[str, object] = {
        "public": "safe",
        invalidation._RELATED_SEARCH_CHANGE_CONTEXT: invalidation.SearchInvalidationCapture(),
    }
    with (
        patch.object(GeneralManagerMeta, "all_classes", []),
        patch.object(invalidation, "get_search_config", return_value=SearchConfig),
        patch.object(
            invalidation,
            "_prepare_scheduled_work",
            side_effect=SchedulerPreparationFailure,
        ),
        patch.object(
            invalidation.transaction,
            "on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch.object(invalidation, "mark_search_index_dirty", return_value=None),
        patch.object(invalidation, "dispatch_index_manager_batch") as dispatch,
        patch.object(invalidation.logger, "warning"),
    ):
        invalidation._handle_search_post_change(
            Owner,
            Owner(id=1),
            action="create",
            change_context=context,
        )

    assert context == {"public": "safe"}
    dispatch.assert_not_called()


def test_async_delete_marker_failure_never_enqueues_unfenced_work() -> None:
    """A missing lifecycle token triggers recovery marking, not a stale delete."""
    import general_manager.search.async_tasks as async_tasks

    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    work = SearchScheduledWork(
        deletes=(
            SearchDeleteTarget(
                manager_class=Owner,
                manager_path=manager_path,
                index_name="global",
                document_id="owner:1",
            ),
        )
    )
    recovery_token = dirty_token(Owner, "global", 4)

    class DeleteTask:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def delay(self, *args: object) -> None:
            self.calls.append(args)

    task = DeleteTask()
    with (
        patch.object(async_tasks, "CELERY_AVAILABLE", True),
        patch.object(async_tasks, "_async_enabled", return_value=True),
        patch.object(async_tasks, "delete_documents_task", task),
        patch("general_manager.search.invalidation.get_setting", return_value=100),
        patch(
            "general_manager.search.invalidation.transaction.on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch(
            "general_manager.search.invalidation.mark_search_index_dirty",
            side_effect=(SchedulerBackendFailure, recovery_token),
        ) as mark_dirty,
        patch(
            "general_manager.search.invalidation.acknowledge_search_index_dirty"
        ) as acknowledge,
        patch("general_manager.search.invalidation.logger.warning"),
    ):
        schedule_search_invalidation_work(work, source_database_alias="default")

    assert mark_dirty.call_count == 2
    assert task.calls == []
    acknowledge.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["create", "update"])
def test_post_change_persistent_config_failure_redirties_existing_fallback(
    action: str,
) -> None:
    """Direct fallback scheduling redirties existing state without live config."""
    from general_manager.search.models import SearchIndexState

    configure(Owner, indexes=(INDEXES[0],))
    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    state = SearchIndexState.objects.create(
        manager_path=manager_path,
        index_name="global",
        schema_fingerprint="known-schema",
    )
    original_generation = state.dirty_generation

    def fail_config(_manager: type[GeneralManager]) -> None:
        raise DeclarationAccessFailure

    with (
        patch.object(GeneralManagerMeta, "all_classes", []),
        patch.object(invalidation, "get_search_config", side_effect=fail_config),
        patch(
            "general_manager.search.reconciliation.get_search_config",
            side_effect=fail_config,
        ),
        patch.object(invalidation, "get_setting", return_value=100),
        patch.object(
            invalidation.transaction,
            "on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch.object(invalidation, "dispatch_index_manager_batch") as dispatch,
        patch.object(invalidation.logger, "warning"),
    ):
        invalidation._handle_search_post_change(
            Owner,
            Owner(id=1),
            action=action,
            change_context={},
        )

    state.refresh_from_db()
    assert state.dirty_since is not None
    assert state.dirty_generation == original_generation + 1
    assert state.schema_fingerprint == "known-schema"
    dispatch.assert_not_called()


@pytest.mark.django_db
def test_captured_async_delete_persistent_config_failure_remains_dirty() -> None:
    """An unfenced captured delete is withheld while durable recovery remains."""
    import general_manager.search.async_tasks as async_tasks
    from general_manager.search.models import SearchIndexState

    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    state = SearchIndexState.objects.create(
        manager_path=manager_path,
        index_name="global",
        schema_fingerprint="known-schema",
    )
    original_generation = state.dirty_generation
    target = SearchDeleteTarget(
        manager_class=Owner,
        manager_path=manager_path,
        index_name="global",
        document_id="owner:1",
    )

    class DeleteTask:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def delay(self, *args: object) -> None:
            self.calls.append(args)

    def fail_config(_manager: type[GeneralManager]) -> None:
        raise DeclarationAccessFailure

    task = DeleteTask()
    context: dict[str, object] = {}
    with (
        patch.object(GeneralManagerMeta, "all_classes", []),
        patch.object(async_tasks, "CELERY_AVAILABLE", True),
        patch.object(async_tasks, "_async_enabled", return_value=True),
        patch.object(async_tasks, "delete_documents_task", task),
        patch.object(invalidation, "capture_delete_targets", return_value=(target,)),
        patch.object(invalidation, "get_search_config", side_effect=fail_config),
        patch(
            "general_manager.search.reconciliation.get_search_config",
            side_effect=fail_config,
        ),
        patch.object(invalidation, "get_setting", return_value=100),
        patch.object(
            invalidation.transaction,
            "on_commit",
            side_effect=lambda callback, **_kwargs: callback(),
        ),
        patch.object(invalidation.logger, "warning"),
    ):
        invalidation._handle_search_pre_change(
            Owner,
            Owner(id=1),
            action="delete",
            change_context=context,
        )
        invalidation._handle_search_post_change(
            Owner,
            None,
            action="delete",
            change_context=context,
        )

    state.refresh_from_db()
    assert state.dirty_since is not None
    assert state.dirty_generation > original_generation
    assert state.schema_fingerprint == "known-schema"
    assert task.calls == []


def test_direct_work_contains_search_config_failure() -> None:
    """Direct invalidation recovers exact dirty pairs when config access fails."""
    with (
        patch.object(
            invalidation,
            "_index_names",
            side_effect=DeclarationAccessFailure,
        ),
        patch.object(
            invalidation,
            "_config_failure_index_names",
            return_value=("global", "private"),
        ),
        patch.object(invalidation.logger, "warning") as warning,
    ):
        plan, deletes = invalidation._direct_work(
            Owner,
            Owner(id=1),
            "update",
            {},
            "default",
        )

    assert plan.dirty_fallbacks == (
        SearchInvalidationPair(Owner, "global"),
        SearchInvalidationPair(Owner, "private"),
    )
    assert deletes == ()
    warning.assert_called_once()


def test_direct_delete_config_failure_preserves_captured_targets() -> None:
    """Delete work survives a later search-configuration failure."""
    manager_path = f"{Owner.__module__}.{Owner.__name__}"
    target = SearchDeleteTarget(
        manager_class=Owner,
        manager_path=manager_path,
        index_name="global",
        document_id="owner:1",
    )
    context = {
        invalidation._DIRECT_SEARCH_CHANGE_CONTEXT: (
            invalidation._PendingDirectSearchChange(
                action="delete",
                delete_targets=(target,),
            )
        )
    }

    with patch.object(
        invalidation,
        "_index_names",
        side_effect=DeclarationAccessFailure,
    ):
        plan, deletes = invalidation._direct_work(
            Owner,
            None,
            "delete",
            context,
            "default",
        )

    assert plan == SearchInvalidationPlan()
    assert deletes == (target,)


def test_direct_work_contains_config_failure_recovery_failure() -> None:
    """Failure of both config paths cannot escape the business mutation."""
    with (
        patch.object(
            invalidation,
            "_index_names",
            side_effect=DeclarationAccessFailure,
        ),
        patch.object(
            invalidation,
            "_config_failure_index_names",
            side_effect=DeclarationAccessFailure,
        ),
        patch.object(invalidation.logger, "warning") as warning,
    ):
        plan, deletes = invalidation._direct_work(
            Owner,
            Owner(id=1),
            "create",
            {},
            "default",
        )

    assert plan == SearchInvalidationPlan()
    assert deletes == ()
    assert warning.call_count == 2
