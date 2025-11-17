"""Capability-specific exception types."""


class CapabilityBindingError(RuntimeError):
    """Raised when a capability cannot be attached to an interface class."""

    def __init__(self, capability_name: str, reason: str) -> None:
        """
        Initialize the CapabilityBindingError with details about the failed capability attachment.

        Parameters:
            capability_name (str): Name of the capability that could not be attached.
            reason (str): Explanation of why the attachment failed.
        """
        message = f"Capability '{capability_name}' could not be attached: {reason}"
        super().__init__(message)
