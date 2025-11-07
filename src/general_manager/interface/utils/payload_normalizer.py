"""Helpers for normalizing payloads used by database-backed interfaces."""

from __future__ import annotations

from typing import Any, Iterable, Tuple

from django.db import models

from general_manager.interface.utils.errors import UnknownFieldError


class PayloadNormalizer:
    """Normalize keyword payloads for database-backed interface operations."""

    def __init__(self, model: type[models.Model]) -> None:
        self.model = model
        self._attributes = set(vars(model).keys())
        self._field_names = {field.name for field in model._meta.get_fields()}
        self._many_to_many_fields = {field.name for field in model._meta.many_to_many}

    # region filter/exclude helpers
    def normalize_filter_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {key: self._unwrap_manager(value) for key, value in kwargs.items()}

    # endregion

    # region writable helpers
    def validate_keys(self, kwargs: dict[str, Any]) -> None:
        for key in kwargs:
            base_key = key.split("_id_list")[0]
            if base_key not in self._attributes and base_key not in self._field_names:
                raise UnknownFieldError(key, self.model.__name__)

    def split_many_to_many(
        self, kwargs: dict[str, Any]
    ) -> Tuple[dict[str, Any], dict[str, Any]]:
        many_kwargs: dict[str, Any] = {}
        for key, _value in list(kwargs.items()):
            base_key = key.split("_id_list")[0]
            if base_key in self._many_to_many_fields:
                many_kwargs[key] = kwargs.pop(key)
        return kwargs, many_kwargs

    def normalize_simple_values(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in kwargs.items():
            normalized_key = key
            normalized_value = value
            manager_value = self._maybe_general_manager(value)
            if manager_value is not None and not key.endswith("_id"):
                normalized_key = f"{key}_id"
                normalized_value = manager_value
            elif manager_value is not None:
                normalized_value = manager_value
            normalized[normalized_key] = normalized_value
        return normalized

    def normalize_many_values(self, kwargs: dict[str, Any]) -> dict[str, list[Any]]:
        normalized: dict[str, list[Any]] = {}
        for key, value in kwargs.items():
            if value is None or value is models.NOT_PROVIDED:
                continue
            if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                normalized[key] = [
                    self._maybe_general_manager(item, default=item) for item in value
                ]
            else:
                normalized[key] = [
                    self._maybe_general_manager(value, default=value),
                ]
        return normalized

    # endregion

    @staticmethod
    def _unwrap_manager(value: Any) -> Any:
        manager_value = PayloadNormalizer._maybe_general_manager(
            value, prefer_instance=True
        )
        return manager_value if manager_value is not None else value

    @staticmethod
    def _maybe_general_manager(
        value: Any,
        *,
        default: Any | None = None,
        prefer_instance: bool = False,
    ) -> Any:
        if not _is_general_manager_instance(value):
            return default
        if prefer_instance:
            instance = getattr(value, "_interface", None)
            if instance is not None:
                return getattr(instance, "_instance", value.identification["id"])
        return value.identification["id"]


def _is_general_manager_instance(value: Any) -> bool:
    manager_cls = _general_manager_base()
    return isinstance(value, manager_cls) if manager_cls else False


def _general_manager_base() -> type | None:
    try:
        from general_manager.manager.general_manager import GeneralManager
    except ImportError:  # pragma: no cover - defensive
        return None
    return GeneralManager
