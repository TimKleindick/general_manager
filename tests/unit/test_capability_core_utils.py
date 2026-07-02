"""Tests for core capability utility functions."""

from __future__ import annotations

from collections.abc import Mapping
import pytest
from unittest.mock import Mock

from general_manager.interface.capabilities.core.utils import with_observability


class PayloadCopyError(RuntimeError):
    """Raised when a test payload is unexpectedly copied."""


class HookError(RuntimeError):
    """Raised by observability hook test doubles."""


class OperationError(RuntimeError):
    """Raised by operation test callables."""


class HookAttributeError(RuntimeError):
    """Raised when reading an observability hook attribute fails."""


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


def test_with_observability_resolves_handler_once():
    class Target:
        handler_lookups = 0

        @property
        def get_capability_handler(self):
            self.handler_lookups += 1
            return lambda _name: None

    target = Target()

    result = with_observability(
        target,
        operation="test_op",
        payload={},
        func=lambda: "result",
    )

    assert result == "result"
    assert target.handler_lookups == 1


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


def test_with_observability_reuses_same_payload_copy_for_all_hooks():
    """All hooks for one invocation should receive the same shallow copy."""
    payload_ids: list[int] = []

    def remember_payload(**kwargs: object) -> None:
        payload = kwargs["payload"]
        assert isinstance(payload, dict)
        payload_ids.append(id(payload))

    capability = Mock()
    capability.before_operation = Mock(side_effect=remember_payload)
    capability.after_operation = Mock(side_effect=remember_payload)
    capability.on_error = None
    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)
    original_payload = {"key": "value"}

    result = with_observability(
        target,
        operation="test",
        payload=original_payload,
        func=lambda: "ok",
    )

    assert result == "ok"
    assert len(payload_ids) == 2
    assert payload_ids[0] == payload_ids[1]


def test_with_observability_does_not_copy_payload_without_capability():
    """Payload conversion should be skipped when no capability is registered."""

    class BrokenMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            if key == "key":
                return "value"
            raise KeyError(key)

        def __iter__(self):
            raise PayloadCopyError

        def __len__(self) -> int:
            return 1

    target = Mock()
    target.get_capability_handler = Mock(return_value=None)

    result = with_observability(
        target,
        operation="test",
        payload=BrokenMapping(),
        func=lambda: "ok",
    )

    assert result == "ok"


def test_with_observability_ignores_absent_hook_attributes():
    """Missing hook attributes should behave the same as hooks set to None."""

    class CapabilityWithoutHooks:
        pass

    target = Mock()
    target.get_capability_handler = Mock(return_value=CapabilityWithoutHooks())

    result = with_observability(target, operation="test", payload={}, func=lambda: "ok")

    assert result == "ok"


def test_with_observability_reads_hook_attributes_before_copying_payload():
    """Hook attribute lookup failures should happen before payload conversion."""

    class BrokenMapping(dict[str, object]):
        def __iter__(self):
            raise PayloadCopyError

    class BrokenCapability:
        @property
        def before_operation(self):
            raise HookAttributeError

    target = Mock()
    target.get_capability_handler = Mock(return_value=BrokenCapability())

    with pytest.raises(HookAttributeError):
        with_observability(
            target,
            operation="test",
            payload=BrokenMapping({"key": "value"}),
            func=lambda: "ok",
        )


def test_with_observability_non_callable_hook_value_raises_when_called():
    """Non-None hook attributes are called and fail normally if not callable."""
    capability = Mock()
    capability.before_operation = "not-callable"
    capability.after_operation = None
    capability.on_error = None
    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    with pytest.raises(TypeError):
        with_observability(target, operation="test", payload={}, func=lambda: "ok")


def test_with_observability_returns_func_result():
    """Test that with_observability returns the function's result."""
    target = Mock()
    target.get_capability_handler = Mock(return_value=None)

    expected_result = {"status": "complete", "data": [1, 2, 3]}

    def func():
        return expected_result

    result = with_observability(target, operation="query", payload={}, func=func)

    assert result is expected_result


def test_with_observability_returns_awaitable_unchanged():
    """Awaitables returned by func should pass through without wrapping."""
    target = Mock()
    target.get_capability_handler = Mock(return_value=None)

    class AwaitableResult:
        def __await__(self):
            if False:
                yield None
            return "complete"

    expected_result = AwaitableResult()

    result = with_observability(
        target,
        operation="query",
        payload={},
        func=lambda: expected_result,
    )

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


def test_with_observability_handler_lookup_exception_propagates():
    """Capability lookup errors should not be wrapped."""
    target = Mock()
    target.get_capability_handler = Mock(side_effect=HookError)

    with pytest.raises(HookError):
        with_observability(target, operation="test", payload={}, func=lambda: "ok")


def test_with_observability_before_exception_skips_func():
    """If before_operation fails, the wrapped callable is not executed."""
    func = Mock(return_value="unreached")
    capability = Mock()
    capability.before_operation = Mock(side_effect=HookError)
    capability.after_operation = Mock()
    capability.on_error = Mock()
    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    with pytest.raises(HookError):
        with_observability(target, operation="test", payload={}, func=func)

    func.assert_not_called()
    capability.after_operation.assert_not_called()
    capability.on_error.assert_not_called()


def test_with_observability_on_error_exception_replaces_operation_exception():
    """An on_error failure should replace the original operation exception."""
    operation_error = OperationError()
    hook_error = HookError()
    capability = Mock()
    capability.before_operation = None
    capability.after_operation = None
    capability.on_error = Mock(side_effect=hook_error)
    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    def func() -> str:
        raise operation_error

    with pytest.raises(HookError) as exc_info:
        with_observability(target, operation="test", payload={}, func=func)

    assert exc_info.value is hook_error


def test_with_observability_after_exception_replaces_successful_result():
    """An after_operation failure should replace a successful return value."""
    hook_error = HookError()
    capability = Mock()
    capability.before_operation = None
    capability.after_operation = Mock(side_effect=hook_error)
    capability.on_error = Mock()
    target = Mock()
    target.get_capability_handler = Mock(return_value=capability)

    with pytest.raises(HookError) as exc_info:
        with_observability(target, operation="test", payload={}, func=lambda: "ok")

    assert exc_info.value is hook_error
    capability.on_error.assert_not_called()


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
