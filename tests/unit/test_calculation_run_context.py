from unittest import mock
from types import ModuleType, SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from general_manager.api import as_of
from general_manager.as_of import as_of_cache_fingerprint
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import DependencyCacheHit
from general_manager.cache.dependency_index import Dependency
from general_manager.cache.dependency_publish import (
    CacheComputeLease,
    PendingDependencyCachePublication,
)
from general_manager.cache.run_context import (
    CalculationRunContext,
    current_calculation_run_context,
    ensure_calculation_run_context,
)
from general_manager.cache.run_context_lru import (
    estimate_cache_entry_size,
    run_context_cache_budget,
)


class DummyDependencyCacheBackend:
    def get(self, key: str, default: object = None) -> object:
        return default

    def set(self, key: str, value: object, timeout: int | None = None) -> None:
        return None


def make_pending_publication(cache_key: str) -> PendingDependencyCachePublication:
    return PendingDependencyCachePublication(
        cache_key=cache_key,
        result=f"value:{cache_key}",
        dependencies=frozenset({("Project", "identification", cache_key)}),
        cache_backend=DummyDependencyCacheBackend(),
        timeout=None,
        started_generation=0,
        lease=CacheComputeLease(
            key=f"dependency_cache_compute_lock:{cache_key}",
            token=f"token:{cache_key}",
        ),
    )


class CountingHashKey:
    def __init__(self) -> None:
        self.hash_calls = 0

    def __hash__(self) -> int:
        self.hash_calls += 1
        return 1

    def __eq__(self, other: object) -> bool:
        return self is other


class CalculationFailed(RuntimeError):
    """Test exception used to exercise context cleanup."""


class PublishFailed(RuntimeError):
    """Test exception used to exercise cleanup after publish failure."""


class LeaseReleaseFailed(RuntimeError):
    """Test exception used to exercise cleanup after lease-release failure."""


def raise_after_buffering(
    context: CalculationRunContext,
    entry: PendingDependencyCachePublication,
) -> None:
    context.buffer_dependency_cache_publication(entry)
    raise CalculationFailed


def test_context_is_active_only_inside_with_block() -> None:
    assert current_calculation_run_context() is None


def test_nested_calculation_run_context_restores_outer_context() -> None:
    with CalculationRunContext() as outer:
        outer.set("scope", "outer")

        with CalculationRunContext() as inner:
            assert current_calculation_run_context() is inner
            assert inner.get("scope") is None
            inner.set("scope", "inner")

        assert current_calculation_run_context() is outer
        assert outer.get("scope") == "outer"

    assert current_calculation_run_context() is None


def test_reentering_same_context_preserves_state_until_outer_exit() -> None:
    context = CalculationRunContext()

    with context as outer:
        outer.set("answer", 42)

        with context as inner:
            assert inner is outer
            assert current_calculation_run_context() is context
            assert inner.get("answer") == 42

        assert current_calculation_run_context() is context
        assert outer.get("answer") == 42

    assert current_calculation_run_context() is None
    assert context.get("answer") is None


def test_reused_run_context_reregisters_for_later_budget_rebuild() -> None:
    with override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": None}):
        context = CalculationRunContext()
        with context:
            pass

        with context:
            context.set("key", "value")

            with (
                override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
                CalculationRunContext(),
            ):
                assert context.get("key") is None
                assert run_context_cache_budget.estimated_bytes == 0


def test_exit_without_enter_is_noop() -> None:
    context = CalculationRunContext()

    context.__exit__(None, None, None)

    assert current_calculation_run_context() is None

    with CalculationRunContext() as ctx:
        assert current_calculation_run_context() is ctx

    assert current_calculation_run_context() is None


def test_get_or_set_reuses_loaded_value_inside_context() -> None:
    calls = 0

    def loader() -> int:
        nonlocal calls
        calls += 1
        return 42

    with CalculationRunContext() as ctx:
        assert ctx.get_or_set(("answer",), loader) == 42
        assert ctx.get_or_set(("answer",), loader) == 42

    assert calls == 1


def test_get_or_set_hit_uses_single_mapping_lookup() -> None:
    key = CountingHashKey()

    with CalculationRunContext() as ctx:
        assert ctx.get_or_set(key, lambda: 42) == 42
        key.hash_calls = 0

        assert ctx.get_or_set(key, lambda: 99) == 42

    assert key.hash_calls == 1


def test_get_or_set_does_not_cache_failed_loader() -> None:
    calls = 0

    def loader() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CalculationFailed
        return 42

    with CalculationRunContext() as ctx:
        with pytest.raises(CalculationFailed):
            ctx.get_or_set(("answer",), loader)

        assert ctx.get_or_set(("answer",), loader) == 42

    assert calls == 2


def test_zero_run_context_budget_disables_value_retention() -> None:
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return "loaded"

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        CalculationRunContext() as context,
    ):
        assert context.get_or_set("key", loader) == "loaded"
        assert context.get_or_set("key", loader) == "loaded"

    assert calls == 2


def test_run_context_budget_evicts_lru_value_across_contexts() -> None:
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    with override_settings(
        GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": entry_size * 2}
    ):
        with CalculationRunContext() as first, CalculationRunContext() as second:
            first.set("a", "A")
            second.set("b", "B")
            assert first.get("a") == "A"
            second.set("c", "C")

            assert first.get("a") == "A"
            assert second.get("b") is None
            assert second.get("c") == "C"


def test_run_context_does_not_retain_value_larger_than_budget() -> None:
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        value = context.get_or_set("large", lambda: b"x" * 1024)

        assert value == b"x" * 1024
        assert context.get("large") is None


def test_finite_budget_set_does_not_invoke_replaced_dict_descriptor() -> None:
    descriptor_accesses: list[str] = []

    class Value:
        @property
        def __dict__(self) -> dict[str, object]:
            descriptor_accesses.append("__dict__")
            raise AssertionError("unexpected instance dictionary lookup")  # noqa: TRY003

    value = Value()
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 10_000}),
        CalculationRunContext() as context,
    ):
        result = context.set("key", value)

        assert result is None
        assert context.get("key") is value

    assert descriptor_accesses == []


def test_finite_budget_set_does_not_invoke_replaced_class_descriptor() -> None:
    descriptor_accesses: list[str] = []

    class Value:
        @property
        def __class__(self) -> type[object]:
            descriptor_accesses.append("__class__")
            raise AssertionError("unexpected instance class lookup")  # noqa: TRY003

    value = Value()
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 10_000}),
        CalculationRunContext() as context,
    ):
        result = context.set("key", value)

        assert result is None
        assert context.get("key") is value

    assert descriptor_accesses == []


def test_finite_budget_set_ignores_unrelated_native_dict_descriptor() -> None:
    class Value:
        __dict__ = object.__dict__["__class__"]

    value = Value()
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.set("key", value)

        assert result is None
        assert context.get("key") is value


def test_finite_budget_get_or_set_does_not_invoke_replaced_slot_descriptor() -> None:
    descriptor_accesses: list[str] = []

    class Value:
        __slots__ = ["payload"]

        def __init__(self) -> None:
            self.payload = "safe"

        @property
        def hostile(self) -> object:
            descriptor_accesses.append("hostile")
            raise AssertionError("unexpected slot descriptor lookup")  # noqa: TRY003

    Value.__slots__[0] = "hostile"
    value = Value()
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 10_000}),
        CalculationRunContext() as context,
    ):
        result = context.get_or_set("key", lambda: value)

        assert result is value
        assert context.get("key") is value

    assert descriptor_accesses == []


def test_finite_budget_get_or_set_ignores_member_descriptor_from_base_slot() -> None:
    class Base:
        __slots__ = ("base_payload",)

    class Value(Base):
        __slots__ = ("payload",)

    value = Value()
    value.base_payload = bytearray(1024)
    value.payload = None
    base_payload_descriptor = Base.__dict__["base_payload"]
    Value.payload = base_payload_descriptor
    Base.base_payload = None

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.get_or_set("key", lambda: value)

        assert result is value
        assert context.get("key") is value


def test_finite_budget_rejects_remaining_native_alias_payload() -> None:
    class Value:
        __slots__ = ("_Renamed__payload", "__payload")

        def __init__(self) -> None:
            self.__payload = None
            self._Renamed__payload = bytearray(1024)

    value = Value()
    Value._Value__payload = None
    Value.__slots__ = ()
    Value.__name__ = "Renamed"

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.get_or_set("key", lambda: value)

        assert result is value
        assert context.get("key") is None


def test_finite_budget_rejects_slots_despite_changed_name_and_qualname() -> None:
    class Value:
        __qualname__ = "Renamed"
        __slots__ = ("_Renamed__payload", "__payload")

        def __init__(self) -> None:
            self.__payload = bytearray(1024)
            self._Renamed__payload = None

    value = Value()
    Value.__slots__ = ()
    Value.__name__ = "Renamed"

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.get_or_set("key", lambda: value)

        assert result is value
        assert context.get("key") is None


def test_finite_budget_rejects_large_same_suffix_private_slot() -> None:
    class Value:
        __slots__ = ("_Other__payload", "__payload")

        def __init__(self) -> None:
            self.__payload = bytearray(1024)
            self._Other__payload = None

    value = Value()
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.get_or_set("key", lambda: value)

        assert result is value
        assert context.get("key") is None


def test_finite_budget_rejects_oversized_dotted_class_private_slot() -> None:
    value_type = type("A.B", (), {"__slots__": ("__payload",)})
    value = value_type()
    setattr(value, "_A.B__payload", bytearray(1024))

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.get_or_set("key", lambda: value)

        assert result is value
        assert context.get("key") is None


@pytest.mark.parametrize("metadata_name", ["__mro__", "__dict__"])
def test_finite_budget_set_avoids_shallow_leaf_metaclass_metadata_descriptors(
    metadata_name: str,
) -> None:
    metadata_accesses: list[str] = []

    class HostileMetadataDescriptor:
        def __get__(self, instance: object, owner: object = None) -> object:
            metadata_accesses.append(metadata_name)
            raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                f"unexpected metaclass descriptor lookup: {metadata_name}"
            )

        def __set__(self, instance: object, value: object) -> None:
            raise AssertionError(  # noqa: TRY003 - test double reports the hook.
                f"unexpected metaclass descriptor assignment: {metadata_name}"
            )

    hostile_meta = type(
        "HostileMeta",
        (type,),
        {metadata_name: HostileMetadataDescriptor()},
    )

    class Value(ModuleType, metaclass=hostile_meta):
        pass

    value = Value("hostile")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        result = context.set("key", value)

        assert result is None
        assert context.get("key") is value

    assert metadata_accesses == []


def test_outermost_run_context_exit_releases_process_accounting() -> None:
    with override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 10_000}):
        context = CalculationRunContext()
        with context:
            context.set("key", "value")
            assert run_context_cache_budget.estimated_bytes > 0

            with context:
                assert run_context_cache_budget.estimated_bytes > 0

            assert run_context_cache_budget.estimated_bytes > 0

        assert run_context_cache_budget.estimated_bytes == 0


@override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": True})
def test_run_context_rejects_invalid_memory_budget() -> None:
    with pytest.raises(ImproperlyConfigured, match="RUN_CONTEXT_CACHE_MAX_BYTES"):
        CalculationRunContext()


def test_run_context_budget_keeps_historical_namespaces_independent() -> None:
    with as_of("2022-01-01"):
        first_fingerprint = as_of_cache_fingerprint()
    with as_of("2022-01-02"):
        second_fingerprint = as_of_cache_fingerprint()
    assert first_fingerprint is not None
    assert second_fingerprint is not None
    first_size = estimate_cache_entry_size(
        ("as_of", first_fingerprint, "key"), "A", stop_after=None
    )
    second_size = estimate_cache_entry_size(
        ("as_of", second_fingerprint, "key"), "B", stop_after=None
    )

    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": first_size + second_size}
        ),
        CalculationRunContext() as context,
    ):
        with as_of("2022-01-01"):
            context.set("key", "A")
        with as_of("2022-01-02"):
            context.set("key", "B")
        with as_of("2022-01-01"):
            assert context.get("key") == "A"
        with as_of("2022-01-03"):
            context.set("key", "C")

        with as_of("2022-01-01"):
            assert context.get("key") == "A"
        with as_of("2022-01-02"):
            assert context.get("key") is None


def test_ensure_calculation_run_context_reuses_active_context() -> None:
    with CalculationRunContext() as outer:
        with ensure_calculation_run_context() as inner:
            assert inner is outer
            inner.set("answer", 42)

        assert current_calculation_run_context() is outer
        assert outer.get("answer") == 42

    assert current_calculation_run_context() is None


def test_ensure_calculation_run_context_creates_context_when_absent() -> None:
    assert current_calculation_run_context() is None

    with ensure_calculation_run_context() as context:
        assert current_calculation_run_context() is context

    assert current_calculation_run_context() is None


def test_public_storage_helpers_store_and_check_values() -> None:
    with CalculationRunContext() as ctx:
        assert ctx.get("missing") is None
        assert ctx.get("missing", "fallback") == "fallback"
        assert not ctx.has("answer")
        assert "answer" not in ctx

        ctx.set("answer", 42)

        assert ctx.get("answer") == 42
        assert ctx.has("answer")
        assert "answer" in ctx


def test_dependency_cache_prefetch_hits_are_available_inside_context() -> None:
    hit = DependencyCacheHit(
        value="ready",
        dependencies=frozenset({("Project", "identification", '{"id": 1}')}),
    )

    with CalculationRunContext() as context:
        context.set_dependency_cache_hits({"cache-key": hit})

        assert context.get_dependency_cache_hit("cache-key") == hit
        assert context.get_dependency_cache_hit("missing", "fallback") == "fallback"


def test_dependency_cache_prefetch_hits_are_cleared_on_exit() -> None:
    hit = DependencyCacheHit(value=10, dependencies=frozenset())
    context = CalculationRunContext()

    with context:
        context.set_dependency_cache_hits({"cache-key": hit})
        assert context.get_dependency_cache_hit("cache-key") == hit

    assert context.get_dependency_cache_hit("cache-key", None) is None


def test_dependency_cache_hits_share_lru_recency() -> None:
    first = DependencyCacheHit(value="A", dependencies=frozenset())
    second = DependencyCacheHit(value="B", dependencies=frozenset())
    third = DependencyCacheHit(value="C", dependencies=frozenset())
    entry_size = estimate_cache_entry_size("cache-a", first, stop_after=None)
    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": entry_size * 2}
        ),
        CalculationRunContext() as context,
    ):
        context.set_dependency_cache_hits({"cache-a": first, "cache-b": second})
        assert context.get_dependency_cache_hit("cache-a") == first
        context.set_dependency_cache_hits({"cache-c": third})

        assert context.get_dependency_cache_hit("cache-a") == first
        assert context.get_dependency_cache_hit("cache-b") is None
        assert context.get_dependency_cache_hit("cache-c") == third


def test_run_values_and_dependency_hits_share_global_lru_across_contexts() -> None:
    first_hit = DependencyCacheHit(value="A", dependencies=frozenset())
    second_hit = DependencyCacheHit(value="B", dependencies=frozenset())
    hit_size = estimate_cache_entry_size("cache-a", first_hit, stop_after=None)
    value_size = estimate_cache_entry_size("ordinary", "value", stop_after=None)
    two_entry_budget = hit_size + max(hit_size, value_size)
    with (
        override_settings(
            GENERAL_MANAGER={
                "RUN_CONTEXT_CACHE_MAX_BYTES": two_entry_budget,
            }
        ),
        CalculationRunContext() as first_context,
        CalculationRunContext() as second_context,
    ):
        second_context.set_dependency_cache_hits({"cache-a": first_hit})
        first_context.set("ordinary", "value")
        assert second_context.get_dependency_cache_hit("cache-a") == first_hit

        second_context.set_dependency_cache_hits({"cache-b": second_hit})

        assert first_context.get("ordinary") is None
        assert second_context.get_dependency_cache_hit("cache-a") == first_hit
        assert second_context.get_dependency_cache_hit("cache-b") == second_hit


def test_pending_dependency_publication_hit_is_pinned_until_flush() -> None:
    entry = make_pending_publication("cache-a")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ),
        mock.patch("general_manager.cache.dependency_publish.release_compute_lease"),
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)
        assert context.get_dependency_cache_hit("cache-a") is not None

        context.flush_dependency_cache_publications()

        assert context.get_dependency_cache_hit("cache-a") is None


def test_publish_failure_unpins_hit_and_preserves_original_error() -> None:
    entry = make_pending_publication("cache-a")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries",
            side_effect=PublishFailed,
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)

        with pytest.raises(PublishFailed):
            context.flush_dependency_cache_publications()

        assert context._dependency_cache_pending_publications == {}
        assert context.get_dependency_cache_hit("cache-a") is None
        publish_batch.assert_called_once_with((entry,))
        release_lease.assert_called_once_with(entry.lease)


def test_publish_failure_tracks_unpinned_hit_under_finite_budget() -> None:
    entry = make_pending_publication("cache-a")
    expected_hit = DependencyCacheHit(
        value=entry.result,
        dependencies=entry.dependencies,
    )
    entry_size = estimate_cache_entry_size(
        entry.cache_key,
        expected_hit,
        stop_after=None,
    )
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": entry_size}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries",
            side_effect=PublishFailed,
        ),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)

        with pytest.raises(PublishFailed):
            context.flush_dependency_cache_publications()

        assert context._dependency_cache_pending_publications == {}
        assert context.get_dependency_cache_hit(entry.cache_key) == expected_hit
        assert run_context_cache_budget.estimated_bytes == entry_size
        release_lease.assert_called_once_with(entry.lease)


def test_release_failure_supersedes_publish_failure_and_unpins_hit() -> None:
    entry = make_pending_publication("cache-a")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries",
            side_effect=PublishFailed,
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease",
            side_effect=LeaseReleaseFailed,
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)

        with pytest.raises(LeaseReleaseFailed):
            context.flush_dependency_cache_publications()

        assert context._dependency_cache_pending_publications == {}
        assert context.get_dependency_cache_hit("cache-a") is None
        publish_batch.assert_called_once_with((entry,))
        release_lease.assert_called_once_with(entry.lease)


@pytest.mark.parametrize(
    ("cleanup_method", "publishes"),
    [
        ("flush_dependency_cache_publications", True),
        ("discard_dependency_cache_publications", False),
    ],
)
def test_release_failure_stops_later_releases_but_unpins_every_removed_hit(
    cleanup_method: str,
    publishes: bool,
) -> None:
    first = make_pending_publication("cache-a")
    second = make_pending_publication("cache-b")
    third = make_pending_publication("cache-c")

    def release_until_failure(lease: CacheComputeLease) -> None:
        if lease == second.lease:
            raise LeaseReleaseFailed

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease",
            side_effect=release_until_failure,
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        for entry in (first, second, third):
            context.buffer_dependency_cache_publication(entry)

        with pytest.raises(LeaseReleaseFailed):
            getattr(context, cleanup_method)()

        assert context._dependency_cache_pending_publications == {}
        assert all(
            context.get_dependency_cache_hit(entry.cache_key) is None
            for entry in (first, second, third)
        )
        assert release_lease.call_args_list == [
            mock.call(first.lease),
            mock.call(second.lease),
        ]
        if publishes:
            publish_batch.assert_called_once_with((first, second, third))
        else:
            publish_batch.assert_not_called()


@pytest.mark.parametrize(
    ("cleanup_method", "publishes"),
    [
        ("flush_dependency_cache_publications", True),
        ("discard_dependency_cache_publications", False),
    ],
)
def test_first_release_failure_still_unpins_every_removed_hit(
    cleanup_method: str,
    publishes: bool,
) -> None:
    first = make_pending_publication("cache-a")
    second = make_pending_publication("cache-b")
    third = make_pending_publication("cache-c")
    entries = (first, second, third)
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease",
            side_effect=LeaseReleaseFailed,
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        for entry in entries:
            context.buffer_dependency_cache_publication(entry)

        with pytest.raises(LeaseReleaseFailed):
            getattr(context, cleanup_method)()

        assert context._dependency_cache_pending_publications == {}
        assert all(
            context.get_dependency_cache_hit(entry.cache_key) is None
            for entry in entries
        )
        release_lease.assert_called_once_with(first.lease)
        if publishes:
            publish_batch.assert_called_once_with(entries)
        else:
            publish_batch.assert_not_called()


def test_discard_dependency_state_clears_hits_after_release_failure() -> None:
    prefetched = DependencyCacheHit(value="ready", dependencies=frozenset())
    first = make_pending_publication("cache-a")
    second = make_pending_publication("cache-b")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 100_000}),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease",
            side_effect=LeaseReleaseFailed,
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.set_dependency_cache_hits({"prefetched": prefetched})
        context.buffer_dependency_cache_publication(first)
        context.buffer_dependency_cache_publication(second)
        assert run_context_cache_budget.estimated_bytes > 0

        with pytest.raises(LeaseReleaseFailed):
            context.discard_dependency_cache_state()

        assert context._dependency_cache_pending_publications == {}
        assert context._dependency_cache_hits == {}
        assert run_context_cache_budget.estimated_bytes == 0
        release_lease.assert_called_once_with(first.lease)


def test_pending_dependency_publication_hit_becomes_evictable_after_discard() -> None:
    entry = make_pending_publication("cache-a")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)
        assert context.get_dependency_cache_hit("cache-a") is not None

        context.discard_dependency_cache_publications()

        assert context.get_dependency_cache_hit("cache-a") is None
        release_lease.assert_called_once_with(entry.lease)


def test_buffered_dependency_cache_publication_is_visible_as_run_hit() -> None:
    entry = make_pending_publication("cache-a")

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ),
        mock.patch("general_manager.cache.dependency_publish.release_compute_lease"),
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)

        hit = context.get_dependency_cache_hit("cache-a")

    assert isinstance(hit, DependencyCacheHit)
    assert hit.value == "value:cache-a"
    assert hit.dependencies == frozenset({("Project", "identification", "cache-a")})


def test_dependency_cache_publications_flush_on_clean_exit() -> None:
    entry = make_pending_publication("cache-a")

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
    ):
        with CalculationRunContext() as context:
            context.buffer_dependency_cache_publication(entry)

    publish_batch.assert_called_once_with((entry,))
    release_lease.assert_called_once_with(entry.lease)


def test_context_cleans_state_when_flush_raises() -> None:
    entry = make_pending_publication("cache-a")
    context = CalculationRunContext()

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries",
            side_effect=PublishFailed,
        ),
        mock.patch("general_manager.cache.dependency_publish.release_compute_lease"),
        pytest.raises(PublishFailed),
    ):
        with context:
            context.set("answer", 42)
            context.buffer_dependency_cache_publication(entry)

    assert current_calculation_run_context() is None
    assert context.get("answer") is None
    assert context.get_dependency_cache_hit("cache-a", None) is None


def test_dependency_cache_publications_discard_on_exception_and_release_leases() -> (
    None
):
    entry = make_pending_publication("cache-a")

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
    ):
        with pytest.raises(CalculationFailed):
            with CalculationRunContext() as context:
                raise_after_buffering(context, entry)

    publish_batch.assert_not_called()
    release_lease.assert_called_once_with(entry.lease)


def test_replacing_buffered_dependency_cache_publication_releases_prior_lease() -> None:
    first = make_pending_publication("cache-a")
    second = PendingDependencyCachePublication(
        cache_key=first.cache_key,
        result="value:cache-a:second",
        dependencies=first.dependencies,
        cache_backend=first.cache_backend,
        timeout=first.timeout,
        started_generation=first.started_generation,
        lease=CacheComputeLease(
            key=first.lease.key,
            token=f"lease:{first.cache_key}:second",
        ),
    )

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
    ):
        with CalculationRunContext() as context:
            context.buffer_dependency_cache_publication(first)
            context.buffer_dependency_cache_publication(second)

            release_lease.assert_called_once_with(first.lease)

    assert release_lease.call_args_list == [
        mock.call(first.lease),
        mock.call(second.lease),
    ]


def test_replacing_pending_hit_stays_pinned_under_finite_budget() -> None:
    first = make_pending_publication("cache-a")
    second = PendingDependencyCachePublication(
        cache_key=first.cache_key,
        result="value:cache-a:second",
        dependencies=first.dependencies,
        cache_backend=first.cache_backend,
        timeout=first.timeout,
        started_generation=first.started_generation,
        lease=CacheComputeLease(
            key=first.lease.key,
            token=f"lease:{first.cache_key}:second",
        ),
    )
    pressure = DependencyCacheHit(value="pressure", dependencies=frozenset())
    pressure_size = estimate_cache_entry_size("pressure", pressure, stop_after=None)
    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": pressure_size}
        ),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(first)
        context.set_dependency_cache_hits({"pressure": pressure})

        context.buffer_dependency_cache_publication(second)

        hit = context.get_dependency_cache_hit(first.cache_key)
        assert isinstance(hit, DependencyCacheHit)
        assert hit.value == second.result
        assert context.get_dependency_cache_hit("pressure") == pressure
        assert context._dependency_cache_pending_publications == {
            first.cache_key: second
        }
        assert run_context_cache_budget.estimated_bytes == pressure_size
        release_lease.assert_called_once_with(first.lease)


def test_failed_prior_lease_release_preserves_pinned_pending_replacement_state() -> (
    None
):
    first = make_pending_publication("cache-a")
    second = PendingDependencyCachePublication(
        cache_key=first.cache_key,
        result="value:cache-a:second",
        dependencies=first.dependencies,
        cache_backend=first.cache_backend,
        timeout=first.timeout,
        started_generation=first.started_generation,
        lease=CacheComputeLease(
            key=first.lease.key,
            token=f"lease:{first.cache_key}:second",
        ),
    )
    pressure = DependencyCacheHit(value="pressure", dependencies=frozenset())
    pressure_size = estimate_cache_entry_size("pressure", pressure, stop_after=None)
    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": pressure_size}
        ),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease",
            side_effect=[LeaseReleaseFailed, None],
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(first)

        with pytest.raises(LeaseReleaseFailed):
            context.buffer_dependency_cache_publication(second)

        context.set_dependency_cache_hits({"pressure": pressure})
        assert context._dependency_cache_pending_publications == {
            first.cache_key: first
        }
        hit = context.get_dependency_cache_hit(first.cache_key)
        assert isinstance(hit, DependencyCacheHit)
        assert hit.value == first.result
        assert context.get_dependency_cache_hit("pressure") == pressure
        assert run_context_cache_budget.estimated_bytes == pressure_size

    assert release_lease.call_args_list == [
        mock.call(first.lease),
        mock.call(first.lease),
    ]


def test_dependency_cache_publication_guardrail_flushes_when_limit_is_reached() -> None:
    first = make_pending_publication("cache-a")
    second = make_pending_publication("cache-b")

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ) as publish_batch,
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
    ):
        with CalculationRunContext(dependency_cache_publish_batch_size=2) as context:
            context.buffer_dependency_cache_publication(first)
            publish_batch.assert_not_called()

            context.buffer_dependency_cache_publication(second)

            publish_batch.assert_called_once_with((first, second))
            assert release_lease.call_args_list == [
                mock.call(first.lease),
                mock.call(second.lease),
            ]


def test_dependency_cache_publication_non_positive_batch_size_flushes_immediately() -> (
    None
):
    entry = make_pending_publication("cache-a")

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ) as publish_batch,
        mock.patch("general_manager.cache.dependency_publish.release_compute_lease"),
        CalculationRunContext(dependency_cache_publish_batch_size=0) as context,
    ):
        context.buffer_dependency_cache_publication(entry)

        publish_batch.assert_called_once_with((entry,))


def test_discard_prefix_removes_matching_tuple_keys_only() -> None:
    with CalculationRunContext() as ctx:
        ctx.set(("orm_instance", "Human", 1, "default"), "alice")
        ctx.set(("orm_instance", "Human", 2, "default"), "bob")
        ctx.set(("other", "Human", 1), "other")
        ctx.set("plain", "value")

        ctx.discard_prefix(("orm_instance", "Human", 1))

        assert not ctx.has(("orm_instance", "Human", 1, "default"))
        assert ctx.get(("orm_instance", "Human", 2, "default")) == "bob"
        assert ctx.get(("other", "Human", 1)) == "other"
        assert ctx.get("plain") == "value"


def test_public_storage_helpers_isolate_sequential_as_of_namespaces() -> None:
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return f"loaded-{calls}"

    with CalculationRunContext() as ctx:
        with as_of("2022-01-01"):
            ctx.set("direct", "date-a")
            assert ctx.get_or_set("loaded", loader) == "loaded-1"
            assert ctx.has("direct")
            assert "direct" in ctx

        with as_of("2022-01-02"):
            assert ctx.get("direct") is None
            assert not ctx.has("direct")
            assert "direct" not in ctx
            ctx.set("direct", "date-b")
            assert ctx.get_or_set("loaded", loader) == "loaded-2"

        with as_of("2022-01-01"):
            assert ctx.get("direct") == "date-a"
            assert ctx.get_or_set("loaded", loader) == "loaded-1"

    assert calls == 2


def test_historical_storage_applies_one_run_namespace_transform() -> None:
    with CalculationRunContext() as ctx, as_of("2022-01-01"):
        ctx.set(("cache", "key"), "value")
        fingerprint = as_of_cache_fingerprint()
        assert fingerprint is not None

        assert ctx._values == {
            ("as_of", fingerprint, ("cache", "key")): "value",
        }


def test_equivalent_offset_instants_share_run_cache_namespace() -> None:
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return "loaded"

    with CalculationRunContext() as ctx:
        with as_of("2022-01-01T01:00:00+01:00"):
            first = ctx.get_or_set("key", loader)
        with as_of("2022-01-01T00:00:00+00:00"):
            second = ctx.get_or_set("key", loader)

    assert first == second == "loaded"
    assert calls == 1


def test_prefix_deletion_only_discards_active_as_of_namespace() -> None:
    prefix = ("orm_instance", "Human")

    with CalculationRunContext() as ctx:
        with as_of("2022-01-01"):
            ctx.set((*prefix, 1), "date-a")
        with as_of("2022-01-02"):
            ctx.set((*prefix, 1), "date-b")
        with as_of("2022-01-01"):
            ctx.discard_prefix(prefix)
            assert ctx.get((*prefix, 1)) is None
        with as_of("2022-01-02"):
            assert ctx.get((*prefix, 1)) == "date-b"


def test_index_and_group_helpers_isolate_sequential_as_of_namespaces() -> None:
    index_calls = 0
    group_calls = 0

    def index_loader() -> list[str]:
        nonlocal index_calls
        index_calls += 1
        return [f"index-{index_calls}"]

    def group_loader() -> list[str]:
        nonlocal group_calls
        group_calls += 1
        return [f"group-{group_calls}"]

    with CalculationRunContext() as ctx:
        results = []
        for search_date in ("2022-01-01", "2022-01-02", "2022-01-01"):
            with as_of(search_date):
                indexed = ctx.index(
                    key="shared",
                    loader=index_loader,
                    index_by=lambda value: value,
                )
                grouped = ctx.group_by(
                    key="shared",
                    loader=group_loader,
                    group_by=lambda value: value,
                )
                results.append((indexed, grouped))

    assert index_calls == 2
    assert group_calls == 2
    assert results[0][0] is results[2][0]
    assert results[0][1] is results[2][1]
    assert results[0][0] != results[1][0]
    assert results[0][1] != results[1][1]


def test_orm_bucket_result_helpers_store_and_clear_entries() -> None:
    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_result(("query", "a"), ("pk1", "pk2"))
        ctx.set(("other", "query", "a"), "keep")

        assert ctx.get_orm_bucket_result(("query", "a")) == ("pk1", "pk2")

        ctx.clear_orm_bucket_results()

        assert ctx.get_orm_bucket_result(("query", "a")) is None
        assert ctx.get(("other", "query", "a")) == "keep"


def test_orm_bucket_result_helpers_distinguish_empty_tuple_from_missing() -> None:
    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_result(("query", "empty"), ())

        assert ctx.get_orm_bucket_result(("query", "empty")) == ()
        assert ctx.get_orm_bucket_result(("query", "missing")) is None


def test_orm_bucket_row_results_are_stored_and_cleared() -> None:
    rows = (object(), object())

    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_rows(("query", "rows"), rows)

        assert ctx.get_orm_bucket_rows(("query", "rows")) == rows

        ctx.clear_orm_bucket_results()

        assert ctx.get_orm_bucket_rows(("query", "rows")) is None


def test_clear_orm_bucket_results_clears_primary_keys_and_rows() -> None:
    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_result(("query", "a"), ("pk1", "pk2"))
        ctx.set_orm_bucket_rows(("query", "a"), ("row1", "row2"))

        ctx.clear_orm_bucket_results()

        assert ctx.get_orm_bucket_result(("query", "a")) is None
        assert ctx.get_orm_bucket_rows(("query", "a")) is None


def test_orm_bucket_rows_index_model_rows_and_prefetch_state() -> None:
    class Row:
        _meta = SimpleNamespace(concrete_model=None)

        def __init__(self, pk: int, database_alias: str) -> None:
            self.pk = pk
            self._state = SimpleNamespace(db=database_alias)

    Row._meta = SimpleNamespace(concrete_model=Row)
    row = Row(7, "default")

    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_rows(("query", "rows"), (row,))

        assert ctx.get_orm_model_row(Row, 7, "default") is row
        assert ctx.get_orm_model_row_items(Row) == (((7, "default"), row),)

        ctx.add_orm_model_relation_prefetched_keys(
            Row,
            "default",
            "members",
            [(7, "default")],
        )
        assert ctx.get_orm_model_relation_prefetched_keys(
            Row,
            "default",
            "members",
        ) == frozenset({(7, "default")})

        ctx.clear_orm_bucket_results()

        assert ctx.get_orm_model_row(Row, 7, "default") is None
        assert ctx.get_orm_model_row_items(Row) == ()
        assert (
            ctx.get_orm_model_relation_prefetched_keys(
                Row,
                "default",
                "members",
            )
            == frozenset()
        )


def test_run_context_reweighs_mutated_orm_index() -> None:
    class Row:
        _meta = SimpleNamespace(concrete_model=None)

        def __init__(self) -> None:
            self.pk = 7
            self._state = SimpleNamespace(db="default")
            self.payload = b"x" * 4096

    Row._meta = SimpleNamespace(concrete_model=Row)
    row = Row()
    empty_index_size = estimate_cache_entry_size(
        ("orm_model_row_index", Row), {}, stop_after=None
    )

    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": empty_index_size}
        ),
        CalculationRunContext() as context,
    ):
        context.set_orm_bucket_rows(("query", "rows"), (row,))

        assert context.get_orm_model_row(Row, 7, "default") is None


def test_bucket_index_helpers_store_replay_and_clear_dependencies() -> None:
    """Store a bucket index, replay its dependencies on hit, then clear it."""
    dependencies: set[Dependency] = {
        ("Project", "filter", '{"status": "active"}'),
    }

    with CalculationRunContext() as ctx:
        ctx.set_bucket_index_result(
            ("source", "projects"),
            ("field", ("code",), False),
            False,
            {"A": "project-a"},
            dependencies,
            1000,
        )

        with DependencyTracker() as tracked_dependencies:
            result = ctx.get_bucket_index_result(
                ("source", "projects"),
                ("field", ("code",), False),
                False,
                1000,
            )

        assert result == {"A": "project-a"}
        assert dependencies <= tracked_dependencies

        ctx.clear_bucket_indexes()

        assert (
            ctx.get_bucket_index_result(
                ("source", "projects"),
                ("field", ("code",), False),
                False,
                1000,
            )
            is None
        )


def test_bucket_index_helpers_distinguish_unique_and_many_indexes() -> None:
    """Keep unique and multi-value bucket indexes in separate cache entries."""
    key_spec = ("field", ("code",), False)

    with CalculationRunContext() as ctx:
        ctx.set_bucket_index_result(
            ("source", "projects"),
            key_spec,
            False,
            {"A": "project-a"},
            set(),
            1000,
        )
        ctx.set_bucket_index_result(
            ("source", "projects"),
            key_spec,
            True,
            {"A": ("project-a", "project-b")},
            set(),
            1000,
        )

        assert ctx.get_bucket_index_result(
            ("source", "projects"),
            key_spec,
            False,
            1000,
        ) == {"A": "project-a"}
        assert ctx.get_bucket_index_result(
            ("source", "projects"),
            key_spec,
            True,
            1000,
        ) == {"A": ("project-a", "project-b")}


def test_index_loads_once_and_groups_by_key() -> None:
    calls = 0

    class Row:
        def __init__(self, day: str, value: int) -> None:
            self.day = day
            self.value = value

    def loader() -> list[Row]:
        nonlocal calls
        calls += 1
        return [Row("2026-06-10", 10), Row("2026-06-11", 11)]

    with CalculationRunContext() as ctx:
        first = ctx.index(
            key=("rows", 1),
            loader=loader,
            index_by=lambda row: row.day,
        )
        second = ctx.index(
            key=("rows", 1),
            loader=loader,
            index_by=lambda row: row.day,
        )

    assert calls == 1
    assert first is second
    assert first["2026-06-10"].value == 10
    assert first["2026-06-11"].value == 11


def test_index_duplicate_keys_keep_last_row() -> None:
    class Row:
        def __init__(self, day: str, value: int) -> None:
            self.day = day
            self.value = value

    def loader() -> list[Row]:
        return [Row("2026-06-10", 10), Row("2026-06-10", 11)]

    with CalculationRunContext() as ctx:
        result = ctx.index(
            key=("rows", "duplicates"),
            loader=loader,
            index_by=lambda row: row.day,
        )

    assert result["2026-06-10"].value == 11


def test_group_by_loads_once_and_groups_rows() -> None:
    calls = 0

    class Row:
        def __init__(self, project_id: int, value: int) -> None:
            self.project_id = project_id
            self.value = value

    def loader() -> list[Row]:
        nonlocal calls
        calls += 1
        return [Row(1, 10), Row(1, 11), Row(2, 20)]

    with CalculationRunContext() as ctx:
        first = ctx.group_by(
            key=("rows", "project"),
            loader=loader,
            group_by=lambda row: row.project_id,
        )
        second = ctx.index_many(
            key=("rows", "project"),
            loader=loader,
            index_by=lambda row: row.project_id,
        )

    assert calls == 1
    assert first is second
    assert [row.value for row in first[1]] == [10, 11]
    assert [row.value for row in first[2]] == [20]
