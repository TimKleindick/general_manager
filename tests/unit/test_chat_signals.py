from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
import pytest

from general_manager.chat import signals
from general_manager.chat.signals import (
    chat_error,
    chat_message_received,
    chat_mutation_executed,
    chat_tool_called,
    emit_chat_error,
    emit_chat_message_received,
    emit_chat_mutation_executed,
    emit_chat_tool_called,
)


@pytest.mark.parametrize(
    ("signal", "emitter", "payload"),
    [
        (chat_message_received, emit_chat_message_received, {"message": "hello"}),
        (chat_mutation_executed, emit_chat_mutation_executed, {"mutation": "create"}),
        (chat_tool_called, emit_chat_tool_called, {"tool_name": "query"}),
        (chat_error, emit_chat_error, {"error": "failed"}),
    ],
)
def test_chat_signal_emitters_use_robust_dispatch(signal, emitter, payload) -> None:
    calls: list[str] = []

    def receiver(sender, **kwargs) -> None:  # type: ignore[no-untyped-def]
        calls.append(sender)
        for key, value in payload.items():
            assert kwargs[key] == value
        raise RuntimeError

    dispatch_uid = f"test-{emitter.__name__}"
    signal.connect(receiver, weak=False, dispatch_uid=dispatch_uid)
    try:
        emitter(**payload)
    finally:
        signal.disconnect(receiver, dispatch_uid=dispatch_uid)

    assert calls == ["general_manager.chat"]


def test_mutation_result_emits_both_existing_signals_once_in_order():
    emitter = getattr(signals, "emit_chat_mutation_result", None)
    assert callable(emitter), "Mutation results need one shared signal owner."
    user = object()
    mutation_input = {"name": "Bolt"}
    result = {"status": "executed", "data": {"id": "1"}}
    received = []

    def receive(sender, signal, **payload):
        received.append((signal, sender, payload))

    chat_tool_called.connect(receive, weak=False)
    chat_mutation_executed.connect(receive, weak=False)
    try:
        emitter(user=user, mutation="createPart", input=mutation_input, result=result)
    finally:
        chat_tool_called.disconnect(receive)
        chat_mutation_executed.disconnect(receive)

    assert received == [
        (
            chat_tool_called,
            "general_manager.chat",
            {
                "user": user,
                "tool_name": "mutate",
                "args": {"mutation": "createPart", "input": mutation_input},
                "result": result,
            },
        ),
        (
            chat_mutation_executed,
            "general_manager.chat",
            {
                "user": user,
                "mutation": "createPart",
                "input": mutation_input,
                "result": result,
            },
        ),
    ]
    assert all(payload["result"] is result for _, _, payload in received)


@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("confirmed", [True, False])
def test_confirmation_transports_preserve_signal_order_and_reject_replay(
    transport, confirmed, monkeypatch
):
    from general_manager.chat import consumer as consumer_module, views
    from general_manager.chat.consumer import ChatConsumer
    from general_manager.chat.models import ChatPendingConfirmation

    user = AnonymousUser()
    conversation = object()
    mutation_input = {"name": "Bolt"}
    result = (
        {"status": "executed", "data": {"id": "1"}}
        if confirmed
        else {"status": "cancelled", "reason": "user_rejected"}
    )
    payload = {
        "type": "confirm",
        "confirmation_id": "confirm-signals",
        "confirmed": confirmed,
    }
    pending = SimpleNamespace(
        confirmation_id="confirm-signals",
        mutation_name="createPart",
        payload={"input": mutation_input},
    )
    order = []
    received = []
    records = []
    deliveries = []
    claimed = False
    consumer = ChatConsumer()

    def claim(**_kwargs):
        nonlocal claimed
        order.append("claim")
        if claimed:
            return None
        claimed = True
        return pending

    def mutate(**kwargs):
        assert kwargs["context"].user is user
        if transport == "websocket":
            assert consumer._pending_confirmation is None
        order.append("execute")
        return result

    def receive(sender, signal, **signal_payload):
        assert sender == "general_manager.chat"
        order.append("tool_signal" if signal is chat_tool_called else "mutation_signal")
        received.append(signal_payload)

    def record(*_args, **kwargs):
        order.append("persist")
        records.append(kwargs)

    async def record_async(**kwargs):
        record(**kwargs)

    async def build_messages(*_args, **_kwargs):
        order.append("context")
        return []

    async def provider_turn(*_args, **_kwargs):
        order.append("provider")
        return [{"type": "done"}]

    async def send(event):
        order.append("send")
        deliveries.append(event)

    monkeypatch.setattr(ChatPendingConfirmation, "claim_for_conversation", claim)
    module = views if transport == "http" else consumer_module
    monkeypatch.setattr(module, "execute_confirmed_chat_mutation", mutate)
    monkeypatch.setattr(module, "enforce_chat_rate_limit", lambda *_args, **_kw: None)
    if transport == "http":
        request = RequestFactory().post(
            "/chat/confirm/", data=json.dumps(payload), content_type="application/json"
        )
        request.user = user
        monkeypatch.setattr(
            views, "_conversation_for_request", lambda _req: conversation
        )
        monkeypatch.setattr(views, "append_chat_message", record)
        monkeypatch.setattr(views, "import_provider", lambda: object)
        monkeypatch.setattr(views, "_build_messages", build_messages)
        monkeypatch.setattr(views, "_run_provider_turn", provider_turn)

        async def dispatch():
            returned, events = await views._execute_confirmation_request(request)
            assert returned is conversation
            order.append("response")
            deliveries.extend(events)

        expected = ["claim", *(["execute"] if confirmed else [])]
        expected += ["tool_signal", "mutation_signal", "persist", "context", "provider"]
        expected += ["response"]
        replay_order = ["claim", "response"]
    else:
        consumer.scope = {"user": user}
        consumer.session_key = "signal-session"
        consumer.conversation = conversation
        consumer._pending_confirmation = {
            "id": "confirm-signals",
            "mutation": "createPart",
            "input": mutation_input,
            "messages": [],
            "history": [],
            "durable": True,
        }
        cancel_timeout = consumer._cancel_confirmation_timeout

        async def cancel():
            order.append("cancel_timeout")
            await cancel_timeout()

        monkeypatch.setattr(module, "get_chat_permission", lambda: None)
        monkeypatch.setattr(
            module, "emit_chat_audit_event", lambda *_args: order.append("audit")
        )
        monkeypatch.setattr(consumer, "_cancel_confirmation_timeout", cancel)
        monkeypatch.setattr(consumer, "_record_message", record_async)
        monkeypatch.setattr(consumer, "_stream_provider_turn", provider_turn)
        monkeypatch.setattr(consumer, "send_json", send)

        async def dispatch():
            await consumer.receive_json(payload)

        expected = ["claim", "cancel_timeout", *(["execute"] if confirmed else [])]
        expected += [
            "tool_signal",
            "mutation_signal",
            "audit",
            "send",
            "persist",
            "provider",
        ]
        replay_order = ["claim", "send"]

    async def run():
        await dispatch()
        assert order == expected
        await dispatch()
        assert order == expected + replay_order

    chat_tool_called.connect(receive, weak=False)
    chat_mutation_executed.connect(receive, weak=False)
    try:
        asyncio.run(run())
    finally:
        chat_tool_called.disconnect(receive)
        chat_mutation_executed.disconnect(receive)

    assert len(received) == 2
    assert all(item["user"] is user and item["result"] == result for item in received)
    assert received[0]["args"] == {"mutation": "createPart", "input": mutation_input}
    assert received[1]["mutation"] == "createPart"
    assert received[1]["input"] is mutation_input
    assert len(records) == 1
    assert records[0]["content"] == json.dumps(result, sort_keys=True)
    assert records[0]["tool_args"] == {
        "mutation": "createPart",
        "input": mutation_input,
    }
    assert records[0]["tool_result"] == result
    assert records[0]["tool_call_id"] == "confirm-signals"
    assert deliveries[0] == {
        "type": "tool_result",
        "id": "confirm-signals",
        "name": "mutate",
        "result": result,
    }
    assert deliveries[-1] == {
        "type": "error",
        "message": "Unknown chat event.",
        "code": "bad_event",
    }
