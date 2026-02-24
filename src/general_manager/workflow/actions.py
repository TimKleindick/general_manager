"""Action protocol and registry used by workflow steps."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class Action(Protocol):
    """Executable workflow action."""

    def execute(
        self,
        context: Mapping[str, Any],
        params: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Execute the action."""


class ActionRegistry:
    """Simple in-memory registry for named actions."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, name: str, action: Action, *, replace: bool = False) -> None:
        if not replace and name in self._actions:
            raise ActionAlreadyRegisteredError(name)
        self._actions[name] = action

    def get(self, name: str) -> Action:
        action = self._actions.get(name)
        if action is None:
            raise ActionNotFoundError(name)
        return action

    def execute(
        self,
        name: str,
        *,
        context: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        action = self.get(name)
        try:
            return action.execute(context or {}, params or {})
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise ActionExecutionError(name) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._actions.keys()))


class ActionError(RuntimeError):
    """Base class for action errors."""


class ActionNotFoundError(ActionError):
    """Raised when an action name is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Action '{name}' is not registered.")


class ActionExecutionError(ActionError):
    """Raised when action execution fails."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Action '{name}' failed during execution.")


class ActionAlreadyRegisteredError(ActionError):
    """Raised when an action is registered more than once."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Action '{name}' is already registered.")
