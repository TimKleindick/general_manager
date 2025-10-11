"""Wrapper for accessing permission-relevant data across manager operations."""

from __future__ import annotations
from typing import Callable, Dict, Any, Optional, TypeVar, Generic
from django.contrib.auth.models import AbstractUser

from general_manager.manager.generalManager import GeneralManager

GeneralManagerData = TypeVar("GeneralManagerData", bound=GeneralManager)


class PermissionDataManager(Generic[GeneralManagerData]):
    """Adapter that exposes permission-related data as a unified interface."""

    def __init__(
        self,
        permission_data: Dict[str, Any] | GeneralManagerData,
        manager: Optional[type[GeneralManagerData]] = None,
    ):
        """
        Create a permission data manager wrapping either a dict or a manager instance.

        Parameters:
            permission_data (dict[str, Any] | GeneralManager): Raw data or manager instance supplying field values.
            manager (type[GeneralManager] | None): Manager class when `permission_data` is a dict.

        Raises:
            TypeError: If `permission_data` is neither a dict nor a `GeneralManager`.
        """
        self.getData: Callable[[str], Any]
        self._permission_data = permission_data
        if isinstance(permission_data, GeneralManager):
            self.getData = lambda name, permission_data=permission_data: getattr(
                permission_data, name
            )
            self._manager = permission_data.__class__
        elif isinstance(permission_data, dict):
            self.getData = (
                lambda name, permission_data=permission_data: permission_data.get(name)
            )
            self._manager = manager
        else:
            raise TypeError(
                "permission_data must be either a dict or an instance of GeneralManager"
            )

    @classmethod
    def forUpdate(
        cls,
        base_data: GeneralManagerData,
        update_data: Dict[str, Any],
    ) -> PermissionDataManager:
        """
        Create a data manager that reflects a pending update to an existing manager.

        Parameters:
            base_data (GeneralManager): Existing manager instance.
            update_data (dict[str, Any]): Fields being updated.

        Returns:
            PermissionDataManager: Wrapper exposing merged data for permission checks.
        """
        merged_data = {**dict(base_data), **update_data}
        return cls(merged_data, base_data.__class__)

    @property
    def permission_data(self) -> Dict[str, Any] | GeneralManagerData:
        """Return the underlying permission payload."""
        return self._permission_data

    @property
    def manager(self) -> type[GeneralManagerData] | None:
        """Return the manager class associated with the permission data."""
        return self._manager

    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to the wrapped permission data."""
        return self.getData(name)
