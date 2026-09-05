from __future__ import annotations

from types import SimpleNamespace

from django.core.cache import cache
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.chat.rate_limits import (
    enforce_chat_rate_limit,
    get_query_timeout_ms,
)


class ChatRateLimitTests(SimpleTestCase):
    def tearDown(self) -> None:
        cache.clear()
        super().tearDown()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "rate_limit": {
                    "requests": 1,
                    "window_seconds": 45,
                }
            }
        }
    )
    def test_request_limit_uses_user_identifier_before_session_or_ip(self) -> None:
        scope = {
            "user": SimpleNamespace(pk=42),
            "session": SimpleNamespace(session_key="session-key"),
            "client": ("192.0.2.1", 80),
        }

        assert enforce_chat_rate_limit(scope) is None
        assert enforce_chat_rate_limit(scope) == {
            "scope": "user:42",
            "retry_after_seconds": 45,
        }

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "rate_limit": {
                    "output_tokens": 2,
                    "window_seconds": 30,
                }
            }
        }
    )
    def test_output_token_budget_blocks_on_increment(self) -> None:
        scope = {"client": ("198.51.100.7", 443)}

        assert (
            enforce_chat_rate_limit(
                scope,
                output_tokens=1,
                count_request=False,
            )
            is None
        )
        assert enforce_chat_rate_limit(
            scope,
            output_tokens=2,
            count_request=False,
        ) == {
            "scope": "ip:198.51.100.7",
            "retry_after_seconds": 30,
        }

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "rate_limit": {
                    "tokens": 10,
                    "input_tokens": 1,
                    "output_tokens": 10,
                    "window_seconds": 30,
                }
            }
        }
    )
    def test_exact_cap_blocks_next_work_and_records_every_token_counter(self) -> None:
        scope = {"client": ("198.51.100.8", 443)}

        assert (
            enforce_chat_rate_limit(
                scope, input_tokens=1, output_tokens=2, count_request=False
            )
            is None
        )
        assert enforce_chat_rate_limit(scope, count_request=False) == {
            "scope": "ip:198.51.100.8",
            "retry_after_seconds": 30,
        }

        assert enforce_chat_rate_limit(
            scope, input_tokens=1, output_tokens=2, count_request=False
        ) == {
            "scope": "ip:198.51.100.8",
            "retry_after_seconds": 30,
        }
        prefix = "general_manager:chat_rate_limit:ip:198.51.100.8"
        assert cache.get(f"{prefix}:tokens") == 6
        assert cache.get(f"{prefix}:input_tokens") == 2
        assert cache.get(f"{prefix}:output_tokens") == 4

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "rate_limit": {
                    "tokens": 1,
                    "window_seconds": 30,
                }
            }
        }
    )
    def test_invalid_existing_counter_value_is_treated_as_zero(self) -> None:
        cache.set("general_manager:chat_rate_limit:anonymous:tokens", "not-an-int")

        assert enforce_chat_rate_limit({}) is None

    @override_settings(GENERAL_MANAGER={"CHAT": {"query_timeout_seconds": 0.001}})
    def test_query_timeout_seconds_rounds_up_to_at_least_one_millisecond(self) -> None:
        assert get_query_timeout_ms() == 1
