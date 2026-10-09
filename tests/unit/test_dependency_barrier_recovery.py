"""Cache barrier failures must not consume another mutation's active count."""

import os
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache

from general_manager.cache import dependency_index as index
from general_manager.cache.dependency_publish import (
    CachePublishAborted,
    CacheComputeLease,
    PendingDependencyCachePublication,
    publish_dependency_cache_entries,
    publish_dependency_cache_entry,
)


@pytest.fixture(params=["locmem", "file", "redis", "django-redis"])
def barrier_cache(request, settings, tmp_path, monkeypatch):
    backend = request.param
    if backend in {"redis", "django-redis"}:
        location = os.environ.get("GENERAL_MANAGER_TEST_REDIS_URL")
        if not location:
            pytest.skip("Set GENERAL_MANAGER_TEST_REDIS_URL for real Redis tests")
        pytest.importorskip("redis")
        if backend == "django-redis":
            pytest.importorskip("django_redis")
        cache_backend = (
            "django.core.cache.backends.redis.RedisCache"
            if backend == "redis"
            else "django_redis.cache.RedisCache"
        )
    elif backend == "file":
        location = str(tmp_path / "barrier-cache")
        cache_backend = "django.core.cache.backends.filebased.FileBasedCache"
    else:
        location = "test-barrier-recovery"
        cache_backend = "django.core.cache.backends.locmem.LocMemCache"
    settings.CACHES = {"default": {"BACKEND": cache_backend, "LOCATION": location}}
    backend_cache = caches["default"]
    monkeypatch.setattr(index, "cache", backend_cache)
    backend_cache.clear()
    yield backend_cache
    backend_cache.clear()


def publish_current_value():
    backend = LocMemCache("barrier-publication", {})
    backend.clear()
    publish_dependency_cache_entry(
        cache_key="recovered-value",
        result=42,
        dependencies=(),
        cache_backend=backend,
        timeout=60,
        started_generation=index.get_dependency_generation(),
    )
    assert backend.get("recovered-value") is not None


@pytest.mark.parametrize("active_count", [0, 1])
@pytest.mark.parametrize("applied", [False, True])
@pytest.mark.parametrize(
    "failed_key",
    [
        index.DEPENDENCY_GENERATION_KEY,
        index.DATA_CHANGE_COUNT_KEY,
        index.DATA_CHANGE_LOCK_KEY,
    ],
)
def test_failed_begin_restores_only_its_own_count(
    barrier_cache, active_count, applied, failed_key
):
    for _ in range(active_count):
        index.begin_dependency_data_change()
    previous_generation = index.get_dependency_generation()
    original_set = barrier_cache.set
    failure = ConnectionError("injected begin write failure")
    failed = False

    def fail_once(key, value, timeout=None, version=None):
        nonlocal failed
        if key == failed_key and not failed:
            failed = True
            if applied:
                original_set(key, value, timeout, version)
            raise failure
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_once):
        with pytest.raises(ConnectionError) as caught:
            index.begin_dependency_data_change()
    assert caught.value is failure
    assert index.get_dependency_generation() >= previous_generation
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY, 0) == active_count
    assert index.is_dependency_data_change_active() is bool(active_count)

    for _ in range(2):
        index.begin_dependency_data_change()
        index.end_dependency_data_change()
        assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY, 0) == active_count
        assert index.is_dependency_data_change_active() is bool(active_count)
    if active_count:
        with pytest.raises(CachePublishAborted):
            publish_current_value()
        index.end_dependency_data_change()
    publish_current_value()


@pytest.mark.parametrize("active_count", [0, 1])
def test_failed_begin_cleanup_is_recoverable_on_retry(barrier_cache, active_count):
    for _ in range(active_count):
        index.begin_dependency_data_change()
    original_set = barrier_cache.set
    primary = ConnectionError("primary flag failure")
    cleanup = ConnectionError("rollback count failure")
    flag_failed = False
    rollback_failed = False

    def fail_begin_and_rollback(key, value, timeout=None, version=None):
        nonlocal flag_failed, rollback_failed
        if key == index.DATA_CHANGE_LOCK_KEY and not flag_failed:
            flag_failed = True
            raise primary
        if key == index.DATA_CHANGE_COUNT_KEY and flag_failed and not rollback_failed:
            rollback_failed = True
            raise cleanup
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_begin_and_rollback):
        with pytest.raises(ConnectionError) as caught:
            index.begin_dependency_data_change()
    assert caught.value is primary
    index.begin_dependency_data_change()
    index.end_dependency_data_change()
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY, 0) == active_count
    assert index.is_dependency_data_change_active() is bool(active_count)
    if active_count:
        index.end_dependency_data_change()
    publish_current_value()


@pytest.mark.parametrize("applied", [False, True])
def test_journal_commit_error_has_a_defined_owner(barrier_cache, applied):
    original_delete = barrier_cache.delete
    failure = ConnectionError("journal deletion failure")
    failed = False

    def fail_once(key, version=None):
        nonlocal failed
        if key == index.DATA_CHANGE_RECOVERY_KEY and not failed:
            failed = True
            if applied:
                original_delete(key, version)
            raise failure
        return original_delete(key, version)

    with patch.object(barrier_cache, "delete", side_effect=fail_once):
        if applied:
            assert index.begin_dependency_data_change() == 1
        else:
            with pytest.raises(ConnectionError) as caught:
                index.begin_dependency_data_change()
            assert caught.value is failure
    assert index.is_dependency_data_change_active() is applied
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == int(applied)
    if applied:
        index.end_dependency_data_change()
    publish_current_value()


def test_rollback_flag_cleanup_failure_blocks_publication_until_retry(barrier_cache):
    original_set = barrier_cache.set
    original_delete = barrier_cache.delete
    primary = ConnectionError("applied flag failure")
    cleanup = ConnectionError("flag cleanup failure")
    failed = False

    def fail_flag(key, value, timeout=None, version=None):
        nonlocal failed
        result = original_set(key, value, timeout, version)
        if key == index.DATA_CHANGE_LOCK_KEY and not failed:
            failed = True
            raise primary
        return result

    def fail_delete(key, version=None):
        if key == index.DATA_CHANGE_LOCK_KEY:
            raise cleanup
        return original_delete(key, version)

    with (
        patch.object(barrier_cache, "set", side_effect=fail_flag),
        patch.object(barrier_cache, "delete", side_effect=fail_delete),
    ):
        with pytest.raises(ConnectionError) as caught:
            index.begin_dependency_data_change()
    assert caught.value is primary
    assert index.is_dependency_data_change_active()
    with pytest.raises(CachePublishAborted):
        publish_current_value()
    index.begin_dependency_data_change()
    index.end_dependency_data_change()
    assert not index.is_dependency_data_change_active()
    publish_current_value()


@pytest.mark.parametrize("active_count", [0, 1])
def test_context_discard_failure_preserves_foreign_barrier(barrier_cache, active_count):
    for _ in range(active_count):
        index.begin_dependency_data_change()
    primary = RuntimeError("context discard failed")
    with patch.object(
        index, "_discard_active_context_dependency_cache_state", side_effect=primary
    ):
        with pytest.raises(RuntimeError) as caught:
            index.begin_dependency_data_change()
    assert caught.value is primary
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == active_count
    assert index.is_dependency_data_change_active() is bool(active_count)
    index.begin_dependency_data_change()
    index.end_dependency_data_change()
    if active_count:
        index.end_dependency_data_change()
    publish_current_value()


def test_release_failure_does_not_turn_committed_begin_into_orphan(barrier_cache):
    original_release = index.release_lock
    release_error = ConnectionError("mutex release response failure")

    def release_then_fail(token):
        original_release(token)
        raise release_error

    with patch.object(index, "release_lock", side_effect=release_then_fail):
        assert index.begin_dependency_data_change() == 1
    assert index.is_dependency_data_change_active()
    index.end_dependency_data_change()
    publish_current_value()


def test_failed_begin_preserves_original_exception_when_release_fails(barrier_cache):
    primary = RuntimeError("context discard failed")
    original_release = index.release_lock
    release_error = ConnectionError("mutex release failed")

    def release_then_fail(token):
        original_release(token)
        raise release_error

    with (
        patch.object(
            index, "_discard_active_context_dependency_cache_state", side_effect=primary
        ),
        patch.object(index, "release_lock", side_effect=release_then_fail),
    ):
        with pytest.raises(RuntimeError) as caught:
            index.begin_dependency_data_change()
    assert caught.value is primary
    assert not index.is_dependency_data_change_active()


def test_observed_mutex_successor_is_not_modified_by_failed_owner(barrier_cache):
    original_set = barrier_cache.set
    replaced = False

    def replace_owner(key, value, timeout=None, version=None):
        nonlocal replaced
        result = original_set(key, value, timeout, version)
        if key == index.DATA_CHANGE_COUNT_KEY and not replaced:
            replaced = True
            original_set(index.LOCK_KEY, "successor", 60)
            # The successor completed recovery and opened its own barriers.
            original_set(index.DATA_CHANGE_COUNT_KEY, 7, None)
            original_set(index.DATA_CHANGE_LOCK_KEY, "1", None)
            barrier_cache.delete(index.DATA_CHANGE_RECOVERY_KEY)
        return result

    with patch.object(barrier_cache, "set", side_effect=replace_owner):
        with pytest.raises(index.DependencyBarrierStateError):
            index.begin_dependency_data_change()
    assert barrier_cache.get(index.LOCK_KEY) == "successor"
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 7
    assert barrier_cache.get(index.DATA_CHANGE_LOCK_KEY) == "1"


@pytest.mark.parametrize(
    "failed_key",
    [
        index.DEPENDENCY_GENERATION_KEY,
        index.DATA_CHANGE_COUNT_KEY,
        index.DATA_CHANGE_LOCK_KEY,
    ],
)
def test_silently_dropped_write_does_not_start_mutation(barrier_cache, failed_key):
    original_set = barrier_cache.set
    dropped = False

    def drop_once(key, value, timeout=None, version=None):
        nonlocal dropped
        if key == failed_key and not dropped:
            dropped = True
            return None
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=drop_once):
        with pytest.raises(index.DependencyBarrierStateError):
            index.begin_dependency_data_change()
    assert not index.is_dependency_data_change_active()
    index.begin_dependency_data_change()
    index.end_dependency_data_change()
    publish_current_value()


@pytest.mark.parametrize("active_count", [1, 2])
def test_failed_end_is_completed_before_next_begin(barrier_cache, active_count):
    for _ in range(active_count):
        index.begin_dependency_data_change()
    original_set = barrier_cache.set
    failed = False
    primary = ConnectionError("end count failure")

    def fail_count(key, value, timeout=None, version=None):
        nonlocal failed
        if key == index.DATA_CHANGE_COUNT_KEY and not failed:
            failed = True
            raise primary
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_count):
        with pytest.raises(ConnectionError) as caught:
            index.end_dependency_data_change()
    assert caught.value is primary
    index.begin_dependency_data_change()
    index.end_dependency_data_change()
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == active_count - 1
    if active_count == 2:
        assert index.is_dependency_data_change_active()
        index.end_dependency_data_change()
    publish_current_value()


def test_recovered_begin_keeps_old_generation_fenced(barrier_cache):
    started_generation = index.get_dependency_generation()
    primary = RuntimeError("context discard failure")
    with patch.object(
        index, "_discard_active_context_dependency_cache_state", side_effect=primary
    ):
        with pytest.raises(RuntimeError):
            index.begin_dependency_data_change()
    assert index.get_dependency_generation() == started_generation + 1
    with pytest.raises(CachePublishAborted):
        publish_dependency_cache_entry(
            cache_key="stale-value",
            result=42,
            dependencies=(),
            cache_backend=barrier_cache,
            timeout=60,
            started_generation=started_generation,
        )
    assert barrier_cache.get("stale-value") is None
    publish_current_value()


def test_disabled_coordination_cache_does_not_block_mutations(settings):
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
    }
    assert index.begin_dependency_data_change() == 0
    index.end_dependency_data_change()
    assert not index.is_dependency_data_change_active()


def test_pending_recovery_blocks_batch_publication(barrier_cache):
    original_set = barrier_cache.set
    flag_failed = False
    flag_error = ConnectionError("flag write failed")
    rollback_error = ConnectionError("rollback write failed")

    def fail_begin_and_rollback(key, value, timeout=None, version=None):
        nonlocal flag_failed
        if key == index.DATA_CHANGE_LOCK_KEY:
            flag_failed = True
            raise flag_error
        if key == index.DATA_CHANGE_COUNT_KEY and flag_failed:
            raise rollback_error
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_begin_and_rollback):
        with pytest.raises(ConnectionError):
            index.begin_dependency_data_change()
    assert barrier_cache.get(index.DATA_CHANGE_LOCK_KEY) is None
    assert index.is_dependency_data_change_active()
    pending = PendingDependencyCachePublication(
        cache_key="batch-value",
        result=42,
        dependencies=frozenset(),
        cache_backend=barrier_cache,
        timeout=60,
        started_generation=index.get_dependency_generation(),
        lease=CacheComputeLease("unused-lease", "unused-token"),
    )
    with pytest.raises(CachePublishAborted):
        publish_dependency_cache_entries((pending,))
    assert barrier_cache.get("batch-value") is None
    index.begin_dependency_data_change()
    index.end_dependency_data_change()
    publish_dependency_cache_entries((pending,))  # The now-stale entry is skipped.
    assert barrier_cache.get("batch-value") is None
    publish_current_value()


def test_concurrent_operation_keeps_its_barrier_during_failed_begin(barrier_cache):
    if type(barrier_cache).__module__ == "django.core.cache.backends.filebased":
        pytest.skip("FileBasedCache.add is not an atomic concurrent mutex")
    started = Event()
    finish = Event()

    def own_barrier():
        index.begin_dependency_data_change()
        started.set()
        assert finish.wait(10)
        index.end_dependency_data_change()

    executor = ThreadPoolExecutor(max_workers=1)
    worker = executor.submit(own_barrier)
    try:
        assert started.wait(5)
        original_set = barrier_cache.set
        failed = False
        failure = ConnectionError("concurrent begin failed")

        def fail_flag_once(key, value, timeout=None, version=None):
            nonlocal failed
            if key == index.DATA_CHANGE_LOCK_KEY and not failed:
                failed = True
                raise failure
            return original_set(key, value, timeout, version)

        with patch.object(barrier_cache, "set", side_effect=fail_flag_once):
            with pytest.raises(ConnectionError):
                index.begin_dependency_data_change()
        index.begin_dependency_data_change()
        index.end_dependency_data_change()
        assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 1
        with pytest.raises(CachePublishAborted):
            publish_current_value()
    finally:
        finish.set()
        worker.result(timeout=10)
        executor.shutdown()
    assert not index.is_dependency_data_change_active()
    publish_current_value()


@pytest.mark.parametrize(
    "method_name",
    [
        "clear_orm_bucket_results",
        "clear_bucket_indexes",
        "clear_bucket_projections",
        "clear_trusted_orm_managers",
    ],
)
def test_post_begin_context_cleanup_still_closes_barrier(barrier_cache, method_name):
    from general_manager.cache.run_context import CalculationRunContext
    from general_manager.cache.signals import data_change

    mutations = []

    class Example:
        @data_change
        def update(self):
            mutations.append(self)
            return self

    primary = RuntimeError("run context clearing failed")
    with CalculationRunContext() as context:
        with patch.object(context, method_name, side_effect=primary):
            with pytest.raises(RuntimeError) as caught:
                Example().update()
    assert caught.value is primary
    assert mutations == []
    assert not index.is_dependency_data_change_active()
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 0


def test_confirmed_commit_does_not_require_another_journal_read(barrier_cache):
    original_get = barrier_cache.get
    original_delete = barrier_cache.delete
    deleted = False
    failed = False
    read_error = ConnectionError("post-delete read failed")

    def delete_journal(key, version=None):
        nonlocal deleted
        result = original_delete(key, version)
        if key == index.DATA_CHANGE_RECOVERY_KEY:
            deleted = True
        return result

    def fail_read_once(key, default=None, version=None):
        nonlocal failed
        if key == index.DATA_CHANGE_RECOVERY_KEY and deleted and not failed:
            failed = True
            raise read_error
        return original_get(key, default, version)

    with (
        patch.object(barrier_cache, "delete", side_effect=delete_journal),
        patch.object(barrier_cache, "get", side_effect=fail_read_once),
    ):
        assert index.begin_dependency_data_change() == 1
    index.end_dependency_data_change()
    assert not index.is_dependency_data_change_active()


def test_end_journal_prepare_failure_does_not_orphan_completed_operation(barrier_cache):
    index.begin_dependency_data_change()
    original_set = barrier_cache.set
    failed = False
    primary = ConnectionError("end journal preparation failure")

    def fail_once(key, value, timeout=None, version=None):
        nonlocal failed
        if key == index.DATA_CHANGE_RECOVERY_KEY and not failed:
            failed = True
            raise primary
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_once):
        with pytest.raises(ConnectionError) as caught:
            index.end_dependency_data_change()
    assert caught.value is primary
    for _ in range(2):
        index.begin_dependency_data_change()
        index.end_dependency_data_change()
        assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 0
        assert not index.is_dependency_data_change_active()
    publish_current_value()


def test_applied_commit_delete_and_read_failure_restores_snapshot(barrier_cache):
    original_delete = barrier_cache.delete
    original_get = barrier_cache.get
    deleted = False
    read_failed = False
    primary = ConnectionError("applied journal delete failed")
    secondary = ConnectionError("commit readback failed")

    def delete_then_fail(key, version=None):
        nonlocal deleted
        result = original_delete(key, version)
        if key == index.DATA_CHANGE_RECOVERY_KEY and not deleted:
            deleted = True
            raise primary
        return result

    def fail_read_once(key, default=None, version=None):
        nonlocal read_failed
        if key == index.DATA_CHANGE_RECOVERY_KEY and deleted and not read_failed:
            read_failed = True
            raise secondary
        return original_get(key, default, version)

    with (
        patch.object(barrier_cache, "delete", side_effect=delete_then_fail),
        patch.object(barrier_cache, "get", side_effect=fail_read_once),
    ):
        with pytest.raises(ConnectionError) as caught:
            index.begin_dependency_data_change()
    assert caught.value is primary
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 0
    assert not index.is_dependency_data_change_active()
    publish_current_value()


def test_end_fences_computations_started_inside_barrier(barrier_cache):
    started = index.begin_dependency_data_change()
    index.end_dependency_data_change()
    assert index.get_dependency_generation() > started
    with pytest.raises(CachePublishAborted):
        publish_dependency_cache_entry(
            cache_key="old-transaction-result",
            result=1250,
            dependencies=(),
            cache_backend=barrier_cache,
            timeout=60,
            started_generation=started,
        )


@pytest.mark.parametrize(
    "failed_key", [index.DATA_CHANGE_COUNT_KEY, index.DATA_CHANGE_RECOVERY_KEY]
)
@pytest.mark.parametrize("applied", [False, True])
def test_owned_end_retry_preserves_another_writer(barrier_cache, failed_key, applied):
    index.begin_dependency_data_change(owner="writer-a")
    index.begin_dependency_data_change(owner="writer-b")
    original_set = barrier_cache.set
    failed = False
    failure = ConnectionError("owned end failure")

    def fail_once(key, value, timeout=None, version=None):
        nonlocal failed
        if key == failed_key and not failed:
            failed = True
            if applied:
                original_set(key, value, timeout, version)
            raise failure
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_once):
        with pytest.raises(ConnectionError):
            index.end_dependency_data_change(owner="writer-a")
    index.end_dependency_data_change(owner="writer-a")
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 1
    assert index.is_dependency_data_change_active()
    index.end_dependency_data_change(owner="writer-b")
    assert not index.is_dependency_data_change_active()
    publish_current_value()


def test_committed_rewarm_queue_is_claimed_after_the_last_writer(barrier_cache):
    index.begin_dependency_data_change(owner="writer-a")
    index.begin_dependency_data_change(owner="writer-b")
    index.record_committed_graphql_rewarm_keys(("recipe-a",))
    index.end_dependency_data_change(owner="writer-a")
    assert index.drain_committed_graphql_rewarm_keys() == ()
    index.record_committed_graphql_rewarm_keys(("recipe-b",))
    index.end_dependency_data_change(owner="writer-b")
    assert index.drain_committed_graphql_rewarm_keys() == ("recipe-a", "recipe-b")
    assert index.drain_committed_graphql_rewarm_keys() == ()


@pytest.mark.parametrize("applied", [False, True])
def test_failed_owned_begin_restores_existing_owner(barrier_cache, applied):
    index.begin_dependency_data_change(owner="existing-writer")
    original_set = barrier_cache.set
    failure = ConnectionError("owned begin metadata failure")
    failed = False

    def fail_once(key, value, timeout=None, version=None):
        nonlocal failed
        if key == index.DATA_CHANGE_OWNERS_KEY and not failed:
            failed = True
            if applied:
                original_set(key, value, timeout, version)
            raise failure
        return original_set(key, value, timeout, version)

    with patch.object(barrier_cache, "set", side_effect=fail_once):
        with pytest.raises(ConnectionError):
            index.begin_dependency_data_change(owner="failed-writer")
    index.end_dependency_data_change(owner="failed-writer")
    assert barrier_cache.get(index.DATA_CHANGE_COUNT_KEY) == 1
    assert index.is_dependency_data_change_active()
    index.end_dependency_data_change(owner="existing-writer")
    assert not index.is_dependency_data_change_active()
