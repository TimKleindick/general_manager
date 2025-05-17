from django.test import TestCase
from django.dispatch import Signal
from contextlib import contextmanager

from general_manager.cache.signals import dataChange, pre_data_change, post_data_change


@contextmanager
def capture_signal(signal: Signal):
    """Context manager to capture dispatched signal payloads."""
    calls = []

    def _receiver(sender, **kwargs):
        calls.append({"sender": sender, **kwargs})

    signal.connect(_receiver, weak=False)
    try:
        yield calls
    finally:
        signal.disconnect(_receiver)


class Dummy:
    """Test helper class decorated with @dataChange for create and update."""

    def __init__(self):
        # simulate existing state storage
        self._old_values = getattr(self, "_old_values", {})
        self.value = None

    @classmethod
    @dataChange
    def create(cls, new_value):
        inst = cls()
        inst.value = new_value
        return inst

    @dataChange
    def update(self, new_value):
        # store old relevant values before change
        self._old_values = getattr(self, "_old_values", {})
        self.value = new_value
        return self


class DataChangeSignalTests(TestCase):
    def setUp(self):
        # Clear any existing receivers before each test
        pre_data_change.receivers.clear()
        post_data_change.receivers.clear()

    def tearDown(self):
        # Clean up receivers after each test
        pre_data_change.receivers.clear()
        post_data_change.receivers.clear()

    def test_create_emits_pre_and_post(self):
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

    def test_update_emits_pre_and_post(self):
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
        inst = Dummy()
        result = inst.update("baz")
        self.assertIsInstance(result, Dummy)
        self.assertEqual(result.value, "baz")
