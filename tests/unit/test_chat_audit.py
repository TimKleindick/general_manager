from __future__ import annotations

from unittest.mock import Mock

from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.chat.audit import emit_chat_audit_event


class ChatAuditTests(SimpleTestCase):
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
