"""Tests for data-change signal dispatch and dependency-cache cleanup."""

from django.db import DEFAULT_DB_ALIAS
from django.test import TestCase
from django.dispatch import Signal
from contextlib import contextmanager
from typing import ClassVar
from unittest.mock import patch

from general_manager.cache import dependency_index
from general_manager.cache.dependency_index import (
    drain_invalidated_cache_keys_for_graphql_rewarm,
    is_dependency_data_change_active,
    record_invalidated_cache_keys_for_graphql_rewarm,
)
from general_manager.cache.signals import data_change, pre_data_change, post_data_change


@contextmanager
def capture_signal(signal: Signal):
    """Context manager to capture dispatched signal payloads."""
    calls = []

    def _receiver(sender, **kwargs):
        """Record one signal emission."""
        calls.append({"sender": sender, **kwargs})

    signal.connect(_receiver, weak=False)
    try:
        yield calls
    finally:
        signal.disconnect(_receiver)


class Dummy:
    """Test helper class decorated with @data_change for create and update."""

    def __init__(self):
        """Initialize a dummy instance with old-value storage."""
        # simulate existing state storage
        self._old_values = getattr(self, "_old_values", {})
        self.value = None

    @classmethod
    @data_change
    def create(cls, new_value):
        """Create a dummy instance with the provided value."""
        inst = cls()
        inst.value = new_value
        return inst

    @data_change
    def update(self, new_value):
        """Update the dummy value and return the instance."""
        # store old relevant values before change
        self._old_values = getattr(self, "_old_values", {})
        self.value = new_value
        return self

    @data_change
    def delete(self):
        """Delete the dummy instance by returning no result."""
        return None


class InvalidatingDummy:
    """Test helper that invalidates identification during delete."""

    def __init__(self):
        """Initialize the pre-delete identification."""
        self.identification = {"id": "before"}

    @data_change
    def delete(self):
        """Invalidate identification while deleting the instance."""
        self.identification["id"] = "after"
        self.identification = None
        return None


class RaisingDummy:
    """Test helper whose update operation raises."""

    @data_change
    def update(self):
        """Raise from a data-change wrapped method."""
        raise ValueError("boom")


class RecordingRaisingDummy:
    """Test helper that records pending rewarm keys before raising."""

    @data_change
    def update(self):
        """Record a pending key and raise from a wrapped method."""
        record_invalidated_cache_keys_for_graphql_rewarm(("stale-key",))
        raise ValueError("boom")


class NestedDataChangeDummy:
    """Test helper whose data-change method invokes another data-change method."""

    @data_change
    def outer(self):
        """Invoke an inner data-change method before returning."""
        self.inner()
        return self

    @data_change
    def inner(self):
        """Return this instance from a nested data-change method."""
        return self


class EventRecordingDummy:
    """Non-ORM helper that records when its mutation body executes."""

    events: ClassVar[list[str]] = []

    @data_change
    def update(self):
        """Record the immediate mutation body."""
        self.events.append("mutation")
        return self


class ClassMethodWrappedDummy:
    """Test helper for manually wrapping a classmethod object."""

    @staticmethod
    def build():
        """Return a new helper instance."""
        return ClassMethodWrappedDummy()


class DataChangeSignalTests(TestCase):
    """Verify data-change signal dispatch and cleanup behavior."""

    def setUp(self):
        """Isolate signal receivers for each test."""
        # Preserve existing receivers so they can be restored after the test run
        self._original_pre_receivers = list(pre_data_change.receivers)
        self._original_post_receivers = list(post_data_change.receivers)
        # Clear any existing receivers before each test
        pre_data_change.receivers.clear()
        post_data_change.receivers.clear()

    def tearDown(self):
        """Restore signal receivers after each test."""
        # Clean up receivers after each test
        pre_data_change.receivers.clear()
        post_data_change.receivers.clear()
        # Restore the original receivers to avoid leaking state into other tests
        pre_data_change.receivers[:] = self._original_pre_receivers
        post_data_change.receivers[:] = self._original_post_receivers

    def test_create_emits_pre_and_post(self):
        """Create operations emit pre-change and post-change signals."""
        # Capture pre and post signals
        with (
            capture_signal(pre_data_change) as pre_calls,
            capture_signal(post_data_change) as post_calls,
        ):
            result = Dummy.create("foo")

        # Assertions for pre_data_change
        self.assertEqual(len(pre_calls), 1)
        pre = pre_calls[0]
        self.assertIs(pre["sender"], Dummy)
        self.assertIsNone(pre["instance"])
        self.assertEqual(pre["action"], "create")

        # Assertions for post_data_change
        self.assertEqual(len(post_calls), 1)
        post = post_calls[0]
        self.assertIs(post["sender"], Dummy)
        self.assertIs(post["instance"], result)
        self.assertEqual(post["action"], "create")
        self.assertEqual(post["old_relevant_values"], {})

    def test_pre_and_post_receive_same_fresh_change_context_and_default_alias(self):
        """One mutation shares a context across signals and uses the default DB."""
        with (
            capture_signal(pre_data_change) as pre_calls,
            capture_signal(post_data_change) as post_calls,
        ):
            Dummy.create("foo")
            Dummy.create("bar")

        self.assertIs(pre_calls[0]["change_context"], post_calls[0]["change_context"])
        self.assertIs(pre_calls[1]["change_context"], post_calls[1]["change_context"])
        self.assertIsNot(pre_calls[0]["change_context"], pre_calls[1]["change_context"])
        self.assertEqual(pre_calls[0]["database_alias"], DEFAULT_DB_ALIAS)
        self.assertEqual(post_calls[0]["database_alias"], DEFAULT_DB_ALIAS)

    def test_pre_receiver_can_share_state_with_post_through_change_context(self):
        """Receivers can store per-mutation state in the mutable context mapping."""
        observed: list[object] = []

        def store_pre_state(sender, change_context, **kwargs):
            del sender, kwargs
            change_context["test"] = "captured"

        def read_pre_state(sender, change_context, **kwargs):
            del sender, kwargs
            observed.append(change_context["test"])

        pre_data_change.connect(store_pre_state, weak=False)
        post_data_change.connect(read_pre_state, weak=False)

        Dummy.create("foo")

        self.assertEqual(observed, ["captured"])

    def test_nested_mutations_receive_distinct_change_contexts(self):
        """Nested wrappers do not accidentally share per-call signal state."""
        with (
            capture_signal(pre_data_change) as pre_calls,
            capture_signal(post_data_change) as post_calls,
        ):
            NestedDataChangeDummy().outer()

        contexts_by_action = {
            call["action"]: call["change_context"] for call in pre_calls
        }
        self.assertIsNot(contexts_by_action["outer"], contexts_by_action["inner"])
        for call in post_calls:
            self.assertIs(call["change_context"], contexts_by_action[call["action"]])

    def test_non_orm_mutation_is_immediate_without_database_atomic(self):
        """Plain managers preserve synchronous pre/body/post execution."""
        EventRecordingDummy.events = []

        def record_pre(sender, **kwargs):
            del sender, kwargs
            EventRecordingDummy.events.append("pre")

        def record_post(sender, **kwargs):
            del sender, kwargs
            EventRecordingDummy.events.append("post")

        pre_data_change.connect(record_pre, weak=False)
        post_data_change.connect(record_post, weak=False)
        with patch("django.db.transaction.atomic") as atomic:
            EventRecordingDummy().update()

        atomic.assert_not_called()
        self.assertEqual(EventRecordingDummy.events, ["pre", "mutation", "post"])

    def test_orm_manager_uses_configured_alias_for_atomic_and_signals(self):
        """ORM-backed managers use their public interface database alias."""
        from general_manager.interface.orm_interface import OrmInterfaceBase

        class SecondaryInterface(OrmInterfaceBase):
            database = "secondary"

        class SecondaryManager:
            Interface = SecondaryInterface

            @data_change
            def update(self):
                return self

        with (
            capture_signal(pre_data_change) as pre_calls,
            capture_signal(post_data_change) as post_calls,
            patch("django.db.transaction.atomic") as atomic,
        ):
            SecondaryManager().update()

        atomic.assert_called_once_with(using="secondary")
        self.assertEqual(pre_calls[0]["database_alias"], "secondary")
        self.assertEqual(post_calls[0]["database_alias"], "secondary")

    def test_update_emits_pre_and_post(self):
        """Update operations emit pre-change and post-change signals."""
        inst = Dummy()
        inst._old_values = {"key": "old"}

        with (
            capture_signal(pre_data_change) as pre_calls,
            capture_signal(post_data_change) as post_calls,
        ):
            result = inst.update("bar")

        # Assertions for pre_data_change
        self.assertEqual(len(pre_calls), 1)
        pre = pre_calls[0]
        self.assertIs(pre["sender"], Dummy)
        self.assertIs(pre["instance"], inst)
        self.assertEqual(pre["action"], "update")

        # Assertions for post_data_change
        self.assertEqual(len(post_calls), 1)
        post = post_calls[0]
        self.assertIs(post["sender"], Dummy)
        self.assertIs(post["instance"], result)
        self.assertEqual(post["action"], "update")
        self.assertEqual(post["old_relevant_values"], {"key": "old"})

    def test_wrapper_returns_original_result(self):
        """The data_change wrapper returns the wrapped function result."""
        inst = Dummy()
        result = inst.update("baz")
        self.assertIsInstance(result, Dummy)
        self.assertEqual(result.value, "baz")

    def test_delete_returning_none_sends_delete_metadata_to_post_signal(self):
        """Delete operations returning None still send delete metadata."""
        inst = Dummy()

        with capture_signal(post_data_change) as post_calls:
            result = inst.delete()

        self.assertIsNone(result)
        self.assertEqual(len(post_calls), 1)
        post = post_calls[0]
        self.assertIs(post["sender"], Dummy)
        self.assertIsNone(post["instance"])
        self.assertIs(post["previous_instance"], inst)
        self.assertIsNone(post["identification"])
        self.assertEqual(post["action"], "delete")

    def test_delete_identification_uses_pre_mutation_snapshot(self):
        """Delete metadata uses the pre-mutation identification snapshot."""
        inst = InvalidatingDummy()

        with capture_signal(post_data_change) as post_calls:
            inst.delete()

        self.assertEqual(len(post_calls), 1)
        self.assertEqual(post_calls[0]["identification"], {"id": "before"})

    def test_rewarm_keys_enqueue_after_dependency_data_change_ends(self):
        """Pending rewarm keys enqueue after the dependency barrier is closed."""
        barrier_states: list[bool] = []
        enqueued_keys: list[tuple[str, ...]] = []

        def record_rewarm_key(sender, **kwargs):
            """Record a pending cache key during the post-change barrier."""
            del sender, kwargs
            barrier_states.append(is_dependency_data_change_active())
            record_invalidated_cache_keys_for_graphql_rewarm(("cache-key",))

        def enqueue_rewarm(cache_keys):
            """Capture the keys enqueued after the barrier closes."""
            enqueued_keys.append(tuple(cache_keys))
            self.assertFalse(is_dependency_data_change_active())
            return True

        post_data_change.connect(record_rewarm_key, weak=False)
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
            side_effect=enqueue_rewarm,
        ):
            Dummy.create("warm")

        self.assertEqual(barrier_states, [True])
        self.assertEqual(enqueued_keys, [("cache-key",)])

    def test_nested_data_change_rewarm_keys_enqueue_after_outer_barrier(self):
        """Nested data-change calls enqueue rewarm keys after the outer exit."""
        barrier_states: list[tuple[str, bool]] = []
        enqueued_keys: list[tuple[str, ...]] = []

        def record_rewarm_key(sender, **kwargs):
            """Record one pending cache key for each post-change action."""
            del sender
            action = kwargs["action"]
            barrier_states.append((action, is_dependency_data_change_active()))
            record_invalidated_cache_keys_for_graphql_rewarm((f"{action}-key",))

        def enqueue_rewarm(cache_keys):
            """Capture the final rewarm batch after all barriers close."""
            enqueued_keys.append(tuple(cache_keys))
            self.assertFalse(is_dependency_data_change_active())
            return True

        post_data_change.connect(record_rewarm_key, weak=False)
        with patch(
            "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
            side_effect=enqueue_rewarm,
        ):
            NestedDataChangeDummy().outer()

        self.assertEqual(barrier_states, [("inner", True), ("outer", True)])
        self.assertEqual(len(enqueued_keys), 1)
        self.assertEqual(set(enqueued_keys[0]), {"inner-key", "outer-key"})

    def test_cleanup_barrier_read_error_does_not_mask_primary_exception(self):
        def raise_from_post_change(sender, **kwargs):
            del sender, kwargs
            raise RuntimeError("primary")

        post_data_change.connect(raise_from_post_change, weak=False)
        with (
            patch(
                "general_manager.cache.dependency_index.is_dependency_data_change_active",
                side_effect=RuntimeError("cleanup"),
            ),
            patch("general_manager.cache.signals.logger.exception") as log,
            self.assertRaisesRegex(RuntimeError, "primary"),
        ):
            Dummy.create("warm")

        log.assert_called_once()
        self.assertFalse(is_dependency_data_change_active())

    def test_data_change_supports_wrapped_classmethod_object(self):
        """The wrapper can invoke a raw classmethod object."""

        def create(cls):
            """Build an instance from the provided class."""
            return cls.build()

        wrapped = data_change(classmethod(create))

        with capture_signal(post_data_change) as post_calls:
            result = wrapped(ClassMethodWrappedDummy)

        self.assertIsInstance(result, ClassMethodWrappedDummy)
        self.assertIs(post_calls[0]["sender"], ClassMethodWrappedDummy)
        self.assertEqual(post_calls[0]["action"], "create")

    def test_data_change_clears_barrier_when_wrapped_function_raises(self):
        """Failed mutations drain pending rewarm keys without enqueueing them."""
        with self.assertRaisesRegex(ValueError, "boom"):
            RecordingRaisingDummy().update()

        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(drain_invalidated_cache_keys_for_graphql_rewarm(), ())

    def test_cleanup_failure_is_logged_when_wrapped_function_already_failed(self):
        """Cleanup errors are logged when another exception is already active."""
        original_end = dependency_index.end_dependency_data_change

        def cleanup_then_raise():
            """Run normal cleanup, then simulate a cleanup failure."""
            original_end()
            raise RuntimeError("cleanup")

        with (
            patch(
                "general_manager.cache.dependency_index.end_dependency_data_change",
                side_effect=cleanup_then_raise,
            ),
            patch("general_manager.cache.signals.logger.exception") as log,
            self.assertRaisesRegex(ValueError, "boom"),
        ):
            RaisingDummy().update()

        log.assert_called_once()
        self.assertFalse(is_dependency_data_change_active())

    def test_cleanup_failure_raises_when_wrapped_function_succeeded(self):
        """Cleanup errors propagate when the wrapped function succeeded."""
        original_end = dependency_index.end_dependency_data_change

        def cleanup_then_raise():
            """Run normal cleanup, then simulate a cleanup failure."""
            original_end()
            raise RuntimeError("cleanup")

        with (
            patch(
                "general_manager.cache.dependency_index.end_dependency_data_change",
                side_effect=cleanup_then_raise,
            ),
            self.assertRaisesRegex(RuntimeError, "cleanup"),
        ):
            Dummy.create("warm")

        self.assertFalse(is_dependency_data_change_active())

    def test_rewarm_enqueue_failure_is_logged(self):
        """Rewarm enqueue failures are logged without failing the mutation."""

        def record_rewarm_key(sender, **kwargs):
            """Record one pending rewarm key from a post-change receiver."""
            del sender, kwargs
            record_invalidated_cache_keys_for_graphql_rewarm(("cache-key",))

        post_data_change.connect(record_rewarm_key, weak=False)
        with (
            patch(
                "general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup",
                side_effect=RuntimeError("boom"),
            ),
            patch("general_manager.cache.signals.logger.exception") as log,
        ):
            Dummy.create("warm")

        log.assert_called_once_with("GraphQL warm-up requeue failed.")

    def test_nested_dependency_data_change_keeps_outer_barrier_active(self):
        """Nested data-change cleanup keeps the outer barrier until final exit."""
        dependency_index.begin_dependency_data_change()
        dependency_index.begin_dependency_data_change()

        dependency_index.end_dependency_data_change()
        self.assertTrue(is_dependency_data_change_active())

        dependency_index.end_dependency_data_change()
        self.assertFalse(is_dependency_data_change_active())
