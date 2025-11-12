"""Capability-specific exception types."""


class CapabilityBindingError(RuntimeError):
    """Raised when a capability cannot be attached to an interface class."""

    def __init__(self, capability_name: str, reason: str) -> None:
        message = f"Capability '{capability_name}' could not be attached: {reason}"
        super().__init__(message)
