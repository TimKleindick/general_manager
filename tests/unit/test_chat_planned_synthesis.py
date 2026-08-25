"""Contract tests for grounded planned-chat synthesis."""

from __future__ import annotations

import json
import asyncio
from types import MappingProxyType
from typing import ClassVar

import pytest

from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.synthesis import (
    SynthesisFailedError,
    synthesize_answer,
)
from general_manager.chat.providers.base import DoneEvent, TextChunkEvent, TokenUsage


class _SynthesisProvider:
    responses: ClassVar[list[str]] = []
    calls: ClassVar[list[list[object]]] = []

    @classmethod
    def from_config(cls, _config: object) -> _SynthesisProvider:
        return cls()

    async def complete(self, _messages: list[object], _tools: list[object]):
        type(self).calls.append(_messages)
        yield TextChunkEvent(type(self).responses.pop(0))
        yield DoneEvent(TokenUsage(input_tokens=1, output_tokens=2))


def _settings() -> PlannedChatSettings:
    profile = ProviderProfile(
        "synthesizer",
        "tests.unit.test_chat_planned_synthesis._SynthesisProvider",
        MappingProxyType({"model": "test"}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"synthesizer": profile}),
        roles=MappingProxyType(
            {"synthesizer": "synthesizer", "fallback_executor": "synthesizer"}
        ),
        catalog_source=None,
    )


def _store() -> EvidenceStore:
    store = EvidenceStore()
    store.add(
        EvidenceRecord.create(
            "ev-query-1", "task_1", "query", "query", {"tool": "query"}, {"rows": []}
        )
    )
    return store


def test_partial_synthesis_accepts_only_resolved_evidence_ids() -> None:
    _SynthesisProvider.responses = [
        json.dumps({"answer": "No parts found.", "evidence_ids": ["ev-query-1"]})
    ]

    result = asyncio.run(
        synthesize_answer(
            "show parts",
            _store(),
            {"resolved": 1, "total": 2, "unresolved": []},
            _settings(),
            RoundBudget(()),
        )
    )

    assert result.evidence_ids == ("ev-query-1",)
    assert result.usage == TokenUsage(input_tokens=1, output_tokens=2)


def test_synthesis_rejects_unknown_evidence_reference() -> None:
    _SynthesisProvider.responses = [
        json.dumps({"answer": "No parts found.", "evidence_ids": ["missing"]})
    ] * 2

    with pytest.raises(SynthesisFailedError, match="synthesis_failed"):
        asyncio.run(
            synthesize_answer("show parts", _store(), {}, _settings(), RoundBudget(()))
        )


def test_synthesis_falls_back_and_sanitizes_unresolved_diagnostics() -> None:
    _SynthesisProvider.calls.clear()
    _SynthesisProvider.responses = [
        json.dumps({"answer": "Bad", "evidence_ids": ["unknown"]}),
        json.dumps({"answer": "No parts found.", "evidence_ids": ["ev-query-1"]}),
    ]
    budget = RoundBudget(())

    result = asyncio.run(
        synthesize_answer(
            "show parts",
            _store(),
            {
                "resolved": 1,
                "total": 2,
                "unresolved": [
                    {
                        "task_id": "task_2",
                        "reason": "deadline_exceeded",
                        "raw": "secret",
                    },
                    {"task_id": "task_3", "reason": "untrusted", "raw": "also secret"},
                ],
            },
            _settings(),
            budget,
        )
    )

    assert result.usage == TokenUsage(input_tokens=2, output_tokens=4)
    assert budget.global_used == 2
    reference = json.loads(
        _SynthesisProvider.calls[0][1].content.removeprefix("RESOLVED_REFERENCE_DATA=")
    )
    assert reference["coverage"]["unresolved"] == [
        {"task_id": "task_2", "reason": "deadline_exceeded"}
    ]
