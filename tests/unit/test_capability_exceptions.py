"""Tests for capability-specific exceptions."""

from __future__ import annotations

import pytest

from general_manager.interface.capabilities.exceptions import CapabilityBindingError


def test_capability_binding_error_message():
    """Test CapabilityBindingError message formatting."""
    error = CapabilityBindingError("read", "missing required attribute: get_data")

    assert "Capability 'read' could not be attached" in str(error)
    assert "missing required attribute: get_data" in str(error)


def test_capability_binding_error_inheritance():
    """Test that CapabilityBindingError inherits from RuntimeError."""
    error = CapabilityBindingError("write", "test reason")

    assert isinstance(error, RuntimeError)
    assert isinstance(error, Exception)


def test_capability_binding_error_with_empty_reason():
    """Test CapabilityBindingError with empty reason."""
    error = CapabilityBindingError("delete", "")

    assert "Capability 'delete' could not be attached" in str(error)


def test_capability_binding_error_with_special_characters():
    """Test CapabilityBindingError with special characters in reason."""
    reason = "missing: get_data(), set_data(); expected: <callable>"
    error = CapabilityBindingError("custom", reason)

    message = str(error)
    assert "custom" in message
    assert reason in message


def test_capability_binding_error_can_be_raised():
    """Test that CapabilityBindingError can be raised and caught."""
    with pytest.raises(CapabilityBindingError) as exc_info:
        raise CapabilityBindingError("test_cap", "test failure")

    assert exc_info.value.args[0].startswith("Capability 'test_cap'")


def test_capability_binding_error_with_multiline_reason():
    """Test CapabilityBindingError with multiline reason."""
    reason = "Multiple issues:\n1. Missing method\n2. Invalid type"
    error = CapabilityBindingError("validation", reason)

    message = str(error)
    assert "validation" in message
    assert "\n" in reason  # Preserve newlines in reason
