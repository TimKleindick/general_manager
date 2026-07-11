from unittest import mock
from types import SimpleNamespace

import pytest

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import DependencyCacheHit
from general_manager.cache.dependency_index import Dependency
from general_manager.cache.dependency_publish import (
    CacheComputeLease,
    PendingDependencyCachePublication,
)
from general_manager.cache.run_context import (
    BUCKET_INDEX_PREFIX,
    CALCULATION_BUCKET_RESULT_MISSING,
    CALCULATION_BUCKET_RESULT_PREFIX,
    CalculationRunContext,
    ORM_BUCKET_COUNT_PREFIX,
    ORM_BUCKET_EXISTS_PREFIX,
    ORM_BUCKET_FIRST_ROW_PREFIX,
    ORM_BUCKET_GET_PREFIX,
    ORM_BUCKET_INDEX_PREFIX,
    ORM_BUCKET_LAST_ROW_PREFIX,
    ORM_BUCKET_MANAGER_RESULT_PREFIX,
    ORM_BUCKET_MEMBERSHIP_PREFIX,
    ORM_BUCKET_RESULT_PREFIX,
    ORM_BUCKET_ROW_RESULT_PREFIX,
    ORM_DIRECT_RELATION_PREFETCH_PREFIX,
    ORM_MODEL_RELATION_PREFETCH_PREFIX,
    ORM_MODEL_ROW_INDEX_PREFIX,
    ORM_QUERY_BUCKET_PREFIX,
    ORM_RELATION_MANAGER_PREFIX,
    TRUSTED_ORM_MANAGER_PREFIX,
    current_calculation_run_context,
    ensure_calculation_run_context,
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


def test_calculation_result_cache_distinguishes_empty_entry_from_missing() -> None:
    signature = ("calculation", "empty")
    dependencies = {("Project", "identification", "42")}

    with CalculationRunContext() as context:
        assert context.get_calculation_bucket_result(signature) is (
            CALCULATION_BUCKET_RESULT_MISSING
        )
        context.set_calculation_bucket_result(signature, (), dependencies)

        entry = context.get_calculation_bucket_result(signature)
        assert entry is not CALCULATION_BUCKET_RESULT_MISSING
        assert entry.snapshots == ()
        assert entry.dependencies == frozenset(dependencies)


def test_calculation_result_cache_hit_replays_dependencies() -> None:
    signature = ("calculation", "dependency")
    dependency = ("Project", "identification", "42")

    with CalculationRunContext() as context:
        context.set_calculation_bucket_result(signature, (("snapshot",),), {dependency})
        with DependencyTracker() as tracked:
            entry = context.get_calculation_bucket_result(signature)
        assert entry.snapshots == (("snapshot",),)
        assert tracked == {dependency}


def test_clear_calculation_result_cache_preserves_other_namespaces() -> None:
    with CalculationRunContext() as context:
        context.set_calculation_bucket_result(("one",), (), ())
        context.set_orm_bucket_result(("orm",), "keep")
        context.set(("unrelated",), "keep")

        context.clear_calculation_bucket_results()

        assert (
            context.get_calculation_bucket_result(("one",))
            is CALCULATION_BUCKET_RESULT_MISSING
        )
        assert context.get_orm_bucket_result(("orm",)) == "keep"
        assert context.get(("unrelated",)) == "keep"


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


def test_discard_prefixes_scans_all_prefixes_and_preserves_unrelated_values() -> None:
    with CalculationRunContext() as ctx:
        ctx.set(("orm", "row", 1), "row")
        ctx.set(("bucket", "index", 1), "index")
        ctx.set(("orm", "other", 1), "other")
        ctx.set(("plain",), "plain")
        ctx.set("non-tuple", "non-tuple")

        ctx.discard_prefixes((("orm",), ("bucket", "index")))

        assert ctx.get(("orm", "row", 1)) is None
        assert ctx.get(("bucket", "index", 1)) is None
        assert ctx.get(("orm", "other", 1)) is None
        assert ctx.get(("plain",)) == "plain"
        assert ctx.get("non-tuple") == "non-tuple"


def test_discard_prefixes_distinguishes_empty_iterable_from_empty_prefix() -> None:
    with CalculationRunContext() as ctx:
        ctx.set(("orm", 1), "orm")
        ctx.set(("bucket", 1), "bucket")
        ctx.set("plain", "plain")

        ctx.discard_prefixes(())
        assert ctx.get(("orm", 1)) == "orm"
        assert ctx.get(("bucket", 1)) == "bucket"

        ctx.discard_prefixes(((),))
        assert ctx.get(("orm", 1)) is None
        assert ctx.get(("bucket", 1)) is None
        assert ctx.get("plain") == "plain"


def test_discard_prefixes_handles_nested_overlapping_and_extra_components() -> None:
    with CalculationRunContext() as ctx:
        ctx.set(("orm",), "short")
        ctx.set(("orm", "row"), "row")
        ctx.set(("orm", "row", "field"), "field")
        ctx.set(("ormx", "row"), "other")

        ctx.discard_prefixes((("orm", "row", "field"), ("orm", "row")))

        assert ctx.get(("orm",)) == "short"
        assert ctx.get(("orm", "row")) is None
        assert ctx.get(("orm", "row", "field")) is None
        assert ctx.get(("ormx", "row")) == "other"


def test_clear_mutation_cache_clears_all_namespaces_and_preserves_other_state() -> None:
    prefixes = (
        ORM_BUCKET_RESULT_PREFIX,
        ORM_BUCKET_ROW_RESULT_PREFIX,
        ORM_BUCKET_MANAGER_RESULT_PREFIX,
        ORM_BUCKET_FIRST_ROW_PREFIX,
        ORM_BUCKET_COUNT_PREFIX,
        ORM_BUCKET_LAST_ROW_PREFIX,
        ORM_BUCKET_GET_PREFIX,
        ORM_BUCKET_INDEX_PREFIX,
        ORM_BUCKET_MEMBERSHIP_PREFIX,
        ORM_MODEL_ROW_INDEX_PREFIX,
        ORM_MODEL_RELATION_PREFETCH_PREFIX,
        ORM_DIRECT_RELATION_PREFETCH_PREFIX,
        ORM_RELATION_MANAGER_PREFIX,
        ORM_QUERY_BUCKET_PREFIX,
        ORM_BUCKET_EXISTS_PREFIX,
        BUCKET_INDEX_PREFIX,
        TRUSTED_ORM_MANAGER_PREFIX,
        CALCULATION_BUCKET_RESULT_PREFIX,
    )
    entry = make_pending_publication("preserve-publication")
    hit = DependencyCacheHit(value="prefetched", dependencies=frozenset())

    with (
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ),
        mock.patch("general_manager.cache.dependency_publish.release_compute_lease"),
        CalculationRunContext() as ctx,
    ):
        for prefix in prefixes:
            ctx.set((prefix, "key"), "target")
        ctx.set(("unrelated", "key"), "keep")
        ctx.set("plain", "keep")
        ctx.set_dependency_cache_hits({"prefetched": hit})
        ctx.buffer_dependency_cache_publication(entry)

        ctx.clear_mutation_cache()

        assert all(ctx.get((prefix, "key")) is None for prefix in prefixes)
        assert ctx.get(("unrelated", "key")) == "keep"
        assert ctx.get("plain") == "keep"
        assert ctx.get_dependency_cache_hit("prefetched") is hit
        assert ctx.get_dependency_cache_hit(entry.cache_key).value == entry.result
        assert entry.cache_key in ctx._dependency_cache_pending_publications


def test_clear_mutation_cache_does_not_touch_nested_context_state() -> None:
    with CalculationRunContext() as outer:
        outer.set((ORM_BUCKET_RESULT_PREFIX, "outer"), "outer")

        with CalculationRunContext() as inner:
            inner.set((ORM_BUCKET_RESULT_PREFIX, "inner"), "inner")
            inner.clear_mutation_cache()
            assert inner.get((ORM_BUCKET_RESULT_PREFIX, "inner")) is None

        assert outer.get((ORM_BUCKET_RESULT_PREFIX, "outer")) == "outer"


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


def test_orm_bucket_count_helpers_preserve_zero_and_clear_entries() -> None:
    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_count(("query", "empty"), 0)

        assert ctx.get_orm_bucket_count(("query", "empty")) == 0
        assert ctx.get_orm_bucket_count(("query", "missing")) is None

        ctx.clear_orm_bucket_results()

        assert ctx.get_orm_bucket_count(("query", "empty")) is None


def test_orm_bucket_scalar_terminal_helpers_preserve_false_and_clear_entries() -> None:
    with CalculationRunContext() as ctx:
        ctx.set_orm_bucket_last_row(("query", "last"), None)
        ctx.set_orm_bucket_get(("query", "get"), False)
        ctx.set_orm_bucket_index(("query", "index"), "row")
        ctx.set_orm_bucket_membership(("query", "contains"), False)

        assert ctx.get_orm_bucket_last_row(("query", "last"), "missing") is None
        assert ctx.get_orm_bucket_get(("query", "get"), "missing") is False
        assert ctx.get_orm_bucket_index(("query", "index"), "missing") == "row"
        assert ctx.get_orm_bucket_membership(("query", "contains"), "missing") is False

        ctx.clear_orm_bucket_results()

        assert ctx.get_orm_bucket_last_row(("query", "last"), "missing") == "missing"
        assert ctx.get_orm_bucket_get(("query", "get"), "missing") == "missing"
        assert ctx.get_orm_bucket_index(("query", "index"), "missing") == "missing"
        assert (
            ctx.get_orm_bucket_membership(("query", "contains"), "missing") == "missing"
        )


def test_direct_relation_prefetch_keys_are_stored_and_cleared() -> None:
    with CalculationRunContext() as ctx:
        assert (
            ctx.get_orm_direct_relation_prefetched_keys(
                object,
                "default",
                "owner",
            )
            == frozenset()
        )
        ctx.add_orm_direct_relation_prefetched_keys(
            object,
            "default",
            "owner",
            [(1, "default")],
        )

        assert ctx.get_orm_direct_relation_prefetched_keys(
            object,
            "default",
            "owner",
        ) == frozenset({(1, "default")})

        ctx.clear_orm_bucket_results()

        assert (
            ctx.get_orm_direct_relation_prefetched_keys(
                object,
                "default",
                "owner",
            )
            == frozenset()
        )


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
