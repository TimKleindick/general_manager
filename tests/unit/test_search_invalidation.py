"""Unit tests for bounded related-search invalidation planning."""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar
from unittest.mock import patch

import pytest

from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.config import (
    IndexConfig,
    SearchChange,
    SearchInvalidationRule,
)
from general_manager.search.invalidation import (
    SearchInvalidationPair,
    finalize_search_invalidation_capture,
    resolve_search_invalidation_phase,
)
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
