"""Wrapper for accessing permission-relevant data across manager operations."""

from __future__ import annotations
from typing import Callable, Optional, TypeVar, Generic, cast

from general_manager.manager.general_manager import GeneralManager


class InvalidPermissionDataError(TypeError):
    """Raised when the permission data manager receives unsupported input."""

    def __init__(self) -> None:
        """
        Exception raised when a permission data input is not a dict or a GeneralManager instance.

        The exception carries the message: "permission_data must be either a dict or an instance of GeneralManager."
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
        manager: Optional[type[GeneralManagerData]] = None,
    ) -> None:
        """
        Wrap a mapping or GeneralManager instance to expose permission-related fields via attribute access.

        Parameters:
            permission_data (dict[str, object] | GeneralManager): Either a dict mapping field names to values or a GeneralManager instance whose attributes provide field values.
            manager (type[GeneralManager] | None): When `permission_data` is a dict, the manager class associated with that data; otherwise ignored.

        Raises:
            InvalidPermissionDataError: If `permission_data` is neither a dict nor an instance of GeneralManager.
        """
        self.get_data: Callable[[str], object]
        self._permission_data = permission_data
        self._manager: type[GeneralManagerData] | None
        if isinstance(permission_data, GeneralManager):
            gm_instance = permission_data

            def manager_getter(name: str) -> object:
                return getattr(gm_instance, name)

            self.get_data = manager_getter
            self._manager = cast(type[GeneralManagerData], permission_data.__class__)
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
    ) -> PermissionDataManager:
        """
        Create a PermissionDataManager representing `base_data` with `update_data` applied.

        Parameters:
            base_data (GeneralManagerData): Existing manager instance whose data will serve as the base.
            update_data (dict[str, object]): Fields to add or override on the base data.

        Returns:
            PermissionDataManager: Wrapper exposing the merged data where keys in `update_data` override those from `base_data`.
        """
        merged_data: dict[str, object] = {**dict(base_data), **update_data}
        return cls(merged_data, base_data.__class__)

    @property
    def permission_data(self) -> dict[str, object] | GeneralManagerData:
        """Return the underlying permission payload."""
        return self._permission_data

    @property
    def manager(self) -> type[GeneralManagerData] | None:
        """Return the manager class associated with the permission data."""
        return self._manager

    def __getattr__(self, name: str) -> object:
        """Proxy attribute access to the wrapped permission data."""
        return self.get_data(name)
