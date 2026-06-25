"""Action protocol and registry used by workflow steps."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

type ActionPayload = Mapping[str, object]
type ActionResult = Mapping[str, object] | None


@runtime_checkable
class Action(Protocol):
    """Executable workflow action used by `ActionRegistry`.

    Actions receive read-only context and parameter mappings and either return a
    mapping result or `None`. Exceptions raised by the implementation are wrapped
    by `ActionRegistry.execute()`.
    """

    def execute(
        self,
        context: ActionPayload,
        params: ActionPayload,
    ) -> ActionResult:
        """Run the side effect and return an optional result mapping."""


class ActionRegistry:
    """Process-local in-memory registry for exact action name strings.

    The registry stores actions without runtime protocol validation. Invalid
    action objects fail later when `execute()` calls their `execute` attribute.
    """

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(self, name: str, action: Action, *, replace: bool = False) -> None:
        """Register `action` under the exact string `name`.

        Raises:
            ActionAlreadyRegisteredError: If `name` is already registered and
                `replace` is false.
        """
        if not replace and name in self._actions:
            raise ActionAlreadyRegisteredError(name)
        self._actions[name] = action

    def get(self, name: str) -> Action:
        """Return the action registered as the exact string `name`.

        Raises:
            ActionNotFoundError: If no action has been registered for `name`.
        """
        action = self._actions.get(name)
        if action is None:
            raise ActionNotFoundError(name)
        return action

    def execute(
        self,
        name: str,
        *,
        context: ActionPayload | None = None,
        params: ActionPayload | None = None,
    ) -> ActionResult:
        """Execute the named action with optional context and params.

        Missing context or params are passed as fresh empty dictionaries. Supplied
        mappings are passed through unchanged, including falsey mappings. Lookup
        failures raise `ActionNotFoundError`; exceptions from the action itself
        are wrapped in `ActionExecutionError` with the original exception as the
        cause.
        """
        action = self.get(name)
        try:
            return action.execute(
                {} if context is None else context,
                {} if params is None else params,
            )
        except Exception as exc:  # pragma: no cover - defensive boundary
            raise ActionExecutionError(name) from exc

    def names(self) -> tuple[str, ...]:
        """Return registered action names sorted alphabetically."""
        return tuple(sorted(self._actions.keys()))


class ActionError(RuntimeError):
    """Base class for action errors."""


class ActionNotFoundError(ActionError):
    """Raised when an action name is not registered.

    The error message includes the missing action name.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Action '{name}' is not registered.")


class ActionExecutionError(ActionError):
    """Raised when action execution fails.

    The error message includes the action name.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Action '{name}' failed during execution.")


class ActionAlreadyRegisteredError(ActionError):
    """Raised when an action is registered more than once.

    The error message includes the duplicate action name.
    """

    def __init__(self, name: str) -> None:
        super().__init__(f"Action '{name}' is already registered.")
