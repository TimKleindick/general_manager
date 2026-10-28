"""Manager-valued selection over an object's raw audit history."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload

from django.db import models
from django.utils import timezone

from general_manager.as_of import HistoricalContextConflictError, resolve_search_date

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

ManagerT = TypeVar("ManagerT", bound="GeneralManager")


class ManagerHistoryQuery(Generic[ManagerT]):
    """Select original managers while retaining explicit raw audit access.

    Query shaping and scalar projections use Django's history queryset. Object
    selection binds each version to its closing snapshot, or to the exact
    explicit/ambient date. ``raw`` is the unchanged audit queryset, including
    tombstones and zero-duration versions, and is intentionally not a dated
    entity read. Less common Django audit operations belong on that queryset.
    """

    def __init__(
        self,
        raw: models.QuerySet[models.Model],
        manager_class: type[ManagerT],
        *,
        search_date: datetime | None = None,
        timeline: models.QuerySet[models.Model] | None = None,
    ) -> None:
        self.raw = raw
        self._manager_class = manager_class
        self._search_date = resolve_search_date(search_date)
        self._timeline = raw if timeline is None else timeline

    def _clone(
        self, raw: models.QuerySet[models.Model]
    ) -> ManagerHistoryQuery[ManagerT]:
        return type(self)(
            raw,
            self._manager_class,
            search_date=self._search_date,
            timeline=self._timeline,
        )

    def _dated_query(self) -> models.QuerySet[models.Model]:
        snapshot = resolve_search_date(self._search_date)
        if snapshot is None:
            return self.raw
        return self.raw.filter(history_date__lte=snapshot)

    def _materialize(self, row: models.Model) -> ManagerT:
        audit = cast(Any, row)
        model = cast(Any, self._manager_class.Interface)._model
        if audit.history_type == "-":
            raise model.DoesNotExist
        successor = (
            self._timeline.filter(
                models.Q(history_date__gt=audit.history_date)
                | models.Q(
                    history_date=audit.history_date, history_id__gt=audit.history_id
                )
            )
            .order_by("history_date", "history_id")
            .first()
        )
        start = cast(datetime, audit.history_date).astimezone(UTC)
        end = (
            cast(datetime, cast(Any, successor).history_date).astimezone(UTC)
            if successor is not None
            else None
        )
        snapshot = resolve_search_date(self._search_date)
        if snapshot is None:
            snapshot = (
                end - timedelta(microseconds=1) if end is not None else timezone.now()
            )
        instant = snapshot.astimezone(UTC)
        if instant < start or (end is not None and instant >= end):
            raise HistoricalContextConflictError
        # A historical row's pk is its history_id, not the original entity id.
        original_pk = getattr(row, model._meta.pk.attname)
        return self._manager_class(original_pk, search_date=snapshot)

    def all(self) -> ManagerHistoryQuery[ManagerT]:
        return self._clone(self.raw.all())

    def filter(self, *args: Any, **kwargs: Any) -> ManagerHistoryQuery[ManagerT]:
        return self._clone(self.raw.filter(*args, **kwargs))

    def exclude(self, *args: Any, **kwargs: Any) -> ManagerHistoryQuery[ManagerT]:
        return self._clone(self.raw.exclude(*args, **kwargs))

    def order_by(self, *fields: str) -> ManagerHistoryQuery[ManagerT]:
        return self._clone(self.raw.order_by(*fields))

    def reverse(self) -> ManagerHistoryQuery[ManagerT]:
        return self._clone(self.raw.reverse())

    def first(self) -> ManagerT | None:
        row = self._dated_query().first()
        return None if row is None else self._materialize(row)

    def last(self) -> ManagerT | None:
        row = self._dated_query().last()
        return None if row is None else self._materialize(row)

    def get(self, *args: Any, **kwargs: Any) -> ManagerT:
        return self._materialize(self._dated_query().get(*args, **kwargs))

    def latest(self, *fields: str) -> ManagerT:
        return self._materialize(self._dated_query().latest(*fields))

    def earliest(self, *fields: str) -> ManagerT:
        return self._materialize(self._dated_query().earliest(*fields))

    def count(self) -> int:
        return self._dated_query().count()

    def exists(self) -> bool:
        return self._dated_query().exists()

    def values(self, *fields: str, **expressions: Any) -> models.QuerySet[Any]:
        return cast(
            models.QuerySet[Any], self._dated_query().values(*fields, **expressions)
        )

    def values_list(self, *fields: str, **kwargs: Any) -> models.QuerySet[Any]:
        return cast(
            models.QuerySet[Any], self._dated_query().values_list(*fields, **kwargs)
        )

    def __iter__(self) -> Iterator[ManagerT]:
        for row in self._dated_query():
            yield self._materialize(row)

    def __len__(self) -> int:
        return self.count()

    def __bool__(self) -> bool:
        return self.exists()

    @overload
    def __getitem__(self, key: int) -> ManagerT: ...

    @overload
    def __getitem__(self, key: slice) -> list[ManagerT]: ...

    def __getitem__(self, key: int | slice) -> ManagerT | list[ManagerT]:
        if isinstance(key, int):
            return self._materialize(self._dated_query()[key])
        return [self._materialize(row) for row in self._dated_query()[key]]

    def __repr__(self) -> str:
        return f"ManagerHistoryQuery({self._manager_class.__name__})"
