"""Wrapper for accessing permission-relevant data across manager operations."""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

from general_manager.manager.general_manager import GeneralManager


class InvalidPermissionDataError(TypeError):
    """Raised when the permission data manager receives unsupported input."""

    def __init__(self) -> None:
        """Build the error for unsupported permission payload types.

        The public message is stable:
        ``permission_data must be either a dict or an instance of GeneralManager.``
        """
        super().__init__(
            "permission_data must be either a dict or an instance of GeneralManager."
        )


GeneralManagerData = TypeVar("GeneralManagerData", bound=GeneralManager)


class PermissionDataManager(Generic[GeneralManagerData]):
    """Adapter that exposes permission-related data as a unified interface."""

    def __init__(
        self,
        permission_data: dict[str, object] | GeneralManagerData,
        manager: type[GeneralManagerData] | None = None,
    ) -> None:
        """Wrap a permission payload and expose its fields through attributes.

        ``permission_data`` accepts ``dict`` instances and ``dict`` subclasses,
        not arbitrary ``Mapping`` implementations. Dictionary payloads are used
        for create, update, and mutation checks. Attribute access returns
        ``dict.get(name)``, so missing keys resolve to ``None`` instead of
        raising ``AttributeError``. ``manager`` records the manager class
        associated with a dictionary payload so delegated permission checks can
        resolve related manager values. ``manager=None`` is valid for
        dictionary payloads that do not need delegated manager resolution.

        Manager instance payloads are used for read/delete checks. Attribute
        access delegates to ``getattr(instance, name)`` and propagates that
        lookup's result or exception. For instance payloads, ``manager`` is
        ignored and inferred from ``type(permission_data)``.

        Wrapper attributes and properties take precedence over payload keys with
        the same name. For example, a dictionary key named ``"manager"`` does
        not shadow the :attr:`manager` property; access the original dictionary
        through :attr:`permission_data` when such keys must be read.

        Args:
            permission_data: Dictionary of field names to permission values or
                a ``GeneralManager`` instance whose attributes provide values.
            manager: Manager class associated with a dictionary payload.

        Raises:
            InvalidPermissionDataError: If ``permission_data`` is neither a
                dictionary nor a ``GeneralManager`` instance.
        """
        self.get_data: Callable[[str], object]
        self._permission_data = permission_data
        self._manager: type[GeneralManagerData] | None
        if isinstance(permission_data, GeneralManager):
            gm_instance = permission_data

            def manager_getter(name: str) -> object:
                return getattr(gm_instance, name)

            self.get_data = manager_getter
            self._manager = type(permission_data)
        elif isinstance(permission_data, dict):
            data_mapping = permission_data

            def dict_getter(name: str) -> object:
                return data_mapping.get(name)

            self.get_data = dict_getter
            self._manager = manager
        else:
            raise InvalidPermissionDataError()

    @classmethod
    def for_update(
        cls,
        base_data: GeneralManagerData,
        update_data: dict[str, object],
    ) -> PermissionDataManager[GeneralManagerData]:
        """Create a wrapper representing ``base_data`` with updates applied.

        ``base_data`` must support ``dict(base_data)``. It is converted with
        that operation and then shallowly overlaid with ``update_data``. Values
        from ``update_data`` win on key conflicts; nested dictionaries or other
        mutable values are not deep-merged. The returned wrapper stores
        ``type(base_data)`` as its ``manager`` and exposes only the merged final
        state through dictionary-style missing-key semantics. The original
        object remains available to callers outside this wrapper if they need a
        separate before/after comparison.

        Args:
            base_data: Existing manager instance whose iterable key/value data
                provides the base permission state.
            update_data: Field values to add or override for the permission
                check.

        Returns:
            Wrapper exposing the merged permission state.
        """
        merged_data: dict[str, object] = {**dict(base_data), **update_data}
        return cls(merged_data, base_data.__class__)

    @property
    def permission_data(self) -> dict[str, object] | GeneralManagerData:
        """Return the original mapping or manager instance payload."""
        return self._permission_data

    @property
    def manager(self) -> type[GeneralManagerData] | None:
        """Return the manager class associated with the permission data."""
        return self._manager

    def __getattr__(self, name: str) -> object:
        """Return the named value from the wrapped permission payload."""
        return self.get_data(name)
