from __future__ import annotations

from unittest import mock

import pytest

from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.core.utils import with_observability


class StatusError(RuntimeError):
    """Test exception that exposes HTTP-style status metadata."""

    status_code = 503


class NameLookupError(ValueError):
    """Raised when a test target name lookup fails."""


class MetadataLookupError(ValueError):
    """Raised when test result metadata lookup fails."""


class DummyTarget:
    __name__ = "DummyTarget"

    def __init__(self, capability: LoggingObservabilityCapability) -> None:
        """
        Initialize the instance with a logging observability capability.

        Parameters:
            capability (LoggingObservabilityCapability): Capability used to record observability events (start, end, error) for operations on the target.
        """
        self._capability = capability

    def get_capability_handler(
        self, name: str
    ) -> LoggingObservabilityCapability | None:
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


class PlainTarget:
    """Target without a ``__name__`` attribute."""


class NonStringNameTarget:
    """Target whose ``__name__`` is not usable as a logging target string."""

    __name__ = 123


class AttributeErrorNameTarget:
    """Target whose optional ``__name__`` lookup behaves like a missing name."""

    @property
    def __name__(self) -> str:
        raise AttributeError


class ValueErrorNameTarget:
    """Target whose ``__name__`` lookup fails with a non-optional error."""

    @property
    def __name__(self) -> str:
        raise NameLookupError


class ResultWithMetadata:
    """Operation result carrying observability metadata."""

    def __init__(self) -> None:
        self.metadata = {
            "status_code": 201,
            "retry_count": 2,
            "request_id": "result-request",
        }


class ResultWithNoneMetadata:
    """Operation result carrying explicit ``None`` observability metadata."""

    def __init__(self) -> None:
        self.metadata = {
            "status_code": None,
            "retry_count": None,
            "request_id": None,
        }


class ResultWithNonMappingMetadata:
    """Operation result carrying metadata that should be ignored."""

    metadata = ("status_code", 201)


class ResultWithAttributeErrorMetadata:
    """Operation result whose optional metadata lookup behaves like missing."""

    @property
    def metadata(self) -> dict[str, object]:
        raise AttributeError


class ResultWithValueErrorMetadata:
    """Operation result whose metadata lookup fails with a non-optional error."""

    @property
    def metadata(self) -> dict[str, object]:
        raise MetadataLookupError


def test_with_observability_logs_before_and_after() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    target = DummyTarget(capability)
    sentinel = object()

    def func() -> object:
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


def test_logging_observability_records_selected_payload_metadata() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    payload = {
        "service": "billing",
        "method": "GET",
        "path": "/invoices",
        "status_code": 200,
        "retry_count": 1,
        "request_id": "payload-request",
        "ignored": "not logged as metadata",
    }

    capability.before_operation(
        operation="demo.payload", target=PlainTarget(), payload=payload
    )

    fake_logger.debug.assert_called_once_with(
        "interface operation start",
        context={
            "operation": "demo.payload",
            "target": "PlainTarget",
            "payload_keys": [
                "ignored",
                "method",
                "path",
                "request_id",
                "retry_count",
                "service",
                "status_code",
            ],
            "service": "billing",
            "method": "GET",
            "path": "/invoices",
            "status_code": 200,
            "retry_count": 1,
            "request_id": "payload-request",
        },
    )


def test_logging_observability_includes_present_none_payload_metadata() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.before_operation(
        operation="demo.none_payload",
        target=PlainTarget(),
        payload={
            "service": None,
            "method": None,
            "path": None,
            "status_code": None,
            "retry_count": None,
            "request_id": None,
        },
    )

    context = fake_logger.debug.call_args.kwargs["context"]
    assert context["service"] is None
    assert context["method"] is None
    assert context["path"] is None
    assert context["status_code"] is None
    assert context["retry_count"] is None
    assert context["request_id"] is None


def test_logging_observability_result_metadata_overrides_payload_metadata() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.after_operation(
        operation="demo.result",
        target=NonStringNameTarget(),
        payload={
            "status_code": 200,
            "retry_count": 1,
            "request_id": "payload-request",
        },
        result=ResultWithMetadata(),
    )

    fake_logger.debug.assert_called_once_with(
        "interface operation end",
        context={
            "operation": "demo.result",
            "target": "NonStringNameTarget",
            "payload_keys": ["request_id", "retry_count", "status_code"],
            "status_code": 201,
            "retry_count": 2,
            "request_id": "result-request",
            "result_type": "ResultWithMetadata",
        },
    )


def test_logging_observability_treats_attribute_error_name_as_missing() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.before_operation(
        operation="demo.target_attribute_error",
        target=AttributeErrorNameTarget(),
        payload={},
    )

    context = fake_logger.debug.call_args.kwargs["context"]
    assert context["target"] == "AttributeErrorNameTarget"


def test_logging_observability_propagates_non_attribute_error_name_lookup() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    with pytest.raises(NameLookupError):
        capability.before_operation(
            operation="demo.target_value_error",
            target=ValueErrorNameTarget(),
            payload={},
        )


def test_logging_observability_includes_present_none_result_metadata() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.after_operation(
        operation="demo.none_result",
        target=PlainTarget(),
        payload={
            "status_code": 200,
            "retry_count": 1,
            "request_id": "payload-request",
        },
        result=ResultWithNoneMetadata(),
    )

    context = fake_logger.debug.call_args.kwargs["context"]
    assert context["status_code"] is None
    assert context["retry_count"] is None
    assert context["request_id"] is None


def test_logging_observability_ignores_non_mapping_result_metadata() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.after_operation(
        operation="demo.non_mapping_result",
        target=PlainTarget(),
        payload={"status_code": 200},
        result=ResultWithNonMappingMetadata(),
    )

    context = fake_logger.debug.call_args.kwargs["context"]
    assert context["status_code"] == 200
    assert context["result_type"] == "ResultWithNonMappingMetadata"


def test_logging_observability_treats_attribute_error_metadata_as_missing() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.after_operation(
        operation="demo.metadata_attribute_error",
        target=PlainTarget(),
        payload={"status_code": 200},
        result=ResultWithAttributeErrorMetadata(),
    )

    context = fake_logger.debug.call_args.kwargs["context"]
    assert context["status_code"] == 200
    assert context["result_type"] == "ResultWithAttributeErrorMetadata"


def test_logging_observability_propagates_non_attribute_error_metadata_lookup() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    with pytest.raises(MetadataLookupError):
        capability.after_operation(
            operation="demo.metadata_value_error",
            target=PlainTarget(),
            payload={},
            result=ResultWithValueErrorMetadata(),
        )


def test_with_observability_logs_errors_and_propagates() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    target = DummyTarget(capability)

    def func() -> object:
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


def test_logging_observability_error_context_uses_exact_error_keys() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    error = StatusError("unavailable")

    capability.on_error(
        operation="demo.error",
        target=PlainTarget(),
        payload={"request_id": "payload-request"},
        error=error,
    )

    fake_logger.error.assert_called_once_with(
        "interface operation error",
        context={
            "operation": "demo.error",
            "target": "PlainTarget",
            "payload_keys": ["request_id"],
            "request_id": "payload-request",
            "error": repr(error),
            "error_class": "StatusError",
            "status_code": 503,
        },
    )


def test_logging_observability_omits_missing_error_status_code() -> None:
    fake_logger = mock.MagicMock()
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.on_error(
        operation="demo.error",
        target=PlainTarget(),
        payload={},
        error=RuntimeError("boom"),
    )

    context = fake_logger.error.call_args.kwargs["context"]
    assert "status_code" not in context


def test_logging_observability_skips_context_when_debug_disabled() -> None:
    fake_logger = mock.MagicMock()
    fake_logger.isEnabledFor.return_value = False
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    target = object()
    payload = {"expensive": object()}

    with mock.patch.object(capability, "_context", side_effect=AssertionError):
        capability.before_operation(operation="filter", target=target, payload=payload)
        capability.after_operation(
            operation="filter",
            target=target,
            payload=payload,
            result=object(),
        )

    fake_logger.debug.assert_not_called()


def test_logging_observability_still_builds_error_context() -> None:
    fake_logger = mock.MagicMock()
    fake_logger.isEnabledFor.return_value = False
    with mock.patch(
        "general_manager.interface.capabilities.core.observability.get_logger",
        return_value=fake_logger,
    ):
        capability = LoggingObservabilityCapability()

    capability.on_error(
        operation="filter",
        target=object(),
        payload={"id": 1},
        error=ValueError("boom"),
    )

    fake_logger.error.assert_called_once()
