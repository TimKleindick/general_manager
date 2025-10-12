"""Wrapper for accessing permission-relevant data across manager operations."""

from __future__ import annotations
from typing import Callable, Optional, TypeVar, Generic, cast
from django.contrib.auth.models import AbstractUser

from general_manager.manager.generalManager import GeneralManager

GeneralManagerData = TypeVar("GeneralManagerData", bound=GeneralManager)


class PermissionDataManager(Generic[GeneralManagerData]):
    """Adapter that exposes permission-related data as a unified interface."""

    def __init__(
        self,
        permission_data: dict[str, object] | GeneralManagerData,
        manager: Optional[type[GeneralManagerData]] = None,
    ) -> None:
        """
        Create a permission data manager wrapping either a dict or a manager instance.

        Parameters:
            permission_data (dict[str, Any] | GeneralManager): Raw data or manager instance supplying field values.
            manager (type[GeneralManager] | None): Manager class when `permission_data` is a dict.

        Raises:
            TypeError: If `permission_data` is neither a dict nor a `GeneralManager`.
        """
        self.getData: Callable[[str], object]
        self._permission_data = permission_data
        self._manager: type[GeneralManagerData] | None
        if isinstance(permission_data, GeneralManager):
            gm_instance = permission_data

            def manager_getter(name: str) -> object:
                return getattr(gm_instance, name)

            self.getData = manager_getter
            self._manager = cast(type[GeneralManagerData], permission_data.__class__)
        elif isinstance(permission_data, dict):
            data_mapping = permission_data

            def dict_getter(name: str) -> object:
                return data_mapping.get(name)

            self.getData = dict_getter
            self._manager = manager
        else:
            raise TypeError(
                "permission_data must be either a dict or an instance of GeneralManager"
            )

    @classmethod
    def forUpdate(
        cls,
        base_data: GeneralManagerData,
        update_data: dict[str, object],
    ) -> PermissionDataManager:
        """
        Create a data manager that reflects a pending update to an existing manager.

        Parameters:
            base_data (GeneralManager): Existing manager instance.
            update_data (dict[str, Any]): Fields being updated.

        Returns:
            PermissionDataManager: Wrapper exposing merged data for permission checks.
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
        return self.getData(name)
