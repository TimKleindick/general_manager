"""Tests for core capability utility functions."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from general_manager.interface.capabilities.core.utils import with_observability


def test_with_observability_no_handler():
    """Test with_observability when target has no get_capability_handler."""
    target = object()
    calls = []

    def func():
        calls.append("executed")
        return "result"

    result = with_observability(
        target, operation="test_op", payload={"key": "value"}, func=func
    )

    assert result == "result"
    assert calls == ["executed"]


def test_with_observability_no_capability():
    """Test with_observability when no observability capability is registered."""
    target = Mock()
    target.get_capability_handler = Mock(return_value=None)
    calls = []

    def func():
        calls.append("executed")
        return 42

    result = with_observability(target, operation="test_op", payload={}, func=func)

    assert result == 42
    assert calls == ["executed"]
    target.get_capability_handler.assert_called_once_with("observability")


def test_with_observability_before_operation():
    """Test that before_operation is called when present."""
    capability = Mock()
    capability.before_operation = Mock()
    capability.after_operation = None
    capability.on_error = None

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    def func():
        return "result"

    payload = {"test": "data"}
    with_observability(target, operation="create", payload=payload, func=func)

    capability.before_operation.assert_called_once()
    call_kwargs = capability.before_operation.call_args[1]
    assert call_kwargs["operation"] == "create"
    assert call_kwargs["target"] is target
    assert call_kwargs["payload"] == payload


def test_with_observability_after_operation():
    """Test that after_operation is called when present."""
    capability = Mock()
    capability.before_operation = None
    capability.after_operation = Mock()
    capability.on_error = None

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    def func():
        return "success"

    payload = {"id": 123}
    result = with_observability(target, operation="update", payload=payload, func=func)

    assert result == "success"
    capability.after_operation.assert_called_once()
    call_kwargs = capability.after_operation.call_args[1]
    assert call_kwargs["operation"] == "update"
    assert call_kwargs["target"] is target
    assert call_kwargs["payload"] == payload
    assert call_kwargs["result"] == "success"


def test_with_observability_on_error():
    """Test that on_error is called when func raises an exception."""
    capability = Mock()
    capability.before_operation = None
    capability.after_operation = None
    capability.on_error = Mock()

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    test_error = ValueError("test error")

    def func():
        raise test_error

    payload = {"action": "delete"}

    with pytest.raises(ValueError, match="test error"):
        with_observability(target, operation="delete", payload=payload, func=func)

    capability.on_error.assert_called_once()
    call_kwargs = capability.on_error.call_args[1]
    assert call_kwargs["operation"] == "delete"
    assert call_kwargs["target"] is target
    assert call_kwargs["payload"] == payload
    assert call_kwargs["error"] is test_error


def test_with_observability_all_hooks():
    """Test that all hooks are called in correct order."""
    call_order = []

    capability = Mock()
    capability.before_operation = Mock(
        side_effect=lambda **_kwargs: call_order.append("before")
    )
    capability.after_operation = Mock(
        side_effect=lambda **_kwargs: call_order.append("after")
    )
    capability.on_error = None

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    def func():
        call_order.append("func")
        return "done"

    with_observability(target, operation="op", payload={}, func=func)

    assert call_order == ["before", "func", "after"]


def test_with_observability_payload_copy():
    """Test that payload is copied to prevent mutations."""
    capability = Mock()
    capability.before_operation = Mock()
    capability.after_operation = None
    capability.on_error = None

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    original_payload = {"mutable": "value"}

    def func():
        # Try to modify payload during execution
        return "ok"

    with_observability(target, operation="test", payload=original_payload, func=func)

    # Original payload should be unchanged
    assert original_payload == {"mutable": "value"}

    # Capability should receive a copy
    call_kwargs = capability.before_operation.call_args[1]
    assert call_kwargs["payload"] == original_payload
    assert call_kwargs["payload"] is not original_payload


def test_with_observability_returns_func_result():
    """Test that with_observability returns the function's result."""
    target = Mock()
    target.get_capability_handler = Mock(return_value=None)

    expected_result = {"status": "complete", "data": [1, 2, 3]}

    def func():
        return expected_result

    result = with_observability(target, operation="query", payload={}, func=func)

    assert result is expected_result


def test_with_observability_func_receives_no_args():
    """Test that the func callable is invoked with no arguments."""
    capability = Mock()
    capability.before_operation = None
    capability.after_operation = None
    capability.on_error = None

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    func = Mock(return_value="result")

    with_observability(target, operation="test", payload={}, func=func)

    func.assert_called_once_with()  # No arguments


def test_with_observability_exception_propagates():
    """Test that exceptions are propagated after calling on_error."""
    capability = Mock()
    capability.on_error = Mock()

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    class CustomError(Exception):
        """Custom error with predefined message."""

        default_message = "custom message"

        def __init__(self, message: str | None = None):
            super().__init__(message or self.default_message)

    def func():
        raise CustomError()

    with pytest.raises(CustomError, match="custom message"):
        with_observability(target, operation="test", payload={}, func=func)


def test_with_observability_missing_hooks_ignored():
    """Test that missing hooks (None) are gracefully ignored."""
    capability = Mock()
    # Explicitly set to None
    capability.before_operation = None
    capability.after_operation = None
    capability.on_error = None

    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    def func():
        return "ok"

    # Should not raise even though hooks are None
    result = with_observability(target, operation="test", payload={}, func=func)
    assert result == "ok"
