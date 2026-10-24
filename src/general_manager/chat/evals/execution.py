"""Provider binding and request accounting for the production eval adapter.

This module does not execute tools, repair answers, validate completion, or
retry requests. Those responsibilities stay with the production scheduler.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from general_manager.chat.planned.config import (
    PlannedChatSettings,
    ProviderProfile,
    build_profile_provider,
)
from general_manager.chat.providers._shared import closing_async_resource
from general_manager.chat.providers.base import (
    ChatEvent,
    Message,
    TokenUsage,
    ToolDefinition,
)


class EvalRequestBudgetExceeded(RuntimeError):
    """An explicit per-turn ceiling stopped dispatch before a provider call."""


@dataclass
class EvalExecution:
    """One case binding, inherited by the scheduler's asyncio tasks."""

    provider: Any
    profiles: Mapping[str, ProviderProfile]
    max_requests: int
    turn_requests: int = 0
    budget_exhausted: bool = False
    provider_error: Exception | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    usage_observations: int = 0

    def begin_turn(self) -> None:
        self.turn_requests = 0
        self.budget_exhausted = False
        self.provider_error = None

    def accounting_observer(
        self, delegate: Callable[..., Any] | None
    ) -> Callable[..., Any]:
        """Observe the scheduler's existing once-only usage accounting callback."""

        def observe(scope: dict[str, Any], **kwargs: Any) -> Any:
            if kwargs.get("count_request") is False and (
                "input_tokens" in kwargs or "output_tokens" in kwargs
            ):
                self.usage_observations += 1
                self.usage = TokenUsage(
                    self.usage.input_tokens + int(kwargs.get("input_tokens", 0)),
                    self.usage.output_tokens + int(kwargs.get("output_tokens", 0)),
                )
            return delegate(scope, **kwargs) if delegate is not None else None

        return observe

    def admit(self, role: str) -> Any:
        # No await separates the check and increment, including concurrent roles.
        if self.turn_requests >= self.max_requests:
            self.budget_exhausted = True
            raise EvalRequestBudgetExceeded("turn_request_budget_exhausted")
        self.turn_requests += 1
        return (
            self.provider
            if self.provider is not None
            else build_profile_provider(self.profiles[role])
        )


_EXECUTION: ContextVar[EvalExecution | None] = ContextVar(
    "chat_eval_execution", default=None
)


class EvaluationProvider:
    """Importable profile bridge; preserves no-argument custom CLI providers."""

    def __init__(self, role: str) -> None:
        self.role = role

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> EvaluationProvider:
        return cls(str(config["role"]))

    @property
    def provider_config(self) -> Mapping[str, Any]:
        return MappingProxyType({"role": self.role})

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        execution = _EXECUTION.get()
        if execution is None:
            raise RuntimeError("explicit_eval_execution_binding_required")
        try:
            provider = execution.admit(self.role)
            async with closing_async_resource(
                provider.complete(messages, tools)
            ) as events:
                async for event in events:
                    yield event
        except EvalRequestBudgetExceeded:
            raise
        except Exception as error:
            if execution.provider_error is None:
                execution.provider_error = error
            raise


@contextmanager
def bind_execution(
    provider: Any,
    settings: PlannedChatSettings,
    max_requests: int,
) -> Iterator[tuple[EvalExecution, PlannedChatSettings]]:
    """Route every role through one accounting bridge and unchanged production IO."""
    profiles = {
        role: settings.profiles[profile] for role, profile in settings.roles.items()
    }
    execution = EvalExecution(provider, MappingProxyType(profiles), max_requests)
    bound_profiles = {
        role: ProviderProfile(
            name=role,
            provider_path="general_manager.chat.evals.execution.EvaluationProvider",
            provider_config=MappingProxyType({"role": role}),
            trust_group=profile.trust_group,
            configured=True,
        )
        for role, profile in profiles.items()
    }
    token = _EXECUTION.set(execution)
    try:
        yield (
            execution,
            replace(
                settings,
                profiles=MappingProxyType(bound_profiles),
                roles=MappingProxyType({role: role for role in profiles}),
                # A supplied provider instance can carry mutable transport state.
                max_concurrent_tasks=1,
            ),
        )
    finally:
        _EXECUTION.reset(token)
