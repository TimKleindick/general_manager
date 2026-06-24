"""Protocol definitions describing capabilities required by database interfaces."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable


class SupportsHistoryQuery(Protocol):
    """Protocol for the query object returned by django-simple-history managers."""

    def as_of(self, search_date: object | None) -> "SupportsHistoryQuery":
        """
        Scope the history query to the state at a given date.

        Args:
            search_date: Date-like cutoff value accepted by the underlying
                history manager, or `None` for the full history.

        Returns:
            History query limited to the specified `search_date`, or full
            history when `search_date` is `None`.
        """
        ...

    def using(self, alias: str) -> "SupportsHistoryQuery":
        """
        Return a history query scoped to the given database/router alias.

        Args:
            alias: Database/router alias to target for the returned query.

        Returns:
            A history query object configured to operate against the specified alias.
        """
        ...

    def filter(self, **kwargs: object) -> "SupportsHistoryQuery":
        """
        Filter the history query using the provided lookup expressions.

        Args:
            **kwargs: Lookup expressions to filter history records.

        Returns:
            A `SupportsHistoryQuery` representing the filtered history results.
        """
        ...

    def last(self) -> object:
        """
        Retrieve the last item from the history query results.

        Returns:
            Final object in the query result set, or `None` when the query is
            empty.
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
    pk: object

    def full_clean(self, *args: object, **kwargs: object) -> None:
        """
        Validate the model's fields and run model- and field-level validation.

        Args:
            *args: Positional arguments forwarded to Django's `Model.full_clean`.
            **kwargs: Keyword arguments forwarded to Django's `Model.full_clean`.

        Raises:
            django.core.exceptions.ValidationError: If validation fails.
        """
        ...

    def save(self, *args: object, **kwargs: object) -> object:
        """
        Persist the model instance using its implementation-defined save behavior.

        Args:
            *args: Positional arguments forwarded to the underlying save method.
            **kwargs: Keyword arguments forwarded to the underlying save method.

        Returns:
            Result of the underlying save operation.
        """
        ...


ModelSupportsHistoryT = TypeVar("ModelSupportsHistoryT", bound=SupportsHistory)
ModelSupportsWriteT = TypeVar("ModelSupportsWriteT", bound=SupportsWrite)
