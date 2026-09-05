"""Bounded accounting shared by every provider round in one chat turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from general_manager.chat.providers.base import TokenUsage


@dataclass
class TurnState:
    """Track provider, mutation, and token budgets across resumes."""

    max_rounds: int
    max_mutations: int
    rounds: int = 0
    mutations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_retries: int = 0

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> TurnState:
        retries = max(0, int(settings.get("max_retries_per_message", 3)))
        configured_mutations = settings.get("max_mutations_per_message", 8)
        mutations = max(
            0, int(8 if configured_mutations is None else configured_mutations)
        )
        configured_rounds = settings.get("max_total_rounds_per_message")
        default_rounds = retries + mutations + 2
        return cls(
            max_rounds=max(
                1,
                default_rounds if configured_rounds is None else int(configured_rounds),
            ),
            max_mutations=mutations,
        )

    @classmethod
    def from_payload(
        cls, settings: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> TurnState:
        state = cls.from_settings(settings)
        saved = payload.get("_gm_turn_state")
        if not isinstance(saved, Mapping):
            return state
        state.rounds = min(state.max_rounds, max(0, int(saved.get("rounds", 0))))
        state.mutations = min(
            state.max_mutations, max(0, int(saved.get("mutations", 0)))
        )
        state.input_tokens = max(0, int(saved.get("input_tokens", 0)))
        state.output_tokens = max(0, int(saved.get("output_tokens", 0)))
        state.tool_retries = max(0, int(saved.get("tool_retries", 0)))
        return state

    def reserve_round(self) -> bool:
        if self.rounds >= self.max_rounds:
            return False
        self.rounds += 1
        return True

    def reserve_mutation(self) -> bool:
        if self.mutations >= self.max_mutations:
            return False
        self.mutations += 1
        return True

    def record_usage(self, usage: TokenUsage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens

    @property
    def usage(self) -> TokenUsage:
        return TokenUsage(self.input_tokens, self.output_tokens)

    def as_payload(self) -> dict[str, int]:
        return {
            "rounds": self.rounds,
            "mutations": self.mutations,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_retries": self.tool_retries,
        }
