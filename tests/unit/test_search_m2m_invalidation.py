"""Unit tests for the exact-through M2M search invalidation bridge."""

from __future__ import annotations

from unittest.mock import patch

from django.db import models
from django.dispatch import Signal

from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.search.config import (
    IndexConfig,
    SearchConfigSpec,
    SearchInvalidationRule,
)
from general_manager.search.m2m_invalidation import (
    M2MInvalidationBinding,
    _dispatch_uid,
    _schedule_owner_ids,
    configure_search_m2m_invalidation,
    compile_m2m_invalidation_bindings,
    handle_m2m_invalidation,
)


class UnitM2MSource(models.Model):
    """Signal endpoint used without database I/O in unit tests."""

    class Meta:
        app_label = "general_manager"


class UnitM2MOwner(models.Model):
    """Owner endpoint with a real auto-created Django through model."""

    sources = models.ManyToManyField(UnitM2MSource)

    class Meta:
        app_label = "general_manager"


class UnitM2MUnrelatedOwner(models.Model):
    """Different relation whose through sender must never match a binding."""

    sources = models.ManyToManyField(UnitM2MSource)

    class Meta:
        app_label = "general_manager"


class UnitOwnerToFieldModel(models.Model):
    code = models.CharField(max_length=32, unique=True)
    sources = models.ManyToManyField(
        UnitM2MSource,
        through="UnitOwnerToFieldThrough",
    )

    class Meta:
        app_label = "general_manager"


class UnitOwnerToFieldThrough(models.Model):
    owner = models.ForeignKey(
        UnitOwnerToFieldModel,
        to_field="code",
        on_delete=models.CASCADE,
    )
    source = models.ForeignKey(UnitM2MSource, on_delete=models.CASCADE)

    class Meta:
        app_label = "general_manager"


class UnitSourceToFieldModel(models.Model):
    code = models.CharField(max_length=32, unique=True)

    class Meta:
        app_label = "general_manager"


class UnitSourceToFieldOwnerModel(models.Model):
    sources = models.ManyToManyField(
        UnitSourceToFieldModel,
        through="UnitSourceToFieldThrough",
    )

    class Meta:
        app_label = "general_manager"


class UnitSourceToFieldThrough(models.Model):
    owner = models.ForeignKey(UnitSourceToFieldOwnerModel, on_delete=models.CASCADE)
    source = models.ForeignKey(
        UnitSourceToFieldModel,
        to_field="code",
        on_delete=models.CASCADE,
    )

    class Meta:
        app_label = "general_manager"


class UnitOwnerToFieldInterface(OrmInterfaceBase[UnitOwnerToFieldModel]):
    _model = UnitOwnerToFieldModel


class UnitM2MSourceInterface(OrmInterfaceBase[UnitM2MSource]):
    _model = UnitM2MSource


class UnitSourceToFieldInterface(OrmInterfaceBase[UnitSourceToFieldModel]):
    _model = UnitSourceToFieldModel


class UnitSourceToFieldOwnerInterface(OrmInterfaceBase[UnitSourceToFieldOwnerModel]):
    _model = UnitSourceToFieldOwnerModel


class UnitM2MSourceManager(GeneralManager):
    """Source manager marker for the immutable binding."""


class UnitM2MOwnerManager(GeneralManager):
    """Owner manager marker for the immutable binding."""


def binding() -> M2MInvalidationBinding:
    """Build the exact auto-through binding used by signal unit tests."""
    field = UnitM2MOwner._meta.get_field("sources")
    assert isinstance(field, models.ManyToManyField)
    return M2MInvalidationBinding(
        owner_manager=UnitM2MOwnerManager,
        source_manager=UnitM2MSourceManager,
        index_names=("global", "secondary"),
        owner_model=UnitM2MOwner,
        source_model=UnitM2MSource,
        relation_name="sources",
        through_model=field.remote_field.through,
        owner_through_field=field.m2m_field_name(),
        source_through_field=field.m2m_reverse_field_name(),
    )


def test_forward_post_add_schedules_copied_owner_id_for_each_index() -> None:
    """A forward add targets the owner instance, never the related source ids."""
    owner = UnitM2MOwner(pk=17)
    related_ids = {2, 3}

    with patch(
        "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
    ) as schedule:
        handle_m2m_invalidation(
            binding(),
            sender=binding().through_model,
            instance=owner,
            action="post_add",
            reverse=False,
            model=UnitM2MSource,
            pk_set=related_ids,
            using="default",
        )

    work = schedule.call_args.args[0]
    assert [dict(target.identification) for target in work.upserts.targets] == [
        {"id": 17},
        {"id": 17},
    ]
    assert [target.index_name for target in work.upserts.targets] == [
        "global",
        "secondary",
    ]
    assert all(target.identification is not owner for target in work.upserts.targets)
    schedule.assert_called_once()
    assert schedule.call_args.kwargs == {"source_database_alias": "default"}
    assert related_ids == {2, 3}


def test_reverse_post_remove_deduplicates_owner_ids_per_event() -> None:
    """Reverse removal targets bounded owner ids from the public pk_set."""
    source = UnitM2MSource(pk=9)

    with patch(
        "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
    ) as schedule:
        handle_m2m_invalidation(
            binding(),
            sender=binding().through_model,
            instance=source,
            action="post_remove",
            reverse=True,
            model=UnitM2MOwner,
            pk_set={5, 3},
            using="replica",
        )

    work = schedule.call_args.args[0]
    assert {
        (target.index_name, target.identification["id"])
        for target in work.upserts.targets
    } == {("global", 3), ("secondary", 3), ("global", 5), ("secondary", 5)}
    assert schedule.call_args.kwargs == {"source_database_alias": "replica"}


def test_signal_with_wrong_endpoint_model_is_ignored() -> None:
    """Exact sender registration is reinforced by endpoint validation."""
    with patch(
        "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
    ) as schedule:
        handle_m2m_invalidation(
            binding(),
            sender=binding().through_model,
            instance=UnitM2MOwner(pk=1),
            action="post_add",
            reverse=False,
            model=UnitM2MOwner,
            pk_set={2},
            using="default",
        )

    schedule.assert_not_called()


def test_bounded_reverse_add_overflow_marks_only_exact_pairs_dirty() -> None:
    """An oversized event emits no targets and degrades its exact indexes."""
    with (
        patch(
            "general_manager.search.m2m_invalidation.get_search_invalidation_max_targets",
            return_value=2,
        ),
        patch(
            "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
        ) as schedule,
    ):
        handle_m2m_invalidation(
            binding(),
            sender=binding().through_model,
            instance=UnitM2MSource(pk=8),
            action="post_add",
            reverse=True,
            model=UnitM2MOwner,
            pk_set={1, 2, 3},
            using="default",
        )

    work = schedule.call_args.args[0]
    assert work.upserts.targets == ()
    assert {
        (pair.owner_class, pair.index_name) for pair in work.upserts.dirty_fallbacks
    } == {
        (UnitM2MOwnerManager, "global"),
        (UnitM2MOwnerManager, "secondary"),
    }


def test_post_clear_is_ignored_because_peers_are_captured_in_pre_clear() -> None:
    """The bridge never guesses peers after Django has deleted through rows."""
    with patch(
        "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
    ) as schedule:
        handle_m2m_invalidation(
            binding(),
            sender=binding().through_model,
            instance=UnitM2MOwner(pk=1),
            action="post_clear",
            reverse=False,
            model=UnitM2MSource,
            pk_set=None,
            using="default",
        )

    schedule.assert_not_called()


def test_target_collection_consumes_at_most_limit_plus_one_ids() -> None:
    """Even a hostile iterable cannot make one signal event grow unbounded."""
    yielded = 0

    def owner_ids():
        nonlocal yielded
        for owner_id in range(10_000):
            yielded += 1
            yield owner_id

    with (
        patch(
            "general_manager.search.m2m_invalidation.get_search_invalidation_max_targets",
            return_value=2,
        ),
        patch(
            "general_manager.search.m2m_invalidation.schedule_search_invalidation_work"
        ),
    ):
        _schedule_owner_ids(binding(), owner_ids(), using="default")

    assert yielded == 3


def test_configuration_is_idempotent_for_the_exact_through_sender() -> None:
    """Repeated startup calls retain one receiver for one stable binding."""
    signal = Signal()
    exact_binding = binding()
    with (
        patch(
            "general_manager.search.m2m_invalidation.compile_m2m_invalidation_bindings",
            return_value=(exact_binding,),
        ),
        patch("general_manager.search.m2m_invalidation.m2m_changed", signal),
        patch(
            "general_manager.search.m2m_invalidation.handle_m2m_invalidation"
        ) as handle,
    ):
        configure_search_m2m_invalidation()
        configure_search_m2m_invalidation()
        signal.send(
            sender=exact_binding.through_model,
            instance=UnitM2MOwner(pk=1),
            action="post_add",
            reverse=False,
            model=UnitM2MSource,
            pk_set={2},
            using="default",
        )

    handle.assert_called_once()


def test_configuration_ignores_a_different_actual_through_sender() -> None:
    """Signal registration never routes merely because endpoints look alike."""
    signal = Signal()
    exact_binding = binding()
    unrelated_field = UnitM2MUnrelatedOwner._meta.get_field("sources")
    assert isinstance(unrelated_field, models.ManyToManyField)
    unrelated_through = unrelated_field.remote_field.through
    assert unrelated_through is not exact_binding.through_model

    with (
        patch(
            "general_manager.search.m2m_invalidation.compile_m2m_invalidation_bindings",
            return_value=(exact_binding,),
        ),
        patch("general_manager.search.m2m_invalidation.m2m_changed", signal),
        patch(
            "general_manager.search.m2m_invalidation.handle_m2m_invalidation"
        ) as handle,
    ):
        configure_search_m2m_invalidation()
        signal.send(
            sender=unrelated_through,
            instance=UnitM2MOwner(pk=1),
            action="post_add",
            reverse=False,
            model=UnitM2MSource,
            pk_set={2},
            using="default",
        )

    handle.assert_not_called()


def test_dispatch_uid_does_not_collide_for_ambiguous_index_names() -> None:
    """Structured UIDs retain both bindings when comma joins would collide."""
    signal = Signal()
    first = binding()
    first = M2MInvalidationBinding(**{**first.__dict__, "index_names": ("a,b", "c")})
    second = binding()
    second = M2MInvalidationBinding(**{**second.__dict__, "index_names": ("a", "b,c")})
    assert first.index_names != second.index_names
    assert _dispatch_uid(first) != _dispatch_uid(second)

    with (
        patch(
            "general_manager.search.m2m_invalidation.compile_m2m_invalidation_bindings",
            return_value=(first, second),
        ),
        patch("general_manager.search.m2m_invalidation.m2m_changed", signal),
        patch(
            "general_manager.search.m2m_invalidation.handle_m2m_invalidation"
        ) as handle,
    ):
        configure_search_m2m_invalidation()
        signal.send(
            sender=first.through_model,
            instance=UnitM2MOwner(pk=1),
            action="post_add",
            reverse=False,
            model=UnitM2MSource,
            pk_set={2},
            using="default",
        )

    assert [call.args[0] for call in handle.call_args_list] == [first, second]


def test_compiler_skips_owner_through_fk_targeting_non_primary_key() -> None:
    config = SearchConfigSpec(
        indexes=(IndexConfig(name="global", fields=()),),
        invalidation_rules=(
            SearchInvalidationRule(source=UnitM2MSourceManager, relation="sources"),
        ),
    )

    type.__setattr__(UnitM2MOwnerManager, "Interface", UnitOwnerToFieldInterface)
    type.__setattr__(UnitM2MSourceManager, "Interface", UnitM2MSourceInterface)
    try:
        with (
            patch(
                "general_manager.search.m2m_invalidation.get_search_config",
                return_value=config,
            ),
            patch("general_manager.search.m2m_invalidation.logger.warning") as warning,
        ):
            bindings = compile_m2m_invalidation_bindings([UnitM2MOwnerManager])
    finally:
        type.__delattr__(UnitM2MSourceManager, "Interface")
        type.__delattr__(UnitM2MOwnerManager, "Interface")

    assert bindings == ()
    warning.assert_called_once()


def test_compiler_skips_source_through_fk_targeting_non_primary_key() -> None:
    config = SearchConfigSpec(
        indexes=(IndexConfig(name="global", fields=()),),
        invalidation_rules=(
            SearchInvalidationRule(source=UnitM2MSourceManager, relation="sources"),
        ),
    )

    type.__setattr__(
        UnitM2MOwnerManager,
        "Interface",
        UnitSourceToFieldOwnerInterface,
    )
    type.__setattr__(UnitM2MSourceManager, "Interface", UnitSourceToFieldInterface)
    try:
        with (
            patch(
                "general_manager.search.m2m_invalidation.get_search_config",
                return_value=config,
            ),
            patch("general_manager.search.m2m_invalidation.logger.warning") as warning,
        ):
            bindings = compile_m2m_invalidation_bindings([UnitM2MOwnerManager])
    finally:
        type.__delattr__(UnitM2MSourceManager, "Interface")
        type.__delattr__(UnitM2MOwnerManager, "Interface")

    assert bindings == ()
    warning.assert_called_once()


def test_invalid_binding_is_logged_and_skipped_without_startup_error() -> None:
    """Malformed relation metadata remains a system-check concern."""

    class SearchConfig:
        indexes = ()
        invalidation_rules = (
            # The manager is intentionally non-ORM and has no relation metadata.
            SearchInvalidationRule(
                source=UnitM2MSourceManager,
                relation="sources",
            ),
        )

    with (
        patch.object(UnitM2MOwnerManager, "SearchConfig", SearchConfig, create=True),
        patch("general_manager.search.m2m_invalidation.logger.warning") as warning,
    ):
        bindings = compile_m2m_invalidation_bindings([UnitM2MOwnerManager])

    assert bindings == ()
    warning.assert_called_once()
