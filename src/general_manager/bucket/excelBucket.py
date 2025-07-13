from __future__ import annotations
from typing import Any, Generator, Iterable, Optional, Type, Union, TYPE_CHECKING, cast

from general_manager.bucket.baseBucket import Bucket, GeneralManagerType
from general_manager.utils.filterParser import parse_filters, create_filter_function

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager


class ExcelBucket(Bucket[GeneralManagerType]):
    """Bucket for managing rows of excel data."""

    def __init__(
        self,
        manager_class: Type[GeneralManagerType],
        data: Iterable[dict[str, Any]],
        filter_definitions: Optional[dict[str, dict]] = None,
        exclude_definitions: Optional[dict[str, dict]] = None,
        sort_key: Optional[Union[str, tuple[str, ...]]] = None,
        reverse: bool = False,
    ) -> None:
        from general_manager.interface.excelInterface import ExcelInterface

        super().__init__(manager_class)
        self._original_data = list(data)
        self.filters = {} if filter_definitions is None else filter_definitions
        self.excludes = {} if exclude_definitions is None else exclude_definitions
        self.sort_key = sort_key
        self.reverse = reverse
        self._current_data: Optional[list[dict[str, Any]]] = None
        if not issubclass(manager_class.Interface, ExcelInterface):
            raise TypeError("Manager class must implement ExcelInterface")
        self.interface_class = manager_class.Interface

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManagerType,
    ) -> "ExcelBucket[GeneralManagerType]":
        if isinstance(other, Bucket):
            if (
                not isinstance(other, ExcelBucket)
                or other._manager_class != self._manager_class
            ):
                raise ValueError("Cannot combine different bucket types")
            data = self._original_data + other._original_data
        else:
            if other.__class__ != self._manager_class:
                raise ValueError("Cannot combine different bucket managers")
            data = self._original_data + [other.identification]
        return ExcelBucket(
            self._manager_class,
            data,
            self.filters.copy(),
            self.excludes.copy(),
            self.sort_key,
            self.reverse,
        )

    # helper functions
    def _row_matches(self, row: dict[str, Any]) -> bool:
        from general_manager.utils.filterParser import apply_lookup

        for field, defs in self.filters.items():
            value = row.get(field)
            for func in defs.get("filter_funcs", []):
                if not func(value):
                    return False
            for lookup, val in defs.get("filter_kwargs", {}).items():
                if not apply_lookup(value, lookup or "exact", val):
                    return False
        for field, defs in self.excludes.items():
            value = row.get(field)
            for func in defs.get("filter_funcs", []):
                if func(value):
                    return False
            for lookup, val in defs.get("filter_kwargs", {}).items():
                if apply_lookup(value, lookup or "exact", val):
                    return False
        return True

    def _get_current_data(self) -> list[dict[str, Any]]:
        if self._current_data is None:
            data = [row for row in self._original_data if self._row_matches(row)]
            if self.sort_key is not None:
                key = self.sort_key
                if isinstance(key, str):
                    key = (key,)
                data = sorted(data, key=lambda r: tuple(r[k] for k in key))
            if self.reverse:
                data.reverse()
            self._current_data = data
        return self._current_data

    def filter(self, **kwargs: Any) -> "ExcelBucket[GeneralManagerType]":
        new_filters = {k: v.copy() for k, v in self.filters.items()}
        parsed = parse_filters(kwargs, self.interface_class.data_fields)
        for field, defs in parsed.items():
            dest = new_filters.setdefault(field, {})
            dest.setdefault("filter_funcs", []).extend(defs.get("filter_funcs", []))
            for lookup, val in defs.get("filter_kwargs", {}).items():
                func = create_filter_function(lookup, val)
                dest.setdefault("filter_funcs", []).append(lambda x, f=func: f(x))
        return ExcelBucket(
            self._manager_class,
            self._original_data,
            new_filters,
            self.excludes.copy(),
            self.sort_key,
            self.reverse,
        )

    def exclude(self, **kwargs: Any) -> "ExcelBucket[GeneralManagerType]":
        new_excludes = {k: v.copy() for k, v in self.excludes.items()}
        parsed = parse_filters(kwargs, self.interface_class.data_fields)
        for field, defs in parsed.items():
            dest = new_excludes.setdefault(field, {})
            dest.setdefault("filter_funcs", []).extend(defs.get("filter_funcs", []))
            for lookup, val in defs.get("filter_kwargs", {}).items():
                func = create_filter_function(lookup, val)
                dest.setdefault("filter_funcs", []).append(lambda x, f=func: f(x))
        return ExcelBucket(
            self._manager_class,
            self._original_data,
            self.filters.copy(),
            new_excludes,
            self.sort_key,
            self.reverse,
        )

    def __iter__(self) -> Generator[GeneralManagerType, None, None]:
        for row in self._get_current_data():
            yield self._manager_class(**row)

    def first(self) -> Optional[GeneralManagerType]:
        try:
            return next(iter(self))
        except StopIteration:
            return None

    def last(self) -> Optional[GeneralManagerType]:
        items = list(self)
        if items:
            return items[-1]
        return None

    def count(self) -> int:
        return len(self)

    def all(self) -> "ExcelBucket[GeneralManagerType]":
        return self

    def __getitem__(
        self, item: int | slice
    ) -> Union[GeneralManagerType, "ExcelBucket[GeneralManagerType]"]:
        data = self._get_current_data()
        result = data[item]
        if isinstance(result, list):
            new_bucket = ExcelBucket(
                self._manager_class,
                self._original_data,
                self.filters.copy(),
                self.excludes.copy(),
                self.sort_key,
                self.reverse,
            )
            new_bucket._current_data = result
            return new_bucket
        return self._manager_class(**result)

    def __contains__(self, item: GeneralManagerType) -> bool:
        return any(item == mgr for mgr in self)

    def __len__(self) -> int:
        return len(self._get_current_data())

    def sort(
        self, key: Union[str, tuple[str, ...]], reverse: bool = False
    ) -> "ExcelBucket[GeneralManagerType]":
        return ExcelBucket(
            self._manager_class,
            self._original_data,
            self.filters.copy(),
            self.excludes.copy(),
            key,
            reverse,
        )

    def get(self, **kwargs: Any) -> GeneralManagerType:
        bucket = self.filter(**kwargs)
        items = list(bucket)
        if len(items) == 1:
            return items[0]
        if len(items) == 0:
            raise ValueError("No matching element found.")
        raise ValueError("Multiple elements found.")
