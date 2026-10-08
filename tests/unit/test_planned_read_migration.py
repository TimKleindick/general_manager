"""New messages use Planned; only mutation continuations use the old loop."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from django.test import override_settings
from graphql import build_schema

from general_manager.chat.planned.config import get_planned_chat_settings
from general_manager.chat.providers.base import Message
from general_manager.chat.settings import validate_chat_settings


def test_planned_is_the_default_with_one_executor():
    with override_settings(GENERAL_MANAGER={"CHAT": {}}):
        settings = get_planned_chat_settings()
    assert settings.enabled is True
    assert {"executor"} <= set(settings.roles)


def test_false_switch_is_deprecated_without_reenabling_legacy_reads():
    with override_settings(GENERAL_MANAGER={"CHAT": {"planned": {"enabled": False}}}):
        with pytest.warns(DeprecationWarning, match="planned.enabled"):
            settings = get_planned_chat_settings()
    assert settings.enabled is True


def test_explicit_read_profiles_do_not_require_unused_legacy_provider():
    provider = "tests.unit.test_chat_planned_config.CheckedConfiguredProvider"
    roles = {
        role: "read"
        for role in [
            "planner",
            "executor",
            "synthesizer",
            "fallback",
        ]
    }
    config = {
        "CHAT": {
            "provider": "unavailable.legacy.Provider",
            "provider_profiles": {
                "read": {
                    "provider": provider,
                    "provider_config": {},
                    "trust_group": "local",
                }
            },
            "planned": {"roles": roles},
        }
    }
    with (
        override_settings(GENERAL_MANAGER=config),
        patch(
            "general_manager.chat.settings.GraphQL.get_schema",
            return_value=SimpleNamespace(
                graphql_schema=build_schema("type Query { ping: String }")
            ),
        ),
        patch("general_manager.chat.schema_index.build_schema_index", return_value={}),
    ):
        validate_chat_settings()


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_false_setting_cannot_dispatch_a_read_to_legacy_loop(transport):
    from general_manager.chat import views

    messages = [Message("user", "read records")]
    prepared = views._PreparedMessageRequest(
        conversation=object(),
        scope={},
        provider=object(),
        messages=messages,
        early_events=None,
        user_text="read records",
        planned_settings=SimpleNamespace(enabled=False),
    )

    async def read(*args, **kwargs):
        yield {"type": "text_chunk", "content": "Planned answer"}

    async def collect():
        return [
            event
            async for event in views._iter_prepared_message_events(
                prepared, transport=transport
            )
        ]

    with (
        patch.object(
            views,
            "prepare_planned_turn",
            new=AsyncMock(return_value=SimpleNamespace(mutation_plan=None)),
        ) as planner,
        patch.object(views, "_planned_catalog_summary", return_value={}),
        patch.object(views, "iter_planned_read_events", new=read),
        patch.object(
            views,
            "_iter_provider_turn_events",
            side_effect=AssertionError("legacy read entered"),
        ),
    ):
        assert asyncio.run(collect()) == [
            {"type": "text_chunk", "content": "Planned answer"}
        ]
        planner.assert_awaited_once()


def test_http_admission_does_not_construct_legacy_provider():
    from general_manager.chat import views

    request = SimpleNamespace(user=None)
    importer = Mock(side_effect=AssertionError("unused provider construction"))
    with (
        patch.object(views, "_parse_json_body", return_value={"text": "read records"}),
        patch.object(views, "_ensure_session_key", return_value="session"),
        patch.object(views, "_request_scope", return_value={}),
        patch.object(views, "enforce_chat_rate_limit", return_value=None),
        patch.object(views, "_conversation_for_request", return_value=object()),
        patch.object(views, "append_chat_message"),
        patch.object(
            views,
            "_build_messages",
            new=AsyncMock(return_value=[Message("user", "read records")]),
        ) as context,
        patch.object(views, "emit_chat_message_received"),
    ):
        prepared = asyncio.run(
            views._prepare_message_request(request, provider_importer=importer)
        )
    assert prepared.provider is None
    assert context.await_args.kwargs["allow_summarization"] is False
    importer.assert_not_called()


def test_websocket_false_setting_still_starts_planned_task():
    from general_manager.chat.consumer import ChatConsumer

    consumer = ChatConsumer()

    async def run():
        with (
            patch(
                "general_manager.chat.consumer.get_planned_chat_settings",
                return_value=SimpleNamespace(enabled=False),
            ),
            patch.object(consumer, "_stream_planned_turn", new=AsyncMock()) as planned,
            patch.object(
                consumer,
                "_stream_provider_turn",
                new=AsyncMock(side_effect=AssertionError("legacy read")),
            ),
        ):
            assert (
                await consumer._stream_message_turn(
                    "read", [Message("user", "read")], []
                )
                is True
            )
            await consumer._provider_task
            planned.assert_awaited_once()

    asyncio.run(run())


def test_websocket_mutation_dispatch_keeps_admitted_turn_state():
    from general_manager.chat.consumer import ChatConsumer
    from general_manager.chat.turns import TurnState
    from general_manager.chat.settings import get_chat_settings

    consumer = ChatConsumer()
    consumer.conversation = None
    consumer.scope = {}
    consumer._active_turn = None
    turn = TurnState.from_settings(get_chat_settings())

    async def run():
        with (
            patch(
                "general_manager.chat.consumer.prepare_planned_turn",
                new=AsyncMock(return_value=SimpleNamespace(mutation_plan=object())),
            ),
            patch.object(consumer, "_planned_catalog_summary", return_value={}),
            patch.object(
                consumer, "_stream_provider_turn", new=AsyncMock()
            ) as mutation,
        ):
            await consumer._stream_planned_turn(
                "write", [], [], get_planned_chat_settings(), turn_state=turn
            )
            assert mutation.await_args.kwargs["turn_state"] is turn

    asyncio.run(run())


def test_single_executor_configuration_has_three_stages_and_shared_recovery():
    with override_settings(GENERAL_MANAGER={"CHAT": {}}):
        settings = get_planned_chat_settings()
    assert set(settings.roles) == {"planner", "executor", "synthesizer", "fallback"}


def test_obsolete_executor_role_configuration_is_rejected():
    from general_manager.chat.settings import ChatConfigurationError

    roles = {
        role: "default"
        for role in [
            "planner",
            "simple_executor",
            "complex_executor",
            "synthesizer",
            "fallback_executor",
        ]
    }
    with override_settings(GENERAL_MANAGER={"CHAT": {"planned": {"roles": roles}}}):
        with pytest.raises(ChatConfigurationError):
            get_planned_chat_settings()


@pytest.mark.parametrize(
    "dependency, calculation, prior_failure, path_depth",
    [
        (False, False, False, None),
        (True, False, False, 1),
        (False, True, False, 8),
        (False, False, True, 2),
    ],
)
def test_task_complexity_does_not_select_a_different_executor(
    monkeypatch, dependency, calculation, prior_failure, path_depth
):
    from dataclasses import replace
    from general_manager.chat.planned.models import EvidenceRequirement
    from tests.unit.test_chat_planned_tool_feedback import _runner
    from tests.unit.test_chat_planned_scheduler import _task

    task = _task("t1")
    if dependency:
        task = replace(task, depends_on=("predecessor",))
    if calculation:
        task = replace(
            task,
            requirements=(
                EvidenceRequirement("calc", "calculation", "sum observed rows", "sum"),
            ),
        )
    runner = _runner(lambda *_: {}, tasks=[task])
    runtime = runner.runtimes["t1"]
    runtime.prior_failure = prior_failure
    runtime.path_depth = path_depth
    seen = []

    async def execute(runtime, candidates):
        seen.append(runtime.role)
        runtime.status = "resolved"
        return None

    monkeypatch.setattr(runner, "_execute_one_pass", execute)
    asyncio.run(runner.run_task(runtime))
    assert seen == ["executor"]


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_failed_planning_never_falls_back_to_write_provider(transport):
    from general_manager.chat import views

    prepared = views._PreparedMessageRequest(
        conversation=object(),
        scope={},
        provider=None,
        messages=[Message("user", "read")],
        early_events=None,
        user_text="read",
        planned_settings=SimpleNamespace(enabled=True),
    )

    async def collect():
        return [
            event
            async for event in views._iter_prepared_message_events(
                prepared, transport=transport
            )
        ]

    with (
        patch.object(
            views,
            "prepare_planned_turn",
            new=AsyncMock(side_effect=RuntimeError("provider failure")),
        ),
        patch.object(views, "_planned_catalog_summary", return_value={}),
        patch.object(
            views, "import_provider", side_effect=AssertionError("legacy fallback")
        ) as provider,
    ):
        with pytest.raises(RuntimeError, match="provider failure"):
            asyncio.run(collect())
    provider.assert_not_called()


def test_websocket_write_provider_is_lazily_constructed_once_after_reconnect():
    from general_manager.chat.consumer import ChatConsumer

    consumer = ChatConsumer()
    consumer.provider = None
    instance = object()
    constructor = Mock(return_value=instance)
    with patch(
        "general_manager.chat.consumer.import_provider", return_value=constructor
    ) as importer:
        assert consumer._get_mutation_provider() is instance
        assert consumer._get_mutation_provider() is instance
    importer.assert_called_once_with()
    constructor.assert_called_once_with()
