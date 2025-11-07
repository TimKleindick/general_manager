"""Helpers for normalizing payloads used by database-backed interfaces."""

from __future__ import annotations

from typing import Any, Iterable, Tuple

from django.db import models

from general_manager.interface.utils.errors import UnknownFieldError


class PayloadNormalizer:
    """Normalize keyword payloads for database-backed interface operations."""

    def __init__(self, model: type[models.Model]) -> None:
        """
        Initialize the normalizer for a Django model and cache metadata used for payload normalization.

        Parameters:
            model (type[django.db.models.Model]): The Django model class whose attributes and fields will be inspected to validate and normalize payload keys and values.
        """
        self.model = model
        self._attributes = set(vars(model).keys())
        self._field_names = {field.name for field in model._meta.get_fields()}
        self._many_to_many_fields = {field.name for field in model._meta.many_to_many}

    # region filter/exclude helpers
    def normalize_filter_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize filter keyword arguments by replacing any general-manager values with their unwrapped identifier or instance.

        Parameters:
            kwargs (dict[str, Any]): Mapping of filter keyword names to values which may include general-manager wrappers.

        Returns:
            dict[str, Any]: A new dictionary with the same keys and values converted to their unwrapped manager form where applicable.
        """
        return {key: self._unwrap_manager(value) for key, value in kwargs.items()}

    # endregion

    # region writable helpers
    def validate_keys(self, kwargs: dict[str, Any]) -> None:
        """
        Validate that each key in the provided kwargs corresponds to a known attribute or field on the target model.

        Parameters:
            kwargs (dict[str, Any]): Mapping of payload keys to values; keys ending with "_id_list" will be validated by their base name (suffix removed).

        Raises:
            UnknownFieldError: If any key's base name is not an attribute of the model instance and not a model field name.
        """
        for key in kwargs:
            base_key = key.split("_id_list")[0]
            if base_key not in self._attributes and base_key not in self._field_names:
                raise UnknownFieldError(key, self.model.__name__)

    def split_many_to_many(
        self, kwargs: dict[str, Any]
    ) -> Tuple[dict[str, Any], dict[str, Any]]:
        """
        Separate many-to-many related entries from a kwargs mapping.

        Parameters:
            kwargs (dict[str, Any]): Mapping of field lookups where keys for many-to-many relations are expected to end with `_id_list`.

        Returns:
            tuple: A pair `(remaining_kwargs, many_kwargs)` where `remaining_kwargs` is the original `kwargs` mapping with many-to-many entries removed, and `many_kwargs` contains the removed entries whose base key (key with a trailing `_id_list` suffix stripped) corresponds to a many-to-many field.

        Notes:
            This function mutates the `kwargs` argument by removing any entries moved into `many_kwargs`.
        """
        many_kwargs: dict[str, Any] = {}
        for key, _value in list(kwargs.items()):
            base_key = key.split("_id_list")[0]
            if base_key in self._many_to_many_fields:
                many_kwargs[key] = kwargs.pop(key)
        return kwargs, many_kwargs

    def normalize_simple_values(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize simple (single-valued) payload entries by converting general-manager objects to their identifier form and adjusting keys.

        For each key/value in `kwargs`, if `value` is recognized as a general-manager instance its identifier is used as the value; when the original key does not end with `_id`, the key is renamed to `{key}_id`. Non-manager values are kept unchanged.

        Parameters:
                kwargs (dict[str, Any]): Mapping of field names to single values to normalize.

        Returns:
                dict[str, Any]: A new mapping with manager values replaced by their identifiers and keys suffixed with `_id` when appropriate.
        """
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
        """
        Normalize values intended to represent multi-valued model fields into lists of underlying identifiers or preserved values.

        For each key in `kwargs`:
        - Keys with value `None` or `models.NOT_PROVIDED` are omitted.
        - Iterable values (except `str` and `bytes`) are converted to lists where each item is resolved from manager-like objects to their identifier (or left unchanged if not resolvable).
        - Non-iterable values are wrapped into a single-item list after the same resolution.

        Parameters:
            kwargs (dict[str, Any]): Mapping of field names to values which may be single items or iterables.

        Returns:
            dict[str, list[Any]]: A new mapping where each key maps to a list of resolved items suitable for multi-valued field assignment.
        """
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
        """
        Return the underlying instance or identifier for a general-manager-like object, or the original value if not one.

        Parameters:
            value (Any): A value that may be a general manager instance.

        Returns:
            Any: The manager's underlying interface instance if available, otherwise the manager's identification `id` when `value` is a GeneralManager; if `value` is not a GeneralManager, returns `value` unchanged.
        """
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
        """
        Resolve a general-manager-like object to its underlying identifier or instance.

        Parameters:
            value (Any): The value to inspect; expected to be a general manager instance when resolution is desired.
            default (Any | None): Value to return if `value` is not a general manager instance. Defaults to `None`.
            prefer_instance (bool): If True, attempt to return the manager's underlying instance via `value._interface._instance` when present;
                otherwise return the manager's identification `"id"`.

        Returns:
            Any: The resolved instance or identifier when `value` is a general manager, or `default` if it is not.
        """
        if not _is_general_manager_instance(value):
            return default
        if prefer_instance:
            instance = getattr(value, "_interface", None)
            if instance is not None:
                return getattr(instance, "_instance", value.identification["id"])
        return value.identification["id"]


def _is_general_manager_instance(value: Any) -> bool:
    """
    Determine whether a value is an instance of the project's GeneralManager base class, if that class can be imported.

    Returns:
        True if `value` is an instance of the GeneralManager base class, `False` otherwise.
    """
    manager_cls = _general_manager_base()
    return isinstance(value, manager_cls) if manager_cls else False


def _general_manager_base() -> type | None:
    """
    Retrieve the GeneralManager base class if it can be imported.

    Returns:
        The `GeneralManager` class when available, otherwise `None`.
    """
    try:
        from general_manager.manager.general_manager import GeneralManager
    except ImportError:  # pragma: no cover - defensive
        return None
    return GeneralManager
