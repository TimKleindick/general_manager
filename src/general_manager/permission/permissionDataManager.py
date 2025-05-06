from __future__ import annotations
from typing import Callable, Dict, Any, Optional, TypeVar, Generic
from django.contrib.auth.models import AbstractUser

from general_manager.manager.generalManager import GeneralManager

GeneralManagerData = TypeVar("GeneralManagerData", bound=GeneralManager)


class PermissionDataManager(Generic[GeneralManagerData]):
    def __init__(
        self,
        permission_data: Dict[str, Any] | GeneralManagerData,
        manager: Optional[type[GeneralManagerData]] = None,
    ):
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
            if manager is None:
                raise ValueError(
                    "Manager must be provided if permission_data is a dict"
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
        merged_data = {**dict(base_data), **update_data}
        return cls(merged_data, base_data.__class__)

    @property
    def permission_data(self) -> Dict[str, Any] | GeneralManagerData:
        return self._permission_data

    @property
    def manager(self) -> type[GeneralManagerData]:
        return self._manager

    def __getattr__(self, name: str) -> Any:
        return self.getData(name)
