"""Helpers for normalizing payloads used by database-backed interfaces."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

from django.db import models

from general_manager.interface.utils.errors import UnknownFieldError

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

PayloadMapping = dict[str, object]
ManyValuePayload = dict[str, list[object]]


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
        self._relation_fields = {
            field.name
            for field in model._meta.get_fields()
            if isinstance(field, (models.ForeignKey, models.OneToOneField))
        }

    # region filter/exclude helpers
    def normalize_filter_kwargs(self, kwargs: PayloadMapping) -> PayloadMapping:
        """
        Normalize filter keyword arguments by replacing any general-manager values with their unwrapped identifier or instance.

        Returns a new mapping and does not mutate `kwargs`. Keys are preserved.
        A "general-manager value" is an actual `GeneralManager` instance. If
        `prefer_instance` resolution cannot find `_interface._instance`, the
        manager's `identification["id"]` is used; malformed manager state such
        as a missing `"id"` propagates the underlying exception.

        Parameters:
            kwargs: Mapping of filter keyword names to values which may include general-manager wrappers.

        Returns:
            A new dictionary with the same keys and values converted to their unwrapped manager form where applicable.
        """
        return {key: self._unwrap_manager(value) for key, value in kwargs.items()}

    # endregion

    # region writable helpers
    def validate_keys(self, kwargs: PayloadMapping) -> None:
        """
        Validate that each key in the provided kwargs corresponds to a known attribute or field on the target model.

        Known attributes are names present in `vars(model)`, which includes
        descriptors, properties, methods, and other class attributes declared on
        the model class. Known fields are names from `model._meta.get_fields()`,
        including forward and reverse Django relations. The `_list` and
        `_id_list` aliases are accepted only when their base name is a real
        local many-to-many field; malformed aliases otherwise validate as their
        literal key and usually raise `UnknownFieldError`.

        Parameters:
            kwargs: Mapping of payload keys to values; keys ending with "_id_list" will be validated by their base name (suffix removed).

        Raises:
            UnknownFieldError: If any key's base name is not an attribute of the model instance and not a model field name.
        """
        for key in kwargs:
            candidate_key = self._key_for_validation(key)
            if (
                candidate_key not in self._attributes
                and candidate_key not in self._field_names
            ):
                raise UnknownFieldError(key, self.model.__name__)

    def split_many_to_many(
        self, kwargs: PayloadMapping
    ) -> tuple[PayloadMapping, PayloadMapping]:
        """
        Mutate a kwargs mapping by moving many-to-many related entries out of it.

        Parameters:
            kwargs: Mapping of field lookups where keys for many-to-many relations are expected to end with `_id_list`.

        Returns:
            tuple: A pair `(remaining_kwargs, many_kwargs)` where `remaining_kwargs` is the original `kwargs` mapping with many-to-many entries removed, and `many_kwargs` contains the removed entries whose base key (key with a trailing `_id_list` suffix stripped) corresponds to a many-to-many field.

        Notes:
            This function mutates the `kwargs` argument by removing any entries moved into `many_kwargs`. The first returned mapping is the same object passed in. If both `<relation>_list` and `<relation>_id_list` are present, iteration order decides the last canonical value stored in `many_kwargs`.
        """
        many_kwargs: PayloadMapping = {}
        for key, _value in list(kwargs.items()):
            base_key = self._m2m_alias_base_key(key)
            if base_key in self._many_to_many_fields:
                many_kwargs[f"{base_key}_id_list"] = kwargs.pop(key)
        return kwargs, many_kwargs

    def split_many_to_many_non_mutating(
        self, kwargs: PayloadMapping
    ) -> tuple[PayloadMapping, PayloadMapping]:
        """
        Return separated many-to-many entries without mutating the input mapping.

        Parameters:
            kwargs: Mapping of field lookups where keys for many-to-many
                relations may use either `<relation>_list` or
                `<relation>_id_list`.

        Returns:
            tuple: A pair `(remaining_kwargs, many_kwargs)` where both mappings
                are new dictionaries. `remaining_kwargs` contains non-matching
                entries from `kwargs`, and `many_kwargs` contains matching
                many-to-many entries under canonical `<relation>_id_list` keys.

        Notes:
            This is the non-mutating counterpart to `split_many_to_many()`. If
            both `<relation>_list` and `<relation>_id_list` are present,
            iteration order decides the last canonical value stored in
            `many_kwargs`.
        """
        remaining_kwargs: PayloadMapping = {}
        many_kwargs: PayloadMapping = {}
        for key, value in kwargs.items():
            base_key = self._m2m_alias_base_key(key)
            if base_key in self._many_to_many_fields:
                many_kwargs[f"{base_key}_id_list"] = value
            else:
                remaining_kwargs[key] = value
        return remaining_kwargs, many_kwargs

    def normalize_simple_values(self, kwargs: PayloadMapping) -> PayloadMapping:
        """
        Normalize simple (single-valued) payload entries by converting general-manager objects to their identifier form and adjusting keys.

        For each key/value in `kwargs`, if `value` is recognized as a general-manager instance its identifier is used as the value; when the original key does not end with `_id`, the key is renamed to `{key}_id`. Non-manager values are kept unchanged.
        Returns a new mapping and does not mutate `kwargs`. Relation-key
        renaming applies only to forward `ForeignKey` and `OneToOneField`
        names cached from the model metadata. Reverse relations, generic
        relations, and many-to-many fields are not relation-renamed here. If
        both a relation key and its `<relation>_id` key appear, the later
        normalized key overwrites the earlier value according to input
        iteration order.

        Parameters:
                kwargs: Mapping of field names to single values to normalize.

        Returns:
                A new mapping with manager values replaced by their identifiers and keys suffixed with `_id` when appropriate.
        """
        normalized: PayloadMapping = {}
        for key, value in kwargs.items():
            normalized_key = key
            normalized_value = value
            manager_value = self._maybe_general_manager(value)
            if manager_value is not None and not key.endswith("_id"):
                normalized_key = f"{key}_id"
                normalized_value = manager_value
            elif manager_value is not None:
                normalized_value = manager_value
            elif (
                key in self._relation_fields
                and not key.endswith("_id")
                and not isinstance(value, models.Model)
                and value is not None
            ):
                normalized_key = f"{key}_id"
            normalized[normalized_key] = normalized_value
        return normalized

    def normalize_many_values(self, kwargs: PayloadMapping) -> ManyValuePayload:
        """
        Normalize values intended to represent multi-valued model fields into lists of underlying identifiers or preserved values.

        For each key in `kwargs`:
        - Keys with value `None` or `models.NOT_PROVIDED` are omitted.
        - Iterable values (except `str` and `bytes`) are converted to lists where each item is resolved from manager-like objects to their identifier (or left unchanged if not resolvable).
        - Non-iterable values are wrapped into a single-item list after the same resolution.
        Returns a new mapping and preserves incoming keys exactly; it does not
        canonicalize `<relation>_list` to `<relation>_id_list`. Call
        `split_many_to_many()` first when canonical many-to-many keys are
        required. Dictionaries and generators are treated as iterables, so a
        dict normalizes its keys and a generator is consumed once.

        Parameters:
            kwargs: Mapping of field names to values which may be single items or iterables.

        Returns:
            A new mapping where each key maps to a list of resolved items suitable for multi-valued field assignment.
        """
        normalized: ManyValuePayload = {}
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

    def _key_for_validation(self, key: str) -> str:
        """Resolve relation list aliases only when they target real M2M fields."""
        base_key = self._m2m_alias_base_key(key)
        return base_key if base_key in self._many_to_many_fields else key

    def _m2m_alias_base_key(self, key: str) -> str:
        """Return the base key for M2M alias forms, or the original key."""
        return _base_field_name(key)

    @staticmethod
    def _unwrap_manager(value: object) -> object:
        """
        Return the underlying instance or identifier for a general-manager-like object, or the original value if not one.

        Parameters:
            value: A value that may be a general manager instance.

        Returns:
            The manager's underlying interface instance if available, otherwise the manager's identification `id` when `value` is a GeneralManager; if `value` is not a GeneralManager, returns `value` unchanged.
        """
        manager_value = PayloadNormalizer._maybe_general_manager(
            value, prefer_instance=True
        )
        return manager_value if manager_value is not None else value

    @staticmethod
    def _maybe_general_manager(
        value: object,
        *,
        default: object | None = None,
        prefer_instance: bool = False,
    ) -> object | None:
        """
        Resolve a general-manager-like object to its underlying identifier or instance.

        Parameters:
            value: The value to inspect; expected to be a general manager instance when resolution is desired.
            default: Value to return if `value` is not a general manager instance. Defaults to `None`.
            prefer_instance (bool): If True, attempt to return the manager's underlying instance via `value._interface._instance` when present;
                otherwise return the manager's identification `"id"`.

        Returns:
            The resolved instance or identifier when `value` is a general manager, or `default` if it is not.
        """
        if not _is_general_manager_instance(value):
            return default
        manager = cast("GeneralManager", value)
        if prefer_instance:
            instance = getattr(manager, "_interface", None)
            if instance is not None:
                return getattr(instance, "_instance", manager.identification["id"])
        return manager.identification["id"]


def _is_general_manager_instance(value: object) -> bool:
    """
    Determine whether a value is an instance of the project's GeneralManager base class, if that class can be imported.

    Returns:
        True if `value` is an instance of the GeneralManager base class, `False` otherwise.
    """
    manager_cls = _general_manager_base()
    return isinstance(value, manager_cls) if manager_cls else False


def _general_manager_base() -> type[object] | None:
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


def _base_field_name(key: str) -> str:
    """
    Normalize payload key suffixes used for relation list fields.

    Accepts both canonical (`*_id_list`) and GraphQL-facing (`*_list`) spellings.
    """
    if key.endswith("_id_list"):
        return key[: -len("_id_list")]
    if key.endswith("_list"):
        return key[: -len("_list")]
    return key
