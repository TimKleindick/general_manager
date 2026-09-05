from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import Client, TransactionTestCase
from django.test.utils import override_settings
from django.utils import timezone

from general_manager.chat.bootstrap import ensure_chat_http_routes
from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.context import (
    ChatSummaryRateLimitExceeded,
    summarize_messages_with_provider,
)
from general_manager.chat.errors import public_chat_error
from general_manager.chat.models import (
    ChatConversation,
    ChatPendingConfirmation,
    append_chat_message,
    create_pending_confirmation,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.turns import TurnState
from general_manager.chat.views import _run_provider_turn
from tests import test_urls


class _Session:
    def __init__(self, session_key: str) -> None:
        self.session_key = session_key


class _SummaryAndAnswerProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls.append(list(messages))
        if messages[0].content.startswith("Summarize the prior conversation"):
            yield TextChunkEvent(content="compressed history")
            yield DoneEvent(usage=TokenUsage(input_tokens=2, output_tokens=3))
            return
        yield TextChunkEvent(content="answer using history")
        yield DoneEvent(usage=TokenUsage(input_tokens=5, output_tokens=7))


class _QueryThenAnswerProvider:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield ToolCallEvent(
                id="query-1",
                name="query",
                args={"manager": "Part", "fields": ["name"]},
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=0))
            return
        yield TextChunkEvent(content="answer")
        yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))


class _DisabledToolsProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.calls += 1
        try:
            yield ToolCallEvent(
                id="disabled-query",
                name="query",
                args={"manager": "Part", "fields": ["name"]},
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=4, output_tokens=5))
        finally:
            self.closed = True


class _ConfirmationProvider:
    instances: ClassVar[list[_ConfirmationProvider]] = []

    def __init__(self) -> None:
        self.calls = 0
        self.__class__.instances.append(self)

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        self.calls += 1
        if self.calls == 1:
            yield ToolCallEvent(
                id="pending-create",
                name="mutate",
                args={"mutation": "createPart", "input": {"name": "Bolt"}},
            )
            yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))
            return
        yield TextChunkEvent(content="confirmed")
        yield DoneEvent(usage=TokenUsage(input_tokens=1, output_tokens=1))


class _ReconnectToolProvider:
    def __init__(self, *, mutation: bool) -> None:
        self.mutation = mutation
        self.calls = 0

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        self.calls += 1
        if self.mutation:
            yield ToolCallEvent(
                id="second-mutation",
                name="mutate",
                args={"mutation": "createPart", "input": {"name": "Nut"}},
            )
        else:
            yield ToolCallEvent(
                id="disallowed-read",
                name="query",
                args={"manager": "Part", "fields": ["name"]},
            )
        yield DoneEvent(usage=TokenUsage())


def _scope(user: object, session_key: str) -> dict[str, object]:
    return {
        "user": user,
        "session": _Session(session_key),
        "client": ("127.0.0.1", 80),
    }


def _state_payload(
    *,
    rounds: int,
    mutations: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    tool_retries: int = 0,
) -> dict[str, int]:
    return {
        "rounds": rounds,
        "mutations": mutations,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "tool_retries": tool_retries,
    }


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "enabled": True,
            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
            "url": "/chat/",
            "max_recent_messages": 2,
            "summarize_after": 2,
            "max_mutations_per_message": 1,
            "rate_limit": {"requests": 100, "window_seconds": 30},
        }
    }
)
class ChatTurnBudgetConsumerTests(TransactionTestCase):
    def setUp(self) -> None:
        cache.clear()

    def tearDown(self) -> None:
        cache.clear()

    def _consumer(
        self, provider: object, session_key: str = "turn-budget"
    ) -> ChatConsumer:
        consumer = ChatConsumer()
        consumer.scope = _scope(AnonymousUser(), session_key)
        consumer.session_key = session_key
        consumer.provider = provider
        consumer.conversation = None
        consumer._history_cache = []
        consumer.channel_name = "chat.turn-budget"
        return consumer

    def test_websocket_summary_and_answer_report_aggregate_usage(self) -> None:
        conversation = ChatConversation.objects.create(session_key="summary-aggregate")
        append_chat_message(conversation, role="user", content="old question")
        append_chat_message(conversation, role="assistant", content="old answer")
        append_chat_message(conversation, role="user", content="recent question")
        provider = _SummaryAndAnswerProvider()
        consumer = self._consumer(provider, "summary-aggregate")

        async def run() -> None:
            with (
                patch.object(
                    consumer,
                    "_get_persistent_conversation",
                    new=AsyncMock(return_value=conversation),
                ),
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch.object(ChatConsumer, "_build_tool_definitions", return_value=[]),
                patch(
                    "general_manager.chat.consumer.get_planned_chat_settings",
                    return_value=SimpleNamespace(enabled=False),
                ),
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
                patch(
                    "general_manager.chat.context.enforce_chat_rate_limit",
                    return_value=None,
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "latest"})

            assert send.await_args_list[-1].args[0] == {
                "type": "done",
                "usage": {"input_tokens": 7, "output_tokens": 10},
            }
            assert len(provider.calls) == 2

        asyncio.run(run())

    def test_websocket_summary_consumes_round_and_blocks_answer_at_cap(self) -> None:
        conversation = ChatConversation.objects.create(session_key="summary-cap")
        append_chat_message(conversation, role="user", content="old question")
        append_chat_message(conversation, role="assistant", content="old answer")
        append_chat_message(conversation, role="user", content="recent question")
        provider = _SummaryAndAnswerProvider()
        consumer = self._consumer(provider, "summary-cap")

        async def run() -> None:
            with (
                patch(
                    "general_manager.chat.consumer.get_chat_settings",
                    return_value={
                        "max_total_rounds_per_message": 1,
                        "max_retries_per_message": 3,
                        "max_mutations_per_message": 1,
                        "rate_limit": {"requests": 100, "window_seconds": 30},
                    },
                ),
                patch.object(
                    consumer,
                    "_get_persistent_conversation",
                    new=AsyncMock(return_value=conversation),
                ),
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch.object(ChatConsumer, "_build_tool_definitions", return_value=[]),
                patch(
                    "general_manager.chat.consumer.get_planned_chat_settings",
                    return_value=SimpleNamespace(enabled=False),
                ),
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
                patch(
                    "general_manager.chat.context.enforce_chat_rate_limit",
                    return_value=None,
                ),
            ):
                await consumer.receive_json({"type": "message", "text": "latest"})

            assert send.await_args_list[-1].args[0] == {
                "type": "error",
                "message": "Chat turn limit exceeded.",
                "code": "turn_limit",
            }
            assert len(provider.calls) == 1

        asyncio.run(run())

    def test_explicit_round_limit_stops_recursive_provider_round(self) -> None:
        provider = _QueryThenAnswerProvider()
        consumer = self._consumer(provider, "explicit-round-cap")
        state = TurnState(max_rounds=1, max_mutations=1)

        async def run() -> None:
            with (
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch.object(ChatConsumer, "_build_tool_definitions", return_value=[]),
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={"rows": [{"name": "Bolt"}]},
                ) as execute_tool,
            ):
                await consumer._stream_provider_turn(
                    [Message(role="user", content="show parts")],
                    [],
                    tool_retries=0,
                    turn_state=state,
                )

            execute_tool.assert_called_once()
            assert len(provider.calls) == 1
            assert send.await_args_list[-1].args[0]["code"] == "turn_limit"

        asyncio.run(run())

    def test_exact_cache_token_total_blocks_tool_without_request_increment(
        self,
    ) -> None:
        provider = _QueryThenAnswerProvider()
        consumer = self._consumer(provider, "cache-equality")
        state = TurnState(max_rounds=3, max_mutations=1)

        async def run() -> None:
            with (
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch.object(ChatConsumer, "_build_tool_definitions", return_value=[]),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                    return_value={"rows": [{"name": "Bolt"}]},
                ) as execute_tool,
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {
                            "enabled": True,
                            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                            "rate_limit": {
                                "requests": 100,
                                "tokens": 1,
                                "window_seconds": 30,
                            },
                        }
                    }
                ),
            ):
                await consumer._stream_provider_turn(
                    [Message(role="user", content="show parts")],
                    [],
                    tool_retries=0,
                    turn_state=state,
                )

            execute_tool.assert_not_called()
            assert len(provider.calls) == 1
            assert state.usage == TokenUsage(input_tokens=1, output_tokens=0)
            assert (
                cache.get(
                    "general_manager:chat_rate_limit:session:cache-equality:requests"
                )
                is None
            )
            assert send.await_args_list[-1].args[0]["code"] == "rate_limited"

        try:
            asyncio.run(run())
        finally:
            cache.clear()

    def test_disabled_tools_account_usage_close_stream_and_skip_execution(self) -> None:
        provider = _DisabledToolsProvider()
        consumer = self._consumer(provider, "disabled-tools")
        state = TurnState(max_rounds=1, max_mutations=1)

        async def run() -> None:
            with (
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch.object(ChatConsumer, "_build_tool_definitions", return_value=[]),
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
                patch(
                    "general_manager.chat.consumer.execute_chat_tool",
                ) as execute_tool,
            ):
                await consumer._stream_provider_turn(
                    [Message(role="user", content="show parts")],
                    [],
                    tool_retries=0,
                    turn_state=state,
                    allow_tools=False,
                )

            execute_tool.assert_not_called()
            assert provider.calls == 1
            assert provider.closed is True
            assert state.usage == TokenUsage(input_tokens=4, output_tokens=5)
            assert send.await_args_list[-1].args[0] == {
                "type": "error",
                "message": "Chat tool retry limit exceeded.",
                "code": "tool_retry_limit",
            }

        asyncio.run(run())

    def test_http_disabled_tools_accounts_terminal_usage_before_rejection(self) -> None:
        provider = _DisabledToolsProvider()
        state = TurnState(max_rounds=1, max_mutations=1)

        async def run() -> None:
            with (
                patch("general_manager.chat.views.execute_chat_tool") as execute_tool,
                patch(
                    "general_manager.chat.views.enforce_chat_rate_limit",
                    return_value=None,
                ),
            ):
                events = await _run_provider_turn(
                    scope={},
                    conversation=None,
                    provider=provider,
                    messages=[Message(role="user", content="show parts")],
                    transport="http",
                    turn_state=state,
                    allow_tools=False,
                )
            execute_tool.assert_not_called()
            assert provider.closed
            assert state.usage == TokenUsage(4, 5)
            assert events[-1]["code"] == "tool_retry_limit"

        asyncio.run(run())

    def test_summary_rate_errors_preserve_retry_metadata_before_and_after_call(
        self,
    ) -> None:
        retry = {"retry_after_seconds": 30, "scope": "test"}
        for replies in ([retry], [None, retry]):
            with self.subTest(after_call=len(replies) == 2):
                provider = _SummaryAndAnswerProvider()

                async def run(
                    provider: _SummaryAndAnswerProvider,
                    replies: list[dict[str, object] | None],
                ) -> None:
                    with patch(
                        "general_manager.chat.context.enforce_chat_rate_limit",
                        side_effect=replies,
                    ):
                        with self.assertRaises(ChatSummaryRateLimitExceeded) as caught:
                            await summarize_messages_with_provider(
                                provider, [], scope={}
                            )
                    assert public_chat_error(caught.exception).as_event() == {
                        "type": "error",
                        "code": "rate_limited",
                        "message": "Chat rate limit exceeded. Try again later.",
                        "retry_after_seconds": 30,
                    }
                    assert len(provider.calls) == len(replies) - 1

                asyncio.run(run(provider, replies))

    def test_in_memory_confirmation_rate_denial_preserves_pending_state(self) -> None:
        consumer = self._consumer(object(), "memory-confirm")
        pending = {
            "id": "memory-pending",
            "mutation": "createPart",
            "input": {"name": "Bolt"},
            "messages": [Message(role="user", content="create a part")],
            "history": [],
            "expires_at": timezone.now(),
            "durable": False,
            "turn_state": TurnState(max_rounds=2, max_mutations=1),
        }
        consumer._pending_confirmation = pending
        scope = consumer.scope
        from general_manager.chat.rate_limits import enforce_chat_rate_limit

        with override_settings(
            GENERAL_MANAGER={
                "CHAT": {
                    "enabled": True,
                    "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                    "rate_limit": {
                        "requests": 100,
                        "tokens": 1,
                        "window_seconds": 30,
                    },
                }
            }
        ):
            assert (
                enforce_chat_rate_limit(scope, input_tokens=1, count_request=False)
                is None
            )

            async def run() -> None:
                with (
                    patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                    patch(
                        "general_manager.chat.consumer.execute_confirmed_chat_mutation"
                    ) as execute_mutation,
                ):
                    await consumer.receive_json(
                        {
                            "type": "confirm",
                            "confirmation_id": "memory-pending",
                            "confirmed": True,
                        }
                    )

                execute_mutation.assert_not_called()
                assert consumer._pending_confirmation is pending
                assert send.await_args_list[-1].args[0]["code"] == "rate_limited"

            asyncio.run(run())


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "enabled": True,
            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
            "url": "/chat/",
            "allowed_mutations": ["createPart"],
            "confirm_mutations": ["createPart"],
            "rate_limit": {"requests": 100, "window_seconds": 30},
        }
    }
)
class ChatTurnBudgetHttpTests(TransactionTestCase):
    def setUp(self) -> None:
        cache.clear()
        test_urls.urlpatterns[:] = []
        ensure_chat_http_routes()
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="turn-budget-user",
            email="turn-budget@example.com",
            password="pw",  # noqa: S106
        )
        self.client.force_login(self.user)

    def tearDown(self) -> None:
        test_urls.urlpatterns[:] = []
        cache.clear()

    def test_sse_pending_state_and_http_denial_leave_row_retriable(self) -> None:
        _ConfirmationProvider.instances = []
        with (
            override_settings(
                GENERAL_MANAGER={
                    "CHAT": {
                        "enabled": True,
                        "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                        "url": "/chat/",
                        "allowed_mutations": ["createPart"],
                        "confirm_mutations": ["createPart"],
                        "max_total_rounds_per_message": 2,
                        "max_mutations_per_message": 1,
                        "rate_limit": {
                            "requests": 100,
                            "tokens": 3,
                            "window_seconds": 30,
                        },
                    }
                }
            ),
            patch(
                "general_manager.chat.views.import_provider",
                return_value=_ConfirmationProvider,
            ),
            patch("general_manager.chat.views.get_tool_definitions", return_value=[]),
            patch(
                "general_manager.chat.views.execute_chat_tool",
                return_value={
                    "status": "confirmation_required",
                    "mutation": "createPart",
                    "input": {"name": "Bolt"},
                },
            ),
            patch(
                "general_manager.chat.views.execute_confirmed_chat_mutation"
            ) as execute_mutation,
        ):
            response = self.client.post(
                "/chat/stream/",
                data=json.dumps({"text": "create a part"}),
                content_type="application/json",
            )

            async def collect_stream() -> bytes:
                stream = response.streaming_content
                if hasattr(stream, "__aiter__"):
                    return b"".join([chunk async for chunk in stream])
                return b"".join(stream)

            body = asyncio.run(collect_stream()).decode()
            assert '"type": "confirm_mutation"' in body

            pending = ChatPendingConfirmation.objects.get(
                confirmation_id="pending-create", resolved_at__isnull=True
            )
            assert pending.payload["_gm_turn_state"] == _state_payload(
                rounds=1, mutations=1, input_tokens=1, output_tokens=1
            )
            from general_manager.chat.rate_limits import enforce_chat_rate_limit

            assert (
                enforce_chat_rate_limit(
                    {"user": self.user, "session": None},
                    input_tokens=1,
                    count_request=False,
                )
                is None
            )

            confirm = self.client.post(
                "/chat/confirm/",
                data=json.dumps(
                    {"confirmation_id": pending.confirmation_id, "confirmed": True}
                ),
                content_type="application/json",
            )

        assert confirm.json()["events"] == [
            {
                "type": "error",
                "message": "Chat rate limit exceeded. Try again later.",
                "code": "rate_limited",
                "retry_after_seconds": 30,
            }
        ]
        pending.refresh_from_db()
        assert pending.resolved_at is None
        execute_mutation.assert_not_called()
        assert _ConfirmationProvider.instances[0].calls == 1

    def test_http_confirmation_restores_read_limit(self) -> None:
        conversation = ChatConversation.objects.create(user=self.user)
        pending = create_pending_confirmation(
            conversation,
            confirmation_id="http-read-limit",
            mutation_name="createPart",
            payload={
                "input": {"name": "Bolt"},
                "_gm_turn_state": _state_payload(rounds=1, mutations=1, tool_retries=1),
            },
            timeout_seconds=30,
        )
        provider = _ReconnectToolProvider(mutation=False)
        with (
            override_settings(
                GENERAL_MANAGER={
                    "CHAT": {
                        "max_retries_per_message": 1,
                        "max_total_rounds_per_message": 3,
                    }
                }
            ),
            patch(
                "general_manager.chat.views.import_provider",
                return_value=lambda: provider,
            ),
            patch(
                "general_manager.chat.views.execute_confirmed_chat_mutation",
                return_value={"status": "executed"},
            ) as execute_mutation,
            patch("general_manager.chat.views.execute_chat_tool") as execute_tool,
            patch("general_manager.chat.views.get_tool_definitions", return_value=[]),
        ):
            response = self.client.post(
                "/chat/confirm/",
                data=json.dumps(
                    {"confirmation_id": pending.confirmation_id, "confirmed": True}
                ),
                content_type="application/json",
            )
        execute_mutation.assert_called_once()
        execute_tool.assert_not_called()
        assert provider.calls == 1
        assert response.json()["events"][-1]["code"] == "tool_retry_limit"


class ChatTurnBudgetReconnectTests(TransactionTestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="reconnect-budget-user",
            email="reconnect-budget@example.com",
            password="pw",  # noqa: S106
        )

    def tearDown(self) -> None:
        cache.clear()

    def _pending(self, *, payload: dict[str, object]) -> ChatConversation:
        conversation = ChatConversation.objects.create(user=self.user)
        append_chat_message(conversation, role="user", content="create a part")
        append_chat_message(
            conversation,
            role="assistant",
            tool_calls=[
                {
                    "id": "reconnect-pending",
                    "name": "mutate",
                    "args": {
                        "mutation": "createPart",
                        "input": {"name": "Bolt"},
                    },
                }
            ],
        )
        create_pending_confirmation(
            conversation,
            confirmation_id="reconnect-pending",
            mutation_name="createPart",
            payload=payload,
            timeout_seconds=30,
        )
        return conversation

    def _consumer(
        self, conversation: ChatConversation, provider: object
    ) -> ChatConsumer:
        consumer = ChatConsumer()
        consumer.scope = _scope(self.user, "reconnect-session")
        consumer.session_key = "reconnect-session"
        consumer.conversation = conversation
        consumer.provider = provider
        consumer._pending_confirmation = None
        consumer._history_cache = []
        consumer.channel_name = "chat.reconnect-budget"
        return consumer

    def test_reconnect_rate_denial_does_not_execute_or_consume_pending_row(
        self,
    ) -> None:
        conversation = self._pending(
            payload={
                "input": {"name": "Bolt"},
                "_gm_turn_state": _state_payload(rounds=1, mutations=1, input_tokens=1),
            }
        )
        provider = _ReconnectToolProvider(mutation=False)
        consumer = self._consumer(conversation, provider)
        scope = consumer.scope
        from general_manager.chat.rate_limits import enforce_chat_rate_limit

        with override_settings(
            GENERAL_MANAGER={
                "CHAT": {
                    "enabled": True,
                    "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                    "rate_limit": {
                        "requests": 100,
                        "tokens": 1,
                        "window_seconds": 30,
                    },
                }
            }
        ):
            assert (
                enforce_chat_rate_limit(scope, input_tokens=1, count_request=False)
                is None
            )

            async def run() -> None:
                with (
                    patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                    patch(
                        "general_manager.chat.consumer.execute_confirmed_chat_mutation"
                    ) as execute_mutation,
                ):
                    await consumer.receive_json(
                        {
                            "type": "confirm",
                            "confirmation_id": "reconnect-pending",
                            "confirmed": True,
                        }
                    )

                execute_mutation.assert_not_called()
                assert provider.calls == 0
                assert send.await_args_list[-1].args[0]["code"] == "rate_limited"

            asyncio.run(run())

        assert ChatPendingConfirmation.objects.get().resolved_at is None

    def test_reconnect_round_cap_blocks_followup_read(self) -> None:
        conversation = self._pending(
            payload={
                "input": {"name": "Bolt"},
                "_gm_turn_state": _state_payload(rounds=1, mutations=1),
            }
        )
        provider = _ReconnectToolProvider(mutation=False)
        consumer = self._consumer(conversation, provider)

        async def run() -> None:
            with (
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {
                            "enabled": True,
                            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                            "max_total_rounds_per_message": 1,
                            "max_mutations_per_message": 1,
                            "rate_limit": {"requests": 100, "window_seconds": 30},
                        }
                    }
                ),
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch(
                    "general_manager.chat.consumer.execute_confirmed_chat_mutation",
                    return_value={"status": "executed"},
                ) as execute_mutation,
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
            ):
                await consumer.receive_json(
                    {
                        "type": "confirm",
                        "confirmation_id": "reconnect-pending",
                        "confirmed": True,
                    }
                )

            execute_mutation.assert_called_once()
            assert provider.calls == 0
            assert send.await_args_list[-1].args[0]["code"] == "turn_limit"

        asyncio.run(run())

    def test_reconnect_restores_read_limit_with_provider_round_available(self) -> None:
        conversation = self._pending(
            payload={
                "input": {"name": "Bolt"},
                "_gm_turn_state": _state_payload(rounds=1, mutations=1, tool_retries=1),
            }
        )
        provider = _ReconnectToolProvider(mutation=False)
        consumer = self._consumer(conversation, provider)

        async def run() -> None:
            with (
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {
                            "enabled": True,
                            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                            "max_total_rounds_per_message": 3,
                            "max_retries_per_message": 1,
                            "max_mutations_per_message": 1,
                            "rate_limit": {"requests": 100, "window_seconds": 30},
                        }
                    }
                ),
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch(
                    "general_manager.chat.consumer.execute_confirmed_chat_mutation",
                    return_value={"status": "executed"},
                ) as execute_mutation,
                patch(
                    "general_manager.chat.consumer.execute_chat_tool"
                ) as execute_tool,
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
            ):
                await consumer.receive_json(
                    {
                        "type": "confirm",
                        "confirmation_id": "reconnect-pending",
                        "confirmed": True,
                    }
                )

            execute_mutation.assert_called_once()
            execute_tool.assert_not_called()
            assert provider.calls == 1
            assert send.await_args_list[-1].args[0]["code"] == "tool_retry_limit"

        asyncio.run(run())

    def test_reconnect_mutation_limit_blocks_second_write(self) -> None:
        conversation = self._pending(
            payload={
                "input": {"name": "Bolt"},
                "_gm_turn_state": _state_payload(rounds=1, mutations=1),
            }
        )
        provider = _ReconnectToolProvider(mutation=True)
        consumer = self._consumer(conversation, provider)

        async def run() -> None:
            with (
                override_settings(
                    GENERAL_MANAGER={
                        "CHAT": {
                            "enabled": True,
                            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                            "max_total_rounds_per_message": 2,
                            "max_mutations_per_message": 1,
                            "rate_limit": {"requests": 100, "window_seconds": 30},
                        }
                    }
                ),
                patch.object(consumer, "send_json", new_callable=AsyncMock) as send,
                patch(
                    "general_manager.chat.consumer.execute_confirmed_chat_mutation",
                    return_value={"status": "executed"},
                ) as execute_mutation,
                patch(
                    "general_manager.chat.consumer.execute_chat_tool"
                ) as execute_tool,
                patch(
                    "general_manager.chat.consumer.enforce_chat_rate_limit",
                    return_value=None,
                ),
            ):
                await consumer.receive_json(
                    {
                        "type": "confirm",
                        "confirmation_id": "reconnect-pending",
                        "confirmed": True,
                    }
                )

            execute_mutation.assert_called_once()
            execute_tool.assert_not_called()
            assert provider.calls == 1
            assert send.await_args_list[-1].args[0] == {
                "type": "error",
                "message": "Chat mutation limit exceeded.",
                "code": "mutation_limit",
            }

        asyncio.run(run())
