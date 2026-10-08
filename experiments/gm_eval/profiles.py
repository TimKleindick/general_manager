"""Importable production provider profiles with explicit experiment injection."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_manager.chat.providers.base import (
        BaseLLMProvider,
        ChatEvent,
        Message,
        ToolDefinition,
    )

from .trace import TraceRecorder, fingerprint, sanitize
from general_manager.chat.planned.schema_projection import (
    VERSION as SCHEMA_TRANSPORT_VERSION,
)


ROLES = (
    "planner",
    "executor",
    "synthesizer",
    "fallback",
)
PROVIDER_PATH = "experiments.gm_eval.profiles.ExperimentProvider"
ProviderFactory = Callable[[Mapping[str, Any]], "BaseLLMProvider"]
_BINDING: ContextVar[tuple[ProviderFactory, TraceRecorder] | None] = ContextVar(
    "gm_eval_provider_binding", default=None
)


class ProviderBindingError(RuntimeError):
    """The experimental provider never reads credentials or initiates login."""


@dataclass(frozen=True)
class EvaluationProfile:
    name: str
    weak_model: str
    strong_model: str
    reasoning: str = "medium"
    schema_transport_version: str = SCHEMA_TRANSPORT_VERSION

    def __post_init__(self) -> None:
        if self.schema_transport_version != SCHEMA_TRANSPORT_VERSION:
            raise ValueError("unsupported_schema_transport_version")

    def role_config(self, role: str) -> dict[str, Any]:
        strong = self.name == "strong-only" or (
            self.name == "weak-fallback" and role == "fallback"
        )
        return {
            "model": self.strong_model if strong else self.weak_model,
            "reasoning": self.reasoning,
            "role": role,
            "profile": self.name,
            "schema_transport_version": self.schema_transport_version,
            "strong": strong,
        }

    def chat_settings(
        self,
        *,
        catalog: object = None,
        audit_sink: Callable[..., Any] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        return {
            "enabled": True,
            "provider": PROVIDER_PATH,
            "provider_config": self.role_config("context"),
            "allowed_mutations": [],
            "confirm_mutations": [],
            "max_results": max_results,
            "provider_profiles": {
                role: {
                    "provider": PROVIDER_PATH,
                    "provider_config": self.role_config(role),
                    "trust_group": "synthetic-eval",
                }
                for role in ROLES
            },
            "planned": {
                "enabled": True,
                "roles": {role: role for role in ROLES},
                "catalog": catalog,
            },
            "audit": {
                "enabled": audit_sink is not None,
                "level": "all",
                "logger": audit_sink,
            },
        }


def make_profile(
    name: str,
    *,
    weak_model: str = "gpt-5.6-luna",
    strong_model: str = "gpt-6-astra",
    reasoning: str = "medium",
) -> EvaluationProfile:
    if (
        name not in {"weak-only", "weak-fallback", "strong-only"}
        or not weak_model
        or not strong_model
        or not reasoning
    ):
        raise ValueError("invalid_evaluation_profile")
    return EvaluationProfile(name, weak_model, strong_model, reasoning)


@contextmanager
def bind_provider_factory(
    factory: ProviderFactory, trace: TraceRecorder
) -> Iterator[None]:
    """Bind one case in memory; context propagation supports scheduler tasks."""
    token = _BINDING.set((factory, trace))
    try:
        yield
    finally:
        _BINDING.reset(token)


class ExperimentProvider:
    """A thin BaseLLMProvider bridge resolved by normal production imports."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        if (
            config is not None
            and config.get("schema_transport_version", SCHEMA_TRANSPORT_VERSION)
            != SCHEMA_TRANSPORT_VERSION
        ):
            raise ValueError("unsupported_schema_transport_version")
        self._config = MappingProxyType(
            dict(config or {"role": "context", "model": "context-only"})
        )

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> ExperimentProvider:
        if not isinstance(config.get("model"), str) or not config["model"]:
            raise ValueError("provider_model_required")
        return cls(config)

    @property
    def provider_config(self) -> Mapping[str, Any]:
        return self._config

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        from general_manager.chat.providers.base import (
            DoneEvent,
            TextChunkEvent,
            ToolCallEvent,
        )

        binding = _BINDING.get()
        if binding is None:
            raise ProviderBindingError("explicit_provider_binding_required")
        factory, trace = binding
        record: dict[str, Any] = {
            **dict(self._config),
            "turn": trace.turn,
            "messages": [
                {
                    "role": message.role,
                    "content": message.logical_content
                    if message.logical_content is not None
                    else message.content,
                    "tool_call_id": message.tool_call_id,
                    "tool_name": message.tool_name,
                }
                for message in messages
            ],
            "schema_projection": [message.projection_receipt for message in messages],
            "tools_sha256": fingerprint(
                [{"name": tool.name, "schema": tool.input_schema} for tool in tools]
            ),
            "text": "",
            "tool_calls": [],
            "reported_usage": None,
            "reported_cost": None,
            "request_count": 0,
            "completed": False,
        }
        trace.calls.append(record)
        started = monotonic()
        provider = None
        try:
            provider = factory(self._config)
            record["request_count"] = 1
            async for event in provider.complete(messages, tools):
                if isinstance(event, TextChunkEvent):
                    record["text"] += event.content
                elif isinstance(event, ToolCallEvent):
                    record["tool_calls"].append(
                        {
                            "id": event.id,
                            "name": event.name,
                            "args": sanitize(event.args),
                        }
                    )
                elif isinstance(event, DoneEvent):
                    record["completed"] = True
                    if not hasattr(provider, "reported_usage"):
                        record["reported_usage"] = {
                            "input_tokens": event.usage.input_tokens,
                            "output_tokens": event.usage.output_tokens,
                            "reasoning_tokens": None,
                        }
                yield event
        except BaseException as error:
            record["error_type"] = type(error).__name__
            from experiments.siwc_eval.errors import EvalError

            if isinstance(error, EvalError):
                record["error_code"] = str(error)
                record["diagnostic"] = sanitize(error.diagnostic)
            raise
        finally:
            record["latency_seconds"] = monotonic() - started
            if provider is not None:
                if hasattr(provider, "reported_usage"):
                    record["reported_usage"] = sanitize(provider.reported_usage)
                record["request_count"] = getattr(
                    provider, "requests", record["request_count"]
                )


class _ObservedTransport:
    def __init__(self, transport: Any, reasoning: str) -> None:
        self.transport = transport
        self.reasoning = reasoning
        self.reported_usage: dict[str, Any] | None = None

    async def stream(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        from contextlib import aclosing

        async with aclosing(
            self.transport.stream({**body, "reasoning": {"effort": self.reasoning}})
        ) as events:
            async for event in events:
                if event.get("type") == "response.completed":
                    usage = event.get("response", {}).get("usage")
                    if isinstance(usage, Mapping):
                        details = usage.get("output_tokens_details")
                        self.reported_usage = {
                            name: value
                            if isinstance(value, int)
                            and not isinstance(value, bool)
                            and value >= 0
                            else None
                            for name, value in {
                                "input_tokens": usage.get("input_tokens"),
                                "output_tokens": usage.get("output_tokens"),
                                "reasoning_tokens": details.get("reasoning_tokens")
                                if isinstance(details, Mapping)
                                else None,
                            }.items()
                        }
                yield event


def siwc_factory(transport: Any) -> ProviderFactory:
    """Adapt an explicitly supplied SIWC transport; never discover credentials."""
    from experiments.siwc_eval.provider import Provider
    from experiments.siwc_eval.suite import MediumTransport

    class ObservedProvider:
        def __init__(
            self, config: Mapping[str, Any], observed: _ObservedTransport
        ) -> None:
            self.observed = observed
            self.provider = Provider(str(config["model"]), observed, max_requests=1)

        @classmethod
        def from_config(cls, config: Mapping[str, Any]) -> ObservedProvider:
            raise ProviderBindingError("explicit_transport_required")

        @property
        def provider_config(self) -> Mapping[str, Any]:
            return self.provider.provider_config

        @property
        def requests(self) -> int:
            return int(self.provider.requests)

        @property
        def reported_usage(self) -> dict[str, Any] | None:
            return self.observed.reported_usage

        async def complete(
            self, messages: list[Message], tools: list[ToolDefinition]
        ) -> AsyncIterator[ChatEvent]:
            async for event in self.provider.complete(messages, tools):
                yield event

    def construct(config: Mapping[str, Any]) -> BaseLLMProvider:
        reasoning = str(config.get("reasoning", "medium"))
        if isinstance(transport, MediumTransport) and reasoning != "medium":
            raise ProviderBindingError("pinned_transport_reasoning_mismatch")
        return ObservedProvider(
            config,
            _ObservedTransport(transport, reasoning),
        )

    return construct
