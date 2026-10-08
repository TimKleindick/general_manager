"""Offline scripted providers decode bound references without changing wire data."""

import asyncio
import json

from experiments.gm_eval.scripted import make_script
from general_manager.chat.planned.schema_projection import (
    REFERENCE_VERSION,
    compact_messages,
    reference_message,
)
from general_manager.chat.providers.base import TextChunkEvent


def test_scripted_probe_reads_complete_bound_reference_with_no_schema_slots():
    evidence = [
        {
            "evidence_id": f"query-{i}",
            "payload": {"data": [{"code": "C01", "name": "Customer" * 40}]},
            "requirement_ids": [f"q{i}"],
        }
        for i in range(8)
    ]
    original = reference_message(
        {
            "task": {"task_id": "probe"},
            "original_request": "Read Customer",
            "task_evidence": evidence,
        },
        (),
        reference_scope="executor",
    )
    messages = compact_messages([original])
    wire = messages[0].content
    assert messages[0].projection_receipt["format"] == REFERENCE_VERSION
    provider = make_script({"turns": ["Read Customer"]}, None)({"role": "executor"})

    async def run():
        return [event async for event in provider.complete(messages, [])]

    events = asyncio.run(run())
    assert isinstance(events[0], TextChunkEvent)
    assert json.loads(events[0].content) == {
        "action": "complete",
        "evidence_ids": [item["evidence_id"] for item in evidence],
    }
    assert messages[0].content == wire
    assert messages[0].logical_content == original.content
