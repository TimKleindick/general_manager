from __future__ import annotations

import pytest

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
