from __future__ import annotations

from unittest.mock import Mock

from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.chat.audit import emit_chat_audit_event


AUDIT_EVENTS: list[dict[str, object]] = []


def _capture_audit_event(event: dict[str, object]) -> None:
    AUDIT_EVENTS.append(event)


class ChatAuditTests(SimpleTestCase):
    def setUp(self) -> None:
        AUDIT_EVENTS.clear()
        super().setUp()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "audit": {
                    "enabled": True,
                    "level": "tool_calls",
                    "max_result_size": 16,
                    "redact_fields": ["password", "token"],
                }
            }
        }
    )
    def test_emit_chat_audit_event_redacts_sensitive_fields_and_truncates_results(
        self,
    ) -> None:
        sink = Mock()

        emit_chat_audit_event(
            "tool_result",
            {
                "tool_name": "mutate",
                "args": {
                    "input": {
                        "username": "alice",
                        "password": "secret-value",
                        "api_token": "abc123",
                    }
                },
                "result": {"payload": "x" * 64},
            },
            sink=sink,
        )

        sink.assert_called_once()
        event = sink.call_args.args[0]
        assert event["args"]["input"] == {
            "username": "alice",
            "password": "[REDACTED]",
            "api_token": "[REDACTED]",
        }
        assert event["result"].endswith("...")
        assert len(event["result"]) == 19

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "audit": {
                    "enabled": True,
                    "level": "messages",
                }
            }
        }
    )
    def test_emit_chat_audit_event_skips_tool_calls_when_level_is_messages(
        self,
    ) -> None:
        sink = Mock()

        emit_chat_audit_event("tool_call", {"tool_name": "query"}, sink=sink)

        sink.assert_not_called()

    @override_settings(GENERAL_MANAGER={"CHAT": {"audit": {"enabled": False}}})
    def test_emit_chat_audit_event_skips_when_disabled(self) -> None:
        sink = Mock()

        emit_chat_audit_event("assistant_message", {"message": "hello"}, sink=sink)

        sink.assert_not_called()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "audit": {
                    "enabled": True,
                    "level": "messages",
                    "logger": "tests.unit.test_chat_audit._capture_audit_event",
                    "redact_fields": ["token"],
                    "max_result_size": 1000,
                }
            }
        }
    )
    def test_emit_chat_audit_event_resolves_configured_sink_and_redacts_lists(
        self,
    ) -> None:
        emit_chat_audit_event(
            "assistant_message",
            {
                "message": "done",
                "items": [{"api_token": "secret"}, {"name": "visible"}],
                "result": {"status": "ok"},
            },
        )

        assert AUDIT_EVENTS == [
            {
                "event_type": "assistant_message",
                "message": "done",
                "items": [{"api_token": "[REDACTED]"}, {"name": "visible"}],
                "result": '{"status": "ok"}',
            }
        ]
