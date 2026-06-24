"""Capability-specific exception types."""


class CapabilityBindingError(RuntimeError):
    """
    Raised when a capability cannot be attached to an interface class.

    The error keeps the original capability name and failure reason as public
    attributes and formats ``str(error)`` as
    ``"Capability '<name>' could not be attached: <reason>"``. Empty reasons
    are preserved, including the trailing separator in the formatted message.
    """

    def __init__(self, capability_name: str, reason: str) -> None:
        """
        Initialize the error with failed binding details.

        Parameters:
            capability_name: Name of the capability that could not be attached.
            reason: Explanation of why the attachment failed. The value is
                stored unchanged and may be an empty or multiline string.

        Raises:
            Exception: Exceptions raised while formatting the message from
                non-string runtime values propagate if callers bypass static
                typing.
        """
        self.capability_name = capability_name
        self.reason = reason
        message = f"Capability '{capability_name}' could not be attached: {reason}"
        super().__init__(message)


__all__ = ["CapabilityBindingError"]
