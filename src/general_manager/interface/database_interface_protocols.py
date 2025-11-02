"""Protocol definitions describing capabilities required by database interfaces."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable


class _SupportsHistoryQuery(Protocol):
    """Protocol for the query object returned by django-simple-history managers."""

    def using(self, alias: str) -> "_SupportsHistoryQuery":
        """
        Return a history query scoped to the given database alias.

        Parameters:
            alias (str): Database/router alias to use for the returned query.

        Returns:
            _SupportsHistoryQuery: A query object that will operate against the specified alias.
        """
        ...

    def filter(self, **kwargs: Any) -> "_SupportsHistoryQuery":
        """
        Return a history query filtered by the provided lookup parameters.

        Parameters:
            **kwargs: Lookup expressions used to filter history records (e.g., field=value).

        Returns:
            A `_SupportsHistoryQuery` representing the filtered history query.
        """
        ...

    def last(self) -> Any:
        """
        Retrieve the last item from the history query results.

        Returns:
            Any: The final object in the query result set, or `None` if the query contains no items.
        """
        ...


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

    def full_clean(self, *args: Any, **kwargs: Any) -> None:
        """
        Validate the model's fields and run model- and field-level validation.

        Parameters:
            *args: Positional arguments supported by Django's Model.full_clean (forwarded to validators).
            **kwargs: Keyword arguments supported by Django's Model.full_clean (for example, `exclude`).

        Raises:
            django.core.exceptions.ValidationError: If validation fails.
        """
        ...

    def save(self, *args: Any, **kwargs: Any) -> Any:
        """
        Persist the model instance using its implementation-defined save behavior.

        Parameters:
            *args: Positional arguments forwarded to the underlying save implementation.
            **kwargs: Keyword arguments forwarded to the underlying save implementation.

        Returns:
            The result of the underlying save operation (commonly the saved instance or its primary key).
        """
        ...


ModelSupportsHistoryT = TypeVar("ModelSupportsHistoryT", bound=SupportsHistory)
ModelSupportsWriteT = TypeVar("ModelSupportsWriteT", bound=SupportsWrite)
