from __future__ import annotations

from unittest import mock

import pytest

from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.core.utils import with_observability


class DummyTarget:
    __name__ = "DummyTarget"

    def __init__(self, capability: LoggingObservabilityCapability):
        """
        Initialize the instance with a logging observability capability.

        Parameters:
            capability (LoggingObservabilityCapability): Capability used to record observability events (start, end, error) for operations on the target.
        """
        self._capability = capability

    def get_capability_handler(self, name: str):
        """
        Retrieve a named capability handler exposed by the target.

        Parameters:
            name (str): Name of the capability to retrieve.

        Returns:
            The capability handler instance if the named capability exists (for example, "observability"), or None if the capability is not available.
        """
        if name == "observability":
            return self._capability
        return None


def test_with_observability_logs_before_and_after():
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    target = DummyTarget(capability)
    sentinel = object()

    def func():
        """
        Provide a sentinel object used by tests.

        Returns:
            The sentinel object used as a unique test value.
        """
        return sentinel

    result = with_observability(
        target,
        operation="demo.op",
        payload={"foo": 1, "bar": 2},
        func=func,
    )

    assert result is sentinel
    expected_context = {
        "operation": "demo.op",
        "target": DummyTarget.__name__,
        "payload_keys": ["bar", "foo"],
    }
    fake_logger.debug.assert_any_call(
        "interface operation start", context=expected_context
    )
    after_context = dict(expected_context)
    after_context["result_type"] = object.__name__
    fake_logger.debug.assert_any_call("interface operation end", context=after_context)


def test_with_observability_logs_errors_and_propagates():
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    target = DummyTarget(capability)

    def func():
        """
        Always raises a ValueError with the message 'boom'.

        Raises:
            ValueError: Always raised with message "boom".
        """
        raise ValueError("boom")

    with pytest.raises(ValueError):
        with_observability(
            target,
            operation="demo.error",
            payload={"foo": "bar"},
            func=func,
        )

    fake_logger.error.assert_called_once()
    error_context = fake_logger.error.call_args.kwargs["context"]
    assert error_context["operation"] == "demo.error"
    assert error_context["target"] == DummyTarget.__name__
    assert error_context["payload_keys"] == ["foo"]
    assert "ValueError" in error_context["error"]
