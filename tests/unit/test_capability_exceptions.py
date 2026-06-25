"""Tests for capability-specific exceptions."""

from __future__ import annotations

import pytest

import general_manager.interface.capabilities.exceptions as capability_exceptions
from general_manager.interface.capabilities.exceptions import CapabilityBindingError


def test_capability_exceptions_public_exports() -> None:
    """The exceptions module exposes its public exception type explicitly."""
    assert capability_exceptions.__all__ == ["CapabilityBindingError"]


def test_capability_binding_error_message() -> None:
    """Test CapabilityBindingError message formatting."""
    error = CapabilityBindingError("read", "missing required attribute: get_data")

    assert "Capability 'read' could not be attached" in str(error)
    assert "missing required attribute: get_data" in str(error)
    assert error.args == (
        "Capability 'read' could not be attached: missing required attribute: get_data",
    )


def test_capability_binding_error_stores_original_details() -> None:
    """Capability name and reason should remain inspectable after formatting."""
    error = CapabilityBindingError("history", "missing required attributes: get_data")

    assert error.capability_name == "history"
    assert error.reason == "missing required attributes: get_data"


def test_capability_binding_error_inheritance() -> None:
    """Test that CapabilityBindingError inherits from RuntimeError."""
    error = CapabilityBindingError("write", "test reason")

    assert isinstance(error, RuntimeError)
    assert isinstance(error, Exception)


def test_capability_binding_error_with_empty_reason() -> None:
    """Test CapabilityBindingError with empty reason."""
    error = CapabilityBindingError("delete", "")

    assert str(error) == "Capability 'delete' could not be attached: "
    assert error.reason == ""


def test_capability_binding_error_with_special_characters() -> None:
    """Test CapabilityBindingError with special characters in reason."""
    reason = "missing: get_data(), set_data(); expected: <callable>"
    error = CapabilityBindingError("custom", reason)

    message = str(error)
    assert "custom" in message
    assert reason in message


def test_capability_binding_error_can_be_raised() -> None:
    """Test that CapabilityBindingError can be raised and caught."""
    with pytest.raises(CapabilityBindingError) as exc_info:
        raise CapabilityBindingError("test_cap", "test failure")

    assert exc_info.value.args[0].startswith("Capability 'test_cap'")


def test_capability_binding_error_with_multiline_reason() -> None:
    """Test CapabilityBindingError with multiline reason."""
    reason = "Multiple issues:\n1. Missing method\n2. Invalid type"
    error = CapabilityBindingError("validation", reason)

    message = str(error)
    assert "validation" in message
    assert error.reason == reason
    assert "\n" in message
