"""Protocol definitions describing capabilities required by database interfaces."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable


class SupportsHistoryQuery(Protocol):
    """Protocol for the query object returned by django-simple-history managers."""

    def as_of(self, search_date: Any | None) -> "SupportsHistoryQuery":
        """
        Scope the history query to the state at a given date.

        Parameters:
            search_date (Any | None): The date or timestamp to scope the history
                to; if `None`, the returned query covers the full history.

        Returns:
            SupportsHistoryQuery: A history query limited to the specified
                `search_date`, or the full history when `search_date` is `None`.
        """
        ...

    def using(self, alias: str) -> "SupportsHistoryQuery":
        """
        Return a history query scoped to the given database/router alias.

        Parameters:
            alias (str): Database/router alias to target for the returned query.

        Returns:
            A history query object configured to operate against the specified alias.
        """
        ...

    def filter(self, **kwargs: Any) -> "SupportsHistoryQuery":
        """
        Filter the history query using the provided lookup expressions.

        Parameters:
            **kwargs: Lookup expressions to filter history records (for example, field=value).

        Returns:
            A `SupportsHistoryQuery` representing the filtered history results.
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

    history: SupportsHistoryQuery


@runtime_checkable
class SupportsActivation(Protocol):
    """Protocol for models that can be activated/deactivated."""

    is_active: bool


@runtime_checkable
class SupportsWrite(Protocol):
    """Protocol for models supporting full_clean/save operations."""

    history: SupportsHistoryQuery
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
