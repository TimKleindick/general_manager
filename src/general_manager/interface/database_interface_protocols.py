"""Protocol definitions describing capabilities required by database interfaces."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable


class _SupportsHistoryQuery(Protocol):
    """Protocol for the query object returned by django-simple-history managers."""

    def using(self, alias: str) -> "_SupportsHistoryQuery": ...

    def filter(self, **kwargs: Any) -> "_SupportsHistoryQuery": ...

    def last(self) -> Any: ...


@runtime_checkable
class SupportsHistory(Protocol):
    """Protocol for models exposing a django-simple-history manager."""

    history: _SupportsHistoryQuery


@runtime_checkable
class SupportsActivation(Protocol):
    """Protocol for models that can be activated/deactivated."""

    is_active: bool


@runtime_checkable
class SupportsWrite(Protocol):
    """Protocol for models supporting full_clean/save operations."""

    history: _SupportsHistoryQuery
    pk: Any

    def full_clean(self, *args: Any, **kwargs: Any) -> None: ...

    def save(self, *args: Any, **kwargs: Any) -> Any: ...


ModelSupportsHistoryT = TypeVar("ModelSupportsHistoryT", bound=SupportsHistory)
ModelSupportsWriteT = TypeVar("ModelSupportsWriteT", bound=SupportsWrite)
